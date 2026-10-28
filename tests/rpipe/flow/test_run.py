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
    from rpipe.structure.make import publish, plan
    from rpipe.api import read_run
    from rpipe.flow.process import process_study as process
    from rpipe.flow.run import run
    from rpipe.structure.system.factory import build_system
    from rpipe.structure.control import SystemConfig

    before = file_bytes(tensor_study)
    train, evaluate = plan(tensor_study, run_root=run_root)
    assert evaluate.train_id == train.id
    publish((train, evaluate))
    assert run(train).status == 'succeeded'
    trained = read_run(train)
    assert trained['execute']['steps'] == 2
    system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), train.assets_dir)
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
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    spec = plan(source_study, run_root=run_root)[0]
    spec.run_dir.mkdir(parents=True)
    previous = spec.run_dir / 'worker-output.json'
    old = json.dumps({'result': {'metrics': {'old': 99}}, 'error': None, 'cleanup_errors': []}).encode()
    previous.write_bytes(old)
    native_popen = subprocess.Popen

    class SilentWorker(native_popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, '-B', '-c', 'pass'], **kwargs)

    module = importlib.import_module('rpipe.flow.worker')
    monkeypatch.setattr(module.subprocess, 'Popen', SilentWorker)
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert not spec.result_path.exists()
    assert previous.read_bytes() == old


@pytest.mark.integration
@pytest.mark.parametrize('extension', ['recipe', 'hook', 'disabled-hook'])
def test_abrupt_worker_exit_keeps_unknown_owner_when_descendants_cannot_be_verified(
        source_study, run_root, monkeypatch, extension):
    import importlib
    import os
    from types import SimpleNamespace
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.control import BusyError

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('recipe: recipe.py\n' if extension == 'recipe' else
                     f'flow: {{study_phases: {str(extension == "hook").lower()}}}\n')
    if extension == 'recipe':
        (source_study / 'recipe.py').write_text('def register(ctx): pass\n', encoding='utf-8')
    else:
        (source_study / 'flow').mkdir()
        (source_study / 'flow' / 'prepare.py').write_text('def run(ctx): pass\n', encoding='utf-8')
    module = importlib.import_module('rpipe.flow.worker')
    native_popen = subprocess.Popen

    class AbruptWorker(native_popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, '-B', '-c', 'raise SystemExit(17)'], **kwargs)

    # Simulate the portable Windows tree-verification limit, without leaving a real child.
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ=os.environ, pathsep=os.pathsep))
    monkeypatch.setattr(module.subprocess, 'Popen', AbruptWorker)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec)
    unknown = extension != 'disabled-hook'
    assert outcome.status == ('unknown' if unknown else 'failed')
    assert outcome.error['type'] == 'ChildExit' and outcome.error['exit_code'] == 17
    assert (spec.run_dir / '.execution').is_dir() is unknown
    assert not spec.result_path.exists()
    if unknown:
        _rejected = run(spec, retry=True)
        assert _rejected.status == 'rejected'
        assert _rejected.error['type'] == 'BusyError'


@pytest.mark.integration
@pytest.mark.parametrize('extension', ['recipe', 'hook', 'workers', 'hf', 'disabled-hook', 'no-hook'])
def test_windows_hard_timeout_admission_matches_possible_descendants(
        source_study, run_root, monkeypatch, extension):
    import importlib
    import os
    from types import SimpleNamespace
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import load_config, write_config
    from rpipe.structure.control import AdmissionError
    from rpipe.flow.context import Budget

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('recipe: recipe.py\n' if extension == 'recipe' else
                     f'flow: {{study_phases: {str(extension != "disabled-hook").lower()}}}\n')
    if extension == 'recipe':
        (source_study / 'recipe.py').write_text('def register(ctx): pass\n', encoding='utf-8')
    elif extension in {'hook', 'disabled-hook'}:
        (source_study / 'flow').mkdir()
        (source_study / 'flow' / 'prepare.py').write_text('def run(ctx): pass\n', encoding='utf-8')
    config = load_config(source_study / 'experiment_config.yaml')
    if extension == 'workers':
        config['data']['config'] = {'num_workers': 1}
    if extension == 'hf':
        config['algorithm']['source'] = 'transformers_trainer'
    write_config(source_study / 'experiment_config.yaml', config)
    spec = plan(source_study, run_root=run_root)[0]
    module = importlib.import_module('rpipe.flow.worker')
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ=os.environ, pathsep=os.pathsep))
    possible = extension not in {'disabled-hook', 'no-hook'}
    if possible:
        with pytest.raises(AdmissionError):
            module.validate_budget(spec, Budget(timeout_seconds=1))
    else:
        assert not module.validate_budget(spec, Budget(timeout_seconds=1))
    assert not run_root.exists()


@pytest.mark.integration
@pytest.mark.parametrize('fault', ['request', 'identity', 'attempt', 'schema', 'scalar',
                                  'truncated', 'missing-draft', 'invalid-closure', 'stage', 'error-logged'])
def test_foreign_or_malformed_worker_response_never_publishes_scientific_success(
        source_study, run_root, monkeypatch, fault):
    import importlib
    from rpipe.flow.run import run
    from rpipe.structure.make import plan

    module = importlib.import_module('rpipe.flow.worker')
    native_popen = subprocess.Popen
    script = '''import json, sys
from pathlib import Path
request, output, fault = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
body = json.loads(request.read_text(encoding='utf-8'))
response = {'schema': 1, 'request': body['request'], 'identity': body['identity'],
            'attempt': body['attempt'], 'result': {'metrics': {'value': 3.0}},
            'error': None, 'cleanup_errors': [], 'unknown_resources': False,
            'science_closed': True, 'diagnostics_complete': True, 'environment': {},
            'stage': 'write', 'error_logged': False}
if fault in {'request', 'attempt'}: response[fault] = 'f' * 32
elif fault == 'identity': response['identity'] = {'foreign': True}
elif fault == 'schema': response['schema'] = True
elif fault == 'scalar': response = []
elif fault == 'missing-draft': response['result'] = {}
elif fault == 'invalid-closure': response['unknown_resources'] = True
elif fault == 'stage': response['stage'] = 'foreign-phase'
elif fault == 'error-logged': response['error_logged'] = 'true'
output.write_text('{' if fault == 'truncated' else json.dumps(response), encoding='utf-8')
'''
    class ForeignWorker(native_popen):
        def __init__(self, args, **kwargs):
            super().__init__([sys.executable, '-B', '-c', script, *args[-2:], fault], **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', ForeignWorker)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert outcome.error is not None
    assert not spec.result_path.exists()
