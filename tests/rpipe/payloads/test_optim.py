import pytest

from rpipe.structure.contracts import AlgorithmConfig
from rpipe.payloads.optim import clip_gradients, make_optimizer, make_scheduler
from rpipe.payloads.resume import resume_stem

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def test_resume_stem_train_defaults_to_latest_eval_to_best():
    train = AlgorithmConfig.from_mapping({'mode': 'train'})
    eval_cfg = AlgorithmConfig.from_mapping({'mode': 'eval'})
    assert resume_stem(train, mode='train') == 'latest'
    assert resume_stem(eval_cfg, mode='eval') == 'best'


def test_resume_stem_false_returns_none():
    off = AlgorithmConfig.from_mapping({'mode': 'train', 'resume': False})
    assert resume_stem(off, mode='train') is None


def test_resume_stem_named_resume_from_overrides_eval_best():
    named = AlgorithmConfig.from_mapping({'mode': 'eval', 'resume_from': 'step_000003'})
    assert resume_stem(named, mode='eval') == 'step_000003'


def test_make_optimizer_passes_valid_extras_and_ignores_unknown_keys():
    import torch

    module = torch.nn.Linear(1, 1)
    cfg = AlgorithmConfig.from_mapping(
        {
            'mode': 'train',
            'optimizer': 'SGD',
            'lr': 0.05,
            'momentum': 0.9,
            'nesterov': True,
            'weight_decay': 1e-4,
            'num_epochs': 20,
            'not_an_optim_kwarg': 1,
        }
    )
    opt = make_optimizer(module, cfg)
    group = opt.param_groups[0]
    assert group['lr'] == pytest.approx(0.05)
    assert group['momentum'] == pytest.approx(0.9)
    assert group['nesterov'] is True
    assert group['weight_decay'] == pytest.approx(1e-4)


def test_make_optimizer_rmsprop_uses_alpha():
    import torch

    module = torch.nn.Linear(1, 1)
    opt = make_optimizer(
        module,
        AlgorithmConfig.from_mapping({'mode': 'train', 'optimizer': 'RMSprop', 'lr': 0.01, 'alpha': 0.8}),
    )
    assert opt.__class__.__name__ == 'RMSprop'
    assert opt.param_groups[0]['alpha'] == pytest.approx(0.8)


def test_make_optimizer_unknown_name_raises_value_error():
    import torch

    module = torch.nn.Linear(1, 1)
    with pytest.raises(ValueError, match='unknown optimizer'):
        make_optimizer(module, AlgorithmConfig.from_mapping({'mode': 'train', 'optimizer': 'nope'}))


def test_linear_scheduler_with_warmup_keeps_positive_lr():
    import torch

    param = torch.nn.Parameter(torch.zeros(1))
    opt = torch.optim.SGD([param], lr=0.1)
    cfg = AlgorithmConfig.from_mapping(
        {'mode': 'train', 'scheduler': 'linear', 'warmup_steps': 2}
    )
    sched = make_scheduler(opt, cfg, 10)
    assert sched is not None
    opt.step()
    sched.step()
    assert opt.param_groups[0]['lr'] > 0


def test_clip_gradients_zero_or_missing_leaves_grad_unchanged():
    import torch

    module = torch.nn.Linear(2, 2)
    module.weight.grad = torch.ones_like(module.weight) * 10
    module.bias.grad = torch.ones_like(module.bias) * 10
    off = AlgorithmConfig.from_mapping({'mode': 'train'})
    assert clip_gradients(module, off) is None
    assert module.weight.grad.abs().max() == pytest.approx(10.0)
    zero = AlgorithmConfig.from_mapping({'mode': 'train', 'max_grad_norm': 0})
    assert clip_gradients(module, zero) is None


def test_clip_gradients_positive_norm_caps_grad():
    import torch

    module = torch.nn.Linear(2, 2)
    module.weight.grad = torch.ones_like(module.weight) * 10
    module.bias.grad = torch.ones_like(module.bias) * 10
    on = AlgorithmConfig.from_mapping({'mode': 'train', 'max_grad_norm': 1.0})
    clip_gradients(module, on)
    assert float(module.weight.grad.norm()) <= 1.0 + 1e-5
