"""Local setup → real Git ignore policy → filesystem aliases; no real data fixtures."""
import json
from pathlib import Path
import subprocess

import pytest

from rpipe.structure.system.local_paths import configure, main

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_system,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def layout(tmp_path):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
    (repo / '.gitignore').write_text('/data\n/output\n', encoding='utf-8')
    return repo, tmp_path / 'raw', tmp_path / 'results' / 'project'


def test_preview_does_not_create_roots_or_aliases(layout):
    repo, data, output = layout
    report = configure(*layout)
    assert report['mode'] == 'preview'
    assert [row['action'] for row in report['entries']] == ['create', 'create']
    assert not any(path.exists() for path in (data, output, repo / 'data', repo / 'output'))


def test_cli_apply_and_repeat_preserve_external_files(layout, capsys):
    repo, data, output = layout
    data.mkdir()
    raw = data / 'samples.txt'
    raw.write_bytes(b'fixed input')
    args = ['--repo', str(repo), '--data-root', str(data), '--output-root', str(output), '--apply']
    # A failed permission probe skips the OS-dependent check without hiding product failures.
    probe = repo / 'probe'
    try:
        probe.symlink_to(data, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'directory symlink permission unavailable: {error}')
    probe.unlink()
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert [row['action'] for row in first['entries']] == ['create', 'create']
    assert (repo / 'data').is_symlink() and (repo / 'data').resolve() == data
    assert (repo / 'output').is_symlink() and (repo / 'output').resolve() == output
    (repo / 'output' / 'result.txt').write_bytes(b'owned result')
    assert main(args) == 0
    assert [row['action'] for row in json.loads(capsys.readouterr().out)['entries']] == ['reuse', 'reuse']
    assert raw.read_bytes() == b'fixed input'
    assert (output / 'result.txt').read_bytes() == b'owned result'


def test_existing_directory_blocks_both_aliases_without_writes(layout):
    repo, data, output = layout
    (repo / 'output').mkdir()
    sentinel = repo / 'output' / 'keep.txt'
    sentinel.write_bytes(b'existing work')
    with pytest.raises(FileExistsError, match='preserve existing'):
        configure(*layout, apply=True)
    assert sentinel.read_bytes() == b'existing work'
    assert not data.exists() and not (repo / 'data').exists()


def test_wrong_symlink_is_preserved(layout, tmp_path):
    repo, data, output = layout
    peer = tmp_path / 'peer'
    peer.mkdir()
    try:
        (repo / 'output').symlink_to(peer, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'directory symlink permission unavailable: {error}')
    with pytest.raises(FileExistsError, match='different or missing target'):
        configure(*layout, apply=True)
    assert (repo / 'output').resolve() == peer
    assert not (repo / 'data').exists() and not output.exists()


@pytest.mark.parametrize('overlap', ['source', 'input'])
def test_nested_roots_are_rejected_before_creating_anything(layout, overlap):
    repo, data, output = layout
    output = (repo if overlap == 'source' else data) / 'nested'
    with pytest.raises(ValueError, match='separate, non-nested'):
        configure(repo, data, output, apply=True)
    assert not output.exists() and not data.exists()


def test_unignored_alias_is_rejected_before_creating_anything(layout):
    repo, data, output = layout
    (repo / '.gitignore').write_text('/data\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Git-ignored'):
        configure(*layout, apply=True)
    assert not data.exists() and not output.exists() and not (repo / 'data').exists()
