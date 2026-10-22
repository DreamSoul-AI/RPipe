"""Test-split body for train ``AlgorithmHook.on_eval_period`` (not a Flow mode)."""

from __future__ import annotations

from typing import Any

from rpipe.structure.algorithm.batch import prepare_tensors
from rpipe.structure.algorithm.tracker import AlgorithmTracker


def eval_batch_limit(config: Any | None) -> int | None:
    """``eval_num_steps``: how many test batches to run. Missing or ``< 0`` = full split."""
    if config is None or not hasattr(config, 'setting'):
        return None
    raw = config.setting('eval_num_steps')
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def eval_test_split(
    tracker: AlgorithmTracker,
    logger: Any,
    data: Any,
    model: Any,
    system: Any,
    extra: dict[str, Any] | None = None,
    *,
    num_steps: int | None = None,
) -> dict[str, float]:
    if num_steps is not None and num_steps <= 0:
        raise ValueError('eval_num_steps must be positive, or use -1 for the full test split')
    import torch

    extra = extra or {}
    module = getattr(model, 'module', None)
    if module is None or not hasattr(data, 'iter_batches'):
        return {}
    device = torch.device(getattr(system, 'device', 'cpu'))
    module.eval()
    with torch.no_grad():
        for index, batch in enumerate(data.iter_batches('test')):
            from rpipe.structure.system.runtime import check_cancelled
            check_cancelled(system)
            images, targets = prepare_tensors(batch, module, device)
            logits = module(images)
            values = tracker.evaluate('test', 'batch', (images, targets), logits)
            tracker.append('test', n=int(images.size(0)), values=values)
            if num_steps is not None and (index + 1) >= num_steps:
                break
    if logger is not None:
        logger.report(tracker, 'test', extra=extra)
    tracker.flush('test', progress=extra.get('progress', extra))
    tracker.save('test')
    tracker.reset('test')
    tracker.flush_state()
    module.train()
    return tracker.segment_mean('test')


def should_early_stop(
    *,
    value: float | None = None,
    accuracy: float | None = None,
    best: float | None,
    stall: int,
    patience: int | None,
    min_delta: float,
    mode: str = 'max',
) -> tuple[bool, float | None, int]:
    """Compare ``value`` (alias ``accuracy``) to ``best``. ``mode`` is max or min."""
    score = value if value is not None else accuracy
    if score is None:
        return False, best, stall
    improved = _is_better(score, best, min_delta=min_delta, mode=mode)
    if improved:
        new_best, new_stall = score, 0
    else:
        new_best = best
        new_stall = stall + 1 if patience is not None else stall
    if patience is None:
        return False, new_best, new_stall
    return new_stall >= int(patience), new_best, new_stall


def _is_better(score: float, best: float | None, *, min_delta: float, mode: str) -> bool:
    if best is None:
        return True
    if str(mode).lower() == 'min':
        return score < best - min_delta
    return score > best + min_delta


def best_spec(config: Any) -> tuple[str, str, str]:
    """``best_split``, ``best_metric``, ``best_mode``."""
    split = str(config.setting('best_split', 'test') or 'test')
    name = config.setting('best_metric', 'Accuracy')
    metric = str(name or 'Accuracy')
    mode = config.setting('best_mode')
    if mode is None:
        mode = 'min' if metric.lower() == 'loss' else 'max'
    return split, metric, str(mode)

