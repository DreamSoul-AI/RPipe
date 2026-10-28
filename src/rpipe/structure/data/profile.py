"""Dataset profile written next to shared data.

One file per dataset: split sizes, class counts, pixel range, and the train
mean / std that Normalize reads. This is a look at the data, not only a
standardize recipe.
"""

from __future__ import annotations

from pathlib import Path
from itertools import chain
from typing import Any, Iterable

import yaml

from rpipe.structure.artifact.io import _read_text, atomic_write_text, contained
from rpipe.structure.control import AdmissionError, DataConfig
from rpipe.structure.artifact.io import validate_run_root
from rpipe.structure.data.factory import DataFactory, vision_root
from rpipe.structure.system.origin import normalize_origin

STATS_NAME = 'stats.yaml'


def _profile_path(profiles: Path, name: str) -> Path:
    folder = profiles / name
    if folder.resolve() != folder:
        raise AdmissionError('profile dataset output cannot be a directory link')
    contained(folder, profiles)
    path = folder / STATS_NAME
    contained(path, folder)
    return path


def summarize_batches(batches: Iterable[tuple[Any, Any]]) -> dict[str, Any]:
    """Channel mean / std / min / max and class counts over NCHW batches in 0–1."""
    import torch

    count = 0
    sum_c = None
    sumsq = None
    min_c = None
    max_c = None
    classes: dict[int, int] = {}
    shape: list[int] | None = None
    for images, targets in batches:
        images = images.detach().double()
        if images.ndim != 4:
            raise ValueError(f'expected NCHW, got {tuple(images.shape)}')
        if shape is None:
            shape = [int(x) for x in images.shape[1:]]
        flat = images.transpose(0, 1).reshape(images.size(1), -1)
        batch_sum = flat.sum(dim=1)
        batch_sq = flat.square().sum(dim=1)
        batch_min = flat.min(dim=1).values
        batch_max = flat.max(dim=1).values
        if sum_c is None:
            sum_c = batch_sum
            sumsq = batch_sq
            min_c = batch_min
            max_c = batch_max
        else:
            sum_c = sum_c + batch_sum
            sumsq = sumsq + batch_sq
            min_c = torch.minimum(min_c, batch_min)
            max_c = torch.maximum(max_c, batch_max)
        count += int(flat.size(1))
        for label in targets.detach().reshape(-1).tolist():
            key = int(label)
            classes[key] = classes.get(key, 0) + 1
    if count <= 0 or sum_c is None or sumsq is None or min_c is None or max_c is None:
        raise ValueError('no samples')
    mean = sum_c / count
    var = sumsq / count - mean.square()
    std = var.clamp_min(0).sqrt()
    rows = [{'label': label, 'count': classes[label]} for label in sorted(classes)]
    return {
        'count': int(sum(classes.values())),
        'shape': shape or [],
        'classes': rows,
        'pixel': {
            'min': _round_list(min_c),
            'max': _round_list(max_c),
            'mean': _round_list(mean),
            'std': _round_list(std),
        },
    }


def load_stats(path: Path | str, *, required: bool = False) -> dict[str, Any] | None:
    file = Path(path)
    if not file.is_file():
        if required:
            raise FileNotFoundError('declared normalization statistics are missing')
        return None
    body = yaml.safe_load(_read_text(file)) or {}
    if (not isinstance(body, dict) or any(not isinstance(body.get(key), (list, tuple))
                                        or not body[key] for key in ('mean', 'std'))):
        if required:
            raise ValueError('declared statistics require nonempty mean and std arrays')
        return None
    return body


def profile_study(study_dir: Path | str, *, configs: Iterable[dict[str, Any]], shared_data_dir: Path | str,
                  profiles_dir: Path | str, download: bool = False) -> list[Path]:
    """Profile resolved selected configs without loading a recipe or changing inputs."""
    study = Path(study_dir).resolve()
    shared = Path(shared_data_dir)
    profiles = Path(profiles_dir).resolve()
    validate_run_root(study, shared)
    validate_run_root(study, profiles)
    requests: dict[str, tuple[DataConfig, str]] = {}
    for config in configs:
        data_config = DataConfig.from_mapping(config.get('data'))
        name = data_config.name
        root = vision_root(name) or name
        if not isinstance(root, str) or not root or root in ('.', '..') or Path(root).name != root:
            raise ValueError('profile data.name must identify one dataset directory')
        data_config.source = data_config.source or 'torch'
        if data_config.path is not None:
            data_config.path = str((study / data_config.path).resolve())
            if download:
                raise ValueError('declared data paths are read-only; download into the owned shared cache')
        folder = _profile_path(profiles, root).parent
        declared_stats = data_config.config.get('stats_path')
        for reference in (data_config.path, declared_stats):
            if reference is not None:
                path = (study / reference).resolve()
                if folder.is_relative_to(path) or path.is_relative_to(folder):
                    raise ValueError('profile output must be outside declared input paths')
        # Profiles describe the full raw input; Run subsets and model normalization do not apply.
        data_config.config.pop('train_size', None)
        data_config.config.pop('stats_path', None)
        data_config.config.update(batch_size=256, augment=False, num_workers=0, download=download)
        origin = normalize_origin(config.get('origin'))
        if root in requests and requests[root] != (data_config, origin):
            raise ValueError(f'conflicting profile inputs for dataset {name!r}; select one Run with --id')
        requests[root] = data_config, origin
    if not requests:
        raise ValueError('profiling requires at least one selected data config')
    written: list[Path] = []
    for root, (data_config, origin) in requests.items():
        name = data_config.name
        data = DataFactory.build(data_config, shared, origin=origin)
        primary = None
        try:
            splits = {}
            for split in ('train', 'test'):
                batches = iter(_iter_pairs(data, split))
                first = next(batches, None)
                if first is None:
                    continue
                batches = chain((first,), batches)
                del first
                splits[split] = summarize_batches(batches)
            if 'train' not in splits:
                raise RuntimeError(f'{name}: train split is empty')
            train_pixel = splits['train']['pixel']
            body = {'name': name, 'summary': _summary_line(name, splits),
                    'mean': list(train_pixel['mean']), 'std': list(train_pixel['std']),
                    'splits': splits}
            path = _profile_path(profiles, root)
            atomic_write_text(path, yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
            written.append(path)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            close = getattr(data, 'close', None)
            if callable(close):
                try:
                    close()
                except BaseException as cleanup:
                    if primary is None:
                        raise
                    primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), cleanup)
                    if callable(getattr(primary, 'add_note', None)):
                        primary.add_note('data profile cleanup also failed: ' + type(cleanup).__name__)
    return written


def _iter_pairs(data: Any, split: str):
    for batch in data.iter_batches(split):
        if isinstance(batch, (tuple, list)) and len(batch) >= 2:
            yield batch[0], batch[1]


def _summary_line(name: str, splits: dict[str, Any]) -> str:
    parts = [name]
    for split, body in splits.items():
        shape = 'x'.join(str(x) for x in body.get('shape') or [])
        classes = body.get('classes') or []
        counts = [int(row['count']) for row in classes]
        balance = ''
        if counts and len(set(counts)) == 1:
            balance = f', {len(counts)} classes of {counts[0]}'
        elif counts:
            balance = f', {len(counts)} classes, counts {min(counts)}–{max(counts)}'
        parts.append(f'{split} {body.get("count")} ({shape}{balance})')
    return '; '.join(parts)


def _round_list(value: Any) -> list[float]:
    return [round(float(item), 6) for item in value.tolist()]
