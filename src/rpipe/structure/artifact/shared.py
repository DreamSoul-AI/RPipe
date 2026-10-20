"""Project input roots, interprocess preparation lock, and explicit cache migration."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

import yaml

from rpipe.structure.artifact._atomic import _owned_stream, _read_text

_held = threading.local()


def _root_identity(root: Path) -> tuple[int, int]:
    identity = root.stat()
    return identity.st_dev, identity.st_ino


def shared_paths(study_dir: Path | str) -> dict[str, Path]:
    study = Path(study_dir).resolve()
    declaration = study / 'study.yaml'
    body = yaml.safe_load(_read_text(declaration)) if declaration.is_file() else None
    if body is not None and not isinstance(body, dict):
        raise ValueError('study.yaml must be a mapping')
    block = (body or {}).get('shared', {})
    if not isinstance(block, dict) or set(block) - {'root', 'data', 'model'}:
        raise ValueError('shared must be a mapping with root, data, model')
    raw = block.get('root')
    if raw is not None and (not isinstance(raw, str) or not raw.strip()):
        raise ValueError('shared.root must be a nonempty path')
    parent = study.parent.parent if study.parent.name == 'studies' else study.parent
    root = (study / raw).resolve() if raw is not None else parent / 'shared'
    out = {'root': root}
    for kind in ('data', 'model'):
        scope = block.get(kind, 'project')
        if scope not in ('project', 'study'):
            raise ValueError(f'shared.{kind} must be project or study')
        out[kind] = (study / 'shared' if scope == 'study' else root) / kind
    return out


@contextlib.contextmanager
def cache_lock(root: Path | str, *, timeout: float = 120, reentrant: bool = True):
    """OS-owned lock; optionally refuse same-thread reentry as well as peers."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError('cache lock timeout must be finite and >= 0')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = (os.getpid(), *_root_identity(root))
    held = getattr(_held, 'roots', {})
    if key in held:
        if not reentrant:
            raise TimeoutError(f'shared input preparation locked: {root}')
        yield
        return
    with contextlib.ExitStack() as stack:
        handle = stack.enter_context(_owned_stream(contextlib.closing(
            (root / '.rpipe.lock').open('a+b'))))
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
    source, target = Path(source), Path(target)
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
        # Symlinks allow cross-volume references; Windows falls back to a hard link.
        try:
            temporary.symlink_to(source.resolve())
        except OSError:
            os.link(source, temporary)
        owned = True
        if temporary != target:
            from rpipe.structure.artifact._atomic import atomic_replace
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
    source, target = Path(source), Path(target)
    files = [path for path in sorted(source.rglob('*')) if path.is_file()
             and path.name not in ('stats.yaml', '.ready', '.rpipe.lock')]
    for path in files:
        destination = target / path.relative_to(source)
        if destination.exists() and not destination.samefile(path) and _digest(destination) != _digest(path):
            raise ValueError(f'conflicting raw input: {destination}')
    for path in files:
        link_input(path, target / path.relative_to(source))


def prepared_resource(root: Path | str, definition: dict, files: list[str], build):
    """Build immutable inputs once, publish atomically, validate every reuse.

    The caller supplies version/format identity and a preparation callback, not
    training. Interrupted staging is retained; no ready marker accepts it.
    """
    names = [Path(name) for name in files]
    if (not names or len(set(names)) != len(names) or any(
            name.anchor or not name.name or '..' in name.parts or name == Path('manifest.json')
            for name in names)):
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
            if not all(path.is_file() for path in paths):
                raise RuntimeError(f'incomplete inputs retained: {staging}')
            from rpipe.structure.artifact._atomic import atomic_write_text
            atomic_write_text(staging / 'manifest.json', json.dumps(
                {'definition': definition, 'files': {name: _digest(staging / name) for name in files}},
                sort_keys=True, indent=2))
            staging.rename(cache)
        manifest = json.loads(_read_text(cache / 'manifest.json'))
        recorded = manifest.get('files')
        if (manifest.get('definition') != definition or not isinstance(recorded, dict)
                or set(recorded) != set(files)
                or any(_digest(cache / name) != digest for name, digest in recorded.items())):
            raise ValueError(f'input cache differs; preserved for diagnosis: {cache}')
        return cache, _digest(cache / 'manifest.json')


def migrate_shared(study_dir: Path | str) -> dict[str, object]:
    """Validate all collisions first, then share bytes and preserve old file paths.

    Same-volume hard links only. Different bytes/formats must be given distinct
    resource names or explicitly kept private. No Run/provenance is rewritten.
    """
    study = Path(study_dir).resolve()
    roots = shared_paths(study)
    rows = []
    with contextlib.ExitStack() as stack:
        lock_roots = {roots['data'], roots['model'], study / 'shared' / 'data',
                      study / 'shared' / 'model'}
        for root in lock_roots:
            root.mkdir(parents=True, exist_ok=True)
        for root in sorted(lock_roots, key=_root_identity):
            stack.enter_context(cache_lock(root))
        for kind in ('data', 'model'):
            old, new = study / 'shared' / kind, roots[kind]
            if old.resolve() == new.resolve():
                continue
            if old in new.parents or new in old.parents:
                raise ValueError('shared migration roots must not contain each other')
            for source in sorted(old.rglob('*')):
                if not source.is_file() or source.name in ('.rpipe.lock', 'stats.yaml'):
                    continue
                if source.is_symlink():
                    raise ValueError(f'migrate explicit input references separately: {source}')
                target = new / source.relative_to(old)
                digest = _digest(source)
                if target.exists() and (not target.is_file() or _digest(target) != digest):
                    raise ValueError(f'conflicting shared input; nothing migrated: {target}')
                if source.stat().st_dev != new.stat().st_dev:
                    raise ValueError('migration requires the same volume; use shared.root or explicit references')
                rows.append((source, target, digest))
        for source, target, digest in rows:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                os.link(source, target)
            elif not source.samefile(target):
                # Prepare the reference before replacing the duplicate. A failure
                # leaves the old source intact, and successful replacement is atomic.
                tmp = source.with_name(f'{uuid.uuid4().hex}.link')
                owned = False
                try:
                    os.link(target, tmp)
                    owned = True
                    from rpipe.structure.artifact._atomic import atomic_replace
                    atomic_replace(tmp, source)
                finally:
                    if owned:
                        try:
                            tmp.unlink(missing_ok=True)
                        except OSError:
                            pass
        return {'study': str(study), 'paths': {k: str(v) for k, v in roots.items()},
                'files': [{'old': str(s), 'shared': str(t), 'sha256': d,
                           'same_file': s.samefile(t)} for s, t, d in rows]}
