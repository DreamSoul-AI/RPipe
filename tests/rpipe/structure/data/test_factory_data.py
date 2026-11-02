import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_data,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from pathlib import Path

from rpipe.structure.data.factory import build_data as data_api
from rpipe.structure.control import DataConfig


def test_stub_source_does_not_download(tmp_path: Path):
    data = data_api(
        DataConfig.from_mapping({'name': 'MNIST', 'source': 'stub'}),
        tmp_path,
    )
    assert data.source == 'stub'
    assert data.meta.get('stub') is True
    assert not (tmp_path / 'mnist').exists()


@pytest.mark.parametrize('linked', ['dataset', 'native-raw', 'canonical-raw'])
def test_owned_vision_cache_cannot_publish_through_links_into_source(tmp_path, monkeypatch, file_bytes, linked):
    from rpipe.structure.control import AdmissionError
    import torchvision.datasets

    source, cache = tmp_path / 'source', tmp_path / 'cache'
    source.mkdir()
    cache.mkdir()
    (source / 'sentinel').write_bytes(b'original source')
    native, canonical = cache / 'mnist' / 'MNIST' / 'raw', cache / 'MNIST' / 'raw'
    if linked == 'dataset':
        (cache / 'mnist').symlink_to(source, target_is_directory=True)
    elif linked == 'native-raw':
        native.parent.mkdir(parents=True)
        native.symlink_to(source, target_is_directory=True)
    else:
        canonical.parent.mkdir(parents=True)
        canonical.symlink_to(source, target_is_directory=True)
        native.mkdir(parents=True)
        (native / 'new-image').write_bytes(b'owned raw input')
    if linked == 'native-raw':
        canonical.mkdir(parents=True, exist_ok=True)
        (canonical / 'new-image').write_bytes(b'owned raw input')
    before = file_bytes(source)
    calls = []

    class Dataset:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        def __len__(self):
            return 2

    monkeypatch.setattr(torchvision.datasets, 'MNIST', Dataset)
    try:
        with pytest.raises(AdmissionError):
            data_api(DataConfig.from_mapping({'name': 'MNIST', 'source': 'torch'}), cache)
    finally:
        assert file_bytes(source) == before
    assert not calls


@pytest.mark.parametrize('mapping', [
    {'name': 'Typo', 'source': 'torch'},
    {'name': 'Typo', 'source': 'stub'},
    {'name': 'Typo'},
    {'name': 'Toy'},
    {'name': 'MNIST', 'source': 'not-installed-source'},
    {'name': 'MNIST', 'source': ''},
    {},
])
def test_unregistered_data_is_rejected(tmp_path, mapping):
    with pytest.raises(ValueError, match='unknown data name/source'):
        data_api(DataConfig.from_mapping(mapping), tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('record_stats', [False, True])
def test_cifar10_builder_uses_fake_dataset(tmp_path: Path, monkeypatch, record_stats):
    from PIL import Image
    downloads = []

    from rpipe.structure.data.factory import build_data as data_api
    from rpipe.structure.control import DataConfig

    class _Fake:
        def __init__(self, root, train=True, download=False, transform=None):
            del root
            downloads.append(download)
            self.train = train
            self.transform = transform
            self._n = 8 if train else 4

        def __len__(self):
            return self._n

        def __getitem__(self, index):
            img = Image.new('RGB', (32, 32), color=(index % 256, 0, 0))
            if self.transform is not None:
                img = self.transform(img)
            return img, 0

    import torchvision.datasets as tv_datasets

    monkeypatch.setattr(tv_datasets, 'CIFAR10', _Fake)
    profile = tmp_path / 'stats.yaml'
    if record_stats:
        profile.write_text('mean: [0.1, 0.2, 0.3]\nstd: [0.2, 0.3, 0.4]\n', encoding='utf-8')
    data = data_api(
        DataConfig.from_mapping(
            {
                'name': 'CIFAR10',
                'source': 'torch',
                'config': {'batch_size': 4, 'train_size': 8, 'augment': False},
            }
        ),
        tmp_path,
        seed=0,
        profile_path=profile,
    )
    assert downloads == [False, False]
    assert data.meta['data_size'] == [3, 32, 32]
    assert data.meta['train_size'] == 8
    assert data.meta['stats_source'] == ('study_profile' if record_stats else 'builtin_fallback')
    assert data.meta['stats_reference'] == (str(profile.resolve()) if record_stats else None)
    images, targets = next(iter(data.iter_batches('train')))
    assert tuple(images.shape) == (4, 3, 32, 32)
    assert tuple(targets.shape) == (4,)
    data.close()


def test_svhn_builder_uses_fake_dataset(tmp_path: Path, monkeypatch):
    from PIL import Image

    from rpipe.structure.data.factory import build_data as data_api
    from rpipe.structure.control import DataConfig

    class _Fake:
        def __init__(self, root, split='train', download=False, transform=None):
            del root, download
            self.transform = transform
            self._n = 6 if split == 'train' else 3

        def __len__(self):
            return self._n

        def __getitem__(self, index):
            img = Image.new('RGB', (32, 32))
            if self.transform is not None:
                img = self.transform(img)
            return img, 1

    import torchvision.datasets as tv_datasets

    monkeypatch.setattr(tv_datasets, 'SVHN', _Fake)
    data = data_api(
        DataConfig.from_mapping({'name': 'SVHN', 'source': 'torch', 'config': {'batch_size': 3, 'augment': False}}),
        tmp_path,
        seed=0,
    )
    assert data.name == 'SVHN'
    images, _ = next(iter(data.iter_batches('test')))
    assert tuple(images.shape[1:]) == (3, 32, 32)


def test_fashionmnist_and_cifar100_builders(tmp_path, monkeypatch):
    from PIL import Image

    from rpipe.structure.data.factory import build_data as data_api
    from rpipe.structure.control import DataConfig

    class _Gray:
        def __init__(self, root, train=True, download=False, transform=None):
            del root, download
            self.transform = transform
            self._n = 4 if train else 2

        def __len__(self):
            return self._n

        def __getitem__(self, index):
            img = Image.new('L', (28, 28), color=index % 256)
            if self.transform is not None:
                img = self.transform(img)
            return img, 0

    class _Color:
        def __init__(self, root, train=True, download=False, transform=None):
            del root, download
            self.transform = transform
            self._n = 4 if train else 2

        def __len__(self):
            return self._n

        def __getitem__(self, index):
            img = Image.new('RGB', (32, 32))
            if self.transform is not None:
                img = self.transform(img)
            return img, 3

    import torchvision.datasets as tv_datasets

    monkeypatch.setattr(tv_datasets, 'FashionMNIST', _Gray)
    monkeypatch.setattr(tv_datasets, 'CIFAR100', _Color)
    fashion = data_api(
        DataConfig.from_mapping({'name': 'FashionMNIST', 'source': 'torch', 'config': {'batch_size': 2}}),
        tmp_path,
        seed=0,
    )
    cifar = data_api(
        DataConfig.from_mapping(
            {'name': 'CIFAR100', 'source': 'torch', 'config': {'batch_size': 2, 'augment': False}}
        ),
        tmp_path,
        seed=0,
    )
    assert fashion.meta['data_size'] == [1, 28, 28]
    assert cifar.meta['target_size'] == 100
    images, _ = next(iter(cifar.iter_batches('train')))
    assert tuple(images.shape) == (2, 3, 32, 32)


def test_dataloader_honors_pin_memory_and_workers(tmp_path, monkeypatch):
    from PIL import Image

    from rpipe.structure.data.factory import build_data as data_api
    from rpipe.structure.control import DataConfig

    class _Fake:
        def __init__(self, root, train=True, download=False, transform=None):
            del root, download
            self.transform = transform
            self._n = 8 if train else 4

        def __len__(self):
            return self._n

        def __getitem__(self, index):
            img = Image.new('RGB', (32, 32))
            if self.transform is not None:
                img = self.transform(img)
            return img, 0

    import torchvision.datasets as tv_datasets

    monkeypatch.setattr(tv_datasets, 'CIFAR10', _Fake)
    data = data_api(
        DataConfig.from_mapping(
            {
                'name': 'CIFAR10',
                'source': 'torch',
                'config': {
                    'batch_size': 4,
                    'train_size': 8,
                    'augment': False,
                    'pin_memory': True,
                    'num_workers': 0,
                },
            }
        ),
        tmp_path,
        seed=0,
    )
    train = data._loaders['train']
    test = data._loaders['test']
    assert train.pin_memory is True
    assert test.pin_memory is True
    assert train.num_workers == 0
    assert data.meta['pin_memory'] is True


@pytest.mark.parametrize('name,shape,classes,mean,std,augmentation', [
    ('MNIST', (1, 28, 28), 10, (0.1307,), (0.3081,), None),
    ('FashionMNIST', (1, 28, 28), 10, (0.2860,), (0.3530,), None),
    ('CIFAR10', (3, 32, 32), 10, (0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010), 'cifar'),
    ('CIFAR100', (3, 32, 32), 100, (0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761), 'cifar'),
    ('SVHN', (3, 32, 32), 10, (0.4377, 0.4438, 0.4728), (0.1980, 0.2010, 0.1970), 'svhn'),
])
def test_vision_specs_keep_constructor_statistics_and_field_order(name, shape, classes, mean, std, augmentation):
    from rpipe.structure.data.factory import _VISION_TORCH, vision_root

    expected = {
        'root': name.lower(), 'ctor': name,
        'train_kw': {'split': 'train'} if name == 'SVHN' else {'train': True},
        'test_kw': {'split': 'test'} if name == 'SVHN' else {'train': False},
        'data_size': shape, 'target_size': classes, 'mean': mean, 'std': std,
        'train_aug': augmentation,
    }
    assert _VISION_TORCH[name] == expected
    assert list(_VISION_TORCH[name]) == list(expected)
    assert vision_root(name) == expected['root']


def test_vision_specs_keep_order_and_independent_split_options(monkeypatch):
    from rpipe.structure.data.factory import _VISION_TORCH, DataRegistry

    names = ['MNIST', 'FashionMNIST', 'CIFAR10', 'CIFAR100', 'SVHN']
    assert list(_VISION_TORCH) == names
    options = [spec[key] for spec in _VISION_TORCH.values() for key in ('train_kw', 'test_kw')]
    assert len({id(option) for option in options}) == 10
    monkeypatch.setitem(_VISION_TORCH['MNIST']['train_kw'], 'probe', True)
    assert all('probe' not in option for option in options[1:])
    assert all(DataRegistry.get(name, 'torch') is not None for name in names)
