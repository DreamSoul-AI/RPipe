"""Study comparison PNG ownership; import the actual Study plotting module without running its CLI."""

import importlib.util
import json
import os
import stat
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_process,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def study_plot_case(tmp_path, old):
    import matplotlib.pyplot as plt

    source = Path(__file__).resolve().parents[5] / 'studies/main_exp/flow/process/curves.py'
    module_spec = importlib.util.spec_from_file_location('study_plot_publication_under_test', source)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    names, models = module.DATA_NAMES, module.MODEL_NAMES
    reference = {'datasets': {data: {'models': {model: {'sparse_points': [
        {'step': 200, 'accuracy_pct_estimate': 50.0}]} for model in models}} for data in names}}
    report = {'final_gates_applied': False, 'experiments': [
        {'data': data, 'model': model, 'optimizer_steps': [200, 400],
         'mean_accuracy_pct': [50.0, 60.0], 'population_std_accuracy_pp': [1.0, 2.0],
         'seed_count_by_step': [2, 3]} for data in names for model in models]}
    dest = tmp_path / 'figures' / 'historical_comparison.png'
    dest.parent.mkdir()
    peer = dest.parent / 'peer.tmp'
    peer.write_bytes(b'peer writer evidence')
    peer_figure = plt.figure(figsize=(1, 1))
    if old:
        peer_figure.savefig(dest)
        dest.chmod(0o640 if os.name != 'nt' else 0o666)
    case = {'plot': module.plot_report, 'report': report, 'reference': reference, 'dest': dest,
            'old': dest.read_bytes() if old else None,
            'mode': stat.S_IMODE(dest.stat().st_mode) if old else None,
            'figures': set(plt.get_fignums()), 'peers': {'peer.tmp'},
            'inputs': {peer: peer.read_bytes()},
            'payload': json.dumps([report, reference], sort_keys=True)}
    try:
        yield case
    finally:
        for number in set(plt.get_fignums()) - case['figures']:
            plt.close(number)
        plt.close(peer_figure)


def _preserved(case, *, retained_staging=False):
    import matplotlib.pyplot as plt

    assert json.dumps([case['report'], case['reference']], sort_keys=True) == case['payload']
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
def test_study_plot_failures_preserve_target_peers_and_release_owned_figure(study_plot_case, monkeypatch, phase):
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    case = study_plot_case
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
        case['plot'](case['report'], case['reference'], case['dest'])
    assert caught.value is error
    _preserved(case)


@pytest.mark.parametrize('old', [False, True])
def test_study_plot_staging_collision_preserves_other_writer(study_plot_case, monkeypatch):
    case = study_plot_case
    token = 'c' * 32
    collision = case['dest'].parent / (token + '.tmp')
    collision.write_bytes(b'other writer collision')
    case['inputs'][collision] = collision.read_bytes()
    case['peers'].add(collision.name)
    monkeypatch.setattr(uuid, 'uuid4', lambda: SimpleNamespace(hex=token))
    with pytest.raises(FileExistsError):
        case['plot'](case['report'], case['reference'], case['dest'])
    _preserved(case)


@pytest.mark.parametrize('old', [False, True])
def test_study_plot_cleanup_denial_keeps_primary_publish_error(study_plot_case, monkeypatch):
    case = study_plot_case
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
        case['plot'](case['report'], case['reference'], case['dest'])
    assert caught.value is error
    _preserved(case, retained_staging=True)


@pytest.mark.parametrize('old', [False, True])
def test_study_plot_success_publishes_valid_png_and_closes_only_owned_figure(study_plot_case, monkeypatch):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    case = study_plot_case
    saved = []
    original = Figure.savefig
    def save(figure, *args, **kwargs):
        saved.append(figure)
        return original(figure, *args, **kwargs)
    monkeypatch.setattr(Figure, 'savefig', save)
    assert case['plot'](case['report'], case['reference'], case['dest']) is None
    assert case['dest'].read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert plt.imread(case['dest']).shape[0] > 0
    assert saved[0].axes[0].lines[0].get_ydata().tolist() == [50.0, 60.0]
    assert set(plt.get_fignums()) == case['figures']
    assert all(path.read_bytes() == data for path, data in case['inputs'].items())
    assert {path.name for path in case['dest'].parent.glob('*.tmp')} == case['peers']
    if case['mode'] is not None:
        assert stat.S_IMODE(case['dest'].stat().st_mode) == case['mode']


@pytest.mark.skipif(os.name != 'nt', reason='native Windows readonly replacement and cleanup')
@pytest.mark.parametrize('old', [True])
def test_readonly_target_denial_cleans_owned_readonly_staging(study_plot_case):
    case = study_plot_case
    case['dest'].chmod(0o444)
    case['mode'] = stat.S_IMODE(case['dest'].stat().st_mode)
    try:
        with pytest.raises(PermissionError):
            case['plot'](case['report'], case['reference'], case['dest'])
        _preserved(case)
    finally:
        case['dest'].chmod(0o666)


@pytest.mark.parametrize('old', [False, True])
@pytest.mark.parametrize('primary_kind', ['save', 'cancel', None])
def test_study_plot_stream_close_io_keeps_primary_and_success_close_stays_strict(study_plot_case, monkeypatch, primary_kind):
    from matplotlib.figure import Figure

    case = study_plot_case
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
        case['plot'](case['report'], case['reference'], case['dest'])
    assert caught.value is expected
    _preserved(case)


@pytest.fixture
def curve_audit_case(tmp_path, monkeypatch):
    """Synthetic 400-point evidence; checkpoint reading never imports Torch."""
    import rpipe.api

    source = Path(__file__).resolve().parents[5] / 'studies/main_exp/flow/process/curves.py'
    spec = importlib.util.spec_from_file_location('study_curve_audit_under_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'DATA_NAMES', ('MNIST',))
    monkeypatch.setattr(module, 'MODEL_NAMES', ('linear',))
    monkeypatch.setattr(module, 'SEEDS', (0,))
    monkeypatch.setattr(rpipe.api, 'source_digest', lambda path: 'current-source')
    study = tmp_path / 'research-version'
    roots = {mode: study / 'runs' / mode for mode in ('train', 'eval')}
    best_path = roots['train'] / 'assets' / 'checkpoints' / 'best.pt'
    best_path.parent.mkdir(parents=True)
    best_path.write_bytes(b'synthetic checkpoint identity; never loaded')
    values = [80.0] * len(module.STEPS)
    results, entries = {}, {}
    for mode, root in roots.items():
        tracker = root / 'assets' / 'tracker'
        tracker.mkdir(parents=True)
        x = module.STEPS if mode == 'train' else [400]
        y = values if mode == 'train' else [80.0]
        rows = [{'event': 'start', 'keep_until': 0}]
        rows.extend({'split': 'test', 'name': 'Accuracy', 'optimizer_step': step,
                     'step': (index + 1) * 240 if mode == 'train' else 40, 'mean': mean}
                    for index, (step, mean) in enumerate(zip(x, y)))
        (tracker / 'scalars.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        (tracker / 'tracker_state.json').write_text(json.dumps({
            'splits': {'test': {'Accuracy': {'history': y}}}}), encoding='utf-8')
        (root / 'assets' / 'logs').mkdir()
        entries[mode] = {'id': mode, 'seed': 0, 'run_dir': mode}
        results[mode] = {
            'status': 'succeeded', 'attempt': ('a' if mode == 'train' else 'b') * 32,
            'quality': None, 'evidence_complete': True,
            'identity': {'id': mode, 'source_digest': 'current-source', 'version': study.name},
            'metrics': {'accuracy': 80.0, 'best_accuracy': 80.0, 'eval_accuracy': 80.0},
            'execute': {'mode': mode, 'resume_count': 0 if mode == 'train' else 1,
                        'resume_path': None if mode == 'train' else str(best_path),
                        'step': 80000 if mode == 'train' else 400},
            'structure': {'data': {'train_size': 60000, 'test_size': 10000,
                                   'test_batch_size': 250}}}
    summaries = {
        'latest': {'path': str(best_path.with_name('latest.pt')), 'step': 80000,
                   'test_history': values, 'tracker_progress': {'step': 80000}},
        'best': {'path': str(best_path), 'step': 400, 'best_metric': 'Accuracy',
                 'best_value': 80.0, 'best_accuracy': 80.0, 'test_accuracy': 80.0}}
    monkeypatch.setattr(module, 'checkpoint_summary', lambda root, stem: summaries[stem])
    case = SimpleNamespace(module=module, study=study, roots=roots, entries=entries,
                           results=results, best_path=best_path,
                           reference={'acceptance_contract': {}, 'source_sha': 'historical',
                                      'evidence_type': 'synthetic fixture'})
    for mode in roots:
        _save_curve_result(case, mode)
    _save_curve_index(case)
    return case


def _save_curve_result(case, mode):
    result, root = case.results[mode], case.roots[mode]
    (root / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    record = {'schema': 1, 'identity': result['identity'], 'attempt': result['attempt'],
              'owner_token': 'fixture-owner', 'phase': 'closing', 'candidate_status': 'succeeded',
              'quality': None, 'evidence_complete': True, 'scientific_result_published': True,
              'derived_status': 'succeeded', 'error': None, 'cleanup_errors': [], 'cancelled': False}
    outcome = root / 'attempts' / record['attempt'] / 'outcome.json'
    outcome.parent.mkdir(parents=True, exist_ok=True)
    outcome.write_text(json.dumps(record), encoding='utf-8')
    (root / 'execution.json').write_text(json.dumps(record), encoding='utf-8')


def _save_curve_index(case):
    (case.study / 'index.json').write_text(json.dumps({'experiments': [
        {'factors': {'data.name': 'MNIST', 'model.name': 'linear', 'algorithm.mode': mode},
         'runs': [case.entries[mode]]} for mode in ('train', 'eval')]}), encoding='utf-8')


def _write_curve_jsonl(case, mode, filename='events.jsonl', *, restored=True):
    row = {'timestamp': '2026-10-23T00:00:00.000Z', 'level': 'INFO',
           'event': 'checkpoint.restored' if mode == 'eval' and restored else 'task.succeeded',
           'logger': 'rpipe.test', 'message': 'synthetic diagnostic',
           'app_name': 'rpipe', 'app_version': 'test', 'runtime_id': 'runtime-a',
           'pid': 123, 'process_name': 'synthetic-worker', 'task_id': mode,
           'attempt_id': case.results[mode]['attempt']}
    path = case.roots[mode] / 'assets' / 'logs' / filename
    path.write_text(json.dumps(row) + '\n', encoding='utf-8')
    return path


def _write_curve_legacy_logs(case):
    (case.roots['train'] / 'assets' / 'logs' / 'run.log').write_text(
        'historical training diagnostic\n', encoding='utf-8')
    (case.roots['eval'] / 'assets' / 'logs' / 'run.log').write_text(
        f'[resume] target={case.best_path} epoch=0 step=400\n'
        f'[split] test resume_path={case.best_path} step=400\n', encoding='utf-8')


def _inspect_curve(case, mode):
    return case.module.inspect_run(case.study, case.entries[mode], 'MNIST', 'linear', mode, 0,
                                   expected_source='current-source')


@pytest.mark.parametrize('filename', ['events.jsonl', 'events.1.jsonl'])
@pytest.mark.parametrize('mode', ['train', 'eval'])
def test_curve_inspection_accepts_jsonl_without_legacy_log(curve_audit_case, filename, mode):
    case = curve_audit_case
    _write_curve_jsonl(case, mode, filename)
    row = _inspect_curve(case, mode)
    assert row['errors'] == []
    assert row['identity'] == case.results[mode]['identity']
    assert row['execute'] == case.results[mode]['execute']
    assert row['resume_event_count'] == (1 if mode == 'eval' else 0)
    assert not (case.roots[mode] / 'assets' / 'logs' / 'run.log').exists()


@pytest.mark.parametrize('count', [1, -1, True, 0.0, '0', None])
def test_curve_train_rejects_restored_or_invalid_scientific_count(curve_audit_case, count):
    case = curve_audit_case
    _write_curve_legacy_logs(case)
    case.results['train']['execute']['resume_count'] = count
    _save_curve_result(case, 'train')
    assert _inspect_curve(case, 'train')['errors']


def test_curve_train_rejects_scientific_resume_path_at_zero_count(curve_audit_case):
    case = curve_audit_case
    _write_curve_legacy_logs(case)
    case.results['train']['execute']['resume_path'] = str(case.best_path)
    _save_curve_result(case, 'train')
    assert _inspect_curve(case, 'train')['errors']


@pytest.mark.parametrize('missing', ['execute', 'resume_count', 'resume_path', 'step'])
def test_curve_forged_diagnostics_cannot_fill_scientific_resume_fields(curve_audit_case, missing):
    case = curve_audit_case
    _write_curve_legacy_logs(case)
    _write_curve_jsonl(case, 'train')
    _write_curve_jsonl(case, 'eval')
    if missing == 'execute':
        case.results['eval'].pop('execute')
    else:
        case.results['eval']['execute'].pop(missing)
    _save_curve_result(case, 'eval')
    report = case.module.compare(case.study, case.reference)
    evaluation = next(row for row in report['runs'] if row['mode'] == 'eval')
    assert report['errors']
    assert evaluation['accuracy_pct'] == [80.0]
    assert evaluation.get('best_source_audit', {}).get('passed') is not True
    assert not report['complete'] and not report['final_gates_applied']


@pytest.mark.parametrize('field,value', [
    ('resume_count', 0), ('resume_count', 2), ('resume_count', -1),
    ('resume_count', True), ('resume_count', 1.0),
    ('resume_path', None), ('resume_path', 'another-train-best.pt'),
    ('step', 200), ('step', True), ('step', 400.0)])
def test_curve_eval_rejects_wrong_scientific_best_source(curve_audit_case, field, value):
    case = curve_audit_case
    _write_curve_legacy_logs(case)
    case.results['eval']['execute'][field] = value
    _save_curve_result(case, 'eval')
    report = case.module.compare(case.study, case.reference)
    evaluation = next(row for row in report['runs'] if row['mode'] == 'eval')
    assert report['errors']
    assert evaluation.get('best_source_audit', {}).get('passed') is not True
    assert all(not row['gates_applied'] for row in report['experiments'])


def test_curve_eval_matches_scientific_best_using_actual_run_directory(curve_audit_case):
    case = curve_audit_case
    relocated = case.roots['eval'].with_name('eval-artifacts')
    case.roots['eval'].rename(relocated)
    case.roots['eval'] = relocated
    case.entries['eval']['run_dir'] = relocated.name
    _save_curve_index(case)
    for mode in ('train', 'eval'):
        _write_curve_jsonl(case, mode)
    before = {path: path.read_bytes() for path in case.study.rglob('*') if path.is_file()}
    report = case.module.compare(case.study, case.reference)
    evaluation = next(row for row in report['runs'] if row['mode'] == 'eval')
    assert report['errors'] == []
    assert evaluation['best_source_audit']['passed'] is True
    assert evaluation['best_source_audit']['eval_loads_matching_train_best'] is True
    assert not report['complete'] and not report['final_gates_applied']
    assert {path: path.read_bytes() for path in case.study.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('field,value', [('id', 'another-eval'),
                                        ('source_digest', 'old-source'), ('version', 'old-version')])
def test_curve_eval_identity_must_match_current_research_source(curve_audit_case, field, value):
    case = curve_audit_case
    _write_curve_legacy_logs(case)
    case.results['eval']['identity'][field] = value
    _save_curve_result(case, 'eval')
    report = case.module.compare(case.study, case.reference)
    assert report['errors']
    assert not report['final_gates_applied']


def test_curve_jsonl_message_is_not_a_resume_event(curve_audit_case):
    case = curve_audit_case
    for mode in ('train', 'eval'):
        path = _write_curve_jsonl(case, mode, restored=False)
        row = json.loads(path.read_text(encoding='utf-8'))
        row['message'] = f'[resume] target={case.best_path} epoch=0 step=400'
        path.write_text(json.dumps(row) + '\n', encoding='utf-8')
    report = case.module.compare(case.study, case.reference)
    evaluation = next(row for row in report['runs'] if row['mode'] == 'eval')
    assert report['errors'] == []
    assert evaluation['resume_event_count'] == 0
    assert evaluation['best_source_audit']['passed'] is True


def test_curve_first_error_mirror_does_not_duplicate_or_supply_diagnostics(curve_audit_case):
    case = curve_audit_case
    source = _write_curve_jsonl(case, 'eval')
    source.with_name('events.first-error.jsonl').write_bytes(source.read_bytes())
    row = _inspect_curve(case, 'eval')
    assert row['errors'] == [] and row['resume_event_count'] == 1
    source.unlink()
    row = _inspect_curve(case, 'eval')
    assert row['errors'] and row['resume_event_count'] == 0


@pytest.mark.parametrize('damage', ['malformed', 'invalid_utf8', 'read_io'])
def test_curve_bad_diagnostics_preserve_science_and_healthy_peer_readonly(
        curve_audit_case, monkeypatch, damage):
    case = curve_audit_case
    _write_curve_jsonl(case, 'train')
    broken = _write_curve_jsonl(case, 'eval')
    if damage == 'malformed':
        broken.write_bytes(b'{"event":\n')
    elif damage == 'invalid_utf8':
        broken.write_bytes(b'\xff\n')
    else:
        original = Path.read_text
        def fail_diagnostics(path, *args, **kwargs):
            if path == broken:
                raise OSError('injected diagnostic read failure')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', fail_diagnostics)
    before = {path: path.read_bytes() for path in case.study.rglob('*') if path.is_file()}
    report = case.module.compare(case.study, case.reference)
    rows = {row['mode']: row for row in report['runs']}
    assert rows['eval']['status'] == 'succeeded'
    assert rows['eval']['metrics'] == case.results['eval']['metrics']
    assert rows['eval']['optimizer_steps'] == [400] and rows['eval']['accuracy_pct'] == [80.0]
    assert rows['eval']['best_source_audit']['resume_evidence_source'] == 'result.execute'
    assert rows['eval']['best_source_audit']['passed'] is True
    assert rows['eval']['errors'] and not rows['train']['errors']
    assert rows['train']['metrics'] == case.results['train']['metrics']
    assert rows['train']['optimizer_steps'] == case.module.STEPS
    assert rows['train']['accuracy_pct'] == [80.0] * 400
    assert report['experiments'][0]['mean_accuracy_pct'] == [80.0] * 400
    assert report['experiments'][0]['seeds_with_observations'] == [0]
    assert report['errors'] and report['status'] == 'invalid'
    assert not report['complete'] and not report['final_gates_applied'] and report['passed'] is None
    assert {path: path.read_bytes() for path in case.study.rglob('*') if path.is_file()} == before


def test_curve_closed_execution_has_complete_evidence(curve_audit_case):
    case = curve_audit_case
    _write_curve_jsonl(case, 'eval')
    row = _inspect_curve(case, 'eval')
    assert row['status'] == row['scientific_status'] == 'succeeded'
    assert row['derived_status'] == 'succeeded' and row['evidence_complete'] is True
    assert row['busy'] is False and row['errors'] == []
    assert row['metrics'] == case.results['eval']['metrics']
    assert row['optimizer_steps'] == [400] and row['accuracy_pct'] == [80.0]


@pytest.mark.parametrize('damage,status,busy', [
    ('owner', 'unknown', True), ('candidate_failed', 'failed', False),
    ('outcome_mismatch', 'unknown', False), ('outcome_typed', 'unknown', False),
    ('outcome_missing', 'unknown', False), ('invalid_attempt', 'unknown', False),
    ('identity_mismatch', 'unknown', False), ('legacy', 'legacy', False)])
def test_curve_execution_projection_preserves_science_without_final_gate(
        curve_audit_case, damage, status, busy):
    case = curve_audit_case
    for mode in ('train', 'eval'):
        _write_curve_jsonl(case, mode)
    root = case.roots['eval']
    index = root / 'execution.json'
    record = json.loads(index.read_text(encoding='utf-8'))
    outcome = root / 'attempts' / record['attempt'] / 'outcome.json'
    if damage == 'owner':
        (root / '.execution').mkdir()
    elif damage == 'candidate_failed':
        record.update(candidate_status='failed', evidence_complete=False, derived_status='failed',
                      error={'type': 'RuntimeError', 'message': '[redacted]'})
        index.write_text(json.dumps(record), encoding='utf-8')
        outcome.write_text(json.dumps(record), encoding='utf-8')
    elif damage == 'outcome_mismatch':
        outcome.write_text(json.dumps({**record, 'candidate_status': 'failed'}), encoding='utf-8')
    elif damage == 'outcome_typed':
        outcome.write_text(json.dumps({**record, 'evidence_complete': 1}), encoding='utf-8')
    elif damage == 'outcome_missing':
        outcome.unlink()
    elif damage == 'invalid_attempt':
        index.write_text(json.dumps({**record, 'attempt': 'x' * 32}), encoding='utf-8')
    elif damage == 'identity_mismatch':
        record['identity'] = {**record['identity'], 'source_digest': 'another-source'}
        index.write_text(json.dumps(record), encoding='utf-8')
        outcome.write_text(json.dumps(record), encoding='utf-8')
    else:
        index.unlink()
    before = {path: path.read_bytes() for path in case.study.rglob('*') if path.is_file()}
    row = _inspect_curve(case, 'eval')
    assert row['status'] == status and row['busy'] is busy
    assert row['scientific_status'] == 'succeeded' and row['evidence_complete'] is False
    if damage == 'legacy':
        assert row['execution_status'] == 'legacy_unindexed'
    assert row['metrics'] == case.results['eval']['metrics']
    assert row['optimizer_steps'] == [400] and row['accuracy_pct'] == [80.0]
    report = case.module.compare(case.study, case.reference)
    assert report['errors'] and not report['complete'] and not report['final_gates_applied']
    assert all(not experiment['gates_applied'] for experiment in report['experiments'])
    assert {path: path.read_bytes() for path in case.study.rglob('*') if path.is_file()} == before
