"""Explicit checkpoint selection and admission before scientific state changes."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from rpipe.structure.artifact import checkpoint
from rpipe.structure.api import TrackerPort
from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.system.runtime import validate_rng


def checkpoint_directory(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('checkpoint directories must be explicit absolute paths')
    return path


def partial_resume_warning(logger=None) -> None:
    import warnings
    message = ('checkpoint resume is partial: RNG/data iterator equivalence is not guaranteed '
               'for unsupported data or Trainer runtimes')
    if logger is not None:
        logger.warning(message)
    warnings.warn(message, RuntimeWarning, stacklevel=2)


def resume_reference(config: AlgorithmConfig, *, checkpoint_dir: Path,
                     train_checkpoint_dir: Path | None) -> tuple[Path | None, bool, str | None]:
    explicit = config.setting('resume_from')
    if explicit is not None:
        return checkpoint.checkpoint_path(explicit), False, str(explicit)
    raw = config.setting('resume')
    if raw is False:
        return None, False, None
    mode = config.mode if config.mode is not None else 'train'
    name = ('latest' if mode == 'train' else 'best') if raw is None else raw
    if not isinstance(name, str) or not name or name in {'.', '..'}:
        raise ValueError('resume must be False, a checkpoint name or an absolute .pt reference')
    path = Path(name)
    if path.is_absolute():
        return checkpoint.checkpoint_path(path), False, name
    if path.name != name or '/' in name or '\\' in name:
        raise ValueError('relative checkpoint paths are not supported')
    root = checkpoint_dir if mode == 'train' else train_checkpoint_dir
    if root is None:
        raise ValueError('evaluation requires an explicit training checkpoint directory or resume_from')
    filename = name if path.suffix.lower() == '.pt' else f'{name}.pt'
    return checkpoint.checkpoint_path(root / filename), mode == 'train' and filename == 'latest.pt', name


def load_resume(config: AlgorithmConfig, *, checkpoint_dir: Path,
                train_checkpoint_dir: Path | None, observation: dict[str, Any]) -> dict | None:
    reference, optional, name = resume_reference(config, checkpoint_dir=checkpoint_dir,
                                                train_checkpoint_dir=train_checkpoint_dir)
    observation.update(resume_count=0, resume_stem=name,
                       resume_path=str(reference) if reference is not None else None)
    if reference is None:
        return None
    try:
        payload = checkpoint.load_snapshot(reference)
    except FileNotFoundError:
        if not optional:
            raise
        observation['resume_skipped'] = name
        return None
    observation['resume_count'] = 1
    return payload


def validate_model_state(model, payload: dict) -> None:
    import torch
    state = payload.get('model')
    module = getattr(model, 'module', None)
    if not isinstance(state, dict) or module is None:
        raise ValueError('checkpoint requires model state for the selected model')
    expected = module.state_dict()
    if set(state) != set(expected):
        raise ValueError('checkpoint model state keys differ')
    for name, value in state.items():
        current = expected[name]
        if (type(value) is not torch.Tensor or type(current) is not torch.Tensor
                or value.shape != current.shape or value.dtype != current.dtype):
            raise ValueError(f'checkpoint model state is incompatible: {name}')


def admit_resume(payload: dict | None, *, model, tracker=None, data=None, contract=None,
                 num_steps: int | None = None, step_period: int = 1) -> None:
    if payload is None:
        return
    validate_model_state(model, payload)
    for field in ('step', 'epoch'):
        value = payload.get(field)
        if field == 'step' and value is not None and type(value) is not int:
            raise ValueError('checkpoint optimizer step must be a nonnegative integer')
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise ValueError(f'invalid checkpoint {field}')
    for field in ('best_value', 'best_accuracy'):
        value = payload.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value)):
            raise ValueError(f'invalid checkpoint {field}')
    if tracker is not None and payload.get('tracker') is not None:
        tracker.validate_state_dict(payload['tracker'])
    if contract is None:
        return
    state = payload.get('training_state')
    if state is None:
        return
    if (not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != 1):
        raise ValueError('unsupported checkpoint training_state version')
    if state.get('data') is not None:
        if json.dumps(state.get('contract'), sort_keys=True, allow_nan=False) != json.dumps(contract, sort_keys=True, allow_nan=False):
            raise ValueError('checkpoint training contract differs; keep budget/settings/runtime unchanged')
        validate = getattr(data, 'validate_training_state', None)
        if not callable(validate):
            raise ValueError('data does not support checkpoint iterator admission')
        validate(state['data'], num_steps=num_steps, step_period=step_period)
    validate_rng(state['rng'])
    if type(state.get('stall', 0)) is not int or state.get('stall', 0) < 0:
        raise ValueError('invalid checkpoint early-stop state')
    if type(state.get('stopped', False)) is not bool:
        raise ValueError('invalid checkpoint stop state')
    percents = state.get('already_percent', [])
    if (not isinstance(percents, list) or any(isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v) or not 0 <= v <= 100 for v in percents)):
        raise ValueError('invalid checkpoint save progress')


def apply_resume(payload: dict | None, *, model, tracker=None) -> None:
    if payload is None:
        return
    model.module.load_state_dict(payload['model'])
    if tracker is not None and payload.get('tracker') is not None:
        tracker.load_state_dict(payload['tracker'])


def build_training_snapshot(
    module: Any,
    optimizer: Any,
    scheduler: Any,
    extra: dict[str, Any],
    tracker: TrackerPort | None = None,
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
    return payload
