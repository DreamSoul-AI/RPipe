"""Runtime System object, factory, and Logger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rpipe.structure.control import SystemConfig
from rpipe.structure.api import SystemPort
from rpipe.structure.system.logger import Logger


class System:
    def __init__(
        self,
        *,
        device: str,
        logger: Logger | None,
        assets_dir: Path,
        meta: dict[str, Any],
    ) -> None:
        self.device = device
        self.logger = logger
        self.assets_dir = assets_dir
        self.meta = meta

    def place_module(self, module: Any) -> Any:
        if module is None:
            return None
        import torch

        return module.to(torch.device(self.device))




    def to_result_snapshot(self) -> dict[str, Any]:
        return {'device': self.device, **self.meta}


def build_system(system_config: SystemConfig, assets_dir: Path | str, *,
                 logger: Logger | None = None,
                 logging_context: dict[str, Any] | None = None) -> SystemPort:
    source = system_config.source if system_config.source is not None else 'native'
    if source != 'native':
        raise ValueError(f'unknown system source: {source!r}')
    if logger is not None and logging_context is not None:
        raise ValueError('provide a logger instance or its construction context, not both')
    assets_dir = Path(assets_dir)
    device = str(system_config.setting('device', 'cpu'))
    deterministic = bool(system_config.setting('deterministic', False))
    system = System(
        device=device,
        logger=None,
        assets_dir=assets_dir,
        meta={
            'ready': True,
            'source': source,
            'deterministic': deterministic,
            'cudnn_benchmark': system_config.setting('cudnn_benchmark'),
            'cudnn_deterministic': system_config.setting('cudnn_deterministic'),
        },
    )
    system.logger = logger if logger is not None else Logger(assets_dir, **(
        logging_context if logging_context is not None else {}))
    return system
