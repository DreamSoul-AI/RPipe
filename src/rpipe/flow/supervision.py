"""Explicit, bounded Windows supervision of a sequential owned Run batch."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from rpipe.structure.artifact.io import contained, identity, read_json, validate_input_paths, write_json
from rpipe.structure.control import AdmissionError, RunSpec
from .context import Budget, Cancellation, RunReport, SupervisionLimits
from rpipe.structure.artifact.io import validate_run_root
from rpipe.structure.artifact.io import safe_error_record as error_record
from .run import run
from rpipe.structure.system.resources import WindowsJob, output_growth, output_snapshot


def _spec_body(spec):
    return {name: str(getattr(spec, name)) if name in {'source_dir', 'run_root'}
            else spec.to_config() if name == 'config' else getattr(spec, name)
            for name, descriptor in RunSpec.__dataclass_fields__.items() if descriptor.init}


def _successful(outcome):
    return (outcome['status'] == 'succeeded' and outcome['evidence_complete'] is True
            and outcome['quality'] is not False and not outcome['cleanup_errors'])


def coordinator():
    """The child cannot inspect a Study or create a Run before its stdin grant."""

    line = sys.stdin.readline()
    if not line:
        return 1
    gate = json.loads(line)
    request = read_json(Path(gate['request_path']))
    nonce = gate['request']
    if request.get('request') != nonce:
        raise AdmissionError('supervision request belongs to another gate')
    specs = tuple(RunSpec(**body) for body in request['specs'])
    progress_path = Path(request['progress'])
    cancellation = Cancellation()
    grant = threading.Event()
    control = {'index': 0}

    def controls():
        try:
            for line in sys.stdin:
                body = json.loads(line)
                if body.get('request') != nonce:
                    raise AdmissionError('supervision control belongs to another request')
                if body.get('action') == 'cancel':
                    cancellation.request()
                    grant.set()
                elif body.get('action') == 'continue' and type(body.get('index')) is int:
                    control['index'] = body['index']
                    grant.set()
                else:
                    raise AdmissionError('invalid supervision control')
        except BaseException as exc:
            control['error'] = error_record(exc)
        finally:
            cancellation.request()
            grant.set()

    threading.Thread(target=controls, daemon=True).start()
    state = {'schema': 1, 'request': nonce, 'identities': [identity(spec) for spec in specs],
             'completed': [], 'current': None, 'done': False, 'error': None}

    def publish():
        write_json(progress_path, state)

    try:
        for index, spec in enumerate(specs):
            if index:
                grant.wait()
                grant.clear()
                if control['index'] != index:
                    cancellation.request()
            if cancellation.is_set():
                break
            state['current'] = {'index': index, 'id': spec.id, 'started': time.monotonic()}
            publish()
            # execute() publishes its own config; make([spec]) would truncate the Study index.
            outcome = run(spec, cancellation=cancellation, retry=request['retry'])
            row = {key: value for key, value in asdict(outcome).items() if key != 'result'}
            row.update(index=index, id=spec.id, identity=identity(spec),
                       elapsed_seconds=max(0.0, time.monotonic() - state['current']['started']),
                       result_path=str(spec.result_path))
            state['completed'].append(row)
            state['current'] = None
            publish()
            if not _successful(row):
                break
        if control.get('error'):
            state['error'] = control['error']
    except BaseException as exc:
        state['error'] = error_record(exc)
    state['done'] = True
    publish()
    return 0 if (len(state['completed']) == len(specs) and not state['error']
                 and all(_successful(row) for row in state['completed'])) else 1


def _progress(path, nonce, specs):
    if not path.exists():
        return None
    if path.stat().st_size > 8 * 1024 * 1024:
        raise AdmissionError('supervision progress exceeds the bounded summary size')
    body = read_json(path)
    if (body.get('schema') != 1 or body.get('request') != nonce
            or body.get('identities') != [identity(spec) for spec in specs]
            or not isinstance(body.get('completed'), list) or type(body.get('done')) is not bool):
        raise AdmissionError('invalid or foreign supervision progress')
    completed = body['completed']
    if len(completed) > len(specs):
        raise AdmissionError('too many supervised outcomes')
    for index, row in enumerate(completed):
        if (not isinstance(row, dict) or row.get('index') != index or row.get('id') != specs[index].id
                or row.get('identity') != identity(specs[index])
                or row.get('status') not in {'succeeded', 'failed', 'cancelled', 'unknown', 'rejected'}
                or type(row.get('evidence_complete')) is not bool
                or row.get('quality') is not None and type(row['quality']) is not bool
                or not isinstance(row.get('cleanup_errors'), list)
                or not isinstance(row.get('elapsed_seconds'), (int, float))
                or not math.isfinite(row['elapsed_seconds']) or row['elapsed_seconds'] < 0):
            raise AdmissionError('invalid supervised outcome')
    current = body.get('current')
    if current is not None and (not isinstance(current, dict) or current.get('index') != len(completed)
                                or len(completed) == len(specs)
                                or current.get('id') != specs[len(completed)].id
                                or not isinstance(current.get('started'), (int, float))
                                or not math.isfinite(current['started'])):
        raise AdmissionError('invalid supervised cell')
    return body


def _partial(spec):
    """Describe already published science without finalizing or reclaiming its owner."""
    row = {'id': spec.id, 'busy': contained(spec.run_dir / '.execution', spec.run_root).exists(),
           'result_path': str(spec.result_path), 'scientific_status': 'missing'}
    path = contained(spec.result_path, spec.run_root)
    if path.is_file():
        try:
            # A broken publisher must not force an unbounded read in the observer.
            if path.stat().st_size > 4 * 1024 * 1024:
                raise AdmissionError('scientific result too large for supervision summary')
            body = read_json(path)
            if body.get('identity') != identity(spec):
                raise AdmissionError('scientific result belongs to another Run')
            row.update(scientific_status=body.get('status', 'unknown'), quality=body.get('quality'),
                       evidence_complete=body.get('evidence_complete') is True and not row['busy'])
        except BaseException as exc:
            row.update(scientific_status='unknown', error=error_record(exc))
    return row


def supervise(specs, *, limits, evidence_dir, cancellation=None, retry=False):
    started = time.monotonic()
    if not isinstance(limits, SupervisionLimits):
        raise AdmissionError('explicit SupervisionLimits required')
    limits.validate()
    if os.name != 'nt':
        raise AdmissionError('process-tree supervision requires Windows Job Objects')
    if type(retry) is not bool:
        raise AdmissionError('retry must be a boolean')
    specs = tuple(specs)
    if not specs or any(not isinstance(spec, RunSpec) for spec in specs):
        raise AdmissionError('a nonempty RunSpec batch is required')
    _one_root(specs)
    if len({spec.id for spec in specs}) != len(specs):
        raise AdmissionError('duplicate supervised Run')
    evidence = Path(evidence_dir).resolve()
    for spec in specs:
        validate_run_root(spec.source_dir, evidence)
        validate_input_paths(spec, output_root=evidence)
        if evidence.is_relative_to(spec.run_root) or spec.run_root.is_relative_to(evidence):
            raise AdmissionError('supervision evidence must be separate from Run roots')
    if evidence.exists() or Path(evidence_dir).is_symlink():
        raise AdmissionError('supervision evidence directory must be new')
    nonce = uuid.uuid4().hex
    progress_path = evidence / 'progress.json'
    roots = tuple({spec.run_root for spec in specs} | {evidence})
    report = {'schema': 1, 'request': nonce, 'status': 'unknown', 'trigger': None,
              'limits': asdict(limits), 'observed': {'rss_bytes_peak': 0, 'output_bytes_peak': 0},
              'completed': [], 'unstarted': [spec.id for spec in specs], 'cleanup_errors': [],
              'accounting': {'rss': 'sampled aggregate working set; not a hard memory quota',
                             'output': 'sampled positive file growth; not a disk quota',
                             'poll_seconds': limits.poll_seconds}, 'tree_empty': False}
    job, child, latest = WindowsJob(), None, None
    baseline = None
    assigned = False
    granted = 0
    evidence_owned = False
    admission_failure = None
    cleanup_failures = []

    def cleanup_error(exc):
        cleanup_failures.append(exc)
        report['cleanup_errors'].append(error_record(exc))

    def send(action, **body):
        child.stdin.write((json.dumps({'request': nonce, 'action': action, **body}) + '\n').encode())
        child.stdin.flush()

    def stop(kind, **details):
        if report['trigger'] is None:
            report['trigger'] = {'kind': kind, 'detected_seconds': max(0.0, time.monotonic() - started), **details}

    def deadlines(now):
        if cancellation is not None and cancellation.is_set():
            stop('cancelled')
        elif now - started >= limits.batch_seconds:
            stop('batch_timeout', limit_seconds=limits.batch_seconds,
                 overshoot_seconds=max(0.0, now - started - limits.batch_seconds))
        if latest:
            current = latest.get('current')
            if current is not None and now - current['started'] >= limits.run_seconds:
                stop('run_timeout', index=current['index'], limit_seconds=limits.run_seconds,
                     overshoot_seconds=max(0.0, now - current['started'] - limits.run_seconds))
            for row in latest['completed']:
                if row['elapsed_seconds'] >= limits.run_seconds:
                    stop('run_timeout', index=row['index'], limit_seconds=limits.run_seconds,
                         overshoot_seconds=row['elapsed_seconds'] - limits.run_seconds)
                if not _successful(row):
                    stop('run_outcome', index=row['index'], status=row['status'])
            if latest.get('error'):
                stop('coordinator_error', error=latest['error'])

    try:
        evidence.mkdir(parents=True, exist_ok=False)
        evidence_owned = True
        baseline = output_snapshot(roots)
        request_path = evidence / 'request.json'
        write_json(request_path, {'request': nonce, 'specs': [_spec_body(spec) for spec in specs],
                                 'progress': str(progress_path), 'retry': retry})
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get('PYTHONPATH', '')
        child = subprocess.Popen([sys.executable, '-B', '-m', 'rpipe', '_supervisor-coordinator'],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=env)
        job.assign(child)
        assigned = True
        growth = output_growth(roots, baseline)
        report['observed']['output_bytes_peak'] = growth
        if growth > limits.output_bytes:
            stop('output_limit', observed_bytes=growth, limit_bytes=limits.output_bytes,
                 overshoot_bytes=growth - limits.output_bytes)
        if cancellation is not None and cancellation.is_set():
            stop('cancelled')
        elif time.monotonic() - started >= limits.batch_seconds:
            stop('batch_timeout', limit_seconds=limits.batch_seconds)
        if report['trigger'] is None:
            child.stdin.write((json.dumps({'request': nonce, 'request_path': str(request_path)}) + '\n').encode())
            child.stdin.flush()
        while True:
            if report['trigger']:
                break
            observed_started = time.monotonic()
            deadlines(observed_started)
            latest = _progress(progress_path, nonce, specs) or latest
            rss = job.rss_bytes()
            growth = output_growth(roots, baseline)
            report['observed']['sample_seconds_max'] = max(
                time.monotonic() - observed_started, report['observed'].get('sample_seconds_max', 0.0))
            report['observed']['rss_bytes_peak'] = max(rss, report['observed']['rss_bytes_peak'])
            report['observed']['output_bytes_peak'] = max(growth, report['observed']['output_bytes_peak'])
            if rss > limits.rss_bytes:
                stop('rss_limit', observed_bytes=rss, limit_bytes=limits.rss_bytes,
                     overshoot_bytes=rss - limits.rss_bytes)
            if growth > limits.output_bytes:
                stop('output_limit', observed_bytes=growth, limit_bytes=limits.output_bytes,
                     overshoot_bytes=growth - limits.output_bytes)
            deadlines(time.monotonic())
            if report['trigger']:
                break
            if child.poll() is not None:
                latest = _progress(progress_path, nonce, specs) or latest
                deadlines(time.monotonic())
                if not latest or not latest['done'] or len(latest['completed']) != len(specs) or child.returncode:
                    stop('coordinator_exit', exit_code=child.returncode)
                elif set(job.pids()) - {child.pid}:
                    stop('descendants_remain')
                break
            if latest and latest['current'] is None and not latest['done']:
                next_index = len(latest['completed'])
                if next_index > granted and next_index < len(specs):
                    if set(job.pids()) - {child.pid}:
                        stop('descendants_remain', index=next_index - 1)
                        break
                    send('continue', index=next_index)
                    granted = next_index
            now = time.monotonic()
            remaining = min(limits.run_seconds, limits.batch_seconds - (now - started))
            if latest and latest.get('current') is not None:
                remaining = min(remaining, limits.run_seconds - (now - latest['current']['started']))
            time.sleep(max(0.0, min(limits.poll_seconds, remaining)))
    except BaseException as exc:
        if not evidence_owned:
            admission_failure = exc
        stop('cancelled' if isinstance(exc, KeyboardInterrupt) else 'observation_failure', error=error_record(exc))
    finally:
        cleanup_started = time.monotonic()
        if child is not None:
            if assigned:
                if report['trigger']:
                    try:
                        send('cancel')
                    except (OSError, ValueError):
                        pass  # A coordinator already gone cannot receive cooperative cancellation.
                    grace = limits.cleanup_seconds / 2
                    try:
                        report['tree_empty'] = job.wait_empty(grace)
                    except BaseException as exc:
                        cleanup_error(exc)
                try:
                    if not report['tree_empty'] and job.pids():
                        job.terminate()
                    remaining = max(0.0, limits.cleanup_seconds - (time.monotonic() - cleanup_started))
                    report['tree_empty'] = job.wait_empty(remaining)
                except BaseException as exc:
                    cleanup_error(exc)
            else:
                # The unassigned child is still at its stdin gate and has no scientific descendants.
                try:
                    child.kill()
                    child.wait(timeout=limits.cleanup_seconds)
                    report['tree_empty'] = True
                except BaseException as exc:
                    cleanup_error(exc)
            try:
                child.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                child.wait(timeout=max(0.001, limits.cleanup_seconds - (time.monotonic() - cleanup_started)))
            except BaseException as exc:
                cleanup_error(exc)
        else:
            report['tree_empty'] = True
        try:
            job.close()
        except BaseException as exc:
            cleanup_error(exc)
        report['cleanup_seconds'] = max(0.0, time.monotonic() - cleanup_started)
    if not evidence_owned:
        admission_failure.cleanup_errors = (*getattr(admission_failure, 'cleanup_errors', ()), *cleanup_failures)
        raise admission_failure
    try:
        latest = _progress(progress_path, nonce, specs) or latest
    except BaseException as exc:
        stop('observation_failure', error=error_record(exc))
    if latest:
        report['completed'] = latest['completed']
        count = len(latest['completed'])
        current = latest.get('current')
        try:
            report['interrupted'] = _partial(specs[current['index']]) if current is not None else None
        except BaseException as exc:
            report['interrupted'] = {'id': specs[current['index']].id, 'scientific_status': 'unknown',
                                     'busy': True, 'error': error_record(exc)}
            stop('observation_failure', error=error_record(exc))
        report['unstarted'] = [spec.id for spec in specs[count + (current is not None):]]
    deadlines(time.monotonic())
    complete = (not report['trigger'] and report['tree_empty'] and not report['cleanup_errors']
                and latest and latest['done'] and len(latest['completed']) == len(specs)
                and all(_successful(row) for row in latest['completed']))
    uncertain = (any(row['status'] == 'unknown' for row in report['completed'])
                 or bool(report.get('interrupted', {}) and report['interrupted'].get('busy')))
    report['status'] = ('succeeded' if complete else 'unknown' if not report['tree_empty']
                        or report['cleanup_errors'] or uncertain else 'cancelled' if report['trigger']
                        and report['trigger']['kind'] == 'cancelled' else 'failed')
    report['evidence_complete'] = bool(complete)
    report['elapsed_seconds'] = max(0.0, time.monotonic() - started)
    report_path = evidence / 'report.json'
    write_json(report_path, report)
    if baseline is not None:
        try:
            growth = output_growth(roots, baseline)
            deadlines(time.monotonic())
            report['elapsed_seconds'] = max(0.0, time.monotonic() - started)
            if report['trigger']:
                report['evidence_complete'] = False
                if report['status'] == 'succeeded':
                    report['status'] = 'cancelled' if report['trigger']['kind'] == 'cancelled' else 'failed'
            written_size = report_path.stat().st_size
            # Include this report's own final growth without a permanently unmeasured rewrite.
            for _ in range(8):
                size = len((json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
                final_growth = growth + max(0, size - written_size)
                peak = max(final_growth, report['observed']['output_bytes_peak'])
                if peak == report['observed']['output_bytes_peak']:
                    break
                report['observed']['output_bytes_peak'] = peak
                if peak > limits.output_bytes:
                    stop('output_limit', observed_bytes=peak, limit_bytes=limits.output_bytes,
                         overshoot_bytes=peak - limits.output_bytes)
                    report['evidence_complete'] = False
                    if report['status'] == 'succeeded':
                        report['status'] = 'failed'
            else:
                raise AdmissionError('final supervision output accounting did not stabilize')
        except BaseException as exc:
            stop('observation_failure', error=error_record(exc))
            report.update(status='unknown', evidence_complete=False)
        write_json(report_path, report)
    return report


def _one_root(specs):
    if len({(s.study_root, s.source_dir, s.source_digest) for s in specs}) > 1:
        raise AdmissionError('one Study source/version/output root is required')


def launch(specs, *, budget: Budget | None = None, cancellation: Cancellation | None = None,
           retry: bool = False) -> tuple[RunReport, ...]:
    specs = tuple(specs)
    if any(not isinstance(spec, RunSpec) for spec in specs):
        raise AdmissionError('launch requires RunSpecs')
    _one_root(specs)
    if len({spec.id for spec in specs}) != len(specs):
        raise AdmissionError('duplicate Run in launch request')
    outcomes = []
    for spec in specs:
        if cancellation and cancellation.is_set():
            break
        outcomes.append(run(spec, budget=budget, cancellation=cancellation, retry=retry))
        outcome = outcomes[-1]
        if (outcome.status != 'succeeded' or outcome.quality is False
                or not outcome.evidence_complete):
            break
    return tuple(outcomes)
