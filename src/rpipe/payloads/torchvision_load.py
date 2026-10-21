"""Load torchvision after its native ops are actually registered.

``import torchvision`` can raise ``operator torchvision::nms does not exist``
when ``torchvision/_C`` fails to load. The real ``OSError`` is swallowed inside
torchvision, then ``_meta_registrations`` registers a fake for an op that was
never created. On Windows that load is sometimes transient: the next process
succeeds. Drop the half-imported package and try again.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from types import ModuleType


def import_torchvision(*, attempts: int = 3, pause: float = 0.25) -> ModuleType:
    os.environ.setdefault('TORCHVISION_WARN_WHEN_EXTENSION_LOADING_FAILS', '1')
    tries = max(1, int(attempts))
    for attempt in range(tries):
        try:
            return importlib.import_module('torchvision')
        except RuntimeError as exc:
            if 'torchvision::nms' not in str(exc) or attempt + 1 == tries:
                raise
            _drop_torchvision_modules()
            time.sleep(pause * (attempt + 1))


def _drop_torchvision_modules() -> None:
    for name in list(sys.modules):
        if name == 'torchvision' or name.startswith('torchvision.'):
            sys.modules.pop(name, None)
