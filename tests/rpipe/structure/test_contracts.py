"""Immutable plans and finite enforceable budgets are admission boundaries."""

import math

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_run_spec_effective_configuration_cannot_drift_after_planning(source_study, run_root):
    from rpipe.api import plan

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(TypeError):
        spec.config['algorithm']['num_steps'] = 100
    assert spec.config['algorithm']['num_steps'] == 2


@pytest.mark.parametrize('field,value', [
    ('timeout_seconds', math.nan), ('timeout_seconds', math.inf),
    ('timeout_seconds', -1), ('memory_bytes', -1), ('memory_bytes', True),
])
def test_budget_invalid_limit_is_rejected(field, value):
    from rpipe.structure.contracts import Budget

    with pytest.raises((TypeError, ValueError)):
        Budget(**{field: value}).validate()


@pytest.mark.parametrize('field,value', [('id', value) for value in
    ('', '.', '..', '../outside', '..\\outside', 'nested/run', 'C:escape', 'C:\\escape',
     ' name', 'name ', 'name.', 'CON', 'NUL.txt')] +
    [('version', 'C:'), ('train_id', '../../outside'), ('train_id', 'C:escape')])
def test_run_spec_rejects_windows_drive_components_without_disk_access(
        source_study, run_root, field, value):
    from dataclasses import replace
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

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
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(AdmissionError):
        replace(spec, **{field: value})
    assert not run_root.exists()


@pytest.mark.parametrize('relative', ['.', '.tmp/forbidden-run', 'src/rpipe/generated'])
def test_run_root_cannot_overlap_the_library_checkout(source_study, relative):
    from pathlib import Path
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

    checkout = Path(__file__).resolve().parents[3]
    with pytest.raises(AdmissionError, match='source'):
        plan(source_study, run_root=checkout / relative)


@pytest.mark.parametrize('marker', ['git_directory', 'git_file', 'pyproject'])
def test_run_root_protects_whole_study_project_not_just_study_folder(tmp_path, marker):
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

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
    from rpipe.api import plan, make
    from rpipe.structure.contracts import AdmissionError

    specs = plan(source_study, run_root=run_root)
    (run_root.parent / '.git').write_text('gitdir: new-checkout-marker\n', encoding='utf-8')
    with pytest.raises(AdmissionError, match='source'):
        make(specs)
    assert not run_root.exists()


def test_run_root_alias_into_library_source_is_rejected(source_study, tmp_path):
    from pathlib import Path
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

    library = Path(__file__).resolve().parents[3] / 'src' / 'rpipe'
    alias = tmp_path / 'source-alias'
    alias.symlink_to(library, target_is_directory=True)
    with pytest.raises(AdmissionError, match='source'):
        plan(source_study, run_root=alias / 'never-written')
