"""Study aggregation reads committed observations and keeps failures explicit."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import pickle
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_process,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


class UnsupportedObservation:
    """A benign research object is outside the saved observation contract."""


@pytest.fixture
def probe_case(tmp_path):
    from rpipe.structure.artifact import io
    from rpipe.structure.make import plan, publish

    script = Path(__file__).resolve().parents[5] / 'studies/main_probe/flow/process/run.py'
    spec = importlib.util.spec_from_file_location('_probe_saved_aggregation', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'source' / 'main_probe'
    source.mkdir(parents=True)
    (source / 'flow/process').mkdir(parents=True)
    (source / 'flow/process/run.py').write_bytes(script.read_bytes())
    (source / 'flow/process/__init__.py').write_text('from .run import run\n', encoding='utf-8')
    (source / 'study.yaml').write_text(
        'study: main_probe\nflow: {study_phases: true}\nseeds: [0]\naxes:\n  data.name: [MNIST, CIFAR10]\n'
        '  model.name: [linear, mlp, cnn, resnet18]\n', encoding='utf-8')
    (source / 'experiment_config.yaml').write_text(
        'experiment: probe\nversion: saved-v1\ndata: {name: MNIST, source: stub}\n'
        'model: {name: linear}\nalgorithm: {mode: train}\nsystem: {device: cpu}\n', encoding='utf-8')
    specs = plan(source, run_root=tmp_path / 'output')
    publish(specs)
    results, records = {}, {}
    for run in specs:
        config = run.to_config()
        result = {'id': run.id, 'identity': run.ref.identity, 'attempt': 'a' * 32,
                  'status': 'succeeded', 'quality': True, 'evidence_complete': True,
                  'metrics': {'loss': .5}, 'probe': {
                      'runs': [{'data': config['data']['name'], 'model': config['model']['name'], 'passed': True}],
                      'selected_complete': True, 'selected_passed': True, 'source_unchanged': True,
                      'archive_after': {'unchanged': True},
                      'main_commit': '98648f3a5c7db7dccf3ca806410d5b6fdee9484c',
                      'dev_commit': 'fixed-code', 'source_manifest_sha256': 'b' * 64,
                      'device': 'cpu', 'torch': 'synthetic-only'}}
        record = {'schema': 1, 'identity': run.ref.identity, 'attempt': 'a' * 32,
                  'owner_token': 'c' * 32, 'phase': 'closing', 'candidate_status': 'succeeded',
                  'quality': True, 'evidence_complete': True, 'scientific_result_published': True,
                  'derived_status': 'succeeded', 'error': None, 'cleanup_errors': [], 'cancelled': False}
        io.write_json(run.result_path, result)
        io.write_json(run.run_dir / 'execution.json', record)
        io.write_json(run.run_dir / 'attempts' / record['attempt'] / 'outcome.json', record)
        results[run.id], records[run.id] = result, record
    from rpipe.structure.artifact.readout import aggregate
    context = SimpleNamespace(scope='study', specs=specs,
                              layout=SimpleNamespace(study_root=specs[0].study_root),
                              state={'process': aggregate(tuple(run.ref for run in specs))})
    return SimpleNamespace(module=module, ctx=context, specs=specs,
                           results=results, records=records, io=io)


def _bytes(case):
    return {path: path.read_bytes() for path in case.ctx.layout.study_root.rglob('*')
            if path.is_file() and path.parent.name != 'summary'}


@pytest.mark.parametrize('sidecar', ['absent', 'false', 'corrupt'])
def test_probe_summary_uses_formal_result_without_a_second_scientific_source(probe_case, sidecar):
    case = probe_case
    if sidecar != 'absent':
        for run in case.specs:
            path = run.assets_dir / 'probe/COMPARISON.json'
            path.parent.mkdir(parents=True)
            path.write_text('{broken' if sidecar == 'corrupt' else '{"selected_passed": false}', encoding='utf-8')
    before = _bytes(case)
    case.module.run(case.ctx)
    body = case.ctx.state['probe']
    assert body['passed'] is True and body['complete'] is True and len(body['runs']) == 8
    assert len(body['inputs']['runs']) == 8 and len(body['inputs']['index_sha256']) == 64
    assert all(Path(row['evidence']).name == 'result.json' for row in body['runs'])
    assert _bytes(case) == before


@pytest.mark.parametrize('damage', ['failed_gate', 'missing_probe', 'quality', 'incomplete',
                                  'busy', 'missing_outcome', 'source', 'wrong_pair'])
def test_probe_summary_cannot_promote_unaccepted_records_from_a_passing_sidecar(probe_case, damage):
    case = probe_case
    run = case.specs[0]
    result = copy.deepcopy(case.results[run.id])
    path = run.assets_dir / 'probe/COMPARISON.json'
    case.io.write_json(path, result['probe'])
    if damage == 'failed_gate':
        result['probe']['selected_passed'] = False
    elif damage == 'missing_probe':
        result.pop('probe')
    elif damage == 'quality':
        result['quality'] = False
        record = {**case.records[run.id], 'quality': False}
        case.io.write_json(run.run_dir / 'execution.json', record)
        case.io.write_json(run.run_dir / 'attempts' / record['attempt'] / 'outcome.json', record)
    elif damage == 'incomplete':
        record = {**case.records[run.id], 'evidence_complete': False}
        case.io.write_json(run.run_dir / 'execution.json', record)
        case.io.write_json(run.run_dir / 'attempts' / record['attempt'] / 'outcome.json', record)
    elif damage == 'busy':
        (run.run_dir / '.execution').mkdir()
    elif damage == 'missing_outcome':
        (run.run_dir / 'attempts' / result['attempt'] / 'outcome.json').unlink()
    elif damage == 'source':
        result['identity']['source_digest'] = 'd' * 64
    else:
        result['probe']['runs'][0]['data'] = 'wrong-pair'
    case.io.write_json(run.result_path, result)
    before = _bytes(case)
    with pytest.raises((ValueError, RuntimeError)):
        case.module.run(case.ctx)
    assert case.ctx.state.get('probe', {}).get('passed') is not True
    assert _bytes(case) == before


@pytest.mark.parametrize('mutation', ['index', 'index_bytes', 'result', 'outcome'])
def test_probe_input_drift_preserves_preexisting_derived_summary(probe_case, monkeypatch, mutation):
    import rpipe.structure.artifact.readout as readout

    case = probe_case
    root, run = case.ctx.layout.study_root, case.specs[0]
    summary = root / 'summary/PROBE_COMPARISON.json'
    case.io.write_json(summary, {'previous': 'derived evidence'})
    old = summary.read_bytes()
    native = readout.read_run
    changed = False
    def read(ref):
        nonlocal changed
        result = native(ref)
        if not changed:
            changed = True
            if mutation == 'index':
                index = case.io.load_index(root)
                index['experiments'][0]['runs'][0]['seed'] = False
                case.io.write_json(root / 'index.json', index)
            elif mutation == 'index_bytes':
                index_path = root / 'index.json'
                index_path.write_bytes(index_path.read_bytes() + b'\n')
            elif mutation == 'result':
                case.io.write_json(run.result_path, {**case.results[run.id], 'unrelated_new_observation': 1})
            else:
                case.io.write_json(run.run_dir / 'attempts' / ('a' * 32) / 'outcome.json', {})
        return result
    # Change a true input after an accepted observation; the publisher must recheck.
    monkeypatch.setattr(readout, 'read_run', read)
    with pytest.raises(ValueError, match='inputs changed'):
        case.module.run(case.ctx)
    assert summary.read_bytes() == old


def test_probe_rejects_a_plan_outside_the_published_identity_set(probe_case):
    case = probe_case
    case.ctx.specs = case.specs[:-1]
    before = _bytes(case)
    with pytest.raises(ValueError, match='explicit current plan'):
        case.module.run(case.ctx)
    assert _bytes(case) == before


def test_real_process_study_loads_the_selected_hook_and_keeps_gate_out_of_statistics(probe_case):
    from rpipe.flow.process import process_study

    case = probe_case
    before = _bytes(case)
    result = process_study(case.specs)
    assert result['complete'] is True
    assert all(row['quality'] is True and row['metrics'] == {'loss': .5} for row in result['runs'])
    assert all(group['metrics']['loss']['mean'] == .5 for group in result['experiments'].values())
    summary = case.ctx.layout.study_root / 'summary/PROBE_COMPARISON.json'
    assert case.io.read_json(summary)['passed'] is True
    assert _bytes(case) == before


def test_old_boolean_metric_cannot_be_promoted_by_the_study_hook(probe_case):
    from rpipe.flow.process import process_study

    case = probe_case
    run = case.specs[0]
    case.io.write_json(run.result_path, {**case.results[run.id], 'metrics': {'probe_passed': True}})
    summary = run.study_root / 'summary/PROBE_COMPARISON.json'
    case.io.write_json(summary, {'previous': 'retain this derived evidence'})
    before = summary.read_bytes()
    with pytest.raises(RuntimeError, match='complete accepted formal'):
        process_study(case.specs)
    assert summary.read_bytes() == before
    assert case.io.read_json(run.study_root / 'summary/process.json')['complete'] is False


@pytest.mark.parametrize('difference', [False, True, 'research_class'])
def test_probe_collect_and_write_use_quality_and_preserve_numeric_metrics(tmp_path, monkeypatch, difference):
    import torch

    source = Path(__file__).resolve().parents[5] / 'studies/main_probe'
    name = '_probe_quality_' + uuid.uuid4().hex
    package = ModuleType(name)
    package.__path__ = [str(source)]
    monkeypatch.setitem(sys.modules, name, package)
    try:
        collect = importlib.import_module(name + '.flow.collect.run')
        write = importlib.import_module(name + '.flow.write.run')
        cell = tmp_path / 'matrix/MNIST_linear'
        cell.mkdir(parents=True)
        observations = cell / 'OBSERVATIONS.pt'
        snapshot = {'step': 30, 'train': {'Loss': .3}, 'test': {'Accuracy': .8},
                    'model': {'weight': torch.tensor([.5])},
                    'optimizer': {'state': {0: {'step': 30}}}, 'scheduler': {'last_epoch': 30}}
        current = copy.deepcopy(snapshot)
        raw = {'old_snaps': {30: snapshot}, 'current_snaps': {30: current},
               'old_best': snapshot, 'new_best': current}
        if difference is True:
            current['model']['weight'] += 1
        torch.save(raw, observations)
        (tmp_path / 'SOURCE_MANIFEST.json').write_text('{"files": {}}', encoding='utf-8')
        # Isolate the collect handoff; the real observed-tree gate still runs in write.
        monkeypatch.setattr(collect, 'compare_observations', lambda raw, data, model: {
            'data': data, 'model': model, 'passed': True})
        monkeypatch.setattr(collect, 'source_manifest', lambda: {})
        monkeypatch.setattr(collect, 'MainArchive', lambda workspace: SimpleNamespace(
            verify_exports=lambda: {'unchanged': True}))
        report = {'runs': [{'data': 'MNIST', 'model': 'linear', 'observations': str(observations)}]}
        collected = {'metrics': {'loss': .5}}
        ctx = SimpleNamespace(control=SimpleNamespace(data={'name': 'MNIST'}, model={'name': 'linear'}),
                              state={'probe_execution': report, 'collected': collected,
                                     'algorithm': SimpleNamespace(workspace=tmp_path)})
        if difference == 'research_class':
            torch.save(UnsupportedObservation(), observations)
            before = observations.read_bytes()
            with pytest.raises(pickle.UnpicklingError):
                collect.run(ctx)
            assert 'quality' not in collected and observations.read_bytes() == before
            return
        collect.run(ctx)
        assert collected['quality'] is True and collected['metrics'] == {'loss': .5}
        ctx.state['result_draft'] = {'metrics': dict(collected['metrics']), 'quality': collected['quality']}
        if difference:
            with pytest.raises(RuntimeError, match='observed state comparison failed'):
                write.run(ctx)
        else:
            write.run(ctx)
        assert collected['quality'] is (not difference)
        assert ctx.state['result_draft']['quality'] is (not difference)
        assert ctx.state['result_draft']['metrics'] == {'loss': .5}
        assert not (tmp_path / 'result.json').exists()
    finally:
        for key in tuple(sys.modules):
            if key.startswith(name + '.'):
                sys.modules.pop(key, None)
