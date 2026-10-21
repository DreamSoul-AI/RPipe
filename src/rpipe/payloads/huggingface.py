"""Selected HuggingFace lifecycle; optional dependencies load during preparation."""
from __future__ import annotations

from typing import Any

from rpipe.payloads.native import NativePayload


class HuggingFacePayload(NativePayload):
    def prepare(self, ctx: Any) -> None:
        from rpipe.structure.contracts import AlgorithmConfig

        source = AlgorithmConfig.from_mapping(ctx.config.get('algorithm')).source
        if source != 'transformers_trainer':
            raise ValueError('HuggingFacePayload requires algorithm.source transformers_trainer')
        super().prepare(ctx)
