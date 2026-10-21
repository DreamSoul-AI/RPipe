"""Pure declarations become validated RunSpecs without execution or output."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_plan_expands_factors_and_seeds_without_loading_payload_or_writing(
        source_study, run_root, file_bytes):
    from rpipe.api import plan

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('axes: {algorithm.lr: [0.1, 0.2]}\nrecipe: missing_recipe.py\n')
    before = file_bytes(source_study)
    specs = plan(source_study, run_root=run_root)
    assert [(s.config['algorithm']['lr'], s.seed) for s in specs] == [
        (0.1, 0), (0.1, 1), (0.2, 0), (0.2, 1)]
    assert len({s.id for s in specs}) == 4
    assert len({s.experiment for s in specs}) == 2
    assert specs == plan(source_study, run_root=run_root)
    assert file_bytes(source_study) == before
    assert not run_root.exists()


@pytest.mark.parametrize('declaration', [
    'study: toy\nseeds: [0, 0]\n',
    'study: toy\nseeds: [true]\n',
    'study: toy\nseeds: [-1]\n',
    'study: toy\nseeds: [0]\naxes: {algorithm.lr: [0.1, 0.1]}\n',
    'study: toy\nseeds: [0]\nunrecognized: value\n',
], ids=['duplicate-seed', 'boolean-seed', 'negative-seed', 'duplicate-factor', 'unknown-field'])
def test_plan_invalid_declaration_rejects_before_output(source_study, run_root, declaration):
    from rpipe.api import plan

    (source_study / 'study.yaml').write_text(declaration, encoding='utf-8')
    with pytest.raises((TypeError, ValueError)):
        plan(source_study, run_root=run_root)
    assert not run_root.exists()


def test_plan_rejects_runtime_output_inside_source(source_study):
    from rpipe.api import plan

    with pytest.raises((TypeError, ValueError)):
        plan(source_study, run_root=source_study / 'runs')
    assert not (source_study / 'runs').exists()
