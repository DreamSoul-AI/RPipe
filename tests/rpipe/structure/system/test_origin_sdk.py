"""Actual SDK transfer policy; repository metadata and transport are synthetic."""
import hashlib
import io
import socket
import sys
import uuid
from types import SimpleNamespace

import pytest

from rpipe.structure.system.origin import download_model
from rpipe.structure.system import transfers

pytestmark = [
    pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
    pytest.mark.structure_layer, pytest.mark.module_system,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.fixture
def dataset_cache(monkeypatch, tmp_path):
    datasets = pytest.importorskip('datasets')
    cache = tmp_path.parent / ('dsc-' + uuid.uuid4().hex[:8])
    monkeypatch.setattr(datasets.config, 'HF_DATASETS_CACHE', cache)
    return cache


@pytest.fixture
def sdk_download(monkeypatch, tmp_path):
    httpx = pytest.importorskip('httpx')
    pytest.importorskip('huggingface_hub')
    from huggingface_hub import constants
    from huggingface_hub.hf_api import HfApi, RepoFile
    from huggingface_hub.utils import _http

    payload = b'{"synthetic":true}\n'
    commit = 'a' * 40
    oid = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    calls = []
    target = ['https://hf-mirror.com/synthetic/config.json']
    failure = [None]
    extra_headers = {}
    on_request = [None]

    def transport(request):
        calls.append(str(request.url))
        if on_request[0] is not None:
            on_request[0](request)
        if failure[0] is not None:
            raise failure[0]
        headers = {'x-repo-commit': commit, 'etag': oid,
                   'content-length': str(len(payload)), **extra_headers}
        if '/resolve/' in request.url.path:
            return httpx.Response(302, headers={**headers, 'location': target[0],
                                  'x-linked-size': str(len(payload))}, request=request)
        return httpx.Response(200, headers=headers, content=payload, request=request)

    def unavailable(**kwargs):
        raise OSError('synthetic ModelScope unavailable')

    def no_network(*args, **kwargs):
        raise AssertionError('real network prohibited')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(constants, 'HF_HUB_DISABLE_XET', True)
    monkeypatch.setattr(constants, 'HF_HUB_DISABLE_IMPLICIT_TOKEN', True)
    monkeypatch.setitem(sys.modules, 'modelscope', SimpleNamespace(snapshot_download=unavailable))
    monkeypatch.setattr(HfApi, 'list_repo_tree', lambda *a, **k: [
        RepoFile(path='config.json', size=len(payload), oid=oid)])
    monkeypatch.setattr(HfApi, 'repo_info', lambda *a, **k: SimpleNamespace(
        sha=commit, siblings=[SimpleNamespace(rfilename='config.json')]))
    if hasattr(_http, '_GLOBAL_CLIENT'):
        client = httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=True)
        monkeypatch.setattr(_http, '_GLOBAL_CLIENT', client)
    else:
        import requests

        class Adapter(requests.adapters.BaseAdapter):
            def send(self, request, **kwargs):
                fake = transport(httpx.Request(request.method, request.url))
                response = requests.Response()
                response.status_code = fake.status_code
                response.headers.update(dict(fake.headers))
                response.raw = io.BytesIO(fake.content)
                response._content = fake.content
                response.url = request.url
                response.request = request
                return response

            def close(self):
                pass

        class Session(requests.Session):
            is_closed = False

            def close(self):
                self.is_closed = True
                super().close()

        client = Session()
        client.mount('https://', Adapter())
        client.mount('http://', Adapter())
        client.event_hooks = client.hooks
        monkeypatch.setattr(_http, '_get_session_from_cache', lambda **kwargs: client)
    with client:

        def run():
            return download_model('rpipe-audit/synthetic-model', tmp_path, revision=commit)

        yield SimpleNamespace(run=run, target=target, calls=calls, client=client,
                              payload=payload, httpx=httpx, failure=failure, extra_headers=extra_headers, on_request=on_request)


@pytest.mark.parametrize('target', [
    'https://huggingface.co/synthetic/config.json',
    'https://cdn-lfs.huggingface.co/synthetic/config.json',
    'http://hf-mirror.com/synthetic/config.json',
    'https://hf-mirror.com.evil.invalid/synthetic/config.json',
])
def test_model_sdk_rejects_redirect_before_unapproved_transport(sdk_download, target):
    sdk_download.target[0] = target
    with pytest.raises(RuntimeError, match='domestic|overseas'):
        sdk_download.run()
    assert sdk_download.calls
    assert all(sdk_download.httpx.URL(url).host == 'hf-mirror.com'
               and sdk_download.httpx.URL(url).scheme == 'https'
               for url in sdk_download.calls)
    assert not sdk_download.client.is_closed


def test_model_sdk_domestic_redirect_and_cached_snapshot(sdk_download):
    from pathlib import Path
    result = Path(sdk_download.run())
    assert (result / 'config.json').read_bytes() == sdk_download.payload
    sdk_download.calls.clear()
    assert Path(sdk_download.run()) == result
    assert sdk_download.calls == []
    assert not sdk_download.client.is_closed


def test_model_sdk_preserves_shared_client_for_foreign_consumer(sdk_download):
    sdk_download.run()
    response = sdk_download.client.get('https://example.org/unrelated.json')
    assert response.content == sdk_download.payload
    assert not sdk_download.client.is_closed


def test_model_sdk_cancellation_preserves_client_and_original_exception(sdk_download):
    error = KeyboardInterrupt('synthetic transport cancellation')
    hooks = {name: list(values) for name, values in sdk_download.client.event_hooks.items()}
    sdk_download.failure[0] = error
    with pytest.raises(KeyboardInterrupt) as caught:
        sdk_download.run()
    assert caught.value is error
    assert sdk_download.client.event_hooks == hooks
    assert not sdk_download.client.is_closed
    sdk_download.failure[0] = None
    assert sdk_download.client.get('https://example.org/unrelated.json').status_code == 200


def test_model_sdk_xet_metadata_uses_guarded_http_without_native_transfer(sdk_download, monkeypatch):
    from pathlib import Path
    from huggingface_hub import file_download
    native = []
    monkeypatch.setattr(file_download, 'is_xet_available', lambda: True)

    def unguarded_native(**kwargs):
        native.append(kwargs)
        raise AssertionError('native transfer has no domestic request guard')

    monkeypatch.setattr(file_download, 'xet_get', unguarded_native)
    sdk_download.extra_headers.update({
        'x-xet-hash': 'b' * 64,
        'x-xet-refresh-route': 'https://hf-mirror.com/api/xet/read-token',
    })
    result = Path(sdk_download.run())
    assert (result / 'config.json').read_bytes() == sdk_download.payload
    assert native == []
    assert file_download.is_xet_available() is True


def test_model_sdk_foreign_thread_keeps_client_and_native_configuration(sdk_download, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from huggingface_hub import file_download
    entered, release = Event(), Event()
    monkeypatch.setattr(file_download, 'is_xet_available', lambda: True)
    original_constants = file_download.constants
    hooks = {name: list(values) for name, values in sdk_download.client.event_hooks.items()}

    def pause(request):
        if request.url.host == 'hf-mirror.com' and request.method == 'HEAD':
            entered.set()
            assert release.wait(5), 'test failed to release the synthetic model transfer'

    sdk_download.on_request[0] = pause
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(sdk_download.run)
        try:
            assert entered.wait(5), 'model never entered synthetic transport'
            assert file_download.is_xet_available() is True
            response = sdk_download.client.get('https://example.org/concurrent.json')
            assert response.content == sdk_download.payload
            assert sdk_download.client.event_hooks == hooks
        finally:
            release.set()
        assert future.result(timeout=5)
    assert file_download.constants is original_constants
    assert file_download.is_xet_available() is True
    assert sdk_download.client.event_hooks == hooks
    assert not sdk_download.client.is_closed


@pytest.mark.parametrize('target,allowed', [
    ('https://modelscope.cn/api/synthetic', True),
    ('https://cdn.modelscope.cn/synthetic', True),
    ('https://modelhub.oss-cn-hangzhou.aliyuncs.com/synthetic', True),
    ('https://modelscope.ai/synthetic', False),
    ('https://modelhub.oss-us-west-1.aliyuncs.com/synthetic', False),
    ('https://modelscope.cn.evil.invalid/synthetic', False),
    ('https://user:password@modelscope.cn/synthetic', False),
    ('http://modelscope.cn/synthetic', False),
])
def test_modelscope_requests_policy_precedes_transfer(monkeypatch, tmp_path, target, allowed):
    requests = pytest.importorskip('requests')
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'synthetic'
        response.url = request.url
        response.request = request
        return response

    def modelscope(**kwargs):
        requests.get(target).raise_for_status()
        return str(tmp_path / 'modelscope-snapshot')

    def no_mirror(**kwargs):
        raise OSError('synthetic mirror unavailable')

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    monkeypatch.setitem(sys.modules, 'modelscope', SimpleNamespace(snapshot_download=modelscope))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=no_mirror))
    if allowed:
        assert download_model('synthetic/model', tmp_path) == str(tmp_path / 'modelscope-snapshot')
        assert calls == [target]
    else:
        with pytest.raises(RuntimeError, match='domestic'):
            download_model('synthetic/model', tmp_path)
        assert calls == []


@pytest.mark.parametrize('streaming', [False, True])
def test_dataset_helper_guards_eager_and_deferred_requests(monkeypatch, tmp_path, streaming):
    from rpipe.structure.system.origin import load_hf_dataset
    requests = pytest.importorskip('requests')
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'synthetic'
        response.url = request.url
        return response

    def rows():
        requests.get('https://huggingface.co/synthetic/data.json')
        yield {'value': 1}

    class Examples:
        generate_examples_fn = staticmethod(rows)

        def __iter__(self):
            return self.generate_examples_fn()

    class DeferredDataset:
        _ex_iterable = Examples()

        def __iter__(self):
            return iter(self._ex_iterable)

    def load_dataset(*args, **kwargs):
        return DeferredDataset() if streaming else list(rows())

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    monkeypatch.setitem(sys.modules, 'datasets', SimpleNamespace(load_dataset=load_dataset))
    for name in ('datasets.config', 'huggingface_hub.constants'):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(HF_ENDPOINT='https://hf-mirror.com',
                                                             ENDPOINT='https://hf-mirror.com'))
    with pytest.raises(RuntimeError, match='domestic'):
        result = load_hf_dataset('synthetic/data', streaming=streaming)
        list(result)
    assert calls == []


def test_actual_dataset_streaming_preserves_views_state_and_pickle(monkeypatch, tmp_path, dataset_cache):
    import pickle
    datasets = pytest.importorskip('datasets')
    from huggingface_hub import constants
    from rpipe.structure.system import origin
    monkeypatch.setattr(constants, 'ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(datasets.config, 'HF_ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    paths = []
    for index in range(2):
        path = tmp_path / f'part{index}.jsonl'
        path.write_text(''.join('{"value":%d}\n' % value
                                for value in range(index * 2, index * 2 + 2)), encoding='utf-8')
        paths.append(str(path))
    result = origin.load_hf_dataset('json', data_files=paths, split='train', streaming=True,
                                    cache_dir=str(dataset_cache))
    assert isinstance(result, datasets.IterableDataset)
    assert list(result) == [{'value': n} for n in range(4)]
    assert transfers._download_origin.get() is None
    assert list(result.iter(batch_size=3)) == [{'value': [0, 1, 2]}, {'value': [3]}]
    assert sorted(row['value'] for row in result.shuffle(seed=7, buffer_size=2)) == list(range(4))
    assert list(result.shard(num_shards=2, index=1)) == [{'value': 2}, {'value': 3}]
    assert list(result.map(lambda row: {'next': row['value'] + 1}))[-1] == {'value': 3, 'next': 4}
    assert int(next(iter(result.with_format('torch')))['value']) == 0
    restored = pickle.loads(pickle.dumps(result))
    iterator = iter(restored)
    assert next(iterator) == {'value': 0}
    state = restored.state_dict()
    restored.load_state_dict(state)
    assert list(restored) == [{'value': n} for n in range(1, 4)]
    assert transfers._download_origin.get() is None


def test_actual_dataset_deferred_generator_refuses_transfer(monkeypatch, tmp_path, dataset_cache):
    datasets = pytest.importorskip('datasets')
    requests = pytest.importorskip('requests')
    from huggingface_hub import constants
    from rpipe.structure.system.origin import load_hf_dataset
    monkeypatch.setattr(constants, 'ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(datasets.config, 'HF_ENDPOINT', 'https://hf-mirror.com')
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        raise AssertionError('foreign transport entered')

    def rows():
        requests.get('https://huggingface.co/synthetic.json')
        yield {'value': 1}

    result = datasets.IterableDataset.from_generator(rows)
    monkeypatch.setattr(datasets, 'load_dataset', lambda *a, **k: result)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    loaded = load_hf_dataset('synthetic/data', streaming=True)
    assert loaded is result
    with pytest.raises(RuntimeError, match='domestic'):
        list(loaded)
    assert calls == []


@pytest.mark.parametrize('target,allowed', [
    ('https://hf-mirror.com/blob', True),
    ('https://huggingface.co/blob', False),
    ('http://hf-mirror.com/blob', False),
    ('https://user:password@hf-mirror.com/blob', False),
])
def test_aiohttp_actual_redirect_loop_checks_before_next_connection(monkeypatch, target, allowed):
    import asyncio
    import inspect
    aiohttp = pytest.importorskip('aiohttp')
    from multidict import CIMultiDict, CIMultiDictProxy
    from rpipe.structure.system import origin
    calls = []

    async def connect(self, request, **kwargs):
        calls.append(str(request.url))
        return SimpleNamespace(protocol=SimpleNamespace(set_response_params=lambda **k: None),
                               close=lambda: None)

    async def send(request, connection):
        kwargs = dict(writer=None, continue100=None, timer=None, request_info=request.request_info,
                      traces=[], loop=asyncio.get_running_loop(), session=request.session)
        if 'stream_writer' in inspect.signature(aiohttp.ClientResponse).parameters:
            kwargs['stream_writer'] = SimpleNamespace(output_size=0)
        return aiohttp.ClientResponse(request.method, request.url, **kwargs)

    async def start(response, connection):
        redirected = response.url.path == '/entry'
        response.status = 302 if redirected else 200
        response.reason = 'synthetic'
        response._headers = CIMultiDictProxy(CIMultiDict({'Location': target} if redirected else {}))
        response._raw_headers = tuple((key.encode(), value.encode())
                                      for key, value in response._headers.items())
        response._body = b'synthetic'
        response._released = False
        response._closed = False
        return response

    monkeypatch.setattr(aiohttp.TCPConnector, 'connect', connect)
    monkeypatch.setattr(aiohttp.ClientRequest, 'send', send)
    monkeypatch.setattr(aiohttp.ClientResponse, 'start', start)

    async def run():
        # Windows creates its event-loop wakeup socket before this coroutine starts.
        monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
        async with aiohttp.ClientSession(trust_env=False) as client:
            with transfers._domestic_transfers('mirror'):
                if allowed:
                    async with client.get('https://hf-mirror.com/entry') as response:
                        assert await response.read() == b'synthetic'
                else:
                    with pytest.raises(RuntimeError, match='domestic'):
                        await client.get('https://hf-mirror.com/entry')
            assert not client.closed
            assert calls == (['https://hf-mirror.com/entry', target] if allowed
                             else ['https://hf-mirror.com/entry'])
            async with client.get('https://example.org/unrelated') as response:
                assert response.status == 200
        assert client.closed

    asyncio.run(run())


def _dataset_worker_policy_probe(batch):
    import os
    import requests
    import threading
    from rpipe.structure.system import origin
    entered = []

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            entered.append(request.url)
            response = requests.Response()
            response.status_code = 200
            response._content = b'synthetic'
            return response

        def close(self):
            pass

    with requests.Session() as client:
        client.mount('https://', Adapter())
        try:
            client.get('https://huggingface.co/synthetic-worker')
        except RuntimeError:
            pass
    return [dict(policy=transfers._download_origin.get(), pid=os.getpid(),
                 thread=threading.get_ident(), transport=bool(entered))
            for _ in batch]


@pytest.mark.parametrize('backend', ['spawn', 'default', 'threads'])
def test_dataset_download_workers_keep_domestic_policy(monkeypatch, backend):
    import importlib
    import multiprocessing
    import os
    import threading
    from multiprocessing.pool import ThreadPool
    datasets = pytest.importorskip('datasets')
    from datasets.download import download_manager
    from huggingface_hub import constants
    from rpipe.structure.system.origin import load_hf_dataset
    parallel = importlib.import_module('datasets.parallel.parallel')
    pool = (ThreadPool if backend == 'threads' else multiprocessing.get_context('spawn').Pool
            if backend == 'spawn' else multiprocessing.Pool)
    monkeypatch.setattr(parallel, 'Pool', pool)
    monkeypatch.setattr(constants, 'ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(datasets.config, 'HF_ENDPOINT', 'https://hf-mirror.com')

    def load_dataset(*args, **kwargs):
        return download_manager.map_nested(_dataset_worker_policy_probe, list(range(4)),
                                          num_proc=2, batched=True, batch_size=2,
                                          disable_tqdm=True)

    monkeypatch.setattr(datasets, 'load_dataset', load_dataset)
    result = load_hf_dataset('synthetic/data')
    assert len(result) == 4
    if backend != 'threads':
        assert all(row['pid'] != os.getpid() for row in result), 'probe did not reach child processes'
    else:
        assert all(row['thread'] != threading.get_ident() for row in result), 'probe ran on caller thread'
    assert all(row['policy'] == 'mirror' and not row['transport'] for row in result)


@pytest.mark.parametrize('primary,close_error', [
    (OSError('read failed'), OSError('close failed')),
    (KeyboardInterrupt('cancel'), OSError('close failed')),
    (ValueError('decode failed'), OSError('close failed')),
    (None, OSError('close failed')),
    (OSError('read failed'), ValueError('programming close error')),
    (None, None),
])
def test_dataset_generator_close_keeps_primary_failure_and_context(primary, close_error):
    from rpipe.structure.system import origin
    closed = []

    class Iterator:
        def __iter__(self):
            return self

        def __next__(self):
            assert transfers._download_origin.get() == 'mirror'
            if primary is not None:
                raise primary
            raise StopIteration

        def close(self):
            closed.append(transfers._download_origin.get())
            if close_error is not None:
                raise close_error

    iterator = transfers._DomesticDatasetGenerator(Iterator)()
    expected = close_error if primary is None or isinstance(close_error, ValueError) else primary
    if expected is None:
        assert list(iterator) == []
    else:
        with pytest.raises(type(expected)) as caught:
            next(iterator)
        assert caught.value is expected
    assert closed == ['mirror']
    assert transfers._download_origin.get() is None
    assert transfers._download_transports_installed is False


def _image_rows(url, shape):
    if shape == 'nested':
        yield {'media': {'image': url}}
    elif shape == 'sequence':
        yield {'images': [url]}
    else:
        yield {'image': url}


@pytest.fixture
def actual_image_case(monkeypatch, dataset_cache):
    datasets = pytest.importorskip('datasets')
    requests = pytest.importorskip('requests')
    from PIL import Image
    from huggingface_hub import constants, HfFileSystem
    import datasets.features.image as image_module
    from rpipe.structure.system.origin import load_hf_dataset
    # A native streaming deepcopy mutates a populated fsspec cache while copying it.
    monkeypatch.setattr(HfFileSystem, '_cache', {})
    monkeypatch.setattr(constants, 'ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(datasets.config, 'HF_ENDPOINT', 'https://hf-mirror.com')
    content = io.BytesIO()
    Image.new('RGB', (1, 1), (1, 2, 3)).save(content, format='PNG')
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = content.getvalue()
        return response

    def xopen(path, *args, **kwargs):
        return io.BytesIO(requests.get(path).content)

    monkeypatch.setattr(image_module, 'xopen', xopen)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))

    def load(streaming, host, shape='flat'):
        url = f'https://{host}/synthetic/image.png'
        feature = ({'media': {'image': datasets.Image()}} if shape == 'nested' else
                   {'images': datasets.List(datasets.Image())} if shape == 'sequence' else
                   {'image': datasets.Image()})
        features = datasets.Features(feature)
        row = next(_image_rows(url, shape))
        result = (datasets.IterableDataset.from_generator(_image_rows,
                  gen_kwargs={'url': url, 'shape': shape}, features=features) if streaming else
                  datasets.Dataset.from_dict({key: [value] for key, value in row.items()}, features=features))
        if streaming:
            import copy
            copy.deepcopy(result)  # Verify this fixture's native baseline before binding.
        before_schema = result.features.to_dict()
        before_type = type(result)
        before_fingerprint = getattr(result, '_fingerprint', None)
        before_table = result.data.table if not streaming else None
        monkeypatch.setattr(datasets, 'load_dataset', lambda *a, **k: result)
        loaded = load_hf_dataset('synthetic/images', streaming=streaming)
        assert loaded is result and type(loaded) is before_type
        assert loaded.features.to_dict() == before_schema
        assert getattr(loaded, '_fingerprint', None) == before_fingerprint
        if before_table is not None:
            assert loaded.data.table.equals(before_table, check_metadata=True)
        return loaded, url

    return SimpleNamespace(load=load, calls=calls)


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('host,allowed', [('hf-mirror.com', True), ('huggingface.co', False)])
def test_actual_dataset_media_reads_keep_domestic_policy(actual_image_case, streaming, host, allowed):
    loaded, url = actual_image_case.load(streaming, host)

    def consume():
        return next(iter(loaded)) if streaming else loaded[0]

    if allowed:
        assert consume()['image'].getpixel((0, 0)) == (1, 2, 3)
        assert actual_image_case.calls == [url]
    else:
        with pytest.raises(RuntimeError, match='domestic'):
            consume()
        assert actual_image_case.calls == []


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('host,allowed', [('hf-mirror.com', True), ('huggingface.co', False)])
@pytest.mark.parametrize('view', ['copy', 'pickle', 'shuffle', 'batch'])
def test_actual_dataset_media_views_preserve_policy(actual_image_case, streaming, host, allowed, view):
    import copy
    import pickle
    loaded, url = actual_image_case.load(streaming, host)
    if view == 'copy':
        loaded = copy.deepcopy(loaded)
    elif view == 'pickle':
        loaded = pickle.loads(pickle.dumps(loaded))
    elif view == 'shuffle':
        loaded = loaded.shuffle(seed=7)

    def consume():
        if view == 'batch':
            return next(loaded.iter(batch_size=1))['image'][0]
        return (next(iter(loaded)) if streaming else loaded[0])['image']

    if allowed:
        assert consume().getpixel((0, 0)) == (1, 2, 3)
        assert actual_image_case.calls == [url]
    else:
        with pytest.raises(RuntimeError, match='domestic'):
            consume()
        assert actual_image_case.calls == []


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('host,allowed', [('hf-mirror.com', True), ('huggingface.co', False)])
@pytest.mark.parametrize('shape', ['nested', 'sequence'])
def test_actual_nested_media_features_preserve_policy(actual_image_case, streaming, host, allowed, shape):
    import pickle
    loaded, url = actual_image_case.load(streaming, host, shape)
    loaded = pickle.loads(pickle.dumps(loaded))

    def consume():
        row = next(iter(loaded)) if streaming else loaded[0]
        return row['media']['image'] if shape == 'nested' else row['images'][0]

    if allowed:
        assert consume().getpixel((0, 0)) == (1, 2, 3)
        assert actual_image_case.calls and set(actual_image_case.calls) == {url}
    else:
        with pytest.raises(RuntimeError, match='domestic'):
            consume()
        assert actual_image_case.calls == []


def test_domestic_sdk_worker_keeps_guard_after_parent_scope_returns(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    requests = pytest.importorskip('requests')
    from rpipe.structure.system import origin
    started, release = Event(), Event()
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'synthetic'
        return response

    def worker():
        started.set()
        assert release.wait(5), 'test did not release worker'
        try:
            requests.get('https://huggingface.co/synthetic-prefetch')
        except RuntimeError:
            return True
        return False

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with transfers._domestic_transfers('mirror'):
            future = executor.submit(worker)
            assert started.wait(5), 'worker did not start'
        release.set()
        assert future.result(timeout=5), 'worker lost guard when its parent returned'
    assert calls == []
    assert transfers._download_origin.get() is None
    assert transfers._download_transports_installed is False


@pytest.mark.parametrize('kind', ['Audio', 'Video', 'Pdf'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('host,allowed', [('hf-mirror.com', True), ('huggingface.co', False)])
def test_actual_media_feature_keeps_deferred_file_guard(monkeypatch, kind, streaming, host, allowed, dataset_cache):
    import importlib
    datasets = pytest.importorskip('datasets')
    requests = pytest.importorskip('requests')
    from huggingface_hub import constants
    from rpipe.structure.system import origin
    monkeypatch.setattr(constants, 'ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(datasets.config, 'HF_ENDPOINT', 'https://hf-mirror.com')
    monkeypatch.setattr(datasets.config, 'HUB_DATASETS_URL',
                        datasets.config.HUB_DATASETS_URL.replace('https://huggingface.co', 'https://hf-mirror.com'))
    calls, opened = [], []
    url = f'https://{host}/datasets/rpipe/audit/resolve/main/media.bin'

    class Decoder:
        def __init__(self, source, **kwargs):
            assert hasattr(source, 'read') and hasattr(source, 'seek')
            self.source = source
            self.metadata = SimpleNamespace(path=None)

        def read(self):
            self.source.seek(0)
            return self.source.read(3)

    class DeferredFile(io.BytesIO):
        def read(self, size=-1):
            requests.get(url)
            return super().read(size)

    def xopen(path, *args, **kwargs):
        assert path == url
        handle = DeferredFile(b'synthetic')
        opened.append(handle)
        return handle

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'synthetic'
        return response

    monkeypatch.setattr(datasets.config, 'TORCHCODEC_AVAILABLE', True)
    monkeypatch.setattr(datasets.config, 'PDFPLUMBER_AVAILABLE', True)
    monkeypatch.setitem(sys.modules, 'datasets.features._torchcodec', SimpleNamespace(AudioDecoder=Decoder))
    monkeypatch.setitem(sys.modules, 'torchcodec', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'torchcodec.decoders', SimpleNamespace(AudioDecoder=Decoder, VideoDecoder=Decoder))
    monkeypatch.setitem(sys.modules, 'torchcodec.encoders', SimpleNamespace(
        AudioEncoder=lambda *a, **k: pytest.fail('URL fixture must not encode media')))
    monkeypatch.setitem(sys.modules, 'pdfplumber', SimpleNamespace(open=Decoder, pdf=SimpleNamespace(PDF=Decoder)))
    module = importlib.import_module('datasets.features.' + kind.lower())
    monkeypatch.setattr(module, 'xopen', xopen)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    features = datasets.Features({'media': getattr(datasets, kind)()})

    def rows():
        yield {'media': url}

    result = (datasets.IterableDataset.from_generator(rows, features=features) if streaming else
              datasets.Dataset.from_dict({'media': [url]}, features=features))
    before = result.features.to_dict()
    monkeypatch.setattr(datasets, 'load_dataset', lambda *a, **k: result)
    loaded = origin.load_hf_dataset('synthetic/media', streaming=streaming)
    assert loaded is result and loaded.features.to_dict() == before

    def consume():
        return (next(iter(loaded)) if streaming else loaded[0])['media']

    if not allowed:
        with pytest.raises(RuntimeError, match='domestic'):
            consume()
        assert calls == [] and opened == []
    else:
        from concurrent.futures import ThreadPoolExecutor
        decoder = consume()
        assert calls == [] and len(opened) == 1
        assert transfers._download_origin.get() is None
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(decoder.read).result(timeout=5) == b'syn'
        assert calls == [url]
        assert not opened[0].closed
        if kind != 'Pdf':
            assert decoder.metadata.path == url
            assert decoder._hf_encoded == {'path': url, 'bytes': None}
        decoder.source.close()
        assert opened[0].closed
    assert transfers._download_origin.get() is None
    assert transfers._download_transports_installed is False


@pytest.mark.parametrize('protocol', range(2, 6))
def test_domestic_reader_preserves_binary_file_protocol_and_pickle(protocol):
    import pickle
    from rpipe.structure.system import origin
    reader = transfers._DomesticReader(io.BytesIO(b'first\nsecond\n'))
    assert reader.__getstate__() == {'stream': reader.stream}
    reader = pickle.loads(pickle.dumps(reader, protocol=protocol))
    assert reader.readable() and reader.seekable()
    assert reader.tell() == 0
    assert next(iter(reader)) == b'first\n'
    reader.seek(0)
    target = bytearray(5)
    assert reader.readinto(target) == 5
    assert target == b'first'
    with reader as same:
        assert same is reader
        assert reader.read() == b'\nsecond\n'
    assert reader.closed
    assert transfers._download_origin.get() is None
    assert transfers._download_transports_installed is False


def test_modelscope_supported_endpoint_is_explicitly_domestic(monkeypatch, tmp_path):
    requests = pytest.importorskip('requests')
    calls = []

    def transport(self, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'synthetic'
        return response

    def modelscope(model_id, revision, cache_dir, endpoint='https://modelscope.ai'):
        requests.get(endpoint + '/api/synthetic')
        return str(tmp_path / 'modelscope-snapshot')

    def forbidden_mirror(**kwargs):
        raise AssertionError('domestic ModelScope must be tried before mirror')

    monkeypatch.setitem(sys.modules, 'modelscope', SimpleNamespace(snapshot_download=modelscope))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=forbidden_mirror))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', transport)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    assert download_model('synthetic/model', tmp_path, revision='fixed') == str(tmp_path / 'modelscope-snapshot')
    assert calls == ['https://modelscope.cn/api/synthetic']


def test_model_sdk_custom_client_cannot_bypass_domestic_transport_guard(sdk_download, monkeypatch):
    client = sdk_download.client
    method = '_send_single_request' if isinstance(client, sdk_download.httpx.Client) else 'send'
    original = getattr(type(client), method)

    class CustomClient(type(client)):
        pass

    def send(instance, request, *args, **kwargs):
        return original(instance, request, *args, **kwargs)

    setattr(CustomClient, method, send)
    monkeypatch.setattr(client, '__class__', CustomClient)
    sdk_download.target[0] = 'https://huggingface.co/synthetic/config.json'
    with pytest.raises(RuntimeError, match='domestic'):
        sdk_download.run()
    assert all(sdk_download.httpx.URL(url).host == 'hf-mirror.com' for url in sdk_download.calls)
    assert not client.is_closed


@pytest.mark.parametrize('override', [False, True])
def test_model_sdk_custom_client_restores_class_and_keeps_domestic_success(sdk_download, monkeypatch, override):
    from pathlib import Path
    client = sdk_download.client
    method = '_send_single_request' if isinstance(client, sdk_download.httpx.Client) else 'send'
    original = getattr(type(client), method)

    class CustomClient(type(client)):
        pass

    if override:
        def send(instance, request, *args, **kwargs):
            return original(instance, request, *args, **kwargs)
        setattr(CustomClient, method, send)
    before = dict(vars(CustomClient))
    monkeypatch.setattr(client, '__class__', CustomClient)
    assert (Path(sdk_download.run()) / 'config.json').read_bytes() == sdk_download.payload
    assert dict(vars(CustomClient)) == before
    assert not client.is_closed
    assert client.get('https://example.org/unrelated.json').content == sdk_download.payload


@pytest.mark.parametrize('allowed', [False, True])
def test_actual_async_sdk_client_getter_keeps_guard_and_client(monkeypatch, allowed):
    import asyncio
    httpx = pytest.importorskip('httpx')
    pytest.importorskip('huggingface_hub')
    from huggingface_hub.utils import _http
    from rpipe.structure.system import origin
    calls = []
    method = httpx.AsyncClient._send_single_request

    class CustomClient(httpx.AsyncClient):
        async def _send_single_request(self, request):
            return await method(self, request)

    async def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b'synthetic')

    async def run():
        async with CustomClient(transport=httpx.MockTransport(transport)) as client:
            # An actual SDK getter alias is delegated, never replaced with a new client.
            monkeypatch.setattr(_http, 'get_async_session', lambda: client, raising=False)
            before = dict(vars(CustomClient))
            with transfers._domestic_transfers('mirror'):
                actual = _http.get_async_session()
                assert actual is client
                url = ('https://hf-mirror.com/blob' if allowed else 'https://huggingface.co/blob')
                if allowed:
                    assert (await actual.get(url)).content == b'synthetic'
                else:
                    with pytest.raises(RuntimeError, match='domestic'):
                        await actual.get(url)
                    assert calls == []
            assert dict(vars(CustomClient)) == before
            assert not client.is_closed
            assert (await client.get('https://example.org/unrelated')).content == b'synthetic'
            assert transfers._download_origin.get() is None
            assert not transfers._download_transports_installed
    asyncio.run(run())


def test_domestic_sdk_unsupported_client_fails_before_use(monkeypatch):
    pytest.importorskip('huggingface_hub')
    from huggingface_hub.utils import _http
    from rpipe.structure.system import origin
    client = object()
    getter = lambda: client
    monkeypatch.setattr(_http, 'get_session', getter)
    with transfers._domestic_transfers('mirror'):
        with pytest.raises(RuntimeError, match='no supported single-request transport'):
            _http.get_session()
    assert _http.get_session is getter
    assert _http.get_session() is client
    assert not transfers._download_transports_installed
