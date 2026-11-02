"""Study stage timing and derived processing use the coordinator's single outcome."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _hook(source, phase, body):
    directory = source / 'flow'
    directory.mkdir(exist_ok=True)
    (directory / f'{phase}.py').write_text(body, encoding='utf-8')
    declaration = source / 'study.yaml'
    text = declaration.read_text(encoding='utf-8')
    if 'study_phases' not in text:
        declaration.write_text(text + 'flow: {study_phases: true}\n', encoding='utf-8')


def test_stage_order_runs_write_gate_before_scientific_publication(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    for phase in ('prepare', 'execute', 'collect', 'summarize', 'write'):
        body = f'def run(ctx):\n    ctx.state.setdefault("hooks", []).append("{phase}")\n'
        if phase == 'summarize':
            body += '    ctx.state["result_draft"]["hooks"] = ctx.state["hooks"]\n'
        if phase == 'write':
            body += '    assert not ctx.spec.result_path.exists()\n'
        _hook(source_study, phase, body)
    science_worker(science_factory())
    outcome = run(plan(source_study, run_root=run_root)[0])
    assert outcome.status == 'succeeded'
    assert outcome.result['hooks'] == ['prepare', 'execute', 'collect', 'summarize', 'write']


def test_write_hook_failure_cannot_publish_success(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    _hook(source_study, 'write', 'def run(ctx):\n    raise ValueError("write gate failed")\n')
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    assert outcome.status == 'failed' and outcome.error['type'] == 'ValueError'
    assert not spec.result_path.exists()


@pytest.mark.parametrize('phase', ['prepare', 'execute', 'collect', 'summarize', 'write'])
@pytest.mark.parametrize('target', ['config', 'control'])
def test_study_hook_configuration_change_cannot_publish_success(
        source_study, run_root, science_factory, file_bytes, phase, target, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    _hook(source_study, phase, 'def run(ctx):\n    ctx.control.run.data.name = "changed"\n'
          if target == 'control' else
          'def run(ctx):\n    ctx.config["data"]["name"] = "changed"\n')
    before = file_bytes(source_study)
    spec = plan(source_study, run_root=run_root)[0]
    payload = science_factory()
    science_worker(payload)
    outcome = run(spec)
    assert outcome.status == 'failed' and outcome.error['type'] == 'SourceMismatch'
    assert payload.calls[-1] == 'close'
    assert not spec.result_path.exists()
    assert not (spec.run_dir / '.execution').exists()
    assert file_bytes(source_study) == before


def test_process_hook_failure_preserves_published_scientific_success(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.flow.run import run

    _hook(source_study, 'process', 'def run(ctx):\n    raise ValueError("derived processing failed")\n')
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    stored = spec.result_path.read_bytes()
    assert outcome.status == 'failed' and outcome.error['type'] == 'ValueError'
    result = read_run(spec.ref)
    assert result['status'] == outcome.status == 'failed'
    assert result['scientific_status'] == 'succeeded'
    assert result['derived_status'] == 'failed'
    assert outcome.scientific_status == 'succeeded' and outcome.derived_status == 'failed'
    assert outcome.result['status'] == 'succeeded'
    from rpipe.structure.control import AdmissionError
    science_worker(science_factory())
    _rejected = run(spec, retry=True)
    assert _rejected.status == 'rejected'
    assert _rejected.error['type'] == 'AdmissionError'
    assert spec.result_path.read_bytes() == stored


def test_formal_result_is_written_once_after_worker_close_under_current_ownership(
        source_study, run_root, science_factory, science_worker, monkeypatch):
    from rpipe.flow import stages
    from rpipe.flow.run import run
    from rpipe.structure.artifact import io
    from rpipe.structure.make import plan

    spec = plan(source_study, run_root=run_root)[0]
    actions = science_factory()
    science_worker(actions)
    write, publish = io.write_json, stages.write
    formal, stages_seen = [], []
    def observed_stage(ctx, record, draft=None):
        ctx.owner.verify()
        stages_seen.append(record['phase'])
        if draft is not None:
            assert actions.calls[-1] == 'close'
            assert ctx.state['science_closed']
            assert ctx.state['worker_response_verified']
            assert ctx.state['child'].poll() is not None
        return publish(ctx, record, draft)
    def observed_write(path, record):
        if path == spec.result_path:
            assert stages_seen[-1] == 'closing'
            formal.append(record)
        return write(path, record)
    monkeypatch.setattr(stages, 'write', observed_stage)
    monkeypatch.setattr(io, 'write_json', observed_write)
    outcome = run(spec)
    assert outcome.status == 'succeeded'
    assert len(formal) == 1
    assert formal[0]['attempt'] == outcome.attempt


@pytest.mark.parametrize('failure', ['execute', 'cancel', 'close'])
def test_owned_failure_cancel_and_unknown_use_the_same_formal_writer(
        source_study, run_root, science_factory, science_worker, monkeypatch, failure):
    from rpipe.flow import stages
    from rpipe.flow.context import Cancellation, Cancelled
    from rpipe.flow.run import run
    from rpipe.structure.artifact import io
    from rpipe.structure.make import plan

    token = Cancellation()
    actions = science_factory(fail='execute' if failure == 'execute' else None,
                              close_error='resource closure unconfirmed' if failure == 'close' else None)
    if failure == 'cancel':
        def cancel(ctx):
            token.request()
            raise Cancelled('scientific boundary observed cancellation')
        actions.execute = cancel
    science_worker(actions)
    phases, write = [], stages.write
    def observed(ctx, record, draft=None):
        ctx.owner.verify()
        phases.append((record['phase'], record['candidate_status']))
        assert draft is None
        return write(ctx, record, draft)
    monkeypatch.setattr(stages, 'write', observed)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, cancellation=token)
    expected = {'execute': 'failed', 'cancel': 'cancelled', 'close': 'unknown'}[failure]
    assert outcome.status == expected
    assert phases[0] == ('running', None)
    assert phases[-1] == ('closing', expected)
    assert io.read_json(spec.run_dir / 'execution.json')['candidate_status'] == expected
    assert not spec.result_path.exists()
    assert (spec.run_dir / '.execution').exists() is (failure == 'close')


def test_prepare_builds_runtime_system_logger_and_research_hook_before_scientific_parts(
        source_study, run_root, monkeypatch):
    from types import SimpleNamespace
    import importlib
    from rpipe.flow import stages
    from rpipe.flow.context import Cancellation, ScienceContext
    from rpipe.structure.artifact.io import ArtifactLayout
    from rpipe.structure.make import plan

    events = []
    system_module = importlib.import_module('rpipe.structure.system.factory')
    class System:
        def __init__(self, *, logger, **kwargs):
            assert logger is None
            self.__dict__.update(kwargs)
            events.append('system')
    def logger(*args, **kwargs):
        events.append('logger')
        return SimpleNamespace(bind=lambda **kwargs: None, info=lambda *args, **kwargs: None,
                               close=lambda: None, diagnostics_complete=True)
    def runtime(seed, config):
        events.append('runtime')
        return {'seed': seed}
    data = SimpleNamespace(meta={'data_size': [2], 'target_size': 2}, close=lambda: None)
    def build_data(*args, **kwargs):
        events.append('data')
        return data
    def build_model(config, path, *, data_meta, system, origin=None):
        assert data_meta == data.meta
        assert isinstance(system, System)
        events.append('model')
        return SimpleNamespace(module=None, close=lambda: None)
    def build_algorithm(config, *, checkpoint_dir, train_checkpoint_dir=None):
        assert checkpoint_dir == ctx.spec.assets_dir / 'checkpoints'
        assert train_checkpoint_dir is None
        events.append('algorithm')
        return SimpleNamespace(mode=config.mode, close=lambda: None)
    def before(source, phase, ctx, study, *, entry='run'):
        assert phase == 'prepare' and entry == 'before'
        assert isinstance(ctx.state['system'], System) and ctx.state['logger'] is not None
        assert 'data' not in ctx.state and 'model' not in ctx.state
        events.append('before')
    monkeypatch.setattr(system_module, 'System', System)
    monkeypatch.setattr(system_module, 'Logger', logger)
    monkeypatch.setattr('rpipe.structure.system.runtime.apply_runtime', runtime)
    monkeypatch.setattr('rpipe.structure.data.factory.build_data', build_data)
    monkeypatch.setattr('rpipe.structure.model.factory.build_model', build_model)
    monkeypatch.setattr('rpipe.structure.algorithm.factory.build_algorithm', build_algorithm)
    monkeypatch.setattr('rpipe.structure.algorithm.tracker.make_tracker', lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(stages, 'run_study_phase', before)
    spec = plan(source_study, run_root=run_root)[0]
    ctx = ScienceContext(spec, lambda: None, ArtifactLayout(spec))
    ctx.state['attempt'] = 'a' * 32
    stages.configure_science(ctx, logging_context={}, request_id='b' * 32)
    assert 'logger' not in ctx.state
    assert not spec.run_dir.exists()
    try:
        stages.prepare(ctx)
        assert events == ['runtime', 'system', 'logger', 'before', 'data', 'model', 'algorithm']
    finally:
        stages.close_science(ctx)


def test_execute_injects_a_read_only_cancel_check_without_configuration_validation(
        source_study, run_root, monkeypatch):
    from types import SimpleNamespace
    from rpipe.flow import stages
    from rpipe.flow.context import Cancellation, Cancelled, ScienceContext
    from rpipe.structure.artifact.io import ArtifactLayout
    from rpipe.structure.make import plan

    spec = plan(source_study, run_root=run_root)[0]
    cancellation = Cancellation()
    def check_cancelled():
        if cancellation.is_set():
            raise Cancelled('requested by caller')
    ctx = ScienceContext(spec, check_cancelled, ArtifactLayout(spec))
    checked = []
    def compute(data, model, system, tracker, cancel_check):
        assert data is ctx.state['data'] and model is ctx.state['model']
        cancel_check()
        checked.append(True)
        cancellation.request()
        cancel_check()
        pytest.fail('requested cancellation was ignored')
    ctx.state.update(data=object(), model=object(), system=object(),
                     algorithm=SimpleNamespace(compute=compute),
                     tracker=SimpleNamespace(flush_state=lambda: None))
    monkeypatch.setattr(ctx, 'validate_config', lambda *args, **kwargs: pytest.fail('batch cancellation revalidated configuration'))
    with pytest.raises(Cancelled):
        stages.execute(ctx)
    assert checked == [True]
