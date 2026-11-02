"""Small offline, fresh-process native checkpoint recovery fixture."""
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch
from torch.utils.data import DataLoader, RandomSampler

from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.algorithm.metrics import MetricBundle
from rpipe.structure.algorithm.train import TrainAlgorithm
from rpipe.structure.data.factory import Data
from rpipe.structure.control import SystemConfig
from rpipe.structure.system.factory import build_system
from rpipe.structure.system.runtime import apply_runtime

root, action, unit, device, period, stop, eval_period, interrupt_step = sys.argv[1:]
tail_epoch = unit == 'epoch-tail'
unit = 'epoch' if tail_epoch else unit
root = Path(root)
root.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(1)
apply_runtime(19, SystemConfig.from_mapping({'deterministic': True, 'cudnn_benchmark': False,
                                           'cudnn_allow_tf32': False, 'matmul_allow_tf32': False}))
trace = []


class Samples:
    def __len__(self):
        return 9 if tail_epoch else 11

    def __getitem__(self, index):
        values = [index / 11, random.random(), float(np.random.random()), float(torch.rand(()))]
        trace.append([index, *values])
        return torch.tensor(values, dtype=torch.float32), index % 2


dataset = Samples()
generator = torch.Generator().manual_seed(19)
loader = DataLoader(dataset, batch_size=2, sampler=RandomSampler(dataset, generator=generator), generator=generator)
# Held-out evaluation must not consume the training RNG or its sample trace.
test_batches = [(torch.zeros(2, 4), torch.zeros(2, dtype=torch.long))]
data = Data(name='offline', source='torch', loaders={'train': loader, 'test': test_batches},
            meta={'seed': 19, 'batch_size': 2, 'train_size': len(dataset)})
if unit == 'step' and not action.endswith('-finite'):
    data._train_set = dataset
model = SimpleNamespace(module=torch.nn.Sequential(torch.nn.Linear(4, 7), torch.nn.Dropout(0.4), torch.nn.Linear(7, 2)))
settings = {'mode': 'train', 'progress_unit': unit,
            ('num_steps' if unit == 'step' else 'num_epochs'): 6 if unit == 'step' else 3,
            'step_period': int(period), 'lr': 0.03, 'momentum': 0.8, 'scheduler': 'cosine',
            'eval_period': int(eval_period), 'checkpoint_period': 1, 'early_stop_patience': int(stop) or None}
if int(stop):
    settings.update(best_split='train', best_metric='Loss', best_mode='min', early_stop_min_delta=100.0)
system = build_system(SystemConfig.from_mapping({'device': device}), root)
model.module = system.place_module(model.module)
from rpipe.structure.artifact import checkpoint
save = checkpoint.save_snapshot


def interrupt(path, payload):
    result = save(path, payload)
    name = Path(path).stem
    if action.startswith('interrupt') and name == 'latest' and payload['step'] == (int(interrupt_step) if unit == 'step' else len(loader) // int(period)):
        raise RuntimeError('fixture interruption after committed checkpoint')
    return result


checkpoint.save_snapshot = interrupt


class CustomMetrics(MetricBundle):
    pass


try:
    tracker = AlgorithmTracker(root, CustomMetrics() if action.endswith('-custom-metrics') else None)
    result = TrainAlgorithm(AlgorithmConfig.from_mapping(settings), checkpoint_dir=root / 'checkpoints').compute(data, model, system, tracker, cancel_check=lambda: None)
except RuntimeError as exc:
    if str(exc) != 'fixture interruption after committed checkpoint' or not action.startswith('interrupt'):
        raise
    result = {'interrupted': True}
(root / f'{action}_trace.json').write_text(json.dumps({'trace': trace, 'result': result}), encoding='utf-8')
