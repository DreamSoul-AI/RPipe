"""Thin explicit CLI. All writes require an external --run-root."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from rpipe import __version__


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='rpipe')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'make', 'run', 'study', 'launch', 'run-one', 'status', 'logs', 'report', 'process', 'shared', 'data', 'tensorboard'):
        command = commands.add_parser(name)
        command.add_argument('study_dir', type=Path)
        if name == 'run-one': command.add_argument('run_id')
        command.add_argument('--run-root', type=Path)
        command.add_argument('--version', dest='study_version')
        command.add_argument('--id', dest='selected_id')
        if name == 'tensorboard': command.add_argument('--out', type=Path, required=True)
        if name == 'data': command.add_argument('--download', action='store_true')
        if name == 'process': command.add_argument('--figures', action='store_true')
        if name in ('status', 'report'): command.add_argument('--legacy', action='store_true')
        if name in ('run', 'study', 'launch', 'run-one'):
            command.add_argument('--timeout', type=float)
            command.add_argument('--memory-bytes', type=int)
    compare = commands.add_parser('compare')
    compare.add_argument('left', type=Path)
    compare.add_argument('right', type=Path)
    compare.add_argument('--legacy', action='store_true')
    compare.add_argument('--atol', type=float, default=0.0)
    compare.add_argument('--rtol', type=float, default=0.0)
    compare.add_argument('--checkpoints', nargs='+', default=['latest'])
    compare.add_argument('--out', type=Path)
    compare.add_argument('--study-source', type=Path, help='explicit recipe source for trusted legacy checkpoint classes')
    worker = commands.add_parser('_worker', help=argparse.SUPPRESS)
    worker.add_argument('request', type=Path)
    worker.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == '_worker':
            from .run import worker as execute_worker
            return execute_worker(args.request, args.output)
        from rpipe import api
        if args.command == 'compare':
            if not args.legacy:
                parser.error('directory comparison requires --legacy; use api.compare with verified RunSpecs')
            value = api.compare_runs(args.left, args.right, atol=args.atol, rtol=args.rtol, checkpoints=args.checkpoints,
                                     study_dir=args.study_source)
            value.update(legacy=True, verified=False, evidence_complete=False)
            if args.out:
                for source in (args.left.resolve(), args.right.resolve()):
                    if args.out.resolve().is_relative_to(source):
                        raise ValueError('comparison output must be outside input Run directories')
                api.write_compare(args.out, value)
            print(json.dumps(value, default=str))
            return 0 if value['passed'] else 1
        if getattr(args, 'legacy', False):
            print(json.dumps(api.read_legacy(args.study_dir), default=str))
            return 0
        if args.run_root is None:
            parser.error('--run-root is required; old Study directories are never implicitly migrated')
        specs = api.plan(args.study_dir, run_root=args.run_root, version=args.study_version)
        selected = getattr(args, 'run_id', None) or args.selected_id
        if selected:
            specs = tuple(s for s in specs if s.id == selected)
            if not specs: parser.error('Run id is not in the actual current plan')
        if args.command == 'plan':
            value = [{'id': s.id, 'experiment': s.experiment, 'seed': s.seed, 'source_digest': s.source_digest,
                      'run_dir': str(s.run_dir), 'train_id': s.train_id} for s in specs]
        elif args.command == 'make':
            value = api.make(specs)
        elif args.command in ('run', 'study', 'launch', 'run-one'):
            if args.command in ('run', 'study'):
                api.make(specs)
            # A single coordinator handles all public launch paths and the train/eval barrier.
            value = [{'status': r.status, 'quality': r.quality, 'evidence_complete': r.evidence_complete,
                      'error_type': r.error.get('type') if r.error else None}
                     for r in api.launch(specs, budget=api.Budget(args.timeout, args.memory_bytes))]
            print(json.dumps(value, default=str))
            return 0 if all(r['status'] == 'succeeded' for r in value) else 1
        elif args.command == 'status':
            value = [api.read_run(s) for s in specs]
        elif args.command == 'logs':
            print('\n'.join(api.logs(s) for s in specs))
            return 0
        elif args.command == 'report':
            print(api.report(specs), end='')
            return 0
        elif args.command == 'shared':
            value = {'data': str(args.run_root.resolve() / 'shared' / 'data'),
                     'model': str(args.run_root.resolve() / 'shared' / 'model')}
        elif args.command == 'data':
            from rpipe.payloads.data_profile import profile_study
            value = profile_study(args.study_dir, shared_data_dir=args.run_root / 'shared' / 'data',
                                  profiles_dir=specs[0].study_root / 'shared' / 'data', download=args.download)
        elif args.command == 'tensorboard':
            from .exports import export_tensorboard
            value = export_tensorboard(specs[0].study_root, args.out)
        else:
            value = api.process(specs)
            if args.figures:
                from .exports import write_learning_curves
                from rpipe.structure.artifacts import load_index
                write_learning_curves(specs[0].study_root, load_index(specs[0].study_root))
        print(json.dumps(value, default=str))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
