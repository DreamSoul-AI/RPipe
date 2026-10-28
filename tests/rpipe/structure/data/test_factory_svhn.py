"""SVHN .mat reader, native training and independent checkpoint evaluation."""

import hashlib

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.flow_layer,
    pytest.mark.module_runner,
    pytest.mark.cost(cost_class='c2'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def test_svhn_mat_train_checkpoint_eval(tmp_path, monkeypatch, request):
    """Local .mat files → real torchvision SVHN → one update → checkpoint → independent eval."""
    import numpy as np
    from scipy.io import savemat
    from torchvision.datasets import SVHN

    from rpipe.structure.algorithm.factory import AlgorithmFactory as algorithm_api
    from rpipe.structure.data.factory import DataFactory as data_api
    from rpipe.structure.model.factory import ModelFactory as model_api
    from rpipe.structure.system.factory import SystemFactory as system_api
    from rpipe.structure.algorithm.tracker import make_tracker
    from rpipe.structure.control import AlgorithmConfig, DataConfig, ModelConfig, SystemConfig

    root = tmp_path / 'data' / 'svhn'
    root.mkdir(parents=True)
    splits = {key: list(value) for key, value in SVHN.split_list.items()}
    for split in ('train', 'test'):
        path = root / splits[split][1]
        savemat(path, {
            'X': np.full((32, 32, 3, 4), 128, dtype=np.uint8),
            'y': np.array([[10], [1], [2], [3]], dtype=np.uint8),
        })
        splits[split][2] = hashlib.md5(path.read_bytes()).hexdigest()
    monkeypatch.setattr(SVHN, 'split_list', splits)
    data = data_api.build(DataConfig(
        name='SVHN', source='torch', config={'batch_size': 4, 'augment': False},
    ), tmp_path / 'data', seed=0)
    images, targets = next(data.iter_batches('test'))
    assert tuple(images.shape) == (4, 3, 32, 32)
    assert targets.tolist() == [0, 1, 2, 3]
    model = model_api.build(ModelConfig(name='linear'), tmp_path, data_meta=data.meta)
    system = system_api.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path / 'assets')
    request.addfinalizer(data.close)
    train = algorithm_api.build(AlgorithmConfig(
        mode='train', config={'num_steps': 1, 'eval_period': 1, 'save_best': True},
    ))
    result = train.run(data, model, system, make_tracker(tmp_path / 'train'))
    assert result['steps'] == 1
    assert 'stub' not in result
    assert system.load_checkpoint('best')['step'] == 1
    independent_model = model_api.build(ModelConfig(name='linear'), tmp_path, data_meta=data.meta)
    evaluate = algorithm_api.build(AlgorithmConfig(mode='eval'))
    evaluated = evaluate.run(data, independent_model, system, make_tracker(tmp_path / 'eval'))
    assert evaluated['accuracy'] == pytest.approx(result['accuracy'])
    assert evaluated['step'] == 1
