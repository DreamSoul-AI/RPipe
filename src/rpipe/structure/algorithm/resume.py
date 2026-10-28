"""Resume target resolution (algorithm-layer; system only loads files)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rpipe.structure.control import AlgorithmConfig


def partial_resume_warning(logger=None) -> None:
    import warnings

    message = ('checkpoint resume is partial: RNG/data iterator equivalence is not guaranteed '
               'for legacy checkpoints or unsupported data/Trainer runtimes')
    if logger is not None:
        logger.warning(message)
    warnings.warn(message, RuntimeWarning, stacklevel=2)


def resume_stem(config: AlgorithmConfig, *, mode: str) -> str | None:
    """Return checkpoint stem/path to load, or None to skip resume."""
    explicit = config.setting('resume_from')
    if explicit:
        return str(explicit)
    raw = config.setting('resume')
    if raw is None:
        return 'latest' if mode == 'train' else 'best'
    if raw is False:
        return None
    text = str(raw).strip().lower()
    if text in ('', 'false', '0', 'none', 'off'):
        return None
    return str(raw)


def sibling_train_checkpoint(
    system: Any,
    *,
    stem: str,
    current_id: str | None = None,
) -> Path | None:
    """Use the plan's explicit same-factor, same-seed training dependency."""
    del current_id
    train_root = getattr(system, 'train_run_dir', None)
    if train_root is None:
        return None
    base = Path(train_root) / 'assets' / 'checkpoints'
    name = stem if stem.endswith('.pt') else f'{stem}.pt'
    path = base / name
    if path.is_file():
        return path
    folder = base / (stem[:-3] if stem.endswith('.pt') else stem)
    if folder.is_dir() and any((folder / name).is_file() for name in ('payload.pt', 'model.pt')):
        return folder
    return None


def sibling_train_ids(study_dir: Path, run_id: str) -> list[str]:
    """Match the current index's factors and seed, even before checkpoints exist."""
    import json
    from rpipe.structure.artifact.io import _read_text

    def load_index(root):
        return json.loads(_read_text(Path(root) / 'index.json'))

    try:
        index = load_index(study_dir)
    except (OSError, TypeError, ValueError):
        return []
    current_factors: dict[str, Any] | None = None
    current_seed: Any = None
    for exp in index.get('experiments') or []:
        for run in exp.get('runs') or []:
            rid = str(run.get('run_dir') or run.get('id') or '')
            if Path(rid) == Path(run_id):
                current_factors = dict(exp.get('factors') or {})
                current_seed = run.get('seed')
                break
        if current_factors is not None:
            break
    if current_factors is None:
        return []
    want = dict(current_factors)
    want['algorithm.mode'] = 'train'
    matched: list[str] = []
    for exp in index.get('experiments') or []:
        if dict(exp.get('factors') or {}) != want:
            continue
        for run in exp.get('runs') or []:
            if run.get('seed') != current_seed:
                continue
            rid = str(run.get('run_dir') or run.get('id') or '')
            if rid:
                matched.append(rid)
    return matched


def resolve_checkpoint_ref(
    ref: str,
    system: Any,
    *,
    mode: str,
) -> str:
    """Turn stem / relative path / ``sibling`` into a path or stem system can load."""
    text = str(ref).strip()
    if text.lower() in ('sibling', 'train'):
        stem = 'best' if mode == 'eval' else 'latest'
        found = sibling_train_checkpoint(system, stem=stem)
        return str(found) if found is not None else stem
    path = Path(text)
    if path.is_absolute():
        return str(path)
    for root in (getattr(system, 'source_dir', None), getattr(system, 'study_root', None)):
        if root is None:
            continue
        relative = Path(root) / path
        if relative.is_file() or relative.is_dir():
            return str(relative)
        if not text.endswith('.pt') and relative.with_name(relative.name + '.pt').is_file():
            return str(relative.with_name(relative.name + '.pt'))
    if '/' in text or '\\' in text:
        source = getattr(system, 'source_dir', None)
        if source is None:
            raise ValueError('relative checkpoint paths require explicit system.source_dir')
        return str(Path(source) / path)
    return text


def _has_checkpoint_material(path: Path) -> bool:
    """Recognize weight-bearing paths without loading tensors during planning."""
    if path.is_file():
        return True
    folder = path if path.is_dir() or path.suffix.lower() != '.pt' else path.with_suffix('')
    return folder.with_name(f'{folder.name}.pt').is_file() or any(
        (folder / name).is_file() for name in ('payload.pt', 'model.pt')
    )


def sibling_train_dependency(study_dir: Path, run_id: str) -> str | None:
    """Identify the train that launch must finish before this eval can run."""
    import yaml
    from rpipe.structure.artifact.io import _read_text

    def load_config(path):
        return yaml.safe_load(_read_text(path))

    root = Path(study_dir) / 'runs' / run_id
    try:
        cfg = AlgorithmConfig.from_mapping(load_config(root / 'config.yaml').get('algorithm'))
    except (OSError, ValueError):
        return None
    if cfg.mode != 'eval':
        return None
    ref = resume_stem(cfg, mode='eval')
    if ref is None:
        return None
    parents = sibling_train_ids(study_dir, run_id)
    if not parents:
        return None
    target = Path(resolve_checkpoint_ref(ref, SimpleNamespace(assets_dir=root / 'assets', study_root=study_dir, source_dir=None, train_run_dir=None), mode='eval'))
    if target.exists() or target.suffix.lower() == '.pt' or '/' in str(target) or '\\' in str(target):
        if _has_checkpoint_material(target):
            # Explicit external/local weights do not depend on a sibling train.
            return next((rid for rid in parents if
                         (Path(study_dir) / 'runs' / rid / 'assets' / 'checkpoints').resolve()
                         in target.resolve().parents), None)
    else:
        own = root / 'assets' / 'checkpoints' / target
        if _has_checkpoint_material(own):
            return None
    return parents[0]


def apply_module_state(module: Any, payload: dict[str, Any] | None) -> None:
    if module is None or not payload:
        return
    state = payload.get('model')
    if state is None:
        return
    module.load_state_dict(state)
