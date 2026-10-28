"""Probe admission reads pinned synthetic inputs before scientific preparation."""
import contextlib
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest
import yaml

from rpipe.structure.artifact.provenance import source_digest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_data,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def probe(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[5]
    namespace = '_probe_prepare_test_' + uuid.uuid4().hex
    package = ModuleType(namespace)
    package.__path__ = [str(root / 'studies' / 'main_probe')]
    monkeypatch.setitem(sys.modules, namespace, package)
    module = importlib.import_module(namespace + '.flow.prepare.data')
    run = importlib.import_module(namespace + '.flow.prepare.run')
    raw = tmp_path / 'data' / 'MNIST' / 'raw' / 'fixture'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b'pinned synthetic input')
    table = tmp_path / 'EXPECTED_DATA.json'
    table.write_text(json.dumps({'data\\MNIST\\raw\\fixture': hashlib.sha256(raw.read_bytes()).hexdigest()}), encoding='utf-8')
    study = tmp_path / 'study'
    study.mkdir()
    pin = hashlib.sha256(table.read_bytes()).hexdigest()
    (study / 'study.yaml').write_text(yaml.safe_dump({'provenance': {'inputs': [
        {'path': '../EXPECTED_DATA.json', 'sha256': pin}]}}), encoding='utf-8')
    monkeypatch.setattr(module, 'EXPECTED_DATA', table)
    monkeypatch.setattr(module, 'source_manifest', lambda: {'source_digest': source_digest(study)})
    workspace = tmp_path / 'workspace'
    yield SimpleNamespace(module=module, run=run, raw=raw, table=table, pin=pin,
                          source=tmp_path / 'data', workspace=workspace, root=tmp_path)
    for name in tuple(sys.modules):
        if name.startswith(namespace + '.'):
            del sys.modules[name]


def _no_preparation(monkeypatch, module):
    def unexpected(*args, **kwargs):
        raise AssertionError('admission must finish before scientific preparation')
    for name in ('bootstrap', 'MainArchive', 'prepared_inputs', 'native_data', 'native_model'):
        monkeypatch.setattr(module, name, unexpected)


@pytest.mark.parametrize('fault', ['missing', 'changed', 'manifest'])
def test_bad_fixed_input_rejected_without_preparation(probe, monkeypatch, fault):
    if fault == 'missing':
        probe.raw.unlink()
    elif fault == 'changed':
        probe.raw.write_bytes(b'wrong bytes')
    else:
        probe.table.write_text('{}', encoding='utf-8')
    before = {p: p.read_bytes() for p in probe.root.rglob('*') if p.is_file()}
    _no_preparation(monkeypatch, probe.module)
    with pytest.raises((ValueError, FileNotFoundError), match='fixed raw|SHA-256'):
        probe.module.prepare(probe.workspace, probe.source, ('MNIST', 'linear'))
    assert not probe.workspace.exists()
    assert {p: p.read_bytes() for p in probe.root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('fault', ['root', 'child', 'search_path'])
def test_foreign_loaded_rpipe_rejected_before_inputs(probe, monkeypatch, fault):
    import rpipe

    foreign = str(probe.root / 'foreign' / '__init__.py')
    if fault == 'root':
        monkeypatch.setattr(rpipe, '__file__', foreign)
    elif fault == 'child':
        child = ModuleType('rpipe.foreign_test')
        child.__file__ = foreign
        monkeypatch.setitem(sys.modules, child.__name__, child)
    else:
        monkeypatch.setattr(rpipe, '__path__', [*rpipe.__path__, str(probe.root / 'foreign')])
    _no_preparation(monkeypatch, probe.module)
    monkeypatch.setattr(probe.module, 'source_manifest', lambda: pytest.fail('foreign runtime must reject first'))
    with pytest.raises(ValueError, match='requires RPipe from'):
        probe.module.prepare(probe.workspace, probe.source, ('MNIST', 'linear'))
    assert not probe.workspace.exists()


def test_matching_pins_reach_bootstrap_and_leave_unconsumed_files(probe, monkeypatch):
    partial = probe.raw.with_name('download.part')
    partial.write_bytes(b'unconsumed partial download')
    class Admitted(Exception):
        pass
    def bootstrap():
        raise Admitted
    monkeypatch.setattr(probe.module, 'bootstrap', bootstrap)
    with pytest.raises(Admitted):
        probe.module.prepare(probe.workspace, probe.source, ('MNIST', 'linear'))
    assert probe.module.verify_raw(probe.source, 'MNIST') == [{
        'dataset': 'MNIST', 'relative': 'fixture', 'sha256': hashlib.sha256(probe.raw.read_bytes()).hexdigest()}]
    assert partial.read_bytes() == b'unconsumed partial download'
    assert not probe.workspace.exists()


@pytest.fixture
def parity(probe, monkeypatch):
    import numpy as np

    closed = []
    class Tensor:
        def reshape(self, *args): return self
        def __sub__(self, other): return self
        def abs(self): return self
        def max(self): return 0
    torch = SimpleNamespace(linspace=lambda *a: Tensor(), zeros=lambda *a, **k: Tensor(),
                            int64=None, no_grad=contextlib.nullcontext, __version__='fake',
                            get_num_threads=lambda: 2, version=SimpleNamespace(cuda=None))
    monkeypatch.setitem(sys.modules, 'torch', torch)
    class Dataset:
        data = np.array([1])
        target = targets = np.array([0])
        def __init__(self, length): self.length = length
        def __len__(self): return self.length
    original = {'train': Dataset(60000), 'test': Dataset(10000)}
    class Model:
        def eval(self): pass
        def __call__(self, *args, **kwargs):
            return {'pred': Tensor()} if kwargs else Tensor()
    current = SimpleNamespace(_train_set=original['train'],
                              _loaders={'test': SimpleNamespace(dataset=original['test'])},
                              meta={'data_size': (1,)}, close=lambda: closed.append('data'))
    model = SimpleNamespace(module=Model(), close=lambda: closed.append('model'))
    stats = SimpleNamespace(mean=np.array([0.5]), std=np.array([0.2]))
    archive = SimpleNamespace(configure=lambda *a: None, aliases=contextlib.nullcontext,
        datasets=lambda *a: original, module=SimpleNamespace(load=lambda *a: stats),
        model=SimpleNamespace(make_model=lambda *a: Model()), config=SimpleNamespace(cfg={'model': {}}),
        dataset=SimpleNamespace(process_dataset=lambda *a: None), verify_exports=lambda: {'unchanged': True})
    cache = probe.root / 'cache'
    for relative in ('data/MNIST/processed/train', 'stats/MNIST', 'stats/stats.yaml'):
        path = cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic cache')
    monkeypatch.setenv('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
    for name, value in {'bootstrap': lambda: None, 'MainArchive': lambda *a: archive,
            'native_data': lambda *a: current, 'native_model': lambda *a: model,
            'prepared_inputs': lambda *a: (cache, 'cache-digest'), 'seed_runtime': lambda *a: None,
            'rng': lambda *a: {}, 'model_state': lambda *a: {},
            'state_compare': lambda *a: {'exact': True}, 'rng_equal': lambda *a: True}.items():
        monkeypatch.setattr(probe.module, name, value)
    monkeypatch.setattr(probe.module.subprocess, 'check_output', lambda *a, **k: 'test-head')
    return SimpleNamespace(current=current, model=model, closed=closed)


def test_success_records_fixed_inputs_and_actual_runtime(probe, parity):
    import rpipe

    partial = probe.raw.with_name('download.part')
    partial.write_bytes(b'leave this alone')
    result = probe.module.prepare(probe.workspace, probe.source, ('MNIST', 'linear'))
    assert result['passed'] is True
    assert result['expected_data_sha256'] == probe.pin
    assert result['rpipe_source'] == str(Path(rpipe.__file__).resolve())
    assert result['raw_files'] == probe.module.verify_raw(probe.source, 'MNIST')
    assert not list(probe.workspace.rglob('download.part'))
    assert partial.read_bytes() == b'leave this alone'
    assert parity.closed == ['model', 'data']
    assert json.loads((probe.workspace / 'PREPARED.json').read_text()) == result


def test_derived_cache_references_only_fixed_inputs(probe, parity, monkeypatch):
    import numpy as np

    inputs = importlib.import_module(probe.module.__package__ + '.inputs')
    partial = probe.raw.with_name('download.part')
    partial.write_bytes(b'unconsumed download')
    staging = probe.root / 'derived'
    stats = SimpleNamespace(mean=np.array([0.5]), std=np.array([0.2]))
    def save(value, path, kind):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'fake stats')
    archive = SimpleNamespace(aliases=contextlib.nullcontext,
        dataset=SimpleNamespace(make_dataset=lambda *a, **k: {},
                                make_data_loader=lambda *a, **k: {'train': []}),
        module=SimpleNamespace(Stats=lambda **k: stats, save=save))
    def prepare_resource(root, definition, files, build):
        assert definition['raw'] == probe.module.verify_raw(probe.source, 'MNIST')
        build(staging)
        return staging, 'fake cache digest'
    monkeypatch.setattr(inputs, 'cache_lock', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(inputs, 'prepared_resource', prepare_resource)
    rows = probe.module.verify_raw(probe.source, 'MNIST')
    result = inputs.prepared_inputs(archive, probe.source, 'MNIST', rows)
    assert result == (staging, 'fake cache digest')
    raw = staging / 'data' / 'MNIST' / 'raw'
    assert sorted(path.name for path in raw.iterdir()) == ['fixture']
    assert (raw / 'fixture').read_bytes() == probe.raw.read_bytes()
    assert partial.read_bytes() == b'unconsumed download'


@pytest.mark.parametrize('phase', ['data', 'model'])
def test_parity_failure_closes_owned_data_and_preserves_cleanup(probe, parity, monkeypatch, phase):
    primary, cleanup = KeyboardInterrupt('parity cancelled'), OSError('close failed')
    def close():
        parity.closed.append('data')
        raise cleanup
    parity.current.close = close
    def fail(*args, **kwargs):
        raise primary
    if phase == 'data':
        parity.current._loaders = {}
        # Fail after Data acquisition, before any model construction.
        monkeypatch.setattr(sys.modules['numpy'], 'array_equal', fail)
    else:
        monkeypatch.setattr(probe.module, 'state_compare', fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        probe.module.prepare(probe.workspace, probe.source, ('MNIST', 'linear'))
    assert caught.value is primary and primary.cleanup_errors == (cleanup,)
    assert parity.closed == (['data'] if phase == 'data' else ['model', 'data'])
    assert not (probe.workspace / 'PREPARED.json').exists()


def test_before_exposes_cleanup_failure_to_coordinator(probe, monkeypatch):
    primary, cleanup = KeyboardInterrupt('cancelled'), OSError('cleanup')
    primary.cleanup_errors = (cleanup,)
    def fail(*args, **kwargs): raise primary
    monkeypatch.setattr(probe.run, 'validate', lambda *args: ('MNIST', 'linear'))
    monkeypatch.setattr(probe.run, 'prepare', fail)
    ctx = SimpleNamespace(config={}, control=SimpleNamespace(seed=0), state={},
        layout=SimpleNamespace(assets_dir=probe.workspace, shared_data_dir=probe.source))
    with pytest.raises(KeyboardInterrupt) as caught:
        probe.run.before(ctx)
    assert caught.value is primary
    assert ctx.state['payload_cleanup_errors'] == [cleanup]
