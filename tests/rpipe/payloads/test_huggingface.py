"""Optional HF lifecycle is offline and its two-step best is independently evaluated."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_hf_two_step_training_publishes_own_best_for_independent_eval(tensor_study, run_root):
    pytest.importorskip('transformers')
    pytest.importorskip('accelerate')
    import yaml
    from rpipe.api import plan, read_run, run
    from rpipe.payloads.huggingface import HuggingFacePayload
    from rpipe.payloads.checkpoints import SystemFactory
    from rpipe.structure.contracts import SystemConfig

    path = tensor_study / 'experiment_config.yaml'
    config = yaml.safe_load(path.read_text())
    config['algorithm']['source'] = 'transformers_trainer'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    train, evaluate = plan(tensor_study, run_root=run_root)
    outcome = run(train, payload=HuggingFacePayload())
    assert outcome.status == 'succeeded', outcome.error
    assert read_run(train)['execute']['steps'] == 2
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), train.assets_dir)
    best = system.load_checkpoint('best')
    assert best is not None
    evaluated = run(evaluate, payload=HuggingFacePayload())
    assert evaluated.status == 'succeeded', evaluated.error
    assert read_run(evaluate)['execute']['step'] == best['step']


def test_missing_hf_dependency_does_not_block_planning_or_reading(source_study, run_root, tmp_path):
    code = '''import importlib.abc, sys
class NoHF(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'transformers', 'datasets', 'accelerate'}:
            raise ModuleNotFoundError('optional dependency absent: ' + fullname)
sys.meta_path.insert(0, NoHF())
from rpipe.api import plan, read_run, report
from rpipe.payloads.huggingface import HuggingFacePayload
specs = plan(sys.argv[1], run_root=sys.argv[2])
assert read_run(specs[0])['status'] == 'missing'
assert specs[0].id in report(specs)
assert HuggingFacePayload is not None
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3] / 'src')
    child = subprocess.run([sys.executable, '-B', '-c', code, str(source_study), str(run_root)],
                           env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
    assert not run_root.exists()
