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
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import cache_lock
    from rpipe.structure.system.logger import Logger
    module = importlib.import_module('rpipe.flow.worker')
    native_popen = subprocess.Popen
    children = []
    gate = tmp_path / 'parent-policy-recorded'
    script = '''
import sys
from pathlib import Path
from rpipe.flow.worker import worker
import rpipe.flow.stages as stages
from rpipe.structure.system.logger import Logger
class Tiny:
    def close(self):
        # CLOSE_FAILURE
def prepare(ctx):
    ctx.state['logger'] = Logger(ctx.layout.assets_dir, **ctx.state['logging_context'])
    ctx.state['logger'].bind(phase='prepare')
    ctx.register_resource(Tiny())
def execute(ctx):
    print('token=NEVER_PERSIST_RAW_WORKER_SECRET' * 100000, flush=True)
    # FAILURE
def collect(ctx): return {'metrics': {'value': 3.0}}
stages.prepare, stages.execute, stages.collect = prepare, execute, collect
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
    assert outcome.status == ('unknown' if worker_fails == 'close' else 'failed' if worker_fails else 'succeeded')
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
        source_study, run_root, science_factory, monkeypatch, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.system.logger import Logger
    monkeypatch.setattr(Logger, '_write_file', lambda *args: (_ for _ in ()).throw(PermissionError('PRIVATE')))
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    assert outcome.status == 'succeeded'
    assert outcome.evidence_complete is False
    assert outcome.error is None and not outcome.cleanup_errors


@pytest.mark.parametrize('boundary', ['execute', 'publication', 'process'])
@pytest.mark.parametrize('logger_raises', [False, True])
def test_parent_failure_logs_once_and_logger_cannot_replace_first_cause(
        source_study, run_root, science_factory, monkeypatch, boundary, logger_raises, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.system.logger import Logger
    flow = importlib.import_module('rpipe.flow.run')
    execution = importlib.import_module('rpipe.structure.artifact.io')
    stages = importlib.import_module('rpipe.flow.stages')
    spec = plan(source_study, run_root=run_root)[0]
    payload = science_factory()
    def primary(*args):
        raise ValueError('token=PRIVATE_PARENT_EXCEPTION')
    if boundary == 'execute':
        payload.execute = primary
    elif boundary == 'process':
        monkeypatch.setattr(stages, 'process', primary)
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
    science_worker(payload)
    outcome = run(spec)
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
        source_study, run_root, science_factory, monkeypatch, primary_fails, logger_raises, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.system.logger import Logger
    spec = plan(source_study, run_root=run_root)[0]
    payload = science_factory(fail='execute' if primary_fails else None,
                              close_error='token=PRIVATE_CLEANUP_EXCEPTION')
    if logger_raises:
        def refused(*args, **kwargs):
            raise OSError('token=PRIVATE_LOGGER_EXCEPTION')
        monkeypatch.setattr(Logger, 'exception', refused)
    science_worker(payload)
    outcome = run(spec)
    assert outcome.status == 'unknown'
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
    assert (spec.run_dir / '.execution').exists()
    assert not spec.result_path.exists()


def test_owner_logs_early_science_failure_once_when_no_scientific_logger_was_created(
        source_study, run_root, monkeypatch):
    import os
    from rpipe.flow.run import run
    from rpipe.structure.make import plan

    module = importlib.import_module('rpipe.flow.worker')
    native_popen = subprocess.Popen
    script = '''from pathlib import Path
import sys
from rpipe.flow import stages
from rpipe.flow.worker import worker
def fail_before_system(ctx):
    assert 'logger' not in ctx.state
    raise ValueError('secret=PRIVATE_EARLY_FAILURE')
stages.prepare = fail_before_system
raise SystemExit(worker(Path(sys.argv[1]), Path(sys.argv[2])))
'''
    class EarlyFailure(native_popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, '-B', '-c', script, *args[-2:]], **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', EarlyFailure)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec)
    assert outcome.status == 'failed' and outcome.error['type'] == 'ValueError'
    rows = [json.loads(line) for line in (spec.assets_dir / 'logs/events.jsonl').read_text(encoding='utf-8').splitlines()]
    failures = [row for row in rows if row['event'] == 'task.failed']
    assert len(failures) == 1
    assert failures[0]['pid'] == os.getpid()
    assert failures[0]['phase'] == 'prepare'
    assert failures[0]['exception_type'] == 'ValueError'
    assert 'PRIVATE_EARLY_FAILURE' not in json.dumps(rows)
    assert not spec.result_path.exists() and not (spec.run_dir / '.execution').exists()
