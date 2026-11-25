"""Impact selection preserves shared boundaries and local validation failures."""
import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]
spec = importlib.util.spec_from_file_location('rpipe_test_selection', Path(__file__).parents[1] / 'impact.py')
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


@pytest.mark.parametrize('paths,scope', [
    ([], 'empty'), (['README.md', 'AGENTS.md'], 'docs'),
    (['studies/_template/study.yaml'], 'full'), (['pyproject.toml'], 'full'),
    (['src/rpipe/flow/worker.py'], 'full'), (['tests/conftest.py'], 'full'),
    (['.github/workflows/unit-tests.yml'], 'full'),
    (['src/rpipe/structure/algorithm/metrics.py'], 'affected'),
    (['README.md', 'src/rpipe/structure/model/factory.py'], 'affected'),
    (['README.md', 'src/rpipe/structure/control.py'], 'full'),
])
def test_selection_preserves_shared_contracts(paths, scope):
    plan = selection.selection(paths)
    assert plan['scope'] == scope
    assert plan['package_required'] == (scope == 'full')
    if scope == 'affected':
        assert set(selection.JOURNEYS) <= set(plan['targets'])
        assert 'tests/studies' in plan['targets']


def test_missing_test_target_requires_full_regression():
    assert selection.selection(['tests/rpipe/test_deleted.py'])['scope'] == 'full'


def test_git_selection_includes_deleted_renamed_and_untracked_paths(monkeypatch):
    calls = []
    def output(command):
        calls.append(command)
        return (b'docs/old.md\0docs/new.md\0' if 'diff' in command
                else b'src/rpipe/flow/new.py\0')
    monkeypatch.setattr(selection.subprocess, 'check_output', output)
    paths = selection.git_paths('origin/dev')
    assert paths == ['docs/new.md', 'docs/old.md', 'src/rpipe/flow/new.py']
    assert '--no-renames' in calls[0]
    assert selection.selection(paths)['scope'] == 'full'


def test_document_failure_stops_execution_and_records_failure(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'README.md').write_text('[missing](absent.md)', encoding='utf-8')
    monkeypatch.setattr(selection, 'REPO', repo)
    assert selection.main(['--changed', 'README.md', '--execute',
                           '--results-root', str(tmp_path / 'evidence')]) == 1


def test_deleted_document_checks_inbound_references(tmp_path, monkeypatch):
    (tmp_path / 'README.md').write_text('[old](deleted.md)', encoding='utf-8')
    monkeypatch.setattr(selection, 'REPO', tmp_path)
    assert selection.check_documents(['deleted.md'])


def test_offline_profile_is_one_batch_with_explicit_costs(tmp_path, monkeypatch):
    entry_spec = importlib.util.spec_from_file_location('rpipe_offline_entry', Path(__file__).parents[1] / 'run.py')
    entry = importlib.util.module_from_spec(entry_spec)
    entry_spec.loader.exec_module(entry)
    commands = []
    def stop(command, **kwargs):
        commands.append(command)
        raise OSError('controlled stop before pytest')
    monkeypatch.setattr(entry.subprocess, 'Popen', stop)
    assert entry.main(['--offline', '--results-root', str(tmp_path)]) == 3
    assert len(commands) == 1
    assert '(unit or integration or e2e)' in commands[0][commands[0].index('-m', 2) + 1]
    assert commands[0].count('--cost-class') == 2
