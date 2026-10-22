import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from pathlib import Path

from rpipe.payloads.tracker import AlgorithmTracker


def test_tracker_keeps_jsonl_on_init(tmp_path: Path):
    leftover = tmp_path / 'tracker' / 'scalars.jsonl'
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_text('{"stale": true}\n', encoding='utf-8')
    tracker = AlgorithmTracker(tmp_path)
    assert 'stale' in tracker.jsonl_path.read_text(encoding='utf-8')
    tracker.append('train', n=1, values={'Loss': 1.0})
    tracker.flush('train')
    text = tracker.jsonl_path.read_text(encoding='utf-8')
    assert 'Loss' in text


def test_tracker_weighted_mean_and_segment(tmp_path: Path):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', n=2, values={'Loss': 1.0})
    tracker.append('train', n=2, values={'Loss': 3.0})
    assert tracker.mean('train')['Loss'] == 2.0
    tracker.save('train')
    tracker.reset('train')
    assert tracker.mean('train')['Loss'] == 0.0
    assert tracker.segment_mean('train')['Loss'] == 2.0
    tracker.flush('train')
    assert (tmp_path / 'tracker' / 'tracker_state.json').is_file()


def test_segment_presence_includes_full_metrics_and_survives_checkpoint(tmp_path):
    import torch
    from rpipe.payloads.metrics import MetricBundle

    tracker = AlgorithmTracker(tmp_path, MetricBundle({'train': ['RMSE']}))
    values = tracker.evaluate('train', 'batch', {'target': torch.tensor([0.0])}, {'pred': torch.tensor([2.0])})
    tracker.append('train', values=values)
    assert values == {} and tracker.has_samples('train')
    state = tracker.state_dict()
    restored = AlgorithmTracker(tmp_path / 'restored')
    restored.load_state_dict(state)
    assert restored.has_samples('train')
    tracker.save('train')
    tracker.reset('train')
    assert not tracker.has_samples('train')
    assert tracker.segment_mean('train') == {'RMSE': 2.0}


def test_checkpoint_preserves_accumulator_without_mean_round_trip(tmp_path):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', n=54, values={'Loss': 7.370161863460789e-09 / 54})
    state = tracker.state_dict()
    state['splits']['train']['Loss']['weighted_sum'] = 7.370161863460789e-09
    state['splits']['train']['Loss']['mean'] = 7.370161863460789e-09 / 54
    assert state['splits']['train']['Loss']['mean'] * 54 != state['splits']['train']['Loss']['weighted_sum']
    restored = AlgorithmTracker(tmp_path / 'restored')
    restored.load_state_dict(state)
    assert restored.state_dict()['splits'] == state['splits']


def test_legacy_tracker_without_accumulator_still_loads(tmp_path):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', n=3, values={'Loss': 2.0})
    state = tracker.state_dict()
    state['splits']['train']['Loss'].pop('weighted_sum')
    restored = AlgorithmTracker(tmp_path / 'restored')
    restored.load_state_dict(state)
    assert restored.mean('train')['Loss'] == 2.0


def test_tracker_constructor_preserves_evidence_arriving_before_open(tmp_path, monkeypatch):
    from rpipe.structure import artifacts as kinds

    target = tmp_path / kinds.TRACKER_JSONL
    native = Path.open
    arrivals = []

    def arriving(path, mode='r', *args, **kwargs):
        if path == target and mode in ('w', 'a') and not arrivals:
            arrivals.append(True)
            with native(path, 'wb') as stream:
                stream.write(b'prior error evidence\n')
        return native(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', arriving)
    AlgorithmTracker(tmp_path)
    assert arrivals == [True]
    assert target.read_bytes() == b'prior error evidence\n'


def test_tracker_constructor_still_rejects_directory(tmp_path):
    from rpipe.structure import artifacts as kinds

    target = tmp_path / kinds.TRACKER_JSONL
    target.mkdir(parents=True)
    with pytest.raises(OSError):
        AlgorithmTracker(tmp_path)


def test_tracker_constructor_does_not_reopen_existing_evidence(tmp_path, monkeypatch):
    from rpipe.structure import artifacts as kinds

    target = tmp_path / kinds.TRACKER_JSONL
    target.parent.mkdir(parents=True)
    target.write_bytes(b'existing diagnostic bytes\n')
    native = Path.open

    def no_reopen(path, *args, **kwargs):
        if path == target:
            raise PermissionError('existing evidence must not be reopened by initialization')
        return native(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, 'open', no_reopen)
        AlgorithmTracker(tmp_path)
    assert target.read_bytes() == b'existing diagnostic bytes\n'


def test_tracker_append_initialization_keeps_checkpoint_raw_suffix_and_marker(tmp_path):
    import json
    from rpipe.structure import artifacts as kinds

    path = tmp_path / kinds.TRACKER_JSONL
    path.parent.mkdir(parents=True)
    prefix = b'{"step": 1}\n'
    raw = prefix + b'{"interrupted":'
    path.write_bytes(raw)
    tracker = AlgorithmTracker(tmp_path)
    assert path.read_bytes() == raw
    tracker.begin_run({'jsonl_path': str(path.resolve()), 'jsonl_offset': len(prefix)})
    data = path.read_bytes()
    assert data.startswith(raw + b'\n')
    assert json.loads(data.splitlines()[-1]) == {'event': 'start', 'keep_until': len(prefix)}


@pytest.mark.parametrize('action,phase', [('begin', 'write'), ('append', 'write'), ('append', 'flush')])
@pytest.mark.parametrize('outcome', ['error', 'cancel', 'normal', 'programming'])
def test_tracker_stream_close_preserves_write_or_flush_failure(tmp_path, stream_close_fault, action, phase, outcome):
    import errno
    tracker = AlgorithmTracker(tmp_path)
    prefix = b'{"old":true}\n'
    tracker.jsonl_path.write_bytes(prefix)
    tracker.state_path.write_bytes(b'old tracker state')
    tracker.append('train', n=1, values={'Loss': 1.0})
    primary = KeyboardInterrupt('cancel ledger') if outcome == 'cancel' else None if outcome == 'normal' else OSError(errno.ENOSPC, 'ledger IO')
    secondary = RuntimeError('unexpected close error') if outcome == 'programming' else OSError(errno.EIO, 'close IO')
    expected = secondary if outcome in ('normal', 'programming') else primary
    handles, calls = stream_close_fault(lambda path, mode: path == tracker.jsonl_path and mode in ('a', 'ab+'), primary, secondary, phase=phase)
    with pytest.raises(type(expected)) as caught:
        if action == 'begin':
            tracker.begin_run({'jsonl_path': str(tracker.jsonl_path.resolve()), 'jsonl_offset': len(prefix)})
        else:
            tracker.flush('train')
    assert caught.value is expected
    assert len(handles) == 1 and handles[0].closed and calls.count('close') == 1
    assert tracker.jsonl_path.read_bytes().startswith(prefix)
    assert tracker.state_path.read_bytes() == b'old tracker state'
    assert calls[0] == 'write' and (phase != 'flush' or 'flush' in calls)


@pytest.mark.parametrize('saved', [False, True])
def test_display_mean_empty_segment_keeps_fallback_copy_and_state(tmp_path, saved):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', n=2, values={'second': 2.0, 'first': 0.0})
    if saved:
        tracker.save('train')
    tracker.reset('train')
    before = tracker.state_dict()
    expected = {'second': 2.0 if saved else 0.0, 'first': 0.0}
    displayed = tracker.display_mean('train')
    assert displayed == expected and list(displayed) == ['second', 'first']
    displayed['second'] = 999.0
    assert tracker.display_mean('train') == expected
    assert tracker.display_mean('missing') == {}
    assert tracker.state_dict() == before


def test_display_mean_running_zero_keeps_order_and_skips_inactive_saved_metric(tmp_path):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', values={'inactive': 7.0, 'zero': 8.0})
    tracker.save('train')
    tracker.reset('train')
    tracker.append('train', n=3, values={'zero': 0.0, 'active': 2.0})
    before = tracker.state_dict()
    displayed = tracker.display_mean('train')
    assert displayed == {'zero': 0.0, 'active': 2.0}
    assert list(displayed) == ['zero', 'active']
    assert tracker.state_dict() == before


def test_display_mean_keeps_original_meter_error_instead_of_saved_fallback(tmp_path):
    tracker = AlgorithmTracker(tmp_path)
    failure = ValueError('meter display failed')

    class Meter:
        n = 1

        @property
        def mean(self):
            raise failure

    meter = Meter()
    tracker._meters['train'] = {'broken': meter}
    tracker._last_segment['train'] = {'saved': 4.0}
    with pytest.raises(ValueError) as raised:
        tracker.display_mean('train')
    assert raised.value is failure
    assert tracker._meters['train']['broken'] is meter
    assert tracker._last_segment['train'] == {'saved': 4.0}


@pytest.mark.parametrize('split', [None, 'train', 'empty'])
def test_save_publishes_split_snapshot_in_meter_order(tmp_path, split):
    from types import SimpleNamespace

    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', values={'second': 4.0, 'first': 2.0})
    tracker.append('test', values={'Loss': 3.0})
    calls = []

    def finish(name):
        calls.append(name)
        return {'full': 0.0}

    tracker.metrics = SimpleNamespace(finish_full=finish)
    tracker.save(split)
    names = ['train', 'test'] if split is None else [split]
    assert calls == names
    assert list(tracker._last_segment) == names
    for name in names:
        expected = {'train': ['second', 'first', 'full'], 'test': ['Loss', 'full']}.get(name, ['full'])
        assert list(tracker.segment_mean(name)) == expected
        assert tracker.segment_mean(name)['full'] == 0.0
        assert all(len(meter.history) == 1 for meter in tracker._meters[name].values())
    saved = tracker.segment_mean(names[0])
    saved.clear()
    assert tracker.segment_mean(names[0])


def test_save_meter_error_keeps_old_split_and_completed_prior_split(tmp_path):
    from types import SimpleNamespace

    tracker = AlgorithmTracker(tmp_path)
    tracker.append('test', values={'Loss': 3.0})
    tracker.append('train', values={'first': 2.0})
    error = RuntimeError('save point failed')
    calls = []

    def fail():
        calls.append('fail')
        raise error

    tracker._meters['train']['broken'] = SimpleNamespace(save_point=fail)
    previous = {'old': 9.0}
    tracker._last_segment['train'] = previous
    tracker.metrics = SimpleNamespace(finish_full=lambda name: {})
    with pytest.raises(RuntimeError) as caught:
        tracker.save()
    assert caught.value is error and calls == ['fail']
    assert tracker._last_segment['train'] is previous
    assert tracker.segment_mean('test') == {'Loss': 3.0}
    assert tracker._meters['train']['first'].history == [2.0]
