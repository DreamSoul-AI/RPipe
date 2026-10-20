from pathlib import Path

import pytest

from rpipe.structure.artifact import artifact_layout, load_result, write_config, validate_result
from rpipe.flow import FlowContext, FlowRunner

pytestmark = [
    pytest.mark.integration,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.flow_layer,
    pytest.mark.module_runner,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def test_full_flow_writes_succeeded_status(tmp_path: Path):
    """FlowRunner from prepare through process writes status succeeded."""
    layout = artifact_layout(tmp_path, 'seed_0')
    write_config(
        layout.config_path,
        {
            'slug': 'seed_0',
            'seed': 0,
            'data': {'name': 'Toy', 'source': 'stub'},
            'model': {'name': 'linear'},
            'algorithm': {'mode': 'train', 'num_steps': 2},
            'system': {'device': 'cpu'},
        },
    )
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    result_path = FlowRunner().run(ctx)
    assert result_path.is_file()
    result = load_result(result_path)
    assert result['status'] == 'succeeded'
    assert result['structure']['data']['source'] == 'stub'
    assert result['structure']['data']['stub'] is True
    assert ctx.control is not None
    assert ctx.control.id
    assert ctx.control.seed == 0
    assert 'train_loss' in result.get('metrics', {}) or 'accuracy' in result.get('metrics', {})
    assert (tmp_path / 'runs' / 'seed_0' / 'process.json').is_file()
    assert not (tmp_path / 'process.json').is_file()


def test_failed_flow_writes_failed_result(tmp_path: Path):
    """A failing execute phase writes status failed and an [error] traceback."""
    layout = artifact_layout(tmp_path, 'bad_run')
    write_config(
        layout.config_path,
        {
            'seed': 0,
            'data': {'name': 'Toy', 'source': 'stub'},
            'model': {'name': 'linear'},
            'algorithm': {'mode': 'train'},
            'system': {'device': 'cpu'},
        },
    )
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})

    def boom(_ctx):
        raise RuntimeError('boom')

    import rpipe.flow.execute as execute_mod

    original = execute_mod.run
    execute_mod.run = boom
    try:
        with pytest.raises(RuntimeError, match='boom'):
            FlowRunner().run(ctx)
    finally:
        execute_mod.run = original

    assert layout.result_path.is_file()
    result = load_result(layout.result_path)
    assert result['status'] == 'failed'
    assert 'boom' in result['error']
    assert validate_result(result) == []
    log = (layout.assets_dir / 'logs' / 'run.log').read_text(encoding='utf-8')
    assert '[flow] start phases=' in log
    assert '[error] phase=execute status=failed RuntimeError: boom' in log
    assert '[error] Traceback (most recent call last):' in log
    assert 'RuntimeError: boom' in log
    assert '[flow] succeeded' not in log


def test_prepare_failure_still_writes_traceback_to_run_log(tmp_path: Path):
    """A failing prepare phase still leaves the traceback in run.log."""
    layout = artifact_layout(tmp_path, 'missing_cfg')
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    with pytest.raises(Exception):
        FlowRunner().run(ctx)
    log = (layout.assets_dir / 'logs' / 'run.log').read_text(encoding='utf-8')
    assert '[flow] start phases=' in log
    assert '[error] phase=prepare status=failed' in log
    assert '[error] Traceback (most recent call last):' in log
    assert '[flow] succeeded' not in log


@pytest.mark.parametrize('layer,mapping', [
    ('data', {'name': 'Typo', 'source': 'torch'}),
    ('data', {'name': 'Toy', 'source': 'torch'}),
    ('model', {'name': 'Typo', 'source': 'custom_torch'}),
    ('model', {'name': 'linear', 'source': 'not-installed-source'}),
])
def test_invalid_configuration_writes_failed_result(tmp_path, layer, mapping):
    """Invalid data/model declaration → prepare failure → failed result, never succeeded."""
    layout = artifact_layout(tmp_path, 'invalid')
    config = {
        'seed': 0,
        'data': {'name': 'Toy', 'source': 'stub'},
        'model': {'name': 'linear'},
        'algorithm': {'mode': 'train', 'num_steps': 2},
        'system': {'device': 'cpu'},
    }
    config[layer] = mapping
    write_config(layout.config_path, config)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    with pytest.raises(ValueError, match=f'unknown {layer} name/source'):
        FlowRunner().run(ctx)
    result = load_result(layout.result_path)
    assert result['status'] == 'failed'
    assert result['metrics'] == {}
    log = (layout.assets_dir / 'logs' / 'run.log').read_text(encoding='utf-8')
    assert 'phase=prepare status=failed' in log
    assert '[flow] succeeded' not in log


@pytest.mark.parametrize('old_success', [False, True])
@pytest.mark.parametrize('stage', ['initialization', 'start_record', 'log_directory'])
def test_logger_start_failure_still_publishes_failed_result(tmp_path, monkeypatch, old_success, stage):
    """Startup logging failures propagate and still invalidate an old writable success."""
    from rpipe.structure.artifact import write_result
    import rpipe.structure.system.logger as logger_mod
    import rpipe.flow.collect as collect_mod

    layout = artifact_layout(tmp_path, 'startup')
    peer = artifact_layout(tmp_path, 'peer')
    peer.root.mkdir(parents=True, exist_ok=True)
    peer.result_path.write_bytes(b'peer result must survive')
    if old_success:
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    original = PermissionError('startup log unavailable')
    phase_calls = []
    monkeypatch.setattr(collect_mod, 'run', lambda ctx: phase_calls.append(ctx))
    if stage == 'initialization':
        def unavailable(*args, **kwargs):
            raise original
        monkeypatch.setattr(logger_mod.Logger, '__init__', unavailable)
    elif stage == 'start_record':
        def unavailable(*args, **kwargs):
            raise original
        monkeypatch.setattr(logger_mod.Logger, 'info', unavailable)
    else:
        (layout.assets_dir / 'logs' / 'run.log').mkdir(parents=True)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    with pytest.raises(OSError) as caught:
        FlowRunner(phases=('collect',)).run(ctx)
    assert stage == 'log_directory' or caught.value is original
    assert not phase_calls
    assert peer.result_path.read_bytes() == b'peer result must survive'
    if stage == 'log_directory':
        assert (layout.assets_dir / 'logs' / 'run.log').is_dir()
    result = load_result(layout.result_path)
    assert result['status'] == 'failed'
    assert str(caught.value) in result['error']
    assert validate_result(result) == []


@pytest.mark.parametrize('old_success', [False, True])
@pytest.mark.parametrize('phase', ['collect', 'process'])
def test_exception_logger_setup_cannot_replace_phase_error(tmp_path, monkeypatch, old_success, phase):
    """Diagnostic logger failure preserves the original error and existing success policy."""
    from importlib import import_module
    from rpipe.structure.artifact import write_result
    import rpipe.structure.system.logger as logger_mod

    layout = artifact_layout(tmp_path, 'diagnostic')
    if old_success:
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    before = layout.result_path.read_bytes() if old_success else None
    original = RuntimeError('phase failed first')
    diagnostic = PermissionError('diagnostic logger unavailable')
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    def unavailable(*args, **kwargs):
        raise diagnostic
    def fail(current):
        current.state.pop('logger', None)
        monkeypatch.setattr(logger_mod.Logger, '__init__', unavailable)
        raise original
    monkeypatch.setattr(import_module('rpipe.flow.' + phase), 'run', fail)
    peer = layout.root / 'peer.bin'
    peer.parent.mkdir(parents=True, exist_ok=True)
    peer.write_bytes(b'peer')
    with pytest.raises(RuntimeError) as caught:
        FlowRunner(phases=(phase,)).run(ctx)
    assert caught.value is original
    assert peer.read_bytes() == b'peer'
    if phase == 'process' and old_success:
        assert layout.result_path.read_bytes() == before
    else:
        result = load_result(layout.result_path)
        assert result['status'] == 'failed'
        assert result['error'] == 'RuntimeError: phase failed first'
        assert validate_result(result) == []


@pytest.mark.parametrize('old_success', [False, True])
@pytest.mark.parametrize('primary_type', [RuntimeError, ValueError])
@pytest.mark.parametrize('secondary_kind', ['publication', 'serialization'])
def test_train_finalizer_keeps_original_failed_result(tmp_path, monkeypatch, old_success, primary_type, secondary_kind):
    """Runner execute -> native train iterator failure plus state refusal -> original failed Result/log."""
    from types import SimpleNamespace
    import torch
    from rpipe.structure.artifact import _atomic, write_result
    from rpipe.structure.algorithm.config import AlgorithmConfig
    from rpipe.structure.algorithm.train import TrainAlgorithm
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.system.factory import SystemFactory
    from rpipe.structure.system.config import SystemConfig

    layout = artifact_layout(tmp_path, 'train-failure')
    if old_success:
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    tracker = AlgorithmTracker(layout.assets_dir)
    tracker.flush_state()
    old_tracker = tracker.state_path.read_bytes()
    peer = layout.root / 'evidence.bin'
    peer.write_bytes(b'preserve peer')
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), layout.assets_dir)
    primary = primary_type('original train failure')
    calls = []
    def batches(split):
        calls.append(split)
        raise primary
    data = SimpleNamespace(meta={'train_size': 1, 'batch_size': 1}, steps_per_epoch=lambda: 1, iter_batches=batches)
    if secondary_kind == 'publication':
        replace = _atomic.atomic_replace
        def refuse(source, target):
            if target == tracker.state_path:
                raise PermissionError('tracker publication refused')
            return replace(source, target)
        monkeypatch.setattr(_atomic, 'atomic_replace', refuse)
    else:
        state_dict = tracker.state_dict
        def bad_state():
            return {**state_dict(), 'unserializable': object()}
        monkeypatch.setattr(tracker, 'state_dict', bad_state)
    ctx = FlowContext(tmp_path, layout, {}, control=SimpleNamespace(to_dict=lambda: {}), state={
        'data': data, 'model': SimpleNamespace(module=torch.nn.Linear(2, 2)), 'system': system,
        'tracker': tracker, 'algorithm': TrainAlgorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 1})),
        'logger': system.logger,
    })
    with pytest.raises(Exception) as caught:
        FlowRunner(phases=('execute',)).run(ctx)
    result = load_result(layout.result_path)
    text = system.logger.path.read_text(encoding='utf-8')
    assert calls == ['train']
    assert tracker.state_path.read_bytes() == old_tracker
    assert peer.read_bytes() == b'preserve peer'
    assert not list(layout.assets_dir.glob('*.tmp'))
    assert not list(tracker.state_path.parent.glob('*.tmp'))
    assert caught.value is primary
    assert result['status'] == 'failed'
    assert result['error'] == f'{primary_type.__name__}: original train failure'
    assert 'tracker final flush failed' in text and 'original train failure' in text
    assert validate_result(result) == []


@pytest.mark.parametrize('old_success', [False, True])
@pytest.mark.parametrize('primary', [False, True], ids=['body-success', 'body-failure'])
@pytest.mark.parametrize('refusal', ['none', 'publication', 'encoding'])
def test_execute_final_tracker_write_failure_blocks_success_result(tmp_path, monkeypatch, old_success, primary, refusal):
    """Actual execute/collect/summarize/write with real tracker: no success may be finalized while final state write is refused."""
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.artifact import _atomic, write_result
    from rpipe.structure.control.control import control_from_config

    layout = artifact_layout(tmp_path, 'synthetic')
    if old_success:
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    tracker = AlgorithmTracker(layout.assets_dir)
    tracker.flush_state()
    old_tracker = tracker.state_path.read_bytes()
    peer = tracker.state_path.with_name('peer.tmp')
    peer.write_bytes(b'peer evidence')
    original = RuntimeError('original synthetic Algorithm failure')
    denied = PermissionError('final tracker publication refused')
    class SyntheticAlgorithm:
        mode = 'eval'
        def run(self, data, model, system, ledger):
            assert data is None and model is None and system is None
            ledger.append('test', values={'Accuracy': 75.0})
            if primary:
                raise original
            return {'mode': 'eval', 'accuracy': 75.0}
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.control = control_from_config({'algorithm': {'mode': 'eval'}})
    ctx.state.update(algorithm=SyntheticAlgorithm(), tracker=tracker)
    replace = _atomic.atomic_replace
    def refuse(source, target):
        if target == tracker.state_path:
            raise denied
        return replace(source, target)
    if refusal == 'publication':
        monkeypatch.setattr(_atomic, 'atomic_replace', refuse)
    elif refusal == 'encoding':
        monkeypatch.setattr(tracker, 'state_dict', lambda: {'bad': object()})
    if primary or refusal != 'none':
        expected = RuntimeError if primary else PermissionError if refusal == 'publication' else TypeError
        with pytest.raises(expected) as caught:
            FlowRunner(phases=['execute', 'collect', 'summarize', 'write']).run(ctx)
        if primary or refusal == 'publication':
            assert caught.value is (original if primary else denied)
        result = load_result(layout.result_path)
        assert result['status'] == 'failed'
        assert result['error'] == f'{type(caught.value).__name__}: {caught.value}'
        assert 'collected' not in ctx.state and 'result_draft' not in ctx.state
        log = (layout.assets_dir / 'logs/run.log').read_text(encoding='utf-8')
        assert type(caught.value).__name__ in log
    else:
        FlowRunner(phases=['execute', 'collect', 'summarize', 'write']).run(ctx)
        result = load_result(layout.result_path)
        assert result['status'] == 'succeeded' and result['metrics']['accuracy'] == 75.0
        assert tracker.state_path.read_bytes() != old_tracker
    if refusal != 'none':
        assert tracker.state_path.read_bytes() == old_tracker
    assert peer.read_bytes() == b'peer evidence'
    assert set(tracker.state_path.parent.glob('*.tmp')) == {peer}
    assert validate_result(result) == []


@pytest.mark.parametrize('old_success', [False, True])
@pytest.mark.parametrize('phase', ['collect', 'process'])
@pytest.mark.parametrize('failure', ['stdout', 'file-write', 'none'])
def test_terminal_log_failure_uses_existing_result_and_diagnostic_policy(tmp_path, monkeypatch, old_success, phase, failure):
    """Terminal IO failures still reach Runner evidence handling after all phase bodies."""
    import builtins
    from importlib import import_module
    from rpipe.structure.artifact import write_result
    from rpipe.structure.system.logger import Logger

    layout = artifact_layout(tmp_path, 'terminal')
    peer = tmp_path / 'peer.bin'
    peer.write_bytes(b'peer must survive')
    if old_success:
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    before = layout.result_path.read_bytes() if old_success else None
    logger = Logger(layout.assets_dir)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.state['logger'] = logger
    primary = PermissionError('terminal log refused')
    calls = []
    monkeypatch.setattr(import_module('rpipe.flow.' + phase), 'run', lambda current: calls.append(current))
    original_print, original_open = builtins.print, Path.open

    def emit(*args, **kwargs):
        if args and str(args[0]).rstrip().endswith(' [flow] succeeded'):
            raise primary
        return original_print(*args, **kwargs)

    class Stream:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, text):
            if text.rstrip().endswith(' [flow] succeeded'):
                raise primary
            return self.handle.write(text)

    def open_stream(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return Stream(handle) if path == logger.path and args and args[0] == 'a' else handle

    if failure == 'stdout':
        monkeypatch.setattr(builtins, 'print', emit)
    elif failure == 'file-write':
        monkeypatch.setattr(Path, 'open', open_stream)
    if failure == 'none':
        assert FlowRunner(phases=(phase,)).run(ctx) == layout.result_path
        assert ' [flow] succeeded' in logger.path.read_text(encoding='utf-8')
        assert not layout.result_path.exists() if before is None else layout.result_path.read_bytes() == before
    else:
        with pytest.raises(PermissionError) as caught:
            FlowRunner(phases=(phase,)).run(ctx)
        assert caught.value is primary
        text = logger.path.read_text(encoding='utf-8')
        assert f'[error] phase={phase} status=failed PermissionError: terminal log refused' in text
        if phase == 'process' and old_success:
            assert layout.result_path.read_bytes() == before
        else:
            result = load_result(layout.result_path)
            assert result['status'] == 'failed'
            assert result['error'] == 'PermissionError: terminal log refused'
            assert validate_result(result) == []
    assert calls == [ctx]
    assert peer.read_bytes() == b'peer must survive'


@pytest.mark.parametrize('utf8_cut', [False, True])
def test_logger_after_interruption_keeps_error_identity_in_native_readout(tmp_path, utf8_cut):
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.artifact.index import write_index
    from rpipe.structure.artifact.readout.logs import event_lines
    from rpipe.structure.artifact.readout.status import _note_from_log

    layout = artifact_layout(tmp_path, 'interrupted')
    logger = Logger(layout.assets_dir)
    write_index(tmp_path, {'study': 'synthetic', 'experiments': [
        {'factors': {}, 'runs': [{'id': 'interrupted', 'run_dir': 'interrupted'}]}]})
    partial = b'2001-01-01T00:00:00.000+00:00 INFO  interrupted [flow] cut '
    if utf8_cut:
        partial += b'\xe4'
    logger.path.write_bytes(partial)
    logger.error('current failure')
    assert logger.path.read_bytes().startswith(partial)
    lines = [line for line in event_lines(tmp_path) if 'current failure' in line]
    assert len(lines) == 1
    assert '[flow]' not in lines[0]
    assert '[error] current failure' in lines[0]
    assert _note_from_log(logger.path, errors_only=True) == '[error] current failure'


@pytest.mark.parametrize('old_success', [False, True])
@pytest.mark.parametrize('phase', ['collect', 'process'])
def test_runner_partial_terminal_write_remains_a_separate_error_event(tmp_path, monkeypatch, old_success, phase):
    from importlib import import_module
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.artifact import write_result
    from rpipe.structure.artifact.index import write_index
    from rpipe.structure.artifact.readout.logs import event_lines
    from rpipe.structure.artifact.readout.status import _note_from_log

    layout = artifact_layout(tmp_path, 'partial')
    logger = Logger(layout.assets_dir)
    logger.path.write_bytes(b'older evidence\n')
    write_index(tmp_path, {'experiments': [{'factors': {}, 'runs': [{'id': 'partial', 'run_dir': 'partial'}]}]})
    if old_success:
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    before = layout.result_path.read_bytes() if old_success else None
    peer = tmp_path / 'peer.bin'
    peer.write_bytes(b'peer')
    primary = PermissionError('terminal partial write')
    native = Path.open
    fragments = []

    class Stream:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, text):
            if text.rstrip().endswith(' [flow] succeeded'):
                fragment = text[:-4]
                fragments.append(fragment)
                self.handle.write(fragment)
                self.handle.flush()
                raise primary
            return self.handle.write(text)

    def opened(path, *args, **kwargs):
        handle = native(path, *args, **kwargs)
        return Stream(handle) if path == logger.path and args and args[0] == 'a' else handle

    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(import_module('rpipe.flow.' + phase), 'run', lambda current: None)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.state['logger'] = logger
    with pytest.raises(PermissionError) as caught:
        FlowRunner(phases=(phase,)).run(ctx)
    assert caught.value is primary
    expected = f'[error] phase={phase} status=failed PermissionError: terminal partial write'
    summaries = [line for line in event_lines(tmp_path) if expected in line]
    assert len(summaries) == 1 and '[flow]' not in summaries[0]
    assert 'terminal partial write' in _note_from_log(logger.path, errors_only=True)
    raw = logger.path.read_bytes()
    assert raw.startswith(b'older evidence\n')
    assert len(fragments) == 1 and fragments[0].encode() in raw
    if phase == 'process' and old_success:
        assert layout.result_path.read_bytes() == before
    else:
        result = load_result(layout.result_path)
        assert result['status'] == 'failed' and result['error'] == 'PermissionError: terminal partial write'
    assert peer.read_bytes() == b'peer'


@pytest.mark.parametrize('kind', ['info', 'report', 'error'])
def test_multiline_phase_log_body_cannot_change_native_readout_event(tmp_path, monkeypatch, kind):
    from importlib import import_module
    import re
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.artifact.index import write_index
    from rpipe.structure.artifact.readout.logs import event_lines
    from rpipe.structure.artifact.readout.status import _note_from_log

    layout = artifact_layout(tmp_path, 'multiline')
    logger = Logger(layout.assets_dir)
    write_index(tmp_path, {'experiments': [{'factors': {}, 'runs': [{'id': 'multiline', 'run_dir': 'multiline'}]}]})
    embedded = '[flow]' if kind == 'error' else '[error]'
    message = 'ordinary detail\n' + embedded + ' body continuation'

    def phase(current):
        if kind == 'report':
            logger.report(AlgorithmTracker(layout.assets_dir / 'tracker'), 'train', {'epoch': 1, 'detail': message})
        else:
            getattr(logger, kind)(message)

    monkeypatch.setattr(import_module('rpipe.flow.collect'), 'run', phase)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.state['logger'] = logger
    FlowRunner(phases=('collect',)).run(ctx)
    expected = {'info': 'flow', 'report': 'epoch', 'error': 'error'}[kind]
    rows = [line for line in event_lines(tmp_path) if 'ordinary detail' in line or 'body continuation' in line]
    assert len(rows) == 2
    header = re.compile(r'^\S+\s+(?:INFO|WARN|ERROR)\s+multiline\s+\[(\w+)\]')
    assert all(header.match(line) and header.match(line).group(1) == expected for line in rows)
    note = _note_from_log(logger.path, errors_only=True)
    assert note.startswith('[error]') if kind == 'error' else note == '-'


@pytest.mark.parametrize('run_id,event,custom_log', [
    (run_id, event, custom) for run_id in ('only', 'name with spaces', 'name [error] Traceback marker')
    for event in ('warn', 'split', 'flow', 'error') for custom in (False, True)
] + [('only', event, False) for event in ('metric', 'time', 'ckpt', 'resume')])
def test_native_log_event_field_is_not_replaced_by_identity_or_body(tmp_path, monkeypatch, run_id, event, custom_log):
    from importlib import import_module
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.artifact import write_result
    from rpipe.structure.artifact.index import write_index
    from rpipe.structure.artifact.readout.logs import event_lines
    from rpipe.structure.artifact.readout.status import list_runs, _note_from_log

    layout = artifact_layout(tmp_path, run_id)
    logger = Logger(layout.assets_dir)
    if custom_log:
        logger.path = layout.root / 'custom.log'
    peer = artifact_layout(tmp_path, 'peer')
    peer_logger = Logger(peer.assets_dir)
    peer_logger.info('peer flow')
    write_index(tmp_path, {'experiments': [{'factors': {'algorithm.mode': 'train'}, 'runs': [
        {'id': run_id, 'seed': 0, 'log': logger.path.relative_to(tmp_path).as_posix()},
        {'id': 'peer', 'seed': 0}]}]})
    write_result(layout.result_path, {'status': 'failed', 'control': {}, 'metrics': {}, 'paths': {}, 'error': 'real primary'})
    write_result(peer.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 2}, 'paths': {}})
    payload = 'quoted [error] payload'

    def phase(current):
        logger.error('real primary')
        if event == 'warn':
            logger.warning(payload)
        elif event == 'split':
            logger.report(AlgorithmTracker(layout.assets_dir / 'tracker'), 'test', {'detail': payload})
        else:
            logger.info(payload, event=event)

    monkeypatch.setattr(import_module('rpipe.flow.collect'), 'run', phase)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.state['logger'] = logger
    FlowRunner(phases=('collect',)).run(ctx)
    entries = set(tmp_path.rglob('*'))
    before = {path: path.read_bytes() for path in entries if path.is_file()}
    listing = list_runs(tmp_path)
    expected = '[error] ' + payload if event == 'error' else '[error] real primary'
    assert listing['runs'][0]['note'] == expected
    assert listing['runs'][1]['status'] == 'succeeded' and listing['counts']['planned'] == 2
    lines = event_lines(tmp_path)
    assert any('peer flow' in line for line in lines)
    assert any('real primary' in line for line in lines)
    quoted = [line for line in lines if payload in line]
    assert len(quoted) == (0 if event in ('split', 'metric', 'time') else 1)
    if quoted:
        assert f' {run_id} [{event}] ' in quoted[0]
    if not custom_log:
        assert _note_from_log(logger.path, errors_only=True) == expected
    assert {path: path.read_bytes() for path in before} == before
    assert set(tmp_path.rglob('*')) == entries


@pytest.mark.parametrize('legacy', [False, True], ids=['native', 'legacy'])
@pytest.mark.parametrize('message,hidden', [
    ('File cache missing', False), ('Traceback writing failed', False),
    ('File "data.csv" is missing', False),
    ('Traceback (most recent call last): unable to persist', False),
    ('File "data.csv", line nope, in worker', False),
    ('File "data.csv", line 12 was unreadable', False),
    ('Traceback (most recent call last):', True),
    ('File "module.py", line 12, in worker', True),
    ('File "module.py", line 12', True),
])
def test_readout_only_suppresses_standard_traceback_shapes(tmp_path, monkeypatch, legacy, message, hidden):
    from importlib import import_module
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.artifact import write_result
    from rpipe.structure.artifact.index import write_index
    from rpipe.structure.artifact.readout.logs import event_lines
    from rpipe.structure.artifact.readout.status import list_runs

    layout = artifact_layout(tmp_path, 'only')
    logger = Logger(layout.assets_dir)
    peer = artifact_layout(tmp_path, 'peer')
    peer_logger = Logger(peer.assets_dir)
    peer_logger.info('peer flow')
    write_index(tmp_path, {'experiments': [{'factors': {'algorithm.mode': 'train'}, 'runs': [
        {'id': 'only', 'seed': 0}, {'id': 'peer', 'seed': 0}]}]})
    write_result(layout.result_path, {'status': 'failed', 'control': {}, 'metrics': {}, 'paths': {}, 'error': message})
    write_result(peer.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})

    def phase(current):
        if legacy:
            with logger.path.open('a', encoding='utf-8') as handle:
                handle.write('legacy [error] ' + message + '\n')
        else:
            logger.error(message)

    monkeypatch.setattr(import_module('rpipe.flow.collect'), 'run', phase)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.state['logger'] = logger
    FlowRunner(phases=('collect',)).run(ctx)
    entries = set(tmp_path.rglob('*'))
    before = {path: path.read_bytes() for path in entries if path.is_file()}
    listing = list_runs(tmp_path)
    assert listing['runs'][0]['note'] == ('-' if hidden else '[error] ' + message)
    assert listing['runs'][1]['status'] == 'succeeded'
    rows = event_lines(tmp_path)
    assert any('peer flow' in row for row in rows)
    assert sum(message in row for row in rows) == (0 if hidden else 1)
    assert {path: path.read_bytes() for path in before} == before
    assert set(tmp_path.rglob('*')) == entries


@pytest.mark.parametrize('declaration', [
    'malformed', 'nonmapping', 'bad-shared', 'bad-root', 'decode', 'read-io',
    'valid-default', 'valid-custom', 'missing',
])
@pytest.mark.parametrize('prior', ['fresh', 'success', 'known-shared', 'success-known-shared'])
def test_failed_result_survives_unavailable_shared_path_metadata(tmp_path, monkeypatch, declaration, prior):
    from rpipe.structure.artifact import write_result

    layout = artifact_layout(tmp_path, 'target')
    peer = artifact_layout(tmp_path, 'peer')
    write_result(peer.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 7}, 'paths': {}})
    peer_before = {p: p.read_bytes() for p in peer.root.rglob('*') if p.is_file()}
    if prior in ('success', 'success-known-shared'):
        write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 9}, 'paths': {}})
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    known = prior in ('known-shared', 'success-known-shared')
    if known:
        ctx.state['result_draft'] = {'paths': {'shared': 'known-shared-input'}}
    contents = {
        'malformed': b'shared: [\n', 'nonmapping': b'[]\n',
        'bad-shared': b'shared: []\n', 'bad-root': b'shared: {root: []}\n',
        'decode': b'\xff\xfe', 'read-io': b'{}\n',
        'valid-default': b'{}\n', 'valid-custom': b'shared: {root: custom-inputs}\n',
    }
    declaration_path = tmp_path / 'study.yaml'
    if declaration != 'missing':
        declaration_path.write_bytes(contents[declaration])
    original_read = Path.open
    io_error = PermissionError('declaration temporarily unreadable')
    def read(path, *args, **kwargs):
        if kwargs.get('mode', args[0] if args else 'r') not in ('r', 'rt'):
            return original_read(path, *args, **kwargs)
        if declaration == 'read-io' and path == declaration_path:
            raise io_error
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', read)
    with pytest.raises(Exception) as caught:
        FlowRunner(phases=('prepare',)).run(ctx)
    if declaration == 'read-io':
        assert caught.value is io_error
    result = load_result(layout.result_path)
    assert result['status'] == 'failed'
    assert result['error'] == f'{type(caught.value).__name__}: {caught.value}'
    assert validate_result(result) == []
    for key, expected in [('artifact', layout.root), ('config', layout.config_path),
                          ('assets', layout.assets_dir), ('result', layout.result_path)]:
        assert result['paths'][key] == str(expected)
    if known:
        assert result['paths']['shared'] == 'known-shared-input'
    elif declaration in ('valid-default', 'valid-custom', 'missing'):
        assert result['paths']['shared'] == str(layout.shared_dir)
    else:
        assert 'shared' not in result['paths']
    assert ctx.state['result'] == result
    log = (layout.assets_dir / 'logs/run.log').read_text(encoding='utf-8')
    assert '[error] phase=' in log and '[flow] succeeded' not in log
    assert {p: p.read_bytes() for p in peer_before} == peer_before
    if declaration != 'missing':
        assert declaration_path.read_bytes() == contents[declaration]
    else:
        assert not declaration_path.exists()


@pytest.mark.parametrize('owner', ['Owner', 'Name [error] marker'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('event', ['split', 'warn', 'flow', 'error'])
def test_native_readout_keeps_samefile_alias_and_distinct_case_run_ownership(tmp_path, monkeypatch, owner, alias, custom, event):
    from importlib import import_module
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.artifact import write_result
    from rpipe.structure.artifact.index import write_index
    from rpipe.structure.artifact.readout.status import list_runs, log_path, _note_from_log
    from rpipe.structure.artifact.readout.logs import event_lines

    layout = artifact_layout(tmp_path, owner)
    index_id = owner.lower() if alias else owner
    if alias and Path(owner) != Path(index_id):
        foreign = Logger(layout.assets_dir)
        foreign.error('distinct-case foreign error')
        layout = artifact_layout(tmp_path, index_id)
        assert not layout.root.samefile(tmp_path / 'runs' / owner)
    logger = Logger(layout.assets_dir)
    if custom:
        logger.path = layout.root / 'custom.log'
    peer = artifact_layout(tmp_path, 'peer')
    Logger(peer.assets_dir).info('peer flow')
    log_ref = f'runs/{index_id}/custom.log' if custom else f'runs/{index_id}/assets/logs/run.log'
    write_index(tmp_path, {'experiments': [{'factors': {'algorithm.mode': 'train'}, 'runs': [
        {'id': index_id, 'run_dir': index_id, 'seed': 0, 'log': log_ref},
        {'id': 'peer', 'seed': 0}]}]})
    write_result(layout.result_path, {'status': 'failed', 'control': {}, 'metrics': {}, 'paths': {}, 'error': 'real primary'})
    write_result(peer.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 2}, 'paths': {}})
    payload = 'quoted [error] payload'
    def phase(ctx):
        logger.error('real primary')
        if event == 'split':
            logger.report(None, 'validation', {'note': payload})
        elif event == 'warn':
            logger.warning(payload)
        else:
            logger.info(payload, event=event)
    monkeypatch.setattr(import_module('rpipe.flow.collect'), 'run', phase)
    ctx = FlowContext(study_dir=tmp_path, layout=layout, config={})
    ctx.state['logger'] = logger
    FlowRunner(phases=('collect',)).run(ctx)
    entries = set(tmp_path.rglob('*'))
    before = {p: p.read_bytes() for p in entries if p.is_file()}
    owned = log_path(tmp_path, {'log': log_ref}, index_id)
    assert owned is not None and owned.samefile(logger.path)
    expected = '[error] ' + payload if event == 'error' else '[error] real primary'
    listing = list_runs(tmp_path)
    assert listing['runs'][0]['note'] == expected
    assert listing['runs'][1]['status'] == 'succeeded' and listing['counts']['planned'] == 2
    lines = event_lines(tmp_path)
    assert any('peer flow' in line for line in lines)
    assert any('real primary' in line for line in lines)
    assert not any('distinct-case foreign error' in line for line in lines)
    quoted = [line for line in lines if payload in line]
    assert len(quoted) == (0 if event == 'split' else 1)
    if quoted:
        assert f' [{event}] ' in quoted[0]
    if not custom:
        assert _note_from_log(logger.path, errors_only=True) == expected
    assert {p: p.read_bytes() for p in before} == before
    assert set(tmp_path.rglob('*')) == entries


@pytest.mark.parametrize('fault', ['normal', 'pipe', 'closed', 'file'])
def test_logger_peer_sink_retains_original_phase_traceback(tmp_path, stream_close_fault, fault):
    import contextlib
    import io
    import os
    from types import SimpleNamespace
    from rpipe.structure.artifact import write_result
    from rpipe.structure.control import control_from_config
    from rpipe.structure.system.logger import Logger

    layout = artifact_layout(tmp_path, 'sink-failure')
    write_result(layout.result_path, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
    logger = Logger(layout.assets_dir)
    peer = layout.root / 'peer.bin'
    peer.write_bytes(b'peer evidence')
    original = RuntimeError('original algorithm failure')
    active = [False]
    if fault == 'file':
        stream_close_fault(lambda path, mode: active[0] and path == logger.path and mode == 'a',
                           PermissionError('file log unavailable'), None)
    reader = []
    if fault in ('pipe', 'closed'):
        read_fd, write_fd = os.pipe()
        reader.append(read_fd)
        sink = io.TextIOWrapper(os.fdopen(write_fd, 'wb', buffering=0), encoding='utf-8', write_through=True)
    else:
        sink = io.StringIO()
    class Algorithm:
        mode = 'train'
        def run(self, *_args):
            active[0] = True
            if fault == 'pipe':
                os.close(reader.pop())
            elif fault == 'closed':
                sink.close()
            raise original
    config = {'id': 'sink-failure', 'seed': 0, 'data': {'name': 'Toy', 'source': 'stub'},
              'model': {'name': 'linear'}, 'algorithm': {'mode': 'train'}, 'system': {'device': 'cpu'}}
    ctx = FlowContext(tmp_path, layout, config)
    ctx.control = control_from_config(config)
    ctx.state.update(logger=logger, algorithm=Algorithm(), tracker=SimpleNamespace(flush_state=lambda: None))
    try:
        with contextlib.redirect_stdout(sink):
            with pytest.raises(RuntimeError) as caught:
                FlowRunner(['execute']).run(ctx)
        assert caught.value is original
        result = load_result(layout.result_path)
        assert result['status'] == 'failed' and result['error'] == 'RuntimeError: original algorithm failure'
        view = sink.getvalue() if fault == 'file' else logger.path.read_text(encoding='utf-8')
        assert 'original algorithm failure' in view
        assert 'Traceback (most recent call last):' in view
        assert peer.read_bytes() == b'peer evidence'
    finally:
        try:
            sink.close()
        except (OSError, ValueError):
            pass
        if reader:
            os.close(reader.pop())
