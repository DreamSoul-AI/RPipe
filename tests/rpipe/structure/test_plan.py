"""Pure declarations become validated RunSpecs without execution or output."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_plan_expands_factors_and_seeds_without_loading_payload_or_writing(
        source_study, run_root, file_bytes):
    from rpipe.structure.make import plan

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
    from rpipe.structure.make import plan

    (source_study / 'study.yaml').write_text(declaration, encoding='utf-8')
    with pytest.raises((TypeError, ValueError)):
        plan(source_study, run_root=run_root)
    assert not run_root.exists()


def test_plan_rejects_runtime_output_inside_source(source_study):
    from rpipe.structure.make import plan

    with pytest.raises((TypeError, ValueError)):
        plan(source_study, run_root=source_study / 'runs')
    assert not (source_study / 'runs').exists()


def test_train_only_display_tags_do_not_break_same_seed_eval_pair(source_study, run_root):
    from rpipe.structure.make import plan

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('axes: {algorithm.mode: [train, eval]}\n'
                     'tags:\n  - when: {algorithm.mode: train}\n    tags: [baseline]\n')
    specs = plan(source_study, run_root=run_root)
    trains = {spec.seed: spec for spec in specs if spec.config['algorithm']['mode'] == 'train'}
    evaluations = [spec for spec in specs if spec.config['algorithm']['mode'] == 'eval']
    assert len(evaluations) == len(trains) == 2
    for evaluation in evaluations:
        assert evaluation.train_id == trains[evaluation.seed].id
        assert 'baseline' in trains[evaluation.seed].config['tags']
        assert 'baseline' not in evaluation.config.get('tags', ())
    assert not run_root.exists()


def test_run_description_is_rendered_from_effective_scientific_settings(source_study, run_root):
    from rpipe.structure.make import plan

    original = plan(source_study, run_root=run_root)
    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('fixed: {data: {config: {train_size: 5}}}\n'
                     'run_description: "{data_name}/{model_name}/{mode}/{seed}/{train_size}/{source}"\n')
    specs = plan(source_study, run_root=run_root)
    assert [s.config['description'] for s in specs] == [
        'Toy/linear/train/0/5/custom_torch', 'Toy/linear/train/1/5/custom_torch']
    assert [s.seed for s in original] == [s.seed for s in specs]
    assert not run_root.exists()


@pytest.mark.parametrize('template', ['{missing}', '{seed.__class__}', '{seed:1000000000}', '{seed!r}'])
def test_run_description_rejects_unknown_or_unbounded_format_syntax(source_study, run_root, template):
    from rpipe.structure.make import plan
    import yaml

    declaration = {'study': 'toy', 'run_description': template}
    (source_study / 'study.yaml').write_text(yaml.safe_dump(declaration), encoding='utf-8')
    with pytest.raises(ValueError, match='run_description'):
        plan(source_study, run_root=run_root)
    assert not run_root.exists()


@pytest.mark.parametrize('declaration, phrase', [
    ('freeze: true\n', 'freeze'),
    ('flow: {prepare_shared: true}\n', 'prepare_shared'),
    ('flow: {study_phases: "false"}\n', 'study_phases'),
    ('flow: {unknown_stage: true}\n', 'flow'),
])
def test_unimplemented_or_ambiguous_declarations_fail_explicitly(source_study, run_root, declaration, phrase):
    from rpipe.structure.make import plan

    (source_study / 'study.yaml').write_text('study: toy\n' + declaration, encoding='utf-8')
    with pytest.raises(ValueError, match=phrase):
        plan(source_study, run_root=run_root)
    assert not run_root.exists()
