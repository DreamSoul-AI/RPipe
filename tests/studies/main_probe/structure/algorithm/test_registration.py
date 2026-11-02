"""Probe-local cleanup errors must reach the outer execution outcome."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def registration(monkeypatch):
    source = Path(__file__).resolve().parents[5] / 'studies/main_probe/structure/algorithm/__init__.py'
    name = '_probe_registration_' + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, source,
                                                submodule_search_locations=[str(source.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    from rpipe.structure.algorithm.factory import AlgorithmRegistry
    from rpipe.structure.data.factory import DataRegistry
    monkeypatch.setattr(AlgorithmRegistry, '_items', {})
    monkeypatch.setattr(DataRegistry, '_items', {})
    monkeypatch.setattr(module.paired, 'seed_runtime', lambda device: None)
    monkeypatch.setattr(module.paired, 'rng', lambda device: {})
    yield module
    for key in list(sys.modules):
        if key == name or key.startswith(name + '.'):
            sys.modules.pop(key, None)


@pytest.mark.parametrize('cleanup_failed', [False, True], ids=['cancel', 'cancel-and-cleanup-failure'])
def test_probe_cancellation_cleanup_reaches_execution_outcome(
        registration, monkeypatch, tmp_path, cleanup_failed, science_factory, science_worker):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    from rpipe.structure.algorithm.factory import AlgorithmRegistry
    from rpipe.structure.control import AlgorithmConfig
    from rpipe.flow.context import Cancelled
    from rpipe.flow.run import run

    primary = Cancelled('probe cancelled')
    secondary = OSError('owned writer close failed')
    if cleanup_failed:
        primary.cleanup_errors = (secondary,)

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(registration.paired, 'run_pair', fail)

    class ProbeBoundary(science_factory):
        def prepare(self, ctx):
            workspace = ctx.layout.assets_dir / 'probe'
            workspace.mkdir(parents=True)
            (workspace / 'PREPARED.json').write_text(json.dumps({'passed': True}), encoding='utf-8')
            registration.register(SimpleNamespace(assets_dir=ctx.layout.assets_dir,
                                                   config={'system': {'device': 'cpu'}}),
                                  ('MNIST', 'linear'))
            factory = AlgorithmRegistry.get('train', 'main_probe')
            ctx.state['algorithm'] = factory(AlgorithmConfig.from_mapping({'mode': 'train'}),
                                             checkpoint_dir=ctx.layout.assets_dir / 'checkpoints')

        def execute(self, ctx):
            ctx.state['algorithm'].compute(None, None, None, None, cancel_check=lambda: None)

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'study.yaml').write_text('study: probe_boundary\nseeds: [0]\n', encoding='utf-8')
    (source / 'experiment_config.yaml').write_text(
        'experiment: probe_boundary\ndata: {name: Toy}\nmodel: {name: linear}\n'
        'algorithm: {mode: train}\nsystem: {device: cpu}\n', encoding='utf-8')
    spec = plan(source, run_root=tmp_path / 'runs')[0]
    science_worker(ProbeBoundary())
    outcome = run(spec)
    record = json.loads((spec.run_dir / 'execution.json').read_text(encoding='utf-8'))
    status = 'failed' if cleanup_failed else 'cancelled'
    assert outcome.status == read_run(spec.ref)['status'] == record['candidate_status'] == status
    assert outcome.error['type'] == 'Cancelled'
    assert bool(outcome.cleanup_errors) is cleanup_failed
    if cleanup_failed:
        assert 'OSError' in json.dumps(outcome.error)
        assert 'OSError' in json.dumps(record['cleanup_errors'])
    assert not spec.result_path.exists()
