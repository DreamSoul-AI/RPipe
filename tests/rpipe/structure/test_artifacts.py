"""Source admission protects stored success and reading cannot repair artifacts."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_source_drift_preserves_saved_reads_and_rejects_new_execution(
        source_study, run_root, science_factory, file_bytes, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run

    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    before = file_bytes(run_root)
    (source_study / 'helper.py').write_text('VALUE = 2\n', encoding='utf-8')
    payload = science_factory()
    assert read_run(spec.ref)['status'] == 'succeeded'
    new_spec = plan(source_study, run_root=run_root)[0]
    science_worker(payload)
    _rejected = run(new_spec)
    assert _rejected.status == 'rejected'
    assert _rejected.error['type'] == 'SourceMismatch'
    assert payload.calls == []
    assert file_bytes(run_root) == before


def test_read_run_is_readonly_and_quality_is_not_inferred_from_execution(
        source_study, run_root, science_factory, file_bytes, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run

    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    before = file_bytes(run_root)
    result = read_run(spec.ref)
    assert result['status'] == outcome.status == 'succeeded'
    assert outcome.quality is None
    assert outcome.evidence_complete is True
    assert file_bytes(run_root) == before


def test_truncated_result_is_rejected_without_repair(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run

    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    spec.result_path.write_bytes(b'{"status": "succeeded",')
    before = spec.result_path.read_bytes()
    record = read_run(spec.ref)
    assert record['status'] == 'unknown' and record['scientific_status'] == 'invalid'
    assert record['evidence_complete'] is False and record['record_error']['type'] == 'JSONDecodeError'
    assert spec.result_path.read_bytes() == before


def test_atomic_publish_failure_does_not_report_scientific_success_or_remove_peer(
        source_study, run_root, science_factory, monkeypatch, science_worker):
    from pathlib import Path

    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    spec = plan(source_study, run_root=run_root)[0]
    run_root.mkdir()
    peer = run_root / 'peer.dat'
    peer.write_bytes(b'other owner data')
    replace = Path.replace
    failed_publications = []

    def fail_selected(source, destination, *args, **kwargs):
        if Path(destination) == spec.result_path:
            failed_publications.append(destination)
            raise OSError('result storage unavailable')
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(Path, 'replace', fail_selected)
    science_worker(science_factory())
    outcome = run(spec)
    assert failed_publications == [spec.result_path]
    assert outcome.status == 'failed'
    assert outcome.evidence_complete is False
    assert outcome.error['type'] == 'OSError'
    assert outcome.error['message'] == '[redacted]'
    assert not spec.result_path.exists()
    assert peer.read_bytes() == b'other owner data'


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
