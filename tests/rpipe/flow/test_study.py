from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from rpipe.flow.study import run_study_phase
from rpipe.flow.study import study_phase_files, study_phases_enabled

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'), pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('package', [False, True])
def test_phase_and_relative_helpers_leave_source_tree_unchanged(tmp_path, monkeypatch, package):
    monkeypatch.setattr(sys, 'dont_write_bytecode', False)
    flow = tmp_path / 'flow'
    flow.mkdir()
    (flow / '__init__.py').write_text('from .helper import VALUE\n', encoding='utf-8')
    (flow / 'helper.py').write_text('VALUE = 7\n', encoding='utf-8')
    phase = flow / 'prepare.py'
    relative = '.'
    if package:
        (flow / 'prepare').mkdir()
        phase = flow / 'prepare' / '__init__.py'
        relative = '..'
    phase.write_text(
        f'from {relative}helper import VALUE\ndef run(ctx):\n    ctx.state["value"] = VALUE\n',
        encoding='utf-8')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    ctx = SimpleNamespace(state={})
    assert run_study_phase(tmp_path, 'prepare', ctx, {'flow': {'study_phases': True}})
    assert ctx.state['value'] == 7
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_relative_helpers_are_isolated_between_studies(tmp_path):
    before = set(sys.modules)
    ctx = SimpleNamespace(state={})
    for label in ('left', 'right'):
        root = tmp_path / label
        phase = root / 'flow' / 'prepare'
        phase.mkdir(parents=True)
        (root / 'helper.py').write_text(f'MARK = {label!r}\n', encoding='utf-8')
        (phase / '__init__.py').write_text('from .run import run\n', encoding='utf-8')
        (phase / 'run.py').write_text(
            'from ...helper import MARK\ndef run(ctx):\n    ctx.state["mark"] = MARK\n', encoding='utf-8')
        assert run_study_phase(root, 'prepare', ctx, {'flow': {'study_phases': True}})
        assert ctx.state['mark'] == label
    assert not {name for name in set(sys.modules) - before if name.startswith('_rpipe_study_')}


def test_disabled_study_directories_do_not_execute(tmp_path):
    phase = tmp_path / 'flow' / 'prepare'
    phase.mkdir(parents=True)
    (phase / '__init__.py').write_text('raise RuntimeError("must not import")\n', encoding='utf-8')
    assert not run_study_phase(tmp_path, 'prepare', None, {})
    assert study_phase_files(tmp_path, {}) == []


@pytest.mark.parametrize('declaration', [{'flow': None}, {'flow': []}, {'flow': {'study_phases': 'true'}},
                                      {'flow': {'study_phases': 1}}])
def test_invalid_phase_declaration_is_rejected(declaration):
    with pytest.raises(ValueError):
        study_phases_enabled(declaration)


def test_invalid_hook_does_not_leak_modules(tmp_path):
    directory = tmp_path / 'flow' / 'prepare'
    directory.parent.mkdir()
    directory.write_text('not a directory', encoding='utf-8')
    with pytest.raises(ValueError, match='directory'):
        study_phase_files(tmp_path, {'flow': {'study_phases': True}})
    directory.unlink()
    directory.mkdir()
    (directory / '__init__.py').write_text('x = 1\n', encoding='utf-8')
    before = set(sys.modules)
    with pytest.raises(AttributeError, match='run'):
        run_study_phase(tmp_path, 'prepare', None, {'flow': {'study_phases': True}})
    assert not {name for name in set(sys.modules) - before if name.startswith('_rpipe_study_')}


@pytest.mark.parametrize('flow_package', [False, True])
def test_flow_parent_and_structure_relative_imports_are_isolated(tmp_path, flow_package):
    before = set(sys.modules)
    for label in ('left', 'right'):
        root = tmp_path / label
        phase = root / 'flow' / 'prepare'
        phase.mkdir(parents=True)
        (root / 'structure').mkdir()
        (root / 'structure' / 'data.py').write_text(f'MARK = {label!r}\n', encoding='utf-8')
        (root / 'recipe.py').write_text('from .structure.data import MARK\n', encoding='utf-8')
        (root / 'flow' / 'helper.py').write_text('READY = True\n', encoding='utf-8')
        if flow_package:
            (root / 'flow' / '__init__.py').write_text('from .helper import READY\n', encoding='utf-8')
        (phase / '__init__.py').write_text('from .run import run\n', encoding='utf-8')
        (phase / 'run.py').write_text(
            'from ..helper import READY\nfrom ...recipe import MARK\n'
            'from ...structure.data import MARK as OTHER\n'
            'def run(ctx):\n    assert READY and MARK == OTHER\n    ctx.state["mark"] = MARK\n',
            encoding='utf-8')
        ctx = SimpleNamespace(state={})
        assert run_study_phase(root, 'prepare', ctx, {'flow': {'study_phases': True}})
        assert ctx.state['mark'] == label
    assert not {name for name in set(sys.modules) - before if name.startswith('_rpipe_study_')}


def test_helper_only_phase_is_not_executed(tmp_path):
    phase = tmp_path / 'flow' / 'execute'
    phase.mkdir(parents=True)
    (phase / 'speed.py').write_text('raise AssertionError("manual tool must not run")\n', encoding='utf-8')
    assert not run_study_phase(tmp_path, 'execute', None, {'flow': {'study_phases': True}})


def test_legacy_root_phase_requires_explicit_migration(tmp_path):
    phase = tmp_path / 'prepare'
    phase.mkdir()
    (phase / '__init__.py').write_text('raise AssertionError("legacy must not run")\n', encoding='utf-8')
    with pytest.raises(ValueError, match='move legacy Study phase to flow/prepare'):
        run_study_phase(tmp_path, 'prepare', None, {'flow': {'study_phases': True}})


def test_import_failure_does_not_leak_study_namespace(tmp_path):
    phase = tmp_path / 'flow' / 'prepare'
    phase.mkdir(parents=True)
    (phase / '__init__.py').write_text('from .run import run\n', encoding='utf-8')
    (phase / 'run.py').write_text('from . import missing_helper\n', encoding='utf-8')
    before = set(sys.modules)
    with pytest.raises(ImportError):
        run_study_phase(tmp_path, 'prepare', None, {'flow': {'study_phases': True}})
    assert not {name for name in set(sys.modules) - before if name.startswith('_rpipe_study_')}
