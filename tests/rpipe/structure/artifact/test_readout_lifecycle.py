"""Source admission protects stored success and reading cannot repair artifacts."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]

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
