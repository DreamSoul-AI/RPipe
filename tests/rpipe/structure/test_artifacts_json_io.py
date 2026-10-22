"""Result writer integration: real encoding failure must preserve prior evidence."""

from pathlib import Path

import pytest

from rpipe.structure.artifacts import read_json as load_result, write_json as write_result

pytestmark = [
    pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
    pytest.mark.structure_layer, pytest.mark.module_artifact,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.mark.parametrize('existing', [False, True])
def test_result_encoding_failure_preserves_target_and_allows_next_write(tmp_path: Path, existing):
    """write_result -> JSON -> real UTF-8 IO: preserve evidence, clean only own stage."""
    target = tmp_path / 'result.json'
    old = {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}}
    if existing:
        write_result(target, old)
    old_bytes = target.read_bytes() if existing else None
    peer = tmp_path / 'result.json.other.tmp'
    peer.write_text('another writer', encoding='utf-8')

    with pytest.raises(UnicodeEncodeError):
        write_result(target, {'status': 'failed', 'error': '\ud800'})

    assert set(tmp_path.iterdir()) == ({target, peer} if existing else {peer})
    assert (target.read_bytes() if existing else None) == old_bytes
    assert peer.read_text(encoding='utf-8') == 'another writer'
    if existing:
        assert load_result(target) == old

    new = {'status': 'failed', 'error': 'original failure'}
    assert write_result(target, new) == target
    assert load_result(target) == new
    assert set(tmp_path.iterdir()) == {target, peer}
    assert peer.read_text(encoding='utf-8') == 'another writer'


def test_result_whole_read_keeps_first_failure(tmp_path, reader_exit_case, reader_close_fault):
    case = reader_exit_case
    path, peer = tmp_path / 'input', tmp_path / 'peer'
    payload = b'{"status":"failed","error":"synthetic"}'
    path.write_bytes(payload)
    peer.write_bytes(b'peer unchanged')
    with reader_close_fault(path, case, 'r') as (handles, closes):
        if case.expected is None:
            assert load_result(path) == {'status':'failed','error':'synthetic'}
        else:
            with pytest.raises(type(case.expected)) as caught:
                load_result(path)
            assert caught.value is case.expected
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert path.read_bytes() == payload and peer.read_bytes() == b'peer unchanged'
