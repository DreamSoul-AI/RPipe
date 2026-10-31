"""The payload boundary cannot publish nonserializable observations as success."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('observation', [object(), {'value': float('nan')}, {'value': object()}],
                         ids=['not-mapping', 'nonfinite-measurement', 'not-serializable'])
def test_invalid_collected_observation_fails_without_success_result(
        source_study, run_root, science_factory, observation, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    class InvalidObservation(science_factory):
        def collect(self, ctx):
            self._phase('collect')
            return observation

    spec = plan(source_study, run_root=run_root)[0]
    payload = InvalidObservation()
    science_worker(payload)
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert outcome.quality is None
    assert outcome.evidence_complete is False
    assert not spec.result_path.exists()
    assert payload.calls == ['prepare', 'execute', 'collect', 'close']


@pytest.mark.parametrize('quality,complete', [(False, True), (None, False)])
def test_scientific_quality_and_evidence_are_preserved_separately(
        source_study, run_root, science_factory, quality, complete, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run

    class ScientificFacts(science_factory):
        def collect(self, ctx):
            self._phase('collect')
            return {'metrics': {'value': 3.0}, 'quality': quality, 'evidence_complete': complete}

    spec = plan(source_study, run_root=run_root)[0]
    science_worker(ScientificFacts())
    outcome = run(spec)
    result = read_run(spec.ref)
    assert outcome.status == 'succeeded'
    assert outcome.quality is result['quality'] is quality
    assert outcome.evidence_complete is result['evidence_complete'] is complete


def test_invalid_quality_does_not_publish_success(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    class InvalidQuality(science_factory):
        def collect(self, ctx):
            return {'quality': 'pass'}

    spec = plan(source_study, run_root=run_root)[0]
    science_worker(InvalidQuality())
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert not spec.result_path.exists()


@pytest.mark.parametrize('phase', ['prepare', 'execute', 'collect', 'close'])
@pytest.mark.parametrize('target', ['config', 'control'])
def test_payload_cannot_change_effective_configuration_after_planning(
        source_study, run_root, science_factory, phase, target, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.control import resolve_control

    spec = plan(source_study, run_root=run_root)[0]
    payload = science_factory()
    original = getattr(payload, phase)

    def change_config(ctx):
        result = original(ctx)
        if target == 'config':
            ctx.config['data']['name'] = 'changed_after_plan'
        else:
            if ctx.control is None:
                ctx.control = resolve_control(ctx.config)
            ctx.control.run.data.name = 'changed_after_plan'
        return result

    setattr(payload, phase, change_config)
    science_worker(payload)
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert outcome.error['type'] == 'SourceMismatch'
    assert payload.calls[-1] == 'close'
    if phase == 'prepare':
        assert 'execute' not in payload.calls
    assert spec.config['data']['name'] == 'Toy'
    assert not spec.result_path.exists()
    assert not (spec.run_dir / '.execution').exists()


def test_release_failure_is_unknown_in_return_record_and_readout(
        source_study, run_root, science_factory, monkeypatch, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import Ownership, read_json

    def denied(owner):
        raise PermissionError('owner release denied')

    monkeypatch.setattr(Ownership, 'release', denied)
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    row = read_run(spec.ref)
    record = read_json(spec.run_dir / 'execution.json')
    attempt = read_json(spec.run_dir / 'attempts' / record['attempt'] / 'outcome.json')
    assert outcome.status == row['status'] == record['candidate_status'] == attempt['candidate_status'] == 'unknown'
    assert not outcome.evidence_complete and not row['evidence_complete']
    assert row['busy'] and row['scientific_status'] == 'succeeded'
    assert outcome.scientific_status == row['scientific_status']
    assert outcome.attempt == row['attempt']
    assert read_json(spec.result_path)['status'] == 'succeeded'
    assert outcome.cleanup_errors[0]['type'] == 'PermissionError'


def test_release_directory_failure_restores_token_and_records_owned_unknown_facts(
        source_study, run_root, science_factory, science_worker, monkeypatch):
    from pathlib import Path
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import Ownership, read_json
    from rpipe.structure.control import BusyError

    spec = plan(source_study, run_root=run_root)[0]
    owner_path = spec.run_dir / '.execution'
    original_rmdir, removed_owner = Path.rmdir, []
    def refuse(path, *args, **kwargs):
        if path == owner_path:
            assert not (path / 'owner.json').exists()
            raise OSError('owned directory release refused')
        return original_rmdir(path, *args, **kwargs)
    original_unlink = Path.unlink
    def remember(path, *args, **kwargs):
        if path == owner_path / 'owner.json':
            removed_owner.append(path.read_bytes())
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'rmdir', refuse)
    monkeypatch.setattr(Path, 'unlink', remember)
    science_worker(science_factory())
    outcome = run(spec)
    assert outcome.status == 'unknown' and not outcome.evidence_complete
    assert removed_owner and (owner_path / 'owner.json').read_bytes() == removed_owner[0]
    record = read_json(spec.run_dir / 'execution.json')
    assert record['candidate_status'] == 'unknown'
    assert record['attempt'] == outcome.attempt
    assert record['cleanup_errors'][0]['type'] == 'OSError'
    assert spec.result_path.exists()
    with pytest.raises(BusyError):
        Ownership(spec).acquire()


@pytest.mark.parametrize('target', ['attempt', 'execution'])
def test_final_record_write_failure_keeps_owner_and_unknown_projection(
        source_study, run_root, science_factory, monkeypatch, target, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact import io as execution

    write = execution.write_json
    def denied(path, record):
        final = record.get('phase') == 'closing'
        selected = path.name == ('outcome.json' if target == 'attempt' else 'execution.json')
        if final and selected:
            raise OSError('final record write denied')
        return write(path, record)

    monkeypatch.setattr(execution, 'write_json', denied)
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    row = read_run(spec.ref)
    assert outcome.status == row['status'] == 'unknown'
    assert not outcome.evidence_complete and not row['evidence_complete']
    assert row['busy'] and spec.result_path.exists()


def test_candidate_success_is_not_complete_until_owner_release(
        source_study, run_root, science_factory, monkeypatch, science_worker):
    from rpipe.structure.artifact.readout import aggregate, read_run
    from rpipe.flow.run import run
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import Ownership

    spec = plan(source_study, run_root=run_root)[0]
    release = Ownership.release
    observed = []
    def closing(owner):
        observed.append(read_run(spec.ref))
        assert aggregate([spec.ref])['complete'] is False
        release(owner)
    monkeypatch.setattr(Ownership, 'release', closing)
    science_worker(science_factory())
    outcome = run(spec)
    assert observed[0]['status'] == 'unknown' and observed[0]['busy']
    assert observed[0]['scientific_status'] == 'succeeded'
    assert outcome.status == read_run(spec.ref)['status'] == 'succeeded'


def test_retry_requires_explicit_admission_and_preserves_previous_attempt(
        source_study, run_root, science_factory, file_bytes, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import read_json
    from rpipe.structure.control import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory(fail='execute'))
    first = run(spec)
    record = read_json(spec.run_dir / 'execution.json')
    old_attempt = spec.run_dir / 'attempts' / record['attempt'] / 'outcome.json'
    stored = old_attempt.read_bytes()
    assert first.status == read_run(spec.ref)['status'] == 'failed'
    before = file_bytes(run_root)
    science_worker(science_factory())
    _rejected = run(spec)
    assert _rejected.status == 'rejected'
    assert _rejected.error['type'] == 'AdmissionError'
    assert file_bytes(run_root) == before
    science_worker(science_factory())
    assert run(spec, retry=True).status == 'succeeded'
    assert old_attempt.read_bytes() == stored
    assert read_json(spec.run_dir / 'execution.json')['attempt'] != record['attempt']


def test_old_return_stays_bound_to_its_attempt_when_retry_finishes_during_release(
        source_study, run_root, science_factory, monkeypatch, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import Ownership, read_json

    spec = plan(source_study, run_root=run_root)[0]
    release = Ownership.release
    retried = []
    def release_and_retry(owner):
        release(owner)
        if not retried:
            retried.append(True)
            science_worker(science_factory())
            assert run(spec, retry=True).status == 'succeeded'
    monkeypatch.setattr(Ownership, 'release', release_and_retry)
    science_worker(science_factory(fail='execute'))
    first = run(spec)
    assert first.status == 'failed'
    assert read_run(spec.ref)['status'] == 'succeeded'
    assert read_json(spec.run_dir / 'execution.json')['candidate_status'] == 'succeeded'


def test_changed_owner_cannot_receive_old_scientific_or_execution_writes(
        source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import read_json, write_json
    from rpipe.flow.run import run

    spec = plan(source_study, run_root=run_root)[0]
    peer = {'peer': 'unchanged'}
    class ChangesOwner(science_factory):
        def execute(self, ctx):
            owner_path = spec.run_dir / '.execution' / 'owner.json'
            write_json(owner_path, {**read_json(owner_path), 'token': 'different-owner'})
            write_json(spec.run_dir / 'execution.json', peer)
    science_worker(ChangesOwner())
    outcome = run(spec)
    assert outcome.status == 'unknown' and not outcome.evidence_complete
    assert not spec.result_path.exists()
    assert read_json(spec.run_dir / 'execution.json') == peer


@pytest.mark.parametrize('kind', ['record', 'invalid-item', 'none-item', 'invalid-sequence'])
def test_supplied_cleanup_records_are_sanitized_before_return_or_persistence(
        source_study, run_root, science_factory, kind, science_worker):
    import json
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    class RemoteCleanup(science_factory):
        def close(self, ctx):
            record = {'type': 'OSError',
                'message': 'secret=CLEANUP_PRIVATE_TEXT', 'traceback': 'C:/private/CLEANUP_PRIVATE_TEXT.py',
                'exit_code': 19}
            ctx.state['cleanup_error_records'] = ([record] if kind == 'record' else
                ['CLEANUP_PRIVATE_TEXT'] if kind == 'invalid-item' else [None] if kind == 'none-item' else record)
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(RemoteCleanup())
    outcome = run(spec)
    assert outcome.status == 'failed'
    assert outcome.cleanup_errors[0]['type'] == ('OSError' if kind == 'record' else 'AdmissionError')
    if kind == 'record':
        assert outcome.cleanup_errors[0]['exit_code'] == 19
    text = json.dumps(outcome.cleanup_errors) + ''.join(
        path.read_text(encoding='utf-8') for path in spec.run_dir.rglob('*.json'))
    assert 'CLEANUP_PRIVATE_TEXT' not in text
