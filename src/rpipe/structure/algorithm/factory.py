"""Scientific construction and runtime support; no orchestration imports."""
from __future__ import annotations

from typing import Any
from pathlib import Path
from rpipe.structure.api import CancelCheck, ComputationObservation, DataPort, ModelPort, SystemPort, TrackerPort
from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.optim import make_optimizer as _make_optimizer, make_scheduler as _make_scheduler
from rpipe.structure.algorithm.resume import (apply_module_state, resolve_checkpoint_ref, resume_stem, sibling_train_checkpoint, partial_resume_warning)

from rpipe.structure.algorithm.progress import (
    CHECKPOINT_LATEST,
    parse_checkpoint_mode,
    parse_percents,
    resolve_budget,
)

class AlgorithmHook:
    """Contract for algorithm-loop hooks. Methods live on ``Algorithm``.

    Base is no-op / do-not-stop. Concrete modes override the points they need.
    Periodic evaluation receives the computation's read-only cancellation checker.
    """

    def eval_period(self) -> int:
        """How often ``on_eval_period`` runs. ``1`` = every progress unit; ``0`` = once after the loop."""
        raw = self.config.setting('eval_period', 1)  # type: ignore[attr-defined]
        if raw is None:
            return 1
        return int(raw)

    def progress_unit(self) -> str:
        return resolve_budget(self.config).unit  # type: ignore[attr-defined]

    def checkpoint_mode(self) -> str:
        raw = self.config.setting('checkpoint', CHECKPOINT_LATEST)  # type: ignore[attr-defined]
        return parse_checkpoint_mode(raw)

    def checkpoint_period(self) -> int:
        raw = self.config.setting('checkpoint_period', 1)  # type: ignore[attr-defined]
        if raw is None:
            return 1
        return int(raw)

    def checkpoint_percents(self) -> tuple[float, ...]:
        return parse_percents(self.config.setting('checkpoint_percents'))  # type: ignore[attr-defined]

    def save_best(self) -> bool:
        return bool(self.config.setting('save_best', False))  # type: ignore[attr-defined]

    def on_eval_period(
        self,
        tracker: TrackerPort,
        logger: Any,
        data: Any,
        model: Any,
        system: Any,
        extra: dict[str, Any] | None = None,
        *,
        cancel_check: CancelCheck,
    ) -> bool:
        """Return True to stop ``compute()``. Not a Flow phase."""
        cancel_check()
        del tracker, logger, data, model, system, extra
        return False

    def on_checkpoint(
        self,
        tracker: TrackerPort,
        logger: Any,
        data: Any,
        model: Any,
        system: Any,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write weights via ``system.save_checkpoint``. Not a Flow phase."""
        del tracker, logger, data, model, system, extra

class Algorithm(AlgorithmHook):
    """Computation for one mode. Optimizer / scheduler / resume live here."""

    def __init__(self, config: AlgorithmConfig) -> None:
        self.config = config
        self.mode = 'train' if config.mode is None else config.mode

    def make_optimizer(self, module: Any) -> Any:
        return _make_optimizer(module, self.config)

    def make_scheduler(self, optimizer: Any, t_max: int) -> Any:
        return _make_scheduler(optimizer, self.config, t_max)

    def resume(
        self,
        data: Any,
        model: Any,
        system: Any,
        tracker: TrackerPort,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        extra = extra if extra is not None else {}
        extra.setdefault('resume_count', 0)
        del data
        stem = resume_stem(self.config, mode=self.mode)
        extra['resume_stem'] = stem
        if stem is None:
            return None
        target = resolve_checkpoint_ref(stem, system, mode=self.mode)
        extra['resume_path'] = target
        loader = getattr(system, 'load_checkpoint', None)
        payload = loader(target) if callable(loader) else None
        logger = getattr(system, 'logger', None)
        if payload is None and self.mode == 'eval':
            fallback = sibling_train_checkpoint(system, stem=Path(str(target)).stem)
            if fallback is not None and callable(loader):
                payload = loader(str(fallback))
                extra['resume_path'] = str(fallback)
        if payload is None:
            if self.mode == 'eval':
                raise FileNotFoundError(f'eval resume missing checkpoint {stem!r}')
            extra['resume_skipped'] = stem
            return None
        apply_module_state(getattr(model, 'module', None), payload)
        extra['resume_count'] += 1
        extra['resume_payload'] = payload
        if self.mode == 'train':
            if payload.get('training_state') is None or self.config.source == 'transformers_trainer':
                partial_resume_warning(logger)
            tracker_state = payload.get('tracker')
            if tracker_state:
                tracker.load_state_dict(tracker_state)
            logger_state = payload.get('logger')
            if logger is not None and logger_state is not None and hasattr(logger, 'load_state_dict'):
                logger.load_state_dict(logger_state)
        if logger is not None:
            logger.info(
                f"target={target} epoch={payload.get('epoch')} step={payload.get('step')}",
                event='resume',
            )
        return payload

    def compute(self, data: DataPort, model: ModelPort, system: SystemPort, tracker: TrackerPort,
                cancel_check: CancelCheck) -> ComputationObservation:
        raise NotImplementedError

def _table() -> dict[tuple[str, str], type[Algorithm]]:
    from rpipe.structure.algorithm.evaluation import EvalAlgorithm
    from rpipe.structure.algorithm.train import TrainAlgorithm

    return {
        ('train', 'custom_torch'): TrainAlgorithm,
        ('eval', 'custom_torch'): EvalAlgorithm,
    }

class AlgorithmRegistry:
    """Lookup table filled on first build. Extra sources call ``register``."""

    _items: dict[tuple[str, str], type[Algorithm]] | None = None

    @classmethod
    def _store(cls) -> dict[tuple[str, str], type[Algorithm]]:
        if cls._items is None:
            cls._items = dict(_table())
        return cls._items

    @classmethod
    def register(cls, mode: str, source: str, impl: type[Algorithm]) -> None:
        cls._store()[(mode, source)] = impl

    @classmethod
    def get(cls, mode: str, source: str) -> type[Algorithm] | None:
        if source == 'transformers_trainer' and (mode, source) not in cls._store():
            from rpipe.structure.algorithm.transformers_trainer import HfEvalAlgorithm, HfTrainAlgorithm
            cls._store().update({('train', source): HfTrainAlgorithm, ('eval', source): HfEvalAlgorithm})
        return cls._store().get((mode, source))

    @classmethod
    def list(cls) -> list[tuple[str, str]]:
        return sorted(set(cls._store()) | {('train', 'transformers_trainer'), ('eval', 'transformers_trainer')})

def build_algorithm(algorithm_config: AlgorithmConfig) -> Algorithm:
    mode = 'train' if algorithm_config.mode is None else algorithm_config.mode
    source = 'custom_torch' if algorithm_config.source is None else algorithm_config.source
    impl = AlgorithmRegistry.get(mode, source)
    if impl is None:
        raise ValueError(f'unknown algorithm mode/source: {mode}/{source}')
    return impl(algorithm_config)
