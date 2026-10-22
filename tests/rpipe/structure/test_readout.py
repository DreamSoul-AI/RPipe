"""Missing evidence and corrupt optional metrics cannot erase valid peers."""

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_aggregate_missing_seed_keeps_gap_and_does_not_fill_zero(
        source_study, run_root, payload_factory, file_bytes):
    from rpipe.api import aggregate, plan, run

    specs = plan(source_study, run_root=run_root)
    assert run(specs[0], payload=payload_factory()).status == 'succeeded'
    before = file_bytes(run_root)
    body = aggregate(specs)
    assert len(body['runs']) == 2 and body['complete'] is False
    assert body['missing_runs'] == [specs[1].id]
    metric = body['experiments'][specs[0].experiment]['metrics']['value']
    assert metric == {'mean': 3.0, 'std': 0.0, 'n': 1, 'complete': False}
    assert file_bytes(run_root) == before


@pytest.mark.parametrize('metrics', [[], 'broken', {'value': True}, {'value': float('nan')}],
                         ids=['list', 'string', 'boolean', 'nonfinite'])
def test_bad_optional_metrics_preserve_good_peer_and_original_bytes(
        source_study, run_root, payload_factory, file_bytes, metrics):
    from rpipe.api import aggregate, plan, run

    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        assert run(spec, payload=payload_factory()).status == 'succeeded'
    damaged = json.loads(specs[1].result_path.read_text())
    damaged['metrics'] = metrics
    specs[1].result_path.write_text(json.dumps(damaged), encoding='utf-8')
    before = file_bytes(run_root)
    body = aggregate(specs)
    assert len(body['runs']) == 2
    assert body['runs'][0]['id'] == specs[0].id
    metric = body['experiments'][specs[0].experiment]['metrics']['value']
    assert metric['mean'] == 3.0 and metric['n'] == 1 and metric['complete'] is False
    assert file_bytes(run_root) == before


def test_aggregate_factor_combinations_do_not_share_seed_statistics(
        source_study, run_root, payload_factory):
    from rpipe.api import aggregate, plan, run

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('axes: {algorithm.lr: [0.1, 0.2]}\n')
    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        assert run(spec, payload=payload_factory()).status == 'succeeded'
    body = aggregate(specs)
    assert len(body['experiments']) == 2
    assert [exp['metrics']['value']['n'] for exp in body['experiments'].values()] == [2, 2]


def test_duplicate_run_cannot_inflate_aggregate_sample_count(source_study, run_root, payload_factory):
    from rpipe.api import aggregate, plan, run
    from rpipe.structure.contracts import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    assert run(spec, payload=payload_factory()).status == 'succeeded'
    with pytest.raises(AdmissionError):
        aggregate([spec, spec])


@pytest.mark.parametrize('bad_value', [True, float('nan')])
def test_compare_non_numeric_optional_metric_has_no_numeric_difference(
        source_study, run_root, payload_factory, bad_value):
    from rpipe.api import plan, run
    from rpipe.structure.readout import compare

    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        assert run(spec, payload=payload_factory()).status == 'succeeded'
    damaged = json.loads(specs[1].result_path.read_text())
    damaged['metrics']['value'] = bad_value
    specs[1].result_path.write_text(json.dumps(damaged), encoding='utf-8')
    assert compare(specs[1], specs[1])['metrics']['value']['difference'] is None


def test_cross_seed_statistics_keep_sample_standard_deviation(
        source_study, run_root, payload_factory):
    import math
    from rpipe.api import aggregate, plan, run

    class Measurements(payload_factory):
        def collect(self, ctx):
            return {'metrics': {'value': 1.0 if ctx.spec.seed == 0 else 3.0}}

    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        assert run(spec, payload=Measurements()).status == 'succeeded'
    metric = aggregate(specs)['experiments'][specs[0].experiment]['metrics']['value']
    assert metric['mean'] == 2.0 and metric['n'] == 2
    assert metric['std'] == pytest.approx(math.sqrt(2))


def test_aggregate_mean_preserves_small_contribution_in_finite_values(
        source_study, run_root, payload_factory):
    from rpipe.api import aggregate, plan, run

    (source_study / 'study.yaml').write_text('seeds: [0, 1, 2]\n', encoding='utf-8')
    values = (1e16, 1.0, -1e16)

    class Measurements(payload_factory):
        def collect(self, ctx):
            return {'metrics': {'value': values[ctx.spec.seed]}}

    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        assert run(spec, payload=Measurements()).status == 'succeeded'
    metric = aggregate(specs)['experiments'][specs[0].experiment]['metrics']['value']
    assert metric['mean'] == pytest.approx(1 / 3)


@pytest.mark.parametrize('field,value', [('status', []), ('quality', 'yes'),
                                        ('evidence_complete', 'yes')])
def test_invalid_result_flags_keep_valid_peer_and_unknown_fact(
        source_study, run_root, payload_factory, file_bytes, field, value):
    from rpipe.api import aggregate, plan, read_run, run

    specs = plan(source_study, run_root=run_root)
    for spec in specs:
        assert run(spec, payload=payload_factory()).status == 'succeeded'
    damaged = json.loads(specs[1].result_path.read_text())
    damaged[field] = value
    specs[1].result_path.write_text(json.dumps(damaged), encoding='utf-8')
    before = file_bytes(run_root)
    result = read_run(specs[1])
    assert result['status'] == 'unknown' and result['evidence_complete'] is False
    body = aggregate(specs)
    assert len(body['runs']) == 2 and body['complete'] is False
    assert body['runs'][0]['status'] == 'succeeded'
    assert file_bytes(run_root) == before


@pytest.mark.parametrize('kind', ['failure', 'cancel'])
def test_read_run_exposes_explicit_failed_or_cancelled_attempt_without_writing(
        source_study, run_root, payload_factory, file_bytes, kind):
    from rpipe.api import Cancellation, plan, read_run, run

    cancellation = Cancellation()
    class Attempt(payload_factory):
        def execute(self, ctx):
            if kind == 'cancel':
                cancellation.request()
            else:
                raise ValueError('attempt failure')
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=Attempt(), cancellation=cancellation)
    before = file_bytes(run_root)
    row = read_run(spec)
    assert row['status'] == outcome.status == ('cancelled' if kind == 'cancel' else 'failed')
    assert row['attempt'] and row['scientific_status'] == 'missing' and not row['busy']
    assert row['evidence_complete'] is False
    assert file_bytes(run_root) == before


def test_result_only_legacy_record_remains_readable_without_migration(
        source_study, run_root, payload_factory, file_bytes):
    from rpipe.api import plan, read_run, run

    spec = plan(source_study, run_root=run_root)[0]
    assert run(spec, payload=payload_factory()).status == 'succeeded'
    (spec.run_dir / 'execution.json').unlink()
    before = file_bytes(run_root)
    row = read_run(spec)
    assert row['status'] == row['scientific_status'] == 'succeeded'
    assert row['execution_status'] == 'legacy_unindexed'
    assert file_bytes(run_root) == before


def test_unindexed_legacy_attempts_are_unknown_and_cannot_be_selected_by_order(
        source_study, run_root, payload_factory, file_bytes):
    from rpipe.api import plan, read_run, run
    from rpipe.structure.artifacts import identity, write_json
    from rpipe.structure.contracts import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    for attempt, status in [('f' * 32, 'succeeded'), ('0' * 32, 'failed')]:
        write_json(spec.run_dir / 'attempts' / attempt / 'outcome.json',
                   {'status': status, 'identity': identity(spec)})
    before = file_bytes(run_root)
    row = read_run(spec)
    assert row['status'] == 'unknown' and row['execution_status'] == 'legacy_unindexed'
    assert 'attempt' not in row and not row['evidence_complete']
    with pytest.raises(AdmissionError, match='legacy'):
        run(spec, payload=payload_factory(), retry=True)
    assert file_bytes(run_root) == before


@pytest.mark.parametrize('damage', ['missing-attempt', 'mismatched-attempt', 'typed-attempt', 'bad-schema', 'bad-path'])
def test_active_execution_record_requires_matching_bounded_attempt_evidence(
        source_study, run_root, payload_factory, file_bytes, damage):
    from rpipe.api import plan, read_run, run
    from rpipe.structure.artifacts import read_json, write_json

    spec = plan(source_study, run_root=run_root)[0]
    assert run(spec, payload=payload_factory()).status == 'succeeded'
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
    row = read_run(spec)
    assert row['status'] == 'unknown' and not row['evidence_complete']
    assert row['scientific_status'] == 'succeeded'
    assert file_bytes(run_root) == before
