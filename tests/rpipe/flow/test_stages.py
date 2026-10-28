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


def test_stage_order_runs_write_gate_before_scientific_publication(source_study, run_root, payload_factory):
    from rpipe.structure.make import plan
    from rpipe.api import run

    for phase in ('prepare', 'execute', 'collect', 'summarize', 'write'):
        body = f'def run(ctx):\n    ctx.state.setdefault("hooks", []).append("{phase}")\n'
        if phase == 'summarize':
            body += '    ctx.state["result_draft"]["hooks"] = ctx.state["hooks"]\n'
        if phase == 'write':
            body += '    assert not ctx.spec.result_path.exists()\n'
        _hook(source_study, phase, body)
    outcome = run(plan(source_study, run_root=run_root)[0], payload=payload_factory())
    assert outcome.status == 'succeeded'
    assert outcome.result['hooks'] == ['prepare', 'execute', 'collect', 'summarize', 'write']


def test_write_hook_failure_cannot_publish_success(source_study, run_root, payload_factory):
    from rpipe.structure.make import plan
    from rpipe.api import run

    _hook(source_study, 'write', 'def run(ctx):\n    raise ValueError("write gate failed")\n')
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=payload_factory())
    assert outcome.status == 'failed' and outcome.error['type'] == 'ValueError'
    assert not spec.result_path.exists()


@pytest.mark.parametrize('phase', ['prepare', 'execute', 'collect', 'summarize', 'write'])
@pytest.mark.parametrize('target', ['config', 'control'])
def test_study_hook_configuration_change_cannot_publish_success(
        source_study, run_root, payload_factory, file_bytes, phase, target):
    from rpipe.structure.make import plan
    from rpipe.api import run

    _hook(source_study, phase, 'def run(ctx):\n    ctx.control.run.data.name = "changed"\n'
          if target == 'control' else
          'def run(ctx):\n    ctx.config["data"]["name"] = "changed"\n')
    before = file_bytes(source_study)
    spec = plan(source_study, run_root=run_root)[0]
    payload = payload_factory()
    outcome = run(spec, payload=payload)
    assert outcome.status == 'failed' and outcome.error['type'] == 'SourceMismatch'
    assert payload.calls[-1] == 'close'
    assert not spec.result_path.exists()
    assert not (spec.run_dir / '.execution').exists()
    assert file_bytes(source_study) == before


def test_process_hook_failure_preserves_published_scientific_success(source_study, run_root, payload_factory):
    from rpipe.structure.make import plan
    from rpipe.api import read_run, run

    _hook(source_study, 'process', 'def run(ctx):\n    raise ValueError("derived processing failed")\n')
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=payload_factory())
    stored = spec.result_path.read_bytes()
    assert outcome.status == 'failed' and outcome.error['type'] == 'ValueError'
    result = read_run(spec)
    assert result['status'] == outcome.status == 'failed'
    assert result['scientific_status'] == 'succeeded'
    assert result['derived_status'] == 'failed'
    assert outcome.scientific_status == 'succeeded' and outcome.derived_status == 'failed'
    assert outcome.result['status'] == 'succeeded'
    from rpipe.structure.control import AdmissionError
    with pytest.raises(AdmissionError, match='immutable'):
        run(spec, payload=payload_factory(), retry=True)
    assert spec.result_path.read_bytes() == stored
