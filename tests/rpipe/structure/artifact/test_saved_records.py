"""Formal index, reference and record constraints preserve saved evidence."""
from dataclasses import replace
import json

import pytest

from rpipe.structure.artifact import io, readout
from rpipe.structure.control import AdmissionError, RunRef, SourceMismatch

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_formal_refs_and_records_need_no_current_source(saved_run, file_bytes):
    ref = saved_run()
    assert not (ref.run_root.parent / 'source').exists()
    before = file_bytes(ref.run_root)
    assert readout.refs_from_index(ref.study_root) == (ref,)
    assert readout.ref_from_run_dir(ref.run_dir) == ref
    assert readout.read_run(ref)['status'] == 'succeeded'
    assert readout.read_study(ref.study_root)['complete'] is True
    assert file_bytes(ref.run_root) == before


@pytest.mark.parametrize('field,value', [
    ('schema', 2), ('schema', True), ('provenance', {}),
    ('experiments', {}),
])
def test_index_rejects_other_shapes_before_reading_runs(saved_run, field, value):
    ref = saved_run()
    index = io.load_index(ref.study_root)
    index[field] = value
    with pytest.raises((AdmissionError, SourceMismatch)):
        readout.refs_from_index(ref.study_root, index)


@pytest.mark.parametrize('field,value', [('algorithm.mode', 'eval'), ('data.name', 'Other'),
                                        ('seed', True)])
def test_changed_factors_are_not_accepted_as_saved_scientific_facts(saved_run, field, value):
    ref = saved_run()
    index = io.load_index(ref.study_root)
    index['experiments'][0]['factors'][field] = value
    with pytest.raises(SourceMismatch, match='factors'):
        readout.refs_from_index(ref.study_root, index)


def test_missing_config_remains_a_visible_run_gap(saved_run):
    ref = saved_run()
    (ref.run_dir / 'config.yaml').unlink()
    assert readout.refs_from_index(ref.study_root) == (ref,)
    row = readout.read_run(ref)
    assert row['status'] == 'unknown' and row['evidence_complete'] is False
    assert row['metadata_status'] == 'missing_config'


@pytest.mark.parametrize('missing', ['execution', 'outcome', 'metrics'])
def test_missing_current_metadata_cannot_become_result_only_success(saved_run, missing):
    ref = saved_run()
    if missing == 'execution':
        (ref.run_dir / 'execution.json').unlink()
    elif missing == 'outcome':
        (ref.run_dir / 'attempts' / ('a' * 32) / 'outcome.json').unlink()
    else:
        result = io.read_json(ref.result_path)
        result.pop('metrics')
        io.write_json(ref.result_path, result)
    row = readout.read_run(ref)
    assert row['status'] == 'unknown'
    assert row['evidence_complete'] is False


def test_result_identity_mismatch_is_an_explicit_error(saved_run):
    ref = saved_run()
    result = io.read_json(ref.result_path)
    result['identity']['config_digest'] = 'c' * 64
    io.write_json(ref.result_path, result)
    with pytest.raises(SourceMismatch):
        readout.read_run(ref)


def test_existing_foreign_attempt_does_not_become_current_success(saved_run):
    ref = saved_run()
    result = io.read_json(ref.result_path)
    result['attempt'] = 'c' * 32
    io.write_json(ref.result_path, result)
    row = readout.read_run(ref)
    assert row['status'] == 'unknown' and row['evidence_complete'] is False


def test_busy_owner_is_only_observed_and_never_removed(saved_run):
    ref = saved_run()
    owner = ref.run_dir / '.execution'
    owner.mkdir()
    row = readout.read_run(ref)
    assert row['status'] == 'unknown' and row['busy'] is True
    assert owner.is_dir()


@pytest.mark.parametrize('mutate', ['seed', 'location', 'group', 'train_id'])
def test_index_identity_group_and_dependency_tampering_are_rejected(saved_run, mutate):
    ref = saved_run()
    index = io.load_index(ref.study_root)
    group = index['experiments'][0]
    row = group['runs'][0]
    if mutate == 'seed':
        row['seed'] = True
    elif mutate == 'location':
        row['run_dir'] = '../peer'
    elif mutate == 'group':
        group['id'] = 'another-group'
    else:
        row['train_id'] = 'missing'
    with pytest.raises((AdmissionError, SourceMismatch)):
        readout.refs_from_index(ref.study_root, index)


def test_shared_record_constraints_reject_boolean_schema_and_missing_metrics(saved_run):
    ref = saved_run()
    record = io.read_json(ref.run_dir / 'execution.json')
    record['schema'] = True
    with pytest.raises(AdmissionError):
        io.validate_execution_record(record)
    result = io.read_json(ref.result_path)
    result.pop('metrics')
    with pytest.raises(AdmissionError):
        io.validate_run_record(result)


def test_ref_construction_is_pure_and_retains_explicit_dependency(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('RunRef attempted filesystem access')
    with monkeypatch.context() as guard:
        guard.setattr(type(tmp_path), 'resolve', forbidden)
        guard.setattr(type(tmp_path), 'exists', forbidden)
        ref = RunRef('run', 7, 'group', tmp_path / 'study' / 'v1', tmp_path,
                     'a' * 64, 'b' * 64, 'v1', 'train')
    assert ref.run_dir == tmp_path / 'study' / 'v1' / 'runs' / 'run'
    assert ref.train_id == 'train'


@pytest.mark.parametrize('change', [{'seed': True}, {'source_digest': 'bad'},
                                    {'study_root': 'relative'}, {'train_id': '../peer'}])
def test_invalid_ref_cannot_redirect_a_read(tmp_path, change):
    ref = RunRef('run', 0, 'group', tmp_path / 'study' / 'v1', tmp_path,
                 'a' * 64, 'b' * 64, 'v1')
    with pytest.raises((AdmissionError, TypeError)):
        replace(ref, **change)
