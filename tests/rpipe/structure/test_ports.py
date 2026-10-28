"""Scientific capability declarations import without concrete implementations."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_api,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_port_import_does_not_load_science_factories_or_execution(tmp_path):
    code = '''import importlib.abc, sys
class PortOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'torch', 'torchvision', 'transformers', 'kornia'}:
            raise AssertionError('scientific import: ' + fullname)
        if fullname.startswith('rpipe.flow') or fullname.endswith('.factory'):
            raise AssertionError('concrete implementation import: ' + fullname)
sys.meta_path.insert(0, PortOnly())
from rpipe.structure.api import (DataPort, ModelPort, AlgorithmPort, SystemPort,
                                 TrackerPort, CancelCheck, ComputationObservation)
assert callable(AlgorithmPort.compute)
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3] / 'src')
    child = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
