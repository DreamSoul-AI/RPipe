"""Managed Study make/reuse boundaries through public CPU entry points."""

import json

import pytest

from rpipe.structure.artifact.provenance import ProvenanceChangedError, load_provenance
from rpipe.structure.artifact import load_index, write_result
from rpipe.structure.make import expand_study, plan_jobs, run_succeeded
from rpipe.flow.process.aggregate import collect_from_index

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_make,
              pytest.mark.cost(cost_class='c1'), pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def managed(tmp_path, monkeypatch):
    from rpipe.structure.artifact import provenance

    monkeypatch.setattr(provenance, 'capture_environment', lambda: {'device': 'cpu'})
    monkeypatch.setattr(provenance, 'capture_git', lambda root: {})
    study = tmp_path / 'study'
    study.mkdir()
    (study / 'study.yaml').write_text(
        'study: reuse\nseeds: [0]\nflow: {prepare_shared: false}\n', encoding='utf-8')
    (study / 'experiment_config.yaml').write_text(
        'experiment: reuse\nversion: v0\ndata: {name: Toy, source: stub}\n'
        'model: {name: linear}\nalgorithm: {mode: train, num_steps: 1}\n'
        'system: {device: cpu}\n', encoding='utf-8')
    (study / 'structure').mkdir()
    (study / 'structure/helper.py').write_text('VALUE = 0\n', encoding='utf-8')
    return study


def _success(study, config):
    path = config.parent / 'result.json'
    write_result(path, {'status': 'succeeded', 'control': {}, 'paths': {},
                        'metrics': {'score': 12}, 'provenance': load_provenance(study)['digest']})
    return path


def _bytes(study):
    return {p.relative_to(study).as_posix(): p.read_bytes() for p in study.rglob('*') if p.is_file()}


def _change(study, name, old, new):
    path = study / name
    path.write_text(path.read_text(encoding='utf-8').replace(old, new), encoding='utf-8')


@pytest.mark.parametrize('entry', ['expand', 'cli'])
def test_source_change_refuses_before_recipe_or_any_publication(managed, entry):
    from rpipe.flow import cli

    study = managed
    (study / 'recipe.py').write_text(
        'from pathlib import Path\n'
        'p = Path(__file__).parent / "recipe-executed"\n'
        'p.write_text("executed")\n'
        'def register(ctx):\n    pass\n', encoding='utf-8')
    with (study / 'study.yaml').open('a', encoding='utf-8') as out:
        out.write('recipe: recipe.py\n')
    first = expand_study(study)
    result = _success(study, first['configs'][0])
    (study / 'recipe-executed').unlink()
    _change(study, 'structure/helper.py', '0', '1')
    before = _bytes(study)
    with pytest.raises(ProvenanceChangedError, match='new version'):
        expand_study(study) if entry == 'expand' else cli.main(['make', str(study)])
    assert _bytes(study) == before
    assert result.read_bytes() == before[result.relative_to(study).as_posix()]


def test_old_success_cannot_reenter_after_disjoint_make(managed):
    first = expand_study(managed)
    old = first['configs'][0]
    result = _success(managed, old)
    raw = result.read_bytes()
    _change(managed, 'structure/helper.py', '0', '1')
    _change(managed, 'study.yaml', '[0]', '[1]')
    second = expand_study(managed)
    assert second['configs'][0].parent != old.parent
    assert load_provenance(managed)['compatible_digests'] == []
    _change(managed, 'study.yaml', '[1]', '[0]')
    before = _bytes(managed)
    with pytest.raises(ProvenanceChangedError, match='binding'):
        expand_study(managed)
    assert _bytes(managed) == before
    assert result.read_bytes() == raw


@pytest.mark.parametrize('change', ['study-description', 'run-description', 'base-description', 'yaml', 'readme'])
def test_metadata_and_format_keep_success_binding(managed, change):
    first = expand_study(managed)
    config = first['configs'][0]
    result = _success(managed, config)
    raw = result.read_bytes()
    digest = load_provenance(managed)['digest']
    if change in ('study-description', 'run-description'):
        with (managed / 'study.yaml').open('a', encoding='utf-8') as out:
            out.write('description: explained\n' if change == 'study-description' else 'run_description: explained {seed}\n')
    elif change == 'base-description':
        with (managed / 'experiment_config.yaml').open('a', encoding='utf-8') as out:
            out.write('description: explained\n')
    elif change == 'yaml':
        _change(managed, 'study.yaml', 'seeds: [0]', 'seeds:\n  - 0 # same seed')
        _change(managed, 'experiment_config.yaml', 'version: v0', 'version: "v0" # same version')
    else:
        (managed / 'README.md').write_text('Documentation only\n', encoding='utf-8')
    second = expand_study(managed)
    assert second['configs'][0].parent == config.parent
    assert digest in load_provenance(managed)['compatible_digests']
    assert result.read_bytes() == raw
    assert run_succeeded(managed, config.parent.name)
    assert plan_jobs(managed, second['configs'], init_gpu=0, num_gpus=0) == []
    groups = collect_from_index(managed, load_index(managed))
    assert groups[0]['runs'][0]['metrics'] == {'score': 12.0}


def test_seed_extension_retains_compatible_lineage_across_multiple_makes(managed):
    first = expand_study(managed)
    old = first['configs'][0]
    result = _success(managed, old)
    raw = result.read_bytes()
    digest = load_provenance(managed)['digest']
    _change(managed, 'study.yaml', '[0]', '[0, 1]')
    second = expand_study(managed)
    _change(managed, 'study.yaml', '[0, 1]', '[0, 1, 2]')
    third = expand_study(managed)
    assert len(second['configs']) == 2 and len(third['configs']) == 3
    assert digest in load_provenance(managed)['compatible_digests']
    assert result.read_bytes() == raw
    jobs = plan_jobs(managed, third['configs'], init_gpu=0, num_gpus=0)
    assert len(jobs) == 2 and all(job['run_id'] != old.parent.name for job in jobs)


@pytest.mark.parametrize('change', ['matching', 'description', 'new-version'])
def test_historical_manifest_only_upgrades_with_sha_proof_or_disjoint_ids(managed, change):
    first = expand_study(managed)
    old = first['configs'][0]
    result = _success(managed, old)
    raw = result.read_bytes()
    path = managed / 'provenance.json'
    saved = load_provenance(managed)
    saved.pop('declarations')
    saved.pop('compatible_digests')
    path.write_text(json.dumps(saved), encoding='utf-8')
    if change == 'description':
        with (managed / 'study.yaml').open('a', encoding='utf-8') as out:
            out.write('description: historically unknown\n')
        before = _bytes(managed)
        with pytest.raises(ProvenanceChangedError, match='historical declaration'):
            expand_study(managed)
        assert _bytes(managed) == before
    else:
        if change == 'new-version':
            _change(managed, 'experiment_config.yaml', 'v0', 'v1')
        second = expand_study(managed)
        assert 'declarations' in load_provenance(managed)
        assert (second['configs'][0].parent == old.parent) == (change == 'matching')
    assert result.read_bytes() == raw


@pytest.mark.parametrize('damage', ['missing-manifest', 'missing-binding', 'unknown-binding', 'source', 'snapshot', 'unmade-config'])
@pytest.mark.parametrize('reader', ['schedule', 'aggregate', 'launch'])
def test_managed_unknown_success_is_refused_without_overwrite(managed, monkeypatch, damage, reader):
    from rpipe.structure.make.schedule import launch_jobs

    first = expand_study(managed)
    config = first['configs'][0]
    result = _success(managed, config)
    if damage == 'missing-manifest':
        (managed / 'provenance.json').unlink()
    elif damage == 'source':
        _change(managed, 'structure/helper.py', '0', '1')
    elif damage == 'unmade-config':
        _change(managed, 'experiment_config.yaml', 'num_steps: 1', 'num_steps: 2')
    elif damage == 'snapshot':
        path = managed / 'provenance.json'
        body = load_provenance(managed)
        body['declarations']['study.yaml'] += '# tampered snapshot\n'
        path.write_text(json.dumps(body), encoding='utf-8')
    else:
        body = json.loads(result.read_text(encoding='utf-8'))
        body.pop('provenance')
        if damage == 'unknown-binding':
            body['provenance'] = 'f' * 16
        write_result(result, body)
    before = _bytes(managed)
    def no_child(*args, **kwargs):
        raise AssertionError('refusal must precede child launch')
    monkeypatch.setattr('rpipe.structure.make.schedule.subprocess.Popen', no_child)
    with pytest.raises(ProvenanceChangedError):
        if reader == 'schedule':
            run_succeeded(managed, config.parent.name)
        elif reader == 'aggregate':
            collect_from_index(managed, load_index(managed))
        else:
            launch_jobs(managed, [{'run_id': config.parent.name, 'mode': 'train', 'device': 'cpu'}])
    assert _bytes(managed) == before


def test_new_version_preserves_old_result_under_changed_computation(managed):
    first = expand_study(managed)
    result = _success(managed, first['configs'][0])
    raw = result.read_bytes()
    _change(managed, 'structure/helper.py', '0', '1')
    _change(managed, 'experiment_config.yaml', 'v0', 'v1')
    second = expand_study(managed)
    assert second['configs'][0].parent != first['configs'][0].parent
    assert result.read_bytes() == raw
    assert len(plan_jobs(managed, second['configs'], init_gpu=0, num_gpus=0)) == 1


@pytest.mark.parametrize('entry', ['expand', 'cli', 'configs'])
@pytest.mark.parametrize('busy', [True, False], ids=['busy', 'idle'])
def test_make_preserves_all_run_configs_while_an_expanded_run_is_owned(managed, entry, busy):
    """Public make/config entry -> busy last Run refuses before publishing any plan bytes."""
    import contextlib
    from rpipe.flow import cli
    from rpipe.structure.artifact.execution import RunLockedError, run_lock
    from rpipe.structure.make import expand_patches, load_study_yaml, write_run_configs

    _change(managed, 'study.yaml', '[0]', '[0, 1]')
    configs = expand_study(managed)['configs']
    with (managed / 'study.yaml').open('a', encoding='utf-8') as out:
        out.write('run_description: refreshed metadata {seed}\n')
    for config in configs:
        with run_lock(config.parent):
            pass
    with run_lock(configs[-1].parent) if busy else contextlib.nullcontext():
        paths = [p for p in managed.rglob('*') if p.is_file() and p.name != '.rpipe.lock']
        before = {p: p.read_bytes() for p in paths}
        error = None
        try:
            if entry == 'configs':
                write_run_configs(managed, expand_patches(load_study_yaml(managed)))
            elif entry == 'cli':
                cli.main(['make', str(managed), '--num-gpus', '0'])
            else:
                expand_study(managed)
        except RunLockedError as exc:
            error = exc
        if busy:
            assert {p: p.read_bytes() for p in managed.rglob('*')
                    if p.is_file() and p.name != '.rpipe.lock'} == before
            assert error is not None
        else:
            assert error is None
            assert all(b'refreshed metadata' in config.read_bytes() for config in configs)

    for config in configs:
        with run_lock(config.parent):
            pass


@pytest.mark.parametrize('entry', ['expand', 'configs'])
@pytest.mark.parametrize('writer_failure', [False, True], ids=['publish', 'failure'])
def test_make_prevents_runner_admission_until_its_writers_finish(managed, monkeypatch, entry, writer_failure):
    """Make writer -> competing public Runner refuses, including before final provenance publication."""
    from rpipe.flow import FlowContext, FlowRunner
    from rpipe.structure.artifact import artifact_layout
    from rpipe.structure.artifact.execution import RunLockedError, run_lock
    from rpipe.structure.make import expand_patches, load_study_yaml, write_run_configs
    import rpipe.structure.make.write as write

    _change(managed, 'study.yaml', '[0]', '[0, 1]')
    configs = expand_study(managed)['configs']
    observed = []
    error = PermissionError('make writer denied')
    def guard(name, original):
        def publish(*args, **kwargs):
            for config in configs:
                ctx = FlowContext(managed, artifact_layout(managed, config.parent.name), {})
                with pytest.raises(RunLockedError):
                    FlowRunner(['collect']).run(ctx)
                assert not (config.parent / 'execution.json').exists()
            observed.append(name)
            if writer_failure and name == ('provenance' if entry == 'expand' else 'config'):
                raise error
            return original(*args, **kwargs)
        return publish
    for name, attribute in [('config', 'write_config'), ('index', 'write_index'), ('provenance', 'write_provenance')]:
        monkeypatch.setattr(write, attribute, guard(name, getattr(write, attribute)))
    def make():
        return expand_study(managed) if entry == 'expand' else write_run_configs(
            managed, expand_patches(load_study_yaml(managed)))
    if writer_failure:
        with pytest.raises(PermissionError) as caught:
            make()
        assert caught.value is error
    else:
        make()
    assert observed[-1] == ('provenance' if entry == 'expand' else 'config')
    for config in configs:
        with run_lock(config.parent):
            pass
