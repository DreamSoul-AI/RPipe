"""Study provenance: source / plan hashes, environment, and the freeze gate."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rpipe.structure.artifact.layout import INDEX_NAME, RUNS_DIRNAME, CONFIG_NAME
from rpipe.structure.artifact._atomic import _read_bytes, _read_text, atomic_write_text
from rpipe.structure.artifact.index import load_index
from rpipe.structure.artifact.layout import validate_run_directory

PROVENANCE_NAME = 'provenance.json'
DECLARATION_FILES = ('study.yaml', 'experiment_config.yaml')


def provenance_path(study_dir: Path | str) -> Path:
    return Path(study_dir) / PROVENANCE_NAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(_read_bytes(path)).hexdigest()


def _inside(study_dir: Path, raw: str) -> Path:
    path = (study_dir / raw).resolve()
    try:
        path.relative_to(study_dir.resolve())
    except ValueError as exc:
        raise ValueError(f'path must stay inside the Study: {raw}') from exc
    return path


def source_files(study_dir: Path | str, study: dict[str, Any]) -> dict[str, str]:
    """Library and Study two-column sources, declarations, recipe, and extra inputs."""
    import rpipe
    from rpipe.structure.api.make_api import study_phase_files

    study_dir = Path(study_dir).resolve()
    package = Path(rpipe.__file__).resolve().parent
    out = {
        'rpipe/' + path.relative_to(package).as_posix(): _sha256(path)
        for path in sorted(package.rglob('*.py'))
    }
    names = list(DECLARATION_FILES)
    names.extend(path.relative_to(study_dir).as_posix() for path in study_phase_files(study_dir, study))
    for folder in ('flow', 'structure'):
        directory = _inside(study_dir, folder)
        names.extend(path.relative_to(study_dir).as_posix() for path in sorted(directory.rglob('*.py')))
    if study.get('recipe') not in (None, ''):
        names.append(str(study['recipe']))
    block = study.get('provenance')
    block = {} if block is None else block
    if not isinstance(block, dict):
        raise ValueError('provenance must be a mapping with an include list')
    include = block.get('include')
    if include is not None and not isinstance(include, list):
        raise ValueError('provenance.include must be a list')
    for pattern in include or []:
        matched = sorted(study_dir.glob(str(pattern)))
        if not matched:
            raise FileNotFoundError(f'provenance.include matched nothing: {pattern}')
        names.extend(path.relative_to(study_dir).as_posix() for path in matched if path.is_file())
    for name in names:
        path = _inside(study_dir, name)
        if path.is_file():
            key = path.relative_to(study_dir).as_posix()
            if key.startswith('rpipe/'):
                try:
                    same_input = path.samefile(package / key.removeprefix('rpipe/'))
                except OSError as exc:
                    raise ProvenanceChangedError(f'Cannot verify library source for Study key: {key}') from exc
                if not same_input:
                    raise ProvenanceChangedError(f'Study source key collides with library: {key}; move the input and make again')
            out[key] = _sha256(path)
    return out


def plan_files(study_dir: Path | str) -> dict[str, str]:
    """``index.json`` and the config of every Run it lists."""
    study_dir = Path(study_dir).resolve()
    index = _inside(study_dir, INDEX_NAME)
    if not index.is_file():
        return {}
    out = {INDEX_NAME: _sha256(index)}
    try:
        body = load_index(study_dir)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc
    for experiment in body.get('experiments') or []:
        for run in experiment.get('runs') or []:
            run_id = run.get('id')
            validate_run_directory(run_id)
            relative = f'{RUNS_DIRNAME}/{run_id}/{CONFIG_NAME}'
            config = _inside(study_dir, relative)
            if config.is_file():
                out[relative] = _sha256(config)
    return out


def capture_environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        'python': sys.version.split()[0],
        'platform': platform.platform(),
    }
    for module in ('torch', 'torchvision', 'numpy'):
        try:
            env[module] = __import__(module).__version__
        except Exception:
            env[module] = None
    try:
        import torch

        env['cuda'] = torch.version.cuda
        env['gpu'] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        env['cuda'] = None
        env['gpu'] = None
    return env


def capture_git(study_dir: Path | str) -> dict[str, Any]:
    try:
        head = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=study_dir, capture_output=True, text=True, errors='replace', timeout=10,
        )
        if head.returncode != 0:
            return {}
        dirty = subprocess.run(
            ['git', 'status', '--porcelain'], cwd=study_dir, capture_output=True, text=True, errors='replace', timeout=30,
        )
        if dirty.returncode != 0:
            return {}
        return {'head': head.stdout.strip(), 'dirty': bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return {}


def digest(body: dict[str, Any]) -> str:
    content = {'files': body.get('files') or {}, 'plan': body.get('plan') or {}}
    text = json.dumps(content, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def write_provenance(study_dir: Path | str, study: dict[str, Any]) -> Path:
    study_dir = Path(study_dir).resolve()
    body = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'files': source_files(study_dir, study),
        'plan': plan_files(study_dir),
        'environment': capture_environment(),
        'git': capture_git(study_dir),
    }
    body['digest'] = digest(body)
    path = provenance_path(study_dir)
    atomic_write_text(path, json.dumps(body, indent=2, ensure_ascii=False))
    return path


def load_provenance(study_dir: Path | str) -> dict[str, Any] | None:
    path = provenance_path(study_dir)
    if not path.is_file():
        return None
    body = json.loads(_read_text(path))
    if not isinstance(body, dict) or any(
            body.get(key) is not None and not isinstance(body[key], dict) for key in ('files', 'plan')):
        raise ValueError('provenance must be a mapping with optional files/plan mappings')
    return body


def provenance_changes(study_dir: Path | str, study: dict[str, Any]) -> list[str]:
    """Relative paths whose hash differs from ``provenance.json``; missing file → ``['provenance.json']``."""
    saved = load_provenance(study_dir)
    if saved is None:
        return [PROVENANCE_NAME]
    changed: list[str] = []
    for key, current in (('files', source_files(study_dir, study)), ('plan', plan_files(study_dir))):
        before = saved.get(key) or {}
        for name in sorted(set(before) | set(current)):
            if before.get(name) != current.get(name):
                changed.append(name)
    return changed


class ProvenanceChangedError(RuntimeError):
    pass


def check_frozen(study_dir: Path | str, study: dict[str, Any]) -> None:
    """Raise when ``freeze: true`` and any source or plan hash changed since make."""
    if study.get('freeze') is not True:
        return
    try:
        changed = provenance_changes(study_dir, study)
    except (OSError, ValueError, RecursionError) as exc:
        raise ProvenanceChangedError(f'Cannot verify frozen provenance: {exc}') from exc
    if changed:
        shown = ', '.join(changed[:5]) + (' …' if len(changed) > 5 else '')
        raise ProvenanceChangedError(
            f'provenance changed after make ({len(changed)}): {shown}; run make again to accept'
        )
