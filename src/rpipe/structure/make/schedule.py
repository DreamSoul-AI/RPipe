"""Run readiness, launch-plan reuse and subprocess lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from rpipe.structure.api.algorithm_api import sibling_train_dependency
from rpipe.structure.api.artifact_api import CorruptArtifactError, load_config
from rpipe.structure.api.artifact_api import load_index
from rpipe.structure.api.artifact_api import validate_run_directory
from rpipe.structure.api.artifact_api import _read_text
from rpipe.structure.api.artifact_api import write_result
from rpipe.structure.api.artifact_api import STATUS_SUCCEEDED, decode_result
from rpipe.structure.api.artifact_api import (
    RUN_ATTEMPT_ENV, RUN_BUSY_EXIT, RunLockedError, owns_execution, run_lock,
)
from rpipe.structure.api.artifact_api import check_result_source
from rpipe.structure.control.mapping import get_setting

from rpipe.structure.make.scripts import (
    JOBS_NAME, SCRIPTS_DIRNAME, bash_path, job_waves, render_bash, repo_root_from,
    scripts_dir, wait_groups, write_jobs_json, write_launch_scripts,
)


def gpu_ids(init_gpu: int, num_gpus: int) -> list[str]:
    if num_gpus <= 0:
        return []
    return [str(init_gpu + i) for i in range(num_gpus)]


def _run_result(study_dir: Path, run_id: str) -> dict[str, Any]:
    validate_run_directory(run_id)
    result_path = Path(study_dir) / 'runs' / run_id / 'result.json'
    try:
        return decode_result(_read_text(result_path))
    except (OSError, ValueError, CorruptArtifactError, RecursionError):
        return {}


def run_succeeded(study_dir: Path, run_id: str) -> bool:
    result = _run_result(study_dir, run_id)
    if result.get('status') != STATUS_SUCCEEDED:
        return False
    check_result_source(study_dir, run_id, result)
    parent = sibling_train_dependency(study_dir, run_id)
    if parent is None:
        return True
    parent_result = _run_result(study_dir, parent)
    if parent_result.get('status') != STATUS_SUCCEEDED:
        return False
    check_result_source(study_dir, parent, parent_result)
    # ponytail: conservative local freshness; copied/backdated artifacts need explicit re-evaluation.
    try:
        runs = Path(study_dir) / 'runs'
        return (runs / parent / 'result.json').stat().st_mtime_ns <= (runs / run_id / 'result.json').stat().st_mtime_ns
    except OSError:
        return False


def _fail_result(study_dir: Path, run_id: str, error: str, *,
                 attempt_token: str | None = None, only_success: bool = False) -> bool:
    validate_run_directory(run_id)
    root = Path(study_dir) / 'runs' / run_id
    try:
        with run_lock(root):
            if attempt_token is not None and not owns_execution(root, attempt_token):
                return False
            body = _run_result(study_dir, run_id)
            if not only_success or body.get('status') == STATUS_SUCCEEDED:
                body.update(status='failed', error=error)
                write_result(root / 'result.json', body)
            return True
    except RunLockedError:
        return False


def _job_fields(config_path: Path) -> tuple[str, str]:
    try:
        cfg = load_config(config_path)
    except (OSError, ValueError):
        return 'train', 'cpu'
    algo = cfg.get('algorithm')
    system = cfg.get('system')
    mode = str(algo.get('mode') or 'train') if isinstance(algo, dict) else 'train'
    device = str(get_setting(system, 'device') or 'cpu') if isinstance(system, dict) else 'cpu'
    return mode, device.lower()


def job_mode(job: dict[str, Any]) -> str:
    return str(job.get('mode') or 'train')


def filter_jobs_by_mode(
    jobs: list[dict[str, Any]],
    modes: list[str] | tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    """Keep jobs whose ``mode`` is in ``modes``. Empty/None = no filter."""
    if not modes:
        return list(jobs)
    wanted = {str(mode).strip().lower() for mode in modes if str(mode).strip()}
    if not wanted:
        return list(jobs)
    return [job for job in jobs if job_mode(job) in wanted]


def filter_batches_by_mode(
    batches: list[list[dict[str, Any]]] | None,
    modes: list[str] | tuple[str, ...] | None,
) -> list[list[dict[str, Any]]] | None:
    if batches is None:
        return None
    if not modes:
        return [list(group) for group in batches if group]
    return [kept for kept in (filter_jobs_by_mode(list(group), modes) for group in batches) if kept]


def _assign_gpus(jobs: list[dict[str, Any]], init_gpu: int, num_gpus: int) -> None:
    gpus = gpu_ids(init_gpu, num_gpus)
    gpu_index = 0
    for job in jobs:
        if str(job.get('device') or 'cuda').lower() == 'cpu' or not gpus:
            job.pop('gpu', None)
            continue
        job['gpu'] = gpus[gpu_index % len(gpus)]
        gpu_index += 1


def plan_jobs(
    study_dir: Path,
    config_paths: list[Path],
    *,
    init_gpu: int = 0,
    num_gpus: int = 1,
    include_done: bool = False,
) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for path in config_paths:
        run_id = path.parent.name
        if not include_done and run_succeeded(study_dir, run_id):
            continue
        mode, device = _job_fields(path)
        pending.append(
            {
                'run_id': run_id,
                'config': str(path.as_posix()),
                'mode': mode,
                'device': device,
            }
        )
    ordered: list[dict[str, Any]] = []
    for wave in job_waves(pending):
        ordered.extend(wave)
    _assign_gpus(ordered, init_gpu, num_gpus)
    return ordered


def load_launch_plan(
    study_dir: Path | str,
    *,
    init_gpu: int,
    num_gpus: int,
    round_size: int,
    include_done: bool = False,
) -> dict[str, Any] | None:
    """Reuse ``scripts/jobs.json`` from a prior make. None → caller should make.

    ``include_done`` keeps already-succeeded jobs that are still listed in the
    file. It does not invent Runs that were omitted from the original make;
    those need ``--remake --include-done``.
    """
    study_dir = Path(study_dir).resolve()
    path = study_dir / SCRIPTS_DIRNAME / JOBS_NAME
    try:
        if not path.is_file():
            return None
        payload = json.loads(_read_text(path))
        if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
            return None
        stored_init_gpu = int(payload.get('init_gpu', 0))
        stored_num_gpus = int(payload.get('num_gpus', 1))
        stored_round = int(payload.get('round') or 1)
    except (OSError, TypeError, ValueError, OverflowError, RecursionError):
        return None
    if stored_round < 1:
        return None
    if stored_init_gpu != int(init_gpu):
        return None
    if stored_num_gpus != int(num_gpus):
        return None
    if int(round_size) > 0 and stored_round != int(round_size):
        return None
    raw_batches = payload.get('batches')
    try:
        wait_groups(payload['jobs'], stored_round, raw_batches)
    except (KeyError, TypeError, ValueError):
        return None
    for job in payload['jobs']:
        if not include_done and run_succeeded(study_dir, str(job.get('run_id') or '')):
            continue
        if str(job.get('device') or '').lower() == 'cpu' and job.get('gpu') is not None:
            return None
        if job.get('config') and Path(str(job['config'])).is_file():
            mode, device = _job_fields(Path(str(job['config'])))
            if mode != job_mode(job) or device != str(job.get('device') or 'cuda').lower():
                return None
        config_path = study_dir / 'runs' / str(job['run_id']) / 'config.yaml'
        if config_path.is_file():
            mode, device = _job_fields(config_path)
            if job.get('mode') and mode != job_mode(job):
                return None
            if job.get('device') and device != str(job['device']).lower():
                return None

    def _keep(rows: list) -> list[dict[str, Any]]:
        if include_done:
            return list(rows)
        return [job for job in rows if not run_succeeded(study_dir, str(job.get('run_id') or ''))]

    jobs = _keep(payload['jobs'])
    batches = None
    if isinstance(raw_batches, list) and raw_batches and isinstance(raw_batches[0], list):
        batches = [kept for kept in (_keep(group) for group in raw_batches) if kept]
    return {
        'jobs_json': path,
        'job_list': jobs,
        'round': stored_round,
        'batches': batches,
        'n_jobs': len(jobs),
        'expand': {'study_dir': study_dir, 'index': study_dir / 'index.json', 'configs': []},
    }


def job_popen_kwargs(console: str) -> dict[str, Any]:
    """Windows ``new`` = one console window per run-one (same idea as Start-Process)."""
    if console == 'new' and os.name == 'nt':
        flag = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0x00000010)
        return {'creationflags': flag}
    return {}


def resolve_console(mode: str = 'auto') -> str:
    raw = str(mode or 'auto').strip().lower()
    if raw in ('', 'auto'):
        return 'new' if os.name == 'nt' else 'shared'
    if raw in ('new', 'window', 'windows', 'separate'):
        return 'new'
    if raw in ('shared', 'same', 'mix'):
        return 'shared'
    raise ValueError('console must be auto, new, or shared')


def launch_job_env(job: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    gpu = job.get('gpu')
    if gpu is not None and str(job.get('device') or 'cuda').lower() != 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = str(gpu)
    return env


def launch_jobs(
    study_dir: Path,
    jobs: list[dict[str, Any]],
    *,
    round_size: int = 4,
    python_exe: str | None = None,
    cwd: Path | None = None,
    batches: list[list[dict[str, Any]]] | None = None,
    retry_failed: bool = True,
    console: str = 'auto',
    refused_runs: list[str] | None = None,
) -> list[int]:
    if round_size < 1:
        raise ValueError('round must be >= 1')
    groups = wait_groups(jobs, round_size, batches)
    for job in jobs:
        run_id = str(job['run_id'])
        result = _run_result(study_dir, run_id)
        if result.get('status') == STATUS_SUCCEEDED:
            check_result_source(study_dir, run_id, result)
    py = python_exe or sys.executable
    root = cwd or repo_root_from(study_dir)
    codes: list[int] = []
    refused: set[Path] = set()
    def refuse(run_id: str) -> None:
        if Path(run_id) not in refused:
            refused.add(Path(run_id))
            if refused_runs is not None:
                refused_runs.append(run_id)
    console_mode = resolve_console(console)
    popen_extra = job_popen_kwargs(console_mode)
    try:
        index = load_index(study_dir)
    except (OSError, TypeError, ValueError):
        index = {}
    dependencies = {
        Path(run_id): parent
        for exp in index.get('experiments') or []
        for run in exp.get('runs') or []
        if (run_id := str(run.get('run_dir') or run.get('id') or ''))
        if (parent := sibling_train_dependency(study_dir, run_id)) is not None
    }
    for run_id, parent in dependencies.items():
        run_id = str(run_id)
        if _run_result(study_dir, run_id).get('status') == STATUS_SUCCEEDED and not run_succeeded(study_dir, run_id):
            if not _fail_result(study_dir, run_id, f'sibling train {parent} is unfinished or newer; re-evaluation required', only_success=True):
                refuse(run_id)
                print(f'error {run_id} Run busy; result preserved', flush=True)
    # A supplied batch must not bypass the train/eval barrier either.
    waves = [
        [kept for group in groups if (kept := [job for job in group if (job_mode(job) == 'eval') == is_eval])]
        for is_eval in (False, True)
    ]
    total_waits = sum(len(wave) for wave in waves)
    wait_index = 0

    def _run_groups(
        groups: list[list[dict[str, Any]]],
        *,
        kind: str,
    ) -> list[tuple[dict[str, Any], int]]:
        nonlocal wait_index
        pairs: list[tuple[dict[str, Any], int]] = []
        total = len(groups)
        for index, group in enumerate(groups, start=1):
            if kind == 'wait':
                wait_index += 1
            position = f'{wait_index}/{total_waits}' if kind == 'wait' else f'{index}/{total}'
            modes = '+'.join(dict.fromkeys(job_mode(job) for job in group)) or 'train'
            print(f'launch: {kind} {position} mode={modes}', flush=True)
            procs: list[tuple[dict[str, Any], subprocess.Popen[str], str]] = []
            primary_error = None
            try:
                for job in group:
                    run_id = str(job['run_id'])
                    validate_run_directory(run_id)
                    try:
                        with run_lock(Path(study_dir) / 'runs' / run_id):
                            pass
                    except RunLockedError:
                        refuse(run_id)
                        pairs.append((job, RUN_BUSY_EXIT))
                        print(f'error {run_id} Run already executing; not launched', flush=True)
                        continue
                    parent = dependencies.get(Path(run_id))
                    if parent is not None and not run_succeeded(study_dir, parent):
                        message = f'blocked: sibling train {parent} did not succeed'
                        changed = _fail_result(study_dir, run_id, message)
                        print(f'error {run_id} {message}', flush=True)
                        if not changed:
                            refuse(run_id)
                        pairs.append((job, 1 if changed else RUN_BUSY_EXIT))
                        continue
                    blocked = False
                    for eval_id, train_id in dependencies.items():
                        if Path(train_id) == Path(run_id) and _run_result(study_dir, str(eval_id)).get('status') == STATUS_SUCCEEDED:
                            if not _fail_result(study_dir, str(eval_id), f'sibling train {run_id} is being rerun; re-evaluation required', only_success=True):
                                blocked = True
                    if blocked:
                        refuse(run_id)
                        pairs.append((job, RUN_BUSY_EXIT))
                        print(f'error {run_id} dependent eval Run busy; not launched', flush=True)
                        continue
                    cmd = [py, '-m', 'rpipe', 'run-one', str(study_dir), run_id]
                    where = 'window' if console_mode == 'new' and os.name == 'nt' else 'here'
                    resource = f'gpu={job["gpu"]}' if job.get('gpu') is not None and str(job.get('device') or 'cuda').lower() != 'cpu' else 'cpu'
                    print(f'+ {resource} {job["run_id"]} ({where})', flush=True)
                    token = uuid.uuid4().hex
                    env = launch_job_env(job)
                    env[RUN_ATTEMPT_ENV] = token
                    procs.append(
                        (
                            job,
                            subprocess.Popen(
                                cmd,
                                cwd=str(root),
                                env=env,
                                **popen_extra,
                            ),
                            token,
                        )
                    )
            except BaseException as error:
                primary_error = error
            for job, proc, token in procs:
                try:
                    code = int(proc.wait())
                except BaseException as error:
                    if primary_error is None:
                        primary_error = error
                    try:
                        code = int(proc.wait())
                    except BaseException:
                        # shortcut: repeated wait failure leaves completion unknown; revisit for forced cancellation.
                        continue
                try:
                    pairs.append((job, code))
                    if code != 0:
                        if code == RUN_BUSY_EXIT or not _fail_result(study_dir, str(job['run_id']),
                                f'run-one exited with code {code}', attempt_token=token, only_success=True):
                            refuse(str(job['run_id']))
                            print(f'error {job["run_id"]} execution unowned or busy; result preserved; no retry', flush=True)
                        print(f'error {job["run_id"]} exit={code}', flush=True)
                    else:
                        try:
                            with run_lock(Path(study_dir) / 'runs' / str(job['run_id'])):
                                owned = owns_execution(Path(study_dir) / 'runs' / str(job['run_id']), token)
                        except RunLockedError:
                            owned = False
                        if not owned:
                            refuse(str(job['run_id']))
                            print(f'error {job["run_id"]} execution unowned or busy; no retry', flush=True)
                except BaseException as error:
                    if primary_error is None:
                        primary_error = error
            if primary_error is not None:
                raise primary_error
        return pairs

    for wave in waves:
        pairs = _run_groups(wave, kind='wait')
        codes.extend(code for _, code in pairs)
        failed = [
            job for job, code in pairs
            if Path(job['run_id']) not in refused
            and (code != 0 or not run_succeeded(study_dir, str(job['run_id'])))
            and (dependencies.get(Path(job['run_id'])) is None
                 or run_succeeded(study_dir, dependencies[Path(job['run_id'])]))
        ]
        if retry_failed and failed:
            print(f'retry {len(failed)} failed jobs (resume latest)', flush=True)
            retry_pairs = _run_groups([[job] for job in failed], kind='retry')
            codes.extend(code for _, code in retry_pairs)
    return codes
