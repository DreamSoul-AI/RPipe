"""Build the pinned historical source using RPipe registries."""
from __future__ import annotations

from pathlib import Path
from .api import (
    COMMIT, SOURCE, HORIZON, DATA_NAMES, MODEL_NAMES, STUDY, REPO,
    Archive, Observation, _observed_dicts, bootstrap, load_archive,
)
from .data import register as register_data
from .model import register as register_model


def install(data_root: Path | str, seed: int = 0, sampler_steps: int = HORIZON, *, archive_root: Path | str) -> Archive:
    """Register the immutable archive as source ``historical_4ccb28d``.

    Builders accept each Run's seed; the argument is a fallback for manual
    callers. The sampler keeps the full 80k budget even in a prefix probe.
    Exact resume remains refused: this historical data adapter has not been
    validated against RPipe's native RNG/cursor restoration contract.
    """
    bootstrap()
    archive = load_archive(archive_root)
    data_root = Path(data_root).resolve()
    if sampler_steps != HORIZON:
        raise ValueError('historical sampler horizon must remain 80000')
    register_data(archive, data_root, seed=seed, sampler_steps=sampler_steps)
    register_model(archive, seed=seed)
    return archive
