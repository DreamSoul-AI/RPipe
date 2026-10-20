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
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='', MPLBACKEND='Agg')
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
def test_invalid_cached_launch_rebuilds_before_dispatch_or_process(tmp_path, monkeypatch, include_done):
    import rpipe.flow.cli as cli

    cache = tmp_path / 'scripts/jobs.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps({'jobs': [None], 'num_gpus': 0, 'round': 1}), encoding='utf-8')
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
        cli.main(['launch', str(tmp_path), '--num-gpus', '0', '--round', '1', *flags])
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
            script = ('import json,sys,time; from pathlib import Path; '
                      'p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); '
                      'p.write_text(json.dumps({"status":"succeeded","control":{},"metrics":{},"paths":{}})); '
                      'time.sleep(0.4); sys.exit(int(sys.argv[2]))')
            self.child = real_popen([sys.executable, '-c', script, str(result_path), str(exit_code)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
