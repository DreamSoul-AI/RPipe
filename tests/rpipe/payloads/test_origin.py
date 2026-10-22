import hashlib
import os
from pathlib import Path

import pytest

from rpipe.payloads.origin import apply_model_origin, bind_endpoint, discard_bad_archive, domestic_endpoint, endpoint_for, foreign_endpoint, model_hub

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_system,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.fixture(autouse=True)
def isolate_hf_endpoint_modules(monkeypatch):
    import sys
    import types
    for name, attr in (('datasets.config', 'HF_ENDPOINT'), ('huggingface_hub.constants', 'ENDPOINT')):
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**{attr: 'https://hf-mirror.com'}))


def test_study_origin_selects_model_hub(monkeypatch):
    monkeypatch.setenv('HF_ENDPOINT', 'https://example.invalid')
    assert model_hub('foreign') == 'https://huggingface.co'
    assert model_hub('domestic') == 'https://hf-mirror.com'
    assert apply_model_origin('domestic') == 'https://hf-mirror.com'
    assert os.environ['HF_ENDPOINT'] == 'https://hf-mirror.com'


def test_two_endpoints_use_different_cifar_hosts():
    foreign = foreign_endpoint('CIFAR10')
    domestic = domestic_endpoint('CIFAR10')
    assert 'toronto.edu' in (foreign.url or '')
    assert 'bcebos.com' in (domestic.url or '')
    assert foreign.archive_md5 == domestic.archive_md5
    assert endpoint_for('MNIST', None).origin == 'foreign'
    assert endpoint_for('MNIST', 'domestic').mirrors[0].endswith('/mnist/')


def test_domestic_rejects_datasets_without_a_mirror():
    with pytest.raises(ValueError, match='domestic'):
        domestic_endpoint('SVHN')
    with pytest.raises(ValueError, match='origin'):
        endpoint_for('CIFAR10', 'vpn')


def test_bind_endpoint_restores_class_url():
    class _Set:
        url = 'https://foreign.example/cifar.tar.gz'
        mirrors = ['https://foreign.example/mnist/']

    domestic = domestic_endpoint('CIFAR10')
    with bind_endpoint(_Set, domestic):
        assert _Set.url == domestic.url
    assert _Set.url == 'https://foreign.example/cifar.tar.gz'

    mnist = domestic_endpoint('MNIST')
    with bind_endpoint(_Set, mnist):
        assert _Set.mirrors == [mnist.mirrors[0]]
    assert _Set.mirrors == ['https://foreign.example/mnist/']


def test_discard_bad_archive_removes_partial_and_keeps_matching_md5(tmp_path: Path):
    endpoint = domestic_endpoint('CIFAR10')
    partial = tmp_path / endpoint.archive
    partial.write_bytes(b'partial')
    discard_bad_archive(tmp_path, endpoint)
    assert not partial.is_file()

    good = tmp_path / endpoint.archive
    payload = b'cifar-bytes'
    good.write_bytes(payload)
    endpoint = endpoint.__class__(
        endpoint.name,
        endpoint.origin,
        url=endpoint.url,
        archive=endpoint.archive,
        archive_md5=hashlib.md5(payload).hexdigest(),
    )
    discard_bad_archive(tmp_path, endpoint)
    assert good.read_bytes() == payload


@pytest.mark.parametrize('kind', ['model', 'data'])
def test_domestic_origin_is_set_before_registered_builder(monkeypatch, tmp_path, kind):
    from rpipe.payloads.models import Model, ModelFactory, ModelRegistry
    from rpipe.structure.contracts import ModelConfig
    from rpipe.payloads.data import Data, DataFactory, DataRegistry
    from rpipe.structure.contracts import DataConfig

    monkeypatch.setenv('HF_ENDPOINT', 'https://example.invalid')
    seen = []
    def builder(config, assets, **kwargs):
        seen.append(os.environ['HF_ENDPOINT'])
        if kind == 'model':
            return Model(name='routing-test', source='test', module=None, meta={})
        return Data(name='routing-test', source='test', loaders={}, meta={})
    registry = ModelRegistry if kind == 'model' else DataRegistry
    monkeypatch.setitem(registry._items, ('routing-test', 'test'), builder)
    factory, config = (ModelFactory, ModelConfig) if kind == 'model' else (DataFactory, DataConfig)
    factory.build(config(name='routing-test', source='test'), tmp_path, origin='domestic')
    assert seen == ['https://hf-mirror.com']


def test_domestic_model_uses_modelscope_before_mirror(monkeypatch, tmp_path):
    import sys
    import types
    from rpipe.payloads import origin
    calls = []
    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return str(tmp_path / 'snapshot')
    monkeypatch.setitem(sys.modules, 'modelscope', types.SimpleNamespace(snapshot_download=snapshot_download))
    result = origin.download_model('org/model', tmp_path, revision='fixed')
    assert result == str(tmp_path / 'snapshot')
    assert calls == [dict(model_id='org/model', revision='fixed', cache_dir=str(tmp_path))]


def test_domestic_model_falls_back_only_to_explicit_mirror(monkeypatch, tmp_path):
    import sys
    import types
    from rpipe.payloads import origin
    calls = []
    def missing(**kwargs):
        raise OSError('not available in ModelScope')
    def mirror(**kwargs):
        calls.append(kwargs)
        return str(tmp_path / 'hf-snapshot')
    monkeypatch.setitem(sys.modules, 'modelscope', types.SimpleNamespace(snapshot_download=missing))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', types.SimpleNamespace(snapshot_download=mirror))
    assert origin.download_model('org/model', tmp_path, revision='fixed') == str(tmp_path / 'hf-snapshot')
    assert calls == [dict(repo_id='org/model', revision='fixed', cache_dir=str(tmp_path), endpoint='https://hf-mirror.com')]


def test_domestic_model_dual_failure_keeps_both_diagnostics(monkeypatch, tmp_path):
    import sys
    import types
    from rpipe.payloads import origin
    def missing(**kwargs):
        raise OSError('ModelScope unavailable')
    def failed(**kwargs):
        raise OSError('mirror unavailable')
    monkeypatch.setitem(sys.modules, 'modelscope', types.SimpleNamespace(snapshot_download=missing))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', types.SimpleNamespace(snapshot_download=failed))
    with pytest.raises(RuntimeError, match='ModelScope') as caught:
        origin.download_model('org/model', tmp_path)
    assert 'mirror' in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)


def test_hf_dataset_import_and_call_use_domestic_endpoint(monkeypatch, tmp_path):
    import sys
    import types
    from rpipe.payloads import origin
    calls = []
    def load_dataset(*args, **kwargs):
        calls.append((os.environ['HF_ENDPOINT'], args, kwargs))
        return 'dataset'
    monkeypatch.setitem(sys.modules, 'datasets', types.SimpleNamespace(load_dataset=load_dataset))
    assert origin.load_hf_dataset('org/data', cache_dir=str(tmp_path), split='train') == 'dataset'
    assert calls == [('https://hf-mirror.com', ('org/data',), dict(cache_dir=str(tmp_path), split='train'))]


def test_domestic_refuses_already_imported_foreign_dataset_endpoint(monkeypatch):
    import sys
    import types
    from rpipe.payloads import origin
    monkeypatch.setitem(sys.modules, 'datasets.config', types.SimpleNamespace(HF_ENDPOINT='https://huggingface.co'))
    with pytest.raises(RuntimeError, match='import'):
        origin.load_hf_dataset('org/data')


@pytest.mark.parametrize('module_name,attr', [('datasets.config', 'HF_ENDPOINT'), ('huggingface_hub.constants', 'ENDPOINT')])
def test_domestic_accepts_cached_mirror_endpoint(monkeypatch, module_name, attr):
    import sys
    import types
    monkeypatch.setitem(sys.modules, module_name, types.SimpleNamespace(**{attr: 'https://hf-mirror.com'}))
    assert apply_model_origin('domestic') == 'https://hf-mirror.com'


def test_domestic_model_local_input_never_downloads(tmp_path, monkeypatch):
    import sys
    from rpipe.payloads.origin import download_model
    local = tmp_path / 'model'
    local.mkdir()
    monkeypatch.setitem(sys.modules, 'modelscope', None)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', None)
    assert download_model(str(local), tmp_path) == str(local)


def test_domestic_model_missing_modelscope_uses_mirror(monkeypatch, tmp_path):
    import sys
    import types
    from rpipe.payloads.origin import download_model
    monkeypatch.setitem(sys.modules, 'modelscope', None)
    calls = []
    def mirror(**kwargs):
        calls.append(kwargs['endpoint'])
        return 'local-snapshot'
    monkeypatch.setitem(sys.modules, 'huggingface_hub', types.SimpleNamespace(snapshot_download=mirror))
    assert download_model('org/model', tmp_path) == 'local-snapshot'
    assert calls == ['https://hf-mirror.com']


def test_domestic_model_cancellation_does_not_try_another_source(monkeypatch, tmp_path):
    import sys
    import types
    from rpipe.payloads.origin import download_model
    def cancel(**kwargs):
        raise KeyboardInterrupt('cancel')
    monkeypatch.setitem(sys.modules, 'modelscope', types.SimpleNamespace(snapshot_download=cancel))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', None)
    with pytest.raises(KeyboardInterrupt, match='cancel'):
        download_model('org/model', tmp_path)


@pytest.mark.parametrize('args,kwargs', [
    (('https://huggingface.co/org/data/resolve/main/data.json',), {}),
    (('json',), {'data_files': {'train': ['https://example.org/data.json']}}),
    (('json', None, None, {'train': ['https://example.org/data.json']}), {}),
    (('json',), {'data_dir': 'https://example.org/data/'}),
])
def test_hf_dataset_rejects_explicit_overseas_urls(monkeypatch, args, kwargs):
    import sys
    import types
    from rpipe.payloads.origin import load_hf_dataset
    def forbidden(*args, **kwargs):
        raise AssertionError('foreign URL reached datasets')
    monkeypatch.setitem(sys.modules, 'datasets', types.SimpleNamespace(load_dataset=forbidden))
    with pytest.raises(ValueError, match='domestic'):
        load_hf_dataset(*args, **kwargs)


def test_archive_checksum_read_exit_preserves_first_failure(tmp_path, reader_exit_case, reader_close_fault):
    from rpipe.payloads.origin import DownloadEndpoint
    case = reader_exit_case
    source, peer = tmp_path / 'archive.bin', tmp_path / 'peer'
    payload = b'synthetic immutable archive'
    source.write_bytes(payload)
    peer.write_bytes(b'peer untouched')
    endpoint = DownloadEndpoint('synthetic', 'domestic', archive=source.name,
                                archive_md5=hashlib.md5(payload).hexdigest())
    with reader_close_fault(source, case, 'rb') as (handles, closes):
        if case.expected is None:
            discard_bad_archive(tmp_path, endpoint)
        else:
            with pytest.raises(type(case.expected)) as caught:
                discard_bad_archive(tmp_path, endpoint)
            assert caught.value is case.expected
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert source.read_bytes() == payload and peer.read_bytes() == b'peer untouched'


@pytest.mark.parametrize('name,archive,checksum', [
    ('CIFAR10', 'cifar-10-python.tar.gz', 'c58f30108f718f92721af3b95e74349a'),
    ('CIFAR100', 'cifar-100-python.tar.gz', 'eb9058c3a382ffc7106e4002c42a8d85'),
])
@pytest.mark.parametrize('policy,host', [
    ('foreign', 'https://www.cs.toronto.edu/~kriz'),
    ('domestic', 'https://dataset.bj.bcebos.com/cifar'),
])
def test_cifar_endpoint_retains_exact_archive_contract(name, archive, checksum, policy, host):
    from dataclasses import asdict

    endpoint = endpoint_for(name, policy)
    assert asdict(endpoint) == dict(name=name, origin=policy, url=f'{host}/{archive}',
                                   mirrors=(), archive=archive, archive_md5=checksum)
    assert endpoint.location == f'{host}/{archive}'


@pytest.mark.parametrize('size', [0, 1, 1024 * 1024, 1024 * 1024 + 7])
def test_archive_checksum_preserves_block_reads_and_input_bytes(tmp_path, monkeypatch, size):
    from rpipe.payloads.origin import DownloadEndpoint

    source = tmp_path / 'archive.bin'
    payload = (b'chunk-data' * (size // 10 + 1))[:size]
    source.write_bytes(payload)
    endpoint = DownloadEndpoint('synthetic', 'domestic', archive=source.name,
                                archive_md5=hashlib.md5(payload).hexdigest())
    open_file, calls, handles = Path.open, [], []

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, count):
            calls.append(count)
            return self.handle.read(count)

    def traced_open(path, *args, **kwargs):
        handle = open_file(path, *args, **kwargs)
        if path == source and args == ('rb',):
            handles.append(handle)
            return Reader(handle)
        return handle

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', traced_open)
        discard_bad_archive(tmp_path, endpoint)
    expected_reads = (size + 1024 * 1024 - 1) // (1024 * 1024) + 1
    assert calls == [1024 * 1024] * expected_reads
    assert len(handles) == 1 and handles[0].closed
    assert source.read_bytes() == payload
