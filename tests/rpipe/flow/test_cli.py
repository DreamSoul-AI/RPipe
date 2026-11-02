"""CLI plan/read commands have observable zero execution and zero output writes."""
from rpipe.structure import make as planning

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('command', ['plan', 'status', 'logs', 'report'])
def test_cli_read_command_preserves_source_and_runtime_bytes(
        source_study, run_root, science_factory, file_bytes, command, capsys, science_worker):
    from rpipe.structure.make import plan, publish
    from rpipe.flow.run import run
    from rpipe.flow.cli import main

    specs = plan(source_study, run_root=run_root)
    publish(specs)
    science_worker(science_factory())
    assert run(specs[0]).status == 'succeeded'
    before_source, before_runtime = file_bytes(source_study), file_bytes(run_root)
    assert main([command, str(source_study), '--run-root', str(run_root)]) == 0
    assert capsys.readouterr().err == ''
    assert file_bytes(source_study) == before_source
    assert file_bytes(run_root) == before_runtime


def test_cli_rejects_retired_legacy_option_without_any_write(tmp_path, file_bytes, capsys):
    from rpipe.flow.cli import main

    for name in ('left', 'right'):
        directory = tmp_path / name
        directory.mkdir()
        (directory / 'result.json').write_text('{"status":"succeeded"}', encoding='utf-8')
    before = file_bytes(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(['compare', str(tmp_path / 'left'), str(tmp_path / 'right'), '--legacy'])
    assert caught.value.code == 2
    assert capsys.readouterr().err
    assert file_bytes(tmp_path) == before


def test_cli_reads_declared_nondefault_version_after_source_content_changes(
        source_study, run_root, file_bytes, monkeypatch, capsys):
    from rpipe.structure.artifact.io import load_config, write_config
    from rpipe.flow.cli import main
    path = source_study / 'experiment_config.yaml'
    config = load_config(path)
    config['version'] = 'declared-v1'
    write_config(path, config)
    specs = planning.plan(source_study, run_root=run_root)
    planning.publish(specs)
    assert specs[0].version == 'declared-v1'
    config['data']['name'] = 'changed_after_publication'
    write_config(path, config)
    before = file_bytes(run_root)
    monkeypatch.setattr(planning, 'plan', lambda *args, **kwargs: pytest.fail('readonly command replanned current source'))
    for command in ('status', 'report'):
        assert main([command, str(source_study), '--run-root', str(run_root)]) == 0
        assert specs[0].id in capsys.readouterr().out
    assert file_bytes(run_root) == before


def test_cli_explicit_saved_version_reads_when_source_path_is_unavailable(
        source_study, run_root, tmp_path, file_bytes, capsys):
    from rpipe.flow.cli import main
    specs = planning.plan(source_study, run_root=run_root, version='recorded-v1')
    planning.publish(specs)
    unavailable = tmp_path / 'unavailable' / source_study.name
    assert not unavailable.exists()
    before = file_bytes(run_root)
    assert main(['status', str(unavailable), '--run-root', str(run_root), '--version', 'recorded-v1']) == 0
    assert specs[0].id in capsys.readouterr().out
    assert file_bytes(run_root) == before


def test_cli_make_requires_explicit_run_root_before_any_write(source_study, file_bytes):
    from rpipe.flow.cli import main

    before = file_bytes(source_study)
    with pytest.raises(SystemExit) as exc:
        main(['make', str(source_study)])
    assert exc.value.code == 2
    assert file_bytes(source_study) == before


def test_cli_retry_is_passed_only_when_explicitly_selected(source_study, run_root, monkeypatch, capsys):
    from rpipe.flow.cli import main
    from rpipe.flow.context import RunReport

    calls = []
    def launch(specs, **kwargs):
        calls.append(kwargs['retry'])
        return (RunReport('failed'),)
    from rpipe.flow import supervision
    monkeypatch.setattr(supervision, 'launch', launch)
    for flag in ([], ['--retry']):
        assert main(['launch', str(source_study), '--run-root', str(run_root), *flag]) == 1
        assert capsys.readouterr().err == ''
    assert calls == [False, True]


def test_cli_tensorboard_passes_explicit_selected_run_ids(source_study, run_root, tmp_path, monkeypatch, capsys):
    from rpipe.flow import exports
    from rpipe.flow.cli import main

    specs = planning.plan(source_study, run_root=run_root)
    planning.publish(specs)
    selections = []
    def export(root, out, **kwargs):
        selections.append(kwargs['run_ids'])
        return {}
    monkeypatch.setattr(exports, 'export_tensorboard', export)
    for selection in ([], ['--id', specs[0].id]):
        assert main(['tensorboard', str(source_study), '--run-root', str(run_root),
                     '--out', str(tmp_path / 'export'), *selection]) == 0
        assert capsys.readouterr().err == ''
    assert selections == [None, [specs[0].id]]


@pytest.mark.parametrize('destination', ['input', 'checkout'])
def test_cli_tensorboard_rejects_input_or_checkout_destination_before_export(
        source_study, run_root, tmp_path, monkeypatch, capsys, destination):
    import yaml
    from rpipe.flow import exports
    from rpipe.flow.cli import main

    raw = tmp_path / 'input'
    raw.mkdir()
    config_path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['data']['path'] = str(raw)
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    (source_study.parent / '.git').mkdir()
    out = raw / 'new-export' if destination == 'input' else source_study.parent / 'new-export'
    called = []
    monkeypatch.setattr(exports, 'export_tensorboard', lambda *args, **kwargs: called.append(True))
    assert main(['tensorboard', str(source_study), '--run-root', str(run_root), '--out', str(out)]) == 1
    assert not called and not out.exists()
    assert capsys.readouterr().err


def test_cli_data_profiles_only_the_selected_effective_input(source_study, run_root, monkeypatch, capsys):
    import json
    import yaml
    import torch
    from rpipe.flow.cli import main
    from rpipe.structure.data import profile
    from rpipe.structure.data.factory import Data

    (source_study / 'study.yaml').write_text(
        'study: Profile\nfixed: {data: {name: MNIST}}\naxes:\n  data.path: [../left, ../right]\nseeds: [0]\n', encoding='utf-8')
    (source_study / 'experiment_config.yaml').write_text(
        yaml.safe_dump({'data': {'name': 'MNIST', 'source': 'custom'}, 'algorithm': {'mode': 'train'}}),
        encoding='utf-8')
    selected = planning.plan(source_study, run_root=run_root)[1]
    calls = []

    def build(config, shared, **kwargs):
        calls.append(config.to_mapping())
        assert config.source == 'custom'
        assert config.path == str((source_study / '../right').resolve())
        return Data(name='MNIST', source='custom', meta={}, loaders={
            'train': [(torch.tensor([[[[0.7, 0.8]]]]), torch.tensor([0]))],
        })

    monkeypatch.setattr(profile, 'build_data', build)
    assert main(['data', str(source_study), '--run-root', str(run_root), '--id', selected.id]) == 0
    captured = capsys.readouterr()
    assert captured.err == '' and len(calls) == 1
    written = json.loads(captured.out)
    from pathlib import Path
    assert yaml.safe_load(Path(written[0]).read_text(encoding='utf-8'))['mean'] == [0.75]
    calls.clear()
    assert main(['data', str(source_study), '--run-root', str(run_root)]) == 1
    assert not calls
    assert 'conflicting profile inputs' in capsys.readouterr().err


@pytest.mark.parametrize('command', ['plan', 'make', 'run', 'launch'])
def test_cli_template_budget_conflict_rejects_without_execution_or_output(
        source_study, run_root, file_bytes, command, monkeypatch, capsys):
    import yaml
    from rpipe.flow.cli import main
    from rpipe.flow import supervision
    base_path = source_study / 'experiment_config.yaml'
    base = yaml.safe_load(base_path.read_text(encoding='utf-8'))
    base['algorithm'] = {'mode': 'train', 'num_epochs': 20}
    base_path.write_text(yaml.safe_dump(base), encoding='utf-8')
    (source_study / 'study.yaml').write_text(
        'study: conflict\nseeds: [0]\nfixed: {algorithm: {num_steps: 2, progress_unit: step}}\n',
        encoding='utf-8')
    before = file_bytes(source_study)
    monkeypatch.setattr(supervision, 'launch', lambda *args, **kwargs: pytest.fail('executed conflict'))
    assert main([command, str(source_study), '--run-root', str(run_root)]) == 1
    assert 'num_epochs' in capsys.readouterr().err
    assert file_bytes(source_study) == before and not run_root.exists()


@pytest.mark.parametrize('selected', [False, True])
def test_cli_plan_show_config_uses_effective_conditions_and_selected_identity(
        source_study, run_root, selected, file_bytes, capsys):
    import json
    import yaml
    from rpipe.flow.cli import main
    path = source_study / 'experiment_config.yaml'
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    config['system']['device'] = 'cpu'
    config['algorithm'] = {'mode': 'train', 'num_epochs': 20}
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    (source_study / 'study.yaml').write_text(
        'study: preview\nseeds: [0]\nfixed: {version: declared, system: {device: cuda}, '
        'algorithm: {num_epochs: null, num_steps: 2}}\n'
        'axes: {data.config.batch_size: [4, 8]}\n', encoding='utf-8')
    specs = planning.plan(source_study, run_root=run_root, version='requested')
    before = file_bytes(source_study)
    argv = ['plan', str(source_study), '--run-root', str(run_root), '--version', 'requested', '--show-config']
    if selected:
        argv += ['--id', specs[1].id]
    assert main(argv) == 0
    rows = json.loads(capsys.readouterr().out)
    expected = [specs[1]] if selected else specs
    assert [row['id'] for row in rows] == [spec.id for spec in expected]
    for row, spec in zip(rows, expected):
        assert row['config'] == spec.to_config()
        assert row['version'] == row['config']['version'] == 'requested'
        assert row['config']['system']['device'] == 'cuda'
        assert row['config']['algorithm']['num_epochs'] is None
        assert row['config']['algorithm']['num_steps'] == 2
    assert file_bytes(source_study) == before and not run_root.exists()


def test_cli_plan_without_show_config_keeps_default_metadata_contract(source_study, run_root, capsys):
    import json
    from rpipe.flow.cli import main
    assert main(['plan', str(source_study), '--run-root', str(run_root)]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert all(set(row) == {'id', 'experiment', 'seed', 'source_digest', 'run_dir', 'version', 'train_id'}
               for row in rows)
    assert not run_root.exists()


def test_cli_plan_show_config_fresh_process_never_imports_backend_or_recipe(
        source_study, run_root, tmp_path, file_bytes):
    import os
    import subprocess
    import sys
    from pathlib import Path
    (source_study / 'study.yaml').write_text('study: readonly\nseeds: [0]\nrecipe: recipe.py\n', encoding='utf-8')
    (source_study / 'recipe.py').write_text("raise AssertionError('recipe executed')\n", encoding='utf-8')
    script = tmp_path / 'preview.py'
    script.write_text("""import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch','torchvision','transformers','datasets','kornia'}:
            raise AssertionError('backend imported: '+fullname)
sys.meta_path.insert(0,Block())
from rpipe.flow.cli import main
raise SystemExit(main(['plan',sys.argv[1],'--run-root',sys.argv[2],'--show-config']))
""", encoding='utf-8')
    before = file_bytes(source_study)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3] / 'src'),
               PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, '-B', str(script), str(source_study), str(run_root)],
                       env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stdout + p.stderr
    assert file_bytes(source_study) == before and not run_root.exists()


@pytest.mark.parametrize('command', ['make', 'run', 'status'])
def test_cli_show_config_option_is_only_valid_for_plan(source_study, run_root, command, file_bytes, capsys):
    from rpipe.flow.cli import main
    before = file_bytes(source_study)
    with pytest.raises(SystemExit) as caught:
        main([command, str(source_study), '--run-root', str(run_root), '--show-config'])
    assert caught.value.code == 2
    assert capsys.readouterr().err
    assert file_bytes(source_study) == before and not run_root.exists()
