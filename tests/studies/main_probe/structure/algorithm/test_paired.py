"""Probe ownership boundaries use fake algorithms and no scientific workload."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rpipe.structure.artifact.io import safe_error_record
from rpipe.flow.context import Cancelled

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def pair_run(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[5] / 'studies/main_probe/structure/algorithm/paired.py'
    spec = importlib.util.spec_from_file_location('_probe_pair_test', source)
    paired = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paired)
    state = SimpleNamespace(fail=None, error=RuntimeError('operation failed'),
                            cleanup_fail=set(), silent_logger_failure=False, resources={})

    def at(stage):
        if state.fail == stage:
            raise state.error

    class Resource:
        def __init__(self, name):
            at(name)
            self.name, self.closed = name, 0
            self.diagnostics_complete = True
            state.resources[name] = self

        def close(self):
            self.closed += 1
            if self.name == 'eval_system_logger' and state.silent_logger_failure:
                self.diagnostics_complete = False
            if self.name in state.cleanup_fail:
                raise OSError(self.name)

        remove = close

    class Module:
        training = True

        def __init__(self, name):
            self.name = name
            self.net = self.model = self

        def register_forward_pre_hook(self, hook):
            return Resource(self.name + '_hook')

        def to(self, device):
            assert device == 'cpu'
            return self

        def parameters(self):
            return []

        def load_state_dict(self, value):
            pass

    class Data(Resource):
        def __init__(self, name):
            super().__init__(name)
            self._train_set = []
            self.original_set = self._train_set
            self.original_iter = self.iter_batches

        def iter_batches(self, split):
            return iter(())

    class Model(Resource):
        def __init__(self, name):
            super().__init__(name)
            self.module = Module(name)

    class System:
        def __init__(self, name):
            at(name)
            self.logger = Resource(name + '_logger')

        def load_checkpoint(self, name):
            return {}

        def checkpoint_dir(self):
            return tmp_path

    class Tracker:
        def __init__(self, assets=None):
            at('eval_tracker')

        def segment_mean(self, split):
            return {'Loss': 0.0}

    class Train:
        def __init__(self, config):
            pass

        def run(self, data, model, system, tracker):
            at('native_train')
            return {'completed': True}

    class Eval(Train):
        def run(self, data, model, system, tracker):
            at('native_eval')
            return {'completed': True}

    cfg = {'model': {}, 'tag': 'fixture', 'fixture': {'optimizer': {}},
           'log': {}, 'metric': {}, 'step': 0}

    def logger(path, **kwargs):
        name = 'old_eval_writer' if 'eval' in path else 'old_train_writer'
        return SimpleNamespace(writer=Resource(name), mean={'test/Loss': 0.0},
                               compare=lambda split: True, reset=lambda: None)

    def train(*args):
        at('old_train')
        cfg['step'] += 30

    def old_test(*args):
        at('old_test')

    old_models = iter(('old_train', 'old_eval'))
    archive = SimpleNamespace(
        config=SimpleNamespace(cfg=cfg), configure=lambda *args: None,
        verify_exports=lambda: [], datasets=lambda name: {},
        model=SimpleNamespace(make_model=lambda config: Module(next(old_models)),
                              make_optimizer=lambda *args, **kw: SimpleNamespace(state_dict=lambda: {}),
                              make_scheduler=lambda *args: SimpleNamespace(state_dict=lambda: {})),
        dataset=SimpleNamespace(make_data_loader=lambda *args, **kw: {'train': [], 'test': []},
                                process_dataset=lambda datasets: None),
        metric=SimpleNamespace(make_logger=logger),
        train_model=SimpleNamespace(train=train, test=old_test),
        test_model=SimpleNamespace(test=lambda *args: at('old_eval')))
    torch = SimpleNamespace(__version__='fake-cpu', save=lambda *args: None,
                            set_rng_state=lambda *args: None)
    for name, module in {
        'torch': torch,
        'rpipe.structure.algorithm.train': SimpleNamespace(TrainAlgorithm=Train),
        'rpipe.structure.algorithm.evaluation': SimpleNamespace(EvalAlgorithm=Eval),
        'rpipe.structure.algorithm.tracker': SimpleNamespace(AlgorithmTracker=Tracker),
        'rpipe.structure.system.factory': SimpleNamespace(
            SystemFactory=SimpleNamespace(build=lambda *args: System('eval_system'))),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(paired, 'bootstrap', lambda: None)
    monkeypatch.setattr(paired, 'MainArchive', lambda workspace: archive)
    monkeypatch.setattr(paired, 'source_manifest', lambda: {})
    monkeypatch.setattr(paired, 'model_state', lambda model: {})
    monkeypatch.setattr(paired, 'rng', lambda device: {'cpu': None})
    monkeypatch.setattr(paired, 'seed_runtime', lambda device: None)
    monkeypatch.setattr(paired, 'release_memory', lambda device: None)
    monkeypatch.setattr(paired, 'cpu_tree', lambda value: value)
    monkeypatch.setattr(paired, 'native_data', lambda *args: Data('eval_data'))
    monkeypatch.setattr(paired, 'native_model', lambda *args: Model('eval_model'))
    monkeypatch.setattr(paired.subprocess, 'check_output', lambda *args, **kwargs: 'fixed-head')
    for filename, body in [('PREPARED.json', {'passed': True}),
                           ('SOURCE_MANIFEST.json', {'files': {}}), ('ENVIRONMENT.json', {})]:
        (tmp_path / filename).write_text(json.dumps(body), encoding='utf-8')
    native = (Data('caller_data'), Model('caller_model'), System('caller_system'),
              Tracker(), {'cpu': None})
    state.run = lambda: paired.run_pair(tmp_path, 'cpu', ('MNIST', 'linear'), native)
    state.paired = paired
    state.native = native
    return state


def assert_released(state):
    for name, resource in state.resources.items():
        assert resource.closed == (0 if name in ('caller_data', 'caller_model', 'caller_system_logger') else 1), name
        if hasattr(resource, 'original_set'):
            assert resource._train_set is resource.original_set
            assert resource.iter_batches == resource.original_iter


@pytest.mark.parametrize('stage', [
    'old_train_writer', 'old_train', 'old_test', 'caller_model_hook', 'native_train',
    'old_eval_writer', 'old_eval', 'eval_data', 'eval_model', 'eval_model_hook',
    'eval_system', 'eval_tracker', 'native_eval',
])
def test_run_pair_releases_only_acquired_local_resources(pair_run, stage):
    pair_run.fail = stage
    with pytest.raises(RuntimeError) as caught:
        pair_run.run()
    assert caught.value is pair_run.error
    assert_released(pair_run)


@pytest.mark.parametrize('error_type', [Cancelled, KeyboardInterrupt])
@pytest.mark.parametrize('stage', ['old_train', 'native_train', 'native_eval'])
def test_cancellation_preserves_primary_and_releases_resources(pair_run, stage, error_type):
    error = error_type('cancelled')
    pair_run.fail, pair_run.error = stage, error
    with pytest.raises(type(error)) as caught:
        pair_run.run()
    assert caught.value is error
    assert_released(pair_run)


def test_success_releases_resources_without_closing_the_callers(pair_run):
    report = pair_run.run()
    assert len(report['runs']) == 1
    assert 'eval_system_logger' in pair_run.resources
    assert_released(pair_run)


def test_silent_eval_logger_failure_marks_callers_evidence_incomplete(pair_run):
    pair_run.silent_logger_failure = True
    report = pair_run.run()
    assert len(report['runs']) == 1
    assert pair_run.native[2].logger.diagnostics_complete is False
    assert_released(pair_run)


@pytest.mark.parametrize('error_type', [RuntimeError, Cancelled])
@pytest.mark.parametrize(('stage', 'cleanup_fail'), [
    ('old_train', {'old_train_writer', 'old_train_hook'}),
    ('native_train', {'caller_model_hook'}),
    ('old_eval', {'old_eval_writer'}),
    ('native_eval', {'eval_system_logger', 'eval_model_hook', 'eval_model', 'eval_data'}),
])
def test_primary_and_every_cleanup_failure_survive(pair_run, stage, cleanup_fail, error_type):
    error = error_type('operation failed')
    pair_run.fail, pair_run.error = stage, error
    pair_run.cleanup_fail = cleanup_fail
    with pytest.raises(type(error)) as caught:
        pair_run.run()
    assert caught.value is error
    assert {str(item) for item in error.cleanup_errors} == pair_run.cleanup_fail
    assert len(safe_error_record(error)['causes']) == len(cleanup_fail)
    assert_released(pair_run)


def test_cleanup_failure_without_primary_still_attempts_remaining_resources(pair_run):
    pair_run.cleanup_fail = {'eval_system_logger', 'eval_model_hook', 'eval_data'}
    with pytest.raises(OSError, match='eval_system_logger') as caught:
        pair_run.run()
    assert {str(item) for item in caught.value.cleanup_errors} == {'eval_model_hook', 'eval_data'}
    assert_released(pair_run)
