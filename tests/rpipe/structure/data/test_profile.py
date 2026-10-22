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

from rpipe.structure.data.factory import _with_recorded_stats
from rpipe.structure.data.profile import _data_names, load_stats, summarize_batches


def test_summarize_batches_reports_shape_classes_and_mean():
    import torch

    images = torch.zeros(4, 1, 2, 2)
    images[:, :, :, 1] = 1
    targets = torch.tensor([0, 0, 1, 1])
    body = summarize_batches([(images, targets)])
    assert body['count'] == 4
    assert body['shape'] == [1, 2, 2]
    assert body['classes'] == [{'label': 0, 'count': 2}, {'label': 1, 'count': 2}]
    assert body['pixel']['mean'] == [0.5]
    assert body['pixel']['min'] == [0.0]
    assert body['pixel']['max'] == [1.0]


def test_recorded_stats_replace_the_builtin_table(tmp_path: Path):
    path = tmp_path / 'stats.yaml'
    path.write_text('name: MNIST\nmean: [0.1]\nstd: [0.2]\n', encoding='utf-8')
    recorded = load_stats(path)
    assert recorded is not None
    spec = {'mean': (0.5,), 'std': (0.5,), 'root': 'mnist'}
    out = _with_recorded_stats(spec, path)
    assert out['mean'] == (0.1,)
    assert out['std'] == (0.2,)
    assert spec['mean'] == (0.5,)
    assert _with_recorded_stats(spec, tmp_path / 'missing.yaml') is spec


def test_data_names_come_from_axes_and_fixed():
    names = _data_names({'axes': {'data.name': ['MNIST', 'CIFAR10']}, 'fixed': {'data': {'name': 'MNIST'}}})
    assert names == ['MNIST', 'CIFAR10']


@pytest.mark.parametrize('study, expected', [
    ({'axes': {'data.name': ['b', 'a', 'b', 'A']}, 'fixed': {'data': {'name': 'a'}}}, ['b', 'a', 'A']),
    ({'axes': {'data.name': [1, '1', 0, '', None]}, 'fixed': {'data': {'name': 1}}}, ['1', '0', '', 'None']),
    ({'axes': [], 'fixed': {'data': {'name': 'MNIST'}}}, ['MNIST']),
])
def test_data_names_keep_string_conversion_first_occurrence_and_input(study, expected):
    import copy
    before = copy.deepcopy(study)
    assert _data_names(study) == expected
    assert study == before


def test_data_names_still_rejects_missing_names():
    with pytest.raises(ValueError, match='study.yaml has no data.name'):
        _data_names({'axes': {'data.name': ()}, 'fixed': {'data': {'name': 0}}})


def test_profile_consumes_batches_incrementally_and_closes_its_data(tmp_path, monkeypatch):
    import weakref
    import torch
    from rpipe.structure.data import profile

    refs = []

    def batches():
        for index in range(5):
            assert sum(ref() is not None for ref in refs) <= 2, 'profiling retained prior batches'
            images = torch.full((2, 1, 2, 2), index / 4)
            refs.append(weakref.ref(images))
            yield images, torch.tensor([0, 1])

    class OwnedData:
        _loaders = {'train': batches()}
        closed = False

        def iter_batches(self, split):
            return iter(self._loaders.get(split, ()))

        def close(self):
            self.closed = True

    data = OwnedData()
    monkeypatch.setattr(profile.DataFactory, 'build', lambda *args, **kwargs: data)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'study.yaml').write_text('fixed: {data: {name: Tiny}}\n', encoding='utf-8')
    paths = profile.profile_study(source, shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles')
    assert data.closed
    assert len(paths) == 1
    import yaml
    body = yaml.safe_load(paths[0].read_text(encoding='utf-8'))
    assert body['splits']['train']['count'] == 10
    assert body['mean'] == [0.5]


def test_profile_keeps_primary_error_when_owned_data_close_also_fails(tmp_path, monkeypatch):
    import torch
    from rpipe.structure.data import profile

    class BrokenData:
        _loaders = {'train': [(torch.zeros(2), torch.zeros(2))]}

        def iter_batches(self, split):
            return iter(self._loaders.get(split, ()))

        def close(self):
            raise OSError('secondary close')

    monkeypatch.setattr(profile.DataFactory, 'build', lambda *args, **kwargs: BrokenData())
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'study.yaml').write_text('fixed: {data: {name: Tiny}}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='NCHW') as caught:
        profile.profile_study(source, shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles')
    assert isinstance(caught.value.cleanup_errors[0], OSError)
    assert not (tmp_path / 'profiles' / 'Tiny' / 'stats.yaml').exists()


@pytest.mark.parametrize('body', ['mean: [0.1]\nstd: [0]\n',
                                 'mean: [.nan]\nstd: [0.2]\n',
                                 'mean: [0.1, 0.2]\nstd: [0.3]\n'])
def test_explicit_normalization_stats_must_be_valid_and_never_silently_fallback(tmp_path, body):
    path = tmp_path / 'stats.yaml'
    path.write_text(body, encoding='utf-8')
    with pytest.raises(ValueError):
        _with_recorded_stats({'mean': (0.5,), 'std': (0.5,)}, path, required=True)


def test_missing_explicit_normalization_stats_is_not_a_builtin_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        _with_recorded_stats({'mean': (0.5,), 'std': (0.5,)}, tmp_path / 'missing.yaml', required=True)


def test_profile_registers_iterator_with_its_data_owner_before_failure(tmp_path, monkeypatch):
    import torch
    from rpipe.structure.data import profile
    from rpipe.structure.data.factory import Data

    class Batches:
        closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return torch.zeros(2), torch.zeros(2)

        def close(self):
            self.closed = True

    batches = Batches()
    data = Data(name='Tiny', source='test', loaders={'train': batches}, meta={})
    monkeypatch.setattr(profile.DataFactory, 'build', lambda *args, **kwargs: data)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'study.yaml').write_text('fixed: {data: {name: Tiny}}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='NCHW'):
        profile.profile_study(source, shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles')
    assert batches.closed
