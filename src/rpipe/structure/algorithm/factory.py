"""Explicit algorithm registry and construction; implementations depend on base."""
from __future__ import annotations

from pathlib import Path
from rpipe.structure.control import AlgorithmConfig, validate_training_budget
from rpipe.structure.algorithm import base

def _table() -> dict[tuple[str, str], type[base.Algorithm]]:
    from rpipe.structure.algorithm.evaluation import EvalAlgorithm
    from rpipe.structure.algorithm.train import TrainAlgorithm

    return {
        ('train', 'custom_torch'): TrainAlgorithm,
        ('eval', 'custom_torch'): EvalAlgorithm,
    }

class AlgorithmRegistry:
    """Lookup table filled on first build. Extra sources call ``register``."""

    _items: dict[tuple[str, str], type[base.Algorithm]] | None = None

    @classmethod
    def _store(cls) -> dict[tuple[str, str], type[base.Algorithm]]:
        if cls._items is None:
            cls._items = dict(_table())
        return cls._items

    @classmethod
    def register(cls, mode: str, source: str, impl: type[base.Algorithm]) -> None:
        cls._store()[(mode, source)] = impl

    @classmethod
    def get(cls, mode: str, source: str) -> type[base.Algorithm] | None:
        if source == 'transformers_trainer' and (mode, source) not in cls._store():
            from rpipe.structure.algorithm.transformers_trainer import HfEvalAlgorithm, HfTrainAlgorithm
            cls._store().update({('train', source): HfTrainAlgorithm, ('eval', source): HfEvalAlgorithm})
        return cls._store().get((mode, source))

    @classmethod
    def list(cls) -> list[tuple[str, str]]:
        return sorted(set(cls._store()) | {('train', 'transformers_trainer'), ('eval', 'transformers_trainer')})

def build_algorithm(algorithm_config: AlgorithmConfig, *, checkpoint_dir: Path,
                    train_checkpoint_dir: Path | None = None) -> base.Algorithm:
    validate_training_budget(algorithm_config)
    mode = 'train' if algorithm_config.mode is None else algorithm_config.mode
    source = 'custom_torch' if algorithm_config.source is None else algorithm_config.source
    impl = AlgorithmRegistry.get(mode, source)
    if impl is None:
        raise ValueError(f'unknown algorithm mode/source: {mode}/{source}')
    return impl(algorithm_config, checkpoint_dir=checkpoint_dir, train_checkpoint_dir=train_checkpoint_dir)
