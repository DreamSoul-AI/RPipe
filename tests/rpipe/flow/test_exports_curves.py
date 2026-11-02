import json
from pathlib import Path

import pytest

from rpipe.structure.artifact.readout import load_curve_series, summarize_curve_series
from rpipe.structure.algorithm.tracker import AlgorithmTracker

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.flow_layer,
    pytest.mark.module_process,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def test_history_prefers_all_report_observations_over_sparse_history(tmp_path: Path, published_specs):
    tracker = published_specs[0].assets_dir / 'tracker'
    tracker.mkdir(parents=True)
    (tracker / 'tracker_state.json').write_text(
        json.dumps({'splits': {'train': {'Accuracy': {'history': [99.0]}}}}), encoding='utf-8'
    )
    train = [0.5] + [float(i * 5) for i in range(1, 12)] + [55.0]
    test = [float(i + 20) for i in range(12)]
    rows = []
    for i, value in enumerate(train):
        rows.append({'step': i, 'split': 'train', 'name': 'Accuracy', 'mean': value})
        rows.append({'step': i, 'split': 'train', 'name': 'Loss', 'mean': 2.0})
        if i < len(test):
            rows.append({'step': i, 'split': 'test', 'name': 'Accuracy', 'mean': test[i]})
    (tracker / 'scalars.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8'
    )

    # Keep the real 0.5% and repeated terminal observation, rather than inventing units or steps.
    assert load_curve_series(published_specs[0].ref)['train']['Accuracy']['values'] == train
    assert load_curve_series(published_specs[0].ref)['test']['Accuracy']['values'] == test


def test_history_skips_invalid_jsonl_rows_and_keeps_valid_observations(tmp_path: Path, published_specs):
    tracker = published_specs[0].assets_dir / 'tracker'
    tracker.mkdir(parents=True)
    rows = [
        b'{"split":"train","name":"Accuracy","mean":0.5}',
        b'{',
        b'[]',
        b'null',
        b'\xff',
        b'{"split":"train","name":"Accuracy","mean":true}',
        b'{"split":"train","name":"Accuracy","mean":"50"}',
        b'{"split":"train","name":"Accuracy","mean":NaN}',
        b'{"split":"train","name":"Accuracy","mean":Infinity}',
        b'{"split":"train","name":"Accuracy","mean":75.0}',
        b'{"split":"train","name":"Accuracy","mean":',
    ]
    (tracker / 'scalars.jsonl').write_bytes(b'\n'.join(rows))

    assert load_curve_series(published_specs[0].ref)['train']['Accuracy']['values'] == [0.5, 75.0]


@pytest.mark.parametrize(
    'jsonl_bytes',
    [None, b'', b'{', b'{"split":"test","name":"Loss","mean":2.0}\n'],
    ids=['missing', 'empty', 'invalid', 'other-metric'],
)
def test_curve_never_revives_state_history_when_jsonl_has_no_matching_values(tmp_path: Path, jsonl_bytes, published_specs):
    tracker = published_specs[0].assets_dir / 'tracker'
    tracker.mkdir(parents=True)
    (tracker / 'tracker_state.json').write_text(
        json.dumps({'splits': {'train': {'Accuracy': {'history': [0.5, 80.0]}}}}), encoding='utf-8'
    )
    if jsonl_bytes is not None:
        (tracker / 'scalars.jsonl').write_bytes(jsonl_bytes)

    assert 'Accuracy' not in load_curve_series(published_specs[0].ref).get('train', {})


def test_curve_follows_checkpoint_prefix_and_preserves_raw_rollback_records(tmp_path, published_specs):
    tracker = AlgorithmTracker(published_specs[0].assets_dir)
    tracker.begin_run()

    def record(step, value):
        tracker.reset('test')
        tracker.append('test', values={'Loss': value})
        tracker.flush('test', progress={'step': step, 'epoch': 1})

    record(10, 1.0)
    at10 = tracker.state_dict()
    record(20, 2.0)
    at20 = tracker.state_dict()
    record(30, 99.0)  # Uncommitted branch, also beyond the eventual final budget.
    tracker.begin_run(at10)
    record(15, 88.0)
    tracker.begin_run(at20)  # Restore another committed prefix, not just prune the last list.
    record(25, 3.0)
    record(25, 4.0)  # A repeated final report uses its last observation.
    series = load_curve_series(published_specs[0].ref)['test']['Loss']
    assert series == {'unit': 'step', 'x': [10, 20, 25], 'values': [1.0, 2.0, 4.0]}
    raw = tracker.jsonl_path.read_text(encoding='utf-8')
    assert '99.0' in raw and '88.0' in raw


@pytest.mark.parametrize('checkpoint', ['fresh', 'legacy', 'other-log', 'invalid-offset'])
def test_curve_does_not_inherit_unidentified_log_prefix(tmp_path, checkpoint, published_specs):
    tracker = AlgorithmTracker(published_specs[0].assets_dir)
    tracker.append('train', values={'Loss': 9.0})
    tracker.flush('train', progress={'step': 90})
    state = tracker.state_dict()
    if checkpoint == 'fresh':
        state = None
    elif checkpoint == 'legacy':
        state = {'step': tracker.step}
    elif checkpoint == 'other-log':
        state['jsonl_path'] = 'another-log'
    else:
        state['jsonl_offset'] += 1000
    tracker.begin_run(state)
    tracker.reset('train')
    tracker.append('train', values={'Loss': 1.0})
    tracker.flush('train', progress={'step': 1})
    assert load_curve_series(published_specs[0].ref)['train']['Loss']['x'] == [1]


def test_progress_aggregation_uses_union_and_separates_epoch_and_legacy():
    summary = summarize_curve_series([
        {'unit': 'step', 'x': [10, 20, 40], 'values': [1.0, 2.0, 4.0]},
        {'unit': 'step', 'x': [10, 30, 40], 'values': [3.0, 6.0, 8.0]},
        {'unit': 'epoch', 'x': [0.5], 'values': [7.0]},
        {'unit': 'observation', 'x': [1, 2], 'values': [9.0, 10.0]},
    ])
    step = summary['by_unit']['step']
    assert step['x'] == [10, 20, 30, 40]
    assert step['mean'] == [2.0, 2.0, 6.0, 6.0]
    assert step['n_at_point'] == [2, 1, 1, 2]
    assert step['std'][1:3] == [0.0, 0.0]
    assert summary['by_unit']['epoch']['x'] == [0.5]
    assert summary['by_unit']['observation']['unit'] == 'observation'


def test_restart_marker_survives_a_partial_jsonl_tail(tmp_path, published_specs):
    tracker = AlgorithmTracker(published_specs[0].assets_dir)
    tracker.append('test', values={'Loss': 9.0})
    tracker.flush('test', progress={'step': 90})
    with tracker.jsonl_path.open('ab') as handle:
        handle.write(b'{"mean":')
    tracker.begin_run()
    tracker.reset('test')
    tracker.append('test', values={'Loss': 1.0})
    tracker.flush('test', progress={'step': 1})
    assert load_curve_series(published_specs[0].ref)['test']['Loss']['x'] == [1]


@pytest.mark.parametrize('coordinates,unit,x', [
    ([{'epoch': 0.5}, {'epoch': 1.5}], 'epoch', [0.5, 1.5]),
    ([{'step': 100}, {'step': 200}], 'observation', [1, 2]),
    ([{'optimizer_step': True}, {'optimizer_step': 20}], 'observation', [1, 2]),
    ([{'optimizer_step': 10}, {}], 'observation', [1, 2]),
])
def test_curve_only_uses_explicit_valid_coordinates(tmp_path, coordinates, unit, x, published_specs):
    tracker = published_specs[0].assets_dir / 'tracker'
    tracker.mkdir(parents=True)
    rows = [dict(split='test', name='Loss', mean=i + 1.0, **point) for i, point in enumerate(coordinates)]
    (tracker / 'scalars.jsonl').write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
    series = load_curve_series(published_specs[0].ref)['test']['Loss']
    assert series['unit'] == unit and series['x'] == x


@pytest.fixture
def tensorboard_writer(monkeypatch):
    import sys
    from types import SimpleNamespace
    writers = []

    class Writer:
        def __init__(self, *, log_dir):
            self.path, self.rows, self.closed = Path(log_dir), [], False
            self.path.mkdir(parents=True)
            writers.append(self)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.closed = True
        def add_scalar(self, tag, value, *, global_step, **kwargs):
            self.rows.append((tag, global_step, value, kwargs))

    monkeypatch.setitem(sys.modules, 'torch.utils.tensorboard', SimpleNamespace(SummaryWriter=Writer))
    return writers


def _tb_source(spec, rows):
    path = spec.assets_dir / 'tracker/scalars.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows))
    return path


def test_tensorboard_exports_rollback_snapshot_and_keeps_inputs(tmp_path, tensorboard_writer, published_specs):
    from rpipe.flow.exports import export_tensorboard
    study = published_specs[0].study_root
    first = {'split': 'train', 'name': 'Loss', 'mean': 1.25, 'optimizer_step': 1}
    tail = dict(first, mean=99, optimizer_step=2)
    keep = len(json.dumps(first).encode()) + 1
    scalar = _tb_source(published_specs[0], [first, tail, {'event': 'start', 'keep_until': keep},
                                    dict(first, mean=2.5, optimizer_step=2),
                                    dict(first, mean=3.5, optimizer_step=2)])
    result = published_specs[0].result_path
    result.write_bytes(b'{"status":"succeeded"}')
    # Conflicting state is deliberately excluded from this display source.
    (scalar.parent / 'tracker_state.json').write_text(json.dumps({'splits': {'extra': {'OnlyState': {'history': [99]}}}}))
    _tb_source(published_specs[1], [dict(first, mean=8)])
    before = {p: p.read_bytes() for p in study.rglob('*') if p.is_file()}
    dest = export_tensorboard(study, tmp_path / 'events')
    assert [w.path for w in tensorboard_writer] == [dest / spec.id for spec in published_specs]
    assert [r[:3] for r in tensorboard_writer[0].rows] == [('step/train/Loss', 1, 1.25), ('step/train/Loss', 2, 3.5)]
    assert all(w.closed for w in tensorboard_writer)
    assert {p: p.read_bytes() for p in before} == before
    with pytest.raises(FileExistsError):
        export_tensorboard(study, dest)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('coordinate', [{'epoch': 2.0}, {'epoch': 0}, {'optimizer_step': 0}])
def test_tensorboard_keeps_integer_coordinates_and_tag_identity(tmp_path, tensorboard_writer, coordinate, published_specs):
    from rpipe.flow.exports import export_tensorboard
    study = published_specs[0].study_root
    _tb_source(published_specs[0], [{'split': 'train/a', 'name': 'Loss/b', 'mean': 0, **coordinate}])
    export_tensorboard(study, tmp_path / 'events')
    unit = 'epoch' if 'epoch' in coordinate else 'step'
    assert tensorboard_writer[0].rows[0][:3] == (f'{unit}/train%2Fa/Loss%2Fb', int(next(iter(coordinate.values()))), 0)


@pytest.mark.parametrize('coordinate', [{'epoch': 0.5}, {}, {'optimizer_step': 2**63}])
def test_tensorboard_refuses_lossy_coordinates_before_writing(tmp_path, tensorboard_writer, coordinate, published_specs):
    from rpipe.flow.exports import export_tensorboard
    study = published_specs[0].study_root
    _tb_source(published_specs[0], [{'split': 'train', 'name': 'Loss', 'mean': 1, **coordinate}])
    with pytest.raises(ValueError, match='integer optimizer_step/epoch'):
        export_tensorboard(study, tmp_path / 'events')
    assert not (tmp_path / 'events').exists()
    assert not tensorboard_writer


@pytest.mark.parametrize('failure', ['missing', 'construct', 'write', 'flush', 'close'])
def test_tensorboard_cli_failure_preserves_formal_results(tmp_path, monkeypatch, capsys, failure, source_study, run_root, published_specs, science_factory, science_worker):
    import sys
    from types import SimpleNamespace
    from rpipe.flow.cli import main
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    study = published_specs[0].study_root
    science_worker(science_factory())
    assert run(published_specs[0]).status == 'succeeded'
    _tb_source(published_specs[0], [{'split': 'train', 'name': 'Loss', 'mean': 1, 'epoch': 1}])
    result = published_specs[0].result_path
    before = {p: p.read_bytes() for p in study.rglob('*') if p.is_file()}
    closed = []
    class Writer:
        def __init__(self, **kwargs):
            if failure == 'construct':
                raise OSError('constructor failure')
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(True)
            if failure in ('flush', 'close'):
                raise OSError(f'{failure} failure')
        def add_scalar(self, *args, **kwargs):
            if failure == 'write':
                raise OSError('write failure')
    monkeypatch.setitem(sys.modules, 'torch.utils.tensorboard', None if failure == 'missing' else SimpleNamespace(SummaryWriter=Writer))
    assert main(['tensorboard', str(source_study), '--run-root', str(run_root), '--out', str(tmp_path / 'events')]) == 1
    assert capsys.readouterr().err
    assert {p: p.read_bytes() for p in before} == before
    assert bool(closed) == (failure in ('write', 'flush', 'close'))


def test_tensorboard_refuses_study_output_and_state_only_inputs(tmp_path, tensorboard_writer, published_specs):
    from rpipe.flow.exports import export_tensorboard
    study = published_specs[0].study_root
    study.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match='separate'):
        export_tensorboard(study, study / 'events')
    with pytest.raises(ValueError, match='no JSONL'):
        export_tensorboard(study, tmp_path / 'events')
    assert not (tmp_path / 'events').exists()


def test_tensorboard_explicit_selection_excludes_other_run_trajectories(tmp_path, tensorboard_writer, published_specs):
    from rpipe.flow.exports import export_tensorboard

    study = published_specs[0].study_root
    row = {'split': 'train', 'name': 'Loss', 'mean': 1, 'optimizer_step': 1}
    _tb_source(published_specs[0], [row])
    _tb_source(published_specs[1], [dict(row, mean=99)])
    export_tensorboard(study, tmp_path / 'events', run_ids=[published_specs[0].id])
    assert [writer.path.name for writer in tensorboard_writer] == [published_specs[0].id]
    assert tensorboard_writer[0].rows[0][2] == 1


@pytest.mark.parametrize('ids', [['missing'], ['../escape'], ['a', 'a']])
def test_tensorboard_bad_or_missing_selection_fails_before_creating_output(tmp_path, tensorboard_writer, ids, published_specs):
    from rpipe.flow.exports import export_tensorboard

    study = published_specs[0].study_root
    _tb_source(published_specs[0], [{'split': 'train', 'name': 'Loss', 'mean': 1, 'optimizer_step': 1}])
    with pytest.raises(ValueError):
        export_tensorboard(study, tmp_path / 'events', run_ids=ids)
    assert not (tmp_path / 'events').exists()
    assert not tensorboard_writer


def test_tensorboard_rejects_tracker_link_outside_selected_output(tmp_path, tensorboard_writer, published_specs):
    from rpipe.flow.exports import export_tensorboard

    peer = tmp_path / 'peer/scalars.jsonl'
    peer.parent.mkdir()
    peer.write_text('{"split":"train","name":"Loss","mean":1,"epoch":1}\n', encoding='utf-8')
    study = published_specs[0].study_root
    target = published_specs[0].assets_dir
    target.mkdir(parents=True, exist_ok=True)
    (target / 'tracker').symlink_to(peer.parent, target_is_directory=True)
    before = peer.read_bytes()
    with pytest.raises(ValueError, match='escape'):
        export_tensorboard(study, tmp_path / 'events', run_ids=[published_specs[0].id])
    assert peer.read_bytes() == before
    assert not (tmp_path / 'events').exists()


def test_tensorboard_rejects_its_library_checkout_before_writing(tmp_path, tensorboard_writer, published_specs):
    import rpipe.flow.exports as exports
    from rpipe.structure.control import AdmissionError

    study = published_specs[0].study_root
    _tb_source(published_specs[0], [{'split': 'train', 'name': 'Loss', 'mean': 1, 'epoch': 1}])
    destination = Path(exports.__file__).resolve().parents[3] / '.tmp' / 'forbidden-tensorboard-export'
    assert not destination.exists()
    with pytest.raises(AdmissionError, match='source|checkout|project'):
        exports.export_tensorboard(study, destination)
    assert not destination.exists()
    assert not tensorboard_writer


@pytest.mark.external
def test_tensorboard_real_event_roundtrip(tmp_path, published_specs):
    pytest.importorskip('tensorboard')
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
    from rpipe.flow.exports import export_tensorboard
    from tensorboard.util import tensor_util
    study = published_specs[0].study_root
    _tb_source(published_specs[0], [{'split': 'test', 'name': 'Accuracy', 'mean': 1.23456789012345, 'epoch': 2}])
    dest = export_tensorboard(study, tmp_path / 'events')
    events = EventAccumulator(str(dest / published_specs[0].id)).Reload().Tensors('epoch/test/Accuracy')
    assert len(events) == 1 and events[0].step == 2
    assert tensor_util.make_ndarray(events[0].tensor_proto).item() == 1.23456789012345


@pytest.mark.parametrize('target', ['index', 'scalar', 'outcome', 'owner'])
def test_learning_curve_refuses_input_drift_during_render_and_preserves_old_image(
        tmp_path, published_specs, science_factory, science_worker, monkeypatch, target):
    from matplotlib.figure import Figure
    from rpipe.flow.exports import write_learning_curves, learning_curves_path
    from rpipe.flow.run import run
    from rpipe.structure.control import SourceMismatch

    spec = published_specs[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    scalar = _tb_source(spec, [{'split': 'train', 'name': 'Loss', 'mean': 1, 'optimizer_step': 1}])
    destination = learning_curves_path(spec.study_root)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b'previous verified image')
    peer = destination.parent / 'peer.tmp'
    peer.write_bytes(b'other renderer evidence')
    from rpipe.structure.artifact.io import read_json
    attempt = read_json(spec.run_dir / 'execution.json')['attempt']
    changed = {'index': spec.study_root / 'index.json', 'scalar': scalar,
               'outcome': spec.run_dir / 'attempts' / attempt / 'outcome.json',
               'owner': spec.run_dir / '.execution'}[target]
    inputs = {p: p.read_bytes() for p in spec.study_root.rglob('*') if p.is_file() and p != changed}
    save = Figure.savefig
    def drift(figure, stream, **kwargs):
        result = save(figure, stream, **kwargs)
        if target == 'owner':
            changed.mkdir()
        else:
            changed.write_bytes(changed.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(Figure, 'savefig', drift)
    with pytest.raises(SourceMismatch):
        write_learning_curves(spec.study_root, None)
    assert all(path.read_bytes() == body for path, body in inputs.items())
    assert destination.read_bytes() == b'previous verified image'
    assert peer.read_bytes() == b'other renderer evidence'
    assert set(destination.parent.glob('*.tmp')) == {peer}


def test_learning_curve_embeds_fingerprints_of_formal_inputs(
        published_specs, science_factory, science_worker):
    import hashlib
    from PIL import Image
    from rpipe.flow.exports import write_learning_curves
    from rpipe.flow.run import run

    spec = published_specs[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    scalar = _tb_source(spec, [{'split': 'train', 'name': 'Loss', 'mean': 1, 'optimizer_step': 1}])
    destination = write_learning_curves(spec.study_root, None)
    with Image.open(destination) as image:
        inputs = json.loads(image.info['RPipeInputs'])
    assert inputs['index_sha256'] == hashlib.sha256((spec.study_root / 'index.json').read_bytes()).hexdigest()
    record = next(row for row in inputs['runs'] if row['identity']['id'] == spec.id)
    for name, path in [('config', spec.run_dir / 'config.yaml'), ('result', spec.result_path)]:
        assert record[name + '_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert inputs['scalars_sha256'][spec.id] == hashlib.sha256(scalar.read_bytes()).hexdigest()


def test_tensorboard_selected_snapshot_ignores_unselected_scalar_updates(
        tmp_path, published_specs, tensorboard_writer, monkeypatch):
    import sys
    from rpipe.flow.exports import export_tensorboard
    selected, other = published_specs
    row = {'split': 'train', 'name': 'Loss', 'mean': 1, 'optimizer_step': 1}
    scalar = _tb_source(selected, [row])
    unselected = _tb_source(other, [dict(row, mean=99)])
    writer_class = sys.modules['torch.utils.tensorboard'].SummaryWriter
    add = writer_class.add_scalar
    def mutate_unselected(writer, *args, **kwargs):
        unselected.write_bytes(unselected.read_bytes() + b'\n')
        return add(writer, *args, **kwargs)
    monkeypatch.setattr(writer_class, 'add_scalar', mutate_unselected)
    destination = export_tensorboard(selected.study_root, tmp_path / 'events', run_ids=[selected.id])
    inputs = json.loads((destination / 'EXPORT_INPUTS.json').read_text(encoding='utf-8'))['inputs']
    assert set(inputs['scalars_sha256']) == {selected.id}
    assert [row['identity']['id'] for row in inputs['runs']] == [selected.id]
    assert [writer.path.name for writer in tensorboard_writer] == [selected.id]


@pytest.mark.parametrize('component', ['figure', 'figures', 'summary'])
def test_learning_curve_cannot_follow_same_study_link_into_formal_run(
        published_specs, science_factory, science_worker, file_bytes, component):
    from rpipe.flow.exports import learning_curves_path, write_learning_curves
    from rpipe.flow.run import run
    from rpipe.structure.control import AdmissionError
    spec = published_specs[0]
    science_worker(science_factory())
    assert run(spec).status == 'succeeded'
    _tb_source(spec, [{'split': 'train', 'name': 'Loss', 'mean': 1, 'optimizer_step': 1}])
    destination = learning_curves_path(spec.study_root)
    if component == 'figure':
        destination.parent.mkdir(parents=True)
        destination.symlink_to(spec.result_path)
    elif component == 'figures':
        destination.parent.parent.mkdir()
        destination.parent.symlink_to(spec.run_dir, target_is_directory=True)
    else:
        destination.parent.parent.symlink_to(spec.run_dir, target_is_directory=True)
    peer = spec.run_dir / 'peer-scientific-evidence'
    peer.write_bytes(b'peer retained')
    before = file_bytes(spec.run_dir)
    with pytest.raises(AdmissionError):
        write_learning_curves(spec.study_root, None)
    assert file_bytes(spec.run_dir) == before
    assert peer.read_bytes() == b'peer retained'
