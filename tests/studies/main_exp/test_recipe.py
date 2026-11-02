"""Preflight ownership is exercised without installing or running a study."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rpipe.flow.context import Cancelled
from rpipe.structure.artifact.io import safe_error_record


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def preflight_run(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / 'studies/main_exp/recipe.py'
    spec = importlib.util.spec_from_file_location('_main_exp_preflight_test', source)
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    state = SimpleNamespace(fail=None, error=RuntimeError('operation failed'),
                            cleanup_fail=set(), silent_logger_failure=False,
                            resources={}, all_resources=[], closed=[], calls=[],
                            compute_count=0, combinations=[('MNIST', 'linear')])

    def at(stage):
        state.calls.append(stage)
        if state.fail == stage:
            raise state.error

    class Resource:
        def __init__(self, name):
            at(name)
            self.name, self.closed = name, 0
            self.diagnostics_complete = True
            state.resources[name] = self
            state.all_resources.append(self)

        def close(self):
            self.closed += 1
            state.closed.append(self.name)
            if self.name == 'native_logger' and state.silent_logger_failure:
                self.diagnostics_complete = False
            if self.name in state.cleanup_fail:
                raise OSError(self.name + ' cleanup failed')

    class Module:
        def to(self, device):
            assert device == 'cpu'
            return self

        def parameters(self):
            return []

    class Observation:
        def result(self):
            return {'batches': 0, 'samples': 0, 'digest': 'empty-fixture'}

    class Data(Resource):
        def __init__(self):
            super().__init__('native_data')
            self.meta = {}

    class Tracker:
        def __init__(self, assets):
            at('native_tracker')
            self._meters = {split: {name: SimpleNamespace(history=[value])
                                   for name, value in [('Loss', .5), ('Accuracy', 100.)]}
                            for split in ('train', 'test')}

    class Train:
        def __init__(self, config, *, checkpoint_dir):
            at('native_algorithm')

        def compute(self, data, model, system, tracker, *, cancel_check):
            cancel_check()
            state.compute_count += 1
            if state.compute_count == 2:
                at('second_native_compute')
            at('native_compute')
            self.scheduler = SimpleNamespace(state_dict=lambda: {})
            self.snapshots[200] = {'model': {}, 'rng': {}, 'scheduler': {}}
            return {'completed': True}

    cfg = {'iteration': 0, 'linear': {'batch_size': 250}}

    def configure(*args):
        cfg['iteration'] = 0

    def make_logger(path):
        writer = Resource('original_writer')

        def reset():
            at('original_reset')

        return SimpleNamespace(writer=writer, reset=reset, state_dict=lambda: {},
                               mean={'train/Loss': .5, 'train/Accuracy': 100.,
                                     'test/Loss': .5, 'test/Accuracy': 100.})

    def train(*args):
        at('original_train')
        cfg['iteration'] = 200

    def make_system(*args):
        at('native_system')
        return SimpleNamespace(logger=Resource('native_logger'))

    def make_model(*args, **kwargs):
        at('native_model')
        return SimpleNamespace(module=Module())

    def save(value, path):
        at('original_save')
        Path(path).write_bytes(b'original diagnostic fixture')

    archive = SimpleNamespace(
        configure=configure, config=SimpleNamespace(cfg=cfg),
        datasets=lambda *args: at('original_datasets') or {},
        model=SimpleNamespace(make_model=lambda *args: Module(),
                              make_optimizer=lambda *args: SimpleNamespace(state_dict=lambda: {}),
                              make_scheduler=lambda *args: SimpleNamespace(state_dict=lambda: {})),
        dataset=SimpleNamespace(process_dataset=lambda datasets: None,
                                make_data_loader=lambda *args: {'train': [], 'test': []}),
        metric=SimpleNamespace(make_logger=make_logger),
        train_model=SimpleNamespace(train=train, test=lambda *args: at('original_test')))
    for name, module in {
        'torch': SimpleNamespace(__version__='fake-cpu', save=save),
        'rpipe.structure.algorithm.train': SimpleNamespace(TrainAlgorithm=Train),
        'rpipe.structure.algorithm.tracker': SimpleNamespace(AlgorithmTracker=Tracker),
        'rpipe.structure.system.factory': SimpleNamespace(build_system=make_system),
        'rpipe.structure.data.factory': SimpleNamespace(build_data=lambda *args, **kwargs: Data()),
        'rpipe.structure.model.factory': SimpleNamespace(build_model=make_model),
        'rpipe.structure.system.runtime': SimpleNamespace(apply_runtime=lambda *args: None),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(recipe, 'bootstrap', lambda: None)
    monkeypatch.setattr(recipe, 'install', lambda *args, **kwargs: archive)
    monkeypatch.setattr(recipe, 'Observation', Observation)
    monkeypatch.setattr(recipe, '_state_cpu', lambda module: {})
    monkeypatch.setattr(recipe, '_rng_state', lambda: {})
    monkeypatch.setattr(recipe, '_rng_equal', lambda *args: True)
    compare = recipe._compare_states

    def compare_states(*args):
        at('native_comparison')
        return compare(*args)

    monkeypatch.setattr(recipe, '_compare_states', compare_states)
    monkeypatch.setattr(recipe.subprocess, 'check_output', lambda *args, **kwargs: 'fixed-head')
    state.output = tmp_path / 'output'
    state.peer = tmp_path / 'peer' / 'COMPARISON.json'
    state.peer.parent.mkdir()
    state.peer.write_bytes(b'previous peer evidence')
    state.recipe = recipe
    state.run = lambda: recipe.preflight(tmp_path / 'data', state.output,
                                        device='cpu', combinations=state.combinations)
    return state


def assert_released(state):
    assert all(resource.closed == 1 for resource in state.resources.values())
    acquired = set(state.resources)
    assert state.closed == [name for name in ('original_writer', 'native_data', 'native_logger')
                            if name in acquired]
    assert state.peer.read_bytes() == b'previous peer evidence'


@pytest.mark.parametrize('selection', [
    [], iter(()), [('MNIST', 'linear'), ('MNIST', 'linear')],
    [('unknown', 'linear')], [('MNIST', 'unknown')], ['MNIST'], [('MNIST',)],
], ids=['empty-list', 'empty-iterator', 'duplicate', 'unknown-data',
        'unknown-model', 'string-pair', 'short-pair'])
def test_preflight_rejects_invalid_selection_before_scientific_setup(
        preflight_run, monkeypatch, selection):
    state = preflight_run

    def forbidden():
        raise AssertionError('scientific setup started before selection admission')

    monkeypatch.setattr(state.recipe, 'bootstrap', forbidden)
    with pytest.raises(ValueError, match='combination'):
        state.recipe.preflight(state.output.parent / 'data', state.output,
                               device='cpu', combinations=selection)
    assert not state.output.exists()
    assert state.calls == []
    assert state.peer.read_bytes() == b'previous peer evidence'


def test_preflight_consumes_explicit_generator_once(preflight_run):
    state = preflight_run
    state.combinations = (pair for pair in [('MNIST', 'linear')])
    report = state.run()
    assert [(row['data'], row['model']) for row in report['runs']] == [('MNIST', 'linear')]
    assert_released(state)


def test_preflight_omitted_selection_uses_declared_matrix(preflight_run, monkeypatch):
    state = preflight_run
    monkeypatch.setattr(state.recipe, 'DATA_NAMES', ('MNIST', 'CIFAR10'))
    monkeypatch.setattr(state.recipe, 'MODEL_NAMES', ('linear',))
    state.combinations = None
    report = state.run()
    assert [(row['data'], row['model']) for row in report['runs']] == [
        ('MNIST', 'linear'), ('CIFAR10', 'linear')]
    assert len(state.all_resources) == 6
    assert all(resource.closed == 1 for resource in state.all_resources)


@pytest.mark.parametrize('stage', [
    'original_datasets', 'original_writer', 'original_train', 'original_test',
    'original_save', 'original_reset', 'native_system', 'native_logger',
    'native_data', 'native_model', 'native_tracker', 'native_algorithm',
    'native_compute', 'native_comparison',
])
def test_preflight_failure_releases_only_acquired_resources(preflight_run, stage):
    state = preflight_run
    state.fail = stage
    with pytest.raises(RuntimeError) as caught:
        state.run()
    assert caught.value is state.error
    assert_released(state)
    assert not (state.output / 'COMPARISON.json').exists()
    if stage.startswith('native_'):
        assert (state.output / 'MNIST_linear' / 'original.pt').read_bytes() == b'original diagnostic fixture'


@pytest.mark.parametrize('error_type', [Cancelled, KeyboardInterrupt])
@pytest.mark.parametrize('stage', ['original_train', 'native_compute'])
def test_preflight_cancellation_preserves_primary_and_closes(preflight_run, stage, error_type):
    state = preflight_run
    state.fail, state.error = stage, error_type('cancelled')
    with pytest.raises(error_type) as caught:
        state.run()
    assert caught.value is state.error
    assert_released(state)
    assert not (state.output / 'COMPARISON.json').exists()


@pytest.mark.parametrize('stage, cleanup', [
    ('original_train', {'original_writer'}),
    ('native_model', {'native_data', 'native_logger'}),
    ('native_compute', {'native_data', 'native_logger'}),
])
def test_preflight_keeps_first_error_and_every_cleanup_failure(preflight_run, stage, cleanup):
    state = preflight_run
    state.fail, state.cleanup_fail = stage, cleanup
    with pytest.raises(RuntimeError) as caught:
        state.run()
    assert caught.value is state.error
    assert_released(state)
    errors = getattr(caught.value, 'cleanup_errors', ())
    assert len(errors) == len(cleanup)
    assert {str(error) for error in errors} == {name + ' cleanup failed' for name in cleanup}
    record = safe_error_record(caught.value)
    assert record['type'] == 'RuntimeError'
    assert len(record['causes']) == len(cleanup)
    assert all(item['type'] == 'OSError' for item in record['causes'])
    assert not (state.output / 'COMPARISON.json').exists()


@pytest.mark.parametrize('cleanup', [
    {'original_writer'}, {'native_data'}, {'native_logger'}, {'native_data', 'native_logger'},
])
def test_preflight_cleanup_failure_prevents_passed_publication(preflight_run, cleanup):
    state = preflight_run
    state.cleanup_fail = cleanup
    with pytest.raises(OSError) as caught:
        state.run()
    assert_released(state)
    all_errors = [caught.value, *getattr(caught.value, 'cleanup_errors', ())]
    assert {str(error) for error in all_errors} == {name + ' cleanup failed' for name in cleanup}
    assert not (state.output / 'COMPARISON.json').exists()
    if 'original_writer' in cleanup:
        assert 'native_system' not in state.calls


def test_preflight_detects_silent_logger_diagnostic_failure(preflight_run):
    state = preflight_run
    state.silent_logger_failure = True
    with pytest.raises(RuntimeError, match='diagnostic|logger|logging'):
        state.run()
    assert_released(state)
    assert not (state.output / 'COMPARISON.json').exists()


def test_preflight_success_closes_before_publishing_comparison(preflight_run, monkeypatch):
    state = preflight_run
    import rpipe.structure.artifact.io as artifact_io
    atomic_write = artifact_io.atomic_write_text

    def publish(path, text, **kwargs):
        assert_released(state)
        return atomic_write(path, text, **kwargs)

    monkeypatch.setattr(artifact_io, 'atomic_write_text', publish)
    report = state.run()
    assert_released(state)
    assert report['passed'] is True and len(report['runs']) == 1
    row = report['runs'][0]
    assert row['passed'] is True
    assert row['initial_state']['exact'] is True
    assert row['inputs_equal'] is True
    assert row['maximum_loss_absolute_difference'] == 0
    assert json.loads((state.output / 'COMPARISON.json').read_text(encoding='utf-8')) == report
    assert (state.output / 'MNIST_linear' / 'original.pt').read_bytes() == b'original diagnostic fixture'


def test_preflight_refuses_existing_output_without_touching_it(preflight_run):
    state = preflight_run
    state.output.mkdir()
    saved = state.output / 'COMPARISON.json'
    saved.write_bytes(b'existing failed evidence')
    with pytest.raises(FileExistsError):
        state.run()
    assert saved.read_bytes() == b'existing failed evidence'
    assert state.resources == {} and state.calls == []
    assert state.peer.read_bytes() == b'previous peer evidence'


def test_later_cell_failure_preserves_first_closed_cell_evidence(preflight_run, monkeypatch):
    state = preflight_run
    state.combinations = [('MNIST', 'linear'), ('CIFAR10', 'linear')]
    state.fail = 'second_native_compute'
    import rpipe.structure.artifact.io as artifact_io
    atomic_write = artifact_io.atomic_write_text
    publications = []

    def publish(path, text, **kwargs):
        assert all(resource.closed == 1 for resource in state.all_resources)
        result = atomic_write(path, text, **kwargs)
        publications.append(Path(path).read_bytes())
        return result

    monkeypatch.setattr(artifact_io, 'atomic_write_text', publish)
    with pytest.raises(RuntimeError) as caught:
        state.run()
    assert caught.value is state.error
    assert len(publications) == 1
    assert (state.output / 'COMPARISON.json').read_bytes() == publications[0]
    rows = json.loads(publications[0])['runs']
    assert [(row['data'], row['model'], row['passed']) for row in rows] == [('MNIST', 'linear', True)]
    assert len(state.all_resources) == 6
    assert all(resource.closed == 1 for resource in state.all_resources)
    assert state.closed == ['original_writer', 'native_data', 'native_logger'] * 2
    for name in ('MNIST_linear', 'CIFAR10_linear'):
        assert (state.output / name / 'original.pt').read_bytes() == b'original diagnostic fixture'
    assert state.peer.read_bytes() == b'previous peer evidence'
