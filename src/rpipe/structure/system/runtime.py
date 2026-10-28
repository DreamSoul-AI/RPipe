"""Apply Run seed and torch runtime flags. Call at the start of prepare."""

from __future__ import annotations

from typing import Any

from rpipe.structure.control import SystemConfig


def check_cancelled(system: Any) -> None:
    """Cooperative boundary; the process owner enforces hard deadlines."""
    cancellation = getattr(system, 'cancellation', None)
    if cancellation is not None and cancellation.is_set():
        from concurrent.futures import CancelledError
        raise CancelledError('scientific payload cancellation requested')



def apply_runtime(seed: int | None, system_config: SystemConfig | None = None) -> dict[str, Any]:
    """Seed python / numpy / torch, then apply system determinism flags.

    Must run before Data / Model are built so shuffle generators and init see
    the same world. Flags live on ``SystemConfig`` (``deterministic``,
    ``cudnn_benchmark``, ``cudnn_deterministic`` and optional TF32 flags).
    """
    import random

    import numpy as np
    import torch

    cfg = system_config or SystemConfig()
    applied: dict[str, Any] = {'seed': seed}
    precision = {name: cfg.setting(name) for name in ('cudnn_allow_tf32', 'matmul_allow_tf32')}
    for name, value in precision.items():
        if value is not None and type(value) is not bool:
            raise ValueError(f'system.{name} must be a boolean or omitted')

    if seed is not None:
        value = int(seed)
        random.seed(value)
        np.random.seed(value)
        torch.manual_seed(value)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(value)
            torch.cuda.manual_seed_all(value)
        applied['seed'] = value

    deterministic = bool(cfg.setting('deterministic', False))
    cudnn_deterministic = cfg.setting('cudnn_deterministic')
    if cudnn_deterministic is None:
        cudnn_deterministic = deterministic
    benchmark = cfg.setting('cudnn_benchmark')
    if benchmark is None:
        benchmark = not bool(cudnn_deterministic)

    torch.backends.cudnn.deterministic = bool(cudnn_deterministic)
    torch.backends.cudnn.benchmark = bool(benchmark)
    torch.use_deterministic_algorithms(deterministic, warn_only=True)
    if precision['cudnn_allow_tf32'] is not None:
        torch.backends.cudnn.allow_tf32 = precision['cudnn_allow_tf32']
    if precision['matmul_allow_tf32'] is not None:
        torch.backends.cuda.matmul.allow_tf32 = precision['matmul_allow_tf32']

    applied['deterministic'] = deterministic
    applied['cudnn_deterministic'] = bool(cudnn_deterministic)
    applied['cudnn_benchmark'] = bool(benchmark)
    applied['cudnn_allow_tf32'] = bool(torch.backends.cudnn.allow_tf32)
    applied['matmul_allow_tf32'] = bool(torch.backends.cuda.matmul.allow_tf32)
    return applied


def make_generator(seed: int | None) -> Any:
    """``torch.Generator`` bound to ``seed``, or None if no seed."""
    if seed is None:
        return None
    import torch

    generator = torch.Generator()
    generator.manual_seed(int(seed))
    return generator


def worker_init_fn(worker_id: int) -> None:
    """Per-DataLoader-worker RNG. Uses the worker's torch initial seed."""
    import random

    import numpy as np
    import torch

    worker_seed = torch.initial_seed() % 2**32
    random.seed(worker_seed)
    np.random.seed(worker_seed)
    del worker_id
