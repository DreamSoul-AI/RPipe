"""Train mode."""

from __future__ import annotations

from typing import Any

from rpipe.structure.algorithm.factory import Algorithm
from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.algorithm.batch import prepare_tensors
from rpipe.structure.algorithm.evaluation_support import (
    _is_better,
    best_spec,
    eval_batch_limit,
    eval_test_split,
    should_early_stop,
)
from rpipe.structure.algorithm.optim import clip_gradients, make_scheduler
from rpipe.structure.algorithm.progress import (
    UNIT_EPOCH,
    UNIT_STEP,
    EtaClock,
    ProgressBudget,
    checkpoint_names,
    crossed_percents,
    due_period,
    format_hms,
    infer_steps_per_epoch,
    resolve_budget,
)
from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.algorithm.resume import capture_rng, restore_rng, partial_resume_warning
from rpipe.structure.algorithm.metrics import BATCH_NAMES, MetricBundle

due_eval_period = due_period


class TrainAlgorithm(Algorithm):
    def __init__(self, config: AlgorithmConfig) -> None:
        super().__init__(config)
        self._best_test: float | None = None
        self._best_metric = best_spec(config)[1]
        self._stall = 0
        self.last_improved = False

    def on_eval_period(
        self,
        tracker: AlgorithmTracker,
        logger: Any,
        data: Any,
        model: Any,
        system: Any,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        extra = extra if extra is not None else {}
        self.last_improved = False
        if getattr(model, 'module', None) is None:
            return False
        metrics = eval_test_split(
            tracker, logger, data, model, system, extra, num_steps=eval_batch_limit(self.config)
        )
        split, metric, mode = best_spec(self.config)
        self._best_metric = metric
        patience = self.config.setting('early_stop_patience')
        if patience is not None:
            patience = int(patience)
        min_delta = float(self.config.setting('early_stop_min_delta', 0.0) or 0.0)
        pool = metrics if split == 'test' else tracker.segment_mean(split)
        score = pool.get(metric)
        previous = self._best_test
        stop, self._best_test, self._stall = should_early_stop(
            value=score,
            best=self._best_test,
            stall=self._stall,
            patience=patience,
            min_delta=min_delta,
            mode=mode,
        )
        self.last_improved = score is not None and _is_better(
            score, previous, min_delta=min_delta, mode=mode
        )
        extra['test_accuracy'] = metrics.get('Accuracy')
        extra['best_metric'] = metric
        extra['best_value'] = self._best_test
        if metric == 'Accuracy':
            extra['best_accuracy'] = self._best_test
        else:
            extra.pop('best_accuracy', None)
        extra['improved'] = self.last_improved
        if stop and logger is not None:
            logger.info(
                f"early stop epoch={extra.get('epoch')} step={extra.get('step')} "
                f'best_{metric}={self._best_test} stall={self._stall}'
            )
        return stop

    def on_checkpoint(
        self,
        tracker: AlgorithmTracker,
        logger: Any,
        data: Any,
        model: Any,
        system: Any,
        extra: dict[str, Any] | None = None,
    ) -> None:
        del data
        extra = extra or {}
        names = list(extra.get('checkpoint_names') or [])
        payload = extra.get('payload')
        writer = getattr(system, 'save_checkpoint', None)
        if not names or payload is None or not callable(writer):
            return
        payload = dict(payload)
        payload['best_metric'] = self._best_metric
        payload['best_value'] = self._best_test
        if self._best_metric == 'Accuracy' and self._best_test is not None:
            payload['best_accuracy'] = self._best_test
        else:
            payload.pop('best_accuracy', None)
        for name in names:
            path = writer(payload, name)
            if logger is not None:
                logger.info(f'name={name} path={path}', event='ckpt')

    def run(self, data: Any, model: Any, system: Any, tracker: AlgorithmTracker) -> dict[str, Any]:
        if getattr(data, 'source', None) == 'stub' and (getattr(data, 'meta', None) or {}).get('stub') is True:
            return _run_stub(self.config, tracker)
        if getattr(model, 'module', None) is None or not callable(getattr(data, 'iter_batches', None)):
            raise ValueError('train requires a model module and data.iter_batches; use explicit stub data for flow checks')
        return _run_supervised(self, data, model, system, tracker)


def run(control_algorithm: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Legacy dict entry; prefer TrainAlgorithm.run."""
    algo = TrainAlgorithm(AlgorithmConfig.from_mapping(control_algorithm))
    return algo.run(
        state.get('data'),
        state.get('model'),
        state.get('system'),
        state['tracker'],
    )


def _current_lr(optimizer: Any, fallback: float) -> float:
    groups = getattr(optimizer, 'param_groups', None)
    if not groups:
        return fallback
    return float(groups[0].get('lr', fallback))


def _hook_extra(*, epoch: int, lr: float, step: int | None = None) -> dict[str, Any]:
    extra: dict[str, Any] = {'epoch': epoch, 'lr': lr}
    if step is not None:
        extra['step'] = step
    return extra


def _build_payload(
    module: Any,
    optimizer: Any,
    scheduler: Any,
    extra: dict[str, Any],
    tracker: AlgorithmTracker | None = None,
    logger: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'epoch': extra.get('epoch'),
        'step': extra.get('step'),
        'model': module.state_dict(),
        'optimizer': optimizer.state_dict() if optimizer is not None else None,
        'scheduler': scheduler.state_dict() if scheduler is not None else None,
    }
    if extra.get('test_accuracy') is not None:
        payload['test_accuracy'] = extra['test_accuracy']
    if extra.get('best_accuracy') is not None:
        payload['best_accuracy'] = extra['best_accuracy']
    if extra.get('best_value') is not None:
        payload['best_value'] = extra['best_value']
        payload['best_metric'] = extra.get('best_metric')
    if tracker is not None:
        payload['tracker'] = tracker.state_dict()
        progress = extra.get('progress') or extra
        payload['tracker']['progress'] = {
            key: progress[key] for key in ('step', 'epoch') if key in progress
        }
    if logger is not None and hasattr(logger, 'state_dict'):
        payload['logger'] = logger.state_dict()
    return payload


def _run_stub(config: AlgorithmConfig, tracker: AlgorithmTracker) -> dict[str, Any]:
    budget = resolve_budget(config, steps_per_epoch=1)
    tracker.append('train', n=1, values={'Loss': 0.0})
    tracker.save('train')
    tracker.reset('train')
    tracker.flush('train')
    return {
        'mode': 'train',
        'steps': budget.num_steps,
        'epochs': budget.num_epochs or 1,
        'stub': True,
        'resume_count': 0, 'resume_path': None, 'resume_step': None,
    }


def bind_step_train_loader(
    data: Any,
    config: AlgorithmConfig,
    *,
    step: int,
    budget: ProgressBudget,
) -> None:
    """When progress is step-budget, rebuild train loader like main ``make_data_loader``."""
    if budget.unit != UNIT_STEP:
        return
    rebind = getattr(data, 'rebind_train_steps', None)
    if not callable(rebind):
        return
    period = max(int(config.setting('step_period', 1) or 1), 1)
    rebind(step=int(step), num_steps=int(budget.num_steps), step_period=period)


def _run_supervised(
    algo: TrainAlgorithm,
    data: Any,
    model: Any,
    system: Any,
    tracker: AlgorithmTracker,
) -> dict[str, Any]:
    import torch
    import torch.nn.functional as F

    config = algo.config
    device = torch.device(getattr(system, 'device', 'cpu'))
    module = system.place_module(model.module)
    model.module = module
    logger = getattr(system, 'logger', None)
    budget = resolve_budget(config, steps_per_epoch=infer_steps_per_epoch(data))
    lr = float(config.setting('lr', 0.1))
    log_period = config.setting('log_period', config.setting('log_interval'))
    log_period = int(log_period) if log_period is not None else None
    eval_period = algo.eval_period()
    ckpt_period = algo.checkpoint_period()
    ckpt_mode = algo.checkpoint_mode()
    percents = algo.checkpoint_percents()
    keep_best = algo.save_best()
    already_percent: set[float] = set()

    resume_extra: dict[str, Any] = {}
    restored = algo.resume(data, model, system, tracker, resume_extra)
    optimizer = algo.make_optimizer(module)
    scheduler = algo.make_scheduler(optimizer, budget.scheduler_t_max())
    if restored:
        opt_state = restored.get('optimizer')
        if opt_state is not None:
            optimizer.load_state_dict(opt_state)
        sched_state = restored.get('scheduler')
        if scheduler is not None and sched_state is not None:
            scheduler.load_state_dict(sched_state)
        if restored.get('best_value') is not None:
            algo._best_test = restored['best_value']
        elif algo._best_metric == 'Accuracy' and restored.get('best_accuracy') is not None:
            algo._best_test = restored['best_accuracy']
    module.train()
    epoch = int(restored.get('epoch') or 0) if restored else 0
    steps = int(restored.get('step') or 0) if restored else 0
    period = max(int(config.setting('step_period', 1) or 1), 1)
    settings = config.to_mapping()
    settings.pop('resume', None)
    settings.pop('resume_from', None)
    settings['config'] = {key: value for key, value in (settings.get('config') or {}).items()
                          if key not in ('resume', 'resume_from')}
    contract = {'unit': budget.unit, 'num_steps': budget.num_steps, 'num_epochs': budget.num_epochs,
                'step_period': period, 'settings': settings, 'device': str(device), 'torch': str(torch.__version__),
                'deterministic': torch.are_deterministic_algorithms_enabled(),
                'cudnn_benchmark': torch.backends.cudnn.benchmark,
                'cudnn_allow_tf32': torch.backends.cudnn.allow_tf32,
                'matmul_allow_tf32': torch.backends.cuda.matmul.allow_tf32}
    exact_metrics = (device.type in ('cpu', 'cuda') and type(tracker.metrics) is MetricBundle
                     and set(tracker.metrics.names_for('train')) <= BATCH_NAMES)
    contract['metrics'] = getattr(tracker.metrics, 'names', None)
    training_state = (restored or {}).get('training_state')
    resume_inside_epoch = False
    if training_state is not None:
        if training_state.get('version') != 1:
            raise ValueError('unsupported checkpoint training_state version')
        if training_state.get('data') is not None and training_state.get('contract') != contract:
            raise ValueError('checkpoint training contract differs; keep budget/settings/runtime unchanged')
        algo._stall = int(training_state.get('stall', 0))
        already_percent = set(training_state.get('already_percent') or [])
    tracker.begin_run((restored or {}).get('tracker'))
    bind_step_train_loader(data, config, step=0 if training_state and training_state.get('data') else steps, budget=budget)
    if training_state is not None:
        if training_state.get('data') is not None:
            restore_data = getattr(data, 'restore_training_state', None)
            if not callable(restore_data):
                raise ValueError('data does not support checkpoint iterator restoration')
            resume_inside_epoch = restore_data(training_state['data'])
        else:
            partial_resume_warning(logger)
        restore_rng(training_state['rng'])
    clock = EtaClock(origin=steps if budget.unit == UNIT_STEP else epoch)
    stop = bool(training_state and training_state.get('stopped'))
    used_lr = _current_lr(optimizer, lr)

    def _timing() -> dict[str, str]:
        current = steps if budget.unit == UNIT_STEP else epoch
        return clock.extra(current=current, total=budget.total)

    def finish_train_segment(extra: dict[str, Any], *, reported: bool = False) -> None:
        if not tracker.has_samples('train'):
            return
        if not reported:
            if logger is not None:
                logger.report(tracker, 'train', extra=extra)
            tracker.flush('train', progress=extra)
        tracker.save('train')
        tracker.reset('train')
        tracker.flush_state()

    def fire_eval(extra: dict[str, Any]) -> bool:
        if budget.unit == UNIT_STEP:
            finish_train_segment(extra, reported=bool(log_period and steps % log_period == 0))
        return bool(algo.on_eval_period(tracker, logger, data, model, system, extra))

    def fire_ckpt(*, extra: dict[str, Any], improved: bool, is_last: bool) -> None:
        nonlocal already_percent
        current = budget.progress(epoch=epoch, step=steps)
        names = checkpoint_names(
            mode=ckpt_mode,
            save_best=keep_best,
            period=ckpt_period,
            percents=percents,
            current=current,
            total=budget.total,
            unit=budget.unit,
            improved=improved,
            is_last=is_last,
            already_percent=already_percent,
        )
        if not names:
            return
        extra = dict(extra)
        extra['checkpoint_names'] = names
        extra['payload'] = _build_payload(module, optimizer, scheduler, extra, tracker, logger)
        data_state = getattr(data, 'training_state', None)
        hits = crossed_percents(current, budget.total, percents, already_percent) if ckpt_mode == 'percent' else []
        extra['payload']['training_state'] = {
            'version': 1, 'rng': capture_rng(), 'data': data_state() if callable(data_state) and exact_metrics else None,
            'contract': contract, 'stall': algo._stall, 'already_percent': sorted(already_percent | set(hits)),
            'stopped': stop,
        }
        algo.on_checkpoint(tracker, logger, data, model, system, extra)
        if ckpt_mode == 'percent':
            for hit in crossed_percents(current, budget.total, percents, already_percent):
                already_percent.add(hit)

    already_done = budget.progress(epoch=epoch, step=steps) >= budget.total
    wrote_last_ckpt = stop
    try:
        if already_done:
            extra = _hook_extra(epoch=epoch, lr=used_lr, step=steps)
            extra.update(_timing())
            fire_eval(extra)
        else:
            while True:
                if stop:
                    break
                finishing_restored_epoch = resume_inside_epoch
                if not resume_inside_epoch:
                    epoch += 1
                resume_inside_epoch = False
                batches_this_epoch = 0
                steps_at_epoch_start = steps
                accum = 0
                step_period = max(int(config.setting('step_period', 1) or 1), 1)
                optimizer.zero_grad()
                for batch in data.iter_batches('train'):
                    from rpipe.structure.system.runtime import check_cancelled
                    check_cancelled(system)
                    batches_this_epoch += 1
                    images, targets = prepare_tensors(batch, module, device)
                    logits = module(images)
                    loss = F.cross_entropy(logits, targets) / step_period
                    loss.backward()
                    accum += 1
                    values = tracker.evaluate('train', 'batch', (images, targets), logits)
                    tracker.append('train', n=int(images.size(0)), values=values)
                    if accum % step_period != 0:
                        continue
                    clip_gradients(module, config)
                    used_lr = _current_lr(optimizer, lr)
                    optimizer.step()
                    optimizer.zero_grad()
                    steps += 1
                    if log_period and steps % log_period == 0:
                        extra = {'epoch': epoch, 'lr': used_lr, 'step': steps}
                        extra.update(_timing())
                        logger.report(tracker, 'train', extra=extra)
                        tracker.flush('train', progress=extra)
                    if budget.unit == UNIT_STEP:
                        extra = _hook_extra(epoch=epoch, lr=used_lr, step=steps)
                        extra.update(_timing())
                        improved = False
                        if due_period(eval_period, steps):
                            if fire_eval(extra):
                                stop = True
                            improved = algo.last_improved
                        if scheduler is not None:
                            scheduler.step()
                        fire_ckpt(extra=extra, improved=improved, is_last=False)
                        if stop:
                            break
                    if steps >= budget.num_steps:
                        stop = True
                        break
                optimizer.zero_grad()
                if batches_this_epoch == 0 and not finishing_restored_epoch:
                    raise ValueError('empty train iterator cannot satisfy a positive training budget')
                if batches_this_epoch and steps == steps_at_epoch_start and not finishing_restored_epoch:
                    raise ValueError('train iterator has fewer batches than step_period; no optimizer update is possible')
                extra = _hook_extra(epoch=epoch, lr=used_lr, step=steps)
                extra.update(_timing())
                finish_train_segment(extra)
                extra = _hook_extra(epoch=epoch, lr=used_lr, step=steps)
                extra.update(_timing())
                if budget.unit == UNIT_EPOCH:
                    improved = False
                    if due_period(eval_period, epoch):
                        if fire_eval(extra):
                            stop = True
                        improved = algo.last_improved
                    last_epoch = budget.num_epochs is not None and epoch >= budget.num_epochs
                    last_steps = steps >= budget.num_steps
                    is_last = last_epoch or last_steps
                    if scheduler is not None:
                        scheduler.step()
                    if is_last:
                        stop = True
                    fire_ckpt(extra=extra, improved=improved, is_last=is_last)
                    if is_last:
                        wrote_last_ckpt = True
                if steps >= budget.num_steps:
                    stop = True
        extra = _hook_extra(epoch=epoch, lr=used_lr, step=steps)
        if eval_period <= 0 and not already_done:
            fire_eval(extra)
            if keep_best and algo.last_improved:
                fire_ckpt(extra=extra, improved=True, is_last=True)
                wrote_last_ckpt = True
        if not already_done and not wrote_last_ckpt:
            fire_ckpt(extra=extra, improved=False, is_last=True)
    except BaseException:
        try:
            tracker.flush_state()
        except Exception as exc:
            if logger is not None:
                try:
                    logger.exception('tracker final flush failed', exc)
                except Exception:
                    pass
        raise
    else:
        tracker.flush_state()

    train_loss = tracker.segment_mean('train').get('Loss', 0.0)
    accuracy = tracker.segment_mean('test').get('Accuracy', 0.0)
    summary: dict[str, Any] = {
        'mode': 'train',
        'steps': steps,
        'epochs': epoch,
        'progress_unit': budget.unit,
        'train_size': data.meta.get('train_size') if getattr(data, 'meta', None) else None,
        'train_loss': train_loss,
        'accuracy': accuracy,
        'best_value': algo._best_test,
        'best_metric': algo._best_metric,
        'resume_count': resume_extra['resume_count'],
        'resume_path': resume_extra.get('resume_path') if restored is not None else None,
        'resume_step': restored.get('step') if restored is not None else None,
    }
    if algo._best_metric == 'Accuracy':
        summary['best_accuracy'] = algo._best_test
    summary['elapsed'] = format_hms(clock.elapsed_seconds())
    summary['elapsed_seconds'] = float(clock.elapsed_seconds())
    return summary


def budget_for(config: AlgorithmConfig) -> ProgressBudget:
    return resolve_budget(config)
