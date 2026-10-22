"""Atomic curve publication and ownership; source: flow/process/curves.py."""

import json
import os
import stat
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from rpipe.flow.exports import learning_curves_path, write_learning_curves
from rpipe.structure.artifacts import write_json as write_result

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_process,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def curve_case(tmp_path, old):
    import matplotlib.pyplot as plt

    directory = tmp_path / 'runs' / 'one'
    layout = SimpleNamespace(root=directory, result_path=directory / 'result.json', assets_dir=directory / 'assets')
    write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    history = layout.assets_dir / 'tracker' / 'tracker_state.json'
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(json.dumps({'splits': {'test': {'Accuracy': {'history': [50.0]}}}}), encoding='utf-8')
    index = {'study': 'synthetic', 'experiments': [
        {'factors': {}, 'runs': [{'id': 'one', 'seed': 0}]}]}
    dest = learning_curves_path(tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    peer = dest.parent / 'peer.tmp'
    peer.write_bytes(b'peer writer evidence')
    peer_figure = plt.figure(figsize=(1, 1))
    if old:
        peer_figure.savefig(dest)
        dest.chmod(0o640 if os.name != 'nt' else 0o666)
    case = {'root': tmp_path, 'index': index, 'dest': dest,
            'old': dest.read_bytes() if old else None,
            'mode': stat.S_IMODE(dest.stat().st_mode) if old else None,
            'figures': set(plt.get_fignums()), 'peers': {'peer.tmp'},
            'inputs': {path: path.read_bytes() for path in (layout.result_path, history, peer)}}
    try:
        yield case
    finally:
        for number in set(plt.get_fignums()) - case['figures']:
            plt.close(number)
        plt.close(peer_figure)


def _preserved(case, *, retained_staging=False):
    import matplotlib.pyplot as plt

    dest = case['dest']
    assert (dest.read_bytes() if dest.exists() else None) == case['old']
    assert set(plt.get_fignums()) == case['figures']
    assert all(path.read_bytes() == data for path, data in case['inputs'].items())
    names = {path.name for path in dest.parent.glob('*.tmp')}
    if retained_staging:
        assert case['peers'] < names and len(names - case['peers']) == 1
    else:
        assert names == case['peers']
    if case['mode'] is not None:
        assert stat.S_IMODE(dest.stat().st_mode) == case['mode']


@pytest.mark.parametrize('old', [False, True])
@pytest.mark.parametrize('phase', ['axes', 'render', 'partial_save', 'cancel', 'replace', 'create'])
def test_curve_failures_preserve_target_peers_and_release_owned_figure(curve_case, monkeypatch, phase):
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    case = curve_case
    error = KeyboardInterrupt('cancel curve save') if phase == 'cancel' else OSError('curve ' + phase)
    def refuse(*args, **kwargs):
        raise error
    def partial_save(figure, destination, **kwargs):
        if hasattr(destination, 'write'):
            destination.write(b'partial PNG')
        else:
            Path(destination).write_bytes(b'partial PNG')
        raise error
    if phase == 'axes':
        monkeypatch.setattr(Figure, 'subplots', refuse)
    elif phase == 'render':
        monkeypatch.setattr(Axes, 'plot', refuse)
    elif phase in ('partial_save', 'cancel'):
        monkeypatch.setattr(Figure, 'savefig', partial_save)
    elif phase == 'replace':
        monkeypatch.setattr(Path, 'replace', refuse)
    else:
        original_open = Path.open
        def refuse_staging(path, mode='r', *args, **kwargs):
            if path.parent == case['dest'].parent and path.suffix == '.tmp' and mode == 'xb':
                raise error
            return original_open(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', refuse_staging)
    with pytest.raises(type(error)) as caught:
        write_learning_curves(case['root'], case['index'])
    assert caught.value is error
    _preserved(case)


@pytest.mark.parametrize('old', [False, True])
def test_curve_staging_collision_preserves_other_writer(curve_case, monkeypatch):
    case = curve_case
    token = 'c' * 32
    collision = case['dest'].parent / (token + '.tmp')
    collision.write_bytes(b'other writer collision')
    case['inputs'][collision] = collision.read_bytes()
    case['peers'].add(collision.name)
    monkeypatch.setattr(uuid, 'uuid4', lambda: SimpleNamespace(hex=token))
    with pytest.raises(FileExistsError):
        write_learning_curves(case['root'], case['index'])
    _preserved(case)


@pytest.mark.parametrize('old', [False, True])
def test_curve_cleanup_denial_keeps_primary_publish_error(curve_case, monkeypatch):
    case = curve_case
    error = OSError('publish denied')
    original_unlink = Path.unlink
    def refuse_publish(*args, **kwargs):
        raise error
    def refuse_owned_cleanup(path, *args, **kwargs):
        if path.parent == case['dest'].parent and path.suffix == '.tmp' and path.name not in case['peers']:
            raise OSError('cleanup denied')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'replace', refuse_publish)
    monkeypatch.setattr(Path, 'unlink', refuse_owned_cleanup)
    with pytest.raises(OSError) as caught:
        write_learning_curves(case['root'], case['index'])
    assert caught.value is error
    _preserved(case, retained_staging=True)


@pytest.mark.parametrize('old', [False, True])
def test_curve_success_publishes_valid_png_and_closes_only_owned_figure(curve_case, monkeypatch):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    case = curve_case
    saved = []
    original = Figure.savefig
    def save(figure, *args, **kwargs):
        saved.append(figure)
        return original(figure, *args, **kwargs)
    monkeypatch.setattr(Figure, 'savefig', save)
    assert write_learning_curves(case['root'], case['index']) == case['dest']
    assert case['dest'].read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert plt.imread(case['dest']).shape[0] > 0
    assert saved[0].axes[0].lines[0].get_ydata().tolist() == [50.0]
    assert set(plt.get_fignums()) == case['figures']
    assert all(path.read_bytes() == data for path, data in case['inputs'].items())
    assert {path.name for path in case['dest'].parent.glob('*.tmp')} == case['peers']
    if case['mode'] is not None:
        assert stat.S_IMODE(case['dest'].stat().st_mode) == case['mode']


@pytest.mark.skipif(os.name != 'nt', reason='native Windows readonly replacement and cleanup')
@pytest.mark.parametrize('old', [True])
def test_readonly_target_denial_cleans_owned_readonly_staging(curve_case):
    case = curve_case
    case['dest'].chmod(0o444)
    case['mode'] = stat.S_IMODE(case['dest'].stat().st_mode)
    try:
        with pytest.raises(PermissionError):
            write_learning_curves(case['root'], case['index'])
        _preserved(case)
    finally:
        case['dest'].chmod(0o666)


@pytest.mark.parametrize('old', [False, True])
@pytest.mark.parametrize('primary_kind', ['save', 'cancel', None])
def test_curve_stream_close_io_keeps_primary_and_success_close_stays_strict(curve_case, monkeypatch, primary_kind):
    from matplotlib.figure import Figure

    case = curve_case
    primary = (KeyboardInterrupt('save cancelled') if primary_kind == 'cancel'
               else RuntimeError('save failed') if primary_kind == 'save' else None)
    close_error = OSError('stream close IO failed')
    original_open = Path.open
    class Stream:
        def __init__(self, raw):
            self.raw = raw
        def __getattr__(self, name):
            return getattr(self.raw, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            self.raw.close()
            raise close_error
    def open_stream(path, mode='r', *args, **kwargs):
        raw = original_open(path, mode, *args, **kwargs)
        return Stream(raw) if path.parent == case['dest'].parent and path.suffix == '.tmp' and mode == 'xb' else raw
    def fail_save(*args, **kwargs):
        raise primary
    monkeypatch.setattr(Path, 'open', open_stream)
    if primary is not None:
        monkeypatch.setattr(Figure, 'savefig', fail_save)
    expected = primary if primary is not None else close_error
    with pytest.raises(type(expected)) as caught:
        write_learning_curves(case['root'], case['index'])
    assert caught.value is expected
    _preserved(case)
