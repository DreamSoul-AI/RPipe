"""Pass config extras through a constructor, keeping only valid parameters."""

from __future__ import annotations

import inspect
from typing import Any, Mapping

from rpipe.structure.contracts import AlgorithmConfig


SKIP_CTOR_KEYS = frozenset(
    {
        'self',
        'optimizer',
        'scheduler',
        'optimizer_name',
        'scheduler_name',
        'params',
        'module',
    }
)


def filter_args(func: Any, arg_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Keep keys that appear in ``func``'s signature (same idea as main ``filter_args``)."""
    sig = inspect.signature(func)
    valid: dict[str, Any] = {}
    for key, value in arg_dict.items():
        if key in SKIP_CTOR_KEYS:
            continue
        param = sig.parameters.get(key)
        if (param is not None
                and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)):
            valid[key] = value
    return valid


def algorithm_blob(config: AlgorithmConfig) -> dict[str, Any]:
    """Flatten extras + nested ``config`` (nested wins, same as ``setting``)."""
    blob = dict(config.extras)
    blob.update(config.config)
    return blob


def torch_attr(namespace: Any, name: str, aliases: Mapping[str, str]) -> Any:
    raw = str(name).strip()
    mapped = aliases.get(raw.lower().replace('-', '_'), raw)
    if hasattr(namespace, mapped):
        return getattr(namespace, mapped)
    want = raw.lower().replace('-', '_')
    for attr in dir(namespace):
        if attr.lower().replace('-', '_') == want:
            return getattr(namespace, attr)
    raise ValueError(f'unknown name {name!r} in {getattr(namespace, "__name__", namespace)}')
