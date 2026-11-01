"""External-root aliases and input references preserve their real targets."""
import errno
import os
from pathlib import Path
import subprocess
import tempfile
import pytest
from rpipe.structure.artifact.io import link_input
from rpipe.structure.make import plan, publish
from rpipe.structure.system.resources import output_snapshot

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('kind', ['symlink', pytest.param('junction', marks=pytest.mark.skipif(
    os.name != 'nt', reason='Windows directory junction'))])
def test_repository_alias_resolves_for_publication_but_monitoring_requires_real_root(tmp_path, kind):
    project = tmp_path / 'project'
    study = project / 'studies' / 'toy'
    study.mkdir(parents=True)
    (project / 'pyproject.toml').write_text('[project]\nname="synthetic"\n', encoding='utf-8')
    (study / 'study.yaml').write_text('study: toy\nseeds: [0]\n', encoding='utf-8')
    (study / 'experiment_config.yaml').write_text('experiment: toy\ndata: {name: Toy, source: stub}\nmodel: {name: linear}\nalgorithm: {mode: train}\nsystem: {device: cpu}\n', encoding='utf-8')
    real = tmp_path / 'external-output'
    real.mkdir()
    peer = real / 'peer.bin'
    peer.write_bytes(b'other project output')
    alias = project / 'output'
    if kind == 'junction':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(real)],
                       check=True, capture_output=True)
    else:
        alias.symlink_to(real, target_is_directory=True)
    assert alias.resolve() == real.resolve()
    specs = plan(study, run_root=alias)
    assert specs[0].run_root == real.resolve()
    publish(specs)
    assert specs[0].run_dir.is_dir()
    with pytest.raises(OSError): output_snapshot([alias])
    assert output_snapshot([real])
    if kind == 'junction': alias.rmdir()
    else: alias.unlink()
    assert peer.read_bytes() == b'other project output'
    assert specs[0].run_dir.is_dir()


def test_file_references_are_counted_once_and_removal_keeps_input(tmp_path):
    raw = tmp_path / 'raw' / 'input.bin'
    raw.parent.mkdir()
    raw.write_bytes(b'fixed scientific bytes')
    output = tmp_path / 'output'
    first = link_input(raw, output / 'first.bin')
    second = link_input(raw, output / 'second.bin')
    assert first.samefile(raw) and second.samefile(raw)
    assert sum(output_snapshot([output]).values()) == raw.stat().st_size
    assert first.is_symlink() or first.stat().st_nlink >= 2
    first.unlink()
    second.unlink()
    assert raw.read_bytes() == b'fixed scientific bytes'


def test_permission_failure_uses_real_same_volume_hard_link(tmp_path, monkeypatch):
    raw, target = tmp_path / 'raw', tmp_path / 'output' / 'input'
    raw.write_bytes(b'unchanged')
    def deny(*args, **kwargs): raise PermissionError(errno.EPERM, 'synthetic link permission')
    monkeypatch.setattr(Path, 'symlink_to', deny)
    assert link_input(raw, target).samefile(raw)
    assert not target.is_symlink() and raw.stat().st_nlink >= 2
    target.unlink()
    assert raw.read_bytes() == b'unchanged'


def test_unavailable_links_never_silently_copy_input(tmp_path, monkeypatch):
    raw, target = tmp_path / 'raw', tmp_path / 'output' / 'input'
    raw.write_bytes(b'unchanged')
    def deny(*args, **kwargs): raise PermissionError(errno.EPERM, 'synthetic link permission')
    def cross_volume(*args, **kwargs): raise OSError(errno.EXDEV, 'synthetic cross-volume hard link')
    monkeypatch.setattr(Path, 'symlink_to', deny)
    monkeypatch.setattr(os, 'link', cross_volume)
    with pytest.raises(OSError): link_input(raw, target)
    assert not target.exists() and not target.is_symlink()
    assert raw.read_bytes() == b'unchanged'


@pytest.mark.parametrize('kind', ['symlink', pytest.param('junction', marks=pytest.mark.skipif(
    os.name != 'nt', reason='Windows directory junction'))])
def test_large_aliases_are_ignored_and_scientific_configuration_is_retained(tmp_path, kind):
    repo = Path(__file__).resolve().parents[4]
    project, real = tmp_path / 'project', tmp_path / 'external'
    project.mkdir()
    real.mkdir()
    sentinel = real / 'fixed.bin'
    sentinel.write_bytes(b'preserved input')
    (project / '.gitignore').write_bytes((repo / '.gitignore').read_bytes())
    subprocess.run(['git', 'init', '--quiet', str(project)], check=True, capture_output=True)
    ignored = ['data', 'model', 'output', 'shared']
    for name in ignored:
        alias = project / name
        if kind == 'junction':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(real)],
                           check=True, capture_output=True)
        else:
            alias.symlink_to(real, target_is_directory=True)
    try:
        selected = subprocess.run(['git', '-C', str(project), 'check-ignore', '-z', '--stdin'],
                                  input=('\0'.join(ignored)+'\0').encode('utf-8'), capture_output=True)
        assert selected.returncode == 0
        assert {v.decode('utf-8') for v in selected.stdout.split(b'\0') if v} == set(ignored)
        for name in ['study.yaml', 'docs/EXPECTED_DATA.json', 'model_config.yaml']:
            kept = subprocess.run(['git', '-C', str(project), 'check-ignore', name], capture_output=True)
            assert kept.returncode == 1
    finally:
        for name in ignored:
            alias = project / name
            if kind == 'junction': alias.rmdir()
            else: alias.unlink()
    assert sentinel.read_bytes() == b'preserved input'


@pytest.mark.external
def test_real_cross_volume_hard_link_failure_preserves_input(tmp_path, monkeypatch):
    configured = os.environ.get('RPIPE_TEST_CROSS_VOLUME_ROOT')
    if not configured: pytest.skip('requires explicit task-owned second-volume temporary root')
    root = Path(configured).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='link-check-', dir=root)).resolve()
    assert workspace.is_relative_to(root)
    target = workspace / 'input'
    raw = tmp_path / 'raw'
    raw.write_bytes(b'unchanged cross-volume input')
    try:
        if raw.stat().st_dev == workspace.stat().st_dev:
            pytest.skip('configured root is on the same actual volume')
        def deny(*args, **kwargs): raise PermissionError(errno.EPERM, 'force hard-link fallback')
        monkeypatch.setattr(Path, 'symlink_to', deny)
        with pytest.raises(OSError) as caught: link_input(raw, target)
        assert caught.value.errno == errno.EXDEV or getattr(caught.value, 'winerror', None) == 17
        assert not target.exists() and not target.is_symlink()
        assert raw.read_bytes() == b'unchanged cross-volume input'
    finally:
        if target.exists() or target.is_symlink(): target.unlink()
        workspace.rmdir()
