"""Finite public execution journeys, with no external assets or downloads."""

import json
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.e2e
def test_native_public_run_trains_two_updates_then_independently_evaluates_own_best(
        tensor_study, run_root, file_bytes):
    from rpipe.api import make, plan, process, read_run, run
    from rpipe.structure.system.factory import SystemFactory
    from rpipe.structure.contracts import SystemConfig

    before = file_bytes(tensor_study)
    train, evaluate = plan(tensor_study, run_root=run_root)
    assert evaluate.train_id == train.id
    make((train, evaluate))
    assert run(train).status == 'succeeded'
    trained = read_run(train)
    assert trained['execute']['steps'] == 2
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), train.assets_dir)
    best = system.load_checkpoint('best')
    latest = system.load_checkpoint('latest')
    assert best is not None and latest['step'] == 2
    assert set(('model', 'optimizer', 'scheduler', 'training_state')) <= set(latest)
    assert run(evaluate).status == 'succeeded'
    evaluated = read_run(evaluate)
    assert evaluated['execute']['step'] == best['step']
    assert 'eval_accuracy' in evaluated['metrics']
    science = {s.id: s.result_path.read_bytes() for s in (train, evaluate)}
    assert process((train, evaluate))['complete'] is True
    assert all(s.result_path.read_bytes() == science[s.id] for s in (train, evaluate))
    assert file_bytes(tensor_study) == before


@pytest.mark.integration
def test_silent_worker_cannot_reuse_previous_worker_response(
        source_study, run_root, monkeypatch):
    import importlib
    from rpipe.api import plan, run

    spec = plan(source_study, run_root=run_root)[0]
    spec.run_dir.mkdir(parents=True)
    previous = spec.run_dir / 'worker-output.json'
    old = json.dumps({'result': {'metrics': {'old': 99}}, 'error': None, 'cleanup_errors': []}).encode()
    previous.write_bytes(old)
    native_popen = subprocess.Popen

    class SilentWorker(native_popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, '-B', '-c', 'pass'], **kwargs)

    module = importlib.import_module('rpipe.flow.run')
    monkeypatch.setattr(module.subprocess, 'Popen', SilentWorker)
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert not spec.result_path.exists()
    assert previous.read_bytes() == old


@pytest.mark.integration
def test_abrupt_worker_exit_keeps_unknown_owner_when_descendants_cannot_be_verified(
        source_study, run_root, monkeypatch):
    import importlib
    import os
    from types import SimpleNamespace
    from rpipe.api import plan, run
    from rpipe.structure.contracts import BusyError

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('recipe: recipe.py\n')
    (source_study / 'recipe.py').write_text('def register(ctx): pass\n', encoding='utf-8')
    module = importlib.import_module('rpipe.flow.run')
    native_popen = subprocess.Popen

    class AbruptWorker(native_popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, '-B', '-c', 'raise SystemExit(17)'], **kwargs)

    # Simulate the portable Windows tree-verification limit, without leaving a real child.
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ=os.environ, pathsep=os.pathsep))
    monkeypatch.setattr(module.subprocess, 'Popen', AbruptWorker)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec)
    assert outcome.status == 'unknown'
    assert outcome.error['type'] == 'ChildExit' and outcome.error['exit_code'] == 17
    assert (spec.run_dir / '.execution').is_dir()
    assert not spec.result_path.exists()
    with pytest.raises(BusyError):
        run(spec)
