"""Artifact interfaces load without executing scientific or lifecycle code."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_importing_artifact_apis_does_not_load_tensor_codecs_or_execution(tmp_path):
    code = '''import importlib.abc, sys
class ReadInterfaces(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'numpy', 'torchvision', 'transformers', 'matplotlib'}:
            raise AssertionError('scientific import before a numerical request: ' + fullname)
        if fullname.startswith('rpipe.flow') or fullname.endswith('.factory'):
            raise AssertionError('execution or construction import: ' + fullname)
sys.meta_path.insert(0, ReadInterfaces())
from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
from rpipe.structure.artifact.readout import read_run, refs_from_index, load_curve_series
from rpipe.structure.artifact.comparison import compare, compare_state
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3] / 'src')
    child = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
