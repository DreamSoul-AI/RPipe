"""One Run lifecycle: admission, ownership, science closure and formal write."""
from __future__ import annotations

import json
import uuid

from rpipe.structure.control import AdmissionError, RunSpec, SourceMismatch
from rpipe.structure.artifact import io, provenance
from .context import Budget, Cancellation, Cancelled, RunContext, RunReport, validate_draft, validate_cleanup_seconds
from . import stages, worker

_runtime_logged = False


def _admit(spec, retry):
    config = spec.run_dir / 'config.yaml'
    if config.exists():
        stored = json.dumps(io.load_config(config), sort_keys=True, allow_nan=False)
        expected = json.dumps(spec.to_config(), sort_keys=True, allow_nan=False)
        if stored != expected:
            raise SourceMismatch('existing configuration differs from the Run declaration')
    if spec.result_path.exists():
        if io.read_json(spec.result_path).get('identity') != io.identity(spec):
            raise SourceMismatch('existing Run has a different source/configuration identity')
        raise AdmissionError('existing scientific result is immutable; choose a new version')
    if (spec.run_dir / 'execution.json').exists():
        record, complete = io.read_execution(spec)
        previous = io.project_execution(record, {}, busy=False, complete=complete)
        if previous['status'] not in {'failed', 'cancelled'}:
            raise AdmissionError('previous execution is unresolved; retry is not safe')
        if not retry:
            raise AdmissionError('previous execution requires explicit retry=True')
    elif (spec.run_dir / 'attempts').is_dir() and any((spec.run_dir / 'attempts').iterdir()):
        raise AdmissionError('unindexed attempts cannot be retried')


def _configure(ctx):
    from rpipe.structure.system.logger import Logger
    global _runtime_logged
    request = ctx.state.setdefault('worker_request', uuid.uuid4().hex)
    ctx.state['logger'] = Logger(ctx.layout.assets_dir, logger='rpipe.flow.run',
                                 task_id=ctx.spec.id, attempt_id=ctx.state['attempt'],
                                 request_id=request, workflow_id=ctx.spec.experiment, layer='flow')
    logger = ctx.state['logger']
    if not _runtime_logged:
        logger.info('RPipe runtime started', event='runtime.started')
        _runtime_logged = True
    logger.info('Run execution started', event='task.started')


def _log_error(ctx, error, message):
    response = ctx.state.get('worker_response', {})
    if isinstance(vars(error).get('record'), dict) and response.get('error_logged') is True:
        return
    logger = ctx.state.get('logger')
    if logger is not None:
        try:
            logger.bind(phase=ctx.state.get('phase') or 'admission')
            if isinstance(error, Cancelled):
                logger.info('Run cancellation requested; resource cleanup pending', event='task.cancel_requested')
            else:
                logger.exception(message, error, event='task.failed')
        except BaseException:
            logger.diagnostics_complete = False


def _interruption(error, cancellation):
    if isinstance(error, KeyboardInterrupt):
        cancellation.request()
        cancelled = Cancelled('execution interrupted; resource cleanup pending')
        cancelled.__cause__ = error
        return cancelled
    return error


def run(spec: RunSpec, *, budget: Budget | None = None,
        cancellation: Cancellation | None = None, retry: bool = False,
        cleanup_seconds: float = 2.0) -> RunReport:
    """A rejected request never becomes a failed scientific Run on disk."""
    budget = budget or Budget()
    cancellation = cancellation or Cancellation()
    try:
        if not isinstance(spec, RunSpec):
            raise AdmissionError('run requires a RunSpec')
        if not isinstance(retry, bool):
            raise AdmissionError('retry must be a boolean')
        budget.validate()
        validate_cleanup_seconds(cleanup_seconds)
        provenance.validate_source(spec)
        worker.validate_budget(spec, budget)
        if cancellation.is_set():
            return RunReport('cancelled', cancelled=True)
        if not (spec.run_dir / '.execution').exists():
            _admit(spec, retry)
        io.validate_train(spec)
    except KeyboardInterrupt:
        cancellation.request()
        return RunReport('cancelled', cancelled=True)
    except (ValueError, TypeError, OSError, RuntimeError) as error:
        return RunReport('rejected', error=io.safe_error_record(error))
    attempt = uuid.uuid4().hex
    owner = io.Ownership(spec, attempt=attempt)
    try:
        owner.acquire()
        _admit(spec, retry)
    except (ValueError, TypeError, OSError, RuntimeError, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        if interrupted:
            cancellation.request()
        cleanup = []
        if owner.acquired:
            try:
                owner.release()
            except BaseException as closing_error:
                cleanup.append(io.safe_error_record(closing_error))
        return RunReport('unknown' if cleanup else 'cancelled' if interrupted else 'rejected',
                         error=io.safe_error_record(error), cancelled=interrupted,
                         cleanup_errors=tuple(cleanup), attempt=attempt if cleanup else None)
    ctx = RunContext(spec, cancellation, io.ArtifactLayout(spec), owner=owner,
                     cleanup_seconds=cleanup_seconds)
    ctx.state.update(attempt=attempt, budget=budget, phase='admission')
    record = {'schema': 1, 'identity': io.identity(spec), 'attempt': attempt,
              'owner_token': owner.token, 'phase': 'running', 'candidate_status': None,
              'quality': None, 'evidence_complete': False, 'scientific_result_published': False,
              'derived_status': 'not_run', 'error': None, 'cleanup_errors': [], 'cancelled': False}
    primary, cleanup, draft = None, [], None
    status, derived = 'failed', 'not_run'
    unknown, complete = False, False
    try:
        record['input_provenance'] = provenance.input_provenance(spec)
        record['environment'] = provenance.environment_snapshot()
        stages.write(ctx, record)
        config = spec.run_dir / 'config.yaml'
        if not config.exists():
            io.write_config(config, ctx.config)
        _configure(ctx)
        provenance.validate_source(spec)
        draft = worker.compute(ctx, budget)
        ctx.validate_config(spec, 'after science response')
        validate_draft(draft)
    except BaseException as error:
        error = _interruption(error, cancellation)
        primary = io.safe_error_record(error)
        attached = getattr(error, 'cleanup_errors', ())
        if not isinstance(attached, (list, tuple)):
            attached = [AdmissionError('attached cleanup errors must be a sequence')]
        supplied = ctx.state.get('cleanup_error_records', [])
        if not isinstance(supplied, (list, tuple)):
            supplied = [AdmissionError('cleanup error records must be a sequence')]
        ctx.state['cleanup_error_records'] = [io.safe_error_record(item) for item in (*supplied, *attached)]
        _log_error(ctx, error, 'Run execution failed')
    finally:
        try:
            worker.close(ctx)
        except BaseException as error:
            ctx.state.setdefault('cleanup_error_records', []).append(io.safe_error_record(error))
            ctx.state['unknown_resources'] = True
        supplied = ctx.state.get('cleanup_error_records', [])
        if not isinstance(supplied, (list, tuple)):
            supplied = [AdmissionError('cleanup error records must be a sequence')]
            ctx.state['unknown_resources'] = True
        cleanup.extend(io.safe_error_record(error) for error in supplied)
        unknown = bool(ctx.state.get('unknown_resources'))
        remote = ctx.state.get('worker_response', {}).get('error')
        if (primary and primary.get('type') in {'Cancelled', 'CancelledError'}
                and remote is not None and remote.get('type') not in {'Cancelled', 'CancelledError', 'KeyboardInterrupt'}):
            primary = io.safe_error_record(remote)
            _log_error(ctx, worker.WorkerFailure(remote), 'Scientific failure reported during cancellation')
    status = ('unknown' if unknown else 'failed' if primary or cleanup
              else 'cancelled' if cancellation.is_set() else 'succeeded')
    if primary and primary.get('type') in {'Cancelled', 'CancelledError'} and not unknown and not cleanup:
        status = 'cancelled'
    record.update(phase='closing', candidate_status=status, error=primary, cleanup_errors=cleanup,
                  cancelled=cancellation.is_set(), budget=vars(budget), stage=ctx.state.get('phase'),
                  cleanup_seconds=cleanup_seconds,
                  environment=ctx.state.get('worker_environment', record.get('environment')))
    if status == 'succeeded':
        try:
            ctx.state['phase'] = 'write'
            if ctx.state.get('logger') is not None:
                ctx.state['logger'].bind(phase='write')
            stages.write(ctx, record, draft)
            complete = bool(record['evidence_complete'])
        except BaseException as error:
            error = _interruption(error, cancellation)
            primary = io.safe_error_record(error)
            _log_error(ctx, error, 'Run publication failed')
            status = 'cancelled' if isinstance(error, Cancelled) else 'failed'
            complete = False
    if status == 'succeeded' and cancellation.is_set():
        status, complete = 'cancelled', False
    if ctx.state.get('published_result') is not None and status == 'succeeded':
        try:
            derived = 'running'
            ctx.state['phase'] = 'process'
            if ctx.state.get('logger') is not None:
                ctx.state['logger'].bind(phase='process')
                ctx.state['logger'].info('Run phase started', event='task.phase_changed')
            stages.process(ctx)
            derived = 'succeeded'
        except BaseException as error:
            error = _interruption(error, cancellation)
            primary = io.safe_error_record(error)
            _log_error(ctx, error, 'Run derived processing failed')
            status = 'cancelled' if primary.get('type') in {'Cancelled', 'CancelledError'} else 'failed'
            derived, complete = status, False
    logger = ctx.state.get('logger')
    diagnostic_errors = []
    if logger is not None:
        reported = ctx.state.get('worker_cleanup_count', 0)
        if type(reported) is not int or not 0 <= reported <= len(cleanup):
            reported = 0
        for failure in cleanup[reported:]:
            try:
                logger.exception('Run cleanup operation failed', failure,
                                 event='operation.failed', operation='cleanup')
            except BaseException:
                logger.diagnostics_complete = False
        try:
            logger.bind(phase='closing')
            logger.info('execution candidate recorded', event='operation.completed',
                        operation='execution_candidate', status=status, outcome='unknown')
        except BaseException as error:
            diagnostic_errors.append(io.safe_error_record(error))
        try:
            logger.close()
        except BaseException as error:
            diagnostic_errors.append(io.safe_error_record(error))
        complete = bool(complete and getattr(logger, 'diagnostics_complete', True) and not diagnostic_errors)
    if status == 'succeeded' and cancellation.is_set():
        status, complete = 'cancelled', False
    record.update(candidate_status=status, error=primary, cleanup_errors=cleanup, derived_status=derived,
                  evidence_complete=bool(complete), cancelled=cancellation.is_set(),
                  diagnostic_errors=diagnostic_errors, stage=ctx.state.get('phase'))
    try:
        stages.write(ctx, record)
    except BaseException as error:
        cleanup.append(io.safe_error_record(error))
        unknown = True
    if not unknown:
        try:
            owner.release()
        except BaseException as error:
            cleanup.append(io.safe_error_record(error))
            unknown = True
    if unknown:
        record.update(candidate_status='unknown', evidence_complete=False, cleanup_errors=cleanup)
        try:
            stages.write(ctx, record)
        except BaseException as error:
            cleanup.append(io.safe_error_record(error))
    result = ctx.state.get('published_result', {})
    projected = io.project_execution(record, result, busy=unknown)
    return RunReport(projected['status'], projected['quality'], projected['evidence_complete'],
                     result, primary, tuple(cleanup), cancellation.is_set(), attempt=attempt,
                     scientific_status=projected['scientific_status'], derived_status=projected['derived_status'])
