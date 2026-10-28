"""Source admission protects stored success and reading cannot repair artifacts."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_source_drift_rejects_read_and_start_without_changing_old_success(
        source_study, run_root, payload_factory, file_bytes):
    from rpipe.structure.make import plan
    from rpipe.api import read_run, run
    from rpipe.structure.control import SourceMismatch

    spec = plan(source_study, run_root=run_root)[0]
    assert run(spec, payload=payload_factory()).status == 'succeeded'
    before = file_bytes(run_root)
    (source_study / 'helper.py').write_text('VALUE = 2\n', encoding='utf-8')
    payload = payload_factory()
    with pytest.raises(SourceMismatch):
        read_run(spec)
    new_spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(SourceMismatch):
        run(new_spec, payload=payload)
    assert payload.calls == []
    assert file_bytes(run_root) == before


def test_read_run_is_readonly_and_quality_is_not_inferred_from_execution(
        source_study, run_root, payload_factory, file_bytes):
    from rpipe.structure.make import plan
    from rpipe.api import read_run, run

    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=payload_factory())
    before = file_bytes(run_root)
    result = read_run(spec)
    assert result['status'] == outcome.status == 'succeeded'
    assert outcome.quality is None
    assert outcome.evidence_complete is True
    assert file_bytes(run_root) == before


def test_truncated_result_is_rejected_without_repair(source_study, run_root, payload_factory):
    from rpipe.structure.make import plan
    from rpipe.api import read_run, run
    from rpipe.structure.control import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    assert run(spec, payload=payload_factory()).status == 'succeeded'
    spec.result_path.write_bytes(b'{"status": "succeeded",')
    before = spec.result_path.read_bytes()
    with pytest.raises(AdmissionError):
        read_run(spec)
    assert spec.result_path.read_bytes() == before


def test_atomic_publish_failure_does_not_report_scientific_success_or_remove_peer(
        source_study, run_root, payload_factory, monkeypatch):
    from pathlib import Path

    from rpipe.structure.make import plan
    from rpipe.api import run

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
    outcome = run(spec, payload=payload_factory())
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


@pytest.mark.parametrize('component', ['assets', 'attempts'])
def test_existing_output_link_cannot_write_outside_run_root(
        source_study, run_root, payload_factory, file_bytes, component):
    from rpipe.structure.make import plan
    from rpipe.api import run
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
    payload = payload_factory()
    with pytest.raises(AdmissionError, match='escapes run_root'):
        run(spec, payload=payload)
    assert payload.calls == []
    assert file_bytes(peer) == before
