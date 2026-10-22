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
from rpipe.structure.algorithm.progress import checkpoint_names, crossed_percents, due_period, format_hms, infer_steps_per_epoch, parse_checkpoint_mode, remaining_seconds, resolve_budget, snapshot_name


def test_budget_defaults_to_one_step():
    budget = resolve_budget(AlgorithmConfig.from_mapping({'mode': 'train'}))
    assert budget.unit == 'step'
    assert budget.num_epochs is None
    assert budget.num_steps == 1
    assert budget.total == 1


def test_budget_infers_step_when_only_num_steps():
    budget = resolve_budget(AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 100}))
    assert budget.unit == 'step'
    assert budget.num_steps == 100
    assert budget.num_epochs is None
    assert budget.total == 100
    assert budget.scheduler_t_max() == 100


def test_budget_step_period_counts_optimizer_steps():
    budget = resolve_budget(
        AlgorithmConfig.from_mapping({'mode': 'train', 'num_epochs': 2, 'step_period': 2}),
        steps_per_epoch=5,
    )
    assert budget.num_steps == 6
    assert budget.steps_per_epoch == 3


def test_budget_epochs_overwrite_steps_when_inferred():
    budget = resolve_budget(
        AlgorithmConfig.from_mapping({'mode': 'train', 'num_epochs': 20, 'num_steps': 1000}),
        steps_per_epoch=5,
    )
    assert budget.unit == 'step'
    assert budget.num_steps == 100
    assert budget.total == 100
    assert budget.steps_from_epochs is True
    assert budget.steps_per_epoch == 5
    assert budget.progress(epoch=3, step=50) == 50


def test_budget_can_still_use_epoch_unit():
    cfg = AlgorithmConfig.from_mapping({'mode': 'train', 'num_epochs': 3, 'progress_unit': 'epoch'})
    budget = resolve_budget(cfg, steps_per_epoch=4)
    assert budget.unit == 'epoch'
    assert budget.total == 3
    assert budget.num_steps == 12


def test_infer_steps_per_epoch_from_meta():
    data = type('D', (), {'meta': {'train_size': 130, 'batch_size': 64}})()
    assert infer_steps_per_epoch(data) == 3


def test_infer_steps_per_epoch_prefers_data_method():
    data = type(
        'D',
        (),
        {'meta': {'train_size': 130, 'batch_size': 64}, 'steps_per_epoch': lambda self=None: 9},
    )()
    assert infer_steps_per_epoch(data) == 9


def test_due_period_and_percent_crossing():
    assert due_period(1, 1) is True
    assert due_period(2, 1) is False
    assert due_period(0, 4) is False
    assert crossed_percents(5, 20, (0.25, 0.5, 1.0), set()) == [0.25]
    assert crossed_percents(5, 20, (0.25, 0.5), {0.25}) == []
    assert snapshot_name('epoch', 5) == 'epoch_0005'
    assert snapshot_name('step', 100) == 'step_000100'


def test_checkpoint_names_default_latest_only():
    assert checkpoint_names(
        mode='latest',
        save_best=False,
        period=1,
        percents=(0.5, 1.0),
        current=1,
        total=4,
        unit='epoch',
        improved=True,
        is_last=False,
    ) == ['latest']


def test_checkpoint_names_best_and_percent():
    already: set[float] = set()
    names = checkpoint_names(
        mode='percent',
        save_best=True,
        period=2,
        percents=(0.5, 1.0),
        current=2,
        total=4,
        unit='epoch',
        improved=True,
        is_last=False,
        already_percent=already,
    )
    assert names == ['latest', 'epoch_0002', 'best']
    parse_checkpoint_mode('percent')
    with pytest.raises(ValueError):
        parse_checkpoint_mode('all')


def test_remaining_seconds_scales_with_leftover():
    assert remaining_seconds(elapsed=10.0, origin=0, current=2, total=10) == 40
    assert remaining_seconds(elapsed=10.0, origin=0, current=0, total=10) is None
    assert remaining_seconds(elapsed=5.0, origin=4, current=5, total=5) == 0
    assert format_hms(40) == '0:00:40'


@pytest.mark.parametrize('unit,epochs,steps,expected', [
    ('step', 3, 5, 5), ('epoch', 3, 5, 3), ('epoch', None, 7, 7),
    ('epoch', None, None, 1), ('epoch', 0, 7, 1), ('step', 9, 0, 1),
    ('epoch', -3, 7, 1), ('step', 9, -2, 1),
])
def test_scheduler_horizon_preserves_unit_precedence_fallback_and_lower_bound(unit, epochs, steps, expected):
    from rpipe.structure.algorithm.progress import ProgressBudget
    assert ProgressBudget(unit, epochs, steps, 1).scheduler_t_max() == expected


@pytest.mark.parametrize('unit,epochs,steps,error', [
    ('step', 4, None, TypeError), ('epoch', 'bad', 7, ValueError),
])
def test_scheduler_horizon_still_rejects_selected_invalid_integer(unit, epochs, steps, error):
    from rpipe.structure.algorithm.progress import ProgressBudget
    with pytest.raises(error):
        ProgressBudget(unit, epochs, steps, 1).scheduler_t_max()


@pytest.mark.parametrize('current,total', [(0, 4), (-1, 4), (2, 0), (2, -1)])
def test_percent_crossing_nonpositive_progress_does_not_consume_input(current, total):
    def untouched():
        raise AssertionError('percent input must remain untouched')
        yield 0.25

    assert crossed_percents(current, total, untouched(), set()) == []


def test_percent_crossing_preserves_generator_order_duplicates_and_membership_short_circuit():
    covered = object()
    already = {covered, 0.75}
    values = iter([0.5, covered, 0.25, 0.5, 0.75, 1.0])
    assert crossed_percents(2, 4, values, already) == [0.5, 0.25, 0.5]
    assert list(values) == []
    assert already == {covered, 0.75}


def test_percent_crossing_keeps_original_iterator_failure():
    failure = OSError('percent iterator failed')

    def broken():
        yield 0.25
        raise failure

    with pytest.raises(OSError) as raised:
        crossed_percents(2, 4, broken(), set())
    assert raised.value is failure
