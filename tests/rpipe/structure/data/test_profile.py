import os
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
from rpipe.structure.data.profile import load_stats, summarize_batches


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


def test_profile_retains_small_nonzero_variance_across_batches():
    import torch

    batches = [(torch.full((4, 1, 4, 4), value), torch.zeros(4, dtype=torch.long))
               for value in (0.999, 1.0)]
    body = summarize_batches(batches)
    assert body['pixel']['mean'] == pytest.approx([0.9995], abs=1e-6)
    assert body['pixel']['std'] == pytest.approx([0.0005], abs=1e-6)


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
    paths = profile.profile_study(source, configs=[{'data': {'name': 'Tiny'}}],
                                  shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles')
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
        profile.profile_study(source, configs=[{'data': {'name': 'Tiny'}}],
                              shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles')
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
        profile.profile_study(source, configs=[{'data': {'name': 'Tiny'}}],
                              shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles')
    assert batches.closed


def test_profile_uses_resolved_declared_input_and_source_once_for_repeated_runs(tmp_path, monkeypatch):
    import copy
    import torch
    from rpipe.structure.data import profile
    from rpipe.structure.data.factory import Data

    source = tmp_path / 'source'
    source.mkdir()
    calls = []

    def build(config, shared, **kwargs):
        calls.append((config.to_mapping(), kwargs['origin']))
        return Data(name=config.name, source=config.source, meta={}, loaders={
            'train': [(torch.tensor([[[[0.2, 0.4]]]]), torch.tensor([0]))],
        })

    monkeypatch.setattr(profile.DataFactory, 'build', build)
    first = {'origin': 'domestic', 'seed': 0, 'data': {'name': 'MNIST', 'source': 'custom',
             'path': '../declared', 'config': {'train_size': 1, 'augment': True, 'num_workers': 2}}}
    second = copy.deepcopy(first)
    second['seed'] = 1
    second['data']['config']['train_size'] = 7
    before = copy.deepcopy([first, second])
    written = profile.profile_study(source, configs=[first, second], shared_data_dir=tmp_path / 'raw',
                                    profiles_dir=tmp_path / 'profiles')
    assert [first, second] == before
    assert len(calls) == len(written) == 1
    config, origin = calls[0]
    assert config['path'] == str((tmp_path / 'declared').resolve())
    assert config['source'] == 'custom' and origin == 'domestic'
    assert 'train_size' not in config['config']
    assert config['config']['augment'] is False and config['config']['num_workers'] == 0
    assert config['config']['download'] is False


def test_profile_rejects_conflicting_inputs_before_any_build_or_write(tmp_path, monkeypatch):
    from rpipe.structure.data import profile

    calls = []
    monkeypatch.setattr(profile.DataFactory, 'build', lambda *args, **kwargs: calls.append(True))
    configs = [{'data': {'name': 'MNIST', 'path': path}} for path in ('../left', '../right')]
    with pytest.raises(ValueError, match='conflicting profile inputs'):
        profile.profile_study(tmp_path / 'source', configs=configs, shared_data_dir=tmp_path / 'raw',
                              profiles_dir=tmp_path / 'profiles')
    assert not calls and not (tmp_path / 'profiles').exists()


def test_profile_download_flag_cannot_mutate_a_declared_input(tmp_path, monkeypatch):
    from rpipe.structure.data import profile

    calls = []
    monkeypatch.setattr(profile.DataFactory, 'build', lambda *args, **kwargs: calls.append(True))
    with pytest.raises(ValueError, match='read-only'):
        profile.profile_study(tmp_path / 'source', configs=[{'data': {'name': 'MNIST', 'path': '../input'}}],
                              shared_data_dir=tmp_path / 'raw', profiles_dir=tmp_path / 'profiles', download=True)
    assert not calls and not (tmp_path / 'profiles').exists()


@pytest.mark.parametrize('target_kind', ['source', 'input', 'peer'])
@pytest.mark.parametrize('link_kind', ['directory', 'stats-file', pytest.param('junction',
    marks=pytest.mark.skipif(os.name != 'nt', reason='Windows junction semantics'))])
def test_profile_rejects_output_links_before_building_or_changing_originals(
        tmp_path, monkeypatch, target_kind, link_kind):
    from rpipe.structure.data import profile
    from rpipe.structure.contracts import AdmissionError

    source, inputs, profiles = (tmp_path / name for name in ('source', 'inputs', 'profiles'))
    for path in (source, inputs, profiles):
        path.mkdir()
    target = source if target_kind == 'source' else inputs if target_kind == 'input' else profiles / 'peer'
    target.mkdir(exist_ok=True)
    sentinel = target / 'stats.yaml'
    sentinel.write_bytes(b'original input or source')
    folder = profiles / 'mnist'
    if link_kind == 'junction':
        import subprocess
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(folder), str(target)],
                       check=True, capture_output=True)
    elif link_kind == 'directory':
        folder.symlink_to(target, target_is_directory=True)
    else:
        folder.mkdir()
        (folder / 'stats.yaml').symlink_to(sentinel)
    calls = []
    monkeypatch.setattr(profile.DataFactory, 'build', lambda *args, **kwargs: calls.append(True))
    with pytest.raises(AdmissionError):
        profile.profile_study(source, configs=[{'data': {'name': 'MNIST', 'path': str(inputs)}}],
                              shared_data_dir=tmp_path / 'raw', profiles_dir=profiles)
    assert not calls
    assert sentinel.read_bytes() == b'original input or source'
