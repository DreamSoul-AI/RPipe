"""E2: real Study/Flow seams with synthetic science and an inline worker.

Preparation, historical computation and its observations are explicit fakes.
The declared counts below are comparison fixtures, never measured MNIST/CIFAR
results. Recipe validation, registration, assembly, all six Study hooks, the
observed-state gates, ownership, worker messages and formal I/O stay real.
"""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize(('study', 'count'), [('main_probe', 8), ('main_exp', 64)])
def test_current_study_declarations_are_admitted_without_starting_science(
        tmp_path, monkeypatch, study, count):
    from rpipe.flow import study as loading
    from rpipe.structure.make import plan

    source = Path(__file__).resolve().parents[4] / 'studies' / study
    output = tmp_path / 'read-only-plan'
    def unexpected(*args, **kwargs):
        raise AssertionError('planning must never load a scientific recipe')
    monkeypatch.setattr(loading, 'load_recipe', unexpected)
    before = {name: (source / name).read_bytes()
              for name in ('study.yaml', 'experiment_config.yaml')}
    specs = plan(source, run_root=output, version='synthetic-admission-only')
    assert len(specs) == count
    assert all(spec.version == 'synthetic-admission-only' for spec in specs)
    assert not output.exists()
    assert {name: (source / name).read_bytes() for name in before} == before


@pytest.fixture
def journey(tmp_path, monkeypatch, request):
    import torch
    from rpipe.flow import stages
    from rpipe.structure.artifact import io
    from rpipe.structure.artifact.provenance import source_digest
    from rpipe.structure.make import plan, publish
    from rpipe.structure.model.factory import Model, ModelRegistry

    # Reuse its real inline request/response transport, then restore actual stages.
    actual = {name: getattr(stages, name) for name in ('prepare', 'execute', 'collect')}
    science_worker = request.getfixturevalue('science_worker')
    science_worker(None)
    for name, operation in actual.items():
        monkeypatch.setattr(stages, name, operation)

    original = Path(__file__).resolve().parents[4] / 'studies/main_probe'
    source = tmp_path / 'source/main_probe'
    source.mkdir(parents=True)
    files = ['recipe.py', 'structure/algorithm/__init__.py']
    for stage in ('prepare', 'execute', 'collect', 'summarize', 'write', 'process'):
        files.extend([f'flow/{stage}/__init__.py', f'flow/{stage}/run.py'])
    files.append('flow/collect/comparison.py')
    for relative in files:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((original / relative).read_bytes())

    spec = importlib.util.spec_from_file_location('_journey_reference_' + uuid4().hex,
                                                original / 'structure/algorithm/paired.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    support = ModuleType('_journey_science_' + uuid4().hex)
    state = SimpleNamespace(events=[], resources={}, fault=None, publications=[])
    for name in ('DATA', 'MODELS', 'mapping', 'state_compare', 'optimizer_compare', 'rng_equal', 'rng'):
        setattr(support, name, getattr(reference, name))
    support.seed_runtime = lambda device: None
    support.source_manifest = lambda: {'source_digest': source_digest(source)}
    support.save_json = io.write_json
    support.MainArchive = lambda workspace: SimpleNamespace(verify_exports=lambda: {'unchanged': True})

    def prepare(workspace, source_data, pair):
        state.events.append((pair, 'prepare'))
        workspace.mkdir(parents=True, exist_ok=False)
        io.write_json(workspace / 'SYNTHETIC.json', {'pair': pair, 'real_data': False})
        if state.fault == 'prepare':
            raise ValueError('synthetic preparation primary failure')
        io.write_json(workspace / 'SOURCE_MANIFEST.json', {'files': support.source_manifest()})
        prepared = {'passed': True, 'synthetic_only': True}
        io.write_json(workspace / 'PREPARED.json', prepared)
        return prepared
    support.prepare = prepare

    class Data:
        def __init__(self, workspace, name):
            self.workspace, self.name = workspace, name
            self.source, self.closed = 'main_probe', 0
            self.meta = {'synthetic_only': True, 'workspace': str(workspace)}
            state.events.append((workspace, 'data'))
            state.resources[workspace] = {'data': self}

        def close(self):
            self.closed += 1
            state.events.append((self.workspace, 'close-data'))
            if state.fault in ('close', 'compute-and-close'):
                raise OSError('synthetic data close failure')

        def to_result_snapshot(self):
            return {'name': self.name, 'source': self.source, **self.meta}
    support.native_data = lambda workspace, name, assets: Data(workspace, name)

    def build_model(config, assets, *, data_meta):
        assert data_meta['synthetic_only'] is True
        workspace = Path(data_meta['workspace'])
        state.events.append((workspace, 'model'))
        model = Model(name=config.name, source=config.source, module=None,
                      meta={'synthetic_only': True})
        state.resources[workspace]['model'] = model
        return model
    monkeypatch.setattr(ModelRegistry, '_items', {
        (name, 'custom_torch'): build_model for name in reference.MODELS})

    def raw_observations():
        snapshots = {}
        rng = reference.rng('cpu')
        for step in (30, 60):
            snapshots[step] = {
                'step': step, 'train': {'Loss': .3, 'Accuracy': 50.},
                'test': {'Loss': .3, 'Accuracy': 50.},
                'model': {'weight': torch.tensor([.5])},
                'optimizer': {'state': {0: {'step': step}}},
                'scheduler': {'last_epoch': step}, 'rng': rng,
                'observations': {'train': {'samples': step * 250, 'batches': step},
                                 'test': {'samples': step // 30 * 10000,
                                          'batches': step // 30 * 10}}}
        observed = {split: {'samples': count, 'batches': batches,
                            'images_sha256': 'synthetic', 'targets_sha256': 'synthetic',
                            'core_inputs_sha256': 'synthetic'}
                    for split, count, batches in (('train', 15000, 60), ('test', 20000, 20))}
        initial = {'weight': torch.tensor([.5])}
        best = snapshots[60]
        evaluation = {'Loss': .3, 'Accuracy': 50.}
        return {'synthetic_only': True, 'old_snaps': snapshots,
                'current_snaps': copy.deepcopy(snapshots), 'old_best': best,
                'new_best': copy.deepcopy(best), 'old_init': initial,
                'current_init': copy.deepcopy(initial), 'old_init_rng': rng,
                'current_init_rng': copy.deepcopy(rng), 'obs_old': observed,
                'obs_new': copy.deepcopy(observed), 'old_eval': evaluation,
                'new_eval': dict(evaluation), 'eval_model_state': best['model'],
                'old_eval_observation': {'samples': 10000, 'batches': 10},
                'eval_observations': {'test': {'samples': 10000, 'batches': 10}},
                'summary': {'train_loss': .3}, 'eval_summary': {'loss': .3},
                'elapsed_seconds': 0., 'gpu_peak_allocated_bytes': 0,
                'gpu_peak_reserved_bytes': 0}

    def run_pair(workspace, device, pair, components, *, cancel_check, checkpoint_dir):
        data, model, system, tracker, initial_rng = components
        assert device == 'cpu' and system.device == 'cpu' and callable(cancel_check)
        assert checkpoint_dir == workspace.parent / 'checkpoints'
        assert state.resources[workspace] == {'data': data, 'model': model}
        assert data.closed == 0 and isinstance(initial_rng, dict)
        assert tracker.assets_dir == workspace.parent
        state.events.append((workspace, 'compute'))
        cancel_check()
        if state.fault == 'cancel':
            request_path, = (workspace.parents[1] / 'attempts').glob('*/worker-request.json')
            request = io.read_json(request_path)
            io.write_json(request_path.parent / 'worker-cancel.json', {
                'schema': 1, 'action': 'cancel', 'request': request['request'],
                'identity': request['identity'], 'attempt': request['attempt']})
            cancel_check()  # The real worker checker must observe this bound control.
            raise AssertionError('worker ignored its cancellation control')
        if state.fault == 'compute-and-close':
            raise ValueError('synthetic compute primary failure')
        cell = workspace / 'matrix' / f'{pair[0]}_{pair[1]}'
        cell.mkdir(parents=True)
        observed = cell / 'OBSERVATIONS.pt'
        torch.save(raw_observations(), observed)
        manifest = workspace / 'SOURCE_MANIFEST.json'
        return {'synthetic_only': True, 'runs': [{'data': pair[0], 'model': pair[1],
                    'observations': str(observed), 'summary': {'train_loss': .3},
                    'elapsed_seconds': 0.}], 'main_commit': reference.MAIN,
                'dev_commit': 'synthetic-study-journey', 'device': device,
                'torch': torch.__version__,
                'source_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    support.run_pair = run_pair
    monkeypatch.setitem(sys.modules, support.__name__, support)
    names = ', '.join(name for name in vars(support) if not name.startswith('_'))
    (source / 'structure/algorithm/paired.py').write_text(
        f'from {support.__name__} import {names}\n', encoding='utf-8')
    (source / 'flow/prepare/data.py').write_text(
        f'from {support.__name__} import prepare\n', encoding='utf-8')

    declaration = yaml.safe_load((original / 'study.yaml').read_text(encoding='utf-8'))
    config = yaml.safe_load((original / 'experiment_config.yaml').read_text(encoding='utf-8'))
    declaration.pop('provenance')
    declaration['fixed']['system']['device'] = config['system']['device'] = 'cpu'
    (source / 'study.yaml').write_text(yaml.safe_dump(declaration), encoding='utf-8')
    (source / 'experiment_config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    specs = plan(source, run_root=tmp_path / 'output', version='synthetic-e2-new')
    publish(specs)
    formal_write = stages.write

    def write(ctx, record, draft=None):
        if draft is not None:
            workspace = ctx.layout.assets_dir / 'probe'
            assert state.resources[workspace]['data'].closed == 1
            assert not ctx.spec.result_path.exists()
            state.publications.append(ctx.spec.id)
        return formal_write(ctx, record, draft)
    monkeypatch.setattr(stages, 'write', write)
    state.specs, state.source = specs, source
    yield state
    # The real loader caches recipes by root; do not leak this fixture's fake module.
    for name, module in tuple(sys.modules.items()):
        if name.startswith(('_rpipe_recipe_', '_probe_')) and module is not None:
            filename = getattr(module, '__file__', None)
            paths = [filename, *getattr(module, '__path__', ())]
            if any(path and Path(path).is_relative_to(source) for path in paths):
                sys.modules.pop(name, None)


def test_synthetic_probe_completes_real_study_handoff(journey):
    from rpipe.flow.run import run
    from rpipe.flow.process import process_study
    from rpipe.structure.artifact import io, readout

    reports = [run(spec) for spec in journey.specs]
    assert [(report.status, report.quality, report.evidence_complete) for report in reports] == [
        ('succeeded', True, True)] * 8
    assert journey.publications == [spec.id for spec in journey.specs]
    assert [event for _, event in journey.events] == [
        'prepare', 'data', 'model', 'compute', 'close-data'] * 8
    for spec in journey.specs:
        record = readout.read_run(spec.ref)
        assert record['probe']['synthetic_only'] is True
        assert record['probe']['selected_passed'] is True
        assert not (spec.run_dir / '.execution').exists()
        assert journey.resources[spec.assets_dir / 'probe']['data'].closed == 1
        assert io.read_json(spec.run_dir / 'execution.json')['scientific_result_published'] is True
    before = {spec.result_path: spec.result_path.read_bytes() for spec in journey.specs}
    result = process_study(journey.specs)
    gate = io.read_json(journey.specs[0].study_root / 'summary/PROBE_COMPARISON.json')
    assert result['complete'] is True
    assert gate['passed'] is True and gate['complete'] is True and len(gate['runs']) == 8
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize('fault', ['prepare', 'cancel', 'close', 'compute-and-close'])
def test_probe_failure_preserves_peer_and_never_publishes_scientific_success(journey, fault):
    from rpipe.flow.run import run
    from rpipe.flow.process import process_study
    from rpipe.structure.artifact import io, readout

    peer, target = journey.specs[:2]
    assert run(peer).status == 'succeeded'
    before = {path: path.read_bytes() for path in peer.run_dir.rglob('*') if path.is_file()}
    journey.fault = fault
    report = run(target)
    assert report.status == ('unknown' if 'close' in fault else 'cancelled' if fault == 'cancel' else 'failed')
    assert not target.result_path.exists() and journey.publications == [peer.id]
    record = io.read_json(target.run_dir / 'execution.json')
    assert record['scientific_result_published'] is False
    assert readout.read_run(target.ref)['status'] == report.status
    assert {path: path.read_bytes() for path in before} == before
    workspace = target.assets_dir / 'probe'
    assert io.read_json(workspace / 'SYNTHETIC.json')['real_data'] is False
    if fault == 'prepare':
        assert workspace not in journey.resources
        assert report.error['type'] == 'ValueError'
        assert any(frame['function'] == 'prepare' for frame in report.error['frames'])
    else:
        assert journey.resources[workspace]['data'].closed == 1
    if 'close' in fault:
        assert (target.run_dir / '.execution').exists()
        assert any(error['type'] == 'OSError' and any(
            frame['function'] == 'close' for frame in error['frames'])
            for error in report.cleanup_errors)
    else:
        assert not (target.run_dir / '.execution').exists()
    if fault == 'compute-and-close':
        assert report.error['type'] == 'ValueError'
        assert any(frame['function'] == 'run_pair' for frame in report.error['frames'])
    summary = target.study_root / 'summary/PROBE_COMPARISON.json'
    io.write_json(summary, {'existing': 'derived peer'})
    saved = summary.read_bytes()
    with pytest.raises(RuntimeError, match='complete accepted formal Run aggregation'):
        process_study(journey.specs)
    assert summary.read_bytes() == saved
    assert {path: path.read_bytes() for path in before} == before
