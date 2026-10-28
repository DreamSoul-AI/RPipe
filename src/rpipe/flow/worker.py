"""Owned scientific child requests, responses and resource cleanup."""
from __future__ import annotations

import multiprocessing
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

from .context import Budget, Cancellation, Cancelled, RunContext, ScienceContext, validate_draft
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
                   ('unknown_resources', 'science_closed', 'diagnostics_complete'))
            or not isinstance(body.get('environment'), dict)):
        raise AdmissionError('worker response has invalid observations')
    if body['science_closed'] == body['unknown_resources']:
        raise AdmissionError('worker response contradicts its resource closure')
    if body['error'] is None:
        validate_draft(body['result'])
    return body


def compute(ctx: RunContext, budget: Budget) -> dict:
    """Dispatch one owned child and return only its validated scientific draft."""
    ctx.state['worker_may_have_descendants'] = validate_budget(ctx.spec, budget)
    attempt = _identifier(ctx.state.get('attempt'), 'attempt')
    request_id = _identifier(ctx.state.setdefault('worker_request', uuid.uuid4().hex), 'request')
    folder = contained(ctx.spec.run_dir / 'attempts' / attempt, ctx.spec.run_root)
    request, output = folder / 'worker-request.json', folder / 'worker-output.json'
    ctx.state.update(worker_request=request_id, worker_response_verified=False, science_closed=False)
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
    options = {'start_new_session': True} if os.name != 'nt' else {}
    if logger is not None:
        logger.info('Unstructured scientific worker output collection is disabled',
                    event='logging.unstructured_record', operation='worker_stdout',
                    drop_reason='raw_output_not_authorized')
    if ctx.cancellation.is_set():
        raise Cancelled('cancelled before scientific child dispatch')
    validate_source(ctx.spec)
    child = subprocess.Popen([sys.executable, '-m', 'rpipe', '_worker', str(request), str(output)],
                             cwd=ctx.study_dir, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, **options)
    ctx.state['child'] = child
    deadline = time.monotonic() + budget.timeout_seconds if budget.timeout_seconds else None
    while child.poll() is None:
        if ctx.cancellation.is_set():
            raise Cancelled('scientific child cancellation requested')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('scientific child exceeded hard timeout')
        time.sleep(0.02)
    if not output.is_file():
        raise WorkerFailure({'type': 'ChildExit', 'exit_code': child.returncode})
    response = _response(read_json(output), ctx, request_id)
    ctx.state.update(worker_response_verified=True, worker_response=response,
                     worker_environment=response['environment'],
                     cleanup_error_records=[safe_error_record(item) for item in response['cleanup_errors']],
                     worker_cleanup_count=len(response['cleanup_errors']),
                     unknown_resources=response['unknown_resources'])
    if logger is not None and not response['diagnostics_complete']:
        logger.diagnostics_complete = False
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
    cleanup = ctx.state.setdefault('cleanup_error_records', [])
    if child.poll() is None:
        try:
            if os.name != 'nt':
                os.killpg(child.pid, signal.SIGTERM)
            else:
                child.terminate()
            child.wait(timeout=2)
        except ProcessLookupError:
            child.poll()
        except BaseException as error:
            cleanup.append(safe_error_record(error))
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
    ctx = ScienceContext(spec=spec, cancellation=Cancellation(), layout=ArtifactLayout(spec))
    ctx.state['attempt'] = attempt
    result, primary, diagnostics = {}, None, []
    started = time.monotonic()
    multiprocessing.current_process().name = 'scientific-worker'
    try:
        validate_source(spec)
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
                        'unknown_resources': unknown, 'science_closed': not unknown,
                        'environment': environment,
                        'diagnostics_complete': bool(logger is not None and not diagnostics
                                                     and logger.diagnostics_complete)})
    return 1 if primary or cleanup or unknown else 0
