"""Missing evidence and corrupt optional metrics cannot erase valid peers."""
import json
import pytest
pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1, pytest.mark.cost(cost_class='c1'), pytest.mark.result_type('categorical', detail='summary')]

def test_aggregate_missing_seed_keeps_gap_and_does_not_fill_zero(source_study, run_root, science_factory, file_bytes, science_worker):
    from rpipe.structure.artifact.readout import aggregate
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    specs = plan(source_study, run_root=run_root)
    science_worker(science_factory())
    assert run(specs[0]).status == 'succeeded'
    before = file_bytes(run_root)
    body = aggregate((ref_spec.ref for ref_spec in specs))
    assert len(body['runs']) == 2 and body['complete'] is False
    assert body['missing_runs'] == [specs[1].id]
    metric = body['experiments'][specs[0].experiment]['metrics']['value']
    assert metric == {'mean': 3.0, 'std': 0.0, 'min': 3.0, 'max': 3.0, 'n': 1, 'complete': False}
    assert file_bytes(run_root) == before

@pytest.mark.parametrize('metrics', [[], 'broken', {'value': True}, {'value': float('nan')}], ids=['list', 'string', 'boolean', 'nonfinite'])
def test_bad_optional_metrics_preserve_good_peer_and_original_bytes(source_study, run_root, science_factory, file_bytes, metrics, science_worker):
    from rpipe.structure.artifact.readout import aggregate
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        science_worker(science_factory())
        assert run(spec).status == 'succeeded'
    damaged = json.loads(specs[1].result_path.read_text())
    damaged['metrics'] = metrics
    specs[1].result_path.write_text(json.dumps(damaged), encoding='utf-8')
    before = file_bytes(run_root)
    body = aggregate((ref_spec.ref for ref_spec in specs))
    assert len(body['runs']) == 2
    assert body['runs'][0]['id'] == specs[0].id
    metric = body['experiments'][specs[0].experiment]['metrics']['value']
    assert metric['mean'] == 3.0 and metric['n'] == 1 and (metric['complete'] is False)
    assert file_bytes(run_root) == before

def test_aggregate_factor_combinations_do_not_share_seed_statistics(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.artifact.readout import aggregate
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('axes: {algorithm.lr: [0.1, 0.2]}\n')
    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        science_worker(science_factory())
        assert run(spec).status == 'succeeded'
    body = aggregate((ref_spec.ref for ref_spec in specs))
    assert len(body['experiments']) == 2
    assert [exp['metrics']['value']['n'] for exp in body['experiments'].values()] == [2, 2]

def test_duplicate_run_cannot_inflate_aggregate_sample_count(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.artifact.readout import aggregate
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    with pytest.raises(AdmissionError):
        aggregate((ref_spec.ref for ref_spec in [spec, spec]))

def test_cross_seed_statistics_keep_sample_standard_deviation(source_study, run_root, science_factory, science_worker):
    import math
    from rpipe.structure.artifact.readout import aggregate
    from rpipe.flow.run import run
    from rpipe.structure.make import plan

    class Measurements(science_factory):

        def collect(self, ctx):
            return {'metrics': {'value': 1.0 if ctx.spec.seed == 0 else 3.0}}
    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        science_worker(Measurements())
        assert run(spec).status == 'succeeded'
    metric = aggregate((ref_spec.ref for ref_spec in specs))['experiments'][specs[0].experiment]['metrics']['value']
    assert metric['mean'] == 2.0 and metric['n'] == 2
    assert metric['std'] == pytest.approx(math.sqrt(2))

def test_aggregate_mean_preserves_small_contribution_in_finite_values(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.artifact.readout import aggregate
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    (source_study / 'study.yaml').write_text('seeds: [0, 1, 2]\n', encoding='utf-8')
    values = (1e+16, 1.0, -1e+16)

    class Measurements(science_factory):

        def collect(self, ctx):
            return {'metrics': {'value': values[ctx.spec.seed]}}
    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        science_worker(Measurements())
        assert run(spec).status == 'succeeded'
    metric = aggregate((ref_spec.ref for ref_spec in specs))['experiments'][specs[0].experiment]['metrics']['value']
    assert metric['mean'] == pytest.approx(1 / 3)

@pytest.mark.parametrize('field,value', [('status', []), ('quality', 'yes'), ('evidence_complete', 'yes')])
def test_invalid_result_flags_keep_valid_peer_and_unknown_fact(source_study, run_root, science_factory, file_bytes, field, value, science_worker):
    from rpipe.structure.artifact.readout import aggregate, read_run
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        science_worker(science_factory())
        assert run(spec).status == 'succeeded'
    damaged = json.loads(specs[1].result_path.read_text())
    damaged[field] = value
    specs[1].result_path.write_text(json.dumps(damaged), encoding='utf-8')
    before = file_bytes(run_root)
    result = read_run(specs[1].ref)
    assert result['status'] == 'unknown' and result['evidence_complete'] is False
    body = aggregate((ref_spec.ref for ref_spec in specs))
    assert len(body['runs']) == 2 and body['complete'] is False
    assert body['runs'][0]['status'] == 'succeeded'
    assert file_bytes(run_root) == before

@pytest.mark.parametrize('kind', ['failure', 'cancel'])
def test_read_run_exposes_explicit_failed_or_cancelled_attempt_without_writing(source_study, run_root, science_factory, file_bytes, kind, science_worker):
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.flow.context import Cancellation, Cancelled
    from rpipe.structure.make import plan
    cancellation = Cancellation()

    class Attempt(science_factory):

        def execute(self, ctx):
            if kind == 'cancel':
                cancellation.request()
                raise Cancelled('scientific boundary observed cancellation')
            else:
                raise ValueError('attempt failure')
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(Attempt())
    outcome = run(spec, cancellation=cancellation)
    before = file_bytes(run_root)
    row = read_run(spec.ref)
    assert row['status'] == outcome.status == ('cancelled' if kind == 'cancel' else 'failed')
    assert row['attempt'] and row['scientific_status'] == 'missing' and (not row['busy'])
    assert row['evidence_complete'] is False
    assert file_bytes(run_root) == before

def test_result_without_current_execution_is_unknown_without_repair(source_study, run_root, science_factory, file_bytes, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    (spec.run_dir / 'execution.json').unlink()
    before = file_bytes(run_root)
    row = read_run(spec.ref)
    assert row['status'] == 'unknown' and row['scientific_status'] == 'succeeded'
    assert row['metadata_status'] == 'missing_execution'
    assert file_bytes(run_root) == before

def test_unindexed_attempts_are_unknown_and_cannot_be_selected_by_order(source_study, run_root, science_factory, file_bytes, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import identity, write_json
    from rpipe.structure.control import AdmissionError
    spec = plan(source_study, run_root=run_root)[0]
    for attempt, status in [('f' * 32, 'succeeded'), ('0' * 32, 'failed')]:
        write_json(spec.run_dir / 'attempts' / attempt / 'outcome.json', {'status': status, 'identity': identity(spec)})
    before = file_bytes(run_root)
    row = read_run(spec.ref)
    assert row['status'] == 'unknown' and row['metadata_status'] == 'missing_execution'
    assert 'attempt' not in row and (not row['evidence_complete'])
    science_worker(science_factory())
    _rejected = run(spec, retry=True)
    assert _rejected.status == 'rejected'
    assert _rejected.error['type'] == 'AdmissionError'
    assert file_bytes(run_root) == before

@pytest.mark.parametrize('damage', ['missing-attempt', 'mismatched-attempt', 'typed-attempt', 'bad-schema', 'bad-path'])
def test_active_execution_record_requires_matching_bounded_attempt_evidence(source_study, run_root, science_factory, file_bytes, damage, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import read_json, write_json
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    path = spec.run_dir / 'execution.json'
    record = read_json(path)
    attempt_path = spec.run_dir / 'attempts' / record['attempt'] / 'outcome.json'
    if damage == 'missing-attempt':
        attempt_path.unlink()
    elif damage == 'mismatched-attempt':
        write_json(attempt_path, {**record, 'candidate_status': 'failed'})
    elif damage == 'typed-attempt':
        write_json(attempt_path, {**record, 'evidence_complete': 1})
    elif damage == 'bad-schema':
        write_json(path, {**record, 'schema': 999})
    else:
        write_json(path, {**record, 'attempt': '../peer'})
    before = file_bytes(run_root)
    row = read_run(spec.ref)
    assert row['status'] == 'unknown' and (not row['evidence_complete'])
    assert row['scientific_status'] == 'succeeded'
    assert file_bytes(run_root) == before
