"""Read saved normalization statistics without preparing or constructing data."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml
from rpipe.structure.artifact.io import _read_text

def load_stats(path: Path | str, *, required: bool = False) -> dict[str, Any] | None:
    file = Path(path)
    if not file.is_file():
        if required:
            raise FileNotFoundError('declared normalization statistics are missing')
        return None
    body = yaml.safe_load(_read_text(file)) or {}
    if (not isinstance(body, dict) or any(not isinstance(body.get(key), (list, tuple))
                                        or not body[key] for key in ('mean', 'std'))):
        if required:
            raise ValueError('declared statistics require nonempty mean and std arrays')
        return None
    return body
