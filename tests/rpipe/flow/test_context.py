"""Runtime requests and contexts stay independent from scientific implementations."""
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_execution,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('field,value', [
    ('timeout_seconds', math.nan), ('timeout_seconds', math.inf),
    ('timeout_seconds', -1), ('memory_bytes', -1), ('memory_bytes', True),
])
def test_budget_invalid_limit_is_rejected(field, value):
    from rpipe.flow.context import Budget

    with pytest.raises((TypeError, ValueError)):
        Budget(**{field: value}).validate()



def test_context_and_readout_imports_do_not_load_science_or_execution(tmp_path):
    code = """import importlib.abc, sys
class PureImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'numpy', 'transformers', 'datasets'}:
            raise AssertionError('scientific import: ' + fullname)
        if fullname in {'rpipe.flow.run', 'rpipe.flow.worker', 'rpipe.flow.stages'}:
            raise AssertionError('execution import: ' + fullname)
sys.meta_path.insert(0, PureImports())
from rpipe.flow.context import Budget, Cancellation, RunContext, ScienceContext, StudyProcessContext
from rpipe.structure.artifact.io import project_execution
from rpipe.structure.artifact.readout import read_run
assert Budget().timeout_seconds is None
assert not Cancellation().is_set()
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3] / 'src')
    child = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
