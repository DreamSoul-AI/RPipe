from pathlib import Path

import pytest

from rpipe.flow.cli import run_study
from rpipe.flow.process.study import run_study as process_study
from rpipe.structure.artifact import load_config, load_index, write_config, write_result
from rpipe.structure.artifact.provenance import load_provenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.flow_layer,
    pytest.mark.module_cli,
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.mark.cost(cost_class='c1')
def test_nested_cpu_device_make_and_run_one_agree(tmp_path, monkeypatch):
    """CLI make → saved plan → Run prepare/write: nested CPU setting bypasses GPU probe."""
    import json
    import rpipe.flow.cli as cli

    study = tmp_path / 'nested_cpu'
    study.mkdir()
    (study / 'study.yaml').write_text('study: nested_cpu\nseeds: [0]\n', encoding='utf-8')
    (study / 'experiment_config.yaml').write_text(
        'version: nested-cpu-v1\ndata: {name: Toy, source: stub}\nmodel: {name: linear}\n'
        'algorithm: {mode: train, config: {num_steps: 2}}\n'
        'system: {device: cuda, config: {device: cpu, vram_bytes: 4294967296}}\n', encoding='utf-8')

    def forbidden(*args):
        raise AssertionError('CPU config must not probe GPU capacity')

    monkeypatch.setattr(cli, 'probe_gpus', forbidden)
    assert cli.main(['make', str(study), '--num-gpus', '1']) == 0
    plan = json.loads((study / 'scripts/jobs.json').read_text(encoding='utf-8'))
    job = plan['jobs'][0]
    assert job['device'] == 'cpu' and 'gpu' not in job
    assert job['vram_bytes'] == 4294967296
    assert cli.main(['run-one', str(study), job['run_id']]) == 0
    result = json.loads((study / 'runs' / job['run_id'] / 'result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'succeeded'
    assert result['structure']['system']['device'] == 'cpu'


@pytest.mark.external
@pytest.mark.cost(cost_class='c2')
def test_run_study_skip_launch_index_groups_train_eval_experiments(tmp_path: Path):
    """run_study with skip_launch writes an index grouped by train_size and mode."""
    import shutil

    repo = Path(__file__).resolve().parents[3]
    src = repo / 'tests' / '_data' / 'studies' / 'mnist_train_size'
    study = tmp_path / 'mnist_train_size'
    shutil.copytree(
        src,
        study,
        ignore=shutil.ignore_patterns('runs', 'shared', 'index.json', '__pycache__'),
    )
    out = run_study(study, skip_launch=True)
    index = load_index(study)
    assert out['index'].is_file()
    assert len(out['configs']) == 18
    assert len(index['experiments']) == 6
    sizes = [exp['factors']['data.config.train_size'] for exp in index['experiments']]
    modes = [exp['factors']['algorithm.mode'] for exp in index['experiments']]
    assert sizes == [500, 500, 2000, 2000, 8000, 8000]
    assert modes == ['train', 'eval', 'train', 'eval', 'train', 'eval']
    for exp in index['experiments']:
        assert [r['seed'] for r in exp['runs']] == [0, 1, 2]
        for run in exp['runs']:
            assert run['log'] == f"runs/{run['id']}/assets/logs/run.log"
    assert (study / 'docs').is_dir()
    assert (study / 'shared' / 'data').is_dir()
    assert (study / 'runs').is_dir()


@pytest.mark.cost(cost_class='c1')
def test_run_study_new_version_replaces_index_and_preserves_old_run(tmp_path: Path):
    """Local YAML → skip-launch → Study process aggregates only the new version and preserves old Run bytes."""
    study = tmp_path / 'versioned'
    study.mkdir()
    write_config(
        study / 'experiment_config.yaml',
        {'version': 'base', 'data': {'name': 'Toy', 'source': 'stub'}},
    )
    declaration = {'study': 'versioned', 'seeds': [0], 'fixed': {'version': 'v1'}}
    write_config(study / 'study.yaml', declaration)
    first = run_study(study, skip_launch=True)
    old_config = first['configs'][0]
    old_body = load_config(old_config)
    assert old_body['version'] == 'v1'
    old_result = write_result(
        old_config.parent / 'result.json',
        {'status': 'succeeded', 'control': old_body, 'metrics': {'accuracy': 80.0}, 'paths': {},
         'provenance': load_provenance(study)['digest']},
    )
    old_bytes = (old_config.read_bytes(), old_result.read_bytes())

    declaration['fixed']['version'] = 'v2'
    write_config(study / 'study.yaml', declaration)
    second = run_study(study, skip_launch=True)
    new_config = second['configs'][0]
    new_body = load_config(new_config)
    index = load_index(study)

    assert new_body['version'] == 'v2'
    assert new_body['id'] != old_body['id']
    assert new_config.parent == study / 'runs' / new_body['id']
    assert len(index['experiments']) == 1
    assert [run['id'] for run in index['experiments'][0]['runs']] == [new_body['id']]
    assert (old_config.read_bytes(), old_result.read_bytes()) == old_bytes
    assert not (new_config.parent / 'result.json').exists()

    pending = process_study(study)
    assert pending['complete'] is False
    assert pending['experiments'][0]['n'] == 0

    write_result(
        new_config.parent / 'result.json',
        {'status': 'succeeded', 'control': new_body, 'metrics': {'accuracy': 20.0}, 'paths': {},
         'provenance': load_provenance(study)['digest']},
    )
    complete = process_study(study)
    current = complete['experiments'][0]
    assert complete['complete'] is True
    assert current['n'] == current['n_planned'] == 1
    assert current['metrics']['accuracy']['mean'] == 20.0
    assert (old_config.read_bytes(), old_result.read_bytes()) == old_bytes
