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

import yaml

from rpipe.structure.artifact.layout import INDEX_NAME, RUNS_DIRNAME, CONFIG_NAME
from rpipe.structure.artifact._atomic import _read_bytes, _read_text, atomic_write_text
from rpipe.structure.artifact.index import load_index
from rpipe.structure.artifact.layout import validate_run_directory
from rpipe.structure.artifact.layout import CorruptArtifactError

PROVENANCE_NAME = 'provenance.json'
DECLARATION_FILES = ('study.yaml', 'experiment_config.yaml')
_STUDY_DISPLAY_AND_EXPANSION = frozenset({
    'study', 'description', 'experiment', 'run_description', 'fixed', 'axes', 'seeds', 'tags',
    'origin', 'freeze', 'provenance',
})


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


def write_provenance(study_dir: Path | str, study: dict[str, Any], *, sources=None) -> Path:
    study_dir = Path(study_dir).resolve()
    sources = _source_snapshot(study_dir, study) if sources is None else sources
    body = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        **sources,
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


def _source_snapshot(study_dir: Path, study: dict[str, Any]) -> dict[str, Any]:
    return {'files': source_files(study_dir, study),
            'declarations': {name: _read_bytes(_inside(study_dir, name)).decode('utf-8')
                             for name in DECLARATION_FILES if (study_dir / name).is_file()},
            'compatible_digests': []}


def _saved_source(study_dir: Path) -> dict[str, Any] | None:
    saved = load_provenance(study_dir)
    if saved is None:
        return None
    chain = saved.get('compatible_digests', [])
    if (saved.get('digest') != digest(saved) or not isinstance(chain, list)
            or any(not isinstance(value, str) or len(value) != 16
                   or any(c not in '0123456789abcdef' for c in value) for value in chain)):
        raise ValueError('invalid provenance digest or compatible_digests')
    return saved


def _declarations(saved: dict[str, Any], current: dict[str, Any]) -> dict[str, str]:
    declarations = saved.get('declarations')
    if declarations is None:
        if any((saved.get('files') or {}).get(name) != current['files'].get(name)
               for name in DECLARATION_FILES):
            raise ValueError('historical declaration changed without a verified snapshot')
        declarations = current['declarations']
    if not isinstance(declarations, dict) or any(
            name not in DECLARATION_FILES or not isinstance(text, str)
            for name, text in declarations.items()):
        raise ValueError('invalid provenance declarations')
    for name in DECLARATION_FILES:
        text = declarations.get(name)
        value = hashlib.sha256(text.encode('utf-8')).hexdigest() if text is not None else None
        if value != (saved.get('files') or {}).get(name):
            raise ValueError(f'declaration snapshot SHA mismatch: {name}')
    return declarations


def _study_execution(declarations: dict[str, str]) -> dict[str, Any]:
    study = yaml.safe_load(declarations.get('study.yaml', '')) or {}
    if not isinstance(study, dict):
        raise ValueError('saved Study declaration must be a mapping')
    return {key: value for key, value in study.items() if key not in _STUDY_DISPLAY_AND_EXPANSION}


def _same_computation(saved: dict[str, Any], current: dict[str, Any]) -> bool:
    old = _declarations(saved, current)
    before = {key: value for key, value in (saved.get('files') or {}).items()
              if key not in DECLARATION_FILES}
    after = {key: value for key, value in current['files'].items() if key not in DECLARATION_FILES}
    return before == after and _study_execution(old) == _study_execution(current['declarations'])


def _check_binding(saved: dict[str, Any] | None, result: dict[str, Any]) -> None:
    if saved is None or result.get('provenance') not in {
            saved['digest'], *saved.get('compatible_digests', [])}:
        raise ValueError('successful Run has missing or unknown provenance binding')


def _run_mapping(study_dir: Path, run_id: str, saved: dict[str, Any]) -> dict[str, Any]:
    from rpipe.structure.api.artifact_api import load_config
    from rpipe.structure.control import RunConfig

    validate_run_directory(run_id)
    name = f'{RUNS_DIRNAME}/{run_id}/{CONFIG_NAME}'
    path = _inside(study_dir, name)
    if (saved.get('plan') or {}).get(name) != _sha256(path):
        raise ValueError(f'Run config is not bound by provenance: {run_id}')
    config = RunConfig.from_mapping(load_config(path))
    if config.id != run_id or config.with_computed_id().id != run_id:
        raise ValueError(f'Run config identity mismatch: {run_id}')
    return config.to_mapping()


def prepare_make_sources(study_dir: Path | str, study: dict[str, Any], runs) -> dict[str, Any]:
    """Refuse incompatible successful reuse before make publishes or executes a recipe."""
    from rpipe.structure.api.artifact_api import load_result, STATUS_SUCCEEDED
    from rpipe.structure.control import canonical_json

    study_dir = Path(study_dir).resolve()
    try:
        current = _source_snapshot(study_dir, study)
        saved = _saved_source(study_dir)
        protected = []
        for run in runs:
            path = _inside(study_dir, f'{RUNS_DIRNAME}/{run.id}/result.json')
            if path.is_file():
                result = load_result(path)
                if result.get('status') == STATUS_SUCCEEDED:
                    protected.append((run, result))
        legacy_unknown = (saved is not None and saved.get('declarations') is None
                          and any((saved.get('files') or {}).get(name) != current['files'].get(name)
                                  for name in DECLARATION_FILES))
        same = saved is not None and not (legacy_unknown and not protected) and _same_computation(saved, current)
        for run, result in protected:
            _check_binding(saved, result)
            if not same:
                raise ValueError('computation source changed with an existing successful Run')
            if canonical_json(_run_mapping(study_dir, run.id, saved)) != canonical_json(run.to_mapping()):
                raise ValueError(f'Run config changed under an existing identity: {run.id}')
        if same:
            current['compatible_digests'] = sorted({saved['digest'], *saved.get('compatible_digests', [])})
        return current
    except (OSError, ValueError, TypeError, RecursionError, CorruptArtifactError, yaml.YAMLError) as exc:
        raise ProvenanceChangedError(f'Cannot reuse make sources: {exc}; preserve old Runs and use a new version') from exc


def check_result_source(study_dir: Path | str, run_id: str, result: dict[str, Any]) -> None:
    """An unknown successful binding is a refusal, never a request to overwrite it."""
    study_dir = Path(study_dir).resolve()
    if not (study_dir / 'study.yaml').exists() and not provenance_path(study_dir).exists():
        return
    try:
        saved = _saved_source(study_dir)
        _check_binding(saved, result)
        study = (yaml.safe_load(_read_text(study_dir / 'study.yaml')) or {}) if (study_dir / 'study.yaml').is_file() else {}
        if not isinstance(study, dict):
            raise ValueError('Study declaration must be a mapping')
        current = _source_snapshot(study_dir, study)
        if any((saved.get('files') or {}).get(name) != current['files'].get(name)
               for name in DECLARATION_FILES):
            raise ValueError('Study declarations changed since make; make must verify compatibility first')
        if not _same_computation(saved, current):
            raise ValueError('successful Run does not match current computation sources')
        _run_mapping(study_dir, run_id, saved)
    except (OSError, ValueError, TypeError, RecursionError, CorruptArtifactError, yaml.YAMLError) as exc:
        raise ProvenanceChangedError(f'Cannot reuse successful Run {run_id}: {exc}; preserve it and use a new version') from exc


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
