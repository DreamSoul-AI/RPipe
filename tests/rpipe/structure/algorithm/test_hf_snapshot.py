"""Controlled Trainer callbacks exercise real HF snapshot publication, without an SDK."""
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_hf_callbacks_publish_complete_snapshot_and_resume_only_remaining_updates(tmp_path, monkeypatch):
    import torch
    from rpipe.structure.algorithm import transformers_trainer as hf
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.artifact import checkpoint
    from rpipe.structure.control import AlgorithmConfig
    from rpipe.structure.data.factory import Data

    updates = []
    class Trainer:
        def __init__(self, *, model, args, optimizers, callbacks, **kwargs):
            self.model, self.args = model, args
            self.optimizer, self.scheduler = optimizers
            self.hook, = callbacks
        def train(self):
            loader = self.get_train_dataloader()
            state = SimpleNamespace(global_step=0, epoch=0.)
            control = SimpleNamespace(should_training_stop=False)
            while state.global_step < self.args.max_steps:
                for batch in loader:
                    self.optimizer.zero_grad()
                    self.compute_loss(self.model, batch).backward()
                    self.optimizer.step()
                    self.scheduler.step()
                    updates.append(1)
                    state.global_step += 1
                    state.epoch = state.global_step / len(loader)
                    self.hook.on_step_end(self.args, state, control)
                    if state.global_step >= self.args.max_steps or control.should_training_stop:
                        break
            self.hook.on_epoch_end(self.args, state, control)
            self.hook.on_train_end(self.args, state, control)
    sdk = SimpleNamespace(Trainer=Trainer, TrainerCallback=object, default_data_collator=lambda rows: rows)
    monkeypatch.setattr(hf, '_require_transformers', lambda: sdk)
    monkeypatch.setattr(hf, 'build_training_arguments', lambda *a, **k: SimpleNamespace())
    dataset = torch.utils.data.TensorDataset(torch.eye(2), torch.tensor([0, 1]))
    def data():
        loader = torch.utils.data.DataLoader(dataset, batch_size=1)
        return Data(name='callback-fixture', source='torch', loaders={'train': loader, 'test': loader},
                    meta={'seed': 7, 'train_size': 2, 'batch_size': 1})
    module = torch.nn.Linear(2, 2)
    config = AlgorithmConfig.from_mapping({'source': 'transformers_trainer', 'num_steps': 4,
        'eval_period': 0, 'checkpoint_period': 1, 'scheduler': 'cosine', 'lr': .01})
    root = tmp_path / 'explicit-checkpoints'
    save = checkpoint.save_snapshot
    def interrupt(path, payload):
        result = save(path, payload)
        if Path(path).name == 'latest.pt' and payload['step'] == 2:
            raise RuntimeError('committed callback interruption')
        return result
    def compute():
        return hf.HfTrainAlgorithm(config, checkpoint_dir=root).compute(
            data(), SimpleNamespace(module=module), SimpleNamespace(device='cpu', logger=None),
            AlgorithmTracker(tmp_path / 'tracker'), cancel_check=lambda: None)
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint, 'save_snapshot', interrupt)
        with pytest.raises(RuntimeError, match='committed callback interruption'):
            compute()
    assert len(updates) == 2
    partial = checkpoint.load_snapshot(root / 'latest.pt')
    assert partial['step'] == partial['tracker']['progress']['step'] == 2
    assert {'model', 'optimizer', 'scheduler', 'tracker'} <= partial.keys()
    assert 'training_state' not in partial  # This callback fixture makes no exact sampler/RNG claim.
    with pytest.warns(RuntimeWarning, match='resume is partial'):
        result = compute()
    assert result['steps'] == len(updates) == 4
    final = checkpoint.load_snapshot(root / 'latest.pt')
    assert final['step'] == final['tracker']['progress']['step'] == 4
    assert final['scheduler']['last_epoch'] == 4
