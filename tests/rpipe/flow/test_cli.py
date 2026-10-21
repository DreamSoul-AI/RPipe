"""CLI plan/read commands have observable zero execution and zero output writes."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('command', ['plan', 'status', 'logs', 'report'])
def test_cli_read_command_preserves_source_and_runtime_bytes(
        source_study, run_root, payload_factory, file_bytes, command, capsys):
    from rpipe.api import plan, run
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
    assert main(['compare', str(tmp_path / 'left'), str(tmp_path / 'right'), '--legacy']) == 0
    assert '"verified": false' in capsys.readouterr().out
    assert file_bytes(tmp_path) == before


def test_cli_make_requires_explicit_run_root_before_any_write(source_study, file_bytes):
    from rpipe.flow.cli import main

    before = file_bytes(source_study)
    with pytest.raises(SystemExit) as exc:
        main(['make', str(source_study)])
    assert exc.value.code == 2
    assert file_bytes(source_study) == before
