"""Only current JSONL trajectories produce curves; state files are not fallbacks."""
import json

import pytest

from rpipe.structure.artifact import io, readout

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_current_curve_preserves_actual_coordinates_and_last_duplicate(saved_run):
    ref = saved_run(history=())
    path = ref.assets_dir / io.TRACKER_JSONL
    rows = [{'split': 'test', 'name': 'Accuracy', 'mean': value, 'optimizer_step': step}
            for step, value in [(1, 10), (3, 20), (3, 25)]]
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    series = readout.load_curve_series(ref)['test']['Accuracy']
    assert series == {'unit': 'step', 'x': [1, 3], 'values': [10, 25]}


def test_tracker_state_history_never_fills_missing_formal_jsonl(saved_run):
    ref = saved_run()
    (ref.assets_dir / io.TRACKER_JSONL).unlink()
    io.write_json(ref.assets_dir / io.TRACKER_STATE,
                  {'splits': {'test': {'Accuracy': {'history': [99.0]}}}})
    assert readout.load_curve_series(ref) == {}


@pytest.mark.parametrize('raw', [b'\xff\x80\n', b'{\n', b'[]\n', b'{}\n'])
def test_bad_jsonl_row_does_not_erase_valid_peer_or_write_input(saved_run, raw):
    ref = saved_run(history=())
    path = ref.assets_dir / io.TRACKER_JSONL
    row = json.dumps({'split': 'test', 'name': 'Accuracy', 'mean': 75.0, 'optimizer_step': 1})
    path.write_bytes(raw + (row + '\n').encode())
    before = path.read_bytes()
    assert readout.load_curve_series(ref)['test']['Accuracy']['values'] == [75.0]
    assert path.read_bytes() == before


def test_saved_rollback_boundary_keeps_only_the_effective_trajectory(saved_run):
    ref = saved_run(history=())
    path = ref.assets_dir / io.TRACKER_JSONL
    first = (json.dumps({'split': 'train', 'name': 'Loss', 'mean': 5, 'optimizer_step': 1}) + '\n').encode()
    abandoned = (json.dumps({'split': 'train', 'name': 'Loss', 'mean': 100, 'optimizer_step': 2}) + '\n').encode()
    marker = (json.dumps({'event': 'start', 'keep_until': len(first)}) + '\n').encode()
    final = (json.dumps({'split': 'train', 'name': 'Loss', 'mean': 3, 'optimizer_step': 2}) + '\n').encode()
    path.write_bytes(first + abandoned + marker + final)
    assert readout.load_curve_series(ref)['train']['Loss']['values'] == [5, 3]


def test_curve_aggregation_uses_real_points_and_sample_ddof_one():
    first = {'unit': 'step', 'x': [1, 3], 'values': [1.0, 4.0]}
    second = {'unit': 'step', 'x': [1, 2], 'values': [3.0, 9.0]}
    result = readout.summarize_curve_series([first, second])
    assert result['x'] == [1, 2, 3] and result['n_at_point'] == [2, 1, 1]
    assert result['mean'] == [2.0, 9.0, 4.0]
    assert result['std'][0] == pytest.approx(2 ** .5)


def test_io_failure_has_no_guessed_state_fallback_and_programming_error_propagates(saved_run, monkeypatch):
    ref = saved_run()
    path = ref.assets_dir / io.TRACKER_JSONL
    original = type(path).open
    def denied(current, *args, **kwargs):
        if current == path:
            raise PermissionError('denied')
        return original(current, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(type(path), 'open', denied)
        assert readout.load_curve_series(ref) == {}
    def broken(current, *args, **kwargs):
        if current == path:
            raise RuntimeError('reader implementation broke')
        return original(current, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(type(path), 'open', broken)
        with pytest.raises(RuntimeError):
            readout.load_curve_series(ref)
