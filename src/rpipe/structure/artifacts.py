"""Owned execution files and immutable shared input preparation."""
from __future__ import annotations
from contextlib import closing
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
import contextlib
import errno
import hashlib
import json
import math
import os
import threading
import yaml
from dataclasses import dataclass
from typing import Any
from types import SimpleNamespace
from .contracts import RunSpec, AdmissionError, SourceMismatch, BusyError


@dataclass(frozen=True)
class ArtifactLayout:
    """Path calculation has no filesystem effects."""

    spec: RunSpec

    @property
    def root(self): return self.spec.run_dir
    @property
    def run_dir(self): return self.spec.run_dir
    @property
    def assets_dir(self): return self.spec.assets_dir
    @property
    def config_path(self): return self.root / 'config.yaml'
    @property
    def result_path(self): return self.spec.result_path
    @property
    def study_root(self): return self.spec.study_root
    @property
    def shared_dir(self): return self.study_root / 'shared'
    @property
    def shared_data_dir(self): return self.spec.run_root / 'shared' / 'data'
    @property
    def shared_model_dir(self): return self.spec.run_root / 'shared' / 'model'


def contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise AdmissionError(f'output path escapes run_root: {path}')
    return resolved


def identity(spec: RunSpec) -> dict[str, str]:
    config = json.dumps(spec.to_config(), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {'id': spec.id, 'source_digest': spec.source_digest,
            'config_digest': hashlib.sha256(config.encode()).hexdigest(), 'version': spec.version}


def validate_source(spec: RunSpec) -> None:
    contained(spec.run_dir, spec.run_root)
    if source_digest(spec.source_dir) != spec.source_digest:
        raise SourceMismatch('source changed after planning')


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(_read_text(path))
    if not isinstance(value, dict):
        raise AdmissionError(f'expected a JSON object: {path.name}')
    return value


def write_json(path: Path, value: dict[str, Any]) -> Path:
    return atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def load_config(path: Path | str) -> dict:
    value = yaml.safe_load(_read_text(path))
    if not isinstance(value, dict):
        raise AdmissionError('config must be a mapping')
    return value


def write_config(path: Path | str, config: dict) -> Path:
    return atomic_write_text(path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))


def load_index(study_root: Path | str) -> dict:
    return read_json(Path(study_root) / 'index.json')


def _cleanup_note(primary, cleanup) -> None:
    """Keep the primary error on Python 3.10 as well as newer runtimes."""
    primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), cleanup)
    if callable(getattr(primary, 'add_note', None)):
        primary.add_note(f'ownership cleanup failed; remains busy: {cleanup}')


def validate_train(spec: RunSpec) -> None:
    """Admit only the same seed/scientific conditions' successful train."""
    if not spec.train_id:
        return
    root = contained(spec.study_root / 'runs' / spec.train_id, spec.run_root)
    try:
        cfg = load_config(root / 'config.yaml')
        parent = read_json(root / 'result.json')
    except (OSError, ValueError) as exc:
        raise AdmissionError('own train evidence is missing or invalid') from exc
    train = RunSpec(spec.train_id, spec.experiment, spec.seed, cfg, spec.source_dir,
                    spec.run_root, spec.source_digest, spec.version)
    def conditions(body):
        body = dict(body)
        for key in ('id', 'description', 'tags'):
            body.pop(key, None)
        body['algorithm'] = dict(body.get('algorithm') or {})
        body['algorithm'].pop('mode', None)
        return body
    if (cfg.get('id') != spec.train_id or cfg.get('seed') != spec.seed
            or cfg.get('algorithm', {}).get('mode') != 'train'
            or conditions(cfg) != conditions(spec.to_config())
            or parent.get('status') != 'succeeded' or parent.get('identity') != identity(train)):
        raise AdmissionError('eval requires its own successful train with matching conditions and seed')


class Ownership:
    """A token-owned directory. Stale and unknown owners remain busy, never stolen."""

    def __init__(self, spec: RunSpec):
        self.spec = spec
        self.path = spec.run_dir / '.execution'
        self.token = uuid.uuid4().hex
        self.acquired = False

    def acquire(self) -> None:
        validate_source(self.spec)
        self.spec.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
        except FileExistsError as exc:
            raise BusyError(f'run busy or unresolved: {self.spec.id}') from exc
        self.acquired = True
        try:
            write_json(self.path / 'owner.json', {'token': self.token, 'pid': os.getpid(), **identity(self.spec)})
        except BaseException as primary:
            try:
                # Only this newly created directory is ours. A partial owner record
                # is retained as busy rather than guessing that execution is free.
                self.path.rmdir()
                self.acquired = False
            except OSError as cleanup:
                _cleanup_note(primary, cleanup)
            raise

    def release(self) -> None:
        if not self.acquired:
            return
        owner = read_json(self.path / 'owner.json')
        if owner.get('token') != self.token:
            raise BusyError('execution ownership changed during close')
        (self.path / 'owner.json').unlink()
        self.path.rmdir()
        self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, primary, tb):
        try:
            self.release()
        except BaseException as cleanup:
            if primary is None:
                raise
            _cleanup_note(primary, cleanup)

@contextmanager
def _owned_stream(manager):
    with ExitStack() as stack:
        value = manager.__enter__()

        def close(exc_type, exc, tb):
            try:
                return manager.__exit__(exc_type, exc, tb)
            except OSError:
                if exc_type is None:
                    raise
        stack.push(close)
        yield value

def _read_text(path: Path | str, *, errors: str='strict') -> str:
    with _owned_stream(closing(Path(path).open('r', encoding='utf-8', errors=errors))) as handle:
        return handle.read()

def _read_bytes(path: Path | str) -> bytes:
    with _owned_stream(closing(Path(path).open('rb'))) as handle:
        return handle.read()

def atomic_replace(source: Path | str, target: Path | str) -> Path:
    """Replace once, allowing a short Windows sharing/access denial to clear."""
    source, target = (Path(source), Path(target))
    for attempt in range(6):
        try:
            source.replace(target)
            return target
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))

def atomic_write_text(path: Path | str, text: str, encoding: str='utf-8', *, newline: str | None=None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f'{uuid.uuid4().hex}.tmp')
    owned = False
    try:
        try:
            mode = target.stat().st_mode
        except FileNotFoundError:
            mode = None
        tmp.touch(exist_ok=False)
        owned = True
        with _owned_stream(closing(tmp.open('w', encoding=encoding, newline=newline))) as handle:
            handle.write(text)
        if mode is not None:
            tmp.chmod(mode)
        return atomic_replace(tmp, target)
    finally:
        if owned:
            try:
                tmp.unlink(missing_ok=True)
            except PermissionError:
                try:
                    tmp.chmod(384)
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            except OSError:
                pass

_held = threading.local()

def _root_identity(root: Path) -> tuple[int, int]:
    identity = root.stat()
    return (identity.st_dev, identity.st_ino)

@contextlib.contextmanager
def cache_lock(root: Path | str, *, timeout: float=120):
    """OS-owned lock; a killed writer cannot leave a permanently owned lock."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('cache lock timeout must be finite and >= 0')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = (os.getpid(), *_root_identity(root))
    held = getattr(_held, 'roots', {})
    if key in held:
        yield
        return
    with contextlib.ExitStack() as stack:
        handle = stack.enter_context(_owned_stream(contextlib.closing((root / '.rpipe.lock').open('a+b'))))
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'shared input preparation locked: {root}') from exc
                time.sleep(0.05)
        held[key] = True
        _held.roots = held

        def release_lock(exc_type, _exc, _tb):
            del held[key]
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                if exc_type is None:
                    raise
        stack.push(release_lock)
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0')
            handle.flush()
        yield

def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with _owned_stream(path.open('rb')) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def link_input(source: Path | str, target: Path | str) -> Path:
    """Reference an immutable input without a second disk copy. Never overwrite."""
    source, target = (Path(source), Path(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target
    if target.exists():
        if target.samefile(source):
            return target
        if not target.is_file() or _digest(source) != _digest(target):
            raise FileExistsError(f'different input reference already exists: {target}')
        temporary = target.with_name(f'{uuid.uuid4().hex}.link')
    owned = False
    try:
        try:
            temporary.symlink_to(source.resolve())
        except OSError:
            os.link(source, temporary)
        owned = True
        if temporary != target:
            atomic_replace(temporary, target)
        return target
    finally:
        if owned and temporary != target:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

def reference_tree(source: Path | str, target: Path | str) -> None:
    """Reference one immutable raw tree through another backend's path layout."""
    source, target = (Path(source), Path(target))
    files = [path for path in sorted(source.rglob('*')) if path.is_file() and path.name not in ('stats.yaml', '.ready', '.rpipe.lock')]
    for path in files:
        destination = target / path.relative_to(source)
        if destination.exists() and (not destination.samefile(path)) and (_digest(destination) != _digest(path)):
            raise ValueError(f'conflicting raw input: {destination}')
    for path in files:
        link_input(path, target / path.relative_to(source))

def prepared_resource(root: Path | str, definition: dict, files: list[str], build):
    """Build immutable inputs once, publish atomically, validate every reuse.

    The caller supplies version/format identity and a preparation callback, not
    training. Interrupted staging is retained; no ready marker accepts it.
    """
    names = [Path(name) for name in files]
    if not names or len(set(names)) != len(names) or any((name.anchor or not name.name or '..' in name.parts or (name == Path('manifest.json')) for name in names)):
        raise ValueError('input files must be unique safe relative names, excluding manifest.json')
    root = Path(root)
    key = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    container = root / key
    cache = container / 'inputs'
    with cache_lock(container):
        if not cache.exists():
            staging = container / ('.building-' + uuid.uuid4().hex)
            staging.mkdir()
            build(staging)
            paths = [staging / name for name in files]
            if not all((path.is_file() for path in paths)):
                raise RuntimeError(f'incomplete inputs retained: {staging}')
            atomic_write_text(staging / 'manifest.json', json.dumps({'definition': definition, 'files': {name: _digest(staging / name) for name in files}}, sort_keys=True, indent=2))
            staging.rename(cache)
        manifest = json.loads(_read_text(cache / 'manifest.json'))
        recorded = manifest.get('files')
        if manifest.get('definition') != definition or not isinstance(recorded, dict) or set(recorded) != set(files) or any((_digest(cache / name) != digest for name, digest in recorded.items())):
            raise ValueError(f'input cache differs; preserved for diagnosis: {cache}')
        return (cache, _digest(cache / 'manifest.json'))

TRACKER = 'tracker'

TRACKER_STATE = 'tracker/tracker_state.json'

TRACKER_JSONL = 'tracker/scalars.jsonl'

LOGS = 'logs'

RUN_LOG = 'logs/run.log'

CHECKPOINTS = 'checkpoints'

SAMPLES = 'samples'

CACHE = 'cache'

WEIGHTS = 'weights'

def list_asset_files(assets_dir: Path | str) -> list[str]:
    root = Path(assets_dir)
    if not root.is_dir():
        return []
    files = [p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()]
    return sorted(files)

def ensure_assets(layout: ArtifactLayout) -> Path:
    layout.assets_dir.mkdir(parents=True, exist_ok=True)
    (layout.assets_dir / TRACKER).mkdir(parents=True, exist_ok=True)
    (layout.assets_dir / LOGS).mkdir(parents=True, exist_ok=True)
    (layout.assets_dir / CHECKPOINTS).mkdir(parents=True, exist_ok=True)
    return layout.assets_dir


def source_digest(source_dir: Path) -> str:
    """Hash actual source and small declared inputs, not Git labels or output copies."""
    source_dir = Path(source_dir).resolve()
    declaration = yaml.safe_load((source_dir / 'study.yaml').read_text(encoding='utf-8-sig'))
    library = Path(__file__).resolve().parents[1]
    members = [('rpipe/' + p.relative_to(library).as_posix(), p) for p in library.rglob('*.py')]
    members += [('study/' + p.relative_to(source_dir).as_posix(), p) for p in source_dir.rglob('*') if p.is_file() and p.suffix in {'.py', '.yaml', '.yml', '.json'} and (not any((part in {'__pycache__', '.tmp', 'runs', 'versions', 'shared', 'assets'} for part in p.relative_to(source_dir).parts)))]
    for raw in (declaration.get('provenance') or {}).get('include', []):
        path = (source_dir / str(raw)).resolve()
        if not path.is_relative_to(source_dir) or not path.is_file():
            raise AdmissionError(f'missing or escaping declared source input: {raw}')
        members.append(('input/' + str(raw), path))
    rows = []
    for name, path in sorted(set(members)):
        if not path.resolve().is_relative_to(library if name.startswith('rpipe/') else source_dir):
            raise AdmissionError(f'source symlink escapes root: {name}')
        rows.append((name, _digest(path)))
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()

kinds = SimpleNamespace(TRACKER=TRACKER, TRACKER_STATE=TRACKER_STATE, TRACKER_JSONL=TRACKER_JSONL, LOGS=LOGS, RUN_LOG=RUN_LOG, CHECKPOINTS=CHECKPOINTS, SAMPLES=SAMPLES, CACHE=CACHE, WEIGHTS=WEIGHTS)

