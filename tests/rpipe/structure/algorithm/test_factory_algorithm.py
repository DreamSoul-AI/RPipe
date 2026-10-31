import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.factory import build_algorithm, AlgorithmRegistry
from rpipe.structure.algorithm.train import TrainAlgorithm


def test_algorithm_factory_builds_native_train(tmp_path):
    algo = build_algorithm(AlgorithmConfig(mode='train', source='custom_torch'), checkpoint_dir=tmp_path / 'checkpoints')
    assert isinstance(algo, TrainAlgorithm)


def test_algorithm_factory_rejects_unregistered_inference(tmp_path):
    with pytest.raises(ValueError, match='inference/custom_torch'):
        build_algorithm(AlgorithmConfig(mode='inference', source='custom_torch'), checkpoint_dir=tmp_path / 'checkpoints')


@pytest.mark.parametrize('field', ['source', 'mode'])
@pytest.mark.parametrize('value', ['', False])
def test_empty_or_false_algorithm_selection_does_not_fall_back_to_native(field, value, tmp_path):
    with pytest.raises(ValueError, match='unknown algorithm'):
        build_algorithm(AlgorithmConfig.from_mapping({field: value}), checkpoint_dir=tmp_path / 'checkpoints')


def test_omitted_algorithm_selection_keeps_native_train_default(tmp_path):
    assert isinstance(build_algorithm(AlgorithmConfig(), checkpoint_dir=tmp_path / 'checkpoints'), TrainAlgorithm)


def test_algorithm_registry_has_train_eval_not_inference():
    keys = AlgorithmRegistry.list()
    assert ('train', 'custom_torch') in keys
    assert ('eval', 'custom_torch') in keys
    assert ('inference', 'custom_torch') not in keys


@pytest.mark.parametrize('mode', ['train', 'eval'])
def test_compute_uses_injected_cancel_check_without_reading_system_token(tmp_path, mode):
    from types import SimpleNamespace
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    class System:
        logger = None
        @property
        def cancellation(self):
            pytest.fail('algorithm reached a lifecycle token through System')
    calls = []
    data = SimpleNamespace(source='stub', meta={'stub': True})
    algorithm = build_algorithm(AlgorithmConfig(mode=mode, source='custom_torch'), checkpoint_dir=tmp_path / 'checkpoints')
    result = algorithm.compute(data, None, System(), AlgorithmTracker(tmp_path),
                               cancel_check=lambda: calls.append('checked'))
    assert result['stub'] and calls


@pytest.mark.parametrize('mode', ['train', 'eval'])
def test_cancel_check_original_exception_rejects_before_scientific_work(tmp_path, mode):
    primary = RuntimeError('owned cancellation sentinel')
    def cancel():
        raise primary
    algorithm = build_algorithm(AlgorithmConfig(mode=mode, source='custom_torch'), checkpoint_dir=tmp_path / 'checkpoints')
    with pytest.raises(RuntimeError) as caught:
        algorithm.compute(None, None, None, object(), cancel_check=cancel)
    assert caught.value is primary
    assert list(tmp_path.iterdir()) == []
