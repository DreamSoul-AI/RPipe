"""Local test manifests identify ordinary, packed and linked Git checkouts."""
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import conftest as records

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.skipif(shutil.which('git') is None, reason='requires local Git for real metadata layouts')
@pytest.mark.parametrize('layout', ['loose', 'packed', 'detached', 'worktree'])
def test_manifest_git_sha_handles_real_git_layouts(tmp_path, monkeypatch, layout):
    repo = tmp_path / 'repo'
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    def git(*args):
        return subprocess.check_output(
            ['git', '-c', f'safe.directory={repo}', '-c', f'core.hooksPath={hooks}', '-c', 'commit.gpgsign=false',
             '-c', 'user.name=metadata-test', '-c', 'user.email=metadata@example.invalid', *args],
            cwd=repo if repo.exists() else tmp_path, text=True, stderr=subprocess.PIPE).strip()
    git('init', '--quiet', str(repo))
    git('commit', '--quiet', '--allow-empty', '-m', 'metadata fixture')
    expected = git('rev-parse', 'HEAD')
    root = repo
    if layout == 'packed':
        git('pack-refs', '--all')
    elif layout == 'detached':
        git('checkout', '--quiet', '--detach')
    elif layout == 'worktree':
        root = tmp_path / 'linked'
        git('worktree', 'add', '--quiet', '--detach', str(root))
        assert (root / '.git').is_file()
    monkeypatch.setattr(records, 'REPO', root)
    assert records._git_sha() == expected


def test_snapshot_without_git_never_attributes_parent_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'REPO', tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('a snapshot must not search its parent checkout')
    monkeypatch.setattr(records, 'subprocess', SimpleNamespace(run=forbidden), raising=False)
    assert records._git_sha() is None


@pytest.mark.parametrize('error', [FileNotFoundError('git missing'),
                                  subprocess.TimeoutExpired('git', 5),
                                  subprocess.CalledProcessError(128, 'git')])
def test_git_fallback_unavailable_is_optional_metadata(tmp_path, monkeypatch, error):
    (tmp_path / '.git').write_text('gitdir: absent')
    monkeypatch.setattr(records, 'REPO', tmp_path)
    def unavailable(*args, **kwargs):
        assert kwargs['timeout'] == 5
        raise error
    monkeypatch.setattr(records, 'subprocess', SimpleNamespace(run=unavailable, SubprocessError=subprocess.SubprocessError), raising=False)
    assert records._git_sha() is None


@pytest.mark.parametrize('location', ['HEAD', 'refs/heads/main'])
@pytest.mark.parametrize('payload', [b'\xff', b'not-a-sha', b'ref:'])
def test_corrupt_direct_metadata_is_optional(tmp_path, monkeypatch, location, payload):
    metadata = tmp_path / '.git'
    (metadata / 'refs/heads').mkdir(parents=True)
    (metadata / 'objects').mkdir()
    if location != 'HEAD':
        (metadata / 'HEAD').write_text('ref: refs/heads/main\n')
    (metadata / location).write_bytes(payload)
    monkeypatch.setattr(records, 'REPO', tmp_path)
    assert records._git_sha() is None


@pytest.mark.parametrize('location', ['HEAD', 'refs/heads/main'])
@pytest.mark.parametrize('error', [PermissionError('metadata denied'), FileNotFoundError('metadata removed')])
def test_direct_metadata_read_failure_is_optional(tmp_path, monkeypatch, location, error):
    metadata = tmp_path / '.git'
    (metadata / 'refs/heads').mkdir(parents=True)
    leaf = metadata / location
    leaf.write_text('a' * 40)
    if location != 'HEAD':
        (metadata / 'HEAD').write_text('ref: refs/heads/main\n')
    original = type(leaf).read_text
    def unavailable(path, *args, **kwargs):
        if path == leaf:
            raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(leaf), 'read_text', unavailable)
    monkeypatch.setattr(records, 'REPO', tmp_path)
    assert records._git_sha() is None


@pytest.mark.parametrize('sha', ['a' * 40, 'A' * 64])
def test_detached_sha_formats_keep_file_fast_path(tmp_path, monkeypatch, sha):
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git/HEAD').write_text(sha + '\n')
    monkeypatch.setattr(records, 'REPO', tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('valid direct commit metadata must keep the file fast path')
    monkeypatch.setattr(records, 'subprocess', SimpleNamespace(run=forbidden, SubprocessError=subprocess.SubprocessError))
    assert records._git_sha() == sha
