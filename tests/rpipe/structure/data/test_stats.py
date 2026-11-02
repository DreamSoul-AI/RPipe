"""Saved statistics parsing is read-only and strict only when declared required."""
import pytest
from rpipe.structure.data.stats import load_stats
pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_data,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('required', [True, False])
def test_saved_stats_load_without_rewriting_source(tmp_path, required):
    path = tmp_path / 'stats.yaml'
    original = b'mean: [0.1]\nstd: [0.2]\n'
    path.write_bytes(original)
    assert load_stats(path, required=required) == {'mean': [0.1], 'std': [0.2]}
    assert path.read_bytes() == original


@pytest.mark.parametrize('required', [True, False])
def test_missing_stats_stay_missing(tmp_path, required):
    path = tmp_path / 'missing.yaml'
    if required:
        with pytest.raises(FileNotFoundError): load_stats(path, required=True)
    else:
        assert load_stats(path) is None
    assert not path.exists()


@pytest.mark.parametrize('body', ['[]', 'mean: []\nstd: [1]', 'mean: [0]'])
def test_required_invalid_stats_do_not_silently_fall_back(tmp_path, body):
    path = tmp_path / 'stats.yaml'
    path.write_text(body, encoding='utf-8')
    with pytest.raises(ValueError): load_stats(path, required=True)
    assert path.read_text(encoding='utf-8') == body
