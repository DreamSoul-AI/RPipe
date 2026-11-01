"""Data construction consumes recorded statistics without changing its defaults."""
from pathlib import Path
import pytest
from rpipe.structure.data.factory import _with_recorded_stats
from rpipe.structure.data.stats import load_stats
pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_data,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_recorded_stats_replace_the_builtin_table(tmp_path: Path):
    path = tmp_path / 'stats.yaml'
    path.write_text('name: MNIST\nmean: [0.1]\nstd: [0.2]\n', encoding='utf-8')
    recorded = load_stats(path)
    assert recorded is not None
    spec = {'mean': (0.5,), 'std': (0.5,), 'root': 'mnist'}
    out = _with_recorded_stats(spec, path)
    assert out['mean'] == (0.1,)
    assert out['std'] == (0.2,)
    assert spec['mean'] == (0.5,)
    assert _with_recorded_stats(spec, tmp_path / 'missing.yaml') is spec
