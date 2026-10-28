"""Explicit input pins verify bytes; environment observations stay separate from identity."""
from rpipe.structure.artifact import provenance as source_provenance
import hashlib
import json
import sys

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _declare(source, inputs, *, data=None, model=None, stats=None):
    declaration = yaml.safe_load((source / 'study.yaml').read_text(encoding='utf-8'))
    declaration['provenance'] = {'inputs': inputs}
    (source / 'study.yaml').write_text(yaml.safe_dump(declaration), encoding='utf-8')
    config_path = source / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    if data is not None:
        config['data']['path'] = str(data)
    if model is not None:
        config['model']['path'] = str(model)
    if stats is not None:
        config['data']['config'] = {'stats_path': str(stats)}
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_declared_pin_drift_is_rejected_without_resigning_saved_success(
        source_study, run_root, tmp_path, science_factory, file_bytes, kind, science_worker):
    from rpipe.structure.make import plan
    from rpipe.api import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.provenance import validate_source
    from rpipe.structure.control import SourceMismatch

    root = tmp_path / 'input'
    root.mkdir()
    item = root / 'data.bin'
    item.write_bytes(b'original')
    pin = {'path': str(item), 'sha256': _sha(item)} if kind == 'file' else {
        'path': str(root), 'files': {'data.bin': _sha(item)}}
    _declare(source_study, [pin], data=item if kind == 'file' else root)
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    stored = file_bytes(run_root)
    item.write_bytes(b'modified')
    with pytest.raises(SourceMismatch):
        validate_source(spec)
    with pytest.raises(SourceMismatch):
        read_run(spec)
    with pytest.raises(SourceMismatch):
        plan(source_study, run_root=run_root)
    assert file_bytes(run_root) == stored


def test_directory_pin_rejects_undeclared_files(source_study, run_root, tmp_path):
    from rpipe.structure.make import plan
    from rpipe.structure.control import SourceMismatch

    root = tmp_path / 'input'
    root.mkdir()
    item = root / 'one.bin'
    item.write_bytes(b'one')
    _declare(source_study, [{'path': str(root), 'files': {'one.bin': _sha(item)}}])
    (root / 'extra.bin').write_bytes(b'two')
    with pytest.raises(SourceMismatch):
        plan(source_study, run_root=run_root)


@pytest.mark.parametrize('name', ['../outside.bin', '/outside.bin', 'C:/outside.bin'])
def test_directory_pin_rejects_escaping_member_names(source_study, run_root, tmp_path, name):
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    root = tmp_path / 'input'
    root.mkdir()
    _declare(source_study, [{'path': str(root), 'files': {name: '0' * 64}}])
    with pytest.raises(AdmissionError):
        plan(source_study, run_root=run_root)


def test_directory_pin_rejects_external_symlink_even_when_hash_matches(source_study, run_root, tmp_path):
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    root = tmp_path / 'input'
    root.mkdir()
    outside = tmp_path / 'outside.bin'
    outside.write_bytes(b'external')
    try:
        (root / 'link.bin').symlink_to(outside)
    except OSError:
        pytest.skip('file symlink creation is unavailable')
    _declare(source_study, [{'path': str(root), 'files': {'link.bin': _sha(outside)}}])
    with pytest.raises(AdmissionError):
        plan(source_study, run_root=run_root)


def test_input_provenance_distinguishes_verified_unverified_and_missing_without_hashing_unpinned(
        source_study, run_root, tmp_path, monkeypatch):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact import io as artifacts

    root = tmp_path / 'input'
    root.mkdir()
    stats = root / 'stats.yaml'
    stats.write_bytes(b'mean: [0.5]\n')
    _declare(source_study, [{'path': str(stats), 'sha256': _sha(stats)}],
             data=root, model=tmp_path / 'missing-model.bin', stats=stats)
    spec = plan(source_study, run_root=run_root)[0]
    digest = artifacts._digest
    def only_declared(path):
        assert path == stats, 'provenance hashed an unpinned input'
        return digest(path)
    monkeypatch.setattr(artifacts, '_digest', only_declared)
    body = source_provenance.input_provenance(spec)
    refs = body['references']
    assert refs['data.path']['status'] == 'unverified'
    assert refs['model.path']['status'] == 'missing'
    assert refs['data.config.stats_path']['status'] == 'verified'
    assert body['complete'] is False


def test_environment_snapshot_is_actual_and_does_not_import_scientific_modules(monkeypatch):
    import builtins
    from rpipe.structure.artifact.provenance import environment_snapshot

    native_import = builtins.__import__
    def reject_science(name, *args, **kwargs):
        assert name.split('.')[0] not in {'torch', 'torchvision', 'transformers', 'datasets', 'numpy'}
        return native_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject_science)
    body = environment_snapshot()
    assert body['python'] == sys.version.split()[0]
    assert body['dependencies']['PyYAML'] == yaml.__version__
    assert 'executable' not in body and 'hostname' not in body
    json.dumps(body, allow_nan=False)


def test_execution_records_input_and_environment_as_observations(
        source_study, run_root, tmp_path, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.api import read_run
    from rpipe.flow.run import run
    from rpipe.structure.artifact.io import read_json

    item = tmp_path / 'input.bin'
    item.write_bytes(b'scientific input')
    _declare(source_study, [{'path': str(item), 'sha256': _sha(item)}], data=item)
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    outcome = run(spec)
    row = read_run(spec)
    record = read_json(spec.run_dir / 'execution.json')
    assert outcome.status == 'succeeded' and outcome.quality is None
    assert record['input_provenance'] == row['input_provenance'] == outcome.result['input_provenance']
    assert record['environment'] == row['environment'] == outcome.result['environment']
    assert row['input_provenance']['references']['data.path']['status'] == 'verified'
    assert 'environment' not in row['identity'] and 'input_provenance' not in row['identity']


@pytest.mark.parametrize('reference', ['pinned-directory', 'unpinned-directory', 'stats-file'])
def test_planning_rejects_input_overlap_before_writing_any_output(
        source_study, run_root, science_factory, file_bytes, reference):
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    output = plan(source_study, run_root=run_root)[0].study_root
    output.mkdir(parents=True)
    item = output / 'input.bin'
    item.write_bytes(b'original input')
    if reference == 'stats-file':
        _declare(source_study, [], stats=item)
    else:
        pins = [{'path': str(output), 'files': {'input.bin': _sha(item)}}] if reference == 'pinned-directory' else []
        _declare(source_study, pins, data=output)
    before = file_bytes(run_root)
    with pytest.raises(AdmissionError):
        plan(source_study, run_root=run_root)
    assert file_bytes(run_root) == before


def test_pinned_shared_input_sibling_is_allowed(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.api import read_run
    from rpipe.flow.run import run

    shared = run_root / 'shared' / 'data'
    shared.mkdir(parents=True)
    item = shared / 'input.bin'
    item.write_bytes(b'fixed shared input')
    _declare(source_study, [{'path': str(shared), 'files': {'input.bin': _sha(item)}}], data=shared)
    spec = plan(source_study, run_root=run_root)[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    assert read_run(spec)['input_provenance']['references']['data.path']['status'] == 'verified'
    assert item.read_bytes() == b'fixed shared input'


@pytest.mark.parametrize('profile_present', [False, True])
def test_implicit_vision_profile_is_an_observed_unverified_input_without_hashing(
        source_study, run_root, tmp_path, monkeypatch, profile_present):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact import io as artifacts

    raw = tmp_path / 'input'
    raw.mkdir()
    item = raw / 'samples.bin'
    item.write_bytes(b'synthetic raw samples')
    _declare(source_study, [{'path': str(raw), 'files': {'samples.bin': _sha(item)}}], data=raw)
    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data'].update(name='MNIST', source='torch')
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    spec = plan(source_study, run_root=run_root)[0]
    profile = spec.study_root / 'shared' / 'data' / 'mnist' / 'stats.yaml'
    if profile_present:
        profile.parent.mkdir(parents=True)
        profile.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
    native_digest = artifacts._digest
    def only_declared(path):
        assert path != profile, 'implicit unpinned profile must not be hashed'
        return native_digest(path)
    monkeypatch.setattr(artifacts, '_digest', only_declared)
    body = source_provenance.input_provenance(spec)
    row = body['references']['data.implicit_stats_path']
    assert row['resolved_path'] == str(profile.resolve())
    assert row['status'] == ('unverified' if profile_present else 'builtin_fallback')
    assert body['complete'] is (not profile_present)


def test_explicit_vision_statistics_override_implicit_profile(source_study, run_root, tmp_path):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.provenance import input_provenance

    raw = tmp_path / 'input'
    raw.mkdir()
    item = raw / 'samples.bin'
    item.write_bytes(b'samples')
    stats = raw / 'stats.yaml'
    stats.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
    _declare(source_study, [{'path': str(raw), 'files': {'samples.bin': _sha(item), 'stats.yaml': _sha(stats)}}],
             data=raw, stats=stats)
    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data'].update(name='MNIST', source='torch')
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    spec = plan(source_study, run_root=run_root)[0]
    profile = spec.study_root / 'shared' / 'data' / 'mnist' / 'stats.yaml'
    profile.parent.mkdir(parents=True)
    profile.write_text('mean: [0.5]\nstd: [0.5]\n', encoding='utf-8')
    body = input_provenance(spec)
    assert 'data.implicit_stats_path' not in body['references']
    assert body['references']['data.config.stats_path']['status'] == 'verified'
    assert body['complete'] is True


@pytest.mark.parametrize('actual_source', ['builtin_fallback', 'study_profile'])
def test_published_provenance_uses_observed_statistics_source_after_prepare(
        source_study, run_root, tmp_path, science_factory, actual_source, science_worker):
    from rpipe.structure.make import plan
    from rpipe.api import read_run
    from rpipe.flow.run import run

    raw = tmp_path / 'input'
    raw.mkdir()
    item = raw / 'samples.bin'
    item.write_bytes(b'synthetic samples')
    _declare(source_study, [{'path': str(raw), 'files': {'samples.bin': _sha(item)}}], data=raw)
    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data'].update(name='MNIST', source='torch')
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    spec = plan(source_study, run_root=run_root)[0]
    profile = spec.study_root / 'shared' / 'data' / 'mnist' / 'stats.yaml'
    if actual_source == 'builtin_fallback':
        profile.parent.mkdir(parents=True)
        profile.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
    class ObservedStats(science_factory):
        def prepare(self, ctx):
            if actual_source == 'builtin_fallback':
                profile.unlink()
            else:
                profile.parent.mkdir(parents=True)
                profile.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
        def collect(self, ctx):
            return {'metrics': {'value': 3.0}, 'structure': {'data': {'stats_source': actual_source}}}
    science_worker(ObservedStats())
    outcome = run(spec)
    assert outcome.status == 'succeeded'
    row = read_run(spec)['input_provenance']
    assert row['references']['data.implicit_stats_path']['status'] == (
        'builtin_fallback' if actual_source == 'builtin_fallback' else 'unverified')
    assert row['complete'] is (actual_source == 'builtin_fallback')


@pytest.mark.parametrize('cache_present', [False, True])
def test_builtin_statistics_do_not_verify_unpinned_default_data_cache(
        source_study, run_root, cache_present):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.provenance import input_provenance

    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data'].update(name='MNIST', source='torch')
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    spec = plan(source_study, run_root=run_root)[0]
    cache = run_root / 'shared' / 'data' / 'mnist'
    if cache_present:
        cache.mkdir(parents=True)
    body = input_provenance(spec)
    assert body['references']['data.implicit_stats_path']['status'] == 'builtin_fallback'
    assert body['references']['data.implicit_path']['status'] == ('unverified' if cache_present else 'missing')
    assert body['complete'] is False


def test_actual_statistics_reference_survives_default_profile_removal(
        source_study, run_root, tmp_path, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.api import read_run
    from rpipe.flow.run import run

    raw = tmp_path / 'input'
    raw.mkdir()
    item = raw / 'samples.bin'
    item.write_bytes(b'samples')
    _declare(source_study, [{'path': str(raw), 'files': {'samples.bin': _sha(item)}}], data=raw)
    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data'].update(name='MNIST', source='torch')
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    spec = plan(source_study, run_root=run_root)[0]
    actual = tmp_path / 'actual-consumed-profile.yaml'
    actual.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
    class ObservedReference(science_factory):
        def collect(self, ctx):
            actual.unlink()
            return {'metrics': {'value': 3.0}, 'structure': {'data': {
                'stats_source': 'study_profile', 'stats_reference': str(actual.resolve())}}}
    science_worker(ObservedReference())
    assert run(spec).status == 'succeeded'
    observed = read_run(spec)['input_provenance']['references']['data.implicit_stats_path']
    assert observed['resolved_path'] == str(actual.resolve())
    assert observed['status'] == 'unverified'


def test_optional_output_root_protects_inputs_without_writing(
        source_study, run_root, tmp_path, file_bytes):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import validate_input_paths
    from rpipe.structure.control import AdmissionError

    raw = tmp_path / 'input'
    raw.mkdir()
    item = raw / 'samples.bin'
    item.write_bytes(b'input')
    _declare(source_study, [], data=raw)
    spec = plan(source_study, run_root=run_root)[0]
    before = file_bytes(tmp_path)
    with pytest.raises(AdmissionError):
        validate_input_paths(spec, output_root=raw / 'export')
    validate_input_paths(spec, output_root=tmp_path / 'allowed-export')
    assert file_bytes(tmp_path) == before


def test_consumed_explicit_statistics_are_unverified_after_unpinned_file_disappears(
        source_study, run_root, tmp_path):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.provenance import input_provenance

    stats = tmp_path / 'stats.yaml'
    stats.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
    _declare(source_study, [], stats=stats)
    spec = plan(source_study, run_root=run_root)[0]
    stats.unlink()
    body = input_provenance(spec, observed_stats_source='explicit_file', observed_stats_reference=str(stats))
    row = body['references']['data.config.stats_path']
    assert row['status'] == 'unverified'
    assert row['observed_reference'] == str(stats)


@pytest.mark.parametrize('destination', ['default-cache', 'canonical-cache', 'recorded-stats'])
def test_export_output_guard_protects_default_cache_and_recorded_statistics(
        source_study, run_root, tmp_path, destination):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.io import identity, validate_input_paths, write_json
    from rpipe.structure.control import AdmissionError

    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data'].update(name='MNIST', source='torch')
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    spec = plan(source_study, run_root=run_root)[0]
    if destination == 'default-cache':
        out = run_root / 'shared' / 'data' / 'mnist' / 'export'
    elif destination == 'canonical-cache':
        out = run_root / 'shared' / 'data' / 'MNIST' / 'raw' / 'export'
    else:
        out = tmp_path / 'actual-statistics'
        out.mkdir()
        stats = out / 'stats.yaml'
        stats.write_text('mean: [0.1]\nstd: [0.9]\n', encoding='utf-8')
        write_json(spec.result_path, {'identity': identity(spec), 'structure': {'data': {
            'stats_source': 'study_profile', 'stats_reference': str(stats)}}})
    with pytest.raises(AdmissionError):
        validate_input_paths(spec, output_root=out)
    # Normal Study output remains allowed; profile preparation belongs to that scope.
    validate_input_paths(spec)
