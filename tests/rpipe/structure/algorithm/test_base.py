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

from rpipe.structure.algorithm.base import Algorithm, AlgorithmHook
from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.tracker import AlgorithmTracker


def test_algorithm_is_algorithm_hook(tmp_path):
    assert issubclass(Algorithm, AlgorithmHook)
    algo = Algorithm(AlgorithmConfig.from_mapping({'mode': 'train'}), checkpoint_dir=tmp_path / 'checkpoints')
    assert isinstance(algo, AlgorithmHook)


def test_eval_period_default_every_epoch(tmp_path):
    algo = Algorithm(AlgorithmConfig.from_mapping({'mode': 'train'}), checkpoint_dir=tmp_path / 'checkpoints')
    assert algo.eval_period() == 1
    assert algo.progress_unit() == 'step'
    assert algo.checkpoint_mode() == 'latest'
    assert algo.save_best() is False
    algo = Algorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'eval_period': 0}), checkpoint_dir=tmp_path / 'checkpoints')
    assert algo.eval_period() == 0


def test_base_on_eval_period_is_noop(tmp_path):
    algo = Algorithm(AlgorithmConfig.from_mapping({'mode': 'train'}), checkpoint_dir=tmp_path / 'checkpoints')
    tracker = AlgorithmTracker(tmp_path)
    assert algo.on_eval_period(tracker, None, None, None, None, {'epoch': 1}, cancel_check=lambda: None) is False
