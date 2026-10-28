"""Compose source declaration, scientific payload, process ownership and publication."""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from rpipe.structure.control import AdmissionError, RunSpec, resolve_control
from rpipe.structure.contracts import Budget, Cancellation
from rpipe.structure.artifact.io import ArtifactLayout, identity, load_config, read_json, write_json
from rpipe.structure.artifact.provenance import environment_snapshot
from rpipe.structure.execution import Cancelled, error_record, execute
from rpipe.structure.execution import RunContext
from rpipe.structure.system.logger import Logger

from .stages import pipeline
from .study import RecipeContext, apply_recipe, _recipe_pickle_module, run_study_phase, study_phase_files

_runtime_logged = False


def configure(ctx, *, runtime_id=None, request_id=None, stdout=True):
    global _runtime_logged
    ctx.state['logger'] = Logger(ctx.layout.assets_dir, logger='rpipe.flow.run', stdout=stdout,
                                 runtime_id=runtime_id, task_id=ctx.spec.id,
                                 attempt_id=ctx.state.get('attempt'), request_id=request_id or uuid.uuid4().hex,
                                 workflow_id=ctx.spec.experiment, layer='flow')
    if not _runtime_logged:
        ctx.state['logger'].info('RPipe runtime started', event='runtime.started')
        _runtime_logged = True
    ctx.state['logger'].info('Run execution started', event='task.started')
    study = load_config(ctx.study_dir / 'study.yaml')
    ctx.state['study'] = study
    ctx.control = resolve_control(ctx.config)
    ctx.state['before_prepare'] = lambda current: run_study_phase(current.study_dir, 'prepare', current, study, entry='before')
    ctx.state['apply_recipe'] = lambda current: apply_recipe(current.study_dir, study, RecipeContext(
        current.study_dir, current.spec.id, current.spec.seed, current.config,
        current.layout.shared_data_dir, current.layout.shared_model_dir, current.layout.assets_dir,
        study_root=current.layout.study_root, run_root=current.spec.run_root))
    ctx.state['checkpoint_pickle_module'] = lambda: _recipe_pickle_module(ctx.study_dir)
    ctx.state['process_hook'] = lambda current: run_study_phase(current.study_dir, 'process', current, study)


class WorkerFailure(Exception):
    def __init__(self, record):
        self.record = record
        super().__init__(record.get('message', 'scientific worker failed'))


class ProcessRun:
    """Default science runs in an owned child, never on a read/plan import."""

    def __init__(self, spec, budget):
        self.spec, self.budget = spec, budget
        self.response = None
        self.response_verified = False
        self.child = None
        self.nonce = uuid.uuid4().hex
        if budget.memory_bytes is not None:
            raise AdmissionError('this process runner cannot enforce a hard memory limit')
        cfg = spec.to_config()
        study = load_config(spec.source_dir / 'study.yaml')
        data = cfg.get('data', {})
        workers = (data.get('config') or {}).get('num_workers', data.get('num_workers', 0))
        self.may_have_descendants = bool(workers or study.get('recipe') or study_phase_files(spec.source_dir, study)
                                         or cfg.get('algorithm', {}).get('source') == 'transformers_trainer')
        # Windows cannot confirm arbitrary extension or backend descendants stopped.
        if budget.timeout_seconds is not None and os.name == 'nt' and self.may_have_descendants:
            raise AdmissionError('hard timeout unavailable for Windows extensions or child-capable backends')

    def prepare(self, ctx):
        ctx.spec.assets_dir.mkdir(parents=True, exist_ok=True)
        folder = ctx.spec.run_dir / 'attempts' / ctx.state['attempt']
        self.request = folder / 'worker-request.json'
        self.output = folder / 'worker-output.json'
        write_json(self.request, {'request': self.nonce, 'identity': identity(ctx.spec), 'spec':
                                {'id': ctx.spec.id, 'seed': ctx.spec.seed,
                                 'config': ctx.config, 'source_dir': str(ctx.study_dir),
                                 'run_root': str(ctx.spec.run_root), 'source_digest': ctx.spec.source_digest,
                                 'version': ctx.spec.version, 'train_id': ctx.spec.train_id},
                                 'logging': dict(ctx.state['logger'].context)})

    def execute(self, ctx):
        log_dir = self.request.parent
        log_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        source = str(Path(__file__).resolve().parents[2])
        env['PYTHONPATH'] = source + os.pathsep + env.get('PYTHONPATH', '')
        options = {'start_new_session': True} if os.name != 'nt' else {}
        # Finish the parent record before competing for the worker's zero-wait log lock.
        ctx.state['logger'].info('Unstructured scientific worker output collection is disabled',
                                 event='logging.unstructured_record', operation='worker_stdout',
                                 drop_reason='raw_output_not_authorized')
        child = subprocess.Popen([sys.executable, '-m', 'rpipe', '_worker', str(self.request), str(self.output)],
                                 cwd=ctx.study_dir, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, **options)
        self.child = child
        ctx.register_process(child)
        deadline = time.monotonic() + self.budget.timeout_seconds if self.budget.timeout_seconds else None
        while child.poll() is None:
            if ctx.cancellation.is_set() or (deadline is not None and time.monotonic() >= deadline):
                if ctx.cancellation.is_set():
                    ctx.state['logger'].info('Cancellation requested for the owned scientific child',
                                             event='task.cancel_requested')
                if os.name != 'nt':
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass  # Coordinator retains busy while this owned child is alive.
                if os.name != 'nt':
                    try:
                        os.killpg(child.pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        ctx.state['unknown_resources'] = True
                elif self.may_have_descendants:
                    ctx.state['unknown_resources'] = True
                if ctx.cancellation.is_set():
                    raise Cancelled('scientific child cancellation requested')
                raise TimeoutError('scientific child exceeded hard timeout')
            time.sleep(0.02)
        if not self.output.is_file():
            ctx.state['logger'].error('Scientific child exited without a response', event='worker.exited',
                                      exit_code=child.returncode, exception_type='ChildExit')
            raise WorkerFailure({'type': 'ChildExit', 'exit_code': child.returncode})
        self.response = read_json(self.output)
        if self.response.get('request') != self.nonce or self.response.get('identity') != identity(ctx.spec):
            raise AdmissionError('worker response belongs to a different request or source/configuration')
        self.response_verified = True
        if isinstance(self.response.get('environment'), dict):
            ctx.state['worker_environment'] = self.response['environment']
        if not self.response.get('diagnostics_complete', True):
            ctx.state['logger'].diagnostics_complete = False
        ctx.state['cleanup_error_records'] = list(self.response.get('cleanup_errors', []))
        ctx.state['worker_cleanup_count'] = len(ctx.state['cleanup_error_records'])
        ctx.state['unknown_resources'] = bool(self.response.get('unknown_resources'))
        if child.returncode != 0 or self.response.get('error'):
            record = self.response.get('error') or {'type': 'ChildExit'}
            raise WorkerFailure({**record, 'exit_code': child.returncode})

    def collect(self, ctx):
        return self.response['result']

    def close(self, ctx):
        child = self.child
        if child is not None:
            # A dead root worker does not prove its children stopped.
            if os.name != 'nt':
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    ctx.state['unknown_resources'] = True
                    ctx.state.setdefault('cleanup_error_records', []).append(error_record(exc))
                else:
                    ctx.state['unknown_resources'] = True
            elif self.may_have_descendants and (child.poll() is None or not self.response_verified
                                               or child.returncode != 0 or self.response.get('error')):
                ctx.state['unknown_resources'] = True


def run(spec, *, payload=None, budget=None, cancellation=None, retry=False):
    budget = budget or Budget()
    budget.validate()
    if payload is None:
        payload = ProcessRun(spec, budget)
        def ready(ctx):
            configure(ctx, request_id=payload.nonce)
            ctx.state['budget'] = budget
        # Only this runner supervises the child deadline. The in-process mechanism
        # never accepts a payload's self-declared hard_timeout capability.
        return execute(spec, payload, cancellation=cancellation, configure=ready, retry=retry)
    return execute(spec, payload, budget=budget, cancellation=cancellation, pipeline=pipeline, configure=configure, retry=retry)


def worker(request: Path, output: Path) -> int:
    body = read_json(request)
    started = time.monotonic()
    result, primary, cleanup = {}, None, []
    ctx, payload = None, None
    try:
        spec = RunSpec(**body['spec'])
        ctx = RunContext(spec=spec, cancellation=Cancellation(), layout=ArtifactLayout(spec))
        multiprocessing.current_process().name = 'scientific-worker'
        context = body.get('logging', {})
        ctx.state['attempt'] = context.get('attempt_id')
        configure(ctx, runtime_id=context.get('runtime_id'), request_id=body['request'], stdout=False)
        # Imports become scientific execution, outside the read/plan path.
        if ctx.config.get('algorithm', {}).get('source') == 'transformers_trainer':
            from rpipe.flow.huggingface import HuggingFaceRun
            payload = HuggingFaceRun()
        else:
            from rpipe.flow.native import NativeRun
            payload = NativeRun()
        result = pipeline(payload, ctx)
    except BaseException as exc:
        primary = error_record(exc)
        if ctx is not None and ctx.state.get('logger') is not None:
            if isinstance(exc, Cancelled):
                ctx.state['logger'].info('Scientific worker cancellation confirmed', event='task.cancelled')
            else:
                ctx.state['logger'].exception('Scientific worker failed', exc, event='task.failed')
    finally:
        try:
            if payload is not None:
                payload.close(ctx)
        except BaseException as exc:
            cleanup.append(error_record(exc))
        for process in ctx.processes if ctx is not None else []:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except BaseException as exc:
                    cleanup.append(error_record(exc))
                if process.poll() is None:
                    ctx.state['unknown_resources'] = True
        if ctx is not None and ctx.state.get('logger') is not None:
            logger = ctx.state['logger']
            for failure in cleanup:
                try:
                    logger.exception('Scientific worker cleanup failed', failure,
                                     event='operation.failed', operation='cleanup')
                except BaseException:
                    logger.diagnostics_complete = False
            logger.info('Scientific worker stopped', event='runtime.stopped',
                        duration_ms=max(0, int((time.monotonic() - started) * 1000)))
            logger.close()
    write_json(output, {'result': result, 'error': primary, 'cleanup_errors': cleanup,
                        'unknown_resources': bool(ctx and ctx.state.get('unknown_resources')),
                        'request': body['request'], 'identity': body['identity'],
                        'environment': environment_snapshot(),
                        'diagnostics_complete': bool(ctx is not None and ctx.state.get('logger') is not None
                                                     and ctx.state['logger'].diagnostics_complete)})
    return 1 if primary or cleanup else 0
