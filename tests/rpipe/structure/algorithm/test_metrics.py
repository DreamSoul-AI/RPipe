import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from rpipe.structure.algorithm.metrics import MetricBundle, accuracy_value, mse_value, pack_io, resolve_metric_names


def test_resolve_metric_names_aliases():
    names = resolve_metric_names({'train': ['loss', 'accuracy'], 'test': ['MSE']})
    assert names['train'] == ['Loss', 'Accuracy']
    assert names['test'] == ['MSE']


def test_pack_io_tuple_and_dict():
    import torch

    logits = torch.tensor([[0.1, 0.9]])
    target = torch.tensor([1])
    packed = pack_io((torch.zeros(1, 1), target), logits)
    assert packed['target'] is target
    assert packed['logits'] is logits
    packed = pack_io({'target': target}, {'logits': logits, 'loss': torch.tensor(0.2)})
    assert abs(float(packed['loss'].item()) - 0.2) < 1e-6


def test_accuracy_and_mse_values():
    import torch

    logits = torch.tensor([[0.1, 4.0], [4.0, 0.1]])
    target = torch.tensor([1, 0])
    packed = pack_io((None, target), logits)
    assert accuracy_value(packed) == 100.0
    pred = torch.tensor([1.0, 2.0])
    y = torch.tensor([1.0, 3.0])
    assert abs(mse_value(pack_io({'target': y}, {'pred': pred})) - 0.5) < 1e-6


def test_topk_accuracy_keeps_sample_axis():
    import torch

    logits = torch.tensor([[5.0, 4.0, 1.0], [1.0, 5.0, 4.0], [4.0, 1.0, 5.0]])
    target = torch.tensor([1, 2, 1])
    packed = pack_io((None, target), logits)
    assert accuracy_value(packed) == 0.0
    assert accuracy_value(packed, topk=2) == pytest.approx(200.0 / 3)


def test_metric_bundle_batch_defaults():
    import torch

    bundle = MetricBundle()
    logits = torch.tensor([[0.1, 4.0]])
    target = torch.tensor([1])
    values = bundle.evaluate('train', 'batch', (None, target), logits)
    assert 'Loss' in values
    assert 'Accuracy' in values
    assert values['Accuracy'] == 100.0


@pytest.mark.parametrize('dtype', ['float32', 'int32', 'int64'])
def test_accuracy_label_vector_keeps_one_target_per_sample(dtype):
    import torch

    labels = torch.tensor([0, 1], dtype=getattr(torch, dtype))
    logits = torch.tensor([[0., 1.], [0., 1.]])
    assert accuracy_value(pack_io((None, labels), logits)) == 50.0


def test_accuracy_rejects_broadcasting_one_prediction_across_samples():
    import torch

    with pytest.raises(ValueError, match='sample counts'):
        accuracy_value(pack_io((None, torch.tensor([0, 0])), torch.tensor([[1., 0.]])))


def test_metric_name_mapping_keeps_empty_lists_and_string_key_collisions():
    result = resolve_metric_names({1: ['loss'], '1': ('accuracy',), 'empty': [], 'scalar': None})
    assert result == {'1': ['Accuracy'], 'empty': [], 'scalar': ['Loss']}
    assert list(result) == ['1', 'empty', 'scalar']


def test_finish_full_keeps_compute_order_duplicates_and_missing_holders():
    from types import SimpleNamespace

    calls = []
    bundle = MetricBundle({'test': ['Loss', 'GLUE', 'RMSE', 'GLUE', 'unknown']})
    bundle._full[('test', 'GLUE')] = SimpleNamespace(compute=lambda: calls.append('GLUE') or len(calls))
    result = bundle.finish_full('test')
    assert result == {'GLUE': 2.0} and calls == ['GLUE', 'GLUE']
    assert list(result) == ['GLUE']
    assert ('test', 'RMSE') not in bundle._full


def test_finish_full_preserves_original_compute_error_and_short_circuit():
    from types import SimpleNamespace

    calls = []
    error = RuntimeError('full metric failed')
    def fail():
        calls.append('RMSE')
        raise error
    bundle = MetricBundle({'test': ['RMSE', 'GLUE']})
    bundle._full[('test', 'RMSE')] = SimpleNamespace(compute=fail)
    bundle._full[('test', 'GLUE')] = SimpleNamespace(compute=lambda: calls.append('GLUE'))
    with pytest.raises(RuntimeError) as caught:
        bundle.finish_full('test')
    assert caught.value is error and calls == ['RMSE']


@pytest.mark.parametrize('branch', ['fallback', 'regression', 'classification', 'missing'])
def test_loss_value_keeps_fallback_and_classification_dispatch(monkeypatch, branch):
    import torch
    import torch.nn.functional as functional
    from types import SimpleNamespace
    from rpipe.structure.algorithm.metrics import loss_value

    logits, pred = object(), object()
    target = SimpleNamespace(dtype=torch.int64)
    calls = []
    def loss(kind, left, right):
        calls.append((kind, left, right))
        return SimpleNamespace(item=lambda: 7.0)
    monkeypatch.setattr(functional, 'mse_loss', lambda a, b: loss('mse', a, b))
    monkeypatch.setattr(functional, 'cross_entropy', lambda a, b: loss('ce', a, b))
    if branch == 'regression':
        target.dtype = torch.float32
    body = {'target': None if branch == 'missing' else target,
            'logits': None if branch == 'fallback' else logits, 'pred': pred}
    assert loss_value(body) == (0.0 if branch == 'missing' else 7.0)
    expected = [] if branch == 'missing' else [(
        'ce' if branch == 'classification' else 'mse', pred if branch == 'fallback' else logits, target)]
    assert calls == expected
