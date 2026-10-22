"""algorithm.source: transformers_trainer — §6.11 keys, real HuggingFace Trainer loop."""

from __future__ import annotations

from typing import Any

import torch.nn as nn
import torch.nn.functional as F

from rpipe.structure.algorithm.batch import maybe_flatten_images
from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.algorithm.hf_map import training_arguments_kwargs
from rpipe.structure.algorithm.progress import (
    UNIT_EPOCH,
    checkpoint_names,
    crossed_percents,
    due_period,
    infer_steps_per_epoch,
    resolve_budget,
)
from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.algorithm.train import (
    TrainAlgorithm,
    bind_step_train_loader,
    _build_payload,
    _current_lr,
    _hook_extra,
)


def _require_transformers() -> Any:
    try:
        import transformers
    except ImportError as exc:
        raise ImportError(
            'algorithm.source transformers_trainer requires transformers and accelerate '
            '(install the `hf` optional extra)'
        ) from exc
    return transformers


def build_training_arguments(
    config: AlgorithmConfig,
    *,
    output_dir: str,
    data: Any | None = None,
    resume_from_checkpoint: str | None = None,
    seed: int | None = None,
) -> Any:
    transformers = _require_transformers()
    kwargs = training_arguments_kwargs(
        config,
        output_dir=output_dir,
        data=data,
        resume_from_checkpoint=resume_from_checkpoint,
        seed=seed,
    )
    return transformers.TrainingArguments(**kwargs)


class _EpochScheduler:
    """Trainer steps every optimizer step; native epoch cosine steps once per epoch."""

    def __init__(self, inner: Any, steps_per_epoch: int) -> None:
        self.inner = inner
        self.steps_per_epoch = max(int(steps_per_epoch), 1)
        self._calls = 0

    def step(self, *args: Any, **kwargs: Any) -> Any:
        self._calls += 1
        if self._calls % self.steps_per_epoch == 0:
            return self.inner.step(*args, **kwargs)
        return None

    def state_dict(self) -> Any:
        return self.inner.state_dict()

    def load_state_dict(self, state: Any) -> None:
        self.inner.load_state_dict(state)

    def get_last_lr(self) -> Any:
        return self.inner.get_last_lr()


class _NativeBatchLoader:
    """Reuse Data's torch DataLoader (same shuffle Generator); yield dict batches for Trainer."""

    def __init__(self, loader: Any) -> None:
        self.loader = loader
        self.dataset = getattr(loader, 'dataset', None)
        self.batch_size = getattr(loader, 'batch_size', None)

    def __len__(self) -> int:
        return len(self.loader)

    def __iter__(self):
        for images, labels in self.loader:
            yield {'pixel_values': images, 'labels': labels}


class _DictVisionDataset:
    def __init__(self, base: Any) -> None:
        self.base = base

    def __len__(self) -> int:
        return len(self.base)

    def __getitem__(self, index: int) -> dict[str, Any]:
        import torch

        image, label = self.base[index]
        if not torch.is_tensor(label):
            label = torch.tensor(label)
        return {'pixel_values': image, 'labels': label}


class _ClassificationWrap(nn.Module):
    def __init__(self, inner: nn.Module) -> None:
        super().__init__()
        self.inner = inner

    def forward(self, pixel_values, labels=None, **kwargs):  # noqa: ANN001
        del kwargs
        images = maybe_flatten_images(pixel_values, self.inner)
        logits = self.inner(images)
        loss = F.cross_entropy(logits, labels) if labels is not None else None
        return {'loss': loss, 'logits': logits}


class HfTrainAlgorithm(TrainAlgorithm):
    def run(self, data: Any, model: Any, system: Any, tracker: AlgorithmTracker) -> dict[str, Any]:
        if (
            getattr(data, 'source', None) == 'stub'
            or getattr(model, 'module', None) is None
            or not callable(getattr(data, 'iter_batches', None))
        ):
            return super().run(data, model, system, tracker)
        return _run_hf_trainer(self, data, model, system, tracker)


class HfEvalAlgorithm(TrainAlgorithm):
    def run(self, data: Any, model: Any, system: Any, tracker: AlgorithmTracker) -> dict[str, Any]:
        from rpipe.structure.algorithm.evaluation import EvalAlgorithm

        out = EvalAlgorithm(self.config).run(data, model, system, tracker)
        out['source'] = 'transformers_trainer'
        return out


def _dataset_from_loader(data: Any, split: str) -> Any | None:
    loaders = getattr(data, '_loaders', None) or {}
    loader = loaders.get(split)
    if loader is None:
        return None
    dataset = getattr(loader, 'dataset', None)
    if dataset is None:
        return None
    return _DictVisionDataset(dataset)


def _constant_scheduler(optimizer: Any, num_training_steps: int) -> Any:
    transformers = _require_transformers()
    return transformers.get_scheduler(
        name='constant',
        optimizer=optimizer,
        num_warmup_steps=0,
        num_training_steps=max(int(num_training_steps), 1),
    )


def _run_hf_trainer(
    algo: HfTrainAlgorithm,
    data: Any,
    model: Any,
    system: Any,
    tracker: AlgorithmTracker,
) -> dict[str, Any]:
    transformers = _require_transformers()
    config = algo.config
    logger = getattr(system, 'logger', None)
    module = system.place_module(model.module) if getattr(system, 'place_module', None) else model.module
    model.module = module
    budget = resolve_budget(config, steps_per_epoch=infer_steps_per_epoch(data))
    lr = float(config.setting('lr', 0.1))
    eval_period = algo.eval_period()
    ckpt_period = algo.checkpoint_period()
    ckpt_mode = algo.checkpoint_mode()
    percents = algo.checkpoint_percents()
    keep_best = algo.save_best()
    already_percent: set[float] = set()
    resume_extra: dict[str, Any] = {}
    restored = algo.resume(data, model, system, tracker, resume_extra)
    optimizer = algo.make_optimizer(module)
    inner_sched = algo.make_scheduler(optimizer, budget.scheduler_t_max())
    spe = infer_steps_per_epoch(data) or 1
    scheduler: Any
    if inner_sched is None:
        scheduler = _constant_scheduler(optimizer, budget.num_steps)
    elif budget.unit == UNIT_EPOCH:
        scheduler = _EpochScheduler(inner_sched, spe)
    else:
        scheduler = inner_sched
    if restored:
        opt_state = restored.get('optimizer')
        if opt_state is not None:
            optimizer.load_state_dict(opt_state)
        sched_state = restored.get('scheduler')
        if inner_sched is not None and sched_state is not None:
            inner_sched.load_state_dict(sched_state)
        if restored.get('best_value') is not None:
            algo._best_test = restored['best_value']
        elif restored.get('best_accuracy') is not None:
            algo._best_test = restored['best_accuracy']
    epoch = int(restored.get('epoch') or 0) if restored else 0
    steps = int(restored.get('step') or 0) if restored else 0
    bind_step_train_loader(data, config, step=steps, budget=budget)
    seed = None
    meta = getattr(data, 'meta', None)
    if isinstance(meta, dict) and meta.get('seed') is not None:
        seed = int(meta['seed'])

    def fire_eval(extra: dict[str, Any]) -> bool:
        if tracker.progress is not None:
            extra['progress'] = tracker.progress
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
        extra['progress'] = tracker.progress
        extra['best_accuracy'] = algo._best_test
        extra['checkpoint_names'] = names
        extra['payload'] = _build_payload(module, optimizer, inner_sched, extra, tracker, logger)
        algo.on_checkpoint(tracker, logger, data, model, system, extra)
        if ckpt_mode == 'percent':
            for hit in crossed_percents(current, budget.total, percents, already_percent):
                already_percent.add(hit)

    already_done = steps >= budget.num_steps
    wrote_last_ckpt = False
    if already_done:
        tracker.begin_run((restored or {}).get('tracker'))
        extra = _hook_extra(epoch=epoch, lr=_current_lr(optimizer, lr), step=steps)
        extra['best_accuracy'] = algo._best_test
        fire_eval(extra)
        return _summary(algo, data, tracker, budget, steps, epoch)

    native_loader = (getattr(data, '_loaders', None) or {}).get('train')
    train_ds = _dataset_from_loader(data, 'train')
    if train_ds is None and native_loader is None:
        return TrainAlgorithm.run(algo, data, model, system, tracker)

    tracker.begin_run((restored or {}).get('tracker'))
    recorded = tracker.progress or {}
    step_origin = recorded.get('step', steps)
    epoch_origin = recorded.get('epoch', epoch)

    def observe(inputs: dict[str, Any], outputs: Any) -> None:
        labels = inputs.get('labels')
        logits = outputs.get('logits') if isinstance(outputs, dict) else None
        pixels = inputs.get('pixel_values')
        if labels is None or logits is None:
            return
        values = tracker.evaluate('train', 'batch', (pixels, labels), logits)
        tracker.append('train', n=int(labels.size(0)), values=values)

    class _Trainer(transformers.Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):  # noqa: ANN001
            from rpipe.structure.system.runtime import check_cancelled
            check_cancelled(system)
            outputs = model(**inputs)
            loss = outputs['loss'] if isinstance(outputs, dict) else outputs.loss
            observe(inputs, outputs)
            return (loss, outputs) if return_outputs else loss

        def get_train_dataloader(self):
            if native_loader is not None:
                return _NativeBatchLoader(native_loader)
            return super().get_train_dataloader()

    class _EpochHook(transformers.TrainerCallback):
        def on_step_end(self, args, state, control, **kwargs):  # noqa: ANN001
            del args, kwargs
            nonlocal epoch, steps, wrote_last_ckpt
            import math
            from rpipe.structure.system.runtime import check_cancelled
            check_cancelled(system)
            if budget.unit == UNIT_EPOCH:
                return control
            epoch = max(math.ceil(float(state.epoch or 0)), 1)
            steps = int(state.global_step)
            extra = _hook_extra(epoch=epoch, lr=_current_lr(optimizer, lr), step=steps)
            progress = {'step': step_origin + steps, 'epoch': epoch_origin + float(state.epoch or 0)}
            improved = False
            if due_period(eval_period, steps):
                if tracker.has_samples('train'):
                    if logger is not None:
                        logger.report(tracker, 'train', extra=extra)
                    tracker.flush('train', progress=progress)
                if fire_eval(extra):
                    control.should_training_stop = True
                improved = algo.last_improved
            is_last = steps >= budget.num_steps or control.should_training_stop
            fire_ckpt(extra=extra, improved=improved, is_last=is_last)
            wrote_last_ckpt = is_last
            return control

        def on_epoch_end(self, args, state, control, **kwargs):  # noqa: ANN001
            del args, kwargs
            nonlocal epoch, steps, wrote_last_ckpt
            epoch = int(round(float(state.epoch or 0)))
            steps = int(state.global_step)
            extra = _hook_extra(epoch=epoch, lr=_current_lr(optimizer, lr), step=steps)
            if tracker.has_samples('train'):
                if logger is not None:
                    logger.report(tracker, 'train', extra=extra)
                tracker.flush('train', progress={
                    'step': step_origin + steps, 'epoch': epoch_origin + float(state.epoch or 0),
                })
                tracker.save('train')
                tracker.reset('train')
            tracker.flush_state()
            if budget.unit == UNIT_EPOCH:
                improved = False
                if due_period(eval_period, epoch):
                    if fire_eval(extra):
                        control.should_training_stop = True
                    improved = algo.last_improved
                last_epoch = budget.num_epochs is not None and epoch >= budget.num_epochs
                fire_ckpt(extra=extra, improved=improved, is_last=last_epoch)
                if last_epoch:
                    wrote_last_ckpt = True
            return control

        def on_train_end(self, args, state, control, **kwargs):  # noqa: ANN001
            del args, kwargs
            nonlocal epoch, steps
            epoch = int(round(float(state.epoch or epoch)))
            steps = int(state.global_step)
            extra = _hook_extra(epoch=epoch, lr=_current_lr(optimizer, lr), step=steps)
            extra['best_accuracy'] = algo._best_test
            if eval_period <= 0:
                fire_eval(extra)
            if not wrote_last_ckpt:
                fire_ckpt(extra=extra, improved=False, is_last=True)
            tracker.flush_state()
            return control

    output_dir = str(system.checkpoint_dir() / 'hf')
    args = build_training_arguments(config, output_dir=output_dir, data=data, seed=seed)
    trainer = _Trainer(
        model=_ClassificationWrap(module),
        args=args,
        train_dataset=train_ds if train_ds is not None else getattr(native_loader, 'dataset', []),
        data_collator=transformers.default_data_collator,
        optimizers=(optimizer, scheduler),
        callbacks=[_EpochHook()],
    )
    trainer.train()
    return _summary(algo, data, tracker, budget, steps, epoch)


def _summary(
    algo: HfTrainAlgorithm,
    data: Any,
    tracker: AlgorithmTracker,
    budget: Any,
    steps: int,
    epoch: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        'mode': 'train',
        'source': 'transformers_trainer',
        'steps': steps,
        'epochs': epoch,
        'progress_unit': budget.unit,
        'train_size': data.meta.get('train_size') if getattr(data, 'meta', None) else None,
        'train_loss': tracker.segment_mean('train').get('Loss', 0.0),
        'accuracy': tracker.segment_mean('test').get('Accuracy', 0.0),
        'best_value': algo._best_test,
        'best_metric': getattr(algo, '_best_metric', 'Accuracy'),
    }
    if out['best_metric'] == 'Accuracy':
        out['best_accuracy'] = algo._best_test
    return out
