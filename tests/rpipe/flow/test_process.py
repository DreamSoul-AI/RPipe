"""Derived processing checks source and writes only outside scientific inputs."""
import importlib
import pytest
from rpipe.structure import make as planning
from rpipe.flow.process import process_study

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('target_kind', ['source', 'input'])
def test_process_rejects_summary_link_before_writing_source_or_input(
        source_study, run_root, tmp_path, file_bytes, monkeypatch, target_kind):
    study = importlib.import_module('rpipe.flow.process')
    from rpipe.structure.artifact.io import load_config, write_config
    from rpipe.structure.control import AdmissionError

    inputs = run_root / 'shared' / 'input'
    inputs.mkdir(parents=True)
    (inputs / 'process.json').write_bytes(b'original input')
    (source_study / 'process.json').write_bytes(b'original source')
    config = load_config(source_study / 'experiment_config.yaml')
    config['data']['path'] = str(inputs)
    write_config(source_study / 'experiment_config.yaml', config)
    spec = planning.plan(source_study, run_root=run_root)[0]
    spec.study_root.mkdir(parents=True)
    target = source_study if target_kind == 'source' else inputs
    (spec.study_root / 'summary').symlink_to(target, target_is_directory=True)
    peer = tmp_path / 'peer'
    peer.write_bytes(b'peer untouched')
    before_source, before_inputs = file_bytes(source_study), file_bytes(inputs)
    hooks = []
    monkeypatch.setattr(study, 'run_study_phase', lambda *args, **kwargs: hooks.append(args))
    try:
        with pytest.raises(AdmissionError):
            process_study([spec])
    finally:
        assert file_bytes(source_study) == before_source
        assert file_bytes(inputs) == before_inputs
        assert peer.read_bytes() == b'peer untouched'
        assert not hooks


def test_process_rechecks_source_before_output_or_hook(
        source_study, run_root, file_bytes, monkeypatch):
    study = importlib.import_module('rpipe.flow.process')
    from rpipe.structure.control import SourceMismatch

    spec = planning.plan(source_study, run_root=run_root)[0]
    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('# changed after planning\n')
    before = file_bytes(source_study)
    hooks = []
    monkeypatch.setattr(study, 'run_study_phase', lambda *args, **kwargs: hooks.append(args))
    with pytest.raises(SourceMismatch):
        process_study([spec])
    assert file_bytes(source_study) == before
    assert not run_root.exists()
    assert not hooks


def test_run_process_receives_saved_records_after_scientific_objects_close(
        source_study, run_root, science_factory, science_worker, monkeypatch):
    from rpipe.flow.context import StudyProcessContext
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import read_json

    actions = science_factory()
    science_worker(actions)
    spec = planning.plan(source_study, run_root=run_root)[0]
    process = importlib.import_module('rpipe.flow.process')
    observed = []
    def inspect(source, phase, ctx, study):
        assert phase == 'process'
        assert isinstance(ctx, StudyProcessContext)
        assert actions.calls[-1] == 'close'
        assert not any(value is actions for value in ctx.state.values())
        assert not hasattr(ctx, 'resources') and not hasattr(ctx, 'control')
        assert ctx.records == (read_json(spec.result_path),)
        observed.append(ctx.scope)
    monkeypatch.setattr(process, 'run_study_phase', inspect)
    outcome = run(spec)
    assert outcome.status == 'succeeded'
    assert observed == ['run']
    assert read_json(spec.result_path) == outcome.result


def test_source_drift_during_aggregation_preserves_existing_summary_and_peer_bytes(
        source_study, run_root, file_bytes, monkeypatch):
    from rpipe.structure.control import SourceMismatch

    spec = planning.plan(source_study, run_root=run_root)[0]
    summary = spec.study_root / 'summary' / 'process.json'
    summary.parent.mkdir(parents=True)
    summary.write_bytes(b'previous verified summary')
    (summary.parent / 'peer.json').write_bytes(b'peer summary untouched')
    before = file_bytes(run_root)
    module = importlib.import_module('rpipe.flow.process')
    aggregate, hooks = module.aggregate, []
    def drift(specs):
        result = aggregate(specs)
        declaration = source_study / 'study.yaml'
        declaration.write_text(declaration.read_text(encoding='utf-8') + '# changed during aggregate\n',
                               encoding='utf-8')
        return result
    monkeypatch.setattr(module, 'aggregate', drift)
    monkeypatch.setattr(module, 'run_study_phase', lambda *args, **kwargs: hooks.append(args))
    with pytest.raises(SourceMismatch):
        process_study([spec])
    assert file_bytes(run_root) == before
    assert hooks == []
