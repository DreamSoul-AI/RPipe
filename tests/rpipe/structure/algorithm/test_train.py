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


from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.algorithm.train import TrainAlgorithm, make_scheduler
from rpipe.structure.algorithm.tracker import AlgorithmTracker


@pytest.mark.parametrize('unit', ['step', 'epoch'])
def test_train_summary_uses_last_evaluation_segment(tmp_path, unit):
    import torch
    from types import SimpleNamespace
    from rpipe.structure.system.factory import SystemFactory
    from rpipe.structure.contracts import SystemConfig

    class Batches:
        meta = {'train_size': 4, 'batch_size': 1}
        calls = 0

        def steps_per_epoch(self):
            return 2 if unit == 'epoch' else 4

        def iter_batches(self, split):
            if split == 'test':
                yield torch.tensor([[1.0, 0.0]]), torch.tensor([0])
                return
            targets = [self.calls, self.calls] if unit == 'epoch' else [0, 0, 1, 1]
            self.calls += 1
            for target in targets:
                yield torch.tensor([[1.0, 0.0]]), torch.tensor([target])

    module = torch.nn.Linear(2, 2, bias=False)
    with torch.no_grad():
        module.weight.copy_(torch.tensor([[5.0, 0.0], [-5.0, 0.0]]))
    expected = [torch.nn.functional.cross_entropy(module(torch.tensor([[1.0, 0.0]])),
                torch.tensor([target])).item() for target in (0, 1)]
    config = {'mode': 'train', 'lr': 0.0, 'weight_decay': 0.0, 'momentum': 0.0,
              'nesterov': False, 'progress_unit': unit, 'log_period': 1,
              'eval_period': 1 if unit == 'epoch' else 2}
    config.update({'num_epochs': 2} if unit == 'epoch' else {'num_steps': 4})
    tracker = AlgorithmTracker(tmp_path)
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    out = TrainAlgorithm(AlgorithmConfig.from_mapping(config)).run(
        Batches(), SimpleNamespace(module=module), system, tracker,
    )
    assert out['train_loss'] == pytest.approx(expected[-1])
    assert tracker.state_dict()['splits']['train']['Loss']['history'] == pytest.approx(expected)


class _Data:
    name = 'Toy'
    source = 'stub'
    meta = {'stub': True}


def test_two_epoch_stub_train_does_not_raise(tmp_path):
    algo = TrainAlgorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'num_epochs': 2}))
    tracker = AlgorithmTracker(tmp_path)
    out = algo.run(_Data(), None, None, tracker)
    assert out['mode'] == 'train'
    assert out['stub'] is True
    assert tracker.segment_mean('train').get('Loss') == 0.0


@pytest.mark.parametrize('mode', ['train', 'eval'])
@pytest.mark.parametrize('source', ['custom_torch', 'transformers_trainer'])
def test_missing_inputs_do_not_become_stub_results(tmp_path, mode, source):
    from types import SimpleNamespace

    from rpipe.structure.algorithm.factory import AlgorithmFactory

    algorithm = AlgorithmFactory.build(AlgorithmConfig(mode=mode, source=source))
    tracker = AlgorithmTracker(tmp_path)
    for data, model in [
        (None, None),
        (SimpleNamespace(source='torch', meta={}, iter_batches=lambda split: iter(())), None),
        (SimpleNamespace(source='stub', meta={'stub': False}), None),
        (SimpleNamespace(source='torch', meta={'stub': True}), None),
        (SimpleNamespace(), SimpleNamespace(module=object())),
    ]:
        with pytest.raises(ValueError, match='requires a model module and data.iter_batches'):
            algorithm.run(data, model, None, tracker)
    assert tracker.segment_mean('train') == {}
    assert tracker.segment_mean('test') == {}


@pytest.mark.parametrize('mode', ['train', 'eval'])
@pytest.mark.parametrize('source', ['custom_torch', 'transformers_trainer'])
def test_explicit_stub_is_supported_by_both_sources(tmp_path, mode, source):
    from types import SimpleNamespace

    from rpipe.structure.algorithm.factory import AlgorithmFactory as algorithm_api
    from rpipe.structure.data.factory import DataFactory as data_api
    from rpipe.structure.contracts import DataConfig

    data = data_api.build(DataConfig(name='Toy', source='stub'), tmp_path, seed=2)
    algorithm = algorithm_api.build(AlgorithmConfig(mode=mode, source=source))
    result = algorithm.run(data, SimpleNamespace(module=object()), None, AlgorithmTracker(tmp_path))
    assert result['mode'] == mode
    assert result['stub'] is True
    assert data.meta['seed'] == 2


def test_make_scheduler_none_or_constant():
    cfg = AlgorithmConfig.from_mapping({'mode': 'train'})
    assert make_scheduler(object(), cfg, 20) is None
    cfg = AlgorithmConfig.from_mapping({'mode': 'train', 'scheduler': 'constant'})
    assert make_scheduler(object(), cfg, 20) is None


def test_make_scheduler_unknown_raises():
    cfg = AlgorithmConfig.from_mapping({'mode': 'train', 'scheduler': 'nope'})
    with pytest.raises(ValueError, match='unknown scheduler'):
        make_scheduler(object(), cfg, 20)


def test_cosine_scheduler_decays_to_eta_min():
    import torch

    param = torch.nn.Parameter(torch.zeros(1))
    opt = torch.optim.SGD([param], lr=0.1)
    cfg = AlgorithmConfig.from_mapping(
        {'mode': 'train', 'scheduler': 'cosine', 'eta_min': 0.0}
    )
    sched = make_scheduler(opt, cfg, 20)
    assert sched is not None
    first = opt.param_groups[0]['lr']
    for _ in range(20):
        opt.step()
        sched.step()
    last = opt.param_groups[0]['lr']
    assert first == pytest.approx(0.1)
    assert last == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize('eval_period', [1, 0], ids=['periodic_eval', 'terminal_eval'])
def test_train_reports_lr_used_by_completed_optimizer_step(tmp_path, eval_period):
    import re
    from types import SimpleNamespace

    import torch

    from rpipe.structure.contracts import SystemConfig
    from rpipe.structure.system.factory import SystemFactory

    images = torch.eye(2)
    targets = torch.arange(2)

    class _Batches:
        meta = {'train_size': 12, 'batch_size': 2}

        def iter_batches(self, split):
            for _ in range(6 if split == 'train' else 1):
                yield images, targets

    module = torch.nn.Linear(2, 2, bias=False)
    torch.nn.init.zeros_(module.weight)
    reference = torch.nn.Linear(2, 2, bias=False)
    reference.load_state_dict(module.state_dict())
    expected_lrs = [0.1, 0.075, 0.025]
    reference_optimizer = torch.optim.SGD(reference.parameters(), lr=expected_lrs[0])
    for used_lr in expected_lrs:
        reference_optimizer.param_groups[0]['lr'] = used_lr
        reference_optimizer.zero_grad()
        torch.nn.functional.cross_entropy(reference(images), targets).backward()
        reference_optimizer.step()

    config = AlgorithmConfig.from_mapping({
        'mode': 'train', 'num_steps': 3, 'step_period': 2,
        'lr': 0.1, 'scheduler': 'cosine', 'log_period': 1,
        'eval_period': eval_period, 'checkpoint_period': 0,
    })
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    model = SimpleNamespace(module=module)
    result = TrainAlgorithm(config).run(_Batches(), model, system, AlgorithmTracker(tmp_path))
    records = re.findall(
        r'\[split\] (train|test).*? lr=([^ ]+) step=(\d+)',
        system.logger.path.read_text(encoding='utf-8'),
    )
    train = [(int(step), float(lr)) for split, lr, step in records if split == 'train']
    test = [(int(step), float(lr)) for split, lr, step in records if split == 'test']
    assert [step for step, _ in train] == ([1, 2, 3] if eval_period else [1, 2, 3, 3])
    assert [lr for _, lr in train] == pytest.approx(expected_lrs if eval_period else expected_lrs + [expected_lrs[-1]])
    assert [step for step, _ in test] == ([1, 2, 3] if eval_period else [3])
    assert [lr for _, lr in test] == pytest.approx(expected_lrs if eval_period else [expected_lrs[-1]])
    assert result['steps'] == 3
    torch.testing.assert_close(module.weight, reference.weight)

    # No new optimizer step: the restored current LR, not the old used LR, is reported.
    TrainAlgorithm(config).run(_Batches(), model, system, AlgorithmTracker(tmp_path))
    resumed = re.findall(
        r'\[split\] test.*? lr=([^ ]+) step=(\d+)',
        system.logger.path.read_text(encoding='utf-8'),
    )
    assert tuple(map(float, resumed[-1])) == pytest.approx((0.0, 3))
    torch.testing.assert_close(module.weight, reference.weight)


@pytest.mark.parametrize('unit', ['step', 'epoch'])
@pytest.mark.parametrize('early_stop', [False, True], ids=['complete', 'early_stop'])
def test_checkpoint_progress_matches_completed_updates(tmp_path, unit, early_stop):
    from copy import deepcopy
    from math import cos, pi
    from types import SimpleNamespace

    import torch

    from rpipe.structure.contracts import SystemConfig
    from rpipe.structure.system.factory import SystemFactory

    images, targets = torch.eye(2), torch.arange(2)

    class _Batches:
        meta = {'train_size': 4, 'batch_size': 2}

        def iter_batches(self, split):
            for _ in range(2 if split == 'train' else 1):
                yield images, targets

    settings = {
        'mode': 'train', 'progress_unit': unit,
        ('num_steps' if unit == 'step' else 'num_epochs'): 4,
        'step_period': 2, 'lr': 0.1, 'momentum': 0.9, 'scheduler': 'cosine',
        'eval_period': 1, 'checkpoint_period': 1, 'checkpoint': 'percent',
        'checkpoint_percents': [0.5, 1.0], 'save_best': True, 'best_metric': 'Loss',
    }
    if early_stop:
        settings.update(early_stop_patience=1, early_stop_min_delta=100.0)
    config = AlgorithmConfig.from_mapping(settings)

    def execute(root, *, interrupt=False):
        module = torch.nn.Linear(2, 2, bias=False)
        torch.nn.init.zeros_(module.weight)
        system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), root)
        snapshots = []
        save = system.save_checkpoint

        def save_and_observe(payload, name):
            snapshots.append((name, deepcopy(payload)))
            path = save(payload, name)
            if interrupt and name == 'latest' and payload['step'] == 2:
                raise RuntimeError('injected interruption after checkpoint')
            return path

        system.save_checkpoint = save_and_observe
        result = TrainAlgorithm(config).run(
            _Batches(), SimpleNamespace(module=module), system, AlgorithmTracker(root)
        )
        return result, module, snapshots, system

    result, module, snapshots, system = execute(tmp_path / 'clean')
    final_step = 2 if early_stop else 4
    assert result['steps'] == final_step
    assert result['best_metric'] == 'Loss'
    assert 'best_accuracy' not in result
    assert {name for name, _ in snapshots} >= {
        'latest', 'best', 'step_000002' if unit == 'step' else 'epoch_0002',
    }
    reference = torch.nn.Linear(2, 2, bias=False)
    torch.nn.init.zeros_(reference.weight)
    optimizer = torch.optim.SGD(reference.parameters(), lr=0.1, momentum=0.9)
    weights = {}
    for step in range(1, final_step + 1):
        optimizer.param_groups[0]['lr'] = 0.05 * (1 + cos(pi * (step - 1) / 4))
        optimizer.zero_grad()
        torch.nn.functional.cross_entropy(reference(images), targets).backward()
        optimizer.step()
        weights[step] = deepcopy(reference.state_dict())
    for _name, payload in snapshots:
        step = payload['step']
        assert payload['scheduler']['last_epoch'] == step
        assert payload['optimizer']['param_groups'][0]['lr'] == pytest.approx(
            0.05 * (1 + cos(pi * step / 4))
        )
        torch.testing.assert_close(payload['model'], weights[step])
        assert payload['best_metric'] == 'Loss'
        assert payload['best_value'] is not None
        assert 'best_accuracy' not in payload
    torch.testing.assert_close(module.weight, reference.weight)

    if not early_stop:
        # Fixed repeated batches isolate optimizer/scheduler recovery, not RNG or sampler parity.
        with pytest.raises(RuntimeError, match='injected interruption'):
            execute(tmp_path / 'interrupted', interrupt=True)
        resumed, resumed_model, _, resumed_system = execute(tmp_path / 'interrupted')
        assert resumed['steps'] == final_step
        assert resumed['best_value'] == pytest.approx(result['best_value'])
        torch.testing.assert_close(resumed_model.weight, module.weight)
        assert resumed_system.load_checkpoint('latest')['scheduler'] == system.load_checkpoint('latest')['scheduler']
        # Budget already done: no update or checkpoint rewrite, and Loss keeps its own name.
        done, done_model, writes, _ = execute(tmp_path / 'interrupted')
        assert done['steps'] == final_step
        assert done['best_metric'] == 'Loss'
        assert 'best_accuracy' not in done
        assert writes == []
        torch.testing.assert_close(done_model.weight, module.weight)


@pytest.mark.parametrize('metric', ['Loss', 'Accuracy'])
def test_checkpoint_hook_normalizes_best_metric_for_all_train_callers(metric):
    from types import SimpleNamespace

    algo = TrainAlgorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'best_metric': metric}))
    algo._best_test = 0.25
    saved = []
    # Some callers build their payload before adding generic best metadata (HF included).
    payload = {'step': 2, 'best_accuracy': 0.25}
    algo.on_checkpoint(None, None, None, None, SimpleNamespace(
        save_checkpoint=lambda body, name: saved.append(body),
    ), {'checkpoint_names': ['latest'], 'payload': payload})
    assert saved[0]['best_metric'] == metric
    assert saved[0]['best_value'] == 0.25
    if metric == 'Accuracy':
        assert saved[0]['best_accuracy'] == 0.25
    else:
        assert 'best_accuracy' not in saved[0]


@pytest.mark.parametrize('primary_type', [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize('secondary_kind', ['publication', 'serialization'])
@pytest.mark.parametrize('log_mode', ['available', 'refused', 'absent'])
def test_train_final_flush_cannot_replace_primary_failure(tmp_path, monkeypatch, primary_type, secondary_kind, log_mode):
    """Supervised train failure -> final flush refusal -> original error and old state survive."""
    from types import SimpleNamespace
    import torch
    from rpipe.structure import artifacts as _atomic
    from rpipe.structure.system.factory import SystemFactory
    from rpipe.structure.contracts import SystemConfig

    tracker = AlgorithmTracker(tmp_path)
    tracker.flush_state()
    old = tracker.state_path.read_bytes()
    peer = tmp_path / 'peer.tmp'
    peer.write_bytes(b'peer evidence')
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    log_path = system.logger.path
    if log_mode == 'absent':
        system.logger = None
    elif log_mode == 'refused':
        def no_log(*args, **kwargs):
            raise OSError('diagnostic log refused')
        monkeypatch.setattr(system.logger, 'exception', no_log)
    primary = primary_type('original iterator failure')
    calls = []
    def batches(split):
        calls.append(split)
        raise primary
    data = SimpleNamespace(meta={'train_size': 1, 'batch_size': 1}, steps_per_epoch=lambda: 1, iter_batches=batches)
    if secondary_kind == 'publication':
        replace = _atomic.atomic_replace
        def refuse(source, target):
            if target == tracker.state_path:
                raise PermissionError('final state publication refused')
            return replace(source, target)
        monkeypatch.setattr(_atomic, 'atomic_replace', refuse)
    else:
        state_dict = tracker.state_dict
        def bad_state():
            return {**state_dict(), 'unserializable': object()}
        monkeypatch.setattr(tracker, 'state_dict', bad_state)
    algorithm = TrainAlgorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 1}))
    with pytest.raises(BaseException) as caught:
        algorithm.run(data, SimpleNamespace(module=torch.nn.Linear(2, 2)), system, tracker)
    assert calls == ['train']
    assert tracker.state_path.read_bytes() == old
    assert peer.read_bytes() == b'peer evidence'
    assert set(tmp_path.glob('*.tmp')) == {peer}
    assert not list(tracker.state_path.parent.glob('*.tmp'))
    assert caught.value is primary
    if log_mode == 'available':
        import json
        text = log_path.read_text(encoding='utf-8')
        record = json.loads(text.splitlines()[-1])
        assert record['message'] == 'tracker final flush failed'
        assert record['exception_type'] == ('PermissionError' if secondary_kind == 'publication' else 'TypeError')
        assert primary_type.__name__ in record['traceback']
        assert record['exception_message'] == '[redacted]'
        assert 'original iterator failure' not in text


@pytest.mark.parametrize('secondary_kind', ['publication', 'serialization'])
def test_successful_train_final_flush_failure_still_propagates(tmp_path, monkeypatch, secondary_kind):
    """Successful tiny CPU train body -> failed final flush -> publication error still propagates."""
    from types import SimpleNamespace
    import torch
    from rpipe.structure import artifacts as _atomic
    from rpipe.structure.system.factory import SystemFactory
    from rpipe.structure.contracts import SystemConfig

    class Batches:
        meta = {'train_size': 2, 'batch_size': 2}
        def steps_per_epoch(self):
            return 1
        def iter_batches(self, split):
            yield torch.eye(2), torch.tensor([0, 1])
    tracker = AlgorithmTracker(tmp_path)
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    algorithm = TrainAlgorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 1, 'eval_period': 0, 'checkpoint_period': 0}))
    armed = []
    def final_checkpoint(*args):
        armed.append(tracker.state_path.read_bytes())
    monkeypatch.setattr(algorithm, 'on_checkpoint', final_checkpoint)
    if secondary_kind == 'publication':
        replace = _atomic.atomic_replace
        def refuse(source, target):
            if armed and target == tracker.state_path:
                raise PermissionError('final state publication refused')
            return replace(source, target)
        monkeypatch.setattr(_atomic, 'atomic_replace', refuse)
    else:
        state_dict = tracker.state_dict
        def bad_state():
            body = state_dict()
            return {**body, 'unserializable': object()} if armed else body
        monkeypatch.setattr(tracker, 'state_dict', bad_state)
    with pytest.raises(PermissionError if secondary_kind == 'publication' else TypeError):
        algorithm.run(Batches(), SimpleNamespace(module=torch.nn.Linear(2, 2)), system, tracker)
    assert len(armed) == 1
    assert tracker.state_path.read_bytes() == armed[0]
    assert not list(tmp_path.glob('*.tmp'))
    assert not list(tracker.state_path.parent.glob('*.tmp'))
