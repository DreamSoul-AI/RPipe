"""Installed-code identity checks use real metadata records, without installing dependencies."""
import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.module_cli, pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    source = Path(__file__).with_name('package_smoke.py')
    spec = importlib.util.spec_from_file_location('_installed_smoke_test', source)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    prefix = tmp_path / 'venv'
    library, scripts = prefix / 'lib', prefix / 'bin'
    package, info = library / 'rpipe', library / 'rpipe-0.4.1.dist-info'
    package.mkdir(parents=True)
    info.mkdir()
    scripts.mkdir()
    init = package / '__init__.py'
    init.write_text("__version__ = '0.4.1'\n", encoding='utf-8')
    cli = package / 'flow/cli.py'
    cli.parent.mkdir()
    cli.write_text('def main(): return 0\n', encoding='utf-8')
    console = scripts / ('rpipe.exe' if os.name == 'nt' else 'rpipe')
    console.write_bytes(b'owned console launcher')
    artifact = tmp_path / 'rpipe-0.4.1-py3-none-any.whl'
    artifact.write_bytes(b'candidate archive bytes: metadata-only unit fixture')
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: rpipe\nVersion: 0.4.1\n', encoding='utf-8')
    (info / 'entry_points.txt').write_text('[console_scripts]\nrpipe = rpipe.flow.cli:main\n', encoding='utf-8')
    origin = {'url': artifact.as_uri(), 'archive_info': {'hashes': {
        'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}}}
    def origin_record():
        (info / 'direct_url.json').write_text(json.dumps(origin), encoding='utf-8')
    origin_record()
    with (info / 'RECORD').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        for path in [init, cli, console, info / 'METADATA', info / 'entry_points.txt', info / 'direct_url.json']:
            digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip('=')
            writer.writerow([Path(os.path.relpath(path, library)).as_posix(), 'sha256=' + digest, path.stat().st_size])
        writer.writerow(['rpipe-0.4.1.dist-info/RECORD', '', ''])
    distribution = importlib.metadata.Distribution.at(info)
    loaded = []
    monkeypatch.setattr(smoke, 'sys', SimpleNamespace(prefix=str(prefix), base_prefix=str(tmp_path / 'base'),
                                                    executable=str(scripts / 'python')))
    monkeypatch.setattr(smoke, 'sysconfig', SimpleNamespace(get_path=lambda name: str(scripts if name == 'scripts' else library)))
    monkeypatch.setattr(smoke, 'metadata', SimpleNamespace(distribution=lambda name: distribution))
    monkeypatch.setattr(smoke, 'util', SimpleNamespace(find_spec=lambda name: SimpleNamespace(origin=str(init))))
    def import_owned(name):
        loaded.append(name)
        return SimpleNamespace(__file__=str(init), __version__='0.4.1')
    monkeypatch.setattr(smoke, 'import_module', import_owned)
    return SimpleNamespace(smoke=smoke, prefix=prefix, library=library, package=package,
                           init=init, cli=cli, console=console, info=info, artifact=artifact,
                           origin=origin, write_origin=origin_record, loaded=loaded)


def test_installed_identity_binds_venv_artifact_record_and_console(installed):
    case = installed
    identity = case.smoke._installed_identity(case.artifact)
    assert identity['installed_module'] == str(case.init)
    assert identity['artifact_sha256'] == hashlib.sha256(case.artifact.read_bytes()).hexdigest()
    assert identity['distribution_root'] == str(case.library)
    assert identity['console'] == str(case.console)
    assert identity['verified_python_files'] == 2 and case.loaded == ['rpipe']


@pytest.mark.parametrize('damage', ['not-venv', 'global-metadata', 'foreign-import', 'stale-archive',
                                   'other-archive', 'changed-code', 'unrecorded-code', 'foreign-console'])
def test_same_version_cannot_hide_foreign_or_stale_installed_code(installed, monkeypatch, tmp_path, damage):
    case = installed
    if damage == 'not-venv':
        case.smoke.sys.base_prefix = str(case.prefix)
    elif damage == 'global-metadata':
        monkeypatch.setattr(case.smoke, 'sysconfig', SimpleNamespace(get_path=lambda name: str(
            case.prefix / 'other-library' if name != 'scripts' else case.console.parent)))
    elif damage == 'foreign-import':
        foreign = tmp_path / 'foreign/rpipe/__init__.py'
        foreign.parent.mkdir(parents=True)
        foreign.write_text("__version__ = '0.4.1'\nraise AssertionError('must not execute foreign code')\n", encoding='utf-8')
        monkeypatch.setattr(case.smoke, 'util', SimpleNamespace(find_spec=lambda name: SimpleNamespace(origin=str(foreign))))
    elif damage == 'stale-archive':
        case.artifact.write_bytes(b'new bytes at the same version and archive filename')
    elif damage == 'other-archive':
        case.origin['url'] = (tmp_path / 'old-rpipe.whl').as_uri()
        case.write_origin()
    elif damage == 'changed-code':
        case.cli.write_text('def main(): return 99\n', encoding='utf-8')
    elif damage == 'unrecorded-code':
        (case.package / 'old_alias.py').write_text('VALUE = 1\n', encoding='utf-8')
    else:
        monkeypatch.setattr(case.smoke, 'sysconfig', SimpleNamespace(get_path=lambda name: str(
            tmp_path / 'foreign-scripts' if name == 'scripts' else case.library)))
    before = {p: p.read_bytes() for p in case.prefix.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError):
        case.smoke._installed_identity(case.artifact)
    assert all(path.read_bytes() == body for path, body in before.items())
    if damage not in {'foreign-console'}:
        assert case.loaded == []


def test_package_subcommands_do_not_inherit_source_search_paths(installed, monkeypatch):
    case = installed
    monkeypatch.setenv('PYTHONPATH', 'foreign-copy-and-source-checkout')
    monkeypatch.setenv('PYTHONHOME', 'foreign-python-home')
    monkeypatch.setenv('PATH', 'owned-torch-runtime;original-path')
    before = dict(os.environ)
    environment = case.smoke._command_environment()
    assert 'PYTHONPATH' not in environment and 'PYTHONHOME' not in environment
    assert environment['PYTHONNOUSERSITE'] == '1'
    assert environment['PYTHONDONTWRITEBYTECODE'] == '1'
    assert environment['PATH'] == before['PATH'] and dict(os.environ) == before
