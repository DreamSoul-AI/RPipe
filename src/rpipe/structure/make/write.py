"""Expand a Study declaration and write Run configs, index, and provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rpipe.structure.api.artifact_api import ensure_study_layout
from rpipe.structure.api.artifact_api import write_provenance
from rpipe.structure.api.artifact_api import prepare_make_sources
from rpipe.structure.make.expand import expand_patches, load_study_yaml
from rpipe.structure.make.recipe import load_recipe

from rpipe.structure.api.artifact_api import (
    artifact_layout,
    build_index,
    experiment_entries,
    load_config,
    write_config,
    write_index,
)
from rpipe.structure.control import experiment_config_from_mapping, run_config_from_merge
from rpipe.structure.make.expand import axis_keys_from_study


def _run_configs(study_dir: Path, patches: list[dict[str, Any]]):
    base_path = study_dir / 'experiment_config.yaml'
    if not base_path.is_file():
        raise FileNotFoundError(f'missing experiment_config.yaml: {base_path}')
    base = experiment_config_from_mapping(load_config(base_path))
    runs = [run_config_from_merge(base, patch) for patch in patches]
    ids = [run.id for run in runs]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate Run in Study expansion; use a new version for repeated computation')
    return runs


def _write_configs(study_dir: Path, runs) -> list[Path]:
    written: list[Path] = []
    for run in runs:
        cfg = run.to_mapping()
        layout = artifact_layout(study_dir, run.id)
        written.append(write_config(layout.config_path, cfg))
    return written


def write_run_configs(study_dir: Path, patches: list[dict[str, Any]]) -> list[Path]:
    return _write_configs(study_dir, _run_configs(study_dir, patches))


def write_study_index(
    study_dir: Path,
    study: dict[str, Any],
    config_paths: list[Path],
) -> Path:
    loaded = [(path, load_config(path)) for path in config_paths]
    experiments = experiment_entries(
        configs=loaded,
        axis_keys=axis_keys_from_study(study),
        study_dir=study_dir,
    )
    index = build_index(
        study=str(study.get('study') or study_dir.name),
        description=str(study.get('description') or ''),
        experiments=experiments,
    )
    return write_index(study_dir, index)


def expand_study(study_dir: Path | str) -> dict[str, Any]:
    """Write Run configs and index from ``study.yaml``."""
    study_dir = Path(study_dir).resolve()
    study = load_study_yaml(study_dir)
    patches = expand_patches(study)
    runs = _run_configs(study_dir, patches)
    sources = prepare_make_sources(study_dir, study, runs)
    load_recipe(study_dir, study)
    ensure_study_layout(study_dir)
    configs = _write_configs(study_dir, runs)
    index_file = write_study_index(study_dir, study, configs)
    provenance_file = write_provenance(study_dir, study, sources=sources)
    return {
        'study_dir': study_dir,
        'index': index_file,
        'configs': configs,
        'provenance': provenance_file,
        'study': study,
    }
