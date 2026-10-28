"""Owned CPU child output is untrusted; diagnostics retain actual producers."""
import importlib
import json
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('worker_fails', [False, True, 'close'])
@pytest.mark.parametrize('held_parent_log', [False, True])
def test_noisy_worker_is_not_persisted_and_events_keep_child_identity(
        source_study, run_root, tmp_path, monkeypatch, worker_fails, held_parent_log):
    from rpipe.structure.make import plan
    from rpipe.api import run
    from rpipe.structure.artifact.io import cache_lock
    from rpipe.structure.system.logger import Logger
    module = importlib.import_module('rpipe.flow.run')
    native_popen = subprocess.Popen
    children = []
    gate = tmp_path / 'parent-policy-recorded'
    script = '''
import sys
from pathlib import Path
from rpipe.flow.run import worker
import rpipe.flow.native as native
class Tiny:
    def prepare(self, ctx): pass
    def execute(self, ctx):
        print('token=NEVER_PERSIST_RAW_WORKER_SECRET' * 100000, flush=True)
        # FAILURE
    def collect(self, ctx): return {'metrics': {'value': 3.0}}
    def close(self, ctx):
        # CLOSE_FAILURE
native.NativeRun = Tiny
# START_GATE
raise SystemExit(worker(Path(sys.argv[1]), Path(sys.argv[2])))
'''
    if held_parent_log:
        script = script.replace('# START_GATE', '''import time
deadline = time.monotonic() + 10
while not Path(sys.argv[3]).exists():
    if time.monotonic() >= deadline: raise TimeoutError('parent policy not recorded')
    time.sleep(.01)
''')
        write = Logger._write_file
        def held_write(logger, record):
            if record['event'] == 'logging.unstructured_record' and record.get('operation') == 'worker_stdout':
                with cache_lock(logger.path.parent, timeout=0):
                    write(logger, record)
                    gate.touch()
                    # A spawned worker runs entirely while this parent record owns the lock.
                    if children:
                        children[-1].wait(timeout=10)
            else:
                write(logger, record)
        monkeypatch.setattr(Logger, '_write_file', held_write)
    script = script.replace('# FAILURE', "raise ValueError('token=PRIVATE_WORKER_EXCEPTION')" if worker_fails is True else 'pass')
    script = script.replace('# CLOSE_FAILURE', "raise RuntimeError('token=PRIVATE_WORKER_CLEANUP')" if worker_fails == 'close' else 'pass')
    class TinyWorker(native_popen):
        def __init__(self, args, **kwargs):
            assert kwargs['stdout'] == subprocess.DEVNULL
            assert kwargs['stderr'] == subprocess.STDOUT
            super().__init__([sys.executable, '-B', '-c', script, *args[-2:], str(gate)], **kwargs)
            children.append(self)
    monkeypatch.setattr(module.subprocess, 'Popen', TinyWorker)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec)
    assert outcome.status == ('failed' if worker_fails else 'succeeded')
    if not worker_fails:
        assert outcome.result['metrics']['value'] == 3.0
    assert children and all(child.poll() is not None for child in children)
    assert not list(spec.run_dir.rglob('worker.log'))
    paths = list(spec.assets_dir.glob('logs/events*.jsonl'))
    text = '\n'.join(path.read_text(encoding='utf-8') for path in paths)
    assert 'NEVER_PERSIST_RAW_WORKER_SECRET' not in text
    rows = [json.loads(line) for path in paths for line in path.read_text(encoding='utf-8').splitlines()]
    assert any(row['event'] == 'logging.unstructured_record' for row in rows)
    child_rows = [row for row in rows if row['pid'] == children[0].pid]
    assert child_rows and all(row['process_name'] == 'scientific-worker' for row in child_rows)
    assert all(row['task_id'] == spec.id for row in rows)
    assert len({row['runtime_id'] for row in rows}) == 1
    assert len({row['attempt_id'] for row in rows}) == 1
    assert len({row['request_id'] for row in rows}) == 1
    if worker_fails is True:
        current = [json.loads(line) for line in (spec.assets_dir / 'logs/events.jsonl').read_text(encoding='utf-8').splitlines()]
        failures = [row for row in current if row['event'] == 'task.failed']
        assert len(failures) == 1 and failures[0]['pid'] == children[0].pid
        assert 'PRIVATE_WORKER_EXCEPTION' not in text
    if worker_fails == 'close':
        current = [json.loads(line) for line in (spec.assets_dir / 'logs/events.jsonl').read_text(encoding='utf-8').splitlines()]
        cleanup = [row for row in current if row['event'] == 'operation.failed' and row['operation'] == 'cleanup']
        assert len(cleanup) == 1 and cleanup[0]['pid'] == children[0].pid
        assert cleanup[0]['exception_type'] == 'RuntimeError' and 'close' in cleanup[0]['traceback']
        assert 'PRIVATE_WORKER_CLEANUP' not in text


def test_in_process_logger_failure_does_not_change_scientific_success(
        source_study, run_root, payload_factory, monkeypatch):
    from rpipe.structure.make import plan
    from rpipe.api import run
    from rpipe.structure.system.logger import Logger
    monkeypatch.setattr(Logger, '_write_file', lambda *args: (_ for _ in ()).throw(PermissionError('PRIVATE')))
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=payload_factory())
    assert outcome.status == 'succeeded'
    assert outcome.evidence_complete is False
    assert outcome.error is None and not outcome.cleanup_errors


@pytest.mark.parametrize('boundary', ['execute', 'publication', 'process'])
@pytest.mark.parametrize('logger_raises', [False, True])
def test_parent_failure_logs_once_and_logger_cannot_replace_first_cause(
        source_study, run_root, payload_factory, monkeypatch, boundary, logger_raises):
    from rpipe.structure.make import plan
    from rpipe.api import run
    from rpipe.structure.system.logger import Logger
    flow = importlib.import_module('rpipe.flow.run')
    execution = importlib.import_module('rpipe.structure.execution')
    spec = plan(source_study, run_root=run_root)[0]
    payload = payload_factory()
    def primary(*args):
        raise ValueError('token=PRIVATE_PARENT_EXCEPTION')
    if boundary == 'execute':
        payload.execute = primary
    elif boundary == 'process':
        configure = flow.configure
        def configured(ctx, **kwargs):
            configure(ctx, **kwargs)
            ctx.state['process_hook'] = primary
        monkeypatch.setattr(flow, 'configure', configured)
    else:
        write = execution.write_json
        def failed_publication(path, value):
            if path == spec.result_path:
                primary()
            return write(path, value)
        monkeypatch.setattr(execution, 'write_json', failed_publication)
    if logger_raises:
        def refused(*args, **kwargs):
            raise OSError('token=PRIVATE_LOGGER_EXCEPTION')
        monkeypatch.setattr(Logger, 'exception', refused)
    outcome = run(spec, payload=payload)
    assert outcome.status == 'failed' and outcome.error['type'] == 'ValueError'
    assert not outcome.cleanup_errors
    assert spec.result_path.exists() is (boundary == 'process')
    text = (spec.assets_dir / 'logs/events.jsonl').read_text(encoding='utf-8')
    rows = [json.loads(line) for line in text.splitlines()]
    failures = [row for row in rows if row['event'] == 'task.failed']
    if not logger_raises:
        assert len(failures) == 1
        assert failures[0]['exception_type'] == 'ValueError' and 'primary' in failures[0]['traceback']
        assert failures[0]['phase'] == ('process' if boundary == 'process' else 'write' if boundary == 'publication' else 'execute')
    assert 'PRIVATE_PARENT_EXCEPTION' not in text and 'PRIVATE_LOGGER_EXCEPTION' not in text


@pytest.mark.parametrize('primary_fails', [False, True])
@pytest.mark.parametrize('logger_raises', [False, True])
def test_cleanup_logs_complete_context_without_replacing_primary_or_secondary_error(
        source_study, run_root, payload_factory, monkeypatch, primary_fails, logger_raises):
    from rpipe.structure.make import plan
    from rpipe.api import run
    from rpipe.structure.system.logger import Logger
    spec = plan(source_study, run_root=run_root)[0]
    payload = payload_factory(fail='execute' if primary_fails else None,
                              close_error='token=PRIVATE_CLEANUP_EXCEPTION')
    if logger_raises:
        def refused(*args, **kwargs):
            raise OSError('token=PRIVATE_LOGGER_EXCEPTION')
        monkeypatch.setattr(Logger, 'exception', refused)
    outcome = run(spec, payload=payload)
    assert outcome.status == 'failed'
    assert (outcome.error['type'] if outcome.error else None) == ('ValueError' if primary_fails else None)
    assert [record['type'] for record in outcome.cleanup_errors] == ['RuntimeError']
    text = (spec.assets_dir / 'logs/events.jsonl').read_text(encoding='utf-8')
    rows = [json.loads(line) for line in text.splitlines()]
    cleanup = [row for row in rows if row['event'] == 'operation.failed' and row.get('operation') == 'cleanup']
    if not logger_raises:
        assert len(cleanup) == 1 and cleanup[0]['exception_type'] == 'RuntimeError'
        assert 'close' in cleanup[0]['traceback']
        assert sum(row['event'] == 'task.failed' for row in rows) == int(primary_fails)
    assert 'PRIVATE_CLEANUP_EXCEPTION' not in text and 'PRIVATE_LOGGER_EXCEPTION' not in text
