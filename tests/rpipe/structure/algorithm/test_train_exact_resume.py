"""Fresh-process continuous vs committed-checkpoint recovery, through native training."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c2'), pytest.mark.result_type('categorical', detail='summary')]


def _equal(left, right):
    import numpy as np
    import torch

    if isinstance(left, torch.Tensor):
        assert torch.equal(left, right)
    elif isinstance(left, np.ndarray):
        assert np.array_equal(left, right)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            _equal(left[key], right[key])
    elif isinstance(left, (list, tuple)):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            _equal(a, b)
    else:
        assert left == right


def _run(root, action, unit, device, period, stop, eval_period=1, interrupt_step=2):
    worker = Path(__file__).resolve().parents[3] / '_data/recovery_worker.py'
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    repo = Path(__file__).resolve().parents[4]
    env['PYTHONPATH'] = str(repo / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    result = subprocess.run([sys.executable, '-B', str(worker), str(root), action, unit, device, str(period), str(stop), str(eval_period), str(interrupt_step)],
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads((root / f'{action}_trace.json').read_text(encoding='utf-8'))


def _recovery(tmp_path, unit, device, period, stop, eval_period=1, interrupt_step=2, finite=False):
    import torch

    suffix = '-finite' if finite else ''
    continuous = _run(tmp_path / 'continuous', 'continuous' + suffix, unit, device, period, stop, eval_period, interrupt_step)
    partial = _run(tmp_path / 'recovered', 'interrupt' + suffix, unit, device, period, stop, eval_period, interrupt_step)
    assert partial['result']['interrupted']
    resumed = _run(tmp_path / 'recovered', 'resume' + suffix, unit, device, period, stop, eval_period, interrupt_step)
    assert continuous['trace'] == partial['trace'] + resumed['trace']
    for key in ('steps', 'epochs', 'best_value', 'best_metric', 'train_loss'):
        assert continuous['result'][key] == resumed['result'][key]
    left = torch.load(tmp_path / 'continuous/checkpoints/latest.pt', weights_only=False, map_location='cpu')
    right = torch.load(tmp_path / 'recovered/checkpoints/latest.pt', weights_only=False, map_location='cpu')
    for key in ('model', 'optimizer', 'scheduler', 'training_state'):
        _equal(left[key], right[key])
    for key in ('splits', 'segment_batches', 'last_segment', 'step', 'progress'):
        _equal(left['tracker'][key], right['tracker'][key])


@pytest.mark.parametrize('unit,period,stop', [('step', 1, 0), ('step', 2, 0), ('epoch', 1, 0), ('epoch', 2, 0), ('epoch-tail', 2, 0), ('step', 1, 3)])
def test_native_resume_matches_continuous_in_fresh_processes(tmp_path, unit, period, stop):
    _recovery(tmp_path, unit, 'cpu', period, stop)


@pytest.mark.parametrize('eval_period,stop,interrupt_step', [(3, 0, 2), (1, 3, 4)])
def test_resume_retains_partial_metric_segment_and_terminal_early_stop(tmp_path, eval_period, stop, interrupt_step):
    _recovery(tmp_path, 'step', 'cpu', 1, stop, eval_period, interrupt_step)


def test_resume_at_last_batch_completes_epoch_without_losing_remaining_budget(tmp_path):
    _recovery(tmp_path, 'step', 'cpu', 2, 0, 2, 3, finite=True)


def test_custom_metric_runtime_is_not_marked_as_exact_resume(tmp_path):
    import torch

    _run(tmp_path, 'continuous-custom-metrics', 'step', 'cpu', 1, 0)
    payload = torch.load(tmp_path / 'checkpoints/latest.pt', weights_only=False, map_location='cpu')
    assert payload['training_state']['data'] is None


@pytest.mark.gpu
def test_cuda_resume_matches_continuous_in_fresh_processes(tmp_path):
    import torch

    if not torch.cuda.is_available():
        pytest.skip('CUDA unavailable')
    _recovery(tmp_path, 'step', 'cuda', 1, 0)
