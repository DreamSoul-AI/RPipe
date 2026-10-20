"""Sibling-dependent launch recovery and stale-result protection."""

import json
import os
from pathlib import Path

import pytest

import rpipe.structure.make.schedule as schedule
from rpipe.structure.artifact import artifact_layout
from rpipe.structure.artifact.index import write_index
from rpipe.structure.artifact.result import load_result, write_result

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_make,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def _study(root: Path):
    jobs = []
    experiments = []
    for mode, prefix in [('train', 't'), ('eval', 'e')]:
        runs = []
        for seed in range(2):
            run_id = f'{prefix}{seed}'
            config = artifact_layout(root, run_id).config_path
            config.write_text(json.dumps({'algorithm': {'mode': mode}, 'system': {'device': 'cpu'}}), encoding='utf-8')
            jobs.append({'run_id': run_id, 'mode': mode, 'device': 'cpu', 'config': str(config)})
            runs.append({'id': run_id, 'seed': seed})
        experiments.append({'factors': {'algorithm.mode': mode}, 'runs': runs})
    write_index(root, {'study': 'recovery', 'experiments': experiments})
    return jobs


def _succeed(study: Path, run_id: str):
    write_result(artifact_layout(study, run_id).result_path, {
        'status': 'succeeded', 'metrics': {'accuracy': 42}, 'control': {}, 'paths': {},
    })


@pytest.mark.parametrize('device,top_level', [('cpu', 'cuda'), ('cuda', 'cpu')])
def test_plan_and_capacity_use_nested_runtime_device(tmp_path, device, top_level):
    from rpipe.structure.make.capacity import attach_estimates, requires_gpu
    from rpipe.structure.system.config import SystemConfig

    layout = artifact_layout(tmp_path, 'nested-device')
    system = {'device': top_level, 'config': {'device': device}}
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'train'}, 'system': system}), encoding='utf-8')
    jobs = schedule.plan_jobs(tmp_path, [layout.config_path], init_gpu=0, num_gpus=1)
    assert jobs[0]['device'] == SystemConfig.from_mapping(system).setting('device') == device
    attach_estimates(jobs)
    assert jobs[0]['device'] == device
    assert requires_gpu(jobs) == (device == 'cuda')
    assert ('gpu' in jobs[0]) == (device == 'cuda')


@pytest.mark.parametrize('device,stale_device', [('cpu', 'cuda'), ('cuda', 'cpu')])
def test_reused_plan_rejects_stale_device_then_accepts_replanned_jobs(tmp_path, device, stale_device):
    layout = artifact_layout(tmp_path, 'stale-device')
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'train'},
        'system': {'device': stale_device, 'config': {'device': device}}}), encoding='utf-8')
    old_jobs = [{'run_id': 'stale-device', 'config': str(layout.config_path), 'mode': 'train', 'device': stale_device}]
    schedule.write_jobs_json(tmp_path, old_jobs, round_size=1, init_gpu=0, num_gpus=1)
    assert schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0) is None
    new_jobs = schedule.plan_jobs(tmp_path, [layout.config_path], init_gpu=0, num_gpus=1)
    schedule.write_jobs_json(tmp_path, new_jobs, round_size=1, init_gpu=0, num_gpus=1)
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0)
    assert plan is not None and plan['job_list'][0]['device'] == device


def test_reused_plan_rejects_gpu_binding_on_cpu_job(tmp_path):
    layout = artifact_layout(tmp_path, 'cpu-stale-gpu')
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'train'}, 'system': {'device': 'cpu'}}), encoding='utf-8')
    jobs = [{'run_id': 'cpu-stale-gpu', 'config': str(layout.config_path), 'mode': 'train', 'device': 'cpu', 'gpu': '0'}]
    schedule.write_jobs_json(tmp_path, jobs, round_size=1, init_gpu=0, num_gpus=1)
    assert schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0) is None


def test_cpu_launch_env_does_not_apply_stale_gpu_binding(monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '7')
    assert schedule.launch_job_env({'device': 'cpu', 'gpu': '2'})['CUDA_VISIBLE_DEVICES'] == '7'


def test_cpu_launch_labels_match_actual_resource_despite_stale_gpu(tmp_path, monkeypatch, capsys):
    jobs = _study(tmp_path)
    for job in jobs:
        job['gpu'] = '2'
    _fake_processes(monkeypatch, tmp_path, {})
    assert schedule.launch_jobs(tmp_path, jobs, console='shared') == [0, 0, 0, 0]
    printed = capsys.readouterr().out
    assert '+ cpu' in printed
    assert '+ gpu=' not in printed


@pytest.mark.parametrize('explicit_batches', [False, True])
def test_launch_rejects_duplicate_run_before_starting_processes(tmp_path, monkeypatch, explicit_batches):
    jobs = _study(tmp_path)
    started = _fake_processes(monkeypatch, tmp_path, {})
    supplied = [[jobs[0], jobs[0]]] if explicit_batches else None
    rows = jobs if explicit_batches else [jobs[0], jobs[0]]
    with pytest.raises(ValueError, match='duplicate Run'):
        schedule.launch_jobs(tmp_path, rows, batches=supplied, console='shared')
    assert started == []


def _fake_processes(monkeypatch, study, failures):
    started = []
    counts = {}

    class Proc:
        def __init__(self, args, **_kwargs):
            self.run_id = args[-1]
            started.append(self.run_id)
            counts[self.run_id] = counts.get(self.run_id, 0) + 1

        def wait(self):
            if counts[self.run_id] <= failures.get(self.run_id, 0):
                write_result(artifact_layout(study, self.run_id).result_path,
                             {'status': 'failed', 'error': 'injected train failure'})
                return 1
            _succeed(study, self.run_id)
            return 0

    monkeypatch.setattr(schedule.subprocess, 'Popen', Proc)
    return started


def test_no_retry_keeps_failed_train_eval_barrier(tmp_path, monkeypatch):
    jobs = _study(tmp_path)
    started = _fake_processes(monkeypatch, tmp_path, {'t0': 1})
    codes = schedule.launch_jobs(tmp_path, jobs, batches=[jobs], retry_failed=False, console='shared')
    assert started == ['t0', 't1', 'e1']
    assert codes == [1, 0, 1, 0]
    assert not schedule.run_succeeded(tmp_path, 'e0')
    assert schedule.run_succeeded(tmp_path, 'e1')
    assert 't0' in load_result(artifact_layout(tmp_path, 'e0').result_path)['error']


@pytest.mark.parametrize('failures', [1, 2], ids=['retry-recovers', 'retry-exhausted'])
def test_launch_finishes_train_retry_before_dependent_eval(tmp_path, monkeypatch, failures):
    jobs = _study(tmp_path)
    started = _fake_processes(monkeypatch, tmp_path, {'t0': failures})
    # Even a supplied mixed batch must respect the dependency barrier.
    codes = schedule.launch_jobs(tmp_path, jobs, batches=[jobs], console='shared')
    assert started[:3] == ['t0', 't1', 't0']
    assert schedule.run_succeeded(tmp_path, 'e1')
    if failures == 1:
        assert started == ['t0', 't1', 't0', 'e0', 'e1']
        assert schedule.run_succeeded(tmp_path, 'e0')
        assert codes == [1, 0, 0, 0, 0]
    else:
        assert 'e0' not in started
        assert not schedule.run_succeeded(tmp_path, 'e0')
        result = load_result(artifact_layout(tmp_path, 'e0').result_path)
        assert result['status'] == 'failed'
        assert 't0' in result['error']


def test_train_only_rerun_invalidates_only_its_successful_sibling_eval(tmp_path, monkeypatch):
    jobs = _study(tmp_path)
    for job in jobs:
        _succeed(tmp_path, job['run_id'])
    started = _fake_processes(monkeypatch, tmp_path, {})
    schedule.launch_jobs(tmp_path, jobs[:1], console='shared')
    assert started == ['t0']
    stale = load_result(artifact_layout(tmp_path, 'e0').result_path)
    assert stale['status'] == 'failed'
    assert stale['metrics']['accuracy'] == 42  # retain old evidence, exclude it from success
    assert schedule.run_succeeded(tmp_path, 'e1')


@pytest.mark.parametrize('parent_succeeded', [True, False], ids=['newer-parent', 'unfinished-parent'])
def test_relaunch_and_make_include_previously_successful_stale_eval(tmp_path, monkeypatch, parent_succeeded):
    jobs = _study(tmp_path)
    for job in jobs:
        _succeed(tmp_path, job['run_id'])
    eval_result = artifact_layout(tmp_path, 'e0').result_path
    train_result = artifact_layout(tmp_path, 't0').result_path
    if not parent_succeeded:
        write_result(train_result, {'status': 'failed', 'error': 'injected failure'})
    os.utime(eval_result, ns=(1_000_000_000, 1_000_000_000))
    os.utime(train_result, ns=(2_000_000_000, 2_000_000_000))
    before = eval_result.read_bytes()
    schedule.write_jobs_json(tmp_path, jobs, round_size=2, init_gpu=0, num_gpus=0,
                             batches=[jobs[:2], jobs[2:]])
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=0, round_size=2)
    expected = ['e0'] if parent_succeeded else ['t0', 'e0']
    assert [job['run_id'] for job in plan['job_list']] == expected
    made = schedule.plan_jobs(tmp_path, [Path(job['config']) for job in jobs], num_gpus=0)
    assert [job['run_id'] for job in made] == expected
    assert eval_result.read_bytes() == before  # planning remains read-only for results
    started = _fake_processes(monkeypatch, tmp_path, {})
    schedule.launch_jobs(tmp_path, plan['job_list'], batches=plan['batches'], console='shared')
    assert started == expected
    assert schedule.run_succeeded(tmp_path, 'e0')


def test_eval_only_launch_blocks_unsuccessful_parent_without_spawning(tmp_path, monkeypatch):
    jobs = _study(tmp_path)
    started = _fake_processes(monkeypatch, tmp_path, {})
    assert schedule.launch_jobs(tmp_path, jobs[2:3], console='shared') == [1]
    assert started == []
    assert not schedule.run_succeeded(tmp_path, 'e0')


@pytest.mark.parametrize('independent', ['disabled', 'external', 'own'])
def test_independent_eval_does_not_require_sibling_success(tmp_path, monkeypatch, independent):
    jobs = _study(tmp_path)
    algorithm = {'mode': 'eval'}
    if independent == 'disabled':
        algorithm['resume'] = False
    else:
        checkpoint = (tmp_path / 'external.pt' if independent == 'external' else
                      artifact_layout(tmp_path, 'e0').assets_dir / 'checkpoints' / 'best.pt')
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(b'path resolution only')
        if independent == 'external':
            algorithm['resume_from'] = str(checkpoint)
    artifact_layout(tmp_path, 'e0').config_path.write_text(json.dumps({'algorithm': algorithm}), encoding='utf-8')
    started = _fake_processes(monkeypatch, tmp_path, {})
    schedule.launch_jobs(tmp_path, jobs[2:3], console='shared')
    assert started == ['e0']
    assert schedule.run_succeeded(tmp_path, 'e0')


@pytest.mark.parametrize('location', ['own', 'external'])
@pytest.mark.parametrize('material', ['empty', 'unrelated', 'empty-meta', 'model.pt', 'payload.pt', 'bundle'])
def test_checkpoint_directory_requires_material_to_be_independent(tmp_path, monkeypatch, location, material):
    jobs = _study(tmp_path)
    own = artifact_layout(tmp_path, 'e0').assets_dir / 'checkpoints' / 'best'
    folder = own if location == 'own' else tmp_path / 'external' / 'best'
    folder.mkdir(parents=True)
    if material in ('model.pt', 'payload.pt'):
        (folder / material).write_bytes(b'path resolution only')
    elif material == 'bundle':
        folder.with_suffix('.pt').write_bytes(b'path resolution only')
    elif material == 'empty-meta':
        (folder / 'meta.json').write_text('{}', encoding='utf-8')
    elif material == 'unrelated':
        (folder / 'notes.txt').write_text('not a checkpoint', encoding='utf-8')
    if location == 'external':
        artifact_layout(tmp_path, 'e0').config_path.write_text(json.dumps({
            'algorithm': {'mode': 'eval', 'resume_from': str(folder)},
        }), encoding='utf-8')
    # A failed train can have a checkpoint; an empty override must not bypass its guard.
    partial = artifact_layout(tmp_path, 't0').assets_dir / 'checkpoints' / 'best.pt'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'partial train checkpoint')
    started = _fake_processes(monkeypatch, tmp_path, {})
    codes = schedule.launch_jobs(tmp_path, jobs[2:3], console='shared')
    independent = material in ('model.pt', 'payload.pt', 'bundle')
    assert started == (['e0'] if independent else [])
    assert codes == ([0] if independent else [1])
    assert schedule.run_succeeded(tmp_path, 'e0') is independent


def test_killed_rerun_cannot_reuse_previous_success(tmp_path, monkeypatch):
    jobs = _study(tmp_path)
    for job in jobs:
        _succeed(tmp_path, job['run_id'])

    class Killed:
        def wait(self):
            return 1

    monkeypatch.setattr(schedule.subprocess, 'Popen', lambda *_args, **_kwargs: Killed())
    schedule.launch_jobs(tmp_path, jobs[:1], retry_failed=False, console='shared')
    assert not schedule.run_succeeded(tmp_path, 't0')
    assert not schedule.run_succeeded(tmp_path, 'e0')


@pytest.mark.parametrize('complete', [True, False], ids=['recovered', 'incomplete'])
def test_bash_chunks_execute_shared_launcher_and_process_after_failure(tmp_path, monkeypatch, complete):
    import rpipe.flow.process as process

    jobs = _study(tmp_path)
    chunks = schedule.render_bash(tmp_path, jobs, python_exe='/opt/python', round_size=2, split_round=1)
    launched = []
    processed = []

    def launch(study, selected, **kwargs):
        assert study == tmp_path
        assert kwargs['batches'] == [selected]
        launched.append([job['run_id'] for job in selected])
        return [1, 0]  # A transient failed execution must not determine final exit.

    monkeypatch.setattr(schedule, 'launch_jobs', launch)
    monkeypatch.setattr(schedule, 'run_succeeded', lambda *_args: complete)
    monkeypatch.setattr(process, 'run_study', lambda study: processed.append(study) or {'complete': complete})
    assert len(chunks) == 2
    for index, chunk in enumerate(chunks):
        body = chunk.split("<<'RPIPE_LAUNCH'\n", 1)[1].rsplit('\nRPIPE_LAUNCH', 1)[0]
        with pytest.raises(SystemExit) as exit_info:
            exec(compile(body, f'launch_{index + 1}.sh', 'exec'), {})
        assert exit_info.value.code == int(not complete)
        assert len(processed) == index  # Only the final chunk processes, even after failure.
    assert launched == [['t0', 't1'], ['e0', 'e1']]
    assert processed == [tmp_path]


_BATCH_MISMATCHES = ['foreign', 'missing', 'metadata', 'duplicate']


def _mismatched_batches(jobs, kind):
    rows = [dict(job) for job in jobs]
    if kind == 'foreign':
        rows[0]['run_id'] = 'peer'
    elif kind == 'missing':
        rows.pop()
    elif kind == 'metadata':
        rows[0]['device'] = 'cuda'
    elif kind == 'duplicate':
        rows.append(dict(rows[0]))
    return [rows]


@pytest.mark.parametrize('kind', _BATCH_MISMATCHES + ['same', 'reordered'])
def test_wait_groups_enforces_declared_job_multiset(tmp_path, kind):
    jobs = _study(tmp_path)
    batches = _mismatched_batches(jobs, kind)
    if kind == 'reordered':
        batches[0].reverse()
    if kind in _BATCH_MISMATCHES:
        with pytest.raises(ValueError, match='batches|duplicate Run'):
            schedule.wait_groups(jobs, 1, batches)
    else:
        assert schedule.wait_groups(jobs, 1, batches) == batches


@pytest.mark.parametrize('kind', _BATCH_MISMATCHES)
def test_launch_refuses_batch_mismatch_before_invalidating_any_result(tmp_path, monkeypatch, kind):
    jobs = _study(tmp_path)
    for run_id in ['t0', 't1', 'e0', 'e1', 'peer']:
        _succeed(tmp_path, run_id)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    started = []

    class Proc:
        def __init__(self, args, **kwargs):
            started.append(args[-1])

        def wait(self):
            return 1

    monkeypatch.setattr(schedule.subprocess, 'Popen', Proc)
    with pytest.raises(ValueError, match='batches|duplicate Run'):
        schedule.launch_jobs(tmp_path, jobs, batches=_mismatched_batches(jobs, kind),
                             retry_failed=False, console='shared')
    assert started == []
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('kind', _BATCH_MISMATCHES)
def test_script_publish_refuses_batch_mismatch_preserving_old_plan(tmp_path, kind):
    jobs = _study(tmp_path)
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ['jobs.json', 'launch.sh', 'launch.ps1']:
        (scripts / name).write_bytes(b'previous artifact')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='batches|duplicate Run'):
        schedule.write_launch_scripts(tmp_path, jobs, round_size=1, init_gpu=0,
                                      num_gpus=0, batches=_mismatched_batches(jobs, kind))
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('kind', _BATCH_MISMATCHES)
def test_render_refuses_batch_mismatch_before_generating_foreign_commands(tmp_path, kind):
    jobs = _study(tmp_path)
    with pytest.raises(ValueError, match='batches|duplicate Run'):
        schedule.render_bash(tmp_path, jobs, python_exe='python', round_size=1,
                             split_round=65535, batches=_mismatched_batches(jobs, kind))


@pytest.mark.parametrize('kind', _BATCH_MISMATCHES)
@pytest.mark.parametrize('include_done', [False, True])
def test_saved_plan_rejects_mismatched_batches_without_rewrite(tmp_path, kind, include_done):
    jobs = _study(tmp_path)
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    path = scripts / 'jobs.json'
    payload = {'jobs': jobs, 'batches': _mismatched_batches(jobs, kind),
               'init_gpu': 0, 'num_gpus': 0, 'round': 1}
    path.write_text(json.dumps(payload), encoding='utf-8')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=0, round_size=0,
                                     include_done=include_done) is None
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before



@pytest.mark.parametrize('include_done', [False, True])
@pytest.mark.parametrize('kind,raw', [
    ('list', b'[]'), ('null', b'null'), ('bool', b'false'), ('text', b'""'), ('number', b'0'),
    ('syntax', b'{'),
    ('deep', b'{"status":"succeeded","unused":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}'),
    ('valid', b'{"status":"succeeded","control":{},"metrics":{},"paths":{}}'),
], ids=['list', 'null', 'bool', 'text', 'number', 'syntax', 'deep', 'valid'])
def test_readiness_parse_failure_keeps_cached_jobs_and_peer(tmp_path, kind, raw, include_done):
    from rpipe.structure.artifact.layout import CorruptArtifactError

    jobs = _study(tmp_path)[:2]
    _succeed(tmp_path, 't1')
    path = tmp_path / 'runs/t0/result.json'
    path.write_bytes(raw)
    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps({'jobs': jobs}), encoding='utf-8')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if kind != 'valid':
        error = RecursionError if kind == 'deep' else ValueError if kind == 'syntax' else CorruptArtifactError
        with pytest.raises(error):
            load_result(path)
    else:
        assert load_result(path)['status'] == 'succeeded'
    assert schedule.run_succeeded(tmp_path, 't0') == (kind == 'valid')
    assert schedule.run_succeeded(tmp_path, 't1')
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0,
                                     include_done=include_done)
    expected = ['t0', 't1'] if include_done else [] if kind == 'valid' else ['t0']
    assert plan is not None
    assert [job['run_id'] for job in plan['job_list']] == expected
    assert plan['n_jobs'] == len(expected)
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('kind', ['deep', 'syntax', 'valid'])
def test_cached_plan_json_parse_keeps_existing_rebuild_boundary(tmp_path, kind):
    _study(tmp_path)
    path = tmp_path / 'scripts/jobs.json'
    path.parent.mkdir()
    raw = (b'{"jobs":[],"unused":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}'
           if kind == 'deep' else b'{' if kind == 'syntax' else b'{"jobs":[]}')
    path.write_bytes(raw)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0)
    assert (plan is None) == (kind != 'valid')
    if kind == 'valid':
        assert plan['job_list'] == [] and plan['n_jobs'] == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_readiness_does_not_suppress_unexpected_decoder_bug(tmp_path, monkeypatch):
    _study(tmp_path)
    _succeed(tmp_path, 't0')
    path = tmp_path / 'runs/t0/result.json'
    before = path.read_bytes()
    error = RuntimeError('unexpected result decoder bug')
    def broken(text):
        raise error
    monkeypatch.setattr(schedule, 'decode_result', broken)
    with pytest.raises(RuntimeError) as caught:
        schedule.run_succeeded(tmp_path, 't0')
    assert caught.value is error
    assert path.read_bytes() == before


@pytest.mark.parametrize('retry', [False, True])
@pytest.mark.parametrize('output_error', ['broken_pipe', 'permission', 'encoding', None])
def test_nonzero_exit_is_recorded_before_error_output(tmp_path, monkeypatch, retry, output_error):
    import builtins

    jobs = _study(tmp_path)
    _succeed(tmp_path, 't1')
    peer = artifact_layout(tmp_path, 't1').result_path
    peer_before = peer.read_bytes()
    config = Path(jobs[0]['config'])
    config_before = config.read_bytes()
    failures = {
        'broken_pipe': BrokenPipeError('closed stdout'),
        'permission': PermissionError('stdout denied'),
        'encoding': UnicodeEncodeError('ascii', 'é', 0, 1, 'stdout encoding'),
    }
    error = failures.get(output_error)
    waited = []

    class Proc:
        def wait(self):
            _succeed(tmp_path, 't0')
            waited.append('t0')
            return 1

    def emit(*args, **kwargs):
        if error is not None and args and str(args[0]).startswith('error t0 exit='):
            raise error
        return builtins.print(*args, **kwargs)

    monkeypatch.setattr(schedule.subprocess, 'Popen', lambda *_args, **_kwargs: Proc())
    monkeypatch.setattr(schedule, 'print', emit, raising=False)
    if error is None:
        assert schedule.launch_jobs(tmp_path, jobs[:1], retry_failed=retry, console='shared') == ([1, 1] if retry else [1])
        assert waited == ['t0'] * (2 if retry else 1)
    else:
        with pytest.raises(type(error)) as caught:
            schedule.launch_jobs(tmp_path, jobs[:1], retry_failed=retry, console='shared')
        assert caught.value is error
        assert waited == ['t0']
    result = load_result(artifact_layout(tmp_path, 't0').result_path)
    assert result['status'] == 'failed'
    assert result['error'] == 'run-one exited with code 1'
    assert not schedule.run_succeeded(tmp_path, 't0')
    assert peer.read_bytes() == peer_before
    assert config.read_bytes() == config_before


@pytest.mark.parametrize('declaration', [
    'malformed', 'nonmapping', 'bad-shared', 'bad-root', 'decode', 'read-io',
    'valid-default', 'valid-custom', 'missing',
])
@pytest.mark.parametrize('route', ['fresh', 'success', 'launch'])
def test_failure_invalidation_does_not_require_shared_layout(tmp_path, monkeypatch, declaration, route):
    layout = artifact_layout(tmp_path, 'target')
    layout.config_path.write_text('algorithm: {mode: train}\nsystem: {device: cpu}\n', encoding='utf-8')
    peer = artifact_layout(tmp_path, 'peer')
    write_result(peer.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 7}, 'paths': {}})
    if route != 'fresh':
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 9}, 'paths': {'assets': str(layout.assets_dir)}})
    write_index(tmp_path, {'experiments': [{'factors': {}, 'runs': [{'id': 'target', 'seed': 0}]}]})
    contents = {
        'malformed': b'shared: [\n', 'nonmapping': b'[]\n',
        'bad-shared': b'shared: []\n', 'bad-root': b'shared: {root: []}\n',
        'decode': b'\xff\xfe', 'read-io': b'{}\n',
        'valid-default': b'{}\n', 'valid-custom': b'shared: {root: custom-inputs}\n',
    }
    source = tmp_path / 'study.yaml'
    if declaration != 'missing':
        source.write_bytes(contents[declaration])
    protected = [layout.config_path, tmp_path / 'index.json', peer.result_path]
    if source.exists():
        protected.append(source)
    before = {p: p.read_bytes() for p in protected}
    original_read = Path.open
    def read(path, *args, **kwargs):
        if kwargs.get('mode', args[0] if args else 'r') not in ('r', 'rt'):
            return original_read(path, *args, **kwargs)
        if declaration == 'read-io' and path == source:
            raise PermissionError('unavailable shared metadata')
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', read)
    starts = []
    class Proc:
        def wait(self):
            return 1
    def start(*args, **kwargs):
        starts.append(load_result(layout.result_path)['status'])
        return Proc()
    monkeypatch.setattr(schedule.subprocess, 'Popen', start)
    if route == 'launch':
        jobs = [{'run_id': 'target', 'mode': 'train', 'device': 'cpu', 'gpu': None}]
        assert schedule.launch_jobs(tmp_path, jobs, retry_failed=False, console='shared') == [1]
        assert starts == ['failed']
        expected_error = 'rerun started; no successful result from this execution yet'
    else:
        schedule._fail_result(tmp_path, 'target', 'primary failure evidence')
        assert starts == []
        expected_error = 'primary failure evidence'
    result = load_result(layout.result_path)
    assert result['status'] == 'failed' and result['error'] == expected_error
    assert result.get('metrics') == ({'accuracy': 9} if route != 'fresh' else None)
    assert result.get('paths') == ({'assets': str(layout.assets_dir)} if route != 'fresh' else None)
    assert {p: p.read_bytes() for p in before} == before
    if declaration == 'missing':
        assert not source.exists()


@pytest.mark.parametrize('damaged_shared', [False, True])
def test_failure_invalidation_keeps_result_publish_error_strict(tmp_path, monkeypatch, damaged_shared):
    layout = artifact_layout(tmp_path, 'target')
    write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    before = layout.result_path.read_bytes()
    (tmp_path / 'study.yaml').write_text('shared: []\n' if damaged_shared else '{}\n', encoding='utf-8')
    primary = PermissionError('result publication refused')
    def refuse(*args, **kwargs):
        raise primary
    monkeypatch.setattr(schedule, 'write_result', refuse)
    with pytest.raises(PermissionError) as caught:
        schedule._fail_result(tmp_path, 'target', 'primary run failure')
    assert caught.value is primary
    assert layout.result_path.read_bytes() == before


@pytest.mark.parametrize('bad', [None, [], {}, {'run_id': ''}, {'run_id': '../peer'}, {'run_id': 1}],
                         ids=['null', 'list', 'missing-id', 'empty-id', 'unsafe-id', 'numeric-id'])
@pytest.mark.parametrize('batched', [False, True])
@pytest.mark.parametrize('include_done', [False, True])
def test_cached_invalid_job_rebuilds_before_readiness(tmp_path, monkeypatch, bad, batched, include_done):
    jobs = _study(tmp_path)[:1] + [bad]
    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    payload = {'jobs': jobs, 'num_gpus': 0, 'round': 2}
    if batched:
        payload['batches'] = [jobs]
    cache.write_text(json.dumps(payload), encoding='utf-8')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail('invalid cache reached readiness filtering')
    monkeypatch.setattr(schedule, 'run_succeeded', forbidden)
    assert schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=0, round_size=2,
                                     include_done=include_done) is None
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('route', ['launch', 'scripts', 'render'])
@pytest.mark.parametrize('bad', [None, {}, {'run_id': '../peer'}], ids=['null', 'missing-id', 'unsafe-id'])
def test_invalid_native_plan_refuses_before_side_effects(tmp_path, monkeypatch, route, bad):
    jobs = _study(tmp_path)[:1] + [bad]
    _succeed(tmp_path, 't0')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ['jobs.json', 'launch.sh', 'launch.ps1']:
        (scripts / name).write_bytes(b'previous artifact')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail('invalid plan reached child creation')
    monkeypatch.setattr(schedule.subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError):
        if route == 'launch':
            schedule.launch_jobs(tmp_path, jobs, retry_failed=False, console='shared')
        elif route == 'scripts':
            schedule.write_launch_scripts(tmp_path, jobs, round_size=2, init_gpu=0, num_gpus=0)
        else:
            schedule.render_bash(tmp_path, jobs, python_exe='python', round_size=2, split_round=1)
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('include_done', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_cached_minimal_legacy_job_and_empty_controls(tmp_path, include_done, empty):
    _study(tmp_path)
    _succeed(tmp_path, 't0')
    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    jobs = [] if empty else [{'run_id': 't0'}, {'run_id': 't1'}]
    cache.write_text(json.dumps({'jobs': jobs}), encoding='utf-8')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0,
                                     include_done=include_done)
    expected = jobs if empty or include_done else [jobs[1]]
    assert plan is not None and plan['job_list'] == expected and plan['n_jobs'] == len(expected)
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('batched', [False, True])
@pytest.mark.parametrize('other', ['Owner', 'owner', 'Other'], ids=['literal', 'case-alias', 'distinct'])
@pytest.mark.parametrize('route', ['launch', 'cache', 'scripts'])
def test_plan_run_identity_uses_native_platform_before_effects(tmp_path, monkeypatch, batched, other, route):
    first = artifact_layout(tmp_path, 'Owner')
    first.config_path.write_text('{}', encoding='utf-8')
    second = artifact_layout(tmp_path, other)
    second.config_path.write_text('{}', encoding='utf-8')
    same_run = first.root.samefile(second.root)
    assert same_run == (other == 'Owner' or (os.name == 'nt' and other == 'owner'))
    jobs = [{'run_id': name, 'mode': 'train', 'device': 'cpu'} for name in ['Owner', other]]
    batches = [jobs] if batched else None
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    cache = scripts / 'jobs.json'
    cache.write_text(json.dumps({'jobs': jobs, 'round': 2, 'num_gpus': 0,
                                **({'batches': batches} if batched else {})}), encoding='utf-8')
    for name in ['launch.sh', 'launch.ps1']:
        (scripts / name).write_bytes(b'previous artifact')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    starts = []
    class Proc:
        def __init__(self, args, **kwargs):
            starts.append(args[-1])
        def wait(self):
            return 1
    def forbidden(*args, **kwargs):
        pytest.fail('duplicate plan reached result invalidation')
    monkeypatch.setattr(schedule.subprocess, 'Popen', Proc)
    monkeypatch.setattr(schedule, '_fail_result', forbidden)
    def execute():
        if route == 'launch':
            return schedule.launch_jobs(tmp_path, jobs, round_size=2, batches=batches,
                                        retry_failed=False, console='shared')
        if route == 'scripts':
            return schedule.write_launch_scripts(tmp_path, jobs, round_size=2, init_gpu=0,
                                                  num_gpus=0, batches=batches)
        return schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=0, round_size=2,
                                         include_done=True)
    if same_run:
        if route == 'cache':
            assert execute() is None
        else:
            with pytest.raises(ValueError, match='duplicate Run'):
                execute()
        assert starts == []
        assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    else:
        result = execute()
        assert result is not None
        if route == 'launch':
            assert result == [1, 1] and len(starts) == 2
        elif route == 'cache':
            assert result['job_list'] == jobs
        for path, raw in before.items():
            if route != 'scripts' or path not in [cache, scripts / 'launch.sh', scripts / 'launch.ps1']:
                assert path.read_bytes() == raw


@pytest.mark.parametrize('include_done', [False, True])
def test_duplicate_cache_rejected_before_success_can_hide_it(tmp_path, monkeypatch, include_done):
    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    jobs = [{'run_id': 'target'}, {'run_id': 'target'}]
    cache.write_text(json.dumps({'jobs': jobs}), encoding='utf-8')
    before = cache.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('duplicate cache reached readiness filtering')
    monkeypatch.setattr(schedule, 'run_succeeded', forbidden)
    assert schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=1, round_size=0,
                                     include_done=include_done) is None
    assert cache.read_bytes() == before


@pytest.mark.parametrize('stage', ['spawn', 'stdout', 'environment', 'invalidation', 'wait', 'exit-diagnostic'])
@pytest.mark.parametrize('cancelled', [False, True], ids=['error', 'cancel'])
@pytest.mark.parametrize('secondary_failure', [False, True], ids=['clean-wait', 'secondary-wait-error'])
def test_exceptional_group_waits_owned_children_preserving_primary(tmp_path, monkeypatch, stage, cancelled, secondary_failure):
    _study(tmp_path)
    _succeed(tmp_path, 'peer')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    primary = KeyboardInterrupt('primary cancellation') if cancelled else OSError('primary launch failure')
    secondary = OSError('secondary wait failure')
    starts, waits = [], []
    failed = False
    class Proc:
        def __init__(self, name):
            self.name = name
        def wait(self):
            nonlocal failed
            waits.append(self.name)
            if stage == 'wait' and self.name == 'first' and not failed:
                failed = True
                raise primary
            if secondary_failure and ((stage == 'exit-diagnostic' and self.name == 'second')
                    or (stage != 'exit-diagnostic' and self.name == 'first' and (stage != 'wait' or failed))):
                raise secondary
            return 1 if stage == 'exit-diagnostic' and self.name == 'first' else 0
    def spawn(args, **kwargs):
        if stage == 'spawn' and args[-1] == 'second':
            raise primary
        starts.append(args[-1])
        return Proc(args[-1])
    def output(message, **kwargs):
        if ((stage == 'stdout' and str(message).startswith('+ cpu second'))
                or (stage == 'exit-diagnostic' and str(message).startswith('error first'))):
            raise primary
    def env(job):
        if stage == 'environment' and job['run_id'] == 'second':
            raise primary
        return {}
    def result(study, run):
        return {'status': 'succeeded'} if stage == 'invalidation' and run == 'second' else {}
    def invalidate(*args):
        raise primary
    monkeypatch.setattr(schedule.subprocess, 'Popen', spawn)
    monkeypatch.setattr('builtins.print', output)
    monkeypatch.setattr(schedule, 'launch_job_env', env)
    monkeypatch.setattr(schedule, '_run_result', result)
    monkeypatch.setattr(schedule, '_fail_result', invalidate)
    jobs = [{'run_id': name, 'device': 'cpu', 'mode': 'train'} for name in ['first', 'second', 'third']]
    with pytest.raises(type(primary)) as caught:
        schedule.launch_jobs(tmp_path, jobs, round_size=2, retry_failed=True, console='shared')
    assert caught.value is primary
    assert 'third' not in starts
    assert set(starts) <= set(waits)
    if stage == 'wait':
        assert waits.count('first') == 2
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('batched', [False, True])
def test_healthy_group_ownership_keeps_original_wait_order(tmp_path, monkeypatch, batched):
    events = []
    class Proc:
        def __init__(self, args, **kwargs):
            self.name = args[-1]
            events.append(('start', self.name))
        def wait(self):
            events.append(('wait', self.name))
            return 0
    monkeypatch.setattr(schedule.subprocess, 'Popen', Proc)
    jobs = [{'run_id': name, 'device': 'cpu'} for name in ['first', 'second', 'third']]
    assert schedule.launch_jobs(tmp_path, jobs, round_size=2,
        batches=[jobs[:2], jobs[2:]] if batched else None, retry_failed=False, console='shared') == [0, 0, 0]
    assert events == [('start', 'first'), ('start', 'second'), ('wait', 'first'),
                      ('wait', 'second'), ('start', 'third'), ('wait', 'third')]



def test_exceptional_group_keeps_nonzero_peer_failure_evidence(tmp_path, monkeypatch):
    primary = OSError('later spawn refused')
    class Proc:
        def wait(self):
            _succeed(tmp_path, 'first')
            return 7
    def spawn(args, **kwargs):
        if args[-1] == 'second':
            raise primary
        return Proc()
    monkeypatch.setattr(schedule.subprocess, 'Popen', spawn)
    jobs = [{'run_id': name, 'mode': 'train', 'device': 'cpu'} for name in ['first', 'second']]
    with pytest.raises(OSError) as caught:
        schedule.launch_jobs(tmp_path, jobs, round_size=2, retry_failed=False, console='shared')
    assert caught.value is primary
    result = load_result(tmp_path / 'runs/first/result.json')
    assert result['status'] == 'failed'
    assert result['error'] == 'run-one exited with code 7'


@pytest.mark.parametrize('spelling', ['e0', 'E0'])
@pytest.mark.parametrize('parent_state', ['failed', 'newer', 'valid'])
@pytest.mark.parametrize('include_done', [False, True])
def test_alias_eval_readiness_and_cache_keep_train_binding(tmp_path, spelling, parent_state, include_done):
    _study(tmp_path)
    # POSIX case-distinct E0 is an independent, unindexed Run, not a Windows alias.
    layout = artifact_layout(tmp_path, spelling)
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'eval'}, 'system': {'device': 'cpu'}}), encoding='utf-8')
    for name in ['t0', 'e0', spelling, 't1', 'e1']:
        _succeed(tmp_path, name)
    parent = tmp_path / 'runs/t0/result.json'
    canonical = tmp_path / 'runs/e0/result.json'
    os.utime(parent, ns=(1_000_000_000, 1_000_000_000))
    os.utime(canonical, ns=(2_000_000_000, 2_000_000_000))
    if parent_state == 'failed':
        write_result(parent, {'status': 'failed', 'error': 'unfinished train'})
    elif parent_state == 'newer':
        os.utime(parent, ns=(3_000_000_000, 3_000_000_000))
    same_run = (tmp_path / 'runs/e0').samefile(layout.root)
    ready = not same_run or parent_state == 'valid'
    jobs = [{'run_id': spelling, 'config': str(layout.config_path), 'mode': 'eval', 'device': 'cpu'}]
    schedule.write_jobs_json(tmp_path, jobs, round_size=1, init_gpu=0, num_gpus=0)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert schedule.run_succeeded(tmp_path, spelling) == ready
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=0, round_size=1, include_done=include_done)
    assert plan is not None and plan['job_list'] == (jobs if include_done or not ready else [])
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('spelling', ['e0', 'E0', 'Other'])
def test_alias_eval_launch_blocks_its_failed_train_before_spawn(tmp_path, monkeypatch, spelling):
    _study(tmp_path)
    layout = artifact_layout(tmp_path, spelling)
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'eval'}, 'system': {'device': 'cpu'}}), encoding='utf-8')
    write_result(tmp_path / 'runs/t0/result.json', {'status': 'failed', 'error': 'unfinished train'})
    same_run = (tmp_path / 'runs/e0').samefile(layout.root)
    starts = _fake_processes(monkeypatch, tmp_path, {})
    codes = schedule.launch_jobs(tmp_path, [{'run_id': spelling, 'mode': 'eval', 'device': 'cpu'}],
                                 retry_failed=False, console='shared')
    assert starts == ([] if same_run else [spelling])
    assert codes == ([1] if same_run else [0])
    if same_run:
        assert 'blocked: sibling train t0' in load_result(layout.result_path)['error']


@pytest.mark.parametrize('spelling', ['t0', 'T0'])
def test_alias_train_rerun_invalidates_only_its_sibling_before_spawn(tmp_path, monkeypatch, spelling):
    _study(tmp_path)
    layout = artifact_layout(tmp_path, spelling)
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'train'}, 'system': {'device': 'cpu'}}), encoding='utf-8')
    for name in ['t0', 'e0', 't1', 'e1', spelling]:
        _succeed(tmp_path, name)
    os.utime(tmp_path / 'runs/t0/result.json', ns=(1_000_000_000, 1_000_000_000))
    os.utime(tmp_path / 'runs/e0/result.json', ns=(2_000_000_000, 2_000_000_000))
    same_run = (tmp_path / 'runs/t0').samefile(layout.root)
    peer = tmp_path / 'runs/e1/result.json'
    before_peer = peer.read_bytes()
    observed = []
    class Proc:
        def __init__(self, *args, **kwargs):
            observed.append(load_result(tmp_path / 'runs/e0/result.json')['status'])
        def wait(self):
            return 1
    monkeypatch.setattr(schedule.subprocess, 'Popen', Proc)
    assert schedule.launch_jobs(tmp_path, [{'run_id': spelling, 'mode': 'train', 'device': 'cpu'}],
                                retry_failed=False, console='shared') == [1]
    assert observed == (['failed'] if same_run else ['succeeded'])
    assert peer.read_bytes() == before_peer


@pytest.mark.parametrize('spelling', ['e0', 'E0'])
@pytest.mark.parametrize('train_failures', [1, 2], ids=['retry-recovers', 'retry-exhausted'])
def test_alias_eval_waits_for_train_retry_before_dispatch(tmp_path, monkeypatch, spelling, train_failures):
    _study(tmp_path)
    layout = artifact_layout(tmp_path, spelling)
    layout.config_path.write_text(json.dumps({'algorithm': {'mode': 'eval'}, 'system': {'device': 'cpu'}}), encoding='utf-8')
    same_run = (tmp_path / 'runs/e0').samefile(layout.root)
    jobs = [{'run_id': 't0', 'mode': 'train', 'device': 'cpu'},
            {'run_id': spelling, 'mode': 'eval', 'device': 'cpu'}]
    starts = _fake_processes(monkeypatch, tmp_path, {'t0': train_failures})
    schedule.launch_jobs(tmp_path, jobs, round_size=2, batches=[jobs], console='shared')
    expected = ['t0', 't0'] + ([] if same_run and train_failures == 2 else [spelling])
    assert starts == expected


@pytest.mark.parametrize('mode', ['train', 'eval'])
def test_launch_mode_log_collapses_repeated_labels(tmp_path, monkeypatch, capsys, mode):
    jobs = [{'run_id': str(i), 'device': 'cpu', 'mode': mode} for i in range(2)]
    _fake_processes(monkeypatch, tmp_path, {})
    assert schedule.launch_jobs(tmp_path, jobs, console='shared') == [0, 0]
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith('launch:')]
    assert lines == [f'launch: wait 1/1 mode={mode}']


@pytest.mark.parametrize('include_done', [False, True])
def test_validated_cache_keeps_job_and_batch_order_without_rewriting(tmp_path, monkeypatch, include_done):
    jobs = [{'run_id': name, 'device': 'cpu', 'extra': {'keep': 0}}
            for name in ['pending-a', 'done', 'pending-b']]
    payload = {'jobs': jobs, 'batches': [[], [jobs[2], jobs[1]], [jobs[0]]],
               'num_gpus': 0, 'round': 2}
    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps(payload), encoding='utf-8')
    before = cache.read_bytes()
    monkeypatch.setattr(schedule, 'run_succeeded', lambda study, run: run == 'done')
    plan = schedule.load_launch_plan(tmp_path, init_gpu=0, num_gpus=0,
                                     round_size=2, include_done=include_done)
    assert plan['job_list'] == (jobs if include_done else [jobs[0], jobs[2]])
    assert plan['batches'] == [[jobs[2], jobs[1]] if include_done else [jobs[2]], [jobs[0]]]
    assert plan['n_jobs'] == (3 if include_done else 2)
    assert plan['job_list'][0] is not plan['batches'][-1][0]
    plan['job_list'][0]['extra']['keep'] = 9
    assert plan['batches'][-1][0]['extra'] == {'keep': 0}
    assert cache.read_bytes() == before
