"""Complete explicit snapshots are committed atomically and never guess old formats."""
import pickle
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_snapshot_roundtrip_keeps_tensor_dtypes_and_structured_extra_state(tmp_path):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
    path = tmp_path / 'checkpoints/latest.pt'
    state = {'model': {'integer': torch.tensor([2**53 + 1], dtype=torch.int64),
                       'complex': torch.tensor([1 + 2j]),
                       '_extra_state': {'names': ['研究', 'fixture'], 'count': 0, 'flag': False}},
             'optimizer': {'state': {0: {'step': torch.tensor(3.)}}},
             'scheduler': None, 'tracker': {'progress': {'step': 3, 'epoch': .5}},
             'training_state': {'rng': {'numpy': ('MT19937', [1, 2, 3], 0, 0, 0.)}}}
    assert save_snapshot(path, state) == path
    saved = load_snapshot(path)
    assert torch.equal(saved['model']['integer'], state['model']['integer'])
    assert torch.equal(saved['model']['complex'], state['model']['complex'])
    assert saved['model']['_extra_state'] == state['model']['_extra_state']
    assert saved['tracker'] == state['tracker'] and saved['training_state'] == state['training_state']


@pytest.mark.parametrize('previous', [False, True])
@pytest.mark.parametrize('failure', ['serialize', 'publish'])
def test_snapshot_precommit_failure_preserves_old_complete_file_peer_and_primary(
        tmp_path, monkeypatch, previous, failure):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
    path = tmp_path / 'latest.pt'
    old, new = {'model': {'w': torch.tensor([1.])}}, {'model': {'w': torch.tensor([2.])}}
    if previous:
        save_snapshot(path, old)
    stored = path.read_bytes() if previous else None
    peer = tmp_path / '.peer-writing'
    peer.write_bytes(b'peer staging')
    primary = OSError('original snapshot publication failure')
    original_save, original_replace = torch.save, Path.replace
    def save(value, target, *args, **kwargs):
        if failure == 'serialize':
            if hasattr(target, 'write'):
                target.write(b'partial snapshot')
            else:
                Path(target).write_bytes(b'partial snapshot')
            raise primary
        return original_save(value, target, *args, **kwargs)
    def replace(source, destination, *args, **kwargs):
        if Path(destination) == path and failure == 'publish':
            raise primary
        return original_replace(source, destination, *args, **kwargs)
    monkeypatch.setattr(torch, 'save', save)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(OSError) as caught:
        save_snapshot(path, new)
    assert caught.value is primary
    assert peer.read_bytes() == b'peer staging'
    if previous:
        assert path.read_bytes() == stored
        assert torch.equal(load_snapshot(path)['model']['w'], old['model']['w'])
    else:
        assert not path.exists()


@pytest.mark.parametrize('payload', [object(), {'model': object()}, {'extra': {1, 2}}])
def test_snapshot_rejects_arbitrary_objects_before_replacing_valid_state(tmp_path, payload):
    from rpipe.structure.artifact.checkpoint import save_snapshot
    path = tmp_path / 'latest.pt'
    save_snapshot(path, {'model': {'counter': 1}})
    before = path.read_bytes()
    with pytest.raises((TypeError, ValueError)):
        save_snapshot(path, payload)
    assert path.read_bytes() == before


@pytest.mark.parametrize('body', [
    {'model': {'w': 1}},
    {'schema': True, 'kind': 'rpipe.snapshot', 'payload': {}},
    {'schema': 2, 'kind': 'rpipe.snapshot', 'payload': {}},
    {'schema': 1, 'kind': 'foreign.snapshot', 'payload': {}},
    {'schema': 1, 'kind': 'rpipe.snapshot', 'payload': []},
])
def test_snapshot_reader_rejects_uncommitted_or_unknown_formats_without_repair(tmp_path, body):
    import torch
    from rpipe.structure.artifact.checkpoint import load_snapshot
    path = tmp_path / 'state.pt'
    torch.save(body, path)
    before = path.read_bytes()
    with pytest.raises((ValueError, TypeError, pickle.UnpicklingError)):
        load_snapshot(path)
    assert path.read_bytes() == before


def test_missing_snapshot_read_is_readonly_and_does_not_create_its_parent(tmp_path):
    from rpipe.structure.artifact.checkpoint import load_snapshot
    parent = tmp_path / 'missing'
    with pytest.raises(FileNotFoundError):
        load_snapshot(parent / 'latest.pt')
    assert not parent.exists()


def test_snapshot_reader_rejects_a_directory_even_with_old_weight_pieces(tmp_path):
    import torch
    from rpipe.structure.artifact.checkpoint import load_snapshot
    folder = tmp_path / 'old'
    folder.mkdir()
    torch.save({'w': 1}, folder / 'model.pt')
    before = (folder / 'model.pt').read_bytes()
    with pytest.raises((ValueError, TypeError, IsADirectoryError)):
        load_snapshot(folder)
    assert (folder / 'model.pt').read_bytes() == before


@pytest.mark.parametrize('path', [Path('relative.pt'), Path('relative'), Path('..')])
def test_snapshot_requires_explicit_absolute_pt_path(path):
    from rpipe.structure.artifact.checkpoint import load_snapshot, save_snapshot
    with pytest.raises((ValueError, TypeError)):
        load_snapshot(path)
    with pytest.raises((ValueError, TypeError)):
        save_snapshot(path, {})


def test_snapshot_cleanup_failure_keeps_original_error_and_peer(tmp_path, monkeypatch):
    import torch
    from rpipe.structure.artifact import checkpoint
    path, peer = tmp_path / 'latest.pt', tmp_path / 'peer.tmp'
    checkpoint.save_snapshot(path, {'step': 1})
    old = path.read_bytes()
    peer.write_bytes(b'peer')
    primary, cleanup = OSError('serialization refused'), PermissionError('staging cleanup refused')
    pending = []
    def refuse_save(body, stream, **kwargs):
        stream.write(b'incomplete')
        raise primary
    unlink = Path.unlink
    def refuse_unlink(target, *args, **kwargs):
        if target != peer and target.suffix == '.writing':
            pending.append(target)
            raise cleanup
        return unlink(target, *args, **kwargs)
    monkeypatch.setattr(torch, 'save', refuse_save)
    monkeypatch.setattr(Path, 'unlink', refuse_unlink)
    with pytest.raises(OSError) as caught:
        checkpoint.save_snapshot(path, {'step': 2})
    assert caught.value is primary and cleanup in primary.cleanup_errors
    assert pending and all(item.exists() for item in pending)
    assert path.read_bytes() == old and peer.read_bytes() == b'peer'


def test_snapshot_load_never_executes_arbitrary_pickle(tmp_path):
    import torch
    from rpipe.structure.artifact.checkpoint import load_snapshot
    target = tmp_path / 'executed'
    class Unsafe:
        def __reduce__(self):
            return exec, (f"open({str(target)!r}, 'w').write('executed')",)
    path = tmp_path / 'unsafe.pt'
    torch.save({'schema': 1, 'kind': 'rpipe.snapshot', 'payload': {'extra': Unsafe()}}, path)
    old = path.read_bytes()
    with pytest.raises(ValueError, match='invalid checkpoint encoding'):
        load_snapshot(path)
    assert not target.exists() and path.read_bytes() == old


def test_snapshot_rejects_tensor_custom_attributes_before_commit(tmp_path):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot
    path = tmp_path / 'latest.pt'
    save_snapshot(path, {'weight': torch.tensor([1.])})
    old = path.read_bytes()
    bad = torch.tensor([2.])
    bad.extra = object()
    with pytest.raises(ValueError):
        save_snapshot(path, {'weight': bad})
    assert path.read_bytes() == old


def test_multistep_scheduler_counter_roundtrip_keeps_remaining_lr_sequence(tmp_path):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
    parameter = torch.nn.Parameter(torch.ones(1))
    optimizer = torch.optim.SGD([parameter], lr=.1)
    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[2, 4, 4], gamma=.5)
    for _ in range(3):
        optimizer.step()
        scheduler.step()
    path = tmp_path / 'latest.pt'
    save_snapshot(path, {'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict()})
    saved = load_snapshot(path)
    other = torch.optim.SGD([torch.nn.Parameter(torch.ones(1))], lr=.1)
    restored = torch.optim.lr_scheduler.MultiStepLR(other, milestones=[2, 4, 4], gamma=.5)
    other.load_state_dict(saved['optimizer'])
    restored.load_state_dict(saved['scheduler'])
    for _ in range(5):
        optimizer.step()
        other.step()
        scheduler.step()
        restored.step()
        assert scheduler.get_last_lr() == restored.get_last_lr()


def test_truncated_snapshot_zip_is_rejected_without_repair(tmp_path):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
    path = tmp_path / 'latest.pt'
    save_snapshot(path, {'weight': torch.arange(1024.)})
    path.write_bytes(path.read_bytes()[:-128])
    corrupt = path.read_bytes()
    # Windows TorchReader can report its failed read/seek as OSError(22).
    with pytest.raises((ValueError, OSError)):
        load_snapshot(path)
    assert path.read_bytes() == corrupt


@pytest.mark.parametrize('reason', ['not a ZIP archive', 'failed finding central directory'])
def test_known_snapshot_zip_decode_failure_retains_original_cause(tmp_path, monkeypatch, reason):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
    path = tmp_path / 'latest.pt'
    save_snapshot(path, {'weight': torch.ones(1)})
    before = path.read_bytes()
    decode = RuntimeError('PytorchStreamReader failed reading zip archive: ' + reason)
    def fail(*args, **kwargs):
        raise decode
    monkeypatch.setattr(torch, 'load', fail)
    with pytest.raises(ValueError) as caught:
        load_snapshot(path)
    assert caught.value.__cause__ is decode and path.read_bytes() == before


def test_snapshot_reader_keeps_unrelated_runtime_failure_visible(tmp_path, monkeypatch):
    import torch
    from rpipe.structure.artifact.checkpoint import save_snapshot, load_snapshot
    path = tmp_path / 'latest.pt'
    save_snapshot(path, {'weight': torch.ones(1)})
    old = path.read_bytes()
    primary = RuntimeError('program failure outside checkpoint decoding')
    def fail(*args, **kwargs):
        raise primary
    monkeypatch.setattr(torch, 'load', fail)
    with pytest.raises(RuntimeError) as caught:
        load_snapshot(path)
    assert caught.value is primary and path.read_bytes() == old
