"""The module CLI entry stays usable in an environment without scientific backends."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_module_help_does_not_import_scientific_backends(tmp_path):
    code = '''import importlib.abc, runpy, sys
class RejectBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'torchvision', 'transformers', 'datasets', 'kornia'}:
            raise AssertionError('unexpected scientific import: ' + fullname)
sys.meta_path.insert(0, RejectBackend())
sys.argv = ['rpipe', '--help']
try:
    runpy.run_module('rpipe', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError('module entry did not return an exit status')
'''
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    child = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
    assert 'usage:' in child.stdout.lower()
    assert not list(tmp_path.iterdir())
