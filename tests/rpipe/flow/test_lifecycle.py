"""Public run admission owns one executor and preserves failure/cancel facts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_same_run_competing_starts_invoke_only_one_payload(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.control import BusyError

    entered, finish = Event(), Event()

    class Blocking(science_factory):
        def execute(self, ctx):
            self._phase('execute')
            entered.set()
            assert finish.wait(5), 'test owner failed to release the payload'

    spec = plan(source_study, run_root=run_root)[0]
    first, competitor = Blocking(), science_factory()
    with ThreadPoolExecutor(max_workers=1) as worker:
        science_worker(first)
        future = worker.submit(run, spec)
        try:
            assert entered.wait(5), 'first payload did not start'
            science_worker(competitor)
            _rejected = run(spec)
            assert _rejected.status == 'rejected'
            assert _rejected.error['type'] == 'BusyError'
        finally:
            finish.set()
        outcome = future.result(timeout=5)
    assert outcome.status == 'succeeded'
    assert first.calls == ['prepare', 'execute', 'collect', 'close']
    assert competitor.calls == []


@pytest.mark.parametrize('phase,expected', [
    ('prepare', ['prepare', 'close']),
    ('execute', ['prepare', 'execute', 'close']),
    ('collect', ['prepare', 'execute', 'collect', 'close']),
])
def test_phase_failure_and_close_failure_preserve_first_error(
        source_study, run_root, science_factory, phase, expected, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run

    payload = science_factory(fail=phase, close_error='secondary close failure')
    science_worker(payload)
    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec)
    assert outcome.status == 'unknown'
    assert outcome.error['type'] == 'ValueError'
    assert outcome.cleanup_errors[0]['type'] == 'RuntimeError'
    assert outcome.quality is None
    assert payload.calls == expected
    assert (spec.run_dir / '.execution').exists()
    assert not spec.result_path.exists()


def test_cancel_before_start_does_not_call_payload(source_study, run_root, science_factory, science_worker):
    from rpipe.flow.run import run
    from rpipe.flow.context import Cancellation
    from rpipe.structure.make import plan

    token = Cancellation()
    token.request()
    payload = science_factory()
    science_worker(payload)
    outcome = run(plan(source_study, run_root=run_root)[0], cancellation=token)
    assert outcome.status == 'cancelled'
    assert outcome.cancelled is True
    assert payload.calls == []
    assert not run_root.exists()


def test_cancel_during_execute_prevents_collect(source_study, run_root, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.flow.context import Cancellation, Cancelled

    token = Cancellation()

    class Cancelling(science_factory):
        def execute(self, ctx):
            self._phase('execute')
            token.request()
            raise Cancelled('scientific boundary observed cancellation')

    payload = Cancelling()
    science_worker(payload)
    outcome = run(plan(source_study, run_root=run_root)[0], cancellation=token)
    assert outcome.status == 'cancelled'
    assert outcome.cancelled is True
    assert payload.calls == ['prepare', 'execute', 'close']


def test_unsupported_hard_memory_rejects_before_science_or_output(
        source_study, run_root, monkeypatch):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.flow.context import Budget
    from rpipe.flow import worker
    calls = []
    monkeypatch.setattr(worker, 'compute', lambda *args: calls.append(args))
    spec = plan(source_study, run_root=run_root)[0]
    rejected = run(spec, budget=Budget(memory_bytes=1048576))
    assert rejected.status == 'rejected'
    assert rejected.error['type'] == 'AdmissionError'
    assert not calls and not run_root.exists()


@pytest.mark.parametrize('stop_fails', [False, True], ids=['confirmed-stop', 'unknown-stop'])
def test_cancel_cleans_only_owned_process_and_unknown_owner_remains_busy(
        source_study, run_root, science_factory, monkeypatch, stop_fails, science_worker):
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.control import BusyError
    from rpipe.flow.context import Cancellation

    def child():
        return subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)

    peer = child()
    owned = child()
    stop = owned.terminate
    token = Cancellation()

    class OwnsProcess(science_factory):
        def execute(self, ctx):
            self._phase('execute')
            ctx.register_process(owned)
            token.request()

    if stop_fails:
        def denied():
            raise PermissionError('owned process stop unconfirmed')
        monkeypatch.setattr(owned, 'terminate', denied)
    spec = plan(source_study, run_root=run_root)[0]
    try:
        science_worker(OwnsProcess())
        outcome = run(spec, cancellation=token)
        assert peer.poll() is None, 'cancel terminated an unrelated peer'
        if stop_fails:
            assert outcome.status == 'unknown' and owned.poll() is None
            science_worker(science_factory())
            _rejected = run(spec)
            assert _rejected.status == 'rejected'
            assert _rejected.error['type'] == 'BusyError'
        else:
            assert outcome.status == 'cancelled' and owned.poll() is not None
            assert not (spec.run_dir / '.execution').exists()
    finally:
        for process, terminate in ((owned, stop), (peer, peer.terminate)):
            if process.poll() is None:
                terminate()
            process.wait(timeout=5)


def test_eval_cannot_bind_successful_training_from_another_seed(
        source_study, run_root, science_factory, file_bytes, science_worker):
    from dataclasses import replace
    from rpipe.structure.make import plan
    from rpipe.flow.run import run
    from rpipe.structure.control import AdmissionError

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('axes: {algorithm.mode: [train, eval]}\n')
    specs = plan(source_study, run_root=run_root)
    trains = [spec for spec in specs if spec.config['algorithm']['mode'] == 'train']
    evaluated = next(spec for spec in specs if spec.config['algorithm']['mode'] == 'eval' and spec.seed == 1)
    for train in trains:
        science_worker(science_factory())
        assert run(train).status == 'succeeded'
    wrong = replace(evaluated, train_id=next(spec.id for spec in trains if spec.seed == 0))
    before = file_bytes(run_root)
    payload = science_factory()
    science_worker(payload)
    _rejected = run(wrong)
    assert _rejected.status == 'rejected'
    assert _rejected.error['type'] == 'AdmissionError'
    assert payload.calls == []
    assert file_bytes(run_root) == before
