"""Source admission protects stored success and reading cannot repair artifacts."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]

def test_partial_ownership_write_and_cleanup_failure_keep_primary_and_busy(
        source_study, run_root, monkeypatch):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact import io as artifacts
    from rpipe.structure.control import BusyError

    spec = plan(source_study, run_root=run_root)[0]
    write_json = artifacts.write_json

    def fail_owner(path, value):
        if path.name == 'owner.json':
            path.write_bytes(b'{"partial":')
            raise OSError('primary disk failure')
        return write_json(path, value)

    monkeypatch.setattr(artifacts, 'write_json', fail_owner)
    owner = artifacts.Ownership(spec)
    with pytest.raises(OSError, match='primary disk failure'):
        owner.acquire()
    with pytest.raises(BusyError):
        artifacts.Ownership(spec).acquire()
    assert (owner.path / 'owner.json').read_bytes() == b'{"partial":'


@pytest.mark.parametrize('replacement', ['directory', 'owner-record'])
def test_release_recovery_never_overwrites_a_replacement_owner(
        source_study, run_root, monkeypatch, replacement):
    from pathlib import Path
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import Ownership
    from rpipe.structure.control import BusyError

    spec = plan(source_study, run_root=run_root)[0]
    owner = Ownership(spec, attempt='a' * 32)
    owner.acquire()
    original_rmdir = Path.rmdir
    peer = b'{"token":"peer-owner","attempt":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}'
    def replace_owner(path, *args, **kwargs):
        if path == owner.path:
            if replacement == 'directory':
                original_rmdir(path)
                path.mkdir()
            (path / 'owner.json').write_bytes(peer)
            raise OSError('release interrupted by peer replacement')
        return original_rmdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'rmdir', replace_owner)
    with pytest.raises(OSError, match='release interrupted'):
        owner.release()
    assert (owner.path / 'owner.json').read_bytes() == peer
    with pytest.raises(BusyError):
        owner.verify()


@pytest.mark.parametrize('entry', ['run', 'publish'])
def test_existing_configuration_cannot_substitute_boolean_for_integer(
        source_study, run_root, file_bytes, monkeypatch, entry):
    from rpipe.flow import worker
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import load_config, write_config
    from rpipe.structure.control import SourceMismatch
    from rpipe.structure.make import plan, publish

    source_config = source_study / 'experiment_config.yaml'
    config = load_config(source_config)
    config['algorithm']['num_steps'] = 1
    write_config(source_config, config)
    spec = plan(source_study, run_root=run_root)[0]
    publish([spec])
    config_path = spec.run_dir / 'config.yaml'
    tampered = load_config(config_path)
    tampered['algorithm']['num_steps'] = True
    write_config(config_path, tampered)
    before = file_bytes(run_root)
    monkeypatch.setattr(worker, 'compute', lambda *args: pytest.fail('rejected config started science'))
    if entry == 'run':
        rejected = run(spec)
        assert rejected.status == 'rejected'
        assert rejected.error['type'] == 'SourceMismatch'
    else:
        with pytest.raises(SourceMismatch):
            publish([spec])
    assert file_bytes(run_root) == before


@pytest.mark.parametrize('component', ['assets', 'attempts'])
def test_existing_output_link_cannot_write_outside_run_root(
        source_study, run_root, science_factory, file_bytes, component, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.control import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    peer = run_root.parent / 'external-peer'
    peer.mkdir()
    (peer / 'evidence').write_bytes(b'other owner bytes')
    spec.run_dir.mkdir(parents=True)
    try:
        (spec.run_dir / component).symlink_to(peer, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'directory symlink unavailable: {error}')
    before = file_bytes(peer)
    payload = science_factory()
    science_worker(payload)
    _rejected = run(spec)
    assert _rejected.status == 'rejected'
    assert _rejected.error['type'] == 'AdmissionError'
    assert payload.calls == []
    assert file_bytes(peer) == before
