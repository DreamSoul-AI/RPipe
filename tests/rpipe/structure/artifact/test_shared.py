from pathlib import Path
import os
import subprocess
import sys

import pytest

from rpipe.structure.artifact.layout import artifact_layout
from rpipe.structure.artifact.shared import cache_lock, link_input, migrate_shared, shared_paths

from rpipe.structure.artifact.shared import prepared_resource

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_nonreentrant_lock_refuses_nested_owner_without_releasing_outer(tmp_path):
    with cache_lock(tmp_path, timeout=0):
        with pytest.raises(TimeoutError):
            with cache_lock(tmp_path / '.', timeout=0, reentrant=False):
                pytest.fail('nonreentrant acquisition entered')
        with cache_lock(tmp_path, timeout=0):
            pass  # Existing shared-cache default remains reentrant.
    with cache_lock(tmp_path, timeout=0, reentrant=False):
        pass



def test_studies_share_inputs_but_not_run_outputs(tmp_path):
    a = artifact_layout(tmp_path / 'studies' / 'a', 'same-id')
    b = artifact_layout(tmp_path / 'studies' / 'b', 'same-id')
    assert a.shared_data_dir == b.shared_data_dir == tmp_path / 'shared' / 'data'
    assert a.shared_model_dir == b.shared_model_dir == tmp_path / 'shared' / 'model'
    assert a.assets_dir != b.assets_dir


def test_private_data_does_not_disable_shared_model(tmp_path):
    study = tmp_path / 'studies' / 'private'
    study.mkdir(parents=True)
    (study / 'study.yaml').write_text('shared: {data: study}\n')
    paths = shared_paths(study)
    assert paths['data'] == study / 'shared' / 'data'
    assert paths['model'] == tmp_path / 'shared' / 'model'


def test_explicit_root_is_resolved_relative_to_study(tmp_path):
    study = tmp_path / 'experiments' / 'a'
    study.mkdir(parents=True)
    (study / 'study.yaml').write_text('shared: {root: ../../input-cache}\n')
    assert shared_paths(study)['model'] == tmp_path / 'input-cache' / 'model'


@pytest.mark.parametrize('block', ['null', '[]', '{root: ""}', '{data: false}', '{model: local}', '{typo: project}'])
def test_bad_shared_config_creates_no_layout(tmp_path, block):
    study = tmp_path / 'studies' / 'a'
    study.mkdir(parents=True)
    (study / 'study.yaml').write_text(f'shared: {block}\n')
    with pytest.raises(ValueError):
        artifact_layout(study, 'r')
    assert not (study / 'runs').exists()
    assert not (tmp_path / 'shared').exists()


def test_migration_deduplicates_identical_bytes_and_keeps_profiles_private(tmp_path):
    studies = [tmp_path / 'studies' / name for name in ('a', 'b')]
    for study in studies:
        for kind in ('data', 'model'):
            folder = study / 'shared' / kind
            folder.mkdir(parents=True)
            (folder / 'input.bin').write_bytes(b'one immutable input')
        (study / 'shared' / 'data' / 'stats.yaml').write_text('private stats')
    for study in studies:
        report = migrate_shared(study)
        assert all(row['same_file'] for row in report['files'])
    for kind in ('data', 'model'):
        shared = tmp_path / 'shared' / kind / 'input.bin'
        assert all((study / 'shared' / kind / 'input.bin').samefile(shared) for study in studies)
        assert shared.read_bytes() == b'one immutable input'
    assert not (tmp_path / 'shared' / 'data' / 'stats.yaml').exists()
    assert migrate_shared(studies[0])['files']  # idempotent, preserved references


def test_migration_checks_all_conflicts_before_linking(tmp_path):
    study = tmp_path / 'studies' / 'a'
    data = study / 'shared' / 'data'
    model = study / 'shared' / 'model'
    data.mkdir(parents=True)
    model.mkdir(parents=True)
    (data / 'raw.bin').write_bytes(b'data')
    (model / 'weights.bin').write_bytes(b'old')
    target = tmp_path / 'shared' / 'model'
    target.mkdir(parents=True)
    (target / 'weights.bin').write_bytes(b'different revision')
    with pytest.raises(ValueError, match='conflicting'):
        migrate_shared(study)
    assert not (tmp_path / 'shared' / 'data' / 'raw.bin').exists()
    assert (model / 'weights.bin').read_bytes() == b'old'
    assert (target / 'weights.bin').read_bytes() == b'different revision'


def test_input_reference_never_copies_or_overwrites(tmp_path):
    source = tmp_path / 'raw'
    source.write_bytes(b'official raw')
    target = link_input(source, tmp_path / 'run' / 'raw')
    assert target.samefile(source)
    assert link_input(source, target) == target
    unrelated = tmp_path / 'other'
    unrelated.write_bytes(b'other')
    with pytest.raises(FileExistsError):
        link_input(unrelated, target)
    assert source.read_bytes() == b'official raw'


def test_identical_raw_duplicates_become_one_file(tmp_path):
    from rpipe.structure.artifact.shared import reference_tree
    source, target = tmp_path / 'raw', tmp_path / 'backend'
    source.mkdir()
    target.mkdir()
    (source / 'input.bin').write_bytes(b'official')
    (target / 'input.bin').write_bytes(b'official')
    (source / 'stats.yaml').write_text('must not be a shared raw input')
    reference_tree(source, target)
    assert (target / 'input.bin').samefile(source / 'input.bin')
    assert not (target / 'stats.yaml').exists()


def test_failed_reference_creation_keeps_duplicate_payload(tmp_path, monkeypatch):
    source, target = tmp_path / 'raw', tmp_path / 'duplicate'
    source.write_bytes(b'complete')
    target.write_bytes(b'complete')
    def refuse(*args, **kwargs):
        raise OSError('injected reference failure')
    monkeypatch.setattr(Path, 'symlink_to', refuse)
    monkeypatch.setattr(os, 'link', refuse)
    with pytest.raises(OSError, match='injected'):
        link_input(source, target)
    assert target.read_bytes() == b'complete'
    assert source.read_bytes() == b'complete'
    assert not list(tmp_path.glob('*.link'))


def test_interprocess_prepare_has_one_writer_and_recovers_after_exit(tmp_path):
    script = '''
from pathlib import Path
import sys, time
from rpipe.structure.artifact.shared import cache_lock
root = Path(sys.argv[1])
with cache_lock(root, timeout=20):
    target = root / 'download.bin'
    if not target.exists():
        with (root / 'writers.txt').open('a') as log:
            log.write('writer\\n')
        time.sleep(.15)
        target.write_bytes(b'complete')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[4] / 'src'))
    processes = [subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, (stdout, stderr)
    assert (tmp_path / 'writers.txt').read_text() == 'writer\n'
    with cache_lock(tmp_path, timeout=0):
        assert (tmp_path / 'download.bin').read_bytes() == b'complete'


def test_terminated_writer_does_not_leave_an_owned_lock(tmp_path):
    script = '''
import sys, time
from rpipe.structure.artifact.shared import cache_lock
with cache_lock(sys.argv[1]):
    print('locked', flush=True)
    time.sleep(30)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[4] / 'src'))
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b'locked'
        with pytest.raises(TimeoutError):
            with cache_lock(tmp_path, timeout=0):
                pass
    finally:
        process.kill()
        process.communicate(timeout=10)
    with cache_lock(tmp_path, timeout=0):
        assert (tmp_path / '.rpipe.lock').is_file()


def test_same_definition_reuses_input_and_changed_revision_builds_separately(tmp_path):
    built = []
    def build(staging):
        built.append(staging)
        (staging / 'weights').write_bytes(b'immutable')
    first, digest = prepared_resource(tmp_path, {'revision': 'one'}, ['weights'], build)
    second, same_digest = prepared_resource(tmp_path, {'revision': 'one'}, ['weights'], build)
    other, _ = prepared_resource(tmp_path, {'revision': 'two'}, ['weights'], build)
    assert first == second and digest == same_digest
    assert other != first and len(built) == 2


def test_partial_build_is_retained_without_publishing_ready(tmp_path):
    def fail(staging):
        (staging / 'data').write_bytes(b'partial')
        raise OSError('injected prepare failure')
    with pytest.raises(OSError, match='injected'):
        prepared_resource(tmp_path, {'revision': 'one'}, ['data'], fail)
    assert not list(tmp_path.glob('*/inputs'))
    assert len(list(tmp_path.glob('*/.building-*/data'))) == 1


def test_corrupt_payload_and_incomplete_manifest_are_not_rebuilt_or_accepted(tmp_path):
    import json
    def build(staging):
        (staging / 'data').write_bytes(b'complete')
    cache, _ = prepared_resource(tmp_path, {}, ['data'], build)
    manifest = cache / 'manifest.json'
    original = manifest.read_bytes()
    (cache / 'data').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='cache differs'):
        prepared_resource(tmp_path, {}, ['data'], build)
    assert (cache / 'data').read_bytes() == b'corrupt'
    (cache / 'data').write_bytes(b'complete')
    body = json.loads(original)
    body['files'] = {}
    manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError, match='cache differs'):
        prepared_resource(tmp_path, {}, ['data'], build)


@pytest.mark.parametrize('files', [[], ['../outside'], ['manifest.json'], ['data', 'data'],
                                 ['./manifest.json'], ['data', './data'], [''], ['.']])
def test_invalid_resource_contract_is_rejected_before_writes(tmp_path, files):
    with pytest.raises(ValueError, match='relative'):
        prepared_resource(tmp_path, {}, files, lambda staging: None)
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), float('-inf'), -1.0])
def test_invalid_cache_lock_budget_is_rejected_before_creating_root(tmp_path, timeout):
    root = tmp_path / 'invalid-budget'
    with pytest.raises(ValueError, match='timeout'):
        with cache_lock(root, timeout=timeout):
            pass
    assert not root.exists()


def test_nan_cache_lock_budget_cannot_disable_contention_deadline(tmp_path, monkeypatch):
    """A bounded busy-lock fixture exposes the old NaN loop without hanging pytest."""
    import errno
    import time
    from types import SimpleNamespace
    import rpipe.structure.artifact.shared as shared

    sleeps = []
    def busy(*args):
        raise BlockingIOError(errno.EACCES, 'fixture lock held by another writer')
    def watchdog(delay):
        sleeps.append(delay)
        if len(sleeps) == 3:
            raise RuntimeError('bounded watchdog: NaN disabled the contention deadline')
    if os.name == 'nt':
        import msvcrt
        monkeypatch.setattr(msvcrt, 'locking', busy)
    else:
        import fcntl
        monkeypatch.setattr(fcntl, 'flock', busy)
    monkeypatch.setattr(shared, 'time', SimpleNamespace(monotonic=time.monotonic, sleep=watchdog))
    root = tmp_path / 'nan-budget'
    with pytest.raises(ValueError, match='timeout'):
        with cache_lock(root, timeout=float('nan')):
            pytest.fail('invalid budget acquired a lock')
    assert sleeps == [] and not root.exists()


def test_interrupted_resource_retry_preserves_failed_staging_and_publishes_fresh(tmp_path):
    def interrupted(staging):
        (staging / 'data').write_bytes(b'partial')
        raise OSError('fixture interrupted preparation')
    with pytest.raises(OSError, match='interrupted'):
        prepared_resource(tmp_path, {'revision': 'retry'}, ['data'], interrupted)
    failed = list(tmp_path.glob('*/.building-*/data'))
    assert len(failed) == 1 and failed[0].read_bytes() == b'partial'

    def complete(staging):
        (staging / 'data').write_bytes(b'complete')
    cache, digest = prepared_resource(tmp_path, {'revision': 'retry'}, ['data'], complete)
    assert (cache / 'data').read_bytes() == b'complete'
    assert failed[0].read_bytes() == b'partial'
    def no_rebuild(staging):
        pytest.fail('published inputs must be reused')
    assert prepared_resource(tmp_path, {'revision': 'retry'}, ['data'], no_rebuild) == (cache, digest)



def test_zero_wait_busy_lock_preserves_cause_and_releases_handle(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace
    import rpipe.structure.artifact.shared as shared

    error = BlockingIOError(errno.EACCES, 'fixture occupied lock')
    def busy(*args):
        raise error
    def no_sleep(delay):
        pytest.fail('zero budget must not sleep')
    with monkeypatch.context() as patch:
        if os.name == 'nt':
            import msvcrt
            patch.setattr(msvcrt, 'locking', busy)
        else:
            import fcntl
            patch.setattr(fcntl, 'flock', busy)
        patch.setattr(shared, 'time', SimpleNamespace(monotonic=lambda: 100, sleep=no_sleep))
        with pytest.raises(TimeoutError, match='shared input preparation locked') as caught:
            with cache_lock(tmp_path, timeout=0):
                pytest.fail('occupied lock was acquired')
        assert caught.value.__cause__ is error
    with cache_lock(tmp_path, timeout=0):
        pass


def test_non_contention_lock_error_is_not_retried_or_wrapped(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace
    import rpipe.structure.artifact.shared as shared

    error = OSError(errno.EBADF, 'fixture invalid descriptor')
    calls = []
    def broken(*args):
        calls.append(args)
        raise error
    def no_sleep(delay):
        pytest.fail('descriptor errors must not retry')
    with monkeypatch.context() as patch:
        if os.name == 'nt':
            import msvcrt
            patch.setattr(msvcrt, 'locking', broken)
        else:
            import fcntl
            patch.setattr(fcntl, 'flock', broken)
        patch.setattr(shared, 'time', SimpleNamespace(monotonic=lambda: 100, sleep=no_sleep))
        with pytest.raises(OSError) as caught:
            with cache_lock(tmp_path):
                pytest.fail('broken descriptor was accepted')
        assert caught.value is error and len(calls) == 1
    with cache_lock(tmp_path, timeout=0):
        pass


def test_nested_lock_exception_keeps_outer_owner_then_releases_for_other_thread(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def acquire():
        with cache_lock(tmp_path, timeout=0):
            return 'acquired'
    with ThreadPoolExecutor(max_workers=1) as worker:
        with pytest.raises(RuntimeError, match='outer interrupted'):
            with cache_lock(tmp_path, timeout=0):
                with pytest.raises(ValueError, match='inner interrupted'):
                    with cache_lock(tmp_path / '.', timeout=0):
                        raise ValueError('inner interrupted')
                with pytest.raises(TimeoutError):
                    worker.submit(acquire).result(timeout=10)
                raise RuntimeError('outer interrupted')
        assert worker.submit(acquire).result(timeout=10) == 'acquired'



def test_finite_lock_deadline_has_bounded_retry_timeline(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace
    import rpipe.structure.artifact.shared as shared

    clock = iter([0.0, 0.0, 0.05, 0.1])
    attempts, sleeps = [], []
    error = BlockingIOError(errno.EAGAIN, 'fixture contention')
    def busy(*args):
        attempts.append(args)
        raise error
    with monkeypatch.context() as patch:
        if os.name == 'nt':
            import msvcrt
            patch.setattr(msvcrt, 'locking', busy)
        else:
            import fcntl
            patch.setattr(fcntl, 'flock', busy)
        patch.setattr(shared, 'time', SimpleNamespace(monotonic=lambda: next(clock), sleep=sleeps.append))
        with pytest.raises(TimeoutError) as caught:
            with cache_lock(tmp_path, timeout=0.1):
                pytest.fail('held lock was acquired')
        assert caught.value.__cause__ is error
        assert len(attempts) == 3 and sleeps == [0.05, 0.05]
    with cache_lock(tmp_path, timeout=0):
        pass


def test_cancelled_wait_cleans_handle_before_next_acquisition(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace
    import rpipe.structure.artifact.shared as shared

    cancelled = KeyboardInterrupt('fixture cancelled wait')
    attempts = []
    def busy(*args):
        attempts.append(args)
        raise BlockingIOError(errno.EACCES, 'fixture contention')
    def cancel(delay):
        assert delay == 0.05
        raise cancelled
    with monkeypatch.context() as patch:
        if os.name == 'nt':
            import msvcrt
            patch.setattr(msvcrt, 'locking', busy)
        else:
            import fcntl
            patch.setattr(fcntl, 'flock', busy)
        patch.setattr(shared, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=cancel))
        with pytest.raises(KeyboardInterrupt) as caught:
            with cache_lock(tmp_path):
                pytest.fail('cancelled wait entered critical section')
        assert caught.value is cancelled and len(attempts) == 1
    with cache_lock(tmp_path, timeout=0):
        pass


def test_repeated_failed_acquisitions_do_not_release_other_thread_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def acquire():
        with cache_lock(tmp_path, timeout=0):
            return 'acquired'
    with ThreadPoolExecutor(max_workers=1) as worker:
        with pytest.raises(KeyboardInterrupt, match='owner cancelled'):
            with cache_lock(tmp_path, timeout=0):
                for _ in range(3):
                    with pytest.raises(TimeoutError):
                        worker.submit(acquire).result(timeout=10)
                raise KeyboardInterrupt('owner cancelled')
        assert worker.submit(acquire).result(timeout=10) == 'acquired'



@pytest.mark.skipif(os.name != 'nt', reason='requires Windows Path equivalence and drive semantics')
@pytest.mark.parametrize('files', [['MANIFEST.JSON'], ['DATA', 'data'], ['C:outside'], ['\\outside']])
def test_windows_resource_aliases_rejected_before_builder_or_layout(tmp_path, files):
    def no_build(staging):
        pytest.fail('invalid contract called builder')
    root = tmp_path / 'never-created'
    with pytest.raises(ValueError, match='relative'):
        prepared_resource(root, {}, files, no_build)
    assert not root.exists()


def test_valid_resource_names_keep_original_manifest_keys_and_reuse(tmp_path):
    import json
    files = ['./weights', 'nested/manifest.json']
    def build(staging):
        for name in files:
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'complete')
    cache, digest = prepared_resource(tmp_path, {}, files, build)
    manifest = cache / 'manifest.json'
    original = manifest.read_bytes()
    assert set(json.loads(original)['files']) == set(files)
    def no_rebuild(staging):
        pytest.fail('valid inputs must be reused')
    assert prepared_resource(tmp_path, {}, files, no_rebuild) == (cache, digest)
    assert manifest.read_bytes() == original
    assert all((cache / name).read_bytes() == b'complete' for name in files)


@pytest.fixture(params=['reference-default', 'reference-hardlink', 'migration-data', 'migration-model'])
def shared_publication_case(tmp_path, monkeypatch, request):
    route = request.param
    if route.startswith('reference'):
        source, target = tmp_path / 'source.bin', tmp_path / 'target.bin'
        if route == 'reference-hardlink':
            def no_symlink(*args, **kwargs):
                raise OSError('fixture symlink privilege unavailable')
            monkeypatch.setattr(Path, 'symlink_to', no_symlink)
        publish_target = target
        def invoke():
            return link_input(source, target)
    else:
        study = tmp_path / 'studies' / 'sample'
        kind = route.split('-')[1]
        source, target = study / 'shared' / kind / 'input.bin', tmp_path / 'shared' / kind / 'input.bin'
        source.parent.mkdir(parents=True)
        target.parent.mkdir(parents=True)
        publish_target = source
        def invoke():
            return migrate_shared(study)
    source.write_bytes(b'immutable input')
    target.write_bytes(b'immutable input')
    return source, target, publish_target, invoke


@pytest.mark.parametrize('cleanup', ['failed-permission', 'failed-io', 'committed-io', 'program-error'])
def test_shared_owned_cleanup_preserves_publication_outcome(tmp_path, monkeypatch, shared_publication_case, cleanup):
    from rpipe.structure.artifact import _atomic

    source, target, destination, invoke = shared_publication_case
    peer = tmp_path / 'peer.link'
    peer.write_bytes(b'other writer evidence')
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (source, target, peer)}
    original = OSError('original publication refusal')
    cleanup_error = (RuntimeError('cleanup program defect') if cleanup == 'program-error' else
                     PermissionError('cleanup permission denied') if cleanup == 'failed-permission' else
                     OSError('cleanup IO refused'))
    stages, removed = [], []
    publish, unlink = _atomic.atomic_replace, Path.unlink
    def attempt(stage, final):
        stages.append(stage)
        assert final == destination
        if cleanup == 'committed-io':
            return publish(stage, final)
        raise original
    def remove(path, *args, **kwargs):
        if path.suffix == '.link':
            assert path in stages and path != peer
            removed.append(path)
            raise cleanup_error
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(_atomic, 'atomic_replace', attempt)
    monkeypatch.setattr(Path, 'unlink', remove)
    if cleanup == 'committed-io':
        result = invoke()
        assert result and source.samefile(target)
    else:
        with pytest.raises(type(cleanup_error) if cleanup == 'program-error' else OSError) as caught:
            invoke()
        assert caught.value is (cleanup_error if cleanup == 'program-error' else original)
    assert len(stages) == len(removed) == 1
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in before} == before


@pytest.mark.parametrize('readonly', [False, True], ids=['native-writable', 'native-readonly'])
def test_shared_native_readonly_cleanup_keeps_original_replace_error(tmp_path, monkeypatch, shared_publication_case, readonly):
    from rpipe.structure.artifact import _atomic

    if readonly and os.name != 'nt':
        pytest.skip('Windows read-only hardlink replacement/unlink semantics')
    source, target, destination, invoke = shared_publication_case
    if readonly:
        source.chmod(0o444)
        target.chmod(0o444)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (source, target)}
    monkeypatch.setattr(_atomic.time, 'sleep', lambda _: None)
    if readonly:
        with pytest.raises(PermissionError) as caught:
            invoke()
        assert caught.value.filename2 is not None
        assert Path(caught.value.filename2).resolve() == destination.resolve()
    else:
        assert invoke() and source.samefile(target)
        assert not list(tmp_path.rglob('*.link'))
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in before} == before


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows extended paths')
@pytest.mark.parametrize('extended_first', [False, True])
@pytest.mark.parametrize('interrupted', [False, True])
def test_cache_lock_extended_alias_reentry_keeps_thread_ownership(tmp_path, extended_first, interrupted):
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path.resolve()
    alias = Path('\\\\?\\' + str(root))
    assert root.samefile(alias)
    first, second = (alias, root) if extended_first else (root, alias)

    def acquire():
        with cache_lock(root, timeout=0):
            return 'acquired'

    with ThreadPoolExecutor(max_workers=1) as worker:
        with cache_lock(first, timeout=0):
            if interrupted:
                with pytest.raises(ValueError, match='inner interrupted'):
                    with cache_lock(second, timeout=0):
                        raise ValueError('inner interrupted')
            else:
                with cache_lock(second, timeout=0):
                    pass
            with pytest.raises(TimeoutError):
                worker.submit(acquire).result(timeout=10)
        assert worker.submit(acquire).result(timeout=10) == 'acquired'


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows extended paths')
@pytest.mark.parametrize('shared_name', ['aaa', 'zzz'])
@pytest.mark.parametrize('extended_shared', [False, True])
def test_migration_lock_order_is_independent_of_path_namespace(tmp_path, monkeypatch, shared_name, extended_shared):
    import contextlib
    import json
    import rpipe.structure.artifact.shared as shared

    study = tmp_path / 'studies' / 'sample'
    study.mkdir(parents=True)
    resource = tmp_path / shared_name
    extended = lambda path: Path('\\\\?\\' + str(path.resolve()))
    declared = extended(resource) if extended_shared else resource
    (study / 'study.yaml').write_text(
        'shared:\n  root: ' + json.dumps(str(declared)) + '\n', encoding='utf-8')
    original = shared.cache_lock
    orders = []
    for spelling in (study, extended(study)):
        order = []
        @contextlib.contextmanager
        def observe(root):
            with original(root, timeout=0):
                identity = Path(root).stat()
                order.append((identity.st_dev, identity.st_ino))
                yield
        with monkeypatch.context() as patch:
            patch.setattr(shared, 'cache_lock', observe)
            assert shared.migrate_shared(spelling)['files'] == []
        assert len(order) == 4 and len(set(order)) == 4
        orders.append(order)
    assert orders[0] == orders[1]


def _lock_cleanup_faults(patch, *, unlock_error=None, close_error=None, acquire_error=None):
    if os.name == 'nt':
        import msvcrt as backend
        method, unlock = 'locking', backend.LK_UNLCK
    else:
        import fcntl as backend
        method, unlock = 'flock', backend.LOCK_UN
    native = getattr(backend, method)
    calls, handles = [], []
    def locking(*args):
        releasing = args[1] == unlock
        calls.append('unlock' if releasing else 'acquire')
        error = unlock_error if releasing else acquire_error
        if error is not None:
            raise error
        return native(*args)
    original_open = Path.open
    class Stream:
        def __init__(self, raw):
            self.raw = raw
        def __getattr__(self, name):
            return getattr(self.raw, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            self.raw.close()
            raise close_error
    def open_lock(path, mode='r', *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path.name == '.rpipe.lock' and mode == 'a+b':
            handles.append(handle)
            if close_error is not None:
                return Stream(handle)
        return handle
    patch.setattr(backend, method, locking)
    patch.setattr(Path, 'open', open_lock)
    return calls, handles


def _reacquire_shared_lock_on_other_thread(root):
    from concurrent.futures import ThreadPoolExecutor
    def acquire():
        with cache_lock(root, timeout=0):
            return 'acquired'
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(acquire).result(timeout=5) == 'acquired'


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('body', ['error', 'cancel', 'normal'])
@pytest.mark.parametrize('cleanup', ['unlock', 'close', 'both'])
def test_lock_cleanup_retains_primary_and_normal_cleanup_stays_strict(tmp_path, monkeypatch, nested, body, cleanup):
    import contextlib
    import errno

    primary = RuntimeError('preparation failed') if body == 'error' else KeyboardInterrupt('cancel preparation') if body == 'cancel' else None
    unlock_error = OSError(errno.EIO, 'unlock IO failed') if cleanup in ('unlock', 'both') else None
    close_error = OSError(errno.EIO, 'close IO failed') if cleanup in ('close', 'both') else None
    expected = primary if primary is not None else unlock_error if unlock_error is not None else close_error
    peer = tmp_path / 'immutable.bin'
    peer.write_bytes(b'immutable shared bytes')
    with monkeypatch.context() as patch:
        calls, handles = _lock_cleanup_faults(patch, unlock_error=unlock_error, close_error=close_error)
        with pytest.raises(type(expected)) as caught:
            with cache_lock(tmp_path, timeout=0):
                with cache_lock(tmp_path / '.', timeout=0) if nested else contextlib.nullcontext():
                    if primary is not None:
                        raise primary
        assert caught.value is expected
        assert calls == ['acquire', 'unlock']
        assert len(handles) == 1 and handles[0].closed
    _reacquire_shared_lock_on_other_thread(tmp_path)
    assert peer.read_bytes() == b'immutable shared bytes'


@pytest.mark.parametrize('fault', ['busy', 'bad_descriptor'])
def test_lock_acquisition_failure_is_not_replaced_by_close_io(tmp_path, monkeypatch, fault):
    import errno

    error = BlockingIOError(errno.EACCES, 'occupied') if fault == 'busy' else OSError(errno.EBADF, 'invalid descriptor')
    with monkeypatch.context() as patch:
        calls, handles = _lock_cleanup_faults(patch, acquire_error=error, close_error=OSError(errno.EIO, 'close failed'))
        with pytest.raises(TimeoutError if fault == 'busy' else OSError) as caught:
            with cache_lock(tmp_path, timeout=0):
                pytest.fail('failed acquisition yielded a body')
        if fault == 'busy':
            assert caught.value.__cause__ is error
        else:
            assert caught.value is error
        assert calls == ['acquire']
        assert len(handles) == 1 and handles[0].closed
    _reacquire_shared_lock_on_other_thread(tmp_path)


@pytest.mark.parametrize('cleanup', ['unlock', 'close', 'both'])
def test_prepared_resource_retains_builder_error_and_diagnostic_staging(tmp_path, monkeypatch, cleanup):
    import errno

    error = RuntimeError('builder failed')
    staging = []
    def build(folder):
        staging.append(folder)
        (folder / 'input.bin').write_bytes(b'partial builder evidence')
        raise error
    with monkeypatch.context() as patch:
        calls, handles = _lock_cleanup_faults(
            patch, unlock_error=OSError(errno.EIO, 'unlock failed') if cleanup in ('unlock', 'both') else None,
            close_error=OSError(errno.EIO, 'close failed') if cleanup in ('close', 'both') else None)
        with pytest.raises(RuntimeError) as caught:
            prepared_resource(tmp_path, {'version': 'cleanup-test'}, ['input.bin'], build)
        assert caught.value is error
        assert calls == ['acquire', 'unlock']
        assert len(handles) == 1 and handles[0].closed
    assert len(staging) == 1 and staging[0].name.startswith('.building-')
    assert (staging[0] / 'input.bin').read_bytes() == b'partial builder evidence'
    assert not (staging[0].parent / 'inputs').exists()
    _reacquire_shared_lock_on_other_thread(staging[0].parent)


@pytest.mark.parametrize('body_error', [False, True])
def test_lock_unexpected_release_programming_error_is_not_silenced(tmp_path, monkeypatch, body_error):
    error = RuntimeError('unexpected release programming error')
    with monkeypatch.context() as patch:
        calls, handles = _lock_cleanup_faults(patch, unlock_error=error)
        with pytest.raises(RuntimeError) as caught:
            with cache_lock(tmp_path, timeout=0):
                if body_error:
                    raise ValueError('preparation failed')
        assert caught.value is error
        assert calls == ['acquire', 'unlock']
        assert len(handles) == 1 and handles[0].closed
    _reacquire_shared_lock_on_other_thread(tmp_path)


@pytest.mark.parametrize('budget', [0, 0.05])
def test_empty_shared_lock_contender_does_not_initialize_before_ownership(tmp_path, budget):
    lock = tmp_path / '.rpipe.lock'
    lock.touch()
    script = """
from pathlib import Path
import os, sys
handle = (Path(sys.argv[1]) / '.rpipe.lock').open('a+b')
if os.name == 'nt':
    import msvcrt
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
print('locked', flush=True)
sys.stdin.readline()
handle.close()
"""
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(TimeoutError):
            with cache_lock(tmp_path, timeout=budget):
                pytest.fail('contender acquired another process lock')
    finally:
        stdout, stderr = process.communicate('release\n', timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert lock.read_bytes() == b''
    identity = lock.stat().st_ino
    with cache_lock(tmp_path, timeout=0):
        assert lock.stat().st_size == 1
        with cache_lock(tmp_path, timeout=0):
            assert lock.stat().st_size == 1
    assert lock.stat().st_ino == identity
    with cache_lock(tmp_path, timeout=0):
        assert lock.stat().st_size == 1


@pytest.mark.parametrize('operation', ['write', 'flush'])
@pytest.mark.parametrize('cancelled', [False, True])
def test_empty_shared_lock_initialization_failure_releases_os_and_reentry_state(tmp_path, monkeypatch, operation, cancelled):
    primary = KeyboardInterrupt('initialization cancelled') if cancelled else OSError('initialization refused')
    with monkeypatch.context() as patch:
        calls, handles = _lock_cleanup_faults(patch)
        original_open = Path.open
        class Stream:
            def __init__(self, raw):
                self.raw = raw
            def __getattr__(self, name):
                return getattr(self.raw, name)
            def write(self, value):
                if operation == 'write':
                    raise primary
                return self.raw.write(value)
            def flush(self):
                if operation == 'flush':
                    raise primary
                return self.raw.flush()
        def open_lock(path, mode='r', *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            return Stream(handle) if path.name == '.rpipe.lock' and mode == 'a+b' else handle
        patch.setattr(Path, 'open', open_lock)
        with pytest.raises(type(primary)) as caught:
            with cache_lock(tmp_path, timeout=0):
                pytest.fail('initialization failure yielded a body')
        assert caught.value is primary
        assert calls == ['acquire', 'unlock']
        assert len(handles) == 1 and handles[0].closed
    _reacquire_shared_lock_on_other_thread(tmp_path)
    with cache_lock(tmp_path, timeout=0):
        pass


def test_shared_digest_read_exit_preserves_first_failure(tmp_path, reader_exit_case, reader_close_fault):
    case = reader_exit_case
    source, target = tmp_path / 'source', tmp_path / 'target'
    payload = b'same immutable bytes'
    source.write_bytes(payload)
    target.write_bytes(payload)
    with reader_close_fault(source, case, 'rb') as (handles, closes):
        if case.expected is None:
            assert link_input(source, target) == target
        else:
            with pytest.raises(type(case.expected)) as caught:
                link_input(source, target)
            assert caught.value is case.expected
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert source.read_bytes() == target.read_bytes() == payload
    assert target.samefile(source) is (case.expected is None)


def test_cache_lock_keeps_raw_stream_without_proxy_context(tmp_path, monkeypatch):
    root = tmp_path / 'shared'
    original_open, handles, closed = Path.open, [], []
    class Proxy:
        def __init__(self, raw):
            self.raw = raw
        def __getattr__(self, name):
            return getattr(self.raw, name)
        def __enter__(self):
            pytest.fail('lock must keep the original stream proxy')
        def __exit__(self, *args):
            pytest.fail('lock owns close, not proxy context semantics')
        def close(self):
            closed.append(self.raw)
            self.raw.close()
    def opening(path, mode='r', *args, **kwargs):
        raw = original_open(path, mode, *args, **kwargs)
        if path == root / '.rpipe.lock' and mode == 'a+b':
            handles.append(raw)
            return Proxy(raw)
        return raw
    monkeypatch.setattr(Path, 'open', opening)
    for _ in range(2):
        with cache_lock(root, timeout=0):
            assert not handles[-1].closed
    assert len(handles) == 2 and closed == handles and all(h.closed for h in handles)
