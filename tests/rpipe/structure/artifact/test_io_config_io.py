from pathlib import Path
import pytest
from rpipe.structure.artifact.io import load_config, write_config
pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,
            pytest.mark.structure_layer,pytest.mark.module_artifact,
            pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]


def test_config_whole_read_keeps_first_failure(tmp_path, reader_exit_case, reader_close_fault):
    case = reader_exit_case
    path, peer = tmp_path / 'input', tmp_path / 'peer'
    payload = b'algorithm: {}\n'
    path.write_bytes(payload)
    peer.write_bytes(b'peer unchanged')
    with reader_close_fault(path, case, 'r') as (handles, closes):
        if case.expected is None:
            assert load_config(path) == {'algorithm': {}}
        else:
            with pytest.raises(type(case.expected)) as caught:
                load_config(path)
            assert caught.value is case.expected
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert path.read_bytes() == payload and peer.read_bytes() == b'peer unchanged'


def test_config_real_utf8_decode_error_survives_close_io(tmp_path, reader_close_fault):
    from types import SimpleNamespace
    path = tmp_path / 'config.yaml'
    path.write_bytes(b'\xff')
    case = SimpleNamespace(primary=None, secondary=OSError('secondary close IO'))
    with reader_close_fault(path, case, 'r') as (handles, closes):
        with pytest.raises(UnicodeDecodeError) as caught:
            load_config(path)
        assert caught.value.object == b'\xff'
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert path.read_bytes() == b'\xff'


def test_config_roundtrip_preserves_slug(tmp_path):
    target = tmp_path / 'configuration.yaml'
    expected = {'id': 'train-size_1.0', 'algorithm': {'kwargs': {'name': 'µ'}}}
    assert write_config(target, expected) == target
    assert load_config(target) == expected
