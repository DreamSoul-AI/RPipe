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


from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.hf_map import hf_optim_name, hf_scheduler_name, training_arguments_kwargs


def test_hf_maps_sgd_cosine_and_resume_key():
    cfg = AlgorithmConfig.from_mapping(
        {
            'mode': 'train',
            'source': 'transformers_trainer',
            'optimizer': 'SGD',
            'lr': 0.1,
            'scheduler': 'cosine',
            'num_steps': 10,
            'warmup_ratio': 0.1,
        }
    )
    kwargs = training_arguments_kwargs(
        cfg, output_dir='/tmp/hf', resume_from_checkpoint='/tmp/ckpt'
    )
    assert kwargs['optim'] == 'sgd'
    assert kwargs['learning_rate'] == 0.1
    assert kwargs['lr_scheduler_type'] == 'cosine'
    assert kwargs['max_steps'] == 10
    assert kwargs['report_to'] == 'none'
    assert kwargs['warmup_ratio'] == 0.1
    assert kwargs['resume_from_checkpoint'] == '/tmp/ckpt'
    assert kwargs['max_grad_norm'] == 0.0
    stepped = training_arguments_kwargs(
        AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 8, 'step_period': 2}),
        output_dir='/tmp/hf',
    )
    assert stepped.get('gradient_accumulation_steps') == 2
    assert hf_optim_name('AdamW') == 'adamw_torch'
    assert hf_scheduler_name(None) == 'constant'
    clipped = training_arguments_kwargs(
        AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 1, 'max_grad_norm': 1.0}),
        output_dir='/tmp/hf',
    )
    assert clipped['max_grad_norm'] == pytest.approx(1.0)


def test_hf_overlay_retains_none_value_identity_and_existing_key_positions(monkeypatch):
    import sys
    from types import SimpleNamespace
    class Arguments:
        def __init__(self, learning_rate=None, custom_flag=None, report_to=None):
            pass
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(TrainingArguments=Arguments))
    token = object()
    config = AlgorithmConfig.from_mapping({'num_steps': 2, 'learning_rate': 0.25,
                                           'custom_flag': token, 'report_to': None})
    result = training_arguments_kwargs(config, output_dir='unused')
    assert result['learning_rate'] == 0.25 and result['report_to'] is None
    assert result['custom_flag'] is token
    assert list(result)[-1] == 'custom_flag'
    assert list(result).index('learning_rate') < list(result).index('report_to')


def test_filter_args_retains_reserved_unknown_and_variadic_guards():
    from rpipe.structure.algorithm.kwargs import filter_args
    def constructor(self, /, x=0, optimizer=None, *args, y=1, **kwargs):
        pass
    token = object()
    payload = {'unknown': token, 'self': token, 'x': token, 'optimizer': token,
               'args': token, 'y': token, 'kwargs': token}
    result = filter_args(constructor, payload)
    assert list(result) == ['x', 'y'] and all(value is token for value in result.values())


@pytest.mark.parametrize('epsilon, present, expected', [
    ({'adam_epsilon': 7}, True, 7),
    ({'adam_epsilon': 7, 'eps': 0}, True, 0),
    ({'adam_epsilon': 7, 'eps': None}, False, None),
])
def test_hf_alias_forwarding_keeps_order_identity_and_epsilon_default(monkeypatch, epsilon, present, expected):
    import sys
    from types import SimpleNamespace
    class Arguments:
        def __init__(self):
            pass
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(TrainingArguments=Arguments))
    token = object()
    payload = {'num_steps': 1, 'weight_decay': 0, 'warmup_ratio': token, 'warmup_steps': 0,
               'adam_beta1': 0, 'adam_beta2': 0, 'momentum': token, **epsilon}
    config = AlgorithmConfig.from_mapping(payload)
    result = training_arguments_kwargs(config, output_dir='unused', resume_from_checkpoint='resume')
    tail = ['weight_decay', 'warmup_ratio', 'warmup_steps', 'adam_beta1', 'adam_beta2']
    if present:
        tail.append('adam_epsilon')
    tail += ['resume_from_checkpoint']
    assert list(result)[-len(tail):] == tail
    assert ('adam_epsilon' in result) == present
    if present:
        assert result['adam_epsilon'] == expected
    assert result['warmup_ratio'] is token
    assert 'momentum' not in result
    assert result['max_grad_norm'] == 0.0
    assert result['resume_from_checkpoint'] == 'resume'
    assert config.extras['momentum'] is token


def test_sgd_momentum_is_consumed_by_optimizer_and_not_training_arguments():
    from rpipe.structure.algorithm.optim import make_optimizer
    import torch

    cfg = AlgorithmConfig.from_mapping({'source': 'transformers_trainer', 'optimizer': 'SGD',
                                       'momentum': 0.7, 'num_steps': 1})
    kwargs = training_arguments_kwargs(cfg, output_dir='unused')
    assert 'momentum' not in kwargs
    assert make_optimizer(torch.nn.Linear(1, 1), cfg).param_groups[0]['momentum'] == 0.7
