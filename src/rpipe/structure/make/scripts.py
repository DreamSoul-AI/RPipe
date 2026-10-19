"""Wait groups and atomic launch-script publication; no subprocess execution."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from rpipe.structure.api.artifact_api import atomic_write_text, validate_run_directory

SCRIPTS_DIRNAME = 'scripts'
JOBS_NAME = 'jobs.json'


def repo_root_from(start: Path) -> Path:
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        pyproject = candidate / 'pyproject.toml'
        if pyproject.is_file():
            return candidate
    return Path.cwd().resolve()


def bash_path(path: Path) -> str:
    text = str(path.resolve())
    if len(text) >= 2 and text[1] == ':':
        return '/' + text[0].lower() + text[2:].replace('\\', '/')
    return text.replace('\\', '/')


def job_waves(jobs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Train jobs first, then eval. Eval waits until the train wave finishes."""
    trains = [job for job in jobs if job.get('mode') != 'eval']
    evals = [job for job in jobs if job.get('mode') == 'eval']
    if trains and evals:
        return [trains, evals]
    return [jobs] if jobs else []


def scripts_dir(study_dir: Path) -> Path:
    path = study_dir / SCRIPTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_jobs_json(
    study_dir: Path,
    jobs: list[dict[str, Any]],
    *,
    round_size: int,
    init_gpu: int,
    num_gpus: int,
    extra: dict[str, Any] | None = None,
    batches: list[list[dict[str, Any]]] | None = None,
) -> Path:
    wait_groups(jobs, round_size, batches)
    payload = {
        'study_dir': str(study_dir.resolve()),
        'round': int(round_size),
        'init_gpu': int(init_gpu),
        'num_gpus': int(num_gpus),
        'jobs': jobs,
    }
    if batches:
        payload['batches'] = batches
    if extra:
        payload.update(extra)
    dest = scripts_dir(study_dir) / JOBS_NAME
    return atomic_write_text(dest, json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def wait_groups(
    jobs: list[dict[str, Any]],
    round_size: int,
    batches: list[list[dict[str, Any]]] | None = None,
) -> list[list[dict[str, Any]]]:
    run_ids = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError('launch job must be an object')
        run_id = job.get('run_id')
        validate_run_directory(run_id)
        run_ids.append(Path(run_id))
    if len(run_ids) != len(set(run_ids)):
        raise ValueError('duplicate Run in launch plan; use a new version for repeated computation')
    if batches:
        grouped = [job for group in batches for job in group]
        key = lambda job: str(job['run_id'])
        if sorted(grouped, key=key) != sorted(jobs, key=key):
            raise ValueError('batches must match declared jobs without missing, extra, or duplicate Run entries')
        return [group for group in batches if group]
    groups: list[list[dict[str, Any]]] = []
    for wave in job_waves(jobs):
        resources: dict[str, list[dict[str, Any]]] = {}
        for job in wave:
            device = str(job.get('device') or 'cuda').lower()
            resources.setdefault(device, []).append(job)
        for rows in resources.values():
            for start in range(0, len(rows), round_size):
                groups.append(rows[start : start + round_size])
    return groups


def render_bash(
    study_dir: Path,
    jobs: list[dict[str, Any]],
    *,
    python_exe: str,
    round_size: int,
    split_round: int,
    batches: list[list[dict[str, Any]]] | None = None,
) -> list[str]:
    if round_size < 1:
        raise ValueError('round must be >= 1')
    if split_round < 1:
        raise ValueError('split_round must be >= 1')
    repo = repo_root_from(study_dir)
    py_b = bash_path(Path(python_exe))
    repo_b = bash_path(repo)
    chunks: list[str] = []
    groups = wait_groups(jobs, round_size, batches)
    partitions = [groups[start:start + split_round] for start in range(0, len(groups), split_round)] or [[]]
    for index, part in enumerate(partitions):
        body = [
            'from pathlib import Path',
            'from rpipe.structure.make.schedule import launch_jobs, run_succeeded',
            f'study = Path({str(study_dir.resolve())!r})',
            f'groups = {part!r}',
            'jobs = [job for group in groups for job in group]',
            'refused = []',
            f"launch_jobs(study, jobs, round_size={round_size}, batches=groups, console='shared', refused_runs=refused)",
            'if refused: raise SystemExit(1)',
            "failed = any(not run_succeeded(study, str(job['run_id'])) for job in jobs)",
        ]
        if index == len(partitions) - 1:
            body.extend([
                'from rpipe.flow.process import run_study',
                'result = run_study(study)',
                "print(study / 'process.json', flush=True)",
                "failed = failed or not result.get('complete', False)",
            ])
        body.append('raise SystemExit(int(failed))')
        chunks.append('\n'.join([
            '#!/bin/bash', f'cd "{repo_b}"', f'"{py_b}" - <<\'RPIPE_LAUNCH\'',
            *body, 'RPIPE_LAUNCH', '',
        ]))
    return chunks


def write_launch_scripts(
    study_dir: Path,
    jobs: list[dict[str, Any]],
    *,
    round_size: int = 4,
    split_round: int = 65535,
    python_exe: str | None = None,
    init_gpu: int = 0,
    num_gpus: int = 1,
    extra: dict[str, Any] | None = None,
    batches: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    py = python_exe or sys.executable
    dest = scripts_dir(study_dir)
    jobs_path = write_jobs_json(
        study_dir,
        jobs,
        round_size=round_size,
        init_gpu=init_gpu,
        num_gpus=num_gpus,
        extra=extra,
        batches=batches,
    )
    chunks = render_bash(
        study_dir,
        jobs,
        python_exe=py,
        round_size=round_size,
        split_round=split_round,
        batches=batches,
    )
    sh_paths: list[Path] = []
    stem = 'launch'
    for k, text in enumerate(chunks, start=1):
        name = f'{stem}_{k}.sh' if len(chunks) > 1 else f'{stem}.sh'
        path = dest / name
        atomic_write_text(path, text, newline='\n')
        sh_paths.append(path)
    ps1 = dest / 'launch.ps1'
    study_win = str(study_dir.resolve())
    py_win = str(Path(py).resolve())
    atomic_write_text(
        ps1, '\n'.join(
            [
                '$ErrorActionPreference = "Stop"',
                f'Set-Location -LiteralPath {json.dumps(str(repo_root_from(study_dir)))}',
                f'& {json.dumps(py_win)} -m rpipe launch {json.dumps(study_win)}',
                '',
            ]
        ),
        encoding='utf-8',
        newline='\n',
    )
    return {
        'scripts_dir': dest,
        'jobs_json': jobs_path,
        'bash': sh_paths,
        'ps1': ps1,
        'n_jobs': len(jobs),
    }
