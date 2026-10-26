"""Controlled archive validation only; never merged into the implementation branch."""
import os
import time
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]

def test_completed_peer():
    assert 2 + 2 == 4

def test_controlled_failure_or_timeout():
    if os.environ['RPIPE_ARCHIVE_PROBE_MODE'] == 'timeout':
        time.sleep(30)
    pytest.fail('controlled CI archive failure')
