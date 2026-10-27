"""The payload boundary cannot publish nonserializable observations as success."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('observation', [object(), {'value': float('nan')}, {'value': object()}],
                         ids=['not-mapping', 'nonfinite-measurement', 'not-serializable'])
def test_invalid_collected_observation_fails_without_success_result(
        source_study, run_root, payload_factory, observation):
    from rpipe.api import plan, run

    class InvalidObservation(payload_factory):
        def collect(self, ctx):
            self._phase('collect')
            return observation

    spec = plan(source_study, run_root=run_root)[0]
    payload = InvalidObservation()
    outcome = run(spec, payload=payload)
    assert outcome.status == 'failed'
    assert outcome.quality is None
    assert outcome.evidence_complete is False
    assert not spec.result_path.exists()
    assert payload.calls == ['prepare', 'execute', 'collect', 'close']


@pytest.mark.parametrize('quality,complete', [(False, True), (None, False)])
def test_scientific_quality_and_evidence_are_preserved_separately(
        source_study, run_root, payload_factory, quality, complete):
    from rpipe.api import plan, read_run, run

    class ScientificFacts(payload_factory):
        def collect(self, ctx):
            self._phase('collect')
            return {'metrics': {'value': 3.0}, 'quality': quality, 'evidence_complete': complete}

    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=ScientificFacts())
    result = read_run(spec)
    assert outcome.status == 'succeeded'
    assert outcome.quality is result['quality'] is quality
    assert outcome.evidence_complete is result['evidence_complete'] is complete


def test_invalid_quality_does_not_publish_success(source_study, run_root, payload_factory):
    from rpipe.api import plan, run

    class InvalidQuality(payload_factory):
        def collect(self, ctx):
            return {'quality': 'pass'}

    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=InvalidQuality())
    assert outcome.status == 'failed'
    assert not spec.result_path.exists()


@pytest.mark.parametrize('entry', ['public', 'mechanism'])
@pytest.mark.parametrize('phase', ['prepare', 'execute', 'collect', 'close'])
@pytest.mark.parametrize('target', ['config', 'control'])
def test_payload_cannot_change_effective_configuration_after_planning(
        source_study, run_root, payload_factory, entry, phase, target):
    from rpipe.api import plan, run
    from rpipe.structure.contracts import control_from_config

    spec = plan(source_study, run_root=run_root)[0]
    payload = payload_factory()
    original = getattr(payload, phase)

    def change_config(ctx):
        result = original(ctx)
        if target == 'config':
            ctx.config['data']['name'] = 'changed_after_plan'
        else:
            if ctx.control is None:
                ctx.control = control_from_config(ctx.config)
            ctx.control.run.data.name = 'changed_after_plan'
        return result

    setattr(payload, phase, change_config)
    if entry == 'mechanism':
        from rpipe.structure.execution import execute
        outcome = execute(spec, payload)
    else:
        outcome = run(spec, payload=payload)
    assert outcome.status == 'failed'
    assert outcome.error['type'] == 'SourceMismatch'
    assert payload.calls[-1] == 'close'
    if phase == 'prepare':
        assert 'execute' not in payload.calls
    assert spec.config['data']['name'] == 'Toy'
    assert not spec.result_path.exists()
    assert not (spec.run_dir / '.execution').exists()


def test_release_failure_is_unknown_in_return_record_and_readout(
        source_study, run_root, payload_factory, monkeypatch):
    from rpipe.api import plan, read_run, run
    from rpipe.structure.artifacts import Ownership, read_json

    def denied(owner):
        raise PermissionError('owner release denied')

    monkeypatch.setattr(Ownership, 'release', denied)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=payload_factory())
    row = read_run(spec)
    record = read_json(spec.run_dir / 'execution.json')
    attempt = read_json(spec.run_dir / 'attempts' / record['attempt'] / 'outcome.json')
    assert outcome.status == row['status'] == record['candidate_status'] == attempt['candidate_status'] == 'unknown'
    assert not outcome.evidence_complete and not row['evidence_complete']
    assert row['busy'] and row['scientific_status'] == 'succeeded'
    assert outcome.scientific_status == row['scientific_status']
    assert outcome.attempt == row['attempt']
    assert read_json(spec.result_path)['status'] == 'succeeded'
    assert outcome.cleanup_errors[0]['type'] == 'PermissionError'


@pytest.mark.parametrize('target', ['attempt', 'execution'])
def test_final_record_write_failure_keeps_owner_and_unknown_projection(
        source_study, run_root, payload_factory, monkeypatch, target):
    from rpipe.api import plan, read_run, run
    from rpipe.structure import execution

    write = execution.write_json
    def denied(path, record):
        final = record.get('phase') == 'closing'
        selected = path.name == ('outcome.json' if target == 'attempt' else 'execution.json')
        if final and selected:
            raise OSError('final record write denied')
        return write(path, record)

    monkeypatch.setattr(execution, 'write_json', denied)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=payload_factory())
    row = read_run(spec)
    assert outcome.status == row['status'] == 'unknown'
    assert not outcome.evidence_complete and not row['evidence_complete']
    assert row['busy'] and spec.result_path.exists()


def test_candidate_success_is_not_complete_until_owner_release(
        source_study, run_root, payload_factory, monkeypatch):
    from rpipe.api import aggregate, plan, read_run, run
    from rpipe.structure.artifacts import Ownership

    spec = plan(source_study, run_root=run_root)[0]
    release = Ownership.release
    observed = []
    def closing(owner):
        observed.append(read_run(spec))
        assert aggregate([spec])['complete'] is False
        release(owner)
    monkeypatch.setattr(Ownership, 'release', closing)
    outcome = run(spec, payload=payload_factory())
    assert observed[0]['status'] == 'unknown' and observed[0]['busy']
    assert observed[0]['scientific_status'] == 'succeeded'
    assert outcome.status == read_run(spec)['status'] == 'succeeded'


def test_retry_requires_explicit_admission_and_preserves_previous_attempt(
        source_study, run_root, payload_factory, file_bytes):
    from rpipe.api import plan, read_run, run
    from rpipe.structure.artifacts import read_json
    from rpipe.structure.contracts import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    first = run(spec, payload=payload_factory(fail='execute'))
    record = read_json(spec.run_dir / 'execution.json')
    old_attempt = spec.run_dir / 'attempts' / record['attempt'] / 'outcome.json'
    stored = old_attempt.read_bytes()
    assert first.status == read_run(spec)['status'] == 'failed'
    before = file_bytes(run_root)
    with pytest.raises(AdmissionError, match='retry'):
        run(spec, payload=payload_factory())
    assert file_bytes(run_root) == before
    assert run(spec, payload=payload_factory(), retry=True).status == 'succeeded'
    assert old_attempt.read_bytes() == stored
    assert read_json(spec.run_dir / 'execution.json')['attempt'] != record['attempt']


def test_old_return_stays_bound_to_its_attempt_when_retry_finishes_during_release(
        source_study, run_root, payload_factory, monkeypatch):
    from rpipe.api import plan, read_run, run
    from rpipe.structure.artifacts import Ownership, read_json

    spec = plan(source_study, run_root=run_root)[0]
    release = Ownership.release
    retried = []
    def release_and_retry(owner):
        release(owner)
        if not retried:
            retried.append(True)
            assert run(spec, payload=payload_factory(), retry=True).status == 'succeeded'
    monkeypatch.setattr(Ownership, 'release', release_and_retry)
    first = run(spec, payload=payload_factory(fail='execute'))
    assert first.status == 'failed'
    assert read_run(spec)['status'] == 'succeeded'
    assert read_json(spec.run_dir / 'execution.json')['candidate_status'] == 'succeeded'


def test_changed_owner_cannot_receive_old_scientific_or_execution_writes(
        source_study, run_root, payload_factory):
    from rpipe.api import plan
    from rpipe.structure.artifacts import read_json, write_json
    from rpipe.structure.execution import execute

    spec = plan(source_study, run_root=run_root)[0]
    peer = {'peer': 'unchanged'}
    class ChangesOwner(payload_factory):
        def execute(self, ctx):
            owner_path = spec.run_dir / '.execution' / 'owner.json'
            write_json(owner_path, {**read_json(owner_path), 'token': 'different-owner'})
            write_json(spec.run_dir / 'execution.json', peer)
    outcome = execute(spec, ChangesOwner())
    assert outcome.status == 'unknown' and not outcome.evidence_complete
    assert not spec.result_path.exists()
    assert read_json(spec.run_dir / 'execution.json') == peer


@pytest.mark.parametrize('kind', ['record', 'invalid-item', 'none-item', 'invalid-sequence'])
def test_supplied_cleanup_records_are_sanitized_before_return_or_persistence(
        source_study, run_root, payload_factory, kind):
    import json
    from rpipe.api import plan, run

    class RemoteCleanup(payload_factory):
        def close(self, ctx):
            record = {'type': 'OSError',
                'message': 'secret=CLEANUP_PRIVATE_TEXT', 'traceback': 'C:/private/CLEANUP_PRIVATE_TEXT.py',
                'exit_code': 19}
            ctx.state['cleanup_error_records'] = ([record] if kind == 'record' else
                ['CLEANUP_PRIVATE_TEXT'] if kind == 'invalid-item' else [None] if kind == 'none-item' else record)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=RemoteCleanup())
    assert outcome.status == 'failed'
    assert outcome.cleanup_errors[0]['type'] == ('OSError' if kind == 'record' else 'AdmissionError')
    if kind == 'record':
        assert outcome.cleanup_errors[0]['exit_code'] == 19
    text = json.dumps(outcome.cleanup_errors) + ''.join(
        path.read_text(encoding='utf-8') for path in spec.run_dir.rglob('*.json'))
    assert 'CLEANUP_PRIVATE_TEXT' not in text
