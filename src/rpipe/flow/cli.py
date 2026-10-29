"""Thin explicit CLI. All writes require an external --run-root."""
from __future__ import annotations
from rpipe.structure import make as planning
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import signal

from rpipe import __version__



@contextmanager
def _interrupt_scope(cancellation):
    previous = signal.getsignal(signal.SIGINT)
    def request(signum, frame):
        cancellation.request()
    signal.signal(signal.SIGINT, request)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _batch_succeeded(specs, reports, cancellation):
    return (bool(specs) and len(reports) == len(specs) and not cancellation.is_set()
            and all(report.status == 'succeeded' and report.quality is not False
                    and report.evidence_complete is True for report in reports))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='rpipe')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'make', 'run', 'launch', 'supervise', 'status', 'logs', 'report', 'process', 'shared', 'data', 'tensorboard'):
        command = commands.add_parser(name)
        command.add_argument('study_dir', type=Path)
        command.add_argument('--run-root', type=Path)
        command.add_argument('--version', dest='study_version')
        command.add_argument('--id', dest='selected_id')
        if name == 'tensorboard': command.add_argument('--out', type=Path, required=True)
        if name == 'data': command.add_argument('--download', action='store_true')
        if name == 'process': command.add_argument('--figures', action='store_true')
        if name in ('run', 'launch'):
            command.add_argument('--timeout', type=float)
            command.add_argument('--memory-bytes', type=int)
            command.add_argument('--retry', action='store_true')
        if name == 'supervise':
            command.add_argument('--evidence-dir', type=Path, required=True)
            command.add_argument('--run-seconds', type=float, required=True)
            command.add_argument('--batch-seconds', type=float, required=True)
            command.add_argument('--rss-bytes', type=int, required=True)
            command.add_argument('--output-bytes', type=int, required=True)
            command.add_argument('--poll-seconds', type=float, default=0.1)
            command.add_argument('--cleanup-seconds', type=float, default=2.0)
            command.add_argument('--retry', action='store_true')
    compare = commands.add_parser('compare')
    compare.add_argument('left', type=Path)
    compare.add_argument('right', type=Path)
    compare.add_argument('--atol', type=float, default=0.0)
    compare.add_argument('--rtol', type=float, default=0.0)
    compare.add_argument('--checkpoints', nargs='+', default=['latest'])
    compare.add_argument('--out', type=Path)
    worker = commands.add_parser('_worker', help=argparse.SUPPRESS)
    worker.add_argument('request', type=Path)
    worker.add_argument('output', type=Path)
    commands.add_parser('_supervisor-coordinator', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command == '_worker':
            from .worker import worker as execute_worker
            return execute_worker(args.request, args.output)
        if args.command == '_supervisor-coordinator':
            from .supervision import coordinator
            return coordinator()
        from rpipe.structure.artifact import readout, comparison, io
        from .context import Budget, Cancellation, SupervisionLimits
        from .supervision import launch, supervise
        from .process import process_study
        if args.command == 'compare':
            left, right = readout.ref_from_run_dir(args.left), readout.ref_from_run_dir(args.right)
            value = comparison.compare(left, right, atol=args.atol, rtol=args.rtol,
                                       checkpoints=args.checkpoints)
            if args.out:
                comparison.write_compare(args.out, value)
            print(json.dumps(value, default=str))
            return 0 if value['passed'] else 1
        if args.run_root is None:
            parser.error('--run-root is required; old Study directories are never implicitly migrated')
        if args.command in ('status', 'logs', 'report'):
            from rpipe.structure.control import _component
            version = args.study_version
            if version is None:
                declaration = io.load_config(args.study_dir / 'study.yaml')
                base = io.load_config(args.study_dir / 'experiment_config.yaml')
                fixed = declaration.get('fixed', {})
                if not isinstance(fixed, dict):
                    raise ValueError('Study fixed conditions must be a mapping')
                version = str(next((value for value in (fixed.get('version'), base.get('version'))
                                    if value is not None), 'default'))
            _component(version, 'version')
            root = Path(args.run_root).resolve()
            study_root = io.contained(root / args.study_dir.name / version, root)
            refs = readout.refs_from_index(study_root)
            if args.selected_id:
                refs = tuple(ref for ref in refs if ref.id == args.selected_id)
                if not refs:
                    parser.error('Run id is not in the published plan')
            if args.command == 'logs':
                print('\n'.join(readout.logs(ref) for ref in refs))
            elif args.command == 'report':
                print(readout.report(refs), end='')
            else:
                print(json.dumps([readout.read_run(ref) for ref in refs], default=str))
            return 0
        if args.command == 'supervise':
            limits = SupervisionLimits(args.run_seconds, args.batch_seconds, args.rss_bytes,
                                          args.output_bytes, args.poll_seconds, args.cleanup_seconds)
            limits.validate()
        specs = planning.plan(args.study_dir, run_root=args.run_root, version=args.study_version)
        selected = args.selected_id
        if selected:
            specs = tuple(s for s in specs if s.id == selected)
            if not specs: parser.error('Run id is not in the actual current plan')
        if args.command == 'plan':
            value = [{'id': s.id, 'experiment': s.experiment, 'seed': s.seed, 'source_digest': s.source_digest,
                      'run_dir': str(s.run_dir), 'version': s.version, 'train_id': s.train_id} for s in specs]
        elif args.command == 'make':
            value = planning.publish(specs)
        elif args.command == 'supervise':
            cancellation = Cancellation()
            with _interrupt_scope(cancellation):
                value = supervise(specs, limits=limits, evidence_dir=args.evidence_dir,
                                  cancellation=cancellation, retry=args.retry)
            print(json.dumps(value, default=str))
            return 0 if (not cancellation.is_set() and value['status'] == 'succeeded'
                         and value.get('evidence_complete') is True) else 1
        elif args.command in ('run', 'launch'):
            cancellation = Cancellation()
            with _interrupt_scope(cancellation):
                if args.command == 'run':
                    planning.publish(specs)
                reports = launch(specs, budget=Budget(args.timeout, args.memory_bytes),
                                 cancellation=cancellation, retry=args.retry)
            value = [{'status': r.status, 'quality': r.quality, 'evidence_complete': r.evidence_complete,
                      'attempt': r.attempt, 'scientific_status': r.scientific_status,
                      'derived_status': r.derived_status,
                      'error_type': r.error.get('type') if r.error else None} for r in reports]
            print(json.dumps(value, default=str))
            return 0 if _batch_succeeded(specs, reports, cancellation) else 1
        elif args.command == 'shared':
            value = {'data': str(args.run_root.resolve() / 'shared' / 'data'),
                     'model': str(args.run_root.resolve() / 'shared' / 'model')}
        elif args.command == 'data':
            from rpipe.structure.artifact.provenance import validate_source
            for spec in specs:
                validate_source(spec)
            from rpipe.structure.data.profile import profile_study
            value = profile_study(args.study_dir, configs=[spec.to_config() for spec in specs],
                                  shared_data_dir=args.run_root / 'shared' / 'data',
                                  profiles_dir=specs[0].study_root / 'shared' / 'data', download=args.download)
        elif args.command == 'tensorboard':
            from rpipe.structure.artifact.io import validate_run_root
            from rpipe.structure.artifact.io import validate_input_paths
            for spec in specs:
                validate_run_root(spec.source_dir, args.out)
                validate_input_paths(spec, output_root=args.out)
            from .exports import export_tensorboard
            value = export_tensorboard(specs[0].study_root, args.out,
                                       run_ids=[s.id for s in specs] if selected else None)
        else:
            value = process_study(specs)
            if args.figures:
                from .exports import write_learning_curves
                from rpipe.structure.artifact.io import load_index
                write_learning_curves(specs[0].study_root, load_index(specs[0].study_root))
        print(json.dumps(value, default=str))
        return 0
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        if args.command == 'compare' and isinstance(exc, RuntimeError):
            raise  # Malformed comparison inputs are ValueError; program failures must remain visible.
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
