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


def test_same_run_competing_starts_invoke_only_one_payload(source_study, run_root, payload_factory):
    from rpipe.api import plan, run
    from rpipe.structure.contracts import BusyError

    entered, finish = Event(), Event()

    class Blocking(payload_factory):
        def execute(self, ctx):
            self._phase('execute')
            entered.set()
            assert finish.wait(5), 'test owner failed to release the payload'

    spec = plan(source_study, run_root=run_root)[0]
    first, competitor = Blocking(), payload_factory()
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(run, spec, payload=first)
        try:
            assert entered.wait(5), 'first payload did not start'
            with pytest.raises(BusyError):
                run(spec, payload=competitor)
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
        source_study, run_root, payload_factory, phase, expected):
    from rpipe.api import plan, run

    payload = payload_factory(fail=phase, close_error='secondary close failure')
    outcome = run(plan(source_study, run_root=run_root)[0], payload=payload)
    assert outcome.status == 'failed'
    assert outcome.error['type'] == 'ValueError'
    assert outcome.cleanup_errors[0]['type'] == 'RuntimeError'
    assert outcome.quality is None
    assert payload.calls == expected


def test_cancel_before_start_does_not_call_payload(source_study, run_root, payload_factory):
    from rpipe.api import cancel, plan, run
    from rpipe.structure.contracts import Cancellation

    token = Cancellation()
    cancel(token)
    payload = payload_factory()
    outcome = run(plan(source_study, run_root=run_root)[0], payload=payload, cancellation=token)
    assert outcome.status == 'cancelled'
    assert outcome.cancelled is True
    assert payload.calls == []


def test_cancel_during_execute_prevents_collect(source_study, run_root, payload_factory):
    from rpipe.api import plan, run
    from rpipe.structure.contracts import Cancellation

    token = Cancellation()

    class Cancelling(payload_factory):
        def execute(self, ctx):
            self._phase('execute')
            token.request()

    payload = Cancelling()
    outcome = run(plan(source_study, run_root=run_root)[0], payload=payload, cancellation=token)
    assert outcome.status == 'cancelled'
    assert outcome.cancelled is True
    assert payload.calls == ['prepare', 'execute', 'close']


@pytest.mark.parametrize('limit', [{'timeout_seconds': 1}, {'memory_bytes': 1048576}])
def test_unsupported_hard_budget_rejects_before_payload_start(
        source_study, run_root, payload_factory, limit):
    from rpipe.api import plan, run
    from rpipe.structure.contracts import AdmissionError, Budget

    payload = payload_factory()
    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(AdmissionError):
        run(spec, payload=payload, budget=Budget(**limit))
    assert payload.calls == []
    assert not run_root.exists()


def test_payload_boolean_claim_cannot_grant_itself_hard_timeout_capability(
        source_study, run_root, payload_factory):
    from rpipe.api import plan, run
    from rpipe.structure.contracts import AdmissionError, Budget

    payload = payload_factory()
    payload.hard_timeout = True
    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(AdmissionError):
        run(spec, payload=payload, budget=Budget(timeout_seconds=1))
    assert payload.calls == []
    assert not run_root.exists()


@pytest.mark.parametrize('stop_fails', [False, True], ids=['confirmed-stop', 'unknown-stop'])
def test_cancel_cleans_only_owned_process_and_unknown_owner_remains_busy(
        source_study, run_root, payload_factory, monkeypatch, stop_fails):
    from rpipe.api import plan, run
    from rpipe.structure.contracts import BusyError, Cancellation

    def child():
        return subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)

    peer = child()
    owned = child()
    stop = owned.terminate
    token = Cancellation()

    class OwnsProcess(payload_factory):
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
        outcome = run(spec, payload=OwnsProcess(), cancellation=token)
        assert peer.poll() is None, 'cancel terminated an unrelated peer'
        if stop_fails:
            assert outcome.status == 'unknown' and owned.poll() is None
            with pytest.raises(BusyError):
                run(spec, payload=payload_factory())
        else:
            assert outcome.status == 'cancelled' and owned.poll() is not None
            assert not (spec.run_dir / '.execution').exists()
    finally:
        for process, terminate in ((owned, stop), (peer, peer.terminate)):
            if process.poll() is None:
                terminate()
            process.wait(timeout=5)


def test_eval_cannot_bind_successful_training_from_another_seed(
        source_study, run_root, payload_factory, file_bytes):
    from dataclasses import replace
    from rpipe.api import plan, run
    from rpipe.structure.contracts import AdmissionError

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('axes: {algorithm.mode: [train, eval]}\n')
    specs = plan(source_study, run_root=run_root)
    trains = [spec for spec in specs if spec.config['algorithm']['mode'] == 'train']
    evaluated = next(spec for spec in specs if spec.config['algorithm']['mode'] == 'eval' and spec.seed == 1)
    for train in trains:
        assert run(train, payload=payload_factory()).status == 'succeeded'
    wrong = replace(evaluated, train_id=next(spec.id for spec in trains if spec.seed == 0))
    before = file_bytes(run_root)
    payload = payload_factory()
    with pytest.raises(AdmissionError):
        run(wrong, payload=payload)
    assert payload.calls == []
    assert file_bytes(run_root) == before
