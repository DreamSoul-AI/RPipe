"""Verify an installed distribution through its public, offline CLI.

Invoke with the new venv Python, the installed wheel/sdist, and a new workspace.
The Toy/Stub route verifies packaging and artifact contracts, not ML accuracy.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import import_module, metadata, util
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _verify_record_file(distribution, entry):
    path = Path(distribution.locate_file(entry)).resolve()
    encoded = base64.urlsafe_b64encode(bytes.fromhex(_sha256(path))).decode().rstrip('=')
    if entry.hash is None or entry.hash.mode != 'sha256' or entry.hash.value != encoded:
        raise RuntimeError(f'installed file differs from its distribution RECORD: {entry}')
    return path


def _installed_identity(artifact):
    prefix = Path(sys.prefix).resolve()
    if prefix == Path(sys.base_prefix).resolve():
        raise RuntimeError('package smoke requires its own new venv')
    libraries = {Path(sysconfig.get_path(name)).resolve() for name in ('purelib', 'platlib')}
    if any(not path.is_relative_to(prefix) for path in libraries):
        raise RuntimeError('venv library paths escape this environment')
    distribution = metadata.distribution('rpipe')
    distribution_root = Path(distribution.locate_file('')).resolve()
    if distribution_root not in libraries:
        raise RuntimeError('rpipe distribution metadata belongs to another environment')
    artifact = Path(artifact).resolve(strict=True)
    if not artifact.name.endswith(('.whl', '.tar.gz')):
        raise RuntimeError('the installed artifact must be an explicit wheel or sdist')
    artifact_sha = _sha256(artifact)
    try:
        origin = json.loads(distribution.read_text('direct_url.json'))
        url = urlsplit(origin['url'])
        archive = origin['archive_info']
        digest = archive.get('hashes', {}).get('sha256')
        old_digest = archive.get('hash', '').removeprefix('sha256=')
        if digest is None:
            digest = old_digest
        if (url.scheme != 'file' or url.netloc not in ('', 'localhost')
                or Path(url2pathname(url.path)).resolve() != artifact
                or digest != artifact_sha or old_digest and old_digest != artifact_sha
                or origin.get('dir_info', {}).get('editable')):
            raise ValueError('artifact provenance mismatch')
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError('installed distribution is not from the specified artifact bytes') from error
    files = distribution.files or []
    package_files = [entry for entry in files if entry.as_posix().startswith('rpipe/')
                     and entry.suffix == '.py']
    if not package_files:
        raise RuntimeError('installed distribution has no recorded rpipe Python files')
    for entry in package_files:
        path = _verify_record_file(distribution, entry)
        if not path.is_relative_to(distribution_root / 'rpipe'):
            raise RuntimeError('installed rpipe file escapes its distribution')
    recorded = {Path(distribution.locate_file(entry)).resolve() for entry in package_files}
    actual = {path.resolve() for path in (distribution_root / 'rpipe').rglob('*.py')}
    if actual != recorded:
        raise RuntimeError('unrecorded Python files remain in the installed package')
    expected = (distribution_root / 'rpipe/__init__.py').resolve()
    spec = util.find_spec('rpipe')
    if spec is None or spec.origin is None or Path(spec.origin).resolve() != expected:
        raise RuntimeError('rpipe import resolves outside the installed distribution')
    module = import_module('rpipe')
    if Path(module.__file__).resolve() != expected or distribution.version != module.__version__:
        raise RuntimeError('distribution metadata and imported module differ')
    scripts = Path(sysconfig.get_path('scripts')).resolve()
    console = scripts / ('rpipe.exe' if os.name == 'nt' else 'rpipe')
    if not scripts.is_relative_to(prefix) or not console.is_file() or not console.resolve().is_relative_to(scripts):
        raise RuntimeError('installed console script is outside this venv or missing')
    entries = [entry for entry in files if Path(distribution.locate_file(entry)).resolve() == console.resolve()]
    if len(entries) != 1:
        raise RuntimeError('console script is absent from the installed distribution RECORD')
    _verify_record_file(distribution, entries[0])
    if not any(entry.group == 'console_scripts' and entry.name == 'rpipe'
               and entry.value == 'rpipe.flow.cli:main' for entry in distribution.entry_points):
        raise RuntimeError('installed console entry does not target the current CLI')
    return {'installed_module': str(expected), 'version': module.__version__,
            'python': sys.executable, 'venv_prefix': str(prefix),
            'distribution_root': str(distribution_root), 'console': str(console),
            'artifact': str(artifact), 'artifact_sha256': artifact_sha,
            'verified_python_files': len(package_files)}


def _command_environment():
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--artifact', required=True, type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace.exists():
        parser.error('workspace must be new; retained smoke artifacts are never overwritten')
    installed = _installed_identity(args.artifact)
    workspace.mkdir(parents=True)
    os.chdir(workspace)
    console = Path(installed['console'])
    evidence = {**installed, 'scope': 'offline Toy/Stub packaging and CLI contract',
                'gpu_used': False, 'commands': [], 'passed': False}
    environment = _command_environment()

    def command(argv, *, expected=0):
        launch_errors = []
        for attempt in range(20):
            try:
                completed = subprocess.run(argv, cwd=workspace, env=environment,
                                           capture_output=True, text=True,
                                           encoding='utf-8', errors='replace', timeout=120)
                break
            except OSError as exc:
                # A Windows sharing violation means CreateProcess did not start;
                # retain it and retry only this bounded pre-launch failure.
                if getattr(exc, 'winerror', None) not in (32, 33) or attempt == 19:
                    raise
                launch_errors.append({'attempt': attempt + 1, 'error': str(exc), 'winerror': exc.winerror})
                time.sleep(0.25)
        record = {'argv': [str(v) for v in argv], 'exit_code': completed.returncode,
                  'stdout': completed.stdout, 'stderr': completed.stderr, 'prelaunch_retries': launch_errors}
        evidence['commands'].append(record)
        (workspace / 'SMOKE_RESULT.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
        if completed.returncode != expected:
            raise RuntimeError(f'command failed: {argv}\n{completed.stdout}\n{completed.stderr}')
        return completed.stdout

    command([sys.executable, '-m', 'rpipe', '--help'])
    command([str(console), '--help'])
    study = workspace / 'toy'
    study.mkdir()
    (study / 'experiment_config.yaml').write_text('''version: installed-smoke-v1
data:
  name: Toy
  source: stub
model:
  name: linear
algorithm:
  source: custom_torch
  mode: train
  num_steps: 2
  progress_unit: step
system:
  device: cpu
  deterministic: true
  cudnn_benchmark: false
''', encoding='utf-8')
    (study / 'study.yaml').write_text('''study: installed_toy
origin: foreign
seeds: [0, 1]
axes:
  algorithm.mode: [train, eval]
''', encoding='utf-8')
    run_root = workspace / 'artifacts'
    planned = json.loads(command([sys.executable, '-m', 'rpipe', 'plan', str(study), '--run-root', str(run_root)]))
    if run_root.exists():
        raise RuntimeError('plan wrote artifacts')
    if len(planned) != 4 or len({row['id'] for row in planned}) != 4:
        raise RuntimeError('plan did not produce four unique train/eval x seed Runs')
    source_before = {path.relative_to(study): path.read_bytes() for path in study.rglob('*') if path.is_file()}
    command([sys.executable, '-m', 'rpipe', 'make', str(study), '--run-root', str(run_root)])
    config_before = {row['id']: (Path(row['run_dir']) / 'config.yaml').read_bytes() for row in planned}
    command([str(console), 'launch', str(study), '--run-root', str(run_root)])
    result_before = {}
    for row in planned:
        run_dir = Path(row['run_dir'])
        body = json.loads((run_dir / 'result.json').read_text(encoding='utf-8'))
        if (body['status'] != 'succeeded' or body.get('evidence_complete') is not True
                or body.get('quality') is False or body['structure']['data']['source'] != 'stub'):
            raise RuntimeError(f'invalid Run status/source: {row["id"]}')
        if (run_dir / '.execution').exists():
            raise RuntimeError('a completed smoke Run still has an execution owner')
        if (run_dir / 'config.yaml').read_bytes() != config_before[row['id']]:
            raise RuntimeError('launch mutated a generated config')
        log = run_dir / 'assets/logs/events.jsonl'
        if not log.is_file() or not (run_dir / 'assets/tracker/scalars.jsonl').is_file():
            raise RuntimeError('Run log/tracker missing')
        records = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        if not records or any(record.get('task_id') != row['id'] or record.get('attempt_id') != body['attempt']
                              or type(record.get('pid')) is not int for record in records):
            raise RuntimeError('Run structured log identity missing or mismatched')
        result_before[row['id']] = (run_dir / 'result.json').read_bytes()
    aggregate = json.loads(command([sys.executable, '-m', 'rpipe', 'process', str(study), '--run-root', str(run_root)]))
    if not aggregate['complete'] or len(aggregate['experiments']) != 2:
        raise RuntimeError('Study process not complete')
    before_reads = {path.relative_to(run_root): path.read_bytes() for path in run_root.rglob('*') if path.is_file()}
    command([str(console), 'status', str(study), '--run-root', str(run_root)])
    command([sys.executable, '-m', 'rpipe', 'logs', str(study), '--run-root', str(run_root)])
    command([str(console), 'report', str(study), '--run-root', str(run_root)])
    after_reads = {path.relative_to(run_root): path.read_bytes() for path in run_root.rglob('*') if path.is_file()}
    if before_reads != after_reads:
        raise RuntimeError('readonly CLI rewrote runtime artifacts')
    # Completed results are immutable; an explicit new version is required to execute again.
    command([sys.executable, '-m', 'rpipe', 'launch', str(study), '--run-root', str(run_root)], expected=1)
    for row in planned:
        if (Path(row['run_dir']) / 'result.json').read_bytes() != result_before[row['id']]:
            raise RuntimeError('repeated launch rewrote a successful Run')
    if {path.relative_to(study): path.read_bytes() for path in study.rglob('*') if path.is_file()} != source_before:
        raise RuntimeError('execution changed source declaration tree')
    evidence.update(passed=True, runs_succeeded=4, experiments_complete=2,
                    generated_configs_unchanged=True, completed_runs_rejected_on_second_launch=True, source_tree_unchanged=True)
    (workspace / 'SMOKE_RESULT.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'installed_module': installed['installed_module'], 'workspace': str(workspace),
                      'commands': len(evidence['commands']), 'runs_succeeded': 4}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
