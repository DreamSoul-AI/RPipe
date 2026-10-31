"""Owned scientific child requests, responses and resource cleanup."""
from __future__ import annotations

import multiprocessing
from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from rpipe.structure.artifact.io import (
    ArtifactLayout, contained, identity, load_config, read_json, safe_error_record, write_json,
)
from rpipe.structure.artifact.provenance import environment_snapshot, validate_source
from rpipe.structure.control import AdmissionError, RunSpec

from .context import Budget, Cancelled, RunContext, ScienceContext, validate_draft
from .study import study_phase_files


class WorkerFailure(Exception):
    def __init__(self, record):
        self.record = record
        super().__init__('scientific worker failed')


def validate_budget(spec: RunSpec, budget: Budget) -> bool:
    """Reject unsupported hard limits before acquiring execution ownership."""
    budget.validate()
    if budget.memory_bytes is not None:
        raise AdmissionError('this process runner cannot enforce a hard memory limit')
    cfg = spec.to_config()
    study = load_config(spec.source_dir / 'study.yaml')
    data = cfg.get('data', {})
    workers = (data.get('config') or {}).get('num_workers', data.get('num_workers', 0))
    descendants = bool(workers or study.get('recipe') or study_phase_files(spec.source_dir, study)
                       or cfg.get('algorithm', {}).get('source') == 'transformers_trainer')
    if budget.timeout_seconds is not None and os.name == 'nt' and descendants:
        raise AdmissionError('hard timeout unavailable for Windows extensions or child-capable backends')
    return descendants


def _identifier(value, name):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{32}', value) is None:
        raise AdmissionError(f'worker {name} must be an explicit request identifier')
    return value


def _response(body, ctx, request):
    if (type(body.get('schema')) is not int or body['schema'] != 1
            or body.get('request') != request or body.get('identity') != identity(ctx.spec)
            or body.get('attempt') != ctx.state['attempt']):
        raise AdmissionError('worker response belongs to a different request, identity or attempt')
    if (not isinstance(body.get('result'), dict)
            or body.get('error') is not None and not isinstance(body['error'], dict)
            or not isinstance(body.get('cleanup_errors'), list)
            or any(not isinstance(item, dict) for item in body['cleanup_errors'])
            or any(type(body.get(name)) is not bool for name in
                   ('unknown_resources', 'science_closed', 'diagnostics_complete', 'error_logged'))
            or body.get('stage') not in {None, 'prepare', 'execute', 'collect', 'summarize', 'write', 'closing'}
            or not isinstance(body.get('environment'), dict)):
        raise AdmissionError('worker response has invalid observations')
    if body['science_closed'] == body['unknown_resources']:
        raise AdmissionError('worker response contradicts its resource closure')
    if body['error'] is None:
        validate_draft(body['result'])
    return body


def _cancel_body(spec, attempt, request):
    return {'schema': 1, 'action': 'cancel', 'request': request,
            'identity': identity(spec), 'attempt': attempt}


def _cancel_check(folder, spec, attempt, request):
    expected = _cancel_body(spec, attempt, request)
    marker = folder / 'worker-cancel.json'

    def check():
        try:
            body = read_json(contained(marker, folder))
        except FileNotFoundError:
            return
        if any(body.get(name) != expected[name] for name in ('request', 'identity', 'attempt')):
            return
        if type(body.get('schema')) is not int or body != expected:
            raise AdmissionError('invalid cancellation control for this worker')
        raise Cancelled('scientific cancellation requested')

    return check


def _request_cancel(ctx):
    if ctx.owner is None:
        raise AdmissionError('worker cancellation requires this attempt owner')
    ctx.owner.verify()
    folder = contained(ctx.spec.run_dir / 'attempts' / ctx.state['attempt'], ctx.spec.run_root)
    marker = folder / 'worker-cancel.json'
    contained(marker, folder)
    body = _cancel_body(ctx.spec, ctx.state['attempt'], ctx.state['worker_request'])
    try:
        saved = read_json(marker)
    except FileNotFoundError:
        write_json(marker, body)
    else:
        if type(saved.get('schema')) is not int or saved != body:
            raise AdmissionError('cancellation path contains a different request')
    ctx.state['worker_cancel_sent'] = True


def _receive_response(ctx):
    """Accept one final response after the owned root has actually exited."""
    child = ctx.state['child']
    if child.poll() is None:
        raise AdmissionError('a worker response is not final before its process exits')
    ctx.state['worker_response_attempted'] = True
    folder = contained(ctx.spec.run_dir / 'attempts' / ctx.state['attempt'], ctx.spec.run_root)
    output = folder / 'worker-output.json'
    if not output.is_file():
        raise WorkerFailure({'type': 'ChildExit', 'exit_code': child.returncode})
    response = _response(read_json(output), ctx, ctx.state['worker_request'])
    existing = ctx.state.get('cleanup_error_records', [])
    if not isinstance(existing, (list, tuple)):
        existing = [AdmissionError('cleanup error records must be a sequence')]
    reported = [safe_error_record(item) for item in response['cleanup_errors']]
    ctx.state.update(worker_response_verified=True, worker_response=response,
                     worker_environment=response['environment'],
                     cleanup_error_records=reported + [safe_error_record(item) for item in existing],
                     worker_cleanup_count=len(reported),
                     unknown_resources=bool(ctx.state.get('unknown_resources')) or response['unknown_resources'])
    ctx.state['phase'] = response.get('stage') or 'worker_admission'
    logger = ctx.state.get('logger')
    if logger is not None and not response['diagnostics_complete']:
        logger.diagnostics_complete = False
    return response


@contextmanager
def _worker_signals():
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN) if os.name == 'nt' else None
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def compute(ctx: RunContext, budget: Budget) -> dict:
    """Dispatch one owned child and return only its validated scientific draft."""
    ctx.state['worker_may_have_descendants'] = validate_budget(ctx.spec, budget)
    attempt = _identifier(ctx.state.get('attempt'), 'attempt')
    request_id = _identifier(ctx.state.setdefault('worker_request', uuid.uuid4().hex), 'request')
    folder = contained(ctx.spec.run_dir / 'attempts' / attempt, ctx.spec.run_root)
    request, output = folder / 'worker-request.json', folder / 'worker-output.json'
    ctx.state.update(worker_request=request_id, worker_response_verified=False, science_closed=False,
                     worker_response_attempted=False, worker_hard_timeout=False,
                     phase='worker_admission')
    spec_body = {name: str(getattr(ctx.spec, name)) if name in {'source_dir', 'run_root'}
                 else ctx.spec.to_config() if name == 'config' else getattr(ctx.spec, name)
                 for name, descriptor in RunSpec.__dataclass_fields__.items() if descriptor.init}
    logger = ctx.state.get('logger')
    logging_context = dict(logger.context) if logger is not None else {}
    logging_context.update(attempt_id=attempt, task_id=ctx.spec.id)
    write_json(request, {'schema': 1, 'request': request_id, 'identity': identity(ctx.spec),
                         'attempt': attempt, 'spec': spec_body,
                         'logging': logging_context})
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[2])
    env['PYTHONPATH'] = source + os.pathsep + env.get('PYTHONPATH', '')
    options = ({'start_new_session': True} if os.name != 'nt'
               else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP})
    if logger is not None:
        logger.info('Unstructured scientific worker output collection is disabled',
                    event='logging.unstructured_record', operation='worker_stdout',
                    drop_reason='raw_output_not_authorized')
    if ctx.cancellation.is_set():
        raise Cancelled('cancelled before scientific child dispatch')
    validate_source(ctx.spec)
    if ctx.cancellation.is_set():
        raise Cancelled('cancelled during scientific child admission')
    child = subprocess.Popen([sys.executable, '-m', 'rpipe', '_worker', str(request), str(output)],
                             cwd=ctx.study_dir, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, **options)
    ctx.state['child'] = child
    deadline = time.monotonic() + budget.timeout_seconds if budget.timeout_seconds else None
    ctx.state['worker_deadline'] = deadline
    while child.poll() is None:
        if deadline is not None and time.monotonic() >= deadline:
            ctx.state['worker_hard_timeout'] = True
            raise TimeoutError('scientific child exceeded hard timeout')
        if ctx.cancellation.is_set():
            raise Cancelled('scientific child cancellation requested')
        time.sleep(0.02)
    response = _receive_response(ctx)
    if response['error'] is not None:
        raise WorkerFailure({**response['error'], 'exit_code': child.returncode})
    if child.returncode != 0 and not response['cleanup_errors'] and not response['unknown_resources']:
        raise WorkerFailure({'type': 'ChildExit', 'exit_code': child.returncode})
    return response['result']


def close(ctx: RunContext) -> None:
    """Stop only the owned child; unresolved descendants retain execution ownership."""
    child = ctx.state.get('child')
    if child is None:
        ctx.state['science_closed'] = False
        return
    supplied = ctx.state.get('cleanup_error_records', [])
    cleanup = [safe_error_record(item) for item in supplied] if isinstance(supplied, (list, tuple)) else [
        safe_error_record(AdmissionError('cleanup error records must be a sequence'))]
    ctx.state['cleanup_error_records'] = cleanup
    started = time.monotonic()
    deadline = started + ctx.cleanup_seconds
    hard_deadline = ctx.state.get('worker_deadline')
    hard_timeout = bool(ctx.state.get('worker_hard_timeout')) or (
        hard_deadline is not None and started >= hard_deadline)
    if child.poll() is None and ctx.cancellation.is_set() and not hard_timeout:
        try:
            _request_cancel(ctx)
            cooperative_until = started + ctx.cleanup_seconds / 2
            if hard_deadline is not None:
                cooperative_until = min(cooperative_until, hard_deadline)
            while child.poll() is None and time.monotonic() < cooperative_until:
                time.sleep(min(0.02, max(0.0, cooperative_until - time.monotonic())))
        except BaseException as error:
            cleanup.append(safe_error_record(error))
    if child.poll() is None:
        try:
            if os.name != 'nt':
                os.killpg(child.pid, signal.SIGTERM)
            else:
                child.terminate()
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except ProcessLookupError:
            child.poll()
        except BaseException as error:
            cleanup.append(safe_error_record(error))
    folder = contained(ctx.spec.run_dir / 'attempts' / ctx.state['attempt'], ctx.spec.run_root)
    output = folder / 'worker-output.json'
    if (child.poll() is not None and output.is_file()
            and not ctx.state.get('worker_response_attempted')):
        try:
            _receive_response(ctx)
        except BaseException as error:
            cleanup.append(safe_error_record(error))
        cleanup = ctx.state['cleanup_error_records']
    unknown = bool(ctx.state.get('unknown_resources')) or child.poll() is None
    if os.name != 'nt':
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            pass
        except OSError as error:
            cleanup.append(safe_error_record(error))
            unknown = True
        else:
            unknown = True
    else:
        response = ctx.state.get('worker_response', {})
        if ctx.state.get('worker_may_have_descendants') and (
                not ctx.state.get('worker_response_verified') or not response.get('science_closed')):
            unknown = True
    ctx.state['unknown_resources'] = unknown
    ctx.state['science_closed'] = bool(child.poll() is not None
                                      and ctx.state.get('worker_response_verified')
                                      and ctx.state['worker_response']['science_closed'] and not unknown)


@_worker_signals()
def worker(request: Path, output: Path) -> int:
    """Compute without ownership or formal publication, then reply after cleanup."""
    from . import stages

    body = read_json(request)
    if type(body.get('schema')) is not int or body['schema'] != 1:
        raise AdmissionError('invalid worker request schema')
    request_id = _identifier(body.get('request'), 'request')
    attempt = _identifier(body.get('attempt'), 'attempt')
    if not isinstance(body.get('spec'), dict) or not isinstance(body.get('logging'), dict):
        raise AdmissionError('worker request must contain a spec and logging context')
    spec = RunSpec(**body['spec'])
    if body.get('identity') != identity(spec):
        raise AdmissionError('worker request identity does not match its specification')
    if (body['logging'].get('attempt_id') != attempt
            or body['logging'].get('task_id') != spec.id):
        raise AdmissionError('worker logging context belongs to a different Run or attempt')
    folder = contained(spec.run_dir / 'attempts' / attempt, spec.run_root)
    if (Path(request).resolve() != folder / 'worker-request.json'
            or Path(output).resolve() != folder / 'worker-output.json'):
        raise AdmissionError('worker request and response must belong to the selected attempt')
    ctx = ScienceContext(spec=spec, cancel_check=_cancel_check(folder, spec, attempt, request_id),
                         layout=ArtifactLayout(spec))
    ctx.state['attempt'] = attempt
    result, primary, diagnostics = {}, None, []
    error_logged = False
    started = time.monotonic()
    multiprocessing.current_process().name = 'scientific-worker'
    try:
        ctx.cancel_check()
        validate_source(spec)
        ctx.cancel_check()
        stages.configure_science(ctx, logging_context=body['logging'], request_id=request_id)
        result = validate_draft(stages.compute(ctx))
        ctx.validate_config(when='before scientific response')
    except BaseException as error:
        primary = safe_error_record(error)
        attached = getattr(error, 'cleanup_errors', ())
        if not isinstance(attached, (list, tuple)):
            attached = [AdmissionError('attached cleanup errors must be a sequence')]
        supplied = ctx.state.get('cleanup_error_records', [])
        if not isinstance(supplied, (list, tuple)):
            supplied = [AdmissionError('cleanup error records must be a sequence')]
        ctx.state['cleanup_error_records'] = [safe_error_record(item) for item in (*supplied, *attached)]
        logger = ctx.state.get('logger')
        if logger is not None:
            try:
                if isinstance(error, Cancelled):
                    logger.info('Scientific worker cancellation confirmed', event='task.cancelled')
                else:
                    logger.exception('Scientific worker failed', error, event='task.failed')
                error_logged = bool(logger.diagnostics_complete)
            except BaseException as diagnostic:
                diagnostics.append(safe_error_record(diagnostic))
    finally:
        try:
            stages.close_science(ctx)
        except BaseException as error:
            supplied = ctx.state.get('cleanup_error_records', [])
            if not isinstance(supplied, (list, tuple)):
                supplied = [AdmissionError('cleanup error records must be a sequence')]
            ctx.state['cleanup_error_records'] = [safe_error_record(item) for item in supplied]
            ctx.state['cleanup_error_records'].append(safe_error_record(error))
            ctx.state['unknown_resources'] = True
        supplied = ctx.state.get('cleanup_error_records', [])
        if not isinstance(supplied, (list, tuple)):
            supplied = [AdmissionError('cleanup error records must be a sequence')]
        cleanup = [safe_error_record(item) for item in supplied]
        ctx.state['cleanup_error_records'] = cleanup
        try:
            ctx.validate_config(when='during scientific close')
        except BaseException as error:
            if primary is None:
                primary = safe_error_record(error)
            else:
                cleanup.append(safe_error_record(error))
        for process in ctx.processes:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except BaseException as error:
                    cleanup.append(safe_error_record(error))
                if process.poll() is None:
                    ctx.state['unknown_resources'] = True
        logger = ctx.state.get('logger')
        if logger is not None:
            for failure in cleanup:
                try:
                    logger.exception('Scientific worker cleanup failed', failure,
                                     event='operation.failed', operation='cleanup')
                except BaseException as error:
                    diagnostics.append(safe_error_record(error))
            try:
                logger.info('Scientific worker stopped', event='runtime.stopped',
                            duration_ms=max(0, int((time.monotonic() - started) * 1000)))
            except BaseException as error:
                diagnostics.append(safe_error_record(error))
            try:
                logger.close()
            except BaseException as error:
                cleanup.append(safe_error_record(error))
                ctx.state['unknown_resources'] = True
    try:
        environment = environment_snapshot()
    except BaseException as error:
        environment = {}
        if primary is None:
            primary = safe_error_record(error)
        else:
            diagnostics.append(safe_error_record(error))
    unknown = bool(ctx.state.get('unknown_resources'))
    write_json(output, {'schema': 1, 'request': request_id, 'identity': identity(spec), 'attempt': attempt,
                        'result': result, 'error': primary, 'cleanup_errors': cleanup,
                        'stage': ctx.state.get('phase'), 'error_logged': error_logged,
                        'unknown_resources': unknown, 'science_closed': not unknown,
                        'environment': environment,
                        'diagnostics_complete': bool(logger is not None and not diagnostics
                                                     and logger.diagnostics_complete)})
    return 1 if primary or cleanup or unknown else 0
