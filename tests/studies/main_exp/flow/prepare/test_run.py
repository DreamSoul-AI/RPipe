"""The Study preparation gate checks adapters and records actual runtime controls."""
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


EXPECTED = {'deterministic': True, 'cudnn_deterministic': True, 'cudnn_benchmark': False,
            'cudnn_allow_tf32': False, 'matmul_allow_tf32': False}


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    namespace = '_main_exp_prepare_' + uuid4().hex
    package = ModuleType(namespace)
    package.__path__ = [str(Path(__file__).resolve().parents[5] / 'studies/main_exp')]
    before = set(sys.modules)
    monkeypatch.setitem(sys.modules, namespace, package)
    module = importlib.import_module(namespace + '.flow.prepare.run')
    ctx = SimpleNamespace(state={layer: SimpleNamespace(
        source=module.SOURCE, meta={'historical_commit': module.COMMIT})
        for layer in ('data', 'model')},
        layout=SimpleNamespace(assets_dir=tmp_path / 'assets'))
    yield module, ctx
    for name in set(sys.modules) - before:
        if name == namespace or name.startswith(namespace + '.') or name.startswith('_main_exp_structure_'):
            sys.modules.pop(name, None)


def _runtime(monkeypatch, actual):
    cudnn = SimpleNamespace(deterministic=actual['cudnn_deterministic'],
                            benchmark=actual['cudnn_benchmark'],
                            allow_tf32=actual['cudnn_allow_tf32'])
    torch = SimpleNamespace(are_deterministic_algorithms_enabled=lambda: actual['deterministic'],
        backends=SimpleNamespace(cudnn=cudnn,
            cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=actual['matmul_allow_tf32']))))
    monkeypatch.setitem(sys.modules, 'torch', torch)


@pytest.mark.parametrize('layer', ['data', 'model'])
@pytest.mark.parametrize('fault', ['source', 'commit', 'missing_commit'])
def test_preparation_refuses_wrong_adapter_before_runtime(preparation, monkeypatch, layer, fault):
    module, ctx = preparation
    adapter = ctx.state[layer]
    if fault == 'source':
        adapter.source = 'unrelated_source'
    elif fault == 'commit':
        adapter.meta['historical_commit'] = '0' * 40
    else:
        adapter.meta.clear()
    monkeypatch.setattr(module, 'validate_runtime', lambda *args: pytest.fail('adapter rejection must occur first'))

    with pytest.raises(ValueError, match=f'main_exp {layer} does not use the declared historical recipe'):
        module.run(ctx)

    assert 'runtime_validation' not in ctx.state
    assert not ctx.layout.assets_dir.exists()


@pytest.mark.parametrize('flag', list(EXPECTED))
def test_preparation_preserves_actual_runtime_mismatch_evidence(preparation, monkeypatch, flag):
    module, ctx = preparation
    actual = dict(EXPECTED, **{flag: not EXPECTED[flag]})
    _runtime(monkeypatch, actual)
    peer = ctx.layout.assets_dir / 'peer.txt'
    peer.parent.mkdir(parents=True)
    peer.write_bytes(b'peer evidence')

    with pytest.raises(ValueError, match='main_exp actual runtime differs from declared controls'):
        module.run(ctx)

    evidence = json.loads((ctx.layout.assets_dir / 'runtime_validation.json').read_text(encoding='utf-8'))
    assert evidence == ctx.state['runtime_validation'] == {
        'expected': EXPECTED, 'actual': actual, 'passed': False}
    assert peer.read_bytes() == b'peer evidence'


def test_preparation_records_matching_runtime_as_passed(preparation, monkeypatch):
    module, ctx = preparation
    _runtime(monkeypatch, EXPECTED)

    module.run(ctx)

    evidence = json.loads((ctx.layout.assets_dir / 'runtime_validation.json').read_text(encoding='utf-8'))
    assert evidence == ctx.state['runtime_validation'] == {
        'expected': EXPECTED, 'actual': EXPECTED, 'passed': True}
