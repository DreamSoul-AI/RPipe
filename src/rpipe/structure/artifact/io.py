"""Owned execution files and immutable shared input preparation."""
from __future__ import annotations
from contextlib import closing
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath
import contextlib
import errno
import hashlib
import json
import math
import os
import re
import threading
import yaml
from dataclasses import dataclass
from typing import Any, TypedDict
from types import SimpleNamespace
from ..control import RunSpec, RunRef, AdmissionError, SourceMismatch, BusyError


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


def identity(run: RunSpec | RunRef) -> dict[str, str]:
    if isinstance(run, RunSpec):
        return run.ref.identity
    if isinstance(run, RunRef):
        return run.identity
    raise AdmissionError('identity requires an explicit Run specification or reference')




def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(_read_text(path))
    if not isinstance(value, dict):
        raise AdmissionError(f'expected a JSON object: {path.name}')
    return value


def write_json(path: Path, value: dict[str, Any]) -> Path:
    return atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def safe_error_record(exc: BaseException | dict[str, Any]) -> dict[str, Any]:
    """Persist failure types and bounded stack metadata, never exception/source text."""
    seen = set()

    def name(value, fallback='unknown'):
        return value if isinstance(value, str) and len(value) <= 160 and re.fullmatch(r'[A-Za-z_<>][A-Za-z0-9_.<>]*', value) else fallback

    def pack(value, depth=0):
        if id(value) in seen or len(seen) >= 16 or depth > 4:
            return None
        seen.add(id(value))
        frames, children, truncated = [], [], []
        if isinstance(value, BaseException):
            remote = vars(value).get('record')
            if isinstance(remote, dict):
                return pack(remote, depth)
            kind = name(type(value).__name__, 'Exception')
            exit_code = vars(value).get('exit_code')
            current = value.__traceback__
            while current is not None and len(frames) < 32:
                frames.append({'module': name(current.tb_frame.f_globals.get('__name__')),
                               'function': name(current.tb_frame.f_code.co_name),
                               'line': current.tb_lineno})
                current = current.tb_next
            if current is not None:
                truncated.append('traceback')
            cause = value.__cause__ or (None if value.__suppress_context__ else value.__context__)
            if cause is not None:
                children.append(cause)
            for key in ('exceptions', 'errors', 'cleanup_errors'):
                group = getattr(value, key, ())
                if isinstance(group, (list, tuple)):
                    children.extend(item for item in group if isinstance(item, BaseException))
        else:
            kind = name(value.get('type'), 'Exception')
            exit_code = value.get('exit_code')
            supplied = value.get('frames', [])
            if isinstance(supplied, list):
                for frame in supplied[:32]:
                    if (isinstance(frame, dict) and type(frame.get('line')) is int
                            and frame['line'] > 0):
                        frames.append({'module': name(frame.get('module')),
                                       'function': name(frame.get('function')), 'line': frame['line']})
                if len(supplied) > 32:
                    truncated.append('traceback')
            children = value.get('causes', [])
            children = [item for item in children if isinstance(item, dict)] if isinstance(children, list) else []
        causes = [record for child in children[:8] if (record := pack(child, depth + 1)) is not None]
        if len(causes) != len(children):
            truncated.append('causes')
        stack = 'Traceback (most recent call last):\n' if frames else ''
        stack += ''.join(f"  at {row['module']}.{row['function']}:{row['line']}\n" for row in frames)
        record = {'type': kind, 'message': '[redacted]', 'traceback': stack + kind + ': [redacted]',
                  'frames': frames, 'causes': causes, 'redacted_fields': ['message']}
        if type(exit_code) is int:
            record['exit_code'] = exit_code
        if truncated:
            record['truncated_fields'] = truncated
        return record

    if not isinstance(exc, (BaseException, dict)):
        exc = AdmissionError('invalid diagnostic record')
    return pack(exc) or {'type': 'Exception', 'message': '[redacted]', 'traceback': '[redacted]'}


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
    train = RunSpec(id=spec.train_id, seed=spec.seed, config=cfg, source_dir=spec.source_dir,
                    run_root=spec.run_root, source_digest=spec.source_digest, version=spec.version)
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

    def __init__(self, spec: RunSpec, *, attempt: str | None = None):
        self.spec = spec
        self.attempt = attempt
        self.path = spec.run_dir / '.execution'
        self.token = uuid.uuid4().hex
        self.acquired = False

    def acquire(self) -> None:
        validate_run_root(self.spec.source_dir, self.spec.run_root)
        validate_input_paths(self.spec)
        contained(self.spec.run_dir, self.spec.run_root)
        contained(self.path, self.spec.run_root)
        self.spec.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
        except FileExistsError as exc:
            raise BusyError(f'run busy or unresolved: {self.spec.id}') from exc
        self.acquired = True
        try:
            record = {'token': self.token, 'pid': os.getpid(), **identity(self.spec)}
            if self.attempt is not None:
                record['attempt'] = self.attempt
            write_json(self.path / 'owner.json', record)
        except BaseException as primary:
            try:
                # Only this newly created directory is ours. A partial owner record
                # is retained as busy rather than guessing that execution is free.
                self.path.rmdir()
                self.acquired = False
            except OSError as cleanup:
                _cleanup_note(primary, cleanup)
            raise

    def verify(self) -> None:
        if not self.acquired:
            raise BusyError('this execution does not own the Run')
        owner = read_json(self.path / 'owner.json')
        if (owner.get('token') != self.token
                or self.attempt is not None and owner.get('attempt') != self.attempt):
            raise BusyError('execution ownership changed during close')

    def release(self) -> None:
        if not self.acquired:
            return
        self.verify()
        directory = self.path.stat()
        record = (self.path / 'owner.json').read_bytes()
        (self.path / 'owner.json').unlink()
        try:
            self.path.rmdir()
        except BaseException as primary:
            try:
                current = self.path.stat()
                if (current.st_dev, current.st_ino) != (directory.st_dev, directory.st_ino):
                    raise BusyError('execution directory changed during release')
                # Restore only our unchanged directory, never replace a peer owner.
                with (self.path / 'owner.json').open('xb') as stream:
                    stream.write(record)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException as cleanup:
                _cleanup_note(primary, cleanup)
            raise
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




def _input_references(config: dict) -> dict:
    references = {}
    for component in ('data', 'model'):
        value = config.get(component, {}).get('path')
        if value is not None:
            references[component + '.path'] = value
    data = config.get('data', {})
    if 'stats_path' in data.get('config', {}):
        references['data.config.stats_path'] = data['config']['stats_path']
    elif 'stats_path' in data:
        references['data.stats_path'] = data['stats_path']
    return references


def _default_vision_references(spec: RunSpec, config: dict) -> dict:
    data = config.get('data', {})
    if (data.get('source') not in (None, 'torch')
            or data.get('name') not in {'MNIST', 'FashionMNIST', 'CIFAR10', 'CIFAR100', 'SVHN'}):
        return {}
    name = data['name'].lower()
    references = {}
    if data.get('path') is None:
        references['data.implicit_path'] = str(spec.run_root / 'shared' / 'data' / name)
    if 'stats_path' not in data.get('config', {}):
        references['data.implicit_stats_path'] = str(spec.study_root / 'shared' / 'data' / name / 'stats.yaml')
    return references


def validate_input_paths(spec: RunSpec, *, output_root: Path | str | None = None) -> None:
    """Keep mutable Study output away from input paths, allowing shared input siblings."""
    provenance = load_config(spec.source_dir / 'study.yaml').get('provenance') or {}
    if not isinstance(provenance, dict) or not isinstance(provenance.get('inputs', []), list):
        raise AdmissionError('provenance.inputs must be a list in a mapping')
    config = spec.to_config()
    references = _input_references(config)
    defaults = _default_vision_references(spec, config)
    if 'data.implicit_path' in defaults:
        # The raw cache also has canonical dataset aliases beside the backend root.
        references['data.implicit_cache_root'] = str(Path(defaults['data.implicit_path']).parent)
    if output_root is not None:
        references.update(defaults)
        if spec.result_path.is_file():
            result = read_json(contained(spec.result_path, spec.run_root))
            if result.get('identity') != identity(spec):
                raise SourceMismatch('recorded input references belong to a different Run identity')
            structure = result.get('structure')
            observed_data = structure.get('data') if isinstance(structure, dict) else None
            if isinstance(observed_data, dict) and observed_data.get('stats_reference') is not None:
                references['data.observed_stats_path'] = observed_data['stats_reference']
    inputs = [(value, not name.endswith('stats_path')) for name, value in references.items()]
    for entry in provenance.get('inputs', []):
        if not isinstance(entry, dict):
            raise AdmissionError('input pin must be a mapping')
        inputs.append((entry.get('path'), 'files' in entry))
    output = (spec.study_root if output_root is None else Path(output_root)).resolve()
    for value, directory in inputs:
        if not isinstance(value, str) or not value:
            raise AdmissionError('input reference paths must be nonempty strings')
        path = (spec.source_dir / value).resolve()
        if (path.is_relative_to(output)
                or directory and not path.is_file() and output.is_relative_to(path)):
            raise AdmissionError('mutable Study output must be separate from declared inputs')







kinds = SimpleNamespace(TRACKER=TRACKER, TRACKER_STATE=TRACKER_STATE, TRACKER_JSONL=TRACKER_JSONL, LOGS=LOGS, RUN_LOG=RUN_LOG, CHECKPOINTS=CHECKPOINTS, SAMPLES=SAMPLES, CACHE=CACHE, WEIGHTS=WEIGHTS)


def read_execution(spec: RunSpec | RunRef) -> tuple[dict, bool]:
    """Read only the explicitly indexed attempt, never UUID or modification order."""
    record = read_json(contained(spec.run_dir / 'execution.json', spec.run_root))
    validate_identity(record.get('identity'), expected=identity(spec))
    try:
        validate_execution_record(record, expected_identity=identity(spec))
    except AdmissionError:
        return record, False
    attempt = record.get('attempt')
    if (not isinstance(attempt, str) or len(attempt) != 32
            or any(char not in '0123456789abcdef' for char in attempt)):
        return record, False
    path = contained(spec.run_dir / 'attempts' / attempt / 'outcome.json', spec.run_root)
    try:
        outcome = read_json(path)
    except (OSError, ValueError, TypeError, RecursionError):
        return record, False
    return record, (json.dumps(outcome, sort_keys=True, allow_nan=False)
                    == json.dumps(record, sort_keys=True, allow_nan=False))



def record_inputs(ref: RunSpec | RunRef) -> dict:
    """Fingerprint the exact files and lock state consumed by the formal reader."""
    inputs = {'identity': identity(ref)}
    execution = None
    for name in ('config', 'result', 'execution'):
        suffix = 'yaml' if name == 'config' else 'json'
        path = contained(ref.run_dir / f'{name}.{suffix}', ref.run_root)
        raw = _read_bytes(path) if path.exists() else None
        inputs[f'{name}_sha256'] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name == 'execution':
            execution = raw
    inputs.update(attempt=None, outcome_sha256=None,
                  busy=contained(ref.run_dir / '.execution', ref.run_root).exists())
    if execution is not None:
        try:
            record = json.loads(execution)
            validate_execution_record(record, expected_identity=inputs['identity'])
        except (ValueError, TypeError, RecursionError):
            return inputs
        inputs['attempt'] = record['attempt']
        path = contained(ref.run_dir / 'attempts' / record['attempt'] / 'outcome.json', ref.run_root)
        inputs['outcome_sha256'] = _digest(path) if path.exists() else None
    return inputs


def validate_run_root(source_dir: Path | str, run_root: Path | str) -> None:
    """Protect the Study, installed library and their complete source projects."""
    output = Path(run_root).resolve()
    roots = set()
    for source in (Path(source_dir).resolve(), Path(__file__).resolve().parents[2]):
        roots.add(source)
        project_found = False
        for candidate in (source, *source.parents):
            if (candidate / '.git').exists():
                roots.add(candidate)
                break
            if not project_found and any((candidate / name).is_file()
                                         for name in ('pyproject.toml', 'setup.py', 'setup.cfg')):
                roots.add(candidate)
                project_found = True
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots):
        raise AdmissionError('run_root must be separate from source roots and their checkouts')


class RunRecord(TypedDict):
    id: str
    identity: dict[str, str]
    attempt: str
    status: str
    quality: bool | None
    evidence_complete: bool
    metrics: dict[str, Any]


class ExecutionRecord(TypedDict):
    schema: int
    identity: dict[str, str]
    attempt: str
    owner_token: str
    phase: str
    candidate_status: str | None
    quality: bool | None
    evidence_complete: bool
    scientific_result_published: bool
    derived_status: str
    error: dict[str, Any] | None
    cleanup_errors: list[dict[str, Any]]
    cancelled: bool


def _record_json(record: dict) -> None:
    if not isinstance(record, dict):
        raise AdmissionError('a saved record must be a JSON object')
    try:
        json.dumps(record, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise AdmissionError('record must contain finite JSON values') from error


def validate_identity(value: dict, *, expected: dict | None = None) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {'id', 'source_digest', 'config_digest', 'version'}:
        raise AdmissionError('record identity must contain its four explicit fields')
    if any(not isinstance(value[name], str) or not value[name] for name in ('id', 'version')):
        raise AdmissionError('record identity requires an id and version')
    for name in ('source_digest', 'config_digest'):
        if not isinstance(value.get(name), str) or re.fullmatch(r'[0-9a-f]{64}', value[name]) is None:
            raise AdmissionError(f'record {name} must be a SHA-256 digest')
    if expected is not None and value != expected:
        raise SourceMismatch('record belongs to a different Run identity')
    return value


def _attempt(value, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{32}', value) is None:
        raise AdmissionError(f'{name} must be an explicit attempt identifier')


def validate_run_record(record: dict, *, expected_identity: dict | None = None,
                        attempt: str | None = None) -> RunRecord:
    """Validate the actual scientific record shape without accessing storage."""
    _record_json(record)
    required = {'id', 'identity', 'attempt', 'status', 'quality', 'evidence_complete', 'metrics'}
    if not required.issubset(record):
        raise AdmissionError('scientific record is missing required metadata')
    observed = validate_identity(record['identity'], expected=expected_identity)
    _attempt(record['attempt'], 'scientific attempt')
    if record['id'] != observed['id'] or attempt is not None and record['attempt'] != attempt:
        raise SourceMismatch('scientific record belongs to a different Run or attempt')
    if (record['status'] != 'succeeded' or type(record['evidence_complete']) is not bool
            or record['quality'] is not None and type(record['quality']) is not bool
            or not isinstance(record['metrics'], dict)):
        raise AdmissionError('scientific record has invalid observations')
    return record


def validate_execution_record(record: dict, *, expected_identity: dict | None = None) -> ExecutionRecord:
    """Validate execution facts; the lifecycle remains their decision maker."""
    _record_json(record)
    required = set(ExecutionRecord.__required_keys__)
    if not required.issubset(record):
        raise AdmissionError('execution record is missing required metadata')
    validate_identity(record['identity'], expected=expected_identity)
    _attempt(record['attempt'], 'execution attempt')
    _attempt(record['owner_token'], 'execution owner token')
    if (type(record['schema']) is not int or record['schema'] != 1
            or not isinstance(record['phase'], str) or record['phase'] not in {'running', 'closing'}
            or any(type(record[name]) is not bool for name in
                   ('evidence_complete', 'scientific_result_published', 'cancelled'))
            or record['quality'] is not None and type(record['quality']) is not bool
            or not isinstance(record['cleanup_errors'], list)
            or any(not isinstance(error, dict) for error in record['cleanup_errors'])
            or record['error'] is not None and not isinstance(record['error'], dict)
            or not isinstance(record['derived_status'], str)
            or record['derived_status'] not in {'not_run', 'running', 'succeeded', 'failed', 'cancelled', 'unknown'}):
        raise AdmissionError('execution record has invalid observations')
    status = record['candidate_status']
    if record['phase'] == 'running':
        if status is not None or record['scientific_result_published'] or record['evidence_complete']:
            raise AdmissionError('running execution cannot be a closed scientific publication')
    elif not isinstance(status, str) or status not in {'succeeded', 'failed', 'cancelled', 'unknown'}:
        raise AdmissionError('closing execution requires a terminal candidate')
    if status == 'succeeded' and (not record['scientific_result_published'] or record['error']
                                  or record['cleanup_errors'] or record['cancelled']
                                  or record['derived_status'] not in {'not_run', 'succeeded'}):
        raise AdmissionError('successful execution contradicts its saved facts')
    return record


def project_execution(record: dict, result: dict, *, busy: bool, complete: bool = True) -> dict:
    """Project one indexed attempt using the same constraints as the writer."""
    status = record.get('candidate_status')
    try:
        validate_execution_record(record)
        valid = record['phase'] == 'closing'
        if record['scientific_result_published']:
            validate_run_record(result, expected_identity=record['identity'], attempt=record['attempt'])
            valid = valid and result['quality'] == record['quality']
        elif result:
            valid = False
    except (ValueError, TypeError):
        valid = False
    observed = record.get('identity')
    return {**result, 'id': observed.get('id') if isinstance(observed, dict) else None,
            'identity': record.get('identity'), 'attempt': record.get('attempt'),
            'status': status if valid and complete and not busy else 'unknown',
            'quality': record.get('quality') if valid else None,
            'evidence_complete': bool(valid and complete and not busy and status == 'succeeded'
                                      and record['evidence_complete'] and result.get('evidence_complete')),
            'scientific_status': result.get('status', 'missing'),
            'derived_status': record.get('derived_status', 'not_run'),
            'input_provenance': record.get('input_provenance'), 'environment': record.get('environment'),
            'error': record.get('error'), 'cleanup_errors': record.get('cleanup_errors', []),
            'cancelled': record.get('cancelled', False), 'busy': busy,
            **({'metadata_status': 'incomplete' if record.get('phase') == 'running' else 'invalid'}
               if not valid or not complete else {})}
