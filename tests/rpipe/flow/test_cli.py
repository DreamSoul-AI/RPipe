"""CLI plan/read commands have observable zero execution and zero output writes."""
from rpipe.structure import make as planning

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('command', ['plan', 'status', 'logs', 'report'])
def test_cli_read_command_preserves_source_and_runtime_bytes(
        source_study, run_root, payload_factory, file_bytes, command, capsys):
    from rpipe.structure.make import plan
    from rpipe.api import run
    from rpipe.flow.cli import main

    specs = plan(source_study, run_root=run_root)
    assert run(specs[0], payload=payload_factory()).status == 'succeeded'
    before_source, before_runtime = file_bytes(source_study), file_bytes(run_root)
    assert main([command, str(source_study), '--run-root', str(run_root)]) == 0
    assert capsys.readouterr().err == ''
    assert file_bytes(source_study) == before_source
    assert file_bytes(run_root) == before_runtime


def test_cli_legacy_compare_is_readonly_and_unverified(tmp_path, file_bytes, capsys):
    from rpipe.flow.cli import main

    for name in ('left', 'right'):
        directory = tmp_path / name
        directory.mkdir()
        (directory / 'result.json').write_text('{"status":"succeeded"}', encoding='utf-8')
    before = file_bytes(tmp_path)
    assert main(['compare', str(tmp_path / 'left'), str(tmp_path / 'right'), '--legacy']) == 1
    import json
    observed = json.loads(capsys.readouterr().out)
    assert observed['passed'] is False
    assert observed['verified'] is False and observed['evidence_complete'] is False
    assert file_bytes(tmp_path) == before


def test_cli_make_requires_explicit_run_root_before_any_write(source_study, file_bytes):
    from rpipe.flow.cli import main

    before = file_bytes(source_study)
    with pytest.raises(SystemExit) as exc:
        main(['make', str(source_study)])
    assert exc.value.code == 2
    assert file_bytes(source_study) == before


def test_cli_retry_is_passed_only_when_explicitly_selected(source_study, run_root, monkeypatch, capsys):
    from rpipe import api
    from rpipe.flow.cli import main
    from rpipe.structure.contracts import RunOutcome

    calls = []
    def launch(specs, **kwargs):
        calls.append(kwargs['retry'])
        return (RunOutcome('failed'),)
    monkeypatch.setattr(api, 'launch', launch)
    for flag in ([], ['--retry']):
        assert main(['launch', str(source_study), '--run-root', str(run_root), *flag]) == 1
        assert capsys.readouterr().err == ''
    assert calls == [False, True]


def test_cli_tensorboard_passes_explicit_selected_run_ids(source_study, run_root, tmp_path, monkeypatch, capsys):
    from rpipe import api
    from rpipe.flow import exports
    from rpipe.flow.cli import main

    specs = planning.plan(source_study, run_root=run_root)
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
    from rpipe import api
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

    monkeypatch.setattr(profile.DataFactory, 'build', build)
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
