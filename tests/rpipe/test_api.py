"""The public API can inspect a declaration without importing scientific backends."""
from rpipe.structure import make as planning

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_api_plan_fresh_process_imports_no_scientific_backend_or_payload(
        source_study, run_root, tmp_path):
    script = tmp_path / 'inspect_plan.py'
    script.write_text('''import importlib.abc, sys
class RejectBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'torchvision', 'transformers', 'datasets', 'kornia'}:
            raise AssertionError('unexpected scientific import: ' + fullname)
sys.meta_path.insert(0, RejectBackend())
from rpipe.structure.make import plan
specs = plan(sys.argv[1], run_root=sys.argv[2])
assert len(specs) == 2
scientific = ('rpipe.structure.data', 'rpipe.structure.model', 'rpipe.structure.algorithm',
              'rpipe.structure.system', 'rpipe.flow.stages', 'rpipe.flow.worker')
assert not any(name == prefix or name.startswith(prefix + '.')
               for name in sys.modules for prefix in scientific)
''', encoding='utf-8')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    child = subprocess.run([sys.executable, '-B', str(script), str(source_study), str(run_root)],
                           env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stdout + child.stderr
    assert not run_root.exists()


def test_make_mixed_study_versions_rejects_before_any_output(source_study, run_root):
    from dataclasses import replace
    from rpipe.structure.make import publish, plan
    from rpipe.structure.control import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(AdmissionError):
        publish([spec, replace(spec, version='another')])
    assert not run_root.exists()


def test_make_owns_run_while_writing_config(source_study, run_root, monkeypatch):
    from rpipe.structure.artifact.io import Ownership
    from rpipe.structure.control import BusyError

    spec = planning.plan(source_study, run_root=run_root)[0]
    from rpipe.structure.artifact import io
    write_config = io.write_config
    checked = []

    def write_under_ownership(path, config):
        with pytest.raises(BusyError):
            Ownership(spec).acquire()
        checked.append(path)
        return write_config(path, config)

    monkeypatch.setattr(io, 'write_config', write_under_ownership)
    planning.publish([spec])
    assert checked == [spec.run_dir / 'config.yaml']
    assert not (spec.run_dir / '.execution').exists()
