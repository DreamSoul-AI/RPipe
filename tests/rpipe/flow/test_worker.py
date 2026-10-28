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
