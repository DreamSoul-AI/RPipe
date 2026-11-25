"""Preview impact-based local checks and execute them through tests/run.py."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
SCIENCE = ('algorithm', 'data', 'model')
JOURNEYS = ['tests/rpipe/test_architecture.py', 'tests/rpipe/test_main.py',
            'tests/rpipe/flow/test_native.py', 'tests/rpipe/flow/test_run_artifacts.py',
            'tests/rpipe/flow/test_cancellation.py']


def git_paths(base: str) -> list[str]:
    """Include committed, staged, unstaged, renamed and untracked files."""
    prefix = ['git', '-c', f'safe.directory={REPO}', '-C', str(REPO)]
    paths = set()
    for args in (['diff', '--name-only', '--no-renames', '-z', base, '--'],
                 ['ls-files', '--others', '--exclude-standard', '-z']):
        output = subprocess.check_output(prefix + args).decode('utf-8')
        paths.update(path for path in output.split('\0') if path)
    return sorted(paths)


def selection(paths: list[str]) -> dict:
    """Narrow only known science modules; shared boundaries require full checks."""
    docs, science, targets = [], False, set()
    full = False
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or '..' in path.parts or '\\' in raw or ':' in raw:
            raise ValueError(f'expected repository-relative path: {raw}')
        if path.suffix == '.md':
            docs.append(raw)
        elif any(raw.startswith(f'src/rpipe/structure/{module}/') for module in SCIENCE):
            science = True
        elif raw.startswith('tests/rpipe/') and path.suffix == '.py' and path.name.startswith('test_'):
            targets.add(raw)
        else:
            full = True
    if science:
        targets.update(f'tests/rpipe/structure/{module}' for module in SCIENCE)
        targets.update(JOURNEYS)
        targets.update(['tests/studies', 'tests/rpipe/flow/test_huggingface.py'])
    if any(not (REPO / target).exists() for target in targets):
        full = True
    scope = 'full' if full else 'affected' if targets else 'docs' if docs else 'empty'
    return {'scope': scope, 'changed': sorted(set(paths)), 'documents': docs,
            'targets': [] if full else sorted(targets),
            'package_required': full,
            'reason': {'full': 'shared code, execution configuration, tooling, packaging or unknown path',
                       'affected': 'science components and their consumers, or explicitly changed test files',
                       'docs': 'Markdown local file references', 'empty': 'no changed files'}[scope]}


def check_documents(paths: list[str]) -> list[str]:
    """Check current documents and inbound local file links after removals."""
    errors = []
    documents = {REPO / path for path in paths if (REPO / path).is_file()}
    if any(not (REPO / path).exists() for path in paths):
        documents.update(REPO.glob('*.md'))
        documents.update((REPO / 'docs').rglob('*.md'))
        if (REPO / 'tests/README.md').is_file():
            documents.add(REPO / 'tests/README.md')
    for path in sorted(documents):
        text = path.read_text(encoding='utf-8-sig')
        if '\ufffd' in text:
            errors.append(f'{path.relative_to(REPO)}: invalid replacement character')
        # Ignore code examples, which may deliberately illustrate missing files.
        prose = re.sub(r'(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$', '', text)
        for link in re.findall(r'\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)', prose):
            url = urlsplit(link.strip('<>'))
            if url.scheme or url.netloc or not url.path:
                continue
            target = path.parent / unquote(url.path)
            if not target.exists():
                errors.append(f'{path.relative_to(REPO)}: missing local file {link}')
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--base', help='compare this Git revision to the current worktree')
    source.add_argument('--changed', action='append', help='explicit changed path; repeat for an exported snapshot')
    parser.add_argument('--execute', action='store_true', help='default is a read-only plan preview')
    parser.add_argument('--results-root', type=Path)
    args = parser.parse_args(argv)
    try:
        plan = selection(args.changed if args.changed is not None else git_paths(args.base))
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return 0
    root = (args.results_root or Path(tempfile.gettempdir()) / 'rpipe-checks').resolve()
    if root == REPO or REPO in root.parents:
        parser.error('results-root must be outside the checkout')
    evidence = Path(tempfile.mkdtemp(prefix='check-', dir=_make_root(root)))
    errors = check_documents(plan['documents'])
    env = dict(os.environ, OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    command = [sys.executable, str(REPO / 'tests/run.py'), '--offline',
               '--timeout-seconds', '1200', '--results-root', str(evidence / 'tests'),
               '--plan-id', 'local-' + plan['scope'], '--', '-p', 'no:cacheprovider',
               '--basetemp', str(evidence / 'temp'), *plan['targets']]
    code = 1 if errors else 0
    if not errors and plan['scope'] in ('full', 'affected'):
        code = subprocess.call(command, cwd=REPO, env=env)
    result = dict(plan, exit_code=code, document_errors=errors,
                  command=command if plan['scope'] in ('full', 'affected') else [],
                  package_status='required separately' if plan['package_required'] else 'not selected',
                  tensorboard_status='optional real-event check remains separate',
                  evidence=str(evidence))
    (evidence / 'selection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return code


def _make_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


if __name__ == '__main__':
    raise SystemExit(main())
