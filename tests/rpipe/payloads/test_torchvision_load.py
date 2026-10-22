import importlib
import sys

import pytest

from rpipe.payloads.torchvision_load import import_torchvision

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_system,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def test_import_torchvision_retries_after_missing_nms_op(monkeypatch):
    calls = {'n': 0}
    real = importlib.import_module

    def fake(name, package=None):
        if name == 'torchvision':
            calls['n'] += 1
            if calls['n'] == 1:
                sys.modules['torchvision.partial'] = object()
                raise RuntimeError('operator torchvision::nms does not exist')
            return sys.modules.setdefault('torchvision', object())
        return real(name, package)

    monkeypatch.setattr('rpipe.payloads.torchvision_load.importlib.import_module', fake)
    monkeypatch.setattr('rpipe.payloads.torchvision_load.time.sleep', lambda _seconds: None)
    module = import_torchvision()
    assert calls['n'] == 2
    assert module is sys.modules['torchvision']
    assert 'torchvision.partial' not in sys.modules


def test_import_torchvision_does_not_retry_other_runtime_errors(monkeypatch):
    def fake(name, package=None):
        raise RuntimeError('unrelated')

    monkeypatch.setattr('rpipe.payloads.torchvision_load.importlib.import_module', fake)
    with pytest.raises(RuntimeError, match='unrelated'):
        import_torchvision()


@pytest.mark.parametrize('attempts', [-2, 0, 1, 2, 3])
def test_retry_exhaustion_preserves_original_error_and_budget(monkeypatch, attempts):
    from rpipe.payloads import torchvision_load

    calls, sleeps, drops = [], [], []
    error = RuntimeError('operator torchvision::nms does not exist')

    def fail(name):
        calls.append(name)
        raise error

    monkeypatch.setattr(torchvision_load.importlib, 'import_module', fail)
    monkeypatch.setattr(torchvision_load.time, 'sleep', sleeps.append)
    monkeypatch.setattr(torchvision_load, '_drop_torchvision_modules', lambda: drops.append(True))
    with pytest.raises(RuntimeError) as caught:
        import_torchvision(attempts=attempts, pause=0.5)
    tries = max(1, attempts)
    assert caught.value is error
    assert calls == ['torchvision'] * tries
    assert sleeps == [0.5 * i for i in range(1, tries)]
    assert len(drops) == tries - 1
