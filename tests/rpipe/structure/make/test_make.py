import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_make,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

import ast
from pathlib import Path

from rpipe.structure.make import (
    expand_patches,
    filter_jobs_by_mode,
    gpu_ids,
    load_launch_plan,
    load_study_yaml,
    plan_jobs,
    render_bash,
    run_succeeded,
    write_launch_scripts,
)
from rpipe.structure.artifact import artifact_layout


def _touch_config(study: Path, run_id: str, *, device: str = 'cuda') -> Path:
    layout = artifact_layout(study, run_id)
    layout.config_path.write_text(
        'id: {}\nsystem:\n  device: {}\n'.format(run_id, device),
        encoding='utf-8',
    )
    return layout.config_path


def _write_succeeded(study: Path, run_id: str) -> None:
    layout = artifact_layout(study, run_id)
    layout.result_path.write_text(
        '{"status": "succeeded", "control": {}, "metrics": {}, "paths": {}}\n',
        encoding='utf-8',
    )


def test_expand_patches_train_size_baseline():
    study = {
        'study': 'demo',
        'experiment': {'name': 'mnist_linear'},
        'fixed': {'seed': 0, 'algorithm': {'mode': 'train', 'lr': 0.1}},
        'axes': {'data.config.train_size': [500, 2000]},
        'tags': [{'when': {'data.config.train_size': 500}, 'tags': ['baseline']}],
        'run_description': 'size={train_size}',
    }
    patches = expand_patches(study)
    assert len(patches) == 2
    assert patches[0]['tags'] == ['baseline']
    assert patches[0]['data']['config']['train_size'] == 500
    assert 'tags' not in patches[1]
    assert patches[0]['description'] == 'size=500'


def test_duplicate_expansion_is_rejected_before_writing(tmp_path):
    from rpipe.structure.make import expand_study
    (tmp_path / 'study.yaml').write_text('study: duplicate\nseeds: [0, 0]\n', encoding='utf-8')
    (tmp_path / 'experiment_config.yaml').write_text('version: duplicate\n', encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate Run'):
        expand_study(tmp_path)
    assert not list((tmp_path / 'runs').glob('*/config.yaml'))
    assert not (tmp_path / 'index.json').exists()


def test_plan_jobs_trains_before_eval(tmp_path: Path):
    study = tmp_path / 'study'
    eval_path = artifact_layout(study, 'e1').config_path
    eval_path.write_text('algorithm:\n  mode: eval\n', encoding='utf-8')
    train_path = artifact_layout(study, 't1').config_path
    train_path.write_text('algorithm:\n  mode: train\n', encoding='utf-8')
    jobs = plan_jobs(study, [eval_path, train_path], init_gpu=0, num_gpus=1)
    assert [j['run_id'] for j in jobs] == ['t1', 'e1']
    assert [j['mode'] for j in jobs] == ['train', 'eval']


def test_render_bash_waits_after_train_wave(tmp_path: Path):
    study = tmp_path / 'study'
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    jobs = [
        {'run_id': 't0', 'gpu': '0', 'mode': 'train'},
        {'run_id': 't1', 'gpu': '0', 'mode': 'train'},
        {'run_id': 'e0', 'gpu': '0', 'mode': 'eval'},
        {'run_id': 'e1', 'gpu': '0', 'mode': 'eval'},
    ]
    text = render_bash(
        study,
        jobs,
        python_exe='/opt/python',
        round_size=4,
        split_round=65535,
    )[0]
    groups = ast.literal_eval(next(line.removeprefix('groups = ') for line in text.splitlines() if line.startswith('groups = ')))
    assert [[job['run_id'] for job in group] for group in groups] == [['t0', 't1'], ['e0', 'e1']]
    assert 'launch_jobs(study, jobs,' in text
    assert 'result = run_study(study)' in text
    assert gpu_ids(2, 3) == ['2', '3', '4']
    assert gpu_ids(0, 0) == []


def test_console_new_uses_windows_flag_only_on_nt():
    from rpipe.structure.make.schedule import job_popen_kwargs, resolve_console

    assert resolve_console('shared') == 'shared'
    assert resolve_console('new') == 'new'
    shared = job_popen_kwargs('shared')
    assert 'creationflags' not in shared
    flags = job_popen_kwargs('new')
    import os

    if os.name == 'nt':
        assert 'creationflags' in flags
    else:
        assert flags == {}


def test_plan_jobs_skips_succeeded_and_assigns_gpu(tmp_path: Path):
    study = tmp_path / 'study'
    done = _touch_config(study, 'done')
    _write_succeeded(study, 'done')
    pending = _touch_config(study, 'pend')
    jobs = plan_jobs(study, [done, pending], init_gpu=1, num_gpus=2)
    assert [j['run_id'] for j in jobs] == ['pend']
    assert jobs[0]['gpu'] == '1'
    assert run_succeeded(study, 'done')
    assert not run_succeeded(study, 'pend')


def test_plan_jobs_does_not_assign_gpu_to_cpu_run(tmp_path: Path):
    study = tmp_path / 'study'
    config = _touch_config(study, 'cpu-run', device='cpu')
    jobs = plan_jobs(study, [config], init_gpu=0, num_gpus=1)
    assert jobs == [
        {
            'run_id': 'cpu-run',
            'config': config.as_posix(),
            'mode': 'train',
            'device': 'cpu',
        }
    ]


def test_render_bash_wait_every_round(tmp_path: Path):
    study = tmp_path / 'study'
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    jobs = [{'run_id': f'r{i}', 'gpu': str(i % 2)} for i in range(4)]
    chunks = render_bash(
        study,
        jobs,
        python_exe='/opt/python',
        round_size=2,
        split_round=65535,
    )
    assert len(chunks) == 1
    text = chunks[0]
    groups = ast.literal_eval(next(line.removeprefix('groups = ') for line in text.splitlines() if line.startswith('groups = ')))
    assert [[job['run_id'] for job in group] for group in groups] == [['r0', 'r1'], ['r2', 'r3']]
    assert [job['gpu'] for group in groups for job in group] == ['0', '1', '0', '1']
    assert 'KMP_DUPLICATE_LIB_OK' not in text


def test_write_launch_scripts_split_round(tmp_path: Path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    study = tmp_path / 'study'
    jobs = [{'run_id': f'r{i}', 'gpu': '0'} for i in range(4)]
    written = write_launch_scripts(
        study,
        jobs,
        round_size=2,
        split_round=1,
        python_exe='/opt/python',
        init_gpu=0,
        num_gpus=1,
    )
    assert written['n_jobs'] == 4
    assert len(written['bash']) == 2
    assert written['jobs_json'].is_file()
    assert written['ps1'].is_file()
    ps1 = written['ps1'].read_text(encoding='utf-8')
    assert 'rpipe launch' in ps1
    assert 'KMP_DUPLICATE_LIB_OK' not in ps1


def test_launch_job_env_only_sets_cuda_for_gpu(monkeypatch):
    from rpipe.structure.make.schedule import launch_job_env

    monkeypatch.delenv('KMP_DUPLICATE_LIB_OK', raising=False)
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)
    cpu = launch_job_env({'run_id': 'cpu', 'device': 'cpu'})
    gpu = launch_job_env({'run_id': 'gpu', 'device': 'cuda', 'gpu': '2'})
    assert 'KMP_DUPLICATE_LIB_OK' not in cpu
    assert 'CUDA_VISIBLE_DEVICES' not in cpu
    assert gpu['CUDA_VISIBLE_DEVICES'] == '2'


def test_load_launch_plan_reuses_jobs_and_drops_succeeded(tmp_path: Path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    study = tmp_path / 'study'
    jobs = [
        {'run_id': 't0', 'gpu': '0', 'mode': 'train'},
        {'run_id': 't1', 'gpu': '0', 'mode': 'train'},
        {'run_id': 'e0', 'gpu': '0', 'mode': 'eval'},
    ]
    batches = [jobs[:2], jobs[2:]]
    write_launch_scripts(
        study,
        jobs,
        round_size=2,
        python_exe='/opt/python',
        init_gpu=0,
        num_gpus=1,
        batches=batches,
    )
    _write_succeeded(study, 't0')
    plan = load_launch_plan(study, init_gpu=0, num_gpus=1, round_size=0)
    assert plan is not None
    assert [j['run_id'] for j in plan['job_list']] == ['t1', 'e0']
    assert [j['run_id'] for j in plan['batches'][0]] == ['t1']
    assert [j['run_id'] for j in plan['batches'][1]] == ['e0']
    assert load_launch_plan(study, init_gpu=0, num_gpus=2, round_size=0) is None
    assert load_launch_plan(study, init_gpu=0, num_gpus=1, round_size=4) is None


def test_filter_jobs_by_mode_keeps_eval_only():
    jobs = [
        {'run_id': 't0', 'mode': 'train'},
        {'run_id': 'e0', 'mode': 'eval'},
        {'run_id': 't1'},
    ]
    assert [j['run_id'] for j in filter_jobs_by_mode(jobs, ['eval'])] == ['e0']
    assert [j['run_id'] for j in filter_jobs_by_mode(jobs, None)] == ['t0', 'e0', 't1']


def test_load_launch_plan_include_done_keeps_succeeded(tmp_path: Path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    study = tmp_path / 'study'
    jobs = [
        {'run_id': 't0', 'gpu': '0', 'mode': 'train'},
        {'run_id': 'e0', 'gpu': '0', 'mode': 'eval'},
    ]
    write_launch_scripts(
        study,
        jobs,
        round_size=2,
        python_exe='/opt/python',
        init_gpu=0,
        num_gpus=1,
        batches=[jobs[:1], jobs[1:]],
    )
    _write_succeeded(study, 't0')
    _write_succeeded(study, 'e0')
    skipped = load_launch_plan(study, init_gpu=0, num_gpus=1, round_size=0)
    assert skipped is not None
    assert skipped['job_list'] == []
    again = load_launch_plan(study, init_gpu=0, num_gpus=1, round_size=0, include_done=True)
    assert [j['run_id'] for j in again['job_list']] == ['t0', 'e0']
    assert [j['run_id'] for j in again['batches'][1]] == ['e0']


def test_launch_jobs_prints_each_wait_group(tmp_path: Path, monkeypatch, capsys):
    import rpipe.structure.make.schedule as schedule

    class _Proc:
        def wait(self) -> int:
            return 0

    monkeypatch.setattr(schedule.subprocess, 'Popen', lambda *_args, **_kwargs: _Proc())
    monkeypatch.setattr(schedule, 'run_succeeded', lambda *_args, **_kwargs: True)
    study = tmp_path / 'study'
    study.mkdir()
    jobs = [
        {'run_id': 't0', 'mode': 'train', 'device': 'cpu'},
        {'run_id': 'e0', 'mode': 'eval', 'device': 'cpu'},
    ]
    schedule.launch_jobs(
        study,
        jobs,
        round_size=1,
        batches=[jobs[:1], jobs[1:]],
        console='shared',
    )
    out = capsys.readouterr().out
    assert 'launch: wait 1/2 mode=train' in out
    assert 'launch: wait 2/2 mode=eval' in out


def test_launch_prints_status_counts_when_nothing_remains(tmp_path: Path, monkeypatch, capsys):
    from rpipe.flow import cli
    from rpipe.structure.artifact.index import write_index

    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    study = tmp_path / 'study'
    jobs = [{'run_id': 't0', 'gpu': '0', 'mode': 'train', 'device': 'cpu'}]
    write_launch_scripts(study, jobs, round_size=1, python_exe='/opt/python', init_gpu=0, num_gpus=1)
    _write_succeeded(study, 't0')
    write_index(
        study,
        {
            'study': 'study',
            'experiments': [
                {
                    'factors': {'algorithm.mode': 'train'},
                    'runs': [{'id': 't0', 'seed': 0, 'run_dir': 't0', 'log': 'runs/t0/assets/logs/run.log'}],
                }
            ],
        },
    )
    monkeypatch.setattr(cli, 'process_study', lambda *_args, **_kwargs: {'complete': True})
    monkeypatch.setattr(cli, 'process_path', lambda *_args, **_kwargs: study / 'process.json')
    assert cli.main(['launch', str(study), '--console', 'shared']) == 0
    out = capsys.readouterr().out
    assert 'nothing to launch' in out
    assert 'planned=1' in out
    assert 'succeeded=1' in out
    assert 'pending=0' in out


def test_launch_mode_eval_does_not_rewrite_jobs_json(tmp_path: Path, monkeypatch):
    from rpipe.flow import cli

    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "rpipe"\n', encoding='utf-8')
    study = tmp_path / 'study'
    jobs = [
        {'run_id': 't0', 'gpu': '0', 'mode': 'train'},
        {'run_id': 'e0', 'gpu': '0', 'mode': 'eval'},
    ]
    write_launch_scripts(
        study,
        jobs,
        round_size=2,
        python_exe='/opt/python',
        init_gpu=0,
        num_gpus=1,
        batches=[jobs[:1], jobs[1:]],
    )
    _write_succeeded(study, 't0')
    launched: list[str] = []

    def fake_launch(_study, job_list, **_kwargs):
        launched.extend(str(job['run_id']) for job in job_list)
        return [0]

    monkeypatch.setattr(cli, 'launch_jobs', fake_launch)
    monkeypatch.setattr(cli, 'process_study', lambda *_args, **_kwargs: {'complete': False})
    monkeypatch.setattr(cli, 'process_path', lambda *_args, **_kwargs: study / 'process.json')
    assert cli.main(['launch', str(study), '--mode', 'eval', '--console', 'shared']) == 1
    assert launched == ['e0']
    stored = (study / 'scripts' / 'jobs.json').read_text(encoding='utf-8')
    assert '"t0"' in stored
    assert '"e0"' in stored


def test_cpu_make_skips_gpu_probe(tmp_path: Path, monkeypatch, capsys):
    from rpipe.flow import cli

    study = tmp_path / 'cpu-study'
    study.mkdir()
    (study / 'study.yaml').write_text(
        'study: cpu-study\norigin: domestic\naxes: {}\nseeds: [0]\n',
        encoding='utf-8',
    )
    (study / 'experiment_config.yaml').write_text(
        'experiment: demo\n'
        'data: {name: Toy, source: stub}\n'
        'model: {name: linear}\n'
        'algorithm: {mode: train, num_steps: 1}\n'
        'system: {device: cpu}\n',
        encoding='utf-8',
    )

    def fail_probe(*_args, **_kwargs):
        raise AssertionError('CPU make must not probe GPUs')

    monkeypatch.setattr(cli, 'probe_gpus', fail_probe)
    assert cli.main(['make', str(study)]) == 0
    out = capsys.readouterr().out
    assert 'make: origin domestic model https://hf-mirror.com' in out
    assert 'make: expand' in out
    assert 'make: pack' in out
    assert not (study / 'activity.json').is_file()
    jobs = (study / 'scripts' / 'jobs.json').read_text(encoding='utf-8')
    assert '"device": "cpu"' in jobs
    assert '"gpu"' not in jobs


def test_expand_rejects_origin_under_data():
    with pytest.raises(ValueError, match='Study'):
        expand_patches(
            {
                'origin': 'domestic',
                'fixed': {'data': {'name': 'CIFAR10', 'origin': 'foreign'}},
                'axes': {},
                'seeds': [0],
            }
        )
    repo = Path(__file__).resolve().parents[4]
    study = load_study_yaml(repo / 'tests' / '_data' / 'studies' / 'cifar_grid')
    patches = expand_patches(study)
    assert len(patches) == 8
    pairs = [
        (p['model']['name'], p['algorithm']['mode'], p['seed'], p['data']['config']['train_size'])
        for p in patches
    ]
    assert pairs[0] == ('linear', 'train', 0, 1024)
    assert patches[0]['origin'] == 'domestic'
    assert 'origin' not in patches[0]['data']
    assert pairs[1] == ('linear', 'eval', 0, 1024)
    assert {name for name, _, _, _ in pairs} == {'linear', 'mlp', 'cnn', 'resnet18'}
    assert sum(1 for p in patches if p.get('tags') == ['baseline']) == 1
    assert patches[0]['description'] == 'model=linear mode=train seed=0'



@pytest.mark.parametrize('text', ['false', '0', '[]', "''", 'true', '1'],
                         ids=['false', 'zero', 'empty-list', 'empty-string', 'true', 'one'])
def test_study_rejects_non_mapping_yaml_root_without_writes(tmp_path, text):
    path = tmp_path / 'study.yaml'
    raw = text.encode('utf-8')
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='mapping'):
        load_study_yaml(tmp_path)
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('text', ['', '# empty document', 'null', '{}'])
def test_study_keeps_legacy_empty_yaml_document_or_mapping(tmp_path, text):
    path = tmp_path / 'study.yaml'
    path.write_text(text, encoding='utf-8')
    assert load_study_yaml(tmp_path) == {}
    assert path.read_text(encoding='utf-8') == text



def _saved_plan_file(root, payload, *, raw=None):
    import json

    path = root / 'scripts/jobs.json'
    path.parent.mkdir()
    path.write_bytes(raw if raw is not None else json.dumps(payload).encode('utf-8'))
    return path


@pytest.mark.parametrize('field,value', [
    ('init_gpu', 'bad'), ('init_gpu', None), ('init_gpu', float('inf')),
    ('num_gpus', 'bad'), ('num_gpus', None), ('num_gpus', float('inf')),
    ('round', 'bad'), ('round', float('inf')),
])
def test_saved_plan_bad_stored_numbers_request_rebuild_without_rewrite(tmp_path, field, value):
    path = _saved_plan_file(tmp_path, {'jobs': [], field: value})
    before = path.read_bytes()
    assert load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0) is None
    assert path.read_bytes() == before


@pytest.mark.parametrize('raw', [b'\xff', b'{', b'[]'])
def test_saved_plan_bad_encoding_json_or_root_requests_rebuild(tmp_path, raw):
    path = _saved_plan_file(tmp_path, {}, raw=raw)
    assert load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0) is None
    assert path.read_bytes() == raw


@pytest.mark.parametrize('operation', ['is_file', 'read_text'])
def test_saved_plan_stat_or_read_refusal_is_optional(tmp_path, monkeypatch, operation):
    operation = 'open' if operation == 'read_text' else operation
    path = _saved_plan_file(tmp_path, {'jobs': []})
    before = path.read_bytes()
    original = getattr(Path, operation)

    def refused(self, *args, **kwargs):
        if kwargs.get('mode', args[0] if args else 'r') not in ('r', 'rt'):
            return original(self, *args, **kwargs)
        if self == path:
            raise PermissionError('injected saved-plan refusal')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, operation, refused)
    assert load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0) is None
    assert path.read_bytes() == before


@pytest.mark.parametrize('extra', [{}, {'round': None}, {'round': 0},
                                  {'init_gpu': '0', 'num_gpus': '1', 'round': '2'}])
def test_saved_plan_legacy_defaults_and_integer_strings_still_reuse(tmp_path, extra):
    path = _saved_plan_file(tmp_path, {'jobs': [], **extra})
    before = path.read_bytes()
    plan = load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0)
    assert plan is not None
    assert plan['job_list'] == []
    assert plan['round'] == (2 if extra.get('round') == '2' else 1)
    assert path.read_bytes() == before


@pytest.mark.parametrize('field', ['init_gpu', 'num_gpus', 'round_size'])
def test_saved_plan_invalid_caller_numbers_are_not_swallowed(tmp_path, field):
    _saved_plan_file(tmp_path, {'jobs': []})
    args = {'init_gpu': 0, 'num_gpus': 1, 'round_size': 0, field: 'bad'}
    with pytest.raises(ValueError):
        load_launch_plan(tmp_path, **args)


def test_saved_plan_unexpected_reader_error_propagates(tmp_path, monkeypatch):
    path = _saved_plan_file(tmp_path, {'jobs': []})
    original = Path.open
    error = RuntimeError('unexpected cache-reader program error')

    def broken(self, *args, **kwargs):
        if kwargs.get('mode', args[0] if args else 'r') not in ('r', 'rt'):
            return original(self, *args, **kwargs)
        if self == path:
            raise error
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', broken)
    with pytest.raises(RuntimeError) as caught:
        load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0)
    assert caught.value is error


def test_missing_saved_plan_still_requests_rebuild(tmp_path):
    assert load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('outcome', ['match', 'mismatch', 'raise'])
def test_match_when_keeps_inequality_short_circuit_and_exception_identity(outcome):
    from rpipe.structure.make.expand import match_when
    calls = []
    primary = RuntimeError('synthetic comparison refusal')
    class Value:
        def __ne__(self, other):
            calls.append(other)
            if outcome == 'raise':
                raise primary
            return outcome == 'mismatch'
        def __eq__(self, other):
            pytest.fail('match_when must preserve the inequality operation')
    if outcome == 'raise':
        with pytest.raises(RuntimeError) as caught:
            match_when({'first': Value(), 'second': Value()}, {'first': 1, 'second': 2})
        assert caught.value is primary
    else:
        assert match_when({'first': Value(), 'second': Value()}, {'first': 1, 'second': 2}) == (outcome == 'match')
    assert calls == ([1, 2] if outcome == 'match' else [1])
    assert match_when({}, {}) is True
