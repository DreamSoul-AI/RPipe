"""Explicit source, input and environment observations; no scientific imports."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path, PureWindowsPath
import yaml

from ..control import RunSpec, AdmissionError, SourceMismatch
from .io import (contained, identity, load_config, validate_run_root, validate_input_paths,
                 _digest, _input_references, _default_vision_references)

def validate_source(spec: RunSpec) -> None:
    validate_run_root(spec.source_dir, spec.run_root)
    validate_input_paths(spec)
    for path in (spec.study_root, spec.study_root / 'summary', spec.study_root / 'summary' / 'figures',
                 spec.study_root / 'summary' / 'process.json', spec.study_root / 'index.json',
                 spec.study_root / '.planning', spec.study_root / '.planning' / '.rpipe.lock',
                 spec.study_root / 'shared' / 'data', spec.run_root / 'shared' / 'data',
                 spec.run_root / 'shared' / 'model',
                 spec.run_dir, spec.assets_dir, spec.assets_dir / 'logs', spec.assets_dir / 'checkpoints',
                 spec.assets_dir / 'tracker', spec.run_dir / 'attempts', spec.run_dir / '.execution',
                 spec.run_dir / 'config.yaml', spec.run_dir / 'execution.json', spec.result_path):
        contained(path, spec.run_root)
    if source_digest(spec.source_dir) != spec.source_digest:
        raise SourceMismatch('source changed after planning')



def _pinned_inputs(source_dir: Path, declaration: dict) -> list[tuple[Path, dict]]:
    provenance = declaration.get('provenance', {})
    if not isinstance(provenance, dict) or set(provenance) - {'inputs', 'include'}:
        raise AdmissionError('provenance must contain only inputs and include')
    includes = provenance.get('include', [])
    if not isinstance(includes, list) or any(not isinstance(item, str) or not item for item in includes):
        raise AdmissionError('provenance.include must be a list of nonempty strings')
    entries = provenance.get('inputs', [])
    if not isinstance(entries, list):
        raise AdmissionError('provenance.inputs must be a list')
    rows, seen = [], set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                or not entry['path'] or set(entry) not in ({'path', 'sha256'}, {'path', 'files'})):
            raise AdmissionError('input pin requires path and exactly one of sha256 or files')
        root = (source_dir / entry['path']).resolve()
        if root in seen:
            raise AdmissionError('input pin paths must be unique')
        seen.add(root)
        expected = {'': entry['sha256']} if 'sha256' in entry else entry['files']
        if not isinstance(expected, dict):
            raise AdmissionError('directory input files must be a mapping')
        files = {}
        for name, digest in expected.items():
            if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
                raise AdmissionError('input pins require SHA-256 hex digests')
            if 'files' in entry:
                relative = PureWindowsPath(name)
                if relative.anchor or not relative.parts or any(part in {'.', '..'} or ':' in part for part in relative.parts):
                    raise AdmissionError('directory input files require safe relative names')
                normalized = Path(*relative.parts).as_posix()
                if normalized in files:
                    raise AdmissionError('directory input files must be unique')
            else:
                normalized = ''
            files[normalized] = digest.lower()
        if 'sha256' in entry:
            if not root.is_file() or _digest(root) != files['']:
                raise SourceMismatch('declared input file is missing or its SHA-256 differs')
            record = {'path': entry['path'], 'resolved_path': str(root), 'kind': 'file',
                      'sha256': files[''], 'status': 'verified'}
        else:
            if not root.is_dir():
                raise SourceMismatch('declared input directory is missing')
            actual = {}
            for path in root.rglob('*'):
                if not path.resolve().is_relative_to(root):
                    raise AdmissionError('input symlink escapes its declared directory')
                if path.is_file():
                    actual[path.relative_to(root).as_posix()] = path
            if set(actual) != set(files):
                raise SourceMismatch('input directory contents differ from its explicit files manifest')
            if any(_digest(path) != files[name] for name, path in actual.items()):
                raise SourceMismatch('declared directory input SHA-256 differs')
            record = {'path': entry['path'], 'resolved_path': str(root), 'kind': 'directory',
                      'files': files, 'status': 'verified'}
        rows.append((root, record))
    return rows



def input_provenance(spec: RunSpec, *, observed_stats_source: str | None = None,
                     observed_stats_reference: str | None = None) -> dict:
    """Verify only explicit pins; describe gaps in declared references without scanning them."""
    pins = _pinned_inputs(spec.source_dir, load_config(spec.source_dir / 'study.yaml'))
    config = spec.to_config()
    references = _input_references(config)
    data = config.get('data', {})
    if observed_stats_reference is not None:
        if not isinstance(observed_stats_reference, str) or not observed_stats_reference:
            raise AdmissionError('observed statistics reference must be a nonempty path or None')
        observed_path = (spec.source_dir / observed_stats_reference).resolve()
        explicit = data.get('config', {}).get('stats_path')
        if explicit is not None and observed_path != (spec.source_dir / explicit).resolve():
            references['data.observed_stats_path'] = observed_stats_reference
    defaults = _default_vision_references(spec, config)
    if 'data.implicit_path' in defaults:
        references['data.implicit_path'] = defaults['data.implicit_path']
    rows = {}
    for name, value in references.items():
        if not isinstance(value, str) or not value:
            raise AdmissionError('input reference paths must be nonempty strings')
        path = (spec.source_dir / value).resolve()
        matching = next((record for root, record in pins if path == root
                         or record['kind'] == 'directory' and path.is_relative_to(root)), None)
        rows[name] = {'path': value, 'resolved_path': str(path), 'status': 'missing' if not path.exists() else
                      'verified' if matching is not None else 'unverified'}
        if (observed_stats_reference is not None and path == observed_path
                and observed_stats_source in ('study_profile', 'explicit_file')):
            rows[name].update(status='verified' if matching is not None else 'unverified',
                              observed_reference=observed_stats_reference, observed_source=observed_stats_source)
        if matching is not None:
            rows[name]['pin'] = matching['path']
    # Native's default vision route reads this mutable profile before using code constants.
    if 'data.implicit_stats_path' in defaults:
        profile = Path(defaults['data.implicit_stats_path'])
        if observed_stats_reference is not None:
            profile = observed_path
        status = 'unverified' if profile.is_file() else 'builtin_fallback'
        if observed_stats_source in ('study_profile', 'builtin_fallback'):
            status = 'unverified' if observed_stats_source == 'study_profile' else 'builtin_fallback'
        if observed_stats_reference is not None and observed_stats_source != 'builtin_fallback':
            status = 'verified' if any(profile == root or record['kind'] == 'directory'
                and profile.is_relative_to(root) for root, record in pins) else 'unverified'
        rows['data.implicit_stats_path'] = {'path': str(profile), 'resolved_path': str(profile.resolve()),
            'status': status, 'origin': 'default_vision_route', 'observed_source': observed_stats_source,
            'observed_reference': observed_stats_reference}
    return {'scope': 'declared_and_default_references', 'pins': [record for _, record in pins], 'references': rows,
            'complete': bool(rows) and all(row['status'] in {'verified', 'builtin_fallback'} for row in rows.values())}



def environment_snapshot() -> dict:
    """Observe the running interpreter and installed distributions without backend imports."""
    from importlib import metadata
    import platform
    import sys
    from rpipe import __version__

    dependencies = {'rpipe': __version__}
    for package in ('PyYAML', 'numpy', 'torch', 'torchvision', 'transformers', 'datasets', 'kornia', 'safetensors'):
        try:
            dependencies[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            dependencies[package] = None
    return {'python': sys.version.split()[0], 'implementation': platform.python_implementation(),
            'platform': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
            'dependencies': dependencies}



def source_manifest(source_dir: Path) -> dict:
    """Hash actual source and small declared inputs, not Git labels or output copies."""
    source_dir = Path(source_dir).resolve()
    declaration = yaml.safe_load((source_dir / 'study.yaml').read_text(encoding='utf-8-sig'))
    if not isinstance(declaration, dict):
        raise AdmissionError('Study declaration must be a mapping')
    _pinned_inputs(source_dir, declaration)
    library = Path(__file__).resolve().parents[2]
    members = [('rpipe/' + p.relative_to(library).as_posix(), p) for p in library.rglob('*.py')]
    members += [('study/' + p.relative_to(source_dir).as_posix(), p) for p in source_dir.rglob('*') if p.is_file() and p.suffix in {'.py', '.yaml', '.yml', '.json'} and (not any((part in {'__pycache__', '.tmp', 'runs', 'versions', 'shared', 'assets'} for part in p.relative_to(source_dir).parts)))]
    for raw in declaration.get('provenance', {}).get('include', []):
        path = (source_dir / raw).resolve()
        if not path.is_relative_to(source_dir) or not path.is_file():
            raise AdmissionError(f'missing or escaping declared source input: {raw}')
        members.append(('input/' + raw, path))
    rows = []
    for name, path in sorted(set(members)):
        if not path.resolve().is_relative_to(library if name.startswith('rpipe/') else source_dir):
            raise AdmissionError(f'source symlink escapes root: {name}')
        rows.append((name, _digest(path)))
    return {'digest': hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest(),
            'files': dict(rows)}



def source_digest(source_dir: Path) -> str:
    """Return the digest of the explicitly observed source manifest."""
    return source_manifest(source_dir)['digest']
