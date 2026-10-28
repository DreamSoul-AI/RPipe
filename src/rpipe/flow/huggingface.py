"""Selected HuggingFace lifecycle; optional dependencies load during preparation."""
from __future__ import annotations

from typing import Any

from rpipe.flow.native import NativeRun


class HuggingFaceRun(NativeRun):
    def prepare(self, ctx: Any) -> None:
        from rpipe.structure.control import AlgorithmConfig

        source = AlgorithmConfig.from_mapping(ctx.config.get('algorithm')).source
        if source != 'transformers_trainer':
            raise ValueError('HuggingFaceRun requires algorithm.source transformers_trainer')
        super().prepare(ctx)
