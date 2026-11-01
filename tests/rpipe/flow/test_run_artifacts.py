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
