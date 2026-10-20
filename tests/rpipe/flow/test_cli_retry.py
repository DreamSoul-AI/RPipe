"""Issue #41: native launch can opt out of retry without changing the default."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from rpipe.flow.cli import main
from rpipe.structure.artifact import write_config

pytestmark = [
    pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
    pytest.mark.flow_layer, pytest.mark.module_cli,
    pytest.mark.cost(cost_class='c2'),
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.mark.parametrize('study_phases', [False, True])
def test_native_launch_drift_after_parent_gate_preserves_success_and_token(tmp_path, monkeypatch, study_phases):
    import rpipe.flow.cli as cli
    import rpipe.structure.make.schedule as schedule
    from _process_tree import ProcessTree
    from rpipe.structure.artifact import artifact_layout, write_result
    from rpipe.structure.artifact.provenance import load_provenance
    from rpipe.structure.artifact.execution import start_execution
    from rpipe.structure.make import expand_study
    asset = tmp_path / 'admission.txt'
    asset.write_text('frozen input', encoding='utf-8')
    write_config(tmp_path / 'study.yaml', {'study':'admission', 'seeds':[0], 'freeze':True,
        'flow':{'prepare_shared':False, 'study_phases':study_phases}, 'provenance':{'include':['admission.txt']}})
    write_config(tmp_path / 'experiment_config.yaml', {'version':'admission-v1',
        'data':{'name':'Toy','source':'stub'}, 'model':{'name':'none','source':'unreachable'},
        'algorithm':{'mode':'inference','source':'unreachable'}, 'system':{'device':'cpu'}})
    config = expand_study(tmp_path)['configs'][0]
    layout = artifact_layout(tmp_path, config.parent.name)
    write_result(layout.result_path, {'status':'succeeded','control':{},'metrics':{},'paths':{},
                                    'provenance':load_provenance(tmp_path)['digest']})
    start_execution(layout.root, 'prior-owner')
    before = layout.result_path.read_bytes()
    token_before = (layout.root / 'execution.json').read_bytes()
    job = {'run_id':config.parent.name,'config':str(config),'mode':'inference','device':'cpu'}
    schedule.write_jobs_json(tmp_path, [job], round_size=1, init_gpu=0, num_gpus=0)
    children = []
    actual_popen = subprocess.Popen
    def launch(command, **kwargs):
        asset.write_text('changed after parent gate', encoding='utf-8')
        tree = ProcessTree()
        with monkeypatch.context() as native:
            native.setattr(subprocess, 'Popen', actual_popen)
            with (tmp_path / f'child-{len(children)}.stdout').open('wb') as out, (tmp_path / f'child-{len(children)}.stderr').open('wb') as err:
                child = tree.start(command, stdout=out, stderr=err, **kwargs)
        wait = child.wait
        child.wait = lambda timeout=None: wait(timeout=8 if timeout is None else timeout)
        children.append((tree, child))
        return child
    monkeypatch.setattr(schedule.subprocess, 'Popen', launch)
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[3] / 'src'))
    monkeypatch.setattr(cli, 'process_study', lambda *_: pytest.fail('refused child reached Study process'))
    try:
        assert cli.main(['launch',str(tmp_path),'--num-gpus','0','--round','1','--console','shared','--include-done']) == 1
        assert len(children) == 1
        assert b'ProvenanceChangedError' in (tmp_path / 'child-0.stderr').read_bytes()
        assert layout.result_path.read_bytes() == before
        assert (layout.root / 'execution.json').read_bytes() == token_before
    finally:
        for tree, child in children:
            tree.terminate(timeout=3)
            child.wait(timeout=3)
            tree.close()


@pytest.mark.parametrize('refusal', ['busy', 'unreceived', 'newer', 'locked'])
@pytest.mark.parametrize('entry', ['cli', 'bash'])
@pytest.mark.parametrize('child_exit', [0, 7])
def test_public_launch_keeps_nonzero_refusal_when_old_result_succeeded(tmp_path, monkeypatch, refusal, entry, child_exit):
    import contextlib
    import rpipe.flow.cli as cli
    import rpipe.flow.process as process
    import rpipe.structure.make.schedule as schedule
    from rpipe.structure.api.artifact_api import RUN_ATTEMPT_ENV, run_lock, start_execution
    from rpipe.structure.artifact import artifact_layout, write_result
    layout = artifact_layout(tmp_path, 'old-success')
    write_config(layout.config_path, {'algorithm': {'mode': 'train'}, 'system': {'device': 'cpu'}})
    write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    before = layout.result_path.read_bytes()
    job = {'run_id': 'old-success', 'mode': 'train', 'device': 'cpu', 'config': str(layout.config_path)}
    schedule.write_jobs_json(tmp_path, [job], round_size=1, init_gpu=0, num_gpus=0)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '-1')
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[3] / 'src'))
    monkeypatch.setattr(cli, '_provenance_gate', lambda *_: True)
    def forbidden_process(*_):
        pytest.fail('refused launch must stop before Study process')
    monkeypatch.setattr(cli, 'process_study', forbidden_process)
    monkeypatch.setattr(process, 'run_study', forbidden_process)
    monkeypatch.setattr(cli, '_print_launch_counts', lambda *_: None)
    actual_popen = subprocess.Popen
    children = []
    held = contextlib.ExitStack()
    def launch(*args, **kwargs):
        assert refusal != 'busy', 'busy Run launched a child'
        if refusal == 'locked':
            held.enter_context(run_lock(layout.root))
            start_execution(layout.root, kwargs['env'][RUN_ATTEMPT_ENV])
        script = f'raise SystemExit({child_exit})'
        if refusal == 'newer':
            script = r'''
import json, os, sys
from pathlib import Path
from rpipe.flow import FlowContext, FlowRunner
import rpipe.flow.collect as collect
from rpipe.structure.api.artifact_api import RUN_ATTEMPT_ENV
from rpipe.structure.artifact import artifact_layout, write_result
root = Path(sys.argv[1])
layout = artifact_layout(root, 'old-success')
handoff = os.environ[RUN_ATTEMPT_ENV]
generations = iter([1, 2])
def phase(ctx):
    write_result(ctx.layout.result_path, {'status': 'succeeded', 'control': {},
                 'metrics': {'generation': next(generations)}, 'paths': {}})
collect.run = phase
ctx = FlowContext(root, layout, {})
FlowRunner(['collect']).run(ctx)
FlowRunner(['collect']).run(ctx)
assert json.loads((layout.root / 'execution.json').read_text())['attempt_token'] != handoff
(layout.root / 'later-success.bytes').write_bytes(layout.result_path.read_bytes())
raise SystemExit(int(sys.argv[2]))
'''
        child = actual_popen([sys.executable, '-c', script, str(tmp_path), str(child_exit)], env=kwargs['env'])
        children.append(child)
        return child
    monkeypatch.setattr(schedule.subprocess, 'Popen', launch)
    try:
        with run_lock(layout.root) if refusal == 'busy' else contextlib.nullcontext():
            if entry == 'cli':
                assert cli.main(['launch', str(tmp_path), '--num-gpus', '0', '--round', '1',
                                 '--console', 'shared', '--include-done']) == 1
            else:
                chunk = schedule.render_bash(tmp_path, [job], python_exe=sys.executable,
                                             round_size=1, split_round=1)[0]
                body = chunk.split("<<'RPIPE_LAUNCH'\n", 1)[1].rsplit('\nRPIPE_LAUNCH', 1)[0]
                with pytest.raises(SystemExit) as stopped:
                    exec(compile(body, 'launch.sh', 'exec'), {})
                assert stopped.value.code == 1
        assert layout.result_path.read_bytes() == ((layout.root / 'later-success.bytes').read_bytes()
                                                   if refusal == 'newer' else before)
        assert len(children) == (0 if refusal == 'busy' else 1)
    finally:
        held.close()
        for child in children:
            child.wait(timeout=5)


@pytest.mark.parametrize('no_retry', [False, True], ids=['default', 'no-retry'])
@pytest.mark.parametrize('fail', [True, False], ids=['failure', 'success'])
def test_native_launch_retry_policy_preserves_attempts_and_failure(tmp_path, no_retry, fail):
    """Real CLI -> scheduler -> single-use Algorithm; inspect attempts, terminal error and logs."""
    shutil.copyfile(Path(__file__).parents[2] / '_data/launch_retry_recipe.py', tmp_path / 'recipe.py')
    write_config(tmp_path / 'study.yaml', {
        'study': 'retry_cli', 'seeds': [0], 'recipe': 'recipe.py', 'freeze': True,
        'flow': {'prepare_shared': False},
    })
    write_config(tmp_path / 'experiment_config.yaml', {
        'version': 'issue41-v1', 'data': {'name': 'Toy', 'source': 'stub'},
        'model': {'name': 'none', 'source': 'retry_fixture'},
        'algorithm': {'mode': 'inference', 'source': 'retry_fixture', 'config': {'fail': fail}},
        'system': {'device': 'cpu'},
    })
    flags = ['--num-gpus', '0', '--round', '1', '--console', 'shared']
    assert main(['make', str(tmp_path), *flags]) == 0
    jobs_path = tmp_path / 'scripts/jobs.json'
    jobs_before = jobs_path.read_bytes()
    run_id = json.loads(jobs_before)['jobs'][0]['run_id']
    run = tmp_path / 'runs' / run_id
    command = [sys.executable, '-m', 'rpipe', 'launch', str(tmp_path), *flags]
    if no_retry:
        command.append('--no-retry')
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='-1', MPLBACKEND='Agg',
               PYTHONPATH=str(Path(__file__).resolve().parents[3] / 'src'))
    result = subprocess.run(command, env=env, capture_output=True, timeout=45)
    (tmp_path / 'launch.stdout').write_bytes(result.stdout)
    (tmp_path / 'launch.stderr').write_bytes(result.stderr)
    assert result.returncode == int(fail), (result.stdout, result.stderr)
    attempts = 2 if fail and not no_retry else 1
    assert (run / 'assets/attempts.txt').read_text().splitlines() == ['attempt'] * attempts
    body = json.loads((run / 'result.json').read_text(encoding='utf-8'))
    assert body['status'] == ('failed' if fail else 'succeeded')
    if fail:
        assert ('ORIGINAL_FAILURE' if no_retry else 'FileExistsError') in body['error']
        assert 'ORIGINAL_FAILURE' in (run / 'assets/logs/run.log').read_text(encoding='utf-8')
        output = result.stdout + result.stderr
        assert b'RuntimeError: ORIGINAL_FAILURE' in output
        assert (b'FileExistsError' in output) == (not no_retry)
    assert (b'launch: retry' in result.stdout) == (attempts == 2)
    assert jobs_path.read_bytes() == jobs_before
    if fail and no_retry:
        # The opt-out belongs to this invocation; a later explicit launch keeps its default retry.
        later = subprocess.run(command[:-1], env=env, capture_output=True, timeout=45)
        (tmp_path / 'later-default.stdout').write_bytes(later.stdout)
        (tmp_path / 'later-default.stderr').write_bytes(later.stderr)
        assert later.returncode == 1 and b'launch: retry' in later.stdout
        assert (run / 'assets/attempts.txt').read_text().splitlines() == ['attempt'] * 3
        assert 'FileExistsError' in json.loads((run / 'result.json').read_text(encoding='utf-8'))['error']
        assert jobs_path.read_bytes() == jobs_before
    if not fail:
        prior = (run / 'result.json').read_bytes()
        skipped = subprocess.run(command, env=env, capture_output=True, timeout=30)
        assert skipped.returncode == 0, (skipped.stdout, skipped.stderr)
        assert b'nothing to launch' in skipped.stdout
        assert (run / 'result.json').read_bytes() == prior
        assert (run / 'assets/attempts.txt').read_text().splitlines() == ['attempt']


@pytest.mark.parametrize('include_done', [False, True])
@pytest.mark.parametrize('damage', ['invalid-job', 'negative-round'])
def test_invalid_cached_launch_rebuilds_before_dispatch_or_process(tmp_path, monkeypatch, include_done, damage):
    import rpipe.flow.cli as cli

    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps({'jobs': [None] if damage == 'invalid-job' else [{'run_id': 'one'}],
                                 'num_gpus': 0, 'round': 1 if damage == 'invalid-job' else -1}), encoding='utf-8')
    before = cache.read_bytes()
    boundary = RuntimeError('rebuild boundary reached')
    def rebuild(*args, **kwargs):
        raise boundary
    def forbidden(*args, **kwargs):
        pytest.fail('invalid cached plan reached downstream side effects')
    monkeypatch.setattr(cli, '_make_from_args', rebuild)
    monkeypatch.setattr(cli, '_provenance_gate', forbidden)
    monkeypatch.setattr(cli, 'launch_jobs', forbidden)
    monkeypatch.setattr(cli, 'process_study', forbidden)
    flags = ['--include-done'] if include_done else []
    with pytest.raises(RuntimeError) as caught:
        cli.main(['launch', str(tmp_path), '--num-gpus', '0', '--round', '0', *flags])
    assert caught.value is boundary
    assert cache.read_bytes() == before
    assert not (tmp_path / 'process.json').exists()



def test_native_launch_spawn_failure_finishes_its_already_started_child(tmp_path, monkeypatch):
    import rpipe.flow.cli as cli
    import rpipe.structure.make.schedule as schedule

    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    jobs = [{'run_id': name, 'mode': 'train', 'device': 'cpu'} for name in ['first', 'second']]
    cache.write_text(json.dumps({'jobs': jobs, 'num_gpus': 0, 'round': 2}), encoding='utf-8')
    before = cache.read_bytes()
    real_popen = subprocess.Popen
    children = []
    primary = OSError('second native spawn refused')
    def spawn(args, **kwargs):
        if args[-1] == 'second':
            raise primary
        child = real_popen([sys.executable, '-c', 'import time; time.sleep(0.4)'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(child)
        return child
    monkeypatch.setattr(cli, '_provenance_gate', lambda *_: True)
    monkeypatch.setattr(schedule.subprocess, 'Popen', spawn)
    try:
        with pytest.raises(OSError) as caught:
            cli.main(['launch', str(tmp_path), '--num-gpus', '0', '--round', '2', '--console', 'shared'])
        assert caught.value is primary
        assert len(children) == 1 and children[0].poll() == 0
        assert cache.read_bytes() == before
        assert not (tmp_path / 'process.json').exists()
    finally:
        # Preserve red-test evidence while allowing the isolated CPU child to finish naturally.
        for child in children:
            child.communicate(timeout=10)


@pytest.mark.parametrize('cancelled', [False, True], ids=['oserror', 'interrupt'])
@pytest.mark.parametrize('exit_code', [0, 7], ids=['child-success', 'child-nonzero'])
def test_native_launch_recovers_transient_wait_before_propagating_primary(tmp_path, monkeypatch, cancelled, exit_code):
    import rpipe.flow.cli as cli
    import rpipe.structure.make.schedule as schedule
    from rpipe.structure.artifact.result import load_result

    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps({'jobs': [{'run_id': 'only', 'mode': 'train', 'device': 'cpu'}],
                                'num_gpus': 0, 'round': 1}), encoding='utf-8')
    before = cache.read_bytes()
    result_path = tmp_path / 'runs/only/result.json'
    real_popen = subprocess.Popen
    children, waits = [], []
    primary = KeyboardInterrupt('first wait interrupted') if cancelled else OSError('first wait temporarily unavailable')
    class Proc:
        def __init__(self, *args, **kwargs):
            from rpipe.structure.api.artifact_api import RUN_ATTEMPT_ENV
            script = ('import json,sys,time; from pathlib import Path; '
                      'from rpipe.structure.artifact.execution import run_lock,start_execution; '
                      'p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); '
                      'lease=run_lock(p.parent); lease.__enter__(); start_execution(p.parent,sys.argv[3]); '
                      'p.write_text(json.dumps({"status":"succeeded","control":{},"metrics":{},"paths":{}})); '
                      'time.sleep(0.4); lease.__exit__(None,None,None); sys.exit(int(sys.argv[2]))')
            self.child = real_popen([sys.executable, '-c', script, str(result_path), str(exit_code), kwargs['env'][RUN_ATTEMPT_ENV]],
                                   env=kwargs['env'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            children.append(self.child)
        def wait(self):
            waits.append('wait')
            if len(waits) == 1:
                raise primary
            return self.child.wait()
    monkeypatch.setattr(cli, '_provenance_gate', lambda *_: True)
    monkeypatch.setattr(schedule.subprocess, 'Popen', Proc)
    try:
        with pytest.raises(type(primary)) as caught:
            cli.main(['launch', str(tmp_path), '--num-gpus', '0', '--round', '1', '--console', 'shared'])
        assert caught.value is primary
        assert waits == ['wait', 'wait']
        assert len(children) == 1 and children[0].poll() == exit_code
        result = load_result(result_path)
        assert result['status'] == ('failed' if exit_code else 'succeeded')
        if exit_code:
            assert result['error'] == 'run-one exited with code 7'
        assert cache.read_bytes() == before
        assert not (tmp_path / 'process.json').exists()
    finally:
        for child in children:
            child.communicate(timeout=10)


@pytest.mark.parametrize('entry', ['cli', 'bash'])
@pytest.mark.parametrize('busy', [True, False], ids=['busy-eval', 'idle-eval'])
@pytest.mark.parametrize('parent_state', ['failed', 'newer'])
def test_native_launch_reports_busy_stale_eval_outside_selected_jobs(tmp_path, entry, busy, parent_state):
    """Public launch -> unselected stale eval correction refuses -> preserve prior Study process."""
    import contextlib
    from _process_tree import ProcessTree
    from rpipe.structure.artifact import load_config, load_result, write_result
    from rpipe.structure.artifact.execution import run_lock, start_execution
    from rpipe.structure.artifact.provenance import load_provenance
    from rpipe.structure.make import expand_study, plan_jobs
    from rpipe.structure.make.schedule import render_bash, sibling_train_dependency, write_jobs_json

    shutil.copyfile(Path(__file__).parents[2] / '_data/launch_retry_recipe.py', tmp_path / 'recipe.py')
    write_config(tmp_path / 'study.yaml', {'study': 'stale-selection', 'seeds': [0],
        'recipe': 'recipe.py', 'freeze': True, 'flow': {'prepare_shared': False},
        'axes': {'algorithm.mode': ['train', 'eval', 'inference']}})
    write_config(tmp_path / 'experiment_config.yaml', {'version': 'stale-selection-v1',
        'data': {'name': 'Toy', 'source': 'stub'}, 'model': {'name': 'none', 'source': 'retry_fixture'},
        'algorithm': {'mode': 'inference', 'source': 'retry_fixture', 'config': {'fail': False}},
        'system': {'device': 'cpu'}})
    configs = expand_study(tmp_path)['configs']
    by_mode = {load_config(config)['algorithm']['mode']: config.parent for config in configs}
    train, evaluation, selected = (by_mode[mode] for mode in ('train', 'eval', 'inference'))
    assert sibling_train_dependency(tmp_path, evaluation.name) == train.name
    provenance = load_provenance(tmp_path)['digest']
    for root in (train, evaluation):
        write_result(root / 'result.json', {'status': 'succeeded', 'control': {}, 'paths': {},
                     'metrics': {'accuracy': 42}, 'provenance': provenance})
    if parent_state == 'failed':
        write_result(train / 'result.json', {'status': 'failed', 'error': 'prior train failed'})
    os.utime(evaluation / 'result.json', ns=(1_000_000_000, 1_000_000_000))
    os.utime(train / 'result.json', ns=(2_000_000_000, 2_000_000_000))
    start_execution(evaluation, 'prior-eval-owner')
    protected = {path: path.read_bytes() for path in (evaluation / 'result.json', evaluation / 'execution.json')}
    jobs = plan_jobs(tmp_path, configs, init_gpu=0, num_gpus=0, include_done=True)
    selected_jobs = [job for job in jobs if job['mode'] == 'inference']
    assert len(selected_jobs) == 1 and selected_jobs[0]['run_id'] == selected.name
    write_jobs_json(tmp_path, jobs, round_size=1, init_gpu=0, num_gpus=0)
    process = tmp_path / 'process.json'
    prior = b'{"retained": "prior Study process"}\n'
    process.write_bytes(prior)
    inputs = {path: path.read_bytes() for path in [tmp_path / 'study.yaml', tmp_path / 'experiment_config.yaml',
        tmp_path / 'index.json', tmp_path / 'provenance.json', *configs]}
    if entry == 'cli':
        command = [sys.executable, '-m', 'rpipe', 'launch', str(tmp_path), '--mode', 'inference',
                   '--num-gpus', '0', '--round', '1', '--console', 'shared', '--no-retry']
    else:
        script = render_bash(tmp_path, selected_jobs, python_exe=sys.executable, round_size=1, split_round=1)[0]
        body = script.split("<<'RPIPE_LAUNCH'\n", 1)[1].rsplit('\nRPIPE_LAUNCH', 1)[0]
        command = [sys.executable, '-c', body]
    tree = ProcessTree()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3] / 'src'),
               CUDA_VISIBLE_DEVICES='-1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', MPLBACKEND='Agg')
    try:
        with run_lock(evaluation) if busy else contextlib.nullcontext():
            with (tmp_path / 'launch.stdout').open('wb') as out, (tmp_path / 'launch.stderr').open('wb') as err:
                tree.start(command, cwd=Path(__file__).resolve().parents[3], env=env, stdout=out, stderr=err)
                code = tree.wait(25)
        output = (tmp_path / 'launch.stdout').read_text(encoding='utf-8', errors='replace')
        assert load_result(selected / 'result.json')['status'] == 'succeeded', output
        assert (selected / 'assets/attempts.txt').read_text().splitlines() == ['attempt']
        assert {path: path.read_bytes() for path in inputs} == inputs
        if busy:
            assert code == 1, output
            assert process.read_bytes() == prior
            assert {path: path.read_bytes() for path in protected} == protected
        else:
            assert code == (0 if entry == 'cli' else 1), output
            assert process.read_bytes() != prior
            assert load_result(evaluation / 'result.json')['status'] == 'failed'
            assert load_result(evaluation / 'result.json')['metrics'] == {'accuracy': 42}
    finally:
        tree.terminate(timeout=5)
        if tree.proc is not None:
            tree.proc.wait(timeout=5)
        tree.close()
