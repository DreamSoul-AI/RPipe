"""Immutable plans and finite enforceable budgets are admission boundaries."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_run_spec_effective_configuration_cannot_drift_after_planning(source_study, run_root):
    from rpipe.structure.make import plan

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(TypeError):
        spec.config['algorithm']['num_steps'] = 100
    assert spec.config['algorithm']['num_steps'] == 2




@pytest.mark.parametrize('field,value', [('id', value) for value in
    ('', '.', '..', '../outside', '..\\outside', 'nested/run', 'C:escape', 'C:\\escape',
     ' name', 'name ', 'name.', 'CON', 'NUL.txt')] +
    [('version', 'C:'), ('train_id', '../../outside'), ('train_id', 'C:escape')])
def test_run_spec_rejects_windows_drive_components_without_disk_access(
        source_study, run_root, field, value):
    from dataclasses import replace
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    changes = {field: value}
    if field == 'id':
        config = spec.to_config()
        config['id'] = value
        changes['config'] = config
    with pytest.raises(AdmissionError):
        replace(spec, **changes)
    assert not run_root.exists()


@pytest.mark.parametrize('field,value', [('seed', 777), ('id', 'unrelated-id')])
def test_run_spec_identity_fields_cannot_disagree_with_effective_config(
        source_study, run_root, field, value):
    from dataclasses import replace
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(AdmissionError):
        replace(spec, **{field: value})
    assert not run_root.exists()


@pytest.mark.parametrize('relative', ['.', '.tmp/forbidden-run', 'src/rpipe/generated'])
def test_run_root_cannot_overlap_the_library_checkout(source_study, relative):
    from pathlib import Path
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    checkout = Path(__file__).resolve().parents[3]
    with pytest.raises(AdmissionError, match='source'):
        plan(source_study, run_root=checkout / relative)


@pytest.mark.parametrize('marker', ['git_directory', 'git_file', 'pyproject'])
def test_run_root_protects_whole_study_project_not_just_study_folder(tmp_path, marker):
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    project = tmp_path / 'host'
    source = project / 'studies' / 'example'
    source.mkdir(parents=True)
    (source / 'study.yaml').write_text('study: example\n', encoding='utf-8')
    (source / 'experiment_config.yaml').write_text('algorithm: {mode: train}\n', encoding='utf-8')
    if marker == 'git_directory':
        (project / '.git').mkdir()
    elif marker == 'git_file':
        (project / '.git').write_text('gitdir: external-managed-worktree\n', encoding='utf-8')
    else:
        (project / 'pyproject.toml').write_text('[project]\nname="host"\n', encoding='utf-8')
    output = project / 'outputs'
    with pytest.raises(AdmissionError, match='source'):
        plan(source, run_root=output)
    assert not output.exists()


def test_source_project_protection_is_rechecked_before_writing(source_study, run_root):
    from rpipe.structure.make import plan, publish
    from rpipe.structure.control import AdmissionError

    specs = plan(source_study, run_root=run_root)
    (run_root.parent / '.git').write_text('gitdir: new-checkout-marker\n', encoding='utf-8')
    with pytest.raises(AdmissionError, match='source'):
        publish(specs)
    assert not run_root.exists()


def test_run_root_alias_into_library_source_is_rejected(source_study, tmp_path):
    from pathlib import Path
    from rpipe.structure.make import plan
    from rpipe.structure.control import AdmissionError

    library = Path(__file__).resolve().parents[3] / 'src' / 'rpipe'
    alias = tmp_path / 'source-alias'
    alias.symlink_to(library, target_is_directory=True)
    with pytest.raises(AdmissionError, match='source'):
        plan(source_study, run_root=alias / 'never-written')
