"""Run writer admission and the last admitted execution identity."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import uuid

from rpipe.structure.artifact._atomic import _read_text, atomic_write_text
from rpipe.structure.artifact.shared import cache_lock

RUN_ATTEMPT_ENV = 'RPIPE_RUN_ATTEMPT_TOKEN'
RUN_BUSY_EXIT = 75


class RunLockedError(TimeoutError):
    """Another execution already owns this Run."""


@contextmanager
def run_lock(root: Path | str):
    with ExitStack() as stack:
        try:
            stack.enter_context(cache_lock(root, timeout=0, reentrant=False))
        except TimeoutError as exc:
            raise RunLockedError(f'Run already executing: {root}') from exc
        yield


def start_execution(root: Path | str, token: str | None = None) -> str:
    token = token or uuid.uuid4().hex
    atomic_write_text(Path(root) / 'execution.json', json.dumps({
        'attempt_token': token, 'pid': os.getpid(),
    }, ensure_ascii=True) + '\n')
    return token


def owns_execution(root: Path | str, token: str) -> bool:
    try:
        body = json.loads(_read_text(Path(root) / 'execution.json'))
        return isinstance(body, dict) and body.get('attempt_token') == token
    except (OSError, ValueError, RecursionError):
        return False
