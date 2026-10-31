"""Valid destination basenames must not overflow private staging components."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import threading

import pytest

from rpipe.structure.artifact import io as _atomic, io as shared

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('existing', [False, True])
def test_atomic_text_accepts_valid_long_basename(tmp_path, existing):
    root = Path('\\\\?\\' + str(tmp_path.resolve())) if os.name == 'nt' else tmp_path
    target = root / ('x' * 245)
    if existing:
        target.write_bytes(b'old')
    assert _atomic.atomic_write_text(target, 'complete') == target
    assert target.read_bytes() == b'complete'
    assert set(root.iterdir()) == {target}






def test_two_preparers_publish_one_complete_resource(tmp_path):
    ready = threading.Barrier(2)
    built = []
    def build(staging):
        built.append(staging)
        (staging / 'data').write_bytes(b'complete')
    def prepare():
        ready.wait(timeout=5)
        return shared.prepared_resource(tmp_path, {'revision': 'one'}, ['data'], build)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(prepare) for _ in range(2)]
        values = [future.result(timeout=10) for future in futures]
    assert len(built) == 1
    assert values[0] == values[1]
    assert (values[0][0] / 'data').read_bytes() == b'complete'
    assert not list(tmp_path.glob('*/.building-*'))
