"""One explicit, atomically published tensor snapshot format."""
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import closing
import os
from pathlib import Path
from pickle import UnpicklingError
import tempfile
from typing import Any

from .io import _owned_stream, atomic_replace


def checkpoint_path(path: Path | str) -> Path:
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.suffix.lower() != '.pt':
        raise ValueError('checkpoint references must be explicit absolute .pt files')
    return path


def _validate(value: Any, torch, active: set[int] | None = None) -> None:
    if type(value) is torch.Tensor:
        if vars(value):
            raise ValueError('snapshot tensors must not carry custom Python state')
        return
    if value is None or type(value) in (bool, int, float, str, bytes):
        return
    if type(value) is Counter:
        scalars = (bool, int, float, str, bytes, type(None))
        if vars(value) or any(type(key) not in scalars or type(count) not in scalars
                              for key, count in value.items()):
            raise ValueError('snapshot counters require plain scalar entries')
        return
    if type(value) not in (dict, OrderedDict, list, tuple):
        raise ValueError(f'unsupported snapshot value type: {type(value).__name__}')
    active = set() if active is None else active
    if id(value) in active:
        raise ValueError('snapshot containers must not be cyclic')
    active.add(id(value))
    try:
        if isinstance(value, dict):
            if any(type(key) not in (str, int) for key in value):
                raise ValueError('snapshot mapping keys must be strings or integers')
            items = value.values()
            if isinstance(value, OrderedDict):
                if set(vars(value)) - {'_metadata'}:
                    raise ValueError('unsupported ordered state attributes')
                if hasattr(value, '_metadata'):
                    _validate(value._metadata, torch, active)
        else:
            items = value
        for item in items:
            _validate(item, torch, active)
    finally:
        active.remove(id(value))


def save_snapshot(path: Path | str, payload: dict[str, Any]) -> Path:
    """Commit once; every failure before replacement preserves the previous file."""
    import torch

    target = checkpoint_path(path)
    if not isinstance(payload, dict):
        raise ValueError('snapshot payload must be a mapping')
    _validate(payload, torch)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{target.name}.', suffix='.writing', dir=target.parent)
    pending = Path(temporary)
    try:
        with _owned_stream(closing(os.fdopen(descriptor, 'wb'))) as stream:
            torch.save({'schema': 1, 'kind': 'rpipe.snapshot', 'payload': payload}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        return atomic_replace(pending, target)
    except BaseException as primary:
        try:
            pending.unlink(missing_ok=True)
        except BaseException as cleanup:
            primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), cleanup)
        raise


def load_snapshot(path: Path | str, *, map_location: Any = 'cpu') -> dict[str, Any]:
    """Load the selected current snapshot, never legacy pieces or research classes."""
    import torch

    source = checkpoint_path(path)
    with _owned_stream(closing(source.open('rb'))) as stream:
        try:
            envelope = torch.load(stream, map_location=map_location, weights_only=True)
        except (UnpicklingError, EOFError) as error:
            raise ValueError('unsupported or invalid checkpoint encoding') from error
        except RuntimeError as error:
            prefix = 'PytorchStreamReader failed reading zip archive: '
            if str(error).startswith((prefix + 'not a ZIP archive',
                                      prefix + 'failed finding central directory')):
                raise ValueError('invalid or truncated checkpoint archive') from error
            raise
    if (type(envelope) is not dict or set(envelope) != {'schema', 'kind', 'payload'}
            or type(envelope.get('schema')) is not int or envelope['schema'] != 1
            or envelope.get('kind') != 'rpipe.snapshot' or not isinstance(envelope.get('payload'), dict)):
        raise ValueError('unsupported or incomplete checkpoint snapshot')
    _validate(envelope['payload'], torch)
    return envelope['payload']
