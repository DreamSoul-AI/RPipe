"""Explicit, repeatable checkout aliases for external data and project output.

Run with --help. Preview is read-only; --apply creates missing roots and symlinks.
Existing entries are never replaced, and this module never starts an experiment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def _separate(left: Path, right: Path) -> bool:
    return not (left.is_relative_to(right) or right.is_relative_to(left))


def configure(repo: Path | str, data_root: Path | str, output_root: Path | str,
              *, apply: bool = False) -> dict:
    """Check both aliases before writing; preserve all existing files and targets."""
    paths = [Path(value).expanduser() for value in (repo, data_root, output_root)]
    if not all(path.is_absolute() for path in paths):
        raise ValueError('repo, data_root and output_root must be absolute paths')
    checkout, data, output = (path.resolve() for path in paths)
    git = ['git', '-c', f'safe.directory={checkout.as_posix()}', '-C', str(checkout)]
    actual = subprocess.run(git + ['rev-parse', '--show-toplevel'], check=True,
                            capture_output=True, text=True, encoding='utf-8', timeout=10)
    if Path(actual.stdout.strip()).resolve() != checkout:
        raise ValueError('repo must be the checkout root')
    if not all(_separate(left, right) for left, right in (
            (checkout, data), (checkout, output), (data, output))):
        raise ValueError('checkout, data and output roots must be separate, non-nested directories')
    entries = []
    for name, target in (('data', data), ('output', output)):
        if target.exists() and not target.is_dir():
            raise ValueError(f'external root is not a directory: {target}')
        alias = checkout / name
        tracked = subprocess.run(git + ['ls-files', '--', name], check=True,
                                 capture_output=True, text=True, timeout=10)
        ignored = subprocess.run(git + ['check-ignore', '--no-index', '--quiet', '--', name],
                                 capture_output=True, text=True, timeout=10)
        if tracked.stdout.strip() or ignored.returncode != 0:
            raise ValueError(f'alias must be untracked and Git-ignored: {alias}')
        if alias.is_symlink():
            if alias.resolve() != target or not alias.is_dir():
                raise FileExistsError(f'existing symlink has a different or missing target: {alias}')
            action = 'reuse'
        elif alias.exists() or getattr(alias, 'is_junction', lambda: False)():
            raise FileExistsError(f'preserve existing non-symlink entry: {alias}')
        else:
            action = 'create'
        entries.append({'alias': str(alias), 'target': str(target), 'action': action})
    if apply:
        for entry in entries:
            if entry['action'] == 'create':
                Path(entry['target']).mkdir(parents=True, exist_ok=True)
                try:
                    Path(entry['alias']).symlink_to(entry['target'], target_is_directory=True)
                except OSError as error:
                    raise OSError(f'cannot create symbolic link {entry["alias"]}; '
                                  'check OS symlink permission. Existing entries and targets are retained; '
                                  'rerun after resolving the error. No junction/copy fallback.') from error
    return {'mode': 'apply' if apply else 'preview', 'repo': str(checkout), 'entries': entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True, help='absolute checkout root')
    parser.add_argument('--data-root', type=Path, required=True, help='absolute central data root')
    parser.add_argument('--output-root', type=Path, required=True, help='absolute project output root')
    parser.add_argument('--apply', action='store_true', help='create missing roots and symbolic links')
    args = parser.parse_args(argv)
    try:
        report = configure(args.repo, args.data_root, args.output_root, apply=args.apply)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
