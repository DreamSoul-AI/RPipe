"""Cached advertised metadata must describe the Run that run-one will load."""
import json
import os
from pathlib import Path
import sys

import pytest

from _process_tree import ProcessTree
from rpipe.structure.artifact import write_config
from rpipe.structure.make import expand_study
from rpipe.structure.make.schedule import load_launch_plan, write_jobs_json

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_cli,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]
ROOT = Path(__file__).resolve().parents[3]

# The public parser, scheduler, Run config read, Runner, locks and writer are real.
# Replace scientific phases with a single bounded CPU observation.
CLI = '''import importlib, json, runpy, sys
from pathlib import Path
from rpipe.structure.artifact import load_config
from rpipe.structure.artifact.provenance import load_provenance
def prepare(ctx):
    cfg = load_config(ctx.layout.config_path)
    (ctx.layout.root / 'payload.json').write_text(json.dumps(cfg))
    ctx.state['result_draft'] = {'status':'succeeded','control':{},'metrics':{},'paths':{},
                               'provenance':load_provenance(ctx.study_dir)['digest']}
for phase in ('prepare','execute','collect','summarize','process'):
    importlib.import_module('rpipe.flow.' + phase).run = prepare if phase == 'prepare' else lambda ctx: None
from rpipe.flow import cli
def remake(args):
    (args.study_dir / 'rebuild-requested').write_text('invalid cached metadata')
    raise SystemExit(23)
cli._make_from_args = remake
cli.process_study = lambda study: {'complete': True}
import rpipe.structure.make.schedule as schedule
native = schedule.subprocess.Popen
def launch(command, **kwargs):
    return native([sys.executable, __file__, *command[3:]], **kwargs)
schedule.subprocess.Popen = launch
assert 'torch' not in sys.modules
runpy.run_module('rpipe', run_name='__main__')
'''


@pytest.mark.parametrize('include_done', [False, True])
@pytest.mark.parametrize('cached', ['foreign', 'missing', 'omitted', 'matching-train', 'matching-eval'])
def test_public_mode_filter_checks_actual_run_config(tmp_path, include_done, cached):
    actual_mode = 'eval' if cached == 'matching-eval' else 'train'
    script = tmp_path / 'cpu_cli.py'
    script.write_text(CLI, encoding='utf-8')
    write_config(tmp_path / 'study.yaml', {'study': 'cached-identity', 'seeds': [0],
                 'flow': {'prepare_shared': False}})
    write_config(tmp_path / 'experiment_config.yaml', {'version': 'cached-identity-v1',
        'data': {'name': 'Toy', 'source': 'stub'}, 'model': {'name': 'none', 'source': 'unused'},
        'algorithm': {'mode': actual_mode, 'source': 'unused'}, 'system': {'device': 'cpu'}})
    config = expand_study(tmp_path)['configs'][0]
    run = config.parent
    result = run / 'result.json'
    result.write_text('{"status":"failed","error":"prior attempt retained"}\n', encoding='utf-8')
    execution = run / 'execution.json'
    execution.write_text('{"attempt_token":"prior-owner"}\n', encoding='utf-8')
    process = tmp_path / 'process.json'
    process.write_text('{"prior":"Study process retained"}\n', encoding='utf-8')
    other = tmp_path / 'other.yaml'
    other.write_text('algorithm: {mode: eval}\nsystem: {device: cpu}\n', encoding='utf-8')
    job = {'run_id': run.name, 'mode': 'train' if cached == 'matching-train' else 'eval', 'device': 'cpu'}
    if cached != 'omitted':
        job['config'] = str(other if cached == 'foreign' else tmp_path / 'missing.yaml' if cached == 'missing' else config)
    cache = write_jobs_json(tmp_path, [job], round_size=1, init_gpu=0, num_gpus=0)
    before = {path: path.read_bytes() for path in (config, result, execution, process, cache, other,
              tmp_path / 'index.json', tmp_path / 'provenance.json',
              tmp_path / 'study.yaml', tmp_path / 'experiment_config.yaml')}
    command = [sys.executable, str(script), 'launch', str(tmp_path), '--mode', 'eval',
               '--num-gpus', '0', '--round', '1', '--console', 'shared', '--no-retry']
    if include_done:
        command.append('--include-done')
    tree = ProcessTree()
    try:
        with (tmp_path / 'cli.stdout').open('wb') as out, (tmp_path / 'cli.stderr').open('wb') as err:
            child = tree.start(command, cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / 'src'),
                CUDA_VISIBLE_DEVICES='-1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                OMP_NUM_THREADS='1', MKL_NUM_THREADS='1'), stdout=out, stderr=err)
            code = tree.wait(8)
        invalid = cached in {'foreign', 'missing', 'omitted'}
        observed = {'cached': cached, 'include_done': include_done, 'actual_mode': actual_mode,
                    'advertised_mode': job['mode'], 'exit_code': code,
                    'payload': json.loads((run / 'payload.json').read_text()) if (run / 'payload.json').exists() else None,
                    'rebuild_requested': (tmp_path / 'rebuild-requested').exists(),
                    'protected_unchanged': all(path.read_bytes() == raw for path, raw in before.items())}
        (tmp_path / 'observation.json').write_text(json.dumps(observed, indent=2), encoding='utf-8')
        assert code == (23 if invalid else 0), observed
        assert observed['rebuild_requested'] == invalid
        assert (observed['payload'] is not None) == (cached == 'matching-eval')
        if cached != 'matching-eval':
            assert observed['protected_unchanged'], observed
        assert config.read_bytes() == before[config] and cache.read_bytes() == before[cache]
    finally:
        owned = tree.terminate(timeout=3)
        if tree.proc is not None:
            tree.proc.wait(timeout=3)
        tree.close()
        (tmp_path / 'owned.json').write_text(json.dumps({'owned_before_cleanup': owned, 'cleanup_confirmed': True}))


@pytest.mark.parametrize('cached', ['foreign', 'missing', 'omitted', 'matching'])
def test_cached_device_checks_actual_nested_run_setting(tmp_path, cached):
    config = tmp_path / 'runs/one/config.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('algorithm: {mode: eval}\nsystem: {device: cuda, config: {device: cpu}}\n', encoding='utf-8')
    other = tmp_path / 'other.yaml'
    other.write_text('algorithm: {mode: eval}\nsystem: {device: cuda}\n', encoding='utf-8')
    job = {'run_id': 'one', 'mode': 'eval', 'device': 'cpu' if cached == 'matching' else 'cuda'}
    if cached != 'omitted':
        job['config'] = str(other if cached == 'foreign' else tmp_path / 'missing.yaml' if cached == 'missing' else config)
    cache = write_jobs_json(tmp_path, [job], round_size=1, init_gpu=0, num_gpus=0)
    before = {path: path.read_bytes() for path in (config, other, cache)}
    plan = load_launch_plan(tmp_path, init_gpu=0, num_gpus=0, round_size=1, include_done=True)
    assert (plan is not None) == (cached == 'matching')
    assert {path: path.read_bytes() for path in before} == before
