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

from pathlib import Path
from types import SimpleNamespace

from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.evaluation import EvalAlgorithm
from rpipe.structure.algorithm.resume import sibling_train_checkpoint
from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.artifact.io import write_json
from rpipe.structure.control import SystemConfig
from rpipe.structure.system.factory import build_system


def test_eval_run_loads_sibling_train_best(tmp_path: Path):
    import torch

    study = tmp_path
    train_id = 'trainrun'
    eval_id = 'evalrun'
    train_assets = study / 'runs' / train_id / 'assets'
    eval_assets = study / 'runs' / eval_id / 'assets'
    train_assets.mkdir(parents=True)
    eval_assets.mkdir(parents=True)
    train_system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), train_assets)
    module = torch.nn.Linear(784, 10)
    with torch.no_grad():
        module.weight.fill_(0.02)
        module.bias.zero_()
    train_system.save_checkpoint(
        {'model': module.state_dict(), 'epoch': 2, 'step': 4, 'best_accuracy': 0.77},
        'best',
    )
    write_json(
        study / 'index.json',
        {'study': 'demo', 'description': '', 'experiments': [{'factors': {'data.config.train_size': 8, 'algorithm.mode': 'train'}, 'runs': [{'id': train_id, 'seed': 0, 'run_dir': train_id}]}, {'factors': {'data.config.train_size': 8, 'algorithm.mode': 'eval'}, 'runs': [{'id': eval_id, 'seed': 0, 'run_dir': eval_id}]}]},
    )
    eval_system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), eval_assets)
    eval_system.train_run_dir = study / 'runs' / train_id
    found = sibling_train_checkpoint(eval_system, stem='best')
    assert found is not None
    assert found.is_file()

    class _Data:
        name = 'Other'
        meta = {'train_size': 8, 'batch_size': 4}

        def iter_batches(self, split: str):
            del split
            yield torch.randn(4, 1, 28, 28), torch.zeros(4, dtype=torch.long)

    class _Model:
        def __init__(self) -> None:
            self.module = torch.nn.Linear(784, 10)

    tracker = AlgorithmTracker(eval_assets)
    out = EvalAlgorithm(AlgorithmConfig.from_mapping({'mode': 'eval'})).compute(
        _Data(), _Model(), eval_system, tracker, cancel_check=lambda: None
    )
    assert out['mode'] == 'eval'
    assert 'accuracy' in out
    assert out['resume_count'] == 1 and out['step'] == 4
    assert Path(out['resume_path']).resolve() == found.resolve()


@pytest.mark.parametrize('current_checkpoint', [True, False], ids=['current-best', 'missing-current-best'])
def test_sibling_lookup_ignores_previous_version_outside_index(tmp_path: Path, current_checkpoint):
    old_best = tmp_path / 'runs' / 'train-v1' / 'assets' / 'checkpoints' / 'best.pt'
    old_best.parent.mkdir(parents=True)
    old_best.write_bytes(b'previous version checkpoint')
    current_best = tmp_path / 'runs' / 'train-v2' / 'assets' / 'checkpoints' / 'best.pt'
    if current_checkpoint:
        current_best.parent.mkdir(parents=True)
        current_best.write_bytes(b'current version checkpoint')
    eval_assets = tmp_path / 'runs' / 'eval-v2' / 'assets'
    eval_assets.mkdir(parents=True)
    write_json(
        tmp_path / 'index.json',
        {'study': 'versioned', 'description': '', 'experiments': [{'factors': {'algorithm.mode': 'train'}, 'runs': [{'id': 'train-v2', 'seed': 0, 'run_dir': 'train-v2'}]}, {'factors': {'algorithm.mode': 'eval'}, 'runs': [{'id': 'eval-v2', 'seed': 0, 'run_dir': 'eval-v2'}]}]},
    )

    # This tests path selection only; checkpoint payloads are not loaded.
    found = sibling_train_checkpoint(SimpleNamespace(train_run_dir=current_best.parents[2]), stem='best')
    assert found == (current_best if current_checkpoint else None)
    assert old_best.read_bytes() == b'previous version checkpoint'


@pytest.mark.parametrize('spelling', ['Eval', 'eval', 'Other'])
@pytest.mark.parametrize('route', ['ids', 'dependency', 'checkpoint'])
def test_sibling_ownership_follows_native_run_component(tmp_path, spelling, route):
    from rpipe.structure.algorithm.resume import sibling_train_ids, sibling_train_dependency
    import json
    import os

    for name, mode in [('Train', 'train'), ('Eval', 'eval'), (spelling, 'eval')]:
        config_path = tmp_path / 'runs' / name / 'config.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({'algorithm': {'mode': mode}, 'system': {'device': 'cpu'}}), encoding='utf-8')
    best = tmp_path / 'runs/Train/assets/checkpoints/best.pt'
    best.parent.mkdir(parents=True)
    best.write_bytes(b'path selection only; never loaded')
    write_json(tmp_path / 'index.json', {'study': 'native-sibling', 'experiments': [
        {'factors': {'algorithm.mode': mode}, 'runs': [{'id': name, 'run_dir': name, 'seed': 0}]}
        for name, mode in [('Train', 'train'), ('Eval', 'eval')]]})
    same_run = (tmp_path / 'runs/Eval').samefile(tmp_path / 'runs' / spelling)
    assert same_run == (spelling == 'Eval' or (os.name == 'nt' and spelling == 'eval'))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if route == 'ids':
        assert sibling_train_ids(tmp_path, spelling) == (['Train'] if same_run else [])
    elif route == 'dependency':
        assert sibling_train_dependency(tmp_path, spelling) == ('Train' if same_run else None)
    else:
        assert sibling_train_checkpoint(SimpleNamespace(train_run_dir=tmp_path / 'runs' / 'Train' if same_run else None),
                                        stem='best', current_id=spelling) == (best if same_run else None)
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
