"""Native integration retains hook timing and isolates each Study's registration."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_native_prepare_before_runs_once_with_system_ready(tensor_study, run_root):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.flow import stages

    with (tensor_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('flow: {study_phases: true}\n')
    flow = tensor_study / 'flow'
    flow.mkdir()
    (flow / 'prepare.py').write_text('''def before(ctx):
    assert ctx.state['system'] is not None
    assert 'data' not in ctx.state
    ctx.state['before_count'] = ctx.state.get('before_count', 0) + 1
def run(ctx):
    assert 'model' in ctx.state and 'data' in ctx.state
''', encoding='utf-8')
    (flow / 'summarize.py').write_text('''def run(ctx):
    ctx.state['result_draft']['before_count'] = ctx.state['before_count']
''', encoding='utf-8')
    outcome = run(plan(tensor_study, run_root=run_root)[0])
    assert outcome.status == 'succeeded', outcome.error
    assert outcome.result['before_count'] == 1


def test_sequential_native_studies_do_not_share_recipe_builders(
        tensor_study, run_root, monkeypatch):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.data.factory import DataRegistry
    from rpipe.flow import stages

    monkeypatch.setattr(DataRegistry, '_items', dict(DataRegistry._items))
    name = 'RunScopedFixture'
    for filename in ('experiment_config.yaml', 'recipe.py'):
        path = tensor_study / filename
        path.write_text(path.read_text().replace('TinyCPU', name), encoding='utf-8')
    assert DataRegistry.get(name, 'fixture') is None
    assert run(plan(tensor_study, run_root=run_root)[0]).status == 'succeeded'
    other = tensor_study.parent / 'other'
    other.mkdir()
    (other / 'study.yaml').write_text('study: no_recipe\nseeds: [7]\n', encoding='utf-8')
    (other / 'experiment_config.yaml').write_bytes((tensor_study / 'experiment_config.yaml').read_bytes())
    outcome = run(plan(other, run_root=run_root)[0])
    assert outcome.status == 'failed'
    assert outcome.error['type'] == 'ValueError'
    assert outcome.error['message'] == '[redacted]'
    assert DataRegistry.get(name, 'fixture') is None


@pytest.mark.parametrize('explicit_stats', [False, True])
def test_native_profile_output_is_external_and_explicit_stats_resolve_from_source(
        source_study, run_root, file_bytes, monkeypatch, explicit_stats):
    import yaml
    from rpipe.structure.make import plan
    from rpipe.structure.data.factory import Data
    from rpipe.flow import stages
    from rpipe.flow.context import Cancellation
    from rpipe.flow.context import ScienceContext

    path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(path.read_text())
    config['data'] = {'name': 'MNIST', 'source': 'torch'}
    stats = source_study / 'normalization.yaml'
    if explicit_stats:
        stats.write_text('mean: [0.5]\nstd: [0.5]\n', encoding='utf-8')
        config['data']['config'] = {'stats_path': 'normalization.yaml'}
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    before = file_bytes(source_study)
    spec = plan(source_study, run_root=run_root)[0]
    calls = []

    def build(data_config, assets, **kwargs):
        calls.append((data_config, assets, kwargs['profile_path']))
        return Data(name='MNIST', source='fixture', loaders={},
                    meta={'data_size': [1, 28, 28], 'target_size': 10})

    monkeypatch.setattr('rpipe.structure.data.factory.build_data', build)
    from rpipe.structure.artifact.io import ArtifactLayout
    ctx = ScienceContext(spec=spec, cancel_check=lambda: None, layout=ArtifactLayout(spec))
    ctx.state['attempt'] = 'a' * 32
    stages.configure_science(ctx, logging_context={}, request_id=None)
    try:
        stages.prepare(ctx)
        data_config, assets, profile = calls[0]
        assert assets == run_root / 'shared' / 'data'
        assert profile == spec.study_root / 'shared' / 'data' / 'mnist' / 'stats.yaml'
        if explicit_stats:
            assert data_config.config['stats_path'] == str(stats)
        assert file_bytes(source_study) == before
    finally:
        stages.close_science(ctx)
