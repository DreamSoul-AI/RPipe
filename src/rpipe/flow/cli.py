"""flow cli: argv → make + Runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from rpipe.flow.process import process_path, run_study as process_study
from rpipe.structure.api.artifact_api import (
    ProvenanceChangedError,
    check_frozen,
    load_provenance,
    provenance_changes,
)
from rpipe.structure.api.artifact_api import format_logs, format_status, list_runs, write_numbers
from rpipe.structure.api.artifact_api import compare_runs, format_compare, write_compare
from rpipe.flow.context import FlowContext
from rpipe.flow.runner import FlowRunner
from rpipe.structure.api.artifact_api import artifact_layout, load_config
from rpipe.structure.api.artifact_api import announce, clear_activity, read_activity, _format_activity
from rpipe.structure.api.make_api import (
    expand_study,
    filter_batches_by_mode,
    filter_jobs_by_mode,
    launch_jobs,
    load_launch_plan,
    load_study_yaml,
    plan_jobs,
    run_succeeded,
    write_launch_scripts,
)
from rpipe.structure.api.system_api import apply_model_origin, normalize_origin
from rpipe.structure.api import data_api
from rpipe.structure.api.make_api import (
    attach_estimates,
    batch_summaries,
    capacity_report,
    estimate_wall_seconds,
    pack_jobs,
    probe_gpus,
    requires_gpu,
    summarize_capacity,
)


def _prepare_shared(study_dir: Path | str, config_paths: list[Path]) -> None:
    study = load_study_yaml(study_dir)
    flow = study.get('flow') or {}
    enabled = flow.get('prepare_shared', True)
    if not isinstance(enabled, bool):
        raise TypeError('flow.prepare_shared must be a boolean')
    if not enabled:
        return
    names = data_api.prepare_shared(study_dir, config_paths)
    if names:
        print('shared data: ' + ', '.join(names), flush=True)


def launch_one(
    study_dir: Path,
    run_id: str,
    phases: list[str] | None = None,
) -> Path:
    layout = artifact_layout(study_dir, run_id)
    cfg = load_config(layout.config_path)
    ctx = FlowContext(study_dir=study_dir, layout=layout, config=cfg)
    return FlowRunner(phases=phases).run(ctx)


def launch_runs(
    study_dir: Path,
    config_paths: list[Path],
    phases: list[str] | None = None,
) -> list[Path]:
    return [launch_one(study_dir, path.parent.name, phases=phases) for path in config_paths]


def run_study(
    study_dir: Path | str,
    *,
    skip_launch: bool = False,
    phases: list[str] | None = None,
) -> dict[str, Any]:
    out = expand_study(study_dir)
    result_paths: list[Path] = []
    if not skip_launch:
        _prepare_shared(out['study_dir'], list(out['configs']))
        result_paths = launch_runs(Path(out['study_dir']), list(out['configs']), phases=phases)
        process_study(Path(out['study_dir']))
    return {
        'study_dir': out['study_dir'],
        'index': out['index'],
        'configs': out['configs'],
        'results': result_paths,
    }


def _parse_round(text: str) -> int:
    raw = str(text).strip().lower()
    if raw in ('auto', '0'):
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('round must be an int or auto') from exc
    if value < 0:
        raise argparse.ArgumentTypeError('round must be >= 0 (0=auto)')
    return value


def _parse_phases(raw: str) -> list[str] | None:
    return [s.strip() for s in raw.split(',') if s.strip()] or None


_JOB_MODES = frozenset({'train', 'eval', 'inference'})


def _normalize_modes(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    modes: list[str] = []
    for item in raw:
        for part in str(item).split(','):
            mode = part.strip().lower()
            if not mode:
                continue
            if mode not in _JOB_MODES:
                raise ValueError(f'mode must be train, eval, or inference; got {mode!r}')
            if mode not in modes:
                modes.append(mode)
    return modes or None


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('study_dir', type=Path, help='Path to studies/<name>/')
    parser.add_argument(
        '--skip-launch',
        action='store_true',
        help='Write configs and index only',
    )
    parser.add_argument(
        '--phases',
        default='',
        help='comma-separated phases; default is the full chain',
    )


def _add_make_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('study_dir', type=Path, help='Path to studies/<name>/')
    parser.add_argument('--init-gpu', default=0, type=int)
    parser.add_argument('--num-gpus', default=1, type=int)
    parser.add_argument(
        '--round',
        default=0,
        type=_parse_round,
        metavar='N|auto',
        help='0/auto = same-type wait groups packed by VRAM; N = fixed chunk size',
    )
    parser.add_argument('--split-round', default=65535, type=int)
    parser.add_argument(
        '--include-done',
        action='store_true',
        help='include Runs that already succeeded',
    )
    parser.add_argument(
        '--console',
        default='auto',
        help='auto/new/shared: Windows auto=new window per run-one; shared=mix in this terminal',
    )


def _make_from_args(args: argparse.Namespace) -> dict[str, Any]:
    study_hint = Path(args.study_dir)
    declared = load_study_yaml(study_hint)
    raw_origin = declared.get('origin')
    if raw_origin not in (None, ''):
        chosen = normalize_origin(raw_origin)
        hub = apply_model_origin(chosen)
        announce(study_hint, 'make', f'origin {chosen} model {hub}')
    announce(study_hint, 'make', 'expand')
    out = expand_study(args.study_dir)
    _prepare_shared(out['study_dir'], list(out['configs']))
    study_dir = Path(out['study_dir'])
    announce(study_dir, 'make', 'pack')
    jobs = plan_jobs(
        study_dir,
        list(out['configs']),
        init_gpu=args.init_gpu,
        num_gpus=args.num_gpus,
        include_done=bool(getattr(args, 'include_done', False)),
    )
    attach_estimates(jobs)
    gpus = probe_gpus(int(args.init_gpu), int(args.num_gpus)) if requires_gpu(jobs) else []
    requested = int(args.round)
    batches: list | None
    if requested <= 0:
        batches = pack_jobs(jobs, gpus)
        jobs = [job for group in batches for job in group]
        round_size = max((len(group) for group in batches), default=1)
        round_source = 'pack'
    else:
        batches = None
        round_size = requested
        round_source = 'cli'
    report = capacity_report(
        jobs,
        gpus,
        round_size=round_size,
        round_source=round_source,
    )
    if batches is not None:
        report['batches'] = batch_summaries(batches)
        report['wall_seconds'] = estimate_wall_seconds(batches)
    written = write_launch_scripts(
        study_dir,
        jobs,
        round_size=round_size,
        split_round=args.split_round,
        init_gpu=args.init_gpu,
        num_gpus=args.num_gpus,
        extra={'capacity': report},
        batches=batches,
    )
    written['round'] = round_size
    written['capacity'] = report
    written['batches'] = batches
    return {'expand': out, 'job_list': jobs, **written}


def _execute_run(args: argparse.Namespace) -> int:
    phases = _parse_phases(args.phases)
    out = run_study(args.study_dir, skip_launch=args.skip_launch, phases=phases)
    for path in out['configs']:
        print(path)
    print(out['index'])
    for path in out['results']:
        print(path)
    return 0


def _print_make_paths(written: dict[str, Any]) -> None:
    print(written['expand']['index'], flush=True)
    print(written['jobs_json'], flush=True)
    print(written['ps1'], flush=True)
    for path in written['bash']:
        print(path, flush=True)
    print(f'{written["n_jobs"]} jobs', flush=True)
    cap = written.get('capacity')
    if isinstance(cap, dict):
        print(summarize_capacity(cap), flush=True)


def _execute_make(args: argparse.Namespace) -> int:
    written = _make_from_args(args)
    _print_make_paths(written)
    clear_activity(written.get('expand', {}).get('study_dir') or args.study_dir)
    return 0


def _execute_launch(args: argparse.Namespace) -> int:
    written = None
    if not bool(getattr(args, 'remake', False)):
        written = load_launch_plan(
            args.study_dir,
            init_gpu=int(args.init_gpu),
            num_gpus=int(args.num_gpus),
            round_size=int(args.round),
            include_done=bool(getattr(args, 'include_done', False)),
        )
    if written is None:
        written = _make_from_args(args)
        _print_make_paths(written)
    else:
        print(written['jobs_json'], flush=True)
        print(f'{written["n_jobs"]} jobs', flush=True)
    jobs = written['job_list']
    study_dir = Path(written.get('expand', {}).get('study_dir') or args.study_dir).resolve()
    if not _provenance_gate(study_dir):
        return 2
    try:
        modes = _normalize_modes(getattr(args, 'modes', None))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if modes:
        jobs = filter_jobs_by_mode(jobs, modes)
        written['batches'] = filter_batches_by_mode(written.get('batches'), modes)
        print(f'mode {"+".join(modes)}: {len(jobs)} jobs', flush=True)
    if not jobs:
        print('nothing to launch')
        process_study(study_dir)
        print(process_path(study_dir))
        _print_launch_counts(study_dir)
        return 0
    launch_jobs(
        study_dir,
        jobs,
        round_size=int(written.get('round') or 1),
        batches=written.get('batches'),
        console=str(getattr(args, 'console', 'auto')),
        retry_failed=bool(getattr(args, 'retry_failed', True)),
    )
    still = [
        job['run_id']
        for job in jobs
        if not run_succeeded(study_dir, str(job['run_id']))
    ]
    if still:
        print('still failed: ' + ' '.join(still), flush=True)
    body = process_study(study_dir)
    print(process_path(study_dir), flush=True)
    if not body.get('complete'):
        print('process partial', flush=True)
    _print_launch_counts(study_dir)
    return 1 if still else 0


def _provenance_gate(study_dir: Path) -> bool:
    if not (study_dir / 'study.yaml').is_file():
        return True
    study = load_study_yaml(study_dir)
    try:
        check_frozen(study_dir, study)
    except ProvenanceChangedError as exc:
        print(exc, file=sys.stderr)
        return False
    if load_provenance(study_dir) is not None:
        changed = provenance_changes(study_dir, study)
        if changed:
            print(f'provenance: {len(changed)} changed', flush=True)
    return True


def _execute_compare(args: argparse.Namespace) -> int:
    try:
        report = compare_runs(
            args.run_a,
            args.run_b,
            atol=float(args.atol),
            rtol=float(args.rtol),
            checkpoints=list(args.checkpoints or ['latest']),
        )
        written = write_compare(args.out, report) if args.out is not None else None
    except (OSError, ValueError, RecursionError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(format_compare(report), end='')
    if written is not None:
        print(written, flush=True)
    return 0 if report['passed'] else 1


def _print_launch_counts(study_dir: Path) -> None:
    try:
        body = list_runs(study_dir)
    except (OSError, TypeError, ValueError, FileNotFoundError, RecursionError):
        return
    line = format_status(body).splitlines()[0]
    print(line, flush=True)


def _execute_report(args: argparse.Namespace) -> int:
    study_dir = Path(args.study_dir).resolve()
    try:
        path = write_numbers(study_dir)
    except FileNotFoundError:
        print(f'missing process: {process_path(study_dir)}', file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(path, flush=True)
    return 0


def _execute_logs(args: argparse.Namespace) -> int:
    study_dir = Path(args.study_dir).resolve()
    try:
        text = format_logs(study_dir)
    except FileNotFoundError:
        print(f'missing index: {study_dir / "index.json"}', file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(text, end='')
    return 0


def _execute_process(args: argparse.Namespace) -> int:
    body = process_study(Path(args.study_dir).resolve())
    print(process_path(args.study_dir))
    print('complete' if body.get('complete') else 'partial')
    return 0


def _execute_data(args: argparse.Namespace) -> int:
    from rpipe.structure.api.data_api import profile_study

    written = profile_study(args.study_dir)
    for path in written:
        print(path, flush=True)
    return 0 if written else 2


def _execute_status(args: argparse.Namespace) -> int:
    study_dir = Path(args.study_dir).resolve()
    try:
        modes = _normalize_modes(getattr(args, 'modes', None))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    activity = read_activity(study_dir)
    if activity is not None:
        print(_format_activity(activity['phase'], activity.get('detail') or '').rstrip(), flush=True)
    try:
        body = list_runs(study_dir, modes=modes)
    except FileNotFoundError:
        if activity is not None:
            return 0
        print(f'missing index: {study_dir / "index.json"}', file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(format_status(body), end='')
    return 0


def _execute_run_one(args: argparse.Namespace) -> int:
    phases = _parse_phases(args.phases)
    path = launch_one(Path(args.study_dir).resolve(), args.run_id, phases=phases)
    print(path)
    return 0


def _configure_stdio() -> None:
    """Windows consoles default to GBK; pack labels use ``×`` and need UTF-8."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except (AttributeError, OSError):
        pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        prog='rpipe',
        description='Run a Study: make configs, then flow phases',
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    run_p = sub.add_parser('run', help='make configs and run each Flow in order')
    _add_run_flags(run_p)

    study_p = sub.add_parser('study', help='alias for run')
    study_sub = study_p.add_subparsers(dest='study_cmd', required=True)
    study_run = study_sub.add_parser('run', help='same as rpipe run')
    _add_run_flags(study_run)

    make_p = sub.add_parser('make', help='write configs, index, and launch scripts')
    _add_make_flags(make_p)

    launch_p = sub.add_parser('launch', help='run pending jobs from make; make only if jobs.json is missing')
    _add_make_flags(launch_p)
    launch_p.add_argument(
        '--no-retry', action='store_false', dest='retry_failed', default=True,
        help='do not retry failed Runs during this launch (default: retry once)',
    )
    launch_p.add_argument(
        '--remake',
        action='store_true',
        help='ignore scripts/jobs.json and run make again',
    )
    launch_p.add_argument(
        '--mode',
        action='append',
        dest='modes',
        metavar='MODE',
        help='only this algorithm.mode (repeatable: train, eval). does not rewrite jobs.json',
    )

    one_p = sub.add_parser('run-one', help='run one Run id')
    one_p.add_argument('study_dir', type=Path)
    one_p.add_argument('run_id', type=str)
    one_p.add_argument('--phases', default='')

    tb_p = sub.add_parser('tensorboard', help='export JSONL trajectories to a fresh TensorBoard directory')
    tb_p.add_argument('study_dir', type=Path)
    tb_p.add_argument('--out', type=Path, required=True, help='new export directory outside the Study')

    proc_p = sub.add_parser('process', help='Study-level process: mean/std/min/max history')
    proc_p.add_argument('study_dir', type=Path, help='Path to studies/<name>/')

    report_p = sub.add_parser('report', help='write docs/NUMBERS.md from process.json')
    report_p.add_argument('study_dir', type=Path, help='Path to studies/<name>/')

    logs_p = sub.add_parser('logs', help='print run.log event lines in time order')
    logs_p.add_argument('study_dir', type=Path, help='Path to studies/<name>/')

    status_p = sub.add_parser('status', help='list index Runs with result status (read-only)')
    status_p.add_argument('study_dir', type=Path, help='Path to studies/<name>/')
    status_p.add_argument(
        '--mode',
        action='append',
        dest='modes',
        metavar='MODE',
        help='only this algorithm.mode (repeatable: train, eval)',
    )

    shared_p = sub.add_parser('shared', help='show shared input paths; explicitly migrate legacy caches')
    shared_p.add_argument('study_dir', type=Path)
    shared_p.add_argument('--migrate', action='store_true', help='validate and hard-link legacy inputs to project storage')

    data_p = sub.add_parser('data', help='profile shared inputs; write Study-owned normalization stats')
    data_p.add_argument('study_dir', type=Path, help='Path to studies/<name>/')

    compare_p = sub.add_parser('compare', help='compare two Run dirs: metrics, tracker history, checkpoints')
    compare_p.add_argument('run_a', type=Path, help='Path to studies/<name>/runs/<id>/')
    compare_p.add_argument('run_b', type=Path)
    compare_p.add_argument('--atol', default=0.0, type=float)
    compare_p.add_argument('--rtol', default=0.0, type=float)
    compare_p.add_argument(
        '--checkpoint',
        action='append',
        dest='checkpoints',
        metavar='NAME',
        help='checkpoint name under assets/checkpoints (repeatable; default latest)',
    )
    compare_p.add_argument('--out', type=Path, default=None, help='write the full comparison JSON')

    args = parser.parse_args(argv)
    if args.cmd == 'shared':
        import json
        from rpipe.structure.api.artifact_api import migrate_shared, shared_paths
        try:
            body = migrate_shared(args.study_dir) if args.migrate else {
                key: str(value) for key, value in shared_paths(args.study_dir).items()}
            print(json.dumps(body, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(f'shared: {exc}', file=sys.stderr)
            return 2
    if args.cmd == 'run' or (args.cmd == 'study' and args.study_cmd == 'run'):
        return _execute_run(args)
    if args.cmd == 'make':
        return _execute_make(args)
    if args.cmd == 'launch':
        return _execute_launch(args)
    if args.cmd == 'run-one':
        return _execute_run_one(args)
    if args.cmd == 'tensorboard':
        from rpipe.flow.process.curves import export_tensorboard
        try:
            dest = export_tensorboard(args.study_dir, args.out)
        except Exception as exc:
            print(f'TensorBoard export failed: {exc}', file=sys.stderr)
            return 2
        print(dest)
        return 0
    if args.cmd == 'process':
        return _execute_process(args)
    if args.cmd == 'report':
        return _execute_report(args)
    if args.cmd == 'logs':
        return _execute_logs(args)
    if args.cmd == 'status':
        return _execute_status(args)
    if args.cmd == 'data':
        return _execute_data(args)
    if args.cmd == 'compare':
        return _execute_compare(args)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
