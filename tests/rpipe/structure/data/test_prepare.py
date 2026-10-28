import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_data,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from pathlib import Path

def _config(root, name):
    return root / 'configs' / (name + '.yaml')

from rpipe.structure.artifact.io import write_config
from rpipe.structure.data.prepare import prepare_shared_data


def test_prepare_skips_stub(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_build(cfg, root, seed=None, origin=None):
        calls.append(str(cfg.name))
        return None

    monkeypatch.setattr('rpipe.structure.data.prepare.DataFactory.build', fake_build)
    config_path = _config(tmp_path, 'r0')
    write_config(config_path, {'id': 'r0', 'data': {'name': 'Toy', 'source': 'stub'}})
    assert prepare_shared_data(tmp_path, [config_path], shared_data_dir=tmp_path / 'shared' / 'data') == []
    assert calls == []


def test_prepare_skips_existing_cache(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_build(cfg, root, seed=None, origin=None):
        calls.append(str(cfg.name))
        return None

    monkeypatch.setattr('rpipe.structure.data.prepare.DataFactory.build', fake_build)
    config_path = _config(tmp_path, 'r0')
    write_config(
        config_path,
        {'id': 'r0', 'data': {'name': 'MNIST', 'source': 'torch', 'config': {'batch_size': 64}}},
    )
    cached = (tmp_path / 'shared' / 'data') / 'mnist'
    cached.mkdir(parents=True)
    (cached / '.ready').write_text('foreign\n', encoding='utf-8')
    assert prepare_shared_data(tmp_path, [config_path], shared_data_dir=tmp_path / 'shared' / 'data') == []
    assert calls == []


def test_prepare_once_per_name_source(tmp_path: Path, monkeypatch):
    calls: list[tuple] = []

    def fake_build(cfg, root, seed=None, origin=None):
        calls.append((cfg.name, cfg.source, cfg.config.get('train_size'), Path(root).name))
        return None

    monkeypatch.setattr('rpipe.structure.data.prepare.DataFactory.build', fake_build)
    paths = []
    for i, size in enumerate((500, 2000, 500)):
        config_path = _config(tmp_path, f'r{i}')
        write_config(
            config_path,
            {
                'id': f'r{i}',
                'data': {
                    'name': 'MNIST',
                    'source': 'torch',
                    'config': {'train_size': size, 'batch_size': 64},
                },
            },
        )
        paths.append(config_path)
    names = prepare_shared_data(tmp_path, paths, shared_data_dir=tmp_path / 'shared' / 'data')
    assert names == ['MNIST']
    assert len(calls) == 1
    assert calls[0] == ('MNIST', 'torch', None, 'data')


def test_prepare_announces_download_before_build(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level('INFO')
    seen: dict[str, str] = {}

    def fake_build(cfg, root, seed=None, origin=None):
        seen['during'] = '\n'.join(record.getMessage() for record in caplog.records)
        return None

    monkeypatch.setattr('rpipe.structure.data.prepare.DataFactory.build', fake_build)
    config_path = _config(tmp_path, 'r0')
    write_config(
        config_path,
        {'id': 'r0', 'data': {'name': 'MNIST', 'source': 'torch', 'config': {'batch_size': 64}}},
    )
    assert prepare_shared_data(tmp_path, [config_path], shared_data_dir=tmp_path / 'shared' / 'data') == ['MNIST']
    out = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'shared MNIST download' in out
    assert 'foreign' in out
    assert 'shared MNIST ready' in out
    assert 'download' in seen['during']
    assert ((tmp_path / 'shared' / 'data') / 'mnist' / '.ready').read_text(encoding='utf-8').strip() == 'foreign'


def test_prepare_retries_partial_archive(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level('INFO')
    calls: list[str] = []

    def fake_build(cfg, root, seed=None, origin=None):
        calls.append(origin or '')
        return None

    monkeypatch.setattr('rpipe.structure.data.prepare.DataFactory.build', fake_build)
    config_path = _config(tmp_path, 'r0')
    write_config(
        config_path,
        {
            'id': 'r0',
            'origin': 'domestic',
            'data': {
                'name': 'CIFAR10',
                'source': 'torch',
                'config': {'batch_size': 64},
            },
        },
    )
    partial = (tmp_path / 'shared' / 'data') / 'cifar10'
    partial.mkdir(parents=True)
    (partial / 'cifar-10-python.tar.gz').write_bytes(b'not-a-full-archive')
    assert prepare_shared_data(tmp_path, [config_path], shared_data_dir=tmp_path / 'shared' / 'data') == ['CIFAR10']
    assert calls == ['domestic']
    out = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'download domestic' in out
    assert 'dataset.bj.bcebos.com' in out
    assert 'cached' not in out


@pytest.mark.parametrize('initial', [None, 'caller-value', '1'], ids=['absent', 'custom', 'already-disabled'])
@pytest.mark.parametrize('body_failure', [False, True], ids=['success', 'body-error'])
def test_concurrent_preparation_preserves_temporary_environment(tmp_path, monkeypatch, initial, body_failure):
    import os
    import threading
    from rpipe.structure.data import prepare as prepare

    if initial is None:
        monkeypatch.delenv('TQDM_DISABLE', raising=False)
    else:
        monkeypatch.setenv('TQDM_DISABLE', initial)
    first, attempt, entered, exited = [threading.Event() for _ in range(4)]
    observed, errors = {}, []
    primary = RuntimeError('preparation refused')

    def fake(study, shared, config_paths, seen, names):
        index = int(study.name)
        if index == 0:
            first.set()
            assert attempt.wait(3)
            entered.wait(0.2)
        else:
            entered.set()
            assert exited.wait(3)
        observed[index] = os.environ.get('TQDM_DISABLE')
        if body_failure:
            raise primary
        return []

    monkeypatch.setattr(prepare, '_prepare', fake)

    def worker(index):
        try:
            if index:
                assert first.wait(3)
                attempt.set()
            study = tmp_path / f'project{index}' / 'studies' / str(index)
            try:
                assert prepare.prepare_shared_data(study, [], shared_data_dir=tmp_path / 'shared' / 'data') == []
            except RuntimeError as exc:
                assert body_failure and exc is primary
        except BaseException as exc:
            errors.append(exc)
        finally:
            if index == 0:
                exited.set()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert observed == {0: '1', 1: '1'}
    assert os.environ.get('TQDM_DISABLE') == initial


@pytest.mark.parametrize('initial', [None, 'caller-value', '1'], ids=['absent', 'custom', 'already-disabled'])
def test_nested_preparation_restores_environment_after_inner_error(tmp_path, monkeypatch, initial):
    import os
    from rpipe.structure.data import prepare as prepare

    if initial is None:
        monkeypatch.delenv('TQDM_DISABLE', raising=False)
    else:
        monkeypatch.setenv('TQDM_DISABLE', initial)
    primary = RuntimeError('inner preparation refused')

    def fake(study, shared, config_paths, seen, names):
        assert os.environ['TQDM_DISABLE'] == '1'
        if study.name == 'inner':
            raise primary
        with pytest.raises(RuntimeError) as caught:
            prepare.prepare_shared_data(tmp_path / 'other-project' / 'studies' / 'inner', [], shared_data_dir=tmp_path / 'shared' / 'data')
        assert caught.value is primary
        assert os.environ['TQDM_DISABLE'] == '1'
        return []

    monkeypatch.setattr(prepare, '_prepare', fake)
    assert prepare.prepare_shared_data(tmp_path / 'project' / 'studies' / 'outer', [], shared_data_dir=tmp_path / 'shared' / 'data') == []
    assert os.environ.get('TQDM_DISABLE') == initial
