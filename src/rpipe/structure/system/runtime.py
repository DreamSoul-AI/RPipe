"""Apply Run seed and torch runtime flags. Call at the start of prepare."""

from __future__ import annotations

from typing import Any

from rpipe.structure.control import SystemConfig


def capture_rng() -> dict[str, Any]:
    import random
    import numpy as np
    import torch

    numpy_state = np.random.get_state()
    return {'python': random.getstate(), 'numpy': (numpy_state[0], numpy_state[1].tolist(), *numpy_state[2:]),
            'torch': torch.get_rng_state(),
            'cuda': torch.cuda.get_rng_state_all() if torch.cuda.is_initialized() else None}


def validate_rng(state: dict[str, Any]) -> None:
    import random
    import numpy as np
    import torch

    if not isinstance(state, dict) or set(state) != {'python', 'numpy', 'torch', 'cuda'}:
        raise ValueError('invalid checkpoint RNG state')
    random.Random().setstate(state['python'])
    numpy_state = state['numpy']
    if (not isinstance(numpy_state, (list, tuple)) or len(numpy_state) != 5
            or not isinstance(numpy_state[1], list)):
        raise ValueError('unsupported checkpoint NumPy RNG state')
    if any(type(value) is not int or not 0 <= value < 2**32 for value in numpy_state[1]):
        raise ValueError('invalid checkpoint NumPy RNG keys')
    np.random.RandomState().set_state((numpy_state[0], np.asarray(numpy_state[1], dtype=np.uint32), *numpy_state[2:]))
    if type(state['torch']) is not torch.Tensor:
        raise ValueError('invalid checkpoint Torch RNG state')
    torch.Generator().set_state(state['torch'].cpu())
    cuda = state['cuda']
    if cuda is not None and (not isinstance(cuda, list) or not torch.cuda.is_available()
                             or len(cuda) != torch.cuda.device_count()):
        raise ValueError('checkpoint CUDA RNG devices do not match current runtime')
    if cuda is not None:
        for value in cuda:
            if type(value) is not torch.Tensor or value.dtype != torch.uint8 or value.ndim != 1:
                raise ValueError('invalid checkpoint CUDA RNG state')


def restore_rng(state: dict[str, Any]) -> None:
    import random
    import numpy as np
    import torch

    validate_rng(state)
    random.setstate(state['python'])
    numpy_state = state['numpy']
    np.random.set_state((numpy_state[0], np.asarray(numpy_state[1], dtype=np.uint32), *numpy_state[2:]))
    torch.set_rng_state(state['torch'].cpu())
    cuda = state['cuda']
    if cuda is not None:
        torch.cuda.set_rng_state_all([value.cpu() for value in cuda])



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
