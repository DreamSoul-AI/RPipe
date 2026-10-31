"""The scientific worker replies after cleanup and never publishes formal results."""
import dataclasses

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _request(spec):
    from rpipe.structure.artifact.io import identity, write_json
    attempt, request_id = 'a' * 32, 'b' * 32
    folder = spec.run_dir / 'attempts' / attempt
    request, response = folder / 'worker-request.json', folder / 'worker-output.json'
    body = {field.name: str(getattr(spec, field.name)) if field.name in {'source_dir', 'run_root'}
            else spec.to_config() if field.name == 'config' else getattr(spec, field.name)
            for field in dataclasses.fields(spec) if field.init}
    write_json(request, {'schema': 1, 'request': request_id, 'attempt': attempt,
                         'identity': identity(spec), 'spec': body,
                         'logging': {'task_id': spec.id, 'attempt_id': attempt}})
    return request, response


def test_scientific_worker_only_writes_its_response_and_diagnostics(
        source_study, run_root, monkeypatch):
    import multiprocessing
    from rpipe.flow import stages, worker
    from rpipe.structure.artifact.io import read_json
    from rpipe.structure.make import plan

    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    closed = []
    class ScienceResource:
        def close(self):
            closed.append(True)
    def compute(ctx):
        assert not hasattr(ctx, 'owner')
        ctx.register_resource(ScienceResource())
        return {'metrics': {'value': 3.0}}
    monkeypatch.setattr(stages, 'compute', compute)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    assert worker.worker(request, response) == 0
    observed = read_json(response)
    assert observed['science_closed'] and not observed['unknown_resources']
    assert observed['request'] == 'b' * 32 and observed['attempt'] == 'a' * 32
    assert closed == [True]
    assert not spec.result_path.exists()
    assert not (spec.run_dir / 'execution.json').exists()
    assert not (spec.run_dir / 'config.yaml').exists()
    assert not (spec.run_dir / '.execution').exists()


@pytest.mark.parametrize('science_fails', [False, True])
def test_worker_logger_close_failure_preserves_first_error_and_reports_unknown(
        source_study, run_root, monkeypatch, science_fails):
    import multiprocessing
    from rpipe.flow import stages, worker
    from rpipe.structure.artifact.io import read_json
    from rpipe.structure.make import plan
    from rpipe.structure.system.logger import Logger

    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    def compute(ctx):
        ctx.state['logger'] = Logger(ctx.layout.assets_dir, **ctx.state['logging_context'])
        if science_fails:
            raise ValueError('secret=PRIVATE_PRIMARY_SCIENCE')
        return {'metrics': {'value': 3.0}}
    def close(logger):
        raise OSError('secret=PRIVATE_SECONDARY_LOGGER')
    monkeypatch.setattr(stages, 'compute', compute)
    monkeypatch.setattr(Logger, 'close', close)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    assert worker.worker(request, response) == 1
    observed = read_json(response)
    assert observed['unknown_resources'] and not observed['science_closed']
    assert (observed['error']['type'] if observed['error'] else None) == (
        'ValueError' if science_fails else None)
    assert observed['cleanup_errors'][0]['type'] == 'OSError'
    assert 'PRIVATE_PRIMARY_SCIENCE' not in response.read_text(encoding='utf-8')
    assert 'PRIVATE_SECONDARY_LOGGER' not in response.read_text(encoding='utf-8')
    assert not spec.result_path.exists()


@pytest.mark.parametrize('control', ['foreign-request', 'foreign-identity', 'foreign-attempt',
                                  'cancel', 'bad-schema', 'bad-action', 'extra-field'])
def test_worker_cancel_control_is_bound_to_exact_request_and_attempt(
        source_study, run_root, monkeypatch, control):
    import multiprocessing
    from rpipe.flow import stages, worker
    from rpipe.structure.artifact.io import identity, read_json, write_json
    from rpipe.structure.make import plan
    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    marker = request.parent / 'worker-cancel.json'
    body = {'schema': 1, 'action': 'cancel', 'request': 'b' * 32,
            'identity': identity(spec), 'attempt': 'a' * 32}
    if control == 'foreign-request':
        body['request'] = 'c' * 32
    elif control == 'foreign-identity':
        body['identity'] = {**body['identity'], 'source_digest': 'c' * 64}
    elif control == 'foreign-attempt':
        body['attempt'] = 'c' * 32
    elif control == 'bad-schema':
        body['schema'] = True
    elif control == 'bad-action':
        body['action'] = 'stop'
    elif control == 'extra-field':
        body['extra'] = True
    write_json(marker, body)
    before, calls = marker.read_bytes(), []
    def compute(ctx):
        assert not hasattr(ctx, 'cancellation')
        ctx.cancel_check()
        calls.append(True)
        return {'metrics': {'value': 1}}
    monkeypatch.setattr(stages, 'compute', compute)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    assert worker.worker(request, response) == (0 if control.startswith('foreign-') else 1)
    observed = read_json(response)
    if control.startswith('foreign-'):
        assert observed['error'] is None and observed['result']['metrics']['value'] == 1
        assert calls == [True]
    else:
        assert observed['error']['type'] == ('Cancelled' if control == 'cancel' else 'AdmissionError')
        assert calls == []
    assert observed['science_closed'] and not observed['unknown_resources']
    assert marker.read_bytes() == before
    assert not spec.result_path.exists() and not (spec.run_dir / 'execution.json').exists()


@pytest.mark.parametrize('science_fails', [False, True])
def test_worker_restores_process_signal_handler_on_success_and_scientific_failure(
        source_study, run_root, monkeypatch, science_fails):
    import multiprocessing
    import os
    import signal
    from rpipe.flow import stages, worker
    from rpipe.structure.make import plan
    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    previous = signal.getsignal(signal.SIGINT)
    def old_handler(signum, frame):
        pytest.fail('worker forwarded an unrequested signal to the previous owner')
    def compute(ctx):
        assert signal.getsignal(signal.SIGINT) == (signal.SIG_IGN if os.name == 'nt' else old_handler)
        if science_fails:
            raise ValueError('scientific failure')
        return {'metrics': {'value': 1}}
    monkeypatch.setattr(stages, 'compute', compute)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    signal.signal(signal.SIGINT, old_handler)
    try:
        assert worker.worker(request, response) == int(science_fails)
        assert signal.getsignal(signal.SIGINT) is old_handler
    finally:
        signal.signal(signal.SIGINT, previous)
