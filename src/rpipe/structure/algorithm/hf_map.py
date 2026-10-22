"""Map algorithm extras onto Transformers TrainingArguments (same keys as native)."""

from __future__ import annotations

from typing import Any

from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.algorithm.kwargs import algorithm_blob, filter_args
from rpipe.structure.algorithm.progress import infer_steps_per_epoch, resolve_budget

OPTIM_TO_HF = {
    'sgd': 'sgd',
    'adam': 'adam',
    'adamw': 'adamw_torch',
    'adamw_torch': 'adamw_torch',
    'adamw_hf': 'adamw_hf',
}

SCHED_TO_HF = {
    'constant': 'constant',
    'cosine': 'cosine',
    'cosine_annealing': 'cosine',
    'cosineannealinglr': 'cosine',
    'linear': 'linear',
    'linear_warmup': 'linear',
}


def hf_optim_name(raw: Any) -> str:
    key = str(raw or 'SGD').lower().replace('-', '_')
    if key not in OPTIM_TO_HF:
        raise ValueError(f'unknown optimizer for transformers_trainer: {raw}')
    return OPTIM_TO_HF[key]


def hf_scheduler_name(raw: Any) -> str:
    if raw is None or str(raw).lower() in ('', 'none', 'constant'):
        return 'constant'
    key = str(raw).lower().replace('-', '_')
    if key not in SCHED_TO_HF:
        raise ValueError(f'unknown scheduler for transformers_trainer: {raw}')
    return SCHED_TO_HF[key]


def training_arguments_kwargs(
    config: AlgorithmConfig,
    *,
    output_dir: str,
    data: Any | None = None,
    resume_from_checkpoint: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Same algorithm keys as native; values HF Trainer understands."""
    budget = resolve_budget(config, steps_per_epoch=infer_steps_per_epoch(data) if data else None)
    kwargs: dict[str, Any] = {
        'output_dir': output_dir,
        'optim': hf_optim_name(config.setting('optimizer', 'SGD')),
        'learning_rate': float(config.setting('lr', 0.1)),
        'lr_scheduler_type': hf_scheduler_name(config.setting('scheduler')),
        'report_to': 'none',
        'save_strategy': 'no',
        'eval_strategy': 'no',
        'logging_strategy': 'epoch',
        'remove_unused_columns': False,
        'dataloader_pin_memory': False,
        'skip_memory_metrics': True,
        'disable_tqdm': True,
        'use_cpu': True,
    }
    if budget.num_epochs is not None and budget.steps_from_epochs:
        kwargs['num_train_epochs'] = float(budget.num_epochs)
    else:
        kwargs['max_steps'] = int(budget.num_steps)
    meta = getattr(data, 'meta', None) if data is not None else None
    if isinstance(meta, dict) and meta.get('batch_size'):
        kwargs['per_device_train_batch_size'] = int(meta['batch_size'])
    raw_clip = config.setting('max_grad_norm')
    kwargs['max_grad_norm'] = 0.0 if raw_clip is None else float(raw_clip)
    if seed is not None:
        kwargs['seed'] = int(seed)
        kwargs['data_seed'] = int(seed)
    period = int(config.setting('step_period', 1) or 1)
    if period > 1:
        kwargs['gradient_accumulation_steps'] = period
    blob = algorithm_blob(config)
    for key in ('weight_decay', 'warmup_ratio', 'warmup_steps', 'adam_beta1',
                'adam_beta2', 'adam_epsilon', 'max_grad_norm', 'momentum'):
        value = blob.get(key)
        if key == 'adam_epsilon':
            value = blob.get('eps', value)
        if value is not None:
            kwargs[key] = value
    if resume_from_checkpoint:
        kwargs['resume_from_checkpoint'] = resume_from_checkpoint
    try:
        from transformers import TrainingArguments
    except ImportError:
        return kwargs
    overlay = filter_args(getattr(TrainingArguments, '__init__', TrainingArguments), blob)
    kwargs.update(overlay)
    return kwargs
