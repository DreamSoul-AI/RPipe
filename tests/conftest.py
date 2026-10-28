"""Five-dimensional test metadata, incremental evidence, and required-plan checks."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

_spec = importlib.util.spec_from_file_location('rpipe_test_entry', Path(__file__).with_name('run.py'))
_entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_entry)
save_json, append_event, write_report = _entry.save_json, _entry.append_event, _entry.write_report

LEVELS = frozenset({'unit', 'integration', 'e2e'})
TYPES = frozenset({'location', 'content', 'physical'})
PRIORITIES = frozenset({'p1', 'p2', 'p3'})
COST_CLASSES = frozenset({'c1', 'c2', 'c3', 'c4'})
RESULT_TYPES = frozenset({'categorical', 'numeric'})
RESULT_DETAILS = frozenset({'summary', 'metrics', 'samples'})
REPO = Path(__file__).resolve().parent.parent
RESULTS_ROOT = Path(os.environ.get('RPIPE_TEST_RESULTS_ROOT', Path(tempfile.gettempdir()) / 'rpipe-tests')).resolve()


def _marker_names(item: pytest.Item) -> set[str]:
    return {marker.name for marker in item.iter_markers()}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption('--cost-class', action='append', default=[], choices=sorted(COST_CLASSES),
                     help='keep tests whose cost_class is one of these values')


def _one_marker(item: pytest.Item, name: str) -> pytest.Mark | None:
    found = list(item.iter_markers(name=name))
    return found[0] if len(found) == 1 else None


def _manifest(config: pytest.Config) -> dict:
    return json.loads((config._rpipe_results_dir / 'manifest.json').read_text(encoding='utf-8'))


def _static_exemption(item: pytest.Item) -> str | None:
    for marker in item.iter_markers():
        if marker.name == 'skip' or (marker.name == 'skipif' and any(condition is True for condition in marker.args)):
            return str(marker.kwargs.get('reason') or 'explicit static skip')
    return None


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]):
    # Capture before pytest applies -m/-k, then persist the final post-filter list.
    collected = list(items)
    yield
    errors, not_selected, kept = [], [], []
    allowed_costs = set(config.getoption('--cost-class') or [])
    result_filter = config._rpipe_plan.get('selection', {}).get('result_type')
    remaining = set(items)
    for item in collected:
        names, node = _marker_names(item), item.nodeid
        for field, choices in [('level', LEVELS), ('type', TYPES), ('priority', PRIORITIES)]:
            values = names & choices
            if len(values) != 1:
                errors.append(f'{node}: need exactly one {field} in {sorted(choices)}, got {sorted(values)}')
        if 'location' in names and 'unit' not in names:
            errors.append(f'{node}: location must be paired with unit')
        cost, result = _one_marker(item, 'cost'), _one_marker(item, 'result_type')
        if cost is None or cost.kwargs.get('cost_class') not in COST_CLASSES:
            errors.append(f'{node}: need exactly one cost(cost_class=c1|c2|c3|c4)')
        if (result is None or not result.args or result.args[0] not in RESULT_TYPES
                or result.kwargs.get('detail') not in RESULT_DETAILS):
            errors.append(f'{node}: need result_type(categorical|numeric, detail=summary|metrics|samples)')
        elif result.args[0] == 'numeric' and result.kwargs.get('detail') == 'summary':
            errors.append(f'{node}: numeric results require metrics or samples detail')
        if 'physical' in names and not names & {'runtime', 'memory'}:
            errors.append(f'{node}: physical needs runtime or memory')
        if cost is not None:
            gpu_count = cost.kwargs.get('gpu_count', 1 if 'gpu' in names else 0)
            if type(gpu_count) is not int or gpu_count < 0:
                errors.append(f'{node}: gpu_count must be a nonnegative integer')
            elif gpu_count and cost.kwargs.get('cost_class') not in {'c3', 'c4'}:
                errors.append(f'{node}: real GPU cost must be c3 or c4')
            estimate = cost.kwargs.get('estimated_wall_time_ms')
            if estimate is not None and (type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate < 0):
                errors.append(f'{node}: estimated_wall_time_ms must be a finite nonnegative number or None')
        reason = None
        if item not in remaining:
            reason = 'pytest selection (-m/-k): ' + str(getattr(config.option, 'markexpr', '') or getattr(config.option, 'keyword', ''))
        elif allowed_costs and cost is not None and cost.kwargs.get('cost_class') not in allowed_costs:
            reason = 'cost_class excluded by plan: ' + ','.join(sorted(allowed_costs))
        elif result_filter and result is not None and result.args[0] != result_filter:
            reason = 'result_type excluded by plan: ' + result_filter
        if reason:
            not_selected.append({'test_id': node, 'reason': reason})
        else:
            kept.append(item)
    removed = [item for item in items if item not in kept]
    if removed:
        config.hook.pytest_deselected(items=removed)
    items[:] = kept
    config._rpipe_not_selected = not_selected
    if errors:
        raise pytest.UsageError('Architecture marker contract:\n' + '\n'.join(errors))


def pytest_configure(config: pytest.Config) -> None:
    if RESULTS_ROOT == REPO or REPO in RESULTS_ROOT.parents:
        raise pytest.UsageError('RPIPE_TEST_RESULTS_ROOT must be outside the source checkout')
    supervised = isinstance(config, pytest.Config) and os.environ.get('RPIPE_TEST_OWNER_PID') == str(os.getppid())
    supplied = os.environ.get('RPIPE_TEST_RUN_DIR') if supervised else None
    plan = json.loads(os.environ.get('RPIPE_TEST_PLAN', '{}')) if supervised else {}
    plan.setdefault('plan_id', 'ad_hoc')
    plan.setdefault('environment_id', 'cpu_ci' if os.environ.get('CI') else 'local')
    plan.setdefault('selection', {})
    plan.setdefault('required', None)
    plan.setdefault('resources', {'gpu_count': 0})
    plan.setdefault('capabilities', {'hard_memory_limit': False, 'hard_per_test_timeout': False,
                                     'descendant_supervision': False})
    if not supervised:
        plan.update(timeout_scope=None, timeout_seconds=None, retry_scope=None, retries=0,
                    budget_seconds=None, supervision_reason='direct pytest has no external timeout/retry supervisor')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_id = Path(supplied).name if supplied else f'{stamp}_{uuid.uuid4().hex[:8]}'
    out_dir = Path(supplied).resolve() if supplied else RESULTS_ROOT / run_id
    if out_dir == REPO or REPO in out_dir.parents:
        raise pytest.UsageError('test evidence must be outside the source checkout')
    out_dir.mkdir(parents=True, exist_ok=True)
    config._rpipe_run_id, config._rpipe_results_dir = run_id, out_dir
    config._rpipe_attempt = int(os.environ.get('RPIPE_TEST_ATTEMPT', '1')) if supervised else 1
    config._rpipe_plan, config._rpipe_started = plan, time.monotonic()
    manifest = _manifest(config) if (out_dir / 'manifest.json').exists() else {
        'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(), 'attempts': [],
        'collection_complete': False,
    }
    manifest.update(
        cwd=str(Path.cwd()), python=sys.version.split()[0], platform=platform.platform(),
        pytest_command=list(config.invocation_params.args),
        git_sha=os.environ.get('GITHUB_SHA') or _git_sha(),
        markexpr=getattr(config.option, 'markexpr', '') or '', plan_id=plan['plan_id'], plan=plan,
        environment_id=plan['environment_id'], cost_class=list(getattr(config.option, 'cost_class', None) or []),
        dependencies={dist.metadata['Name']: dist.version for dist in importlib.metadata.distributions() if dist.metadata['Name']},
    )
    manifest.setdefault('command', list(config.invocation_params.args))
    if not supervised:
        manifest['attempts'] = [{'attempt': 1, 'status': 'running', 'started_at': manifest['started_at']}]
    save_json(out_dir / 'manifest.json', manifest)
    for name in ('events.jsonl', 'phases.jsonl'):
        (out_dir / name).touch()
    (out_dir / 'artifacts').mkdir(exist_ok=True)


def _git_sha() -> str | None:
    try:
        metadata = REPO / '.git'
        head = metadata / 'HEAD'
        if head.is_file():
            raw = head.read_text(encoding='utf-8').strip()
            if raw.startswith('ref: '):
                ref = metadata / raw[5:].strip()
                raw = ref.read_text(encoding='utf-8').strip() if ref.is_file() else None
            if raw is not None:
                return raw if len(raw) in (40, 64) and all(c in '0123456789abcdefABCDEF' for c in raw) else None
        if not metadata.exists():
            return None
        return subprocess.run(['git', '-c', f'safe.directory={REPO}', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip() or None
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None


def pytest_collection_finish(session: pytest.Session) -> None:
    config = session.config
    manifest = _manifest(config)
    selected = [item.nodeid for item in session.items]
    if config._rpipe_attempt > 1 and manifest.get('selected') != selected:
        raise pytest.UsageError('test selection changed between batch attempts')
    exclusions = [{'test_id': item.nodeid, 'reason': reason} for item in session.items
                  if (reason := _static_exemption(item)) is not None]
    exempt_ids = {entry['test_id'] for entry in exclusions}
    required = config._rpipe_plan.get('required')
    manifest.update(selected=selected, required=list(required) if required is not None else [node for node in selected if node not in exempt_ids],
                    not_selected=getattr(config, '_rpipe_not_selected', []), exclusions=exclusions,
                    collection_complete=not bool(session.testsfailed),
                    configuration_matrix=[{'test_id': item.nodeid, 'parameter_id': getattr(getattr(item, 'callspec', None), 'id', None)}
                                          for item in session.items])
    classes = [_one_marker(item, 'cost').kwargs['cost_class'] for item in session.items]
    manifest['batch_cost_class'] = max(classes, default=None)
    estimates = {item.nodeid: _one_marker(item, 'cost').kwargs.get('estimated_wall_time_ms') for item in session.items}
    manifest['estimated_cost'] = {'known_wall_time_ms': sum(value for value in estimates.values() if isinstance(value, (int, float))),
                                  'unknown': [node for node, value in estimates.items() if value is None],
                                  'source': 'cost marker; unknown values are not zero estimates'}
    manifest['shared_preparation'] = {'actual_wall_time_ms': (time.monotonic() - config._rpipe_started) * 1000,
                                     'scope': 'pytest configure through collection; launcher startup is in batch wall time'}
    for attempt in manifest['attempts']:
        if attempt['attempt'] == config._rpipe_attempt:
            attempt['shared_preparation'] = manifest['shared_preparation']
    save_json(config._rpipe_results_dir / 'manifest.json', manifest)
    print(f'RPipe plan {manifest["plan_id"]}: selected={len(selected)}, required={len(manifest["required"])}; {config._rpipe_results_dir / "manifest.json"}', flush=True)


def _event(item: pytest.Item) -> dict:
    names = _marker_names(item)
    cost_mark, result_mark = _one_marker(item, 'cost'), _one_marker(item, 'result_type')
    plan = getattr(item.config, '_rpipe_plan', {})
    return {
        'run_id': item.config._rpipe_run_id, 'attempt': getattr(item.config, '_rpipe_attempt', 1), 'test_id': item.nodeid,
        'level': next(iter(names & LEVELS), ''), 'type': next(iter(names & TYPES), ''),
        'priority': next(iter(names & PRIORITIES), ''),
        'tags': sorted(names - LEVELS - TYPES - PRIORITIES - {'cost', 'result_type'}),
        'target': item.location[0], 'result_type': result_mark.args[0],
        'plan_id': plan.get('plan_id', 'ad_hoc'), 'cost_class': cost_mark.kwargs.get('cost_class'),
        'result_detail': result_mark.kwargs.get('detail'), 'environment_id': plan.get('environment_id', 'local'),
        'verdict': None, 'category': None, 'metrics': [], 'status': 'running',
        'duration_ms': None, 'started_at': datetime.now(timezone.utc).isoformat(), 'failure': None,
        'cost': {'estimated_wall_time_ms': cost_mark.kwargs.get('estimated_wall_time_ms'),
                 'estimate_source': cost_mark.kwargs.get('estimate_source', 'not supplied'), 'actual_wall_time_ms': None,
                 'cpu_time_ms': None, 'peak_memory_bytes': None, 'unmeasured_reason': 'only total phase wall time is sampled'},
        'artifacts': [],
    }


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem):
    item._rpipe_reports = {}
    save_json(item.config._rpipe_results_dir / 'active.json', _event(item))
    yield


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    cost = _one_marker(item, 'cost')
    if cost is None:
        return
    names = _marker_names(item)
    if 'gpu' in names or cost.kwargs.get('gpu_count', 0):
        manifest = _manifest(item.config)
        reason = 'CPU test plan provides no explicit GPU runner/resources'
        manifest.setdefault('not_executed', []).append({'test_id': item.nodeid, 'reason': reason})
        save_json(item.config._rpipe_results_dir / 'manifest.json', manifest)
        pytest.skip(reason)


def _status(report: pytest.TestReport, result_type: str | None) -> str:
    if report.skipped:
        return 'xfail' if getattr(report, 'wasxfail', False) else 'skipped'
    if report.passed:
        if getattr(report, 'wasxfail', False):
            return 'xpass'
        return 'measured' if result_type == 'numeric' else 'passed'
    if getattr(report, 'wasxfail', False):
        return 'xfail'
    if str(getattr(report, 'longrepr', '')).startswith('[XPASS(strict)]'):
        return 'xpass'
    return 'error' if result_type == 'numeric' else 'failed'


def _json_values(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_values(entry) for key, entry in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_values(entry) for entry in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return {'invalid_type': type(value).__name__}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    config = item.config
    out_dir = getattr(config, '_rpipe_results_dir', None)
    if out_dir is None:
        return
    reports = getattr(item, '_rpipe_reports', {})
    reports[report.when] = report
    item._rpipe_reports = reports
    exception_types = getattr(item, '_rpipe_exception_types', {})
    if call is not None and getattr(call, 'excinfo', None) is not None:
        exception_types[report.when] = call.excinfo.typename
    item._rpipe_exception_types = exception_types
    properties = dict(getattr(item, 'user_properties', []))
    metrics = properties.get('metrics', [])
    append_event(out_dir / 'phases.jsonl', {
        'run_id': config._rpipe_run_id, 'test_id': item.nodeid, 'attempt': getattr(config, '_rpipe_attempt', 1),
        'phase': report.when, 'status': _status(report, None), 'duration_ms': report.duration * 1000,
        'metrics': _json_values(metrics), 'failure': str(report.longrepr)[:4000] if report.failed else None,
        'exception_type': exception_types.get(report.when),
    })
    if report.when != 'teardown':
        return
    ordered = [reports[name] for name in ('setup', 'call', 'teardown') if name in reports]
    failed = [part for part in ordered if part.failed]
    primary = failed[0] if failed else next((part for part in ordered if part.skipped), reports.get('call', report))
    event = _event(item)
    result_type = event['result_type']
    status = _status(primary, result_type)
    if any(part.when != 'call' for part in failed):
        status = 'error'
    valid_metrics = isinstance(metrics, list) and bool(metrics) and all(
        isinstance(metric, dict) and isinstance(metric.get('value'), (int, float))
        and not isinstance(metric.get('value'), bool) and math.isfinite(metric['value'])
        and metric.get('name') and metric.get('unit') and metric.get('threshold') is None for metric in metrics)
    try:
        json.dumps(metrics, allow_nan=False)
    except (TypeError, ValueError):
        valid_metrics = False
    failure = {'longrepr': str(primary.longrepr)[:4000], 'when': primary.when} if primary.failed else None
    if result_type == 'numeric' and status == 'measured' and not valid_metrics:
        status, failure = 'error', {'longrepr': 'numeric result has missing or invalid measurements', 'when': 'call'}
    verdict, category = None, None
    if result_type == 'categorical' and status in {'passed', 'failed'}:
        verdict = category = 'pass' if status == 'passed' else 'fail'
        if status == 'passed' and properties.get('category') not in (None, 'pass', 'fail'):
            category, verdict, status = properties['category'], None, 'completed'
    duration_ms = round(sum(part.duration or 0 for part in ordered) * 1000)
    event.update(status=status, verdict=verdict, category=category, metrics=_json_values(metrics),
                 duration_ms=duration_ms, started_at=datetime.fromtimestamp(ordered[0].start, timezone.utc).isoformat(),
                 failure=failure, cleanup_failures=[{'longrepr': str(part.longrepr)[:4000], 'when': part.when} for part in failed[1:]],
                 phases={part.when: {'status': _status(part, None), 'duration_ms': part.duration * 1000} for part in ordered})
    event['cost']['actual_wall_time_ms'] = duration_ms
    event['exception_type'] = exception_types.get(primary.when)
    if primary.skipped:
        event['reason'] = str(primary.longrepr)[:4000]
    append_event(out_dir / 'events.jsonl', event)
    save_json(out_dir / 'active.json', {})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    out_dir = getattr(config, '_rpipe_results_dir', None)
    if out_dir is None:
        return
    if int(exitstatus) == 2:
        _entry._interrupted_event(out_dir, getattr(config, '_rpipe_attempt', 1), 'error', 'pytest interrupted before this attempt completed')
    manifest = _manifest(config)
    manifest['exit_status'] = int(exitstatus)
    for attempt in manifest.get('attempts', []):
        if attempt['attempt'] == getattr(config, '_rpipe_attempt', 1):
            attempt.update(status='interrupted' if int(exitstatus) == 2 else 'completed', exit_status=int(exitstatus))
    save_json(out_dir / 'manifest.json', manifest)
    _entry.record_unexecuted(out_dir, f'pytest ended before a complete event, exit_status={int(exitstatus)}')
    summary = write_report(out_dir, int(exitstatus))
    if summary['blocking'] and int(exitstatus) == 0:
        session.exitstatus = 1


@pytest.fixture
def stream_close_fault(monkeypatch):
    def inject(select, primary, secondary, *, phase='write'):
        original = Path.open
        handles, calls = [], []
        class Stream:
            def __init__(self, raw):
                self.raw = raw
            def __getattr__(self, name):
                return getattr(self.raw, name)
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()
            def write(self, value):
                calls.append('write')
                if phase == 'write' and primary is not None:
                    self.raw.write(value[:3])
                    self.raw.flush()
                    raise primary
                return self.raw.write(value)
            def flush(self):
                calls.append('flush')
                self.raw.flush()
                if phase == 'flush' and primary is not None:
                    raise primary
            def close(self):
                calls.append('close')
                self.raw.close()
                if secondary is not None:
                    raise secondary
        def opening(path, mode='r', *args, **kwargs):
            raw = original(path, mode, *args, **kwargs)
            if select(path, mode):
                handles.append(raw)
                return Stream(raw)
            return raw
        monkeypatch.setattr(Path, 'open', opening)
        return handles, calls
    return inject


@pytest.fixture(params=['read-io', 'cancel', 'body-programming', 'close-io', 'close-programming', 'success'])
def reader_exit_case(request):
    from types import SimpleNamespace
    kind = request.param
    primary = (OSError('first read IO') if kind == 'read-io' else
               KeyboardInterrupt('first read cancellation') if kind == 'cancel' else
               ValueError('first read programming error') if kind == 'body-programming' else None)
    secondary = (RuntimeError('unexpected close programming error') if kind == 'close-programming' else
                 None if kind == 'success' else OSError('second close IO'))
    if kind == 'close-programming':
        primary = OSError('first read IO')
    expected = secondary if kind == 'close-programming' or primary is None else primary
    return SimpleNamespace(kind=kind, primary=primary, secondary=secondary, expected=expected)


@pytest.fixture
def reader_close_fault(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def inject(selected, case, selected_mode):
        native = Path.open
        handles, closes = [], []
        class Stream:
            def __init__(self, raw):
                self.raw = raw
            def __getattr__(self, name):
                return getattr(self.raw, name)
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()
            def read(self, *args, **kwargs):
                if case.primary is not None:
                    raise case.primary
                return self.raw.read(*args, **kwargs)
            def readline(self, *args, **kwargs):
                if case.primary is not None:
                    raise case.primary
                return self.raw.readline(*args, **kwargs)
            def close(self):
                closes.append(self.raw)
                self.raw.close()
                if case.secondary is not None:
                    raise case.secondary
        def opening(path, mode='r', *args, **kwargs):
            raw = native(path, mode, *args, **kwargs)
            if path == selected and mode == selected_mode:
                handles.append(raw)
                return Stream(raw)
            return raw
        with monkeypatch.context() as scope:
            scope.setattr(Path, 'open', opening)
            yield handles, closes
    return inject


@pytest.fixture
def science_factory():
    class ScientificActions:
        def __init__(self, *, fail=None, close_error=None):
            self.calls = []
            self.fail = fail
            self.close_error = close_error

        def _phase(self, phase):
            self.calls.append(phase)
            if self.fail == phase:
                raise ValueError(f'{phase} primary failure')

        def prepare(self, ctx):
            self._phase('prepare')

        def execute(self, ctx):
            self._phase('execute')
            return {'value': 3.0}

        def collect(self, ctx):
            self._phase('collect')
            return {'metrics': {'value': 3.0}}

        def close(self, ctx):
            self.calls.append('close')
            if self.close_error:
                raise RuntimeError(self.close_error)

    return ScientificActions


@pytest.fixture
def science_worker(monkeypatch):
    """Inline the real worker protocol, replacing only scientific phase actions."""
    import multiprocessing
    import os
    import subprocess
    from types import SimpleNamespace
    from rpipe.flow import stages, worker
    from rpipe.flow.study import run_study_phase

    selected = []

    def install(actions):
        selected[:] = [actions]

    def prepare(ctx):
        from rpipe.structure.system.logger import Logger
        ctx.state['logger'] = Logger(ctx.layout.assets_dir, **ctx.state['logging_context'])
        ctx.state['logger'].bind(phase='prepare')
        actions = selected[0]
        ctx.state['test_science_actions'] = actions
        class ScientificResource:
            def close(self):
                actions.close(ctx)
        ctx.register_resource(ScientificResource())
        run_study_phase(ctx.study_dir, 'prepare', ctx, ctx.state['study'], entry='before')
        actions.prepare(ctx)

    def execute(ctx):
        return ctx.state['test_science_actions'].execute(ctx)

    def collect(ctx):
        return ctx.state['test_science_actions'].collect(ctx)

    class InlineWorker:
        def __init__(self, args, **kwargs):
            self.pid = os.getpid()
            name = multiprocessing.current_process().name
            try:
                self.returncode = worker.worker(Path(args[-2]), Path(args[-1]))
            finally:
                multiprocessing.current_process().name = name
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(stages, 'prepare', prepare)
    monkeypatch.setattr(stages, 'execute', execute)
    monkeypatch.setattr(stages, 'collect', collect)
    monkeypatch.setattr(worker, 'subprocess', SimpleNamespace(Popen=InlineWorker,
        DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT))
    return install


@pytest.fixture
def model_system():
    """A device-placement port for model-only construction tests."""
    class Placement:
        device = 'cpu'
        def place_module(self, module):
            return module.to('cpu') if module is not None else None
    return Placement()
