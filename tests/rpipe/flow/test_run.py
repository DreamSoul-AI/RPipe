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
    from rpipe.payloads.checkpoints import SystemFactory
    from rpipe.structure.contracts import SystemConfig

    before = file_bytes(tensor_study)
    train, evaluate = plan(tensor_study, run_root=run_root)
    assert evaluate.train_id == train.id
    make((train, evaluate))
    assert run(train).status == 'succeeded'
    trained = read_run(train)
    assert trained['execute']['steps'] == 2
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), train.assets_dir)
    try:
        best = system.load_checkpoint('best')
        latest = system.load_checkpoint('latest')
    finally:
        system.close()
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
