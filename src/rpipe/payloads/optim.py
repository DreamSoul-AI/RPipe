"""Native optimizer / scheduler (algorithm-layer interface)."""

from __future__ import annotations

from typing import Any

from rpipe.structure.contracts import AlgorithmConfig
from rpipe.payloads.kwargs import algorithm_blob, filter_args, torch_attr

OPTIM_ALIASES = {
    'sgd': 'SGD',
    'adam': 'Adam',
    'adamw': 'AdamW',
    'rmsprop': 'RMSprop',
    'adagrad': 'Adagrad',
}

SCHED_ALIASES = {
    'cosine': 'CosineAnnealingLR',
    'cosine_annealing': 'CosineAnnealingLR',
    'cosineannealinglr': 'CosineAnnealingLR',
    'step': 'StepLR',
    'steplr': 'StepLR',
    'multistep': 'MultiStepLR',
    'exponential': 'ExponentialLR',
}

_LINEAR_SCHED = frozenset({'linear', 'linear_warmup', 'linearannealinglr'})
_CONSTANT_SCHED = frozenset({'', 'none', 'constant', 'null'})


def make_optimizer(module: Any, config: AlgorithmConfig) -> Any:
    """Build ``torch.optim.*`` by name; extras overlay via ``filter_args``."""
    import torch

    raw = config.setting('optimizer', config.setting('optimizer_name', 'SGD')) or 'SGD'
    try:
        cls = torch_attr(torch.optim, raw, OPTIM_ALIASES)
    except ValueError as exc:
        raise ValueError(f'unknown optimizer: {raw}') from exc
    blob = algorithm_blob(config)
    if blob.get('lr') is None:
        blob['lr'] = 0.1
    kwargs = filter_args(cls, blob)
    if 'betas' in kwargs and isinstance(kwargs['betas'], list):
        kwargs['betas'] = tuple(kwargs['betas'])
    return cls(module.parameters(), **kwargs)


def clip_gradients(module: Any, config: AlgorithmConfig) -> Any:
    """Clip parameter grads. ``max_grad_norm`` <= 0 or missing = no clip (native default)."""
    raw = config.setting('max_grad_norm')
    if raw is None:
        return None
    value = float(raw)
    if value <= 0:
        return None
    import torch

    return torch.nn.utils.clip_grad_norm_(module.parameters(), value)


def make_scheduler(optimizer: Any, config: AlgorithmConfig, t_max: int) -> Any:
    """Build an LR scheduler. ``None`` / ``constant`` = fixed lr (no scheduler object)."""
    name = config.setting('scheduler', config.setting('scheduler_name'))
    if name is None or str(name).lower().replace('-', '_') in _CONSTANT_SCHED:
        return None
    key = str(name).lower().replace('-', '_')
    import torch

    horizon = int(config.setting('T_max', t_max) or t_max)
    horizon = max(horizon, 1)
    if key in _LINEAR_SCHED:
        warmup = config.setting('warmup_steps')
        if warmup is None:
            ratio = float(config.setting('warmup_ratio', 0.0) or 0.0)
            warmup = int(horizon * ratio)
        warmup = max(int(warmup or 0), 0)

        def lr_lambda(step: int) -> float:
            if warmup > 0 and step < warmup:
                return float(step + 1) / float(max(warmup, 1))
            denom = max(horizon - warmup, 1)
            return max(0.0, float(horizon - step) / float(denom))

        return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    try:
        cls = torch_attr(torch.optim.lr_scheduler, name, SCHED_ALIASES)
    except ValueError as exc:
        raise ValueError(f'unknown scheduler: {name}') from exc
    blob = algorithm_blob(config)
    blob.setdefault('T_max', horizon)
    blob.setdefault('total_iters', horizon)
    kwargs = filter_args(cls, blob)
    return cls(optimizer, **kwargs)
