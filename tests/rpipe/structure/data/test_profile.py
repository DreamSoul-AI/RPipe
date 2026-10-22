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
