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
from rpipe.structure.algorithm.factory import AlgorithmFactory, AlgorithmRegistry
from rpipe.structure.algorithm.train import TrainAlgorithm


def test_algorithm_factory_builds_native_train():
    algo = AlgorithmFactory.build(AlgorithmConfig(mode='train', source='custom_torch'))
    assert isinstance(algo, TrainAlgorithm)


def test_algorithm_factory_rejects_unregistered_inference():
    with pytest.raises(ValueError, match='inference/custom_torch'):
        AlgorithmFactory.build(AlgorithmConfig(mode='inference', source='custom_torch'))


def test_algorithm_registry_has_train_eval_not_inference():
    keys = AlgorithmRegistry.list()
    assert ('train', 'custom_torch') in keys
    assert ('eval', 'custom_torch') in keys
    assert ('inference', 'custom_torch') not in keys
