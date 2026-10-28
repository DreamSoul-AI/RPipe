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
              'rpipe.structure.system', 'rpipe.flow.native', 'rpipe.flow.huggingface')
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
    from rpipe import api
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


def test_launch_unknown_owner_stops_new_dispatch(source_study, run_root, monkeypatch):
    from rpipe import api
    from rpipe.structure.contracts import RunOutcome

    specs = planning.plan(source_study, run_root=run_root)
    calls = []

    def unresolved(spec, **kwargs):
        calls.append(spec.id)
        return RunOutcome('unknown')

    monkeypatch.setattr(api, 'run', unresolved)
    assert [outcome.status for outcome in api.launch(specs)] == ['unknown']
    assert calls == [specs[0].id]


def test_launch_preserves_explicit_retry_choice(source_study, run_root, monkeypatch):
    from rpipe import api
    from rpipe.structure.contracts import RunOutcome

    specs = planning.plan(source_study, run_root=run_root)
    calls = []
    def execute(spec, **kwargs):
        calls.append(kwargs['retry'])
        return RunOutcome('failed')
    monkeypatch.setattr(api, 'run', execute)
    api.launch(specs, retry=True)
    assert calls == [True, True]


@pytest.mark.parametrize('target_kind', ['source', 'input'])
def test_process_rejects_summary_link_before_writing_source_or_input(
        source_study, run_root, tmp_path, file_bytes, monkeypatch, target_kind):
    from rpipe import api
    from rpipe.flow import study
    from rpipe.structure.artifact.io import load_config, write_config
    from rpipe.structure.control import AdmissionError

    inputs = run_root / 'shared' / 'input'
    inputs.mkdir(parents=True)
    (inputs / 'process.json').write_bytes(b'original input')
    (source_study / 'process.json').write_bytes(b'original source')
    config = load_config(source_study / 'experiment_config.yaml')
    config['data']['path'] = str(inputs)
    write_config(source_study / 'experiment_config.yaml', config)
    spec = planning.plan(source_study, run_root=run_root)[0]
    spec.study_root.mkdir(parents=True)
    target = source_study if target_kind == 'source' else inputs
    (spec.study_root / 'summary').symlink_to(target, target_is_directory=True)
    peer = tmp_path / 'peer'
    peer.write_bytes(b'peer untouched')
    before_source, before_inputs = file_bytes(source_study), file_bytes(inputs)
    hooks = []
    monkeypatch.setattr(study, 'run_study_phase', lambda *args, **kwargs: hooks.append(args))
    try:
        with pytest.raises(AdmissionError):
            api.process([spec])
    finally:
        assert file_bytes(source_study) == before_source
        assert file_bytes(inputs) == before_inputs
        assert peer.read_bytes() == b'peer untouched'
        assert not hooks


def test_process_rechecks_source_before_output_or_hook(
        source_study, run_root, file_bytes, monkeypatch):
    from rpipe import api
    from rpipe.flow import study
    from rpipe.structure.control import SourceMismatch

    spec = planning.plan(source_study, run_root=run_root)[0]
    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('# changed after planning\n')
    before = file_bytes(source_study)
    hooks = []
    monkeypatch.setattr(study, 'run_study_phase', lambda *args, **kwargs: hooks.append(args))
    with pytest.raises(SourceMismatch):
        api.process([spec])
    assert file_bytes(source_study) == before
    assert not run_root.exists()
    assert not hooks
