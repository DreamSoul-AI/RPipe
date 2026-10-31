"""Explicit snapshot roots and admission before any scientific state changes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from rpipe.structure.control import AlgorithmConfig, SystemConfig
from rpipe.structure.algorithm.resume import load_resume, resume_reference
from rpipe.structure.artifact import checkpoint

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('mode,name,optional', [('train', 'latest', True), ('eval', 'best', False)])
def test_default_resume_uses_only_explicit_root(tmp_path, mode, name, optional):
    own, train = tmp_path / 'own', tmp_path / 'dependency'
    path, missing_ok, label = resume_reference(AlgorithmConfig(mode=mode),
                                             checkpoint_dir=own, train_checkpoint_dir=train)
    assert path == (own if mode == 'train' else train) / f'{name}.pt'
    assert missing_ok is optional and label == name
    assert not own.exists() and not train.exists()


def test_disabled_resume_reads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, 'load_snapshot', lambda *a, **k: pytest.fail('unexpected read'))
    observation = {}
    assert load_resume(AlgorithmConfig.from_mapping({'resume': False}), checkpoint_dir=tmp_path,
                       train_checkpoint_dir=None, observation=observation) is None
    assert observation['resume_path'] is None and observation['resume_count'] == 0


@pytest.mark.parametrize('mode,explicit', [('train', False), ('train', True), ('eval', False)])
def test_missing_snapshot_never_scans_siblings_or_creates_storage(tmp_path, mode, explicit):
    old = tmp_path / 'previous-version/best.pt'
    old.parent.mkdir()
    old.write_bytes(b'previous evidence')
    root = tmp_path / 'current'
    config = {'mode': mode}
    if explicit:
        config['resume_from'] = str(root / 'latest.pt')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    observation = {}
    if mode == 'train' and not explicit:
        assert load_resume(AlgorithmConfig.from_mapping(config), checkpoint_dir=root,
                           train_checkpoint_dir=None, observation=observation) is None
    else:
        with pytest.raises(FileNotFoundError):
            load_resume(AlgorithmConfig.from_mapping(config), checkpoint_dir=root,
                        train_checkpoint_dir=root, observation=observation)
    assert not root.exists()
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('reference', ['latest', 'nested/latest.pt', '../latest.pt'])
def test_resume_from_requires_absolute_file_reference(tmp_path, reference):
    config = AlgorithmConfig.from_mapping({'resume_from': reference})
    with pytest.raises(ValueError):
        resume_reference(config, checkpoint_dir=tmp_path / 'own', train_checkpoint_dir=None)
    assert list(tmp_path.iterdir()) == []


def test_evaluation_requires_injected_dependency_instead_of_guessing_own_best(tmp_path):
    with pytest.raises(ValueError, match='explicit training checkpoint'):
        resume_reference(AlgorithmConfig(mode='eval'), checkpoint_dir=tmp_path,
                         train_checkpoint_dir=None)


def test_eval_loads_only_injected_training_best(tmp_path):
    import torch
    from rpipe.structure.algorithm.evaluation import EvalAlgorithm
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.system.factory import build_system

    training, own = tmp_path / 'selected', tmp_path / 'eval'
    original = torch.nn.Linear(2, 2)
    with torch.no_grad():
        original.weight.fill_(0.02)
        original.bias.zero_()
    checkpoint.save_snapshot(training / 'best.pt', {'model': original.state_dict(), 'epoch': 2, 'step': 4})
    checkpoint.save_snapshot(own / 'best.pt', {'model': {'wrong': torch.ones(1)}})
    data = SimpleNamespace(meta={}, iter_batches=lambda split: iter([(torch.eye(2), torch.tensor([0, 1]))]))
    model = SimpleNamespace(module=torch.nn.Linear(2, 2))
    system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path / 'assets')
    before = {p: p.read_bytes() for p in (training, own) for p in p.rglob('*') if p.is_file()}
    result = EvalAlgorithm(AlgorithmConfig(mode='eval'), checkpoint_dir=own,
                           train_checkpoint_dir=training).compute(
        data, model, system, AlgorithmTracker(tmp_path / 'tracker'), cancel_check=lambda: None)
    assert result['step'] == 4 and result['resume_count'] == 1
    assert result['resume_path'] == str(training / 'best.pt')
    for key, value in original.state_dict().items():
        assert torch.equal(model.module.state_dict()[key], value)
    assert all(path.read_bytes() == body for path, body in before.items())


@pytest.mark.parametrize('damage', ['model', 'tracker', 'data', 'rng', 'contract', 'fractional-step'])
def test_resume_rejection_precedes_model_tracker_rng_and_data_mutations(tmp_path, monkeypatch, damage):
    import torch
    from rpipe.structure.algorithm.train import TrainAlgorithm
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.data.factory import Data
    from rpipe.structure.system.factory import build_system
    from rpipe.structure.system.runtime import capture_rng
    from rpipe.structure.artifact.comparison import compare_state

    dataset = torch.utils.data.TensorDataset(torch.eye(2), torch.tensor([0, 1]))
    def data():
        loader = torch.utils.data.DataLoader(dataset, batch_size=1, shuffle=True,
                                           generator=torch.Generator().manual_seed(7))
        return Data(name='admission', source='fixture', loaders={'train': loader, 'test': loader},
                    meta={'seed': 7, 'batch_size': 1, 'train_size': 2})
    root = tmp_path / 'checkpoints'
    config = AlgorithmConfig.from_mapping({'num_steps': 1, 'eval_period': 0})
    system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path / 'assets')
    TrainAlgorithm(config, checkpoint_dir=root).compute(
        data(), SimpleNamespace(module=torch.nn.Linear(2, 2)), system,
        AlgorithmTracker(tmp_path / 'original'), cancel_check=lambda: None)
    payload = checkpoint.load_snapshot(root / 'latest.pt')
    assert payload['training_state']['data'] is not None
    if damage == 'model':
        payload['model']['weight'] = torch.ones(7, 2)
    elif damage == 'tracker':
        payload['tracker']['step'] = -1
    elif damage == 'data':
        payload['training_state']['data']['consumed'] = -1
    elif damage == 'rng':
        payload['training_state']['rng']['torch'] = 'invalid-rng'
    elif damage == 'contract':
        payload['training_state']['contract']['num_steps'] = 2
    else:
        payload['step'] = 0.5
    checkpoint.save_snapshot(root / 'latest.pt', payload)
    saved = (root / 'latest.pt').read_bytes()
    current_data, model = data(), SimpleNamespace(module=torch.nn.Linear(2, 2))
    model.module.eval()
    tracker = AlgorithmTracker(tmp_path / 'current')
    weights, state, rng = deepcopy(model.module.state_dict()), deepcopy(tracker.state_dict()), capture_rng()
    loader = current_data._loaders['train']
    def forbidden(*args, **kwargs):
        pytest.fail('scientific state mutated before snapshot admission')
    monkeypatch.setattr(model.module, 'load_state_dict', forbidden)
    monkeypatch.setattr(tracker, 'load_state_dict', forbidden)
    monkeypatch.setattr(current_data, 'restore_training_state', forbidden)
    monkeypatch.setattr(current_data, 'rebind_train_steps', forbidden)
    with pytest.raises((ValueError, TypeError)):
        TrainAlgorithm(config, checkpoint_dir=root).compute(
            current_data, model, system, tracker, cancel_check=lambda: None)
    assert not model.module.training
    assert compare_state(weights, model.module.state_dict(), atol=0, rtol=0)['passed']
    assert compare_state(rng, capture_rng(), atol=0, rtol=0)['passed']
    assert state == tracker.state_dict()
    assert current_data._loaders['train'] is loader and current_data._resume_data is None
    assert (root / 'latest.pt').read_bytes() == saved
