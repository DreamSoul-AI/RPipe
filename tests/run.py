"""Unified non-interactive test entry (TESTING.md 附录：RPipe)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _expr(parts: list[str]) -> str:
    return ' and '.join(f'({part})' for part in parts if part)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run RPipe tests with TESTING.md filters.')
    parser.add_argument('--level', choices=('unit', 'integration', 'e2e'))
    parser.add_argument('--type', dest='test_type', choices=('location', 'content', 'physical'))
    parser.add_argument('--priority', choices=('p1', 'p2', 'p3'))
    parser.add_argument(
        '--cost-class',
        action='append',
        choices=('c1', 'c2', 'c3', 'c4'),
        default=[],
        help='repeat to allow more than one cost class',
    )
    parser.add_argument('--layer', help='architecture layer marker, e.g. structure_layer')
    parser.add_argument('--module', help='module_* marker without prefix, e.g. control')
    parser.add_argument(
        '--fast',
        action='store_true',
        help='unit + p1 + c1, exclude slow/external/gpu',
    )
    parser.add_argument(
        '--core',
        action='store_true',
        help='unit c1 and c2, exclude slow/external/gpu',
    )
    parser.add_argument('--all', action='store_true', help='run every collected test')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    extra = list(args.pytest_args)
    if extra and extra[0] == '--':
        extra = extra[1:]

    parts: list[str] = []
    cost_classes = list(args.cost_class)
    if args.fast:
        parts.append('unit and p1 and not slow and not external and not gpu')
        cost_classes = cost_classes or ['c1']
    elif args.core:
        parts.append('unit and not slow and not external and not gpu')
        cost_classes = cost_classes or ['c1', 'c2']
    elif not args.all:
        if args.level:
            parts.append(args.level)
        if args.test_type:
            parts.append(args.test_type)
        if args.priority:
            parts.append(args.priority)
        if args.layer:
            parts.append(args.layer)
        if args.module:
            name = args.module if args.module.startswith('module_') else f'module_{args.module}'
            parts.append(name)

    cmd = [sys.executable, '-m', 'pytest']
    expr = _expr(parts)
    if expr:
        cmd.extend(['-m', expr])
    for cost_class in cost_classes:
        cmd.extend(['--cost-class', cost_class])
    cmd.extend(extra)
    print(subprocess.list2cmdline(cmd), flush=True)
    # Preserve environment and diagnostics when an embedding process captures
    # this launcher; implicit handle/environment inheritance can be lost there.
    return subprocess.call(cmd, env=dict(os.environ), stdout=sys.stdout, stderr=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
