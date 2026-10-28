"""Materialize project-shared datasets once (not per Run)."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any

from rpipe.structure.artifact.io import cache_lock
from rpipe.structure.artifact.io import _read_text, atomic_write_text
from rpipe.structure.control import DataConfig
from rpipe.structure.data.factory import build_data, vision_root
from rpipe.structure.system.origin import endpoint_for, normalize_origin

def announce(study: Path, phase: str, message: str) -> None:
    import logging
    logging.getLogger(__name__).info('%s [%s] %s', study, phase, message)


READY_NAME = '.ready'
_prepare_environment_lock = RLock()


def prepare_shared_data(study_dir: Path | str, config_paths: list[Path], *, shared_data_dir: Path | str) -> list[str]:
    """Download / build each unique ``data.name`` + ``source`` into ``shared/data``.

    ``train_size`` is ignored here so subset Runs reuse the same files.
    A dataset is cached only after a successful build writes ``.ready``.
    Returns names materialized on this call (cached names omitted).
    """
    study = Path(study_dir)
    shared = Path(shared_data_dir)
    seen: set[tuple[str, str, str]] = set()
    names: list[str] = []
    with _prepare_environment_lock:
        previous = os.environ.get('TQDM_DISABLE')
        os.environ['TQDM_DISABLE'] = '1'
        try:
            with cache_lock(shared):
                return _prepare(study, shared, config_paths, seen, names)
        finally:
            if previous is None:
                os.environ.pop('TQDM_DISABLE', None)
            else:
                os.environ['TQDM_DISABLE'] = previous


def _prepare(study, shared, config_paths, seen, names):
    for path in config_paths:
        import yaml
        loaded = yaml.safe_load(_read_text(path))
        if not isinstance(loaded, dict):
            raise ValueError(f'config must be a mapping: {path}')
        mapping = _shared_data_mapping(loaded)
        if mapping is None:
            continue
        origin = normalize_origin(loaded.get('origin'))
        key = (str(mapping.get('name') or ''), str(mapping.get('source') or ''), origin)
        if key in seen:
            continue
        seen.add(key)
        root_name = vision_root(key[0]) or key[0]
        folder = shared / root_name
        marker = folder / READY_NAME
        if marker.is_file() and _read_text(marker).strip() == origin:
            announce(study, 'make', f'shared {key[0]} cached')
            continue
        endpoint = endpoint_for(key[0], origin)
        detail = f'shared {key[0]} download {origin} {endpoint.location}'
        announce(study, 'make', detail)
        build_data(DataConfig.from_mapping(mapping), shared, origin=origin)
        atomic_write_text(marker, f'{origin}\n')
        announce(study, 'make', f'shared {key[0]} ready')
        names.append(key[0])
    return names

def _shared_data_mapping(cfg: dict[str, Any]) -> dict[str, Any] | None:
    data = cfg.get('data')
    if not isinstance(data, dict) or not data.get('name'):
        return None
    if data.get('source') in (None, '', 'stub'):
        return None
    mapping = dict(data)
    inner = dict(mapping.get('config') or {})
    inner.pop('train_size', None)
    mapping['config'] = inner
    return mapping
