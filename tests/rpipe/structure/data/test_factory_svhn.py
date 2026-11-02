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

    from rpipe.structure.algorithm.factory import build_algorithm as algorithm_api
    from rpipe.structure.data.factory import build_data as data_api
    from rpipe.structure.model.factory import build_model as model_api
    from rpipe.structure.system.factory import build_system as system_api
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
    data = data_api(DataConfig(
        name='SVHN', source='torch', config={'batch_size': 4, 'augment': False},
    ), tmp_path / 'data', seed=0)
    images, targets = next(data.iter_batches('test'))
    assert tuple(images.shape) == (4, 3, 32, 32)
    assert targets.tolist() == [0, 1, 2, 3]
    system = system_api(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path / 'assets')
    model = model_api(ModelConfig(name='linear'), tmp_path, data_meta=data.meta, system=system)
    request.addfinalizer(data.close)
    train = algorithm_api(AlgorithmConfig(
        mode='train', config={'num_steps': 1, 'eval_period': 1, 'save_best': True},
    ), checkpoint_dir=tmp_path / 'checkpoints')
    result = train.compute(data, model, system, make_tracker(tmp_path / 'train'), cancel_check=lambda: None)
    assert result['steps'] == 1
    assert 'stub' not in result
    from rpipe.structure.artifact.checkpoint import load_snapshot
    assert load_snapshot(tmp_path / 'checkpoints/best.pt')['step'] == 1
    independent_model = model_api(ModelConfig(name='linear'), tmp_path, data_meta=data.meta, system=system)
    evaluate = algorithm_api(AlgorithmConfig(mode='eval'), checkpoint_dir=tmp_path / 'eval-checkpoints', train_checkpoint_dir=tmp_path / 'checkpoints')
    evaluated = evaluate.compute(data, independent_model, system, make_tracker(tmp_path / 'eval'), cancel_check=lambda: None)
    assert evaluated['accuracy'] == pytest.approx(result['accuracy'])
    assert evaluated['step'] == 1
