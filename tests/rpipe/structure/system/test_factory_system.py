import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_system,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from pathlib import Path

from rpipe.structure.control import SystemConfig
from rpipe.structure.system.factory import build_system


@pytest.mark.parametrize('source', ['', 'unsupported', 'custom_torch'])
def test_unknown_system_source_rejects_before_construction_or_output(tmp_path, monkeypatch, source):
    import importlib
    module = importlib.import_module('rpipe.structure.system.factory')
    def unexpected(*args, **kwargs):
        pytest.fail('rejected system source constructed resources')
    monkeypatch.setattr(module, 'System', unexpected)
    monkeypatch.setattr(module, 'Logger', unexpected)
    with pytest.raises(ValueError, match='unknown system source'):
        build_system(SystemConfig.from_mapping({'source': source}), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_system_object_precedes_its_logger_and_receives_explicit_context(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module('rpipe.structure.system.factory')
    events, log = [], object()
    class System:
        def __init__(self, *, logger, **kwargs):
            assert logger is None
            self.__dict__.update(kwargs)
            events.append('system')
    def logger(root, **context):
        assert root == tmp_path
        assert context == {'request_id': 'fixture'}
        events.append('logger')
        return log
    monkeypatch.setattr(module, 'System', System)
    monkeypatch.setattr(module, 'Logger', logger)
    system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path,
                          logging_context={'request_id': 'fixture'})
    assert events == ['system', 'logger']
    assert system.logger is log


def test_system_does_not_own_snapshot_storage(tmp_path):
    system = build_system(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    assert not any(hasattr(system, name) for name in ('checkpoint_dir', 'save_checkpoint', 'load_checkpoint', 'checkpoint_pickle_module'))
