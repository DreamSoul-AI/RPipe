import json
from pathlib import Path

import pytest

from rpipe.flow import cli
from rpipe.structure.comparison import compare_runs

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_artifact,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def _run(root: Path, name: str, *, weight: float = 1.0, loss: float = 0.5, history=(1.0, 0.5)) -> Path:
    import torch

    run = root / name
    (run / 'assets' / 'tracker').mkdir(parents=True)
    (run / 'assets' / 'checkpoints').mkdir(parents=True)
    (run / 'result.json').write_text(
        json.dumps({'status': 'succeeded', 'control': {}, 'paths': {}, 'metrics': {'loss': loss, 'tag': 'x'}}),
        encoding='utf-8',
    )
    (run / 'assets' / 'tracker' / 'tracker_state.json').write_text(
        json.dumps({'splits': {'train': {'Loss': {'history': list(history)}}}}),
        encoding='utf-8',
    )
    torch.save(
        {
            'model': {'w': torch.full((2, 2), weight), 'b': torch.zeros(2)},
            'optimizer': {'state': {0: {'step': torch.tensor(3.0)}}, 'param_groups': [{'lr': 0.1}]},
            'scheduler': None,
        },
        run / 'assets' / 'checkpoints' / 'latest.pt',
    )
    return run


def test_identical_runs_pass_exactly(tmp_path: Path):
    report = compare_runs(_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    assert report['passed'] is True
    assert set(report['items']) == {'metrics', 'history', 'checkpoint.latest.model', 'checkpoint.latest.optimizer'}
    assert report['items']['metrics']['keys'] == ['loss']


def test_differences_fail_and_tolerance_accepts_small_drift(tmp_path: Path):
    a = _run(tmp_path, 'a')
    b = _run(tmp_path, 'b', weight=1.0 + 1e-7, loss=0.5 + 1e-7, history=(1.0, 0.5 + 1e-7))
    exact = compare_runs(a, b)
    assert exact['passed'] is False
    assert exact['items']['checkpoint.latest.model']['problems'] == ['model.w: max abs diff 1.19209e-07']
    assert compare_runs(a, b, atol=1e-6)['passed'] is True


def test_shape_length_and_missing_checkpoint_fail(tmp_path: Path):
    a = _run(tmp_path, 'a')
    b = _run(tmp_path, 'b', history=(1.0,))
    report = compare_runs(a, b, checkpoints=['latest', 'best'])
    assert report['items']['history']['problems'] == ['history.train.Loss: length 2 != 1']
    assert report['items']['checkpoint.best.model']['missing'] == 'a'
    assert report['passed'] is False


def test_cli_compare_exit_codes_and_json(tmp_path: Path, capsys):
    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b', loss=0.7)
    out = tmp_path / 'cmp.json'
    assert cli.main(['compare', '--legacy', str(a), str(a)]) == 0
    assert cli.main(['compare', '--legacy', str(a), str(b), '--out', str(out)]) == 1
    body = json.loads(out.read_text(encoding='utf-8'))
    assert body['items']['metrics']['passed'] is False
    assert cli.main(['compare', '--legacy', str(a), str(tmp_path / 'missing')]) == 1
    observed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert observed[0]['passed'] is True
    assert observed[1]['items']['metrics']['passed'] is False
    assert all(row['verified'] is False for row in observed)


@pytest.mark.parametrize('kind', ['integer', 'integer-tensor', 'complex-tensor'])
def test_exact_checkpoint_comparison_preserves_integer_and_complex_differences(tmp_path, kind):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, delta in [(a, 0), (b, 1)]:
        path = run / 'assets/checkpoints/latest.pt'
        payload = torch.load(path, weights_only=False)
        if kind == 'integer':
            payload['scheduler'] = {'epoch': 2**53 + delta}
        elif kind == 'integer-tensor':
            payload['model']['counter'] = torch.tensor([2**53 + delta], dtype=torch.int64)
        else:
            payload['model']['complex'] = torch.tensor([1 + delta * 1j])
        torch.save(payload, path)
    assert compare_runs(a, b)['passed'] is False


@pytest.mark.parametrize('field,value', [('result-root', []), ('metrics', 7), ('metrics', False), ('metrics', [['loss', 0.5]]), ('tracker-root', []), ('splits', 7), ('split', []), ('meter', 7), ('history', 'bad')])
def test_compare_bad_containers_are_file_errors_without_evidence_overwrite(tmp_path, capsys, field, value):
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    path = a / ('result.json' if field in ('result-root', 'metrics') else 'assets/tracker/tracker_state.json')
    body = json.loads(path.read_text())
    if field.endswith('root'):
        body = value
    elif field in ('metrics', 'splits'):
        body[field] = value
    elif field == 'split':
        body['splits']['train'] = value
    elif field == 'meter':
        body['splits']['train']['Loss'] = value
    else:
        body['splits']['train']['Loss']['history'] = value
    path.write_text(json.dumps(body), encoding='utf-8')
    output = tmp_path / 'compare.json'
    output.write_bytes(b'prior comparison')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert cli.main(['compare', '--legacy', str(a), str(b), '--out', str(output)]) == 1
    assert capsys.readouterr().err
    assert {p: p.read_bytes() for p in before} == before


def test_compare_output_refusal_uses_cli_file_error_boundary(tmp_path, monkeypatch, capsys):
    from rpipe.structure import comparison as compare
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    output = tmp_path / 'comparison.json'
    output.write_bytes(b'previous comparison')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}

    def refused(*args, **kwargs):
        raise PermissionError('comparison output refused')
    monkeypatch.setattr(compare, 'atomic_write_text', refused)
    assert cli.main(['compare', '--legacy', str(a), str(b), '--out', str(output)]) == 1
    captured = capsys.readouterr()
    assert 'comparison output refused' in captured.err
    assert not captured.out
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('different', [False, True])
def test_compare_exact_huge_integers_without_float_coercion(tmp_path, different):
    a = _run(tmp_path, 'a', loss=10 ** 400)
    b = _run(tmp_path, 'b', loss=0 if different else 10 ** 400)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    output = tmp_path / 'comparison.json'
    assert cli.main(['compare', '--legacy', str(a), str(b), '--out', str(output)]) == (1 if different else 0)
    result = json.loads(output.read_text())
    assert result['items']['metrics']['max_abs_diff'] == (10 ** 400 if different else 0)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('flag', ['--atol', '--rtol'])
@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_compare_nonfinite_tolerances_are_argument_errors(tmp_path, flag, value, capsys):
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    output = tmp_path / 'comparison.json'
    output.write_bytes(b'old comparison')
    assert cli.main(['compare', '--legacy', str(a), str(b), f'{flag}={value}', '--out', str(output)]) == 1
    assert output.read_bytes() == b'old comparison'
    assert capsys.readouterr().err


@pytest.mark.parametrize('field', ['metrics', 'splits', 'split'])
def test_compare_keeps_legacy_null_optional_containers(tmp_path, field):
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    for run in (a, b):
        p = run / ('result.json' if field == 'metrics' else 'assets/tracker/tracker_state.json')
        body = json.loads(p.read_text())
        if field == 'split':
            body['splits']['train'] = None
        else:
            body[field] = None
        p.write_text(json.dumps(body), encoding='utf-8')
    assert cli.main(['compare', '--legacy', str(a), str(b)]) == 0


@pytest.mark.parametrize('delta,atol,rtol,passed', [(1, 1.0, 0.0, True), (2, 1.0, 0.0, False), (10 ** 390, 0.0, 1e-08, True), (10 ** 390, 0.0, 1e-12, False)], ids=['absolute-boundary', 'absolute-outside', 'relative-pass', 'relative-fail'])
def test_compare_huge_integer_tolerance_retains_exact_difference(tmp_path, delta, atol, rtol, passed):
    a = _run(tmp_path, 'a', loss=10 ** 400)
    b = _run(tmp_path, 'b', loss=10 ** 400 + delta)
    output = tmp_path / 'comparison.json'
    assert cli.main(['compare', '--legacy', str(a), str(b), f'--atol={atol}', f'--rtol={rtol}', '--out', str(output)]) == (0 if passed else 1)
    result = json.loads(output.read_text())
    assert result['items']['metrics']['max_abs_diff'] == delta


@pytest.mark.parametrize('kind', ['result', 'tracker', 'checkpoint'])
def test_compare_directory_is_bad_file_not_missing(tmp_path, kind, capsys):
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    path = a / {'result': 'result.json', 'tracker': 'assets/tracker/tracker_state.json', 'checkpoint': 'assets/checkpoints/latest.pt'}[kind]
    path.unlink()
    path.mkdir()
    output = tmp_path / 'comparison.json'
    output.write_bytes(b'old comparison')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert cli.main(['compare', '--legacy', str(a), str(b), '--out', str(output)]) == 1
    assert capsys.readouterr().err
    assert {p: p.read_bytes() for p in before} == before and path.is_dir()


@pytest.mark.parametrize('kind', ['empty', 'pickle', 'truncated-zip'])
def test_compare_corrupt_checkpoint_is_file_error_without_rewrite(tmp_path, kind, capsys):
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    path = a / 'assets/checkpoints/latest.pt'
    path.write_bytes(b'' if kind == 'empty' else b'not a checkpoint' if kind == 'pickle' else path.read_bytes()[:32])
    output = tmp_path / 'comparison.json'
    output.write_bytes(b'old comparison')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert cli.main(['compare', '--legacy', str(a), str(b), '--out', str(output)]) == 1
    assert capsys.readouterr().err
    assert {p: p.read_bytes() for p in before} == before


def test_compare_unexpected_program_error_is_not_swallowed(tmp_path, monkeypatch):
    from rpipe.structure import comparison as compare
    a, b = (_run(tmp_path, 'a'), _run(tmp_path, 'b'))
    error = RuntimeError('unexpected compare program error')

    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(compare, '_history', broken)
    with pytest.raises(RuntimeError) as caught:
        cli.main(['compare', '--legacy', str(a), str(b)])
    assert caught.value is error


@pytest.mark.parametrize('reference', ['parent', 'absolute', 'file-alias', 'root-alias'])
@pytest.mark.parametrize('entry', ['api', 'cli'])
def test_compare_checkpoint_ownership_rejects_external_payload(tmp_path, capsys, monkeypatch, reference, entry):
    import torch
    a, b = _run(tmp_path, 'a', weight=1.0), _run(tmp_path, 'b', weight=2.0)
    outside = tmp_path / 'outside'
    outside.mkdir()
    external = outside / 'shared.pt'
    torch.save({'model': {'w': torch.tensor([37])}}, external)
    name = {'parent': '../../../outside/shared', 'absolute': str(external.resolve())[:-3]}.get(reference, 'mirror')
    if reference == 'file-alias':
        for run in (a, b):
            (run / 'assets/checkpoints/mirror.pt').symlink_to(external)
    elif reference == 'root-alias':
        for run in (a, b):
            root = run / 'assets/checkpoints'
            root.rename(run / 'assets/original-checkpoints')
            root.symlink_to(outside, target_is_directory=True)
        name = 'shared'
    output = tmp_path / 'comparison.json'
    output.write_text('old comparison\n', encoding='utf-8')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file() and not p.is_symlink()}
    reads = []
    original_load = torch.load
    def load(path, *args, **kwargs):
        reads.append(path)
        return original_load(path, *args, **kwargs)
    monkeypatch.setattr(torch, 'load', load)
    if entry == 'api':
        with pytest.raises(ValueError, match='checkpoint'):
            compare_runs(a, b, checkpoints=[name])
    else:
        assert cli.main(['compare', '--legacy', str(a), str(b), '--checkpoint', name, '--out', str(output)]) == 1
        assert 'checkpoint' in capsys.readouterr().err
    assert reads == []
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('reference', ['nested', 'absolute-own', 'file-alias', 'root-alias', 'missing'])
def test_compare_checkpoint_ownership_keeps_local_references(tmp_path, reference):
    a = _run(tmp_path, 'a')
    root = a / 'assets/checkpoints'
    original = root / 'latest.pt'
    name = 'latest'
    if reference == 'nested':
        target = root / 'nested/copy.pt'
        target.parent.mkdir()
        target.write_bytes(original.read_bytes())
        name = 'nested/copy'
    elif reference == 'absolute-own':
        name = str(original.resolve())[:-3]
    elif reference == 'file-alias':
        (root / 'mirror.pt').symlink_to(original)
        name = 'mirror'
    elif reference == 'root-alias':
        target = a / 'assets/original-checkpoints'
        root.rename(target)
        root.symlink_to(target, target_is_directory=True)
    elif reference == 'missing':
        name = 'not-created'
    before = {p: p.read_bytes() for p in a.rglob('*') if p.is_file() and not p.is_symlink()}
    report = compare_runs(a, a, checkpoints=[name])
    assert report['passed'] is (reference != 'missing')
    if reference == 'missing':
        assert report['items']['checkpoint.not-created.model']['missing'] == 'a'
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('name', ['left\u2028right', 'left\x85right', 'ordinary name'])
def test_compare_checkpoint_display_keeps_one_line_per_item(tmp_path, name):
    from rpipe.structure.comparison import format_compare
    a = _run(tmp_path, 'a')
    root = a / 'assets/checkpoints'
    (root / f'{name}.pt').write_bytes((root / 'latest.pt').read_bytes())
    before = {p: p.read_bytes() for p in a.rglob('*') if p.is_file()}
    report = compare_runs(a, a, checkpoints=[name])
    assert report['passed'] is True
    view = format_compare(report)
    assert len(view.splitlines()) == len(report['items']) + 1
    assert f'checkpoint.{name}.model' in report['items']
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.skipif(__import__('os').name != 'nt', reason='native Windows extended path alias')
def test_compare_checkpoint_ownership_keeps_extended_own_path(tmp_path):
    a = _run(tmp_path, 'a')
    original = a / 'assets/checkpoints/latest.pt'
    native = Path('//?/' + original.resolve().as_posix())
    assert native.samefile(original)
    report = compare_runs(a, a, checkpoints=[str(native)[:-3]])
    assert report['passed'] is True


@pytest.mark.parametrize('kind', ['metrics', 'history', 'tensor', 'complex-tensor'])
@pytest.mark.parametrize('rtol', [0.0, 1e-5], ids=['exact', 'relative'])
@pytest.mark.parametrize('left,right,passed', [
    (1.0, float('inf'), False), (float('inf'), 1.0, False),
    (1.0, -float('inf'), False), (-float('inf'), 1.0, False),
    (float('inf'), -float('inf'), False), (-float('inf'), float('inf'), False),
    (float('inf'), float('inf'), True), (-float('inf'), -float('inf'), True),
    (float('nan'), float('nan'), True), (float('nan'), 1.0, False),
    (1.0, float('nan'), False), (1.0, 1.0, True),
], ids=['finite-posinf', 'posinf-finite', 'finite-neginf', 'neginf-finite',
        'posinf-neginf', 'neginf-posinf', 'same-posinf', 'same-neginf',
        'paired-nan', 'nan-finite', 'finite-nan', 'finite-control'])
def test_compare_nonfinite_evidence_keeps_value_identity(tmp_path, capsys, kind, rtol, left, right, passed):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, value in [(a, left), (b, right)]:
        if kind == 'metrics':
            path = run / 'result.json'
            body = json.loads(path.read_text())
            body['metrics']['loss'] = value
            path.write_text(json.dumps(body), encoding='utf-8')
        elif kind == 'history':
            path = run / 'assets/tracker/tracker_state.json'
            body = json.loads(path.read_text())
            body['splits']['train']['Loss']['history'] = [value]
            path.write_text(json.dumps(body), encoding='utf-8')
        else:
            path = run / 'assets/checkpoints/latest.pt'
            body = torch.load(path, weights_only=False)
            dtype = torch.complex128 if kind == 'complex-tensor' else torch.float64
            body['model']['w'] = torch.tensor([value, 2.0], dtype=dtype)
            torch.save(body, path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert compare_runs(a, b, rtol=rtol)['passed'] is passed
    output = tmp_path / 'compare.json'
    assert cli.main(['compare', '--legacy', str(a), str(b), f'--rtol={rtol}', '--out', str(output)]) == (0 if passed else 1)
    assert json.loads(output.read_text())['passed'] is passed
    observed = json.loads(capsys.readouterr().out)
    assert observed['passed'] is passed
    assert observed['legacy'] is True and observed['verified'] is False
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('dtype', ['float64', 'complex128'])
@pytest.mark.parametrize('lead', [float('inf'), -float('inf'), float('nan')], ids=['posinf', 'neginf', 'nan'])
@pytest.mark.parametrize('delta,passed', [(1e-7, True), (1.0, False)], ids=['finite-inside', 'finite-outside'])
def test_compare_nonfinite_matches_do_not_hide_finite_tensor_drift(tmp_path, dtype, lead, delta, passed):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, value in [(a, 1.0), (b, 1.0 + delta)]:
        path = run / 'assets/checkpoints/latest.pt'
        body = torch.load(path, weights_only=False)
        body['model']['w'] = torch.tensor([lead, value], dtype=getattr(torch, dtype))
        torch.save(body, path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert compare_runs(a, b, rtol=1e-5)['passed'] is passed
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('shape', [(), (1,), (1, 1)], ids=['scalar-tensor', 'vector', 'matrix'])
@pytest.mark.parametrize('base,delta,atol,rtol,passed', [
    (2**60, 1, 0.5, 0.0, False),
    (2**60, 1, 1.0, 0.0, True),
    (-(2**60), 2, 1.0, 0.0, False),
    (-(2**63), 2**64 - 1, 1.0, 0.0, False),
    (2**60, 2, 0.0, 2**-60, False),
    (2**60, 1, 0.0, 2**-60, True),
], ids=['absolute-outside', 'absolute-boundary', 'negative-outside', 'signed-range', 'relative-outside', 'relative-boundary'])
def test_integer_tensor_tolerance_matches_exact_scalar_contract(tmp_path, capsys, shape, base, delta, atol, rtol, passed):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, number in [(a, base + delta), (b, base)]:
        torch.save({'model': {'counter': torch.tensor(number, dtype=torch.int64).reshape(shape)}},
                   run / 'assets/checkpoints/latest.pt')
    before = {p: p.read_bytes() for run in (a, b) for p in run.rglob('*') if p.is_file()}
    report = compare_runs(a, b, atol=atol, rtol=rtol)
    assert report['passed'] is passed
    item = report['items']['checkpoint.latest.model']
    assert item['max_abs_diff'] == delta
    output = tmp_path / 'comparison.json'
    assert cli.main(['compare', '--legacy', str(a), str(b), f'--atol={atol}', f'--rtol={rtol}', '--out', str(output)]) == (0 if passed else 1)
    saved = json.loads(output.read_text(encoding='utf-8'))
    assert saved['passed'] is passed
    assert saved['items']['checkpoint.latest.model']['max_abs_diff'] == delta
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('dtype,left,right,atol,passed,difference', [
    ('bool', True, False, 1.0, True, 1),
    ('bool', True, False, 0.5, False, 1),
    ('int32', 11, 10, 1.0, True, 1),
    ('int32', 12, 10, 1.0, False, 2),
    ('int64', 2**60, 2**60, 0.5, True, 0),
    ('uint64', 2**64 - 1, 2**64 - 2, 0.5, False, 1),
    ('uint64', 2**64 - 1, 2**64 - 2, 1.0, True, 1),
], ids=['bool-within', 'bool-outside', 'small-boundary', 'small-outside', 'identical-large', 'unsigned-outside', 'unsigned-boundary'])
def test_integer_tensor_tolerance_keeps_other_numeric_contracts(tmp_path, dtype, left, right, atol, passed, difference):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, number in [(a, left), (b, right)]:
        torch.save({'model': {'counter': torch.tensor([number], dtype=getattr(torch, dtype))}},
                   run / 'assets/checkpoints/latest.pt')
    before = {p: p.read_bytes() for run in (a, b) for p in run.rglob('*') if p.is_file()}
    report = compare_runs(a, b, atol=atol)
    assert report['passed'] is passed
    assert report['items']['checkpoint.latest.model']['max_abs_diff'] == difference
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('kind', ['metrics', 'history', 'tensor', 'scalar-tensor', 'complex-tensor', 'scalar-complex-tensor'])
@pytest.mark.parametrize('atol,rtol,passed', [
    (0.0, 1.5, False), (0.0, 2.0, True),
    (1.7e308, 0.8, False), (1.7e308, 1.0, True),
], ids=['relative-outside', 'relative-boundary', 'sum-outside', 'sum-boundary'])
def test_finite_comparison_overflow_keeps_mathematical_tolerance(tmp_path, capsys, kind, atol, rtol, passed):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, value in [(a, -1.7e308), (b, 1.7e308)]:
        if kind == 'metrics':
            path = run / 'result.json'
            body = json.loads(path.read_text())
            body['metrics']['loss'] = value
            path.write_text(json.dumps(body), encoding='utf-8')
        elif kind == 'history':
            path = run / 'assets/tracker/tracker_state.json'
            body = json.loads(path.read_text())
            body['splits']['train']['Loss']['history'] = [value]
            path.write_text(json.dumps(body), encoding='utf-8')
        else:
            path = run / 'assets/checkpoints/latest.pt'
            body = torch.load(path, weights_only=False)
            dtype = torch.complex128 if 'complex' in kind else torch.float64
            body['model']['w'] = torch.tensor(value if kind.startswith('scalar') else [value, 2.0], dtype=dtype)
            torch.save(body, path)
    before = {p: p.read_bytes() for run in (a, b) for p in run.rglob('*') if p.is_file()}
    assert compare_runs(a, b, atol=atol, rtol=rtol)['passed'] is passed
    output = tmp_path / 'comparison.json'
    assert cli.main(['compare', '--legacy', str(a), str(b), f'--atol={atol}', f'--rtol={rtol}', '--out', str(output)]) == (0 if passed else 1)
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['passed'] is passed
    assert any(item['max_abs_diff'] is None for item in report['items'].values())
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('lead', [None, float('inf'), float('nan')], ids=['finite-only', 'paired-inf', 'paired-nan'])
@pytest.mark.parametrize('rtol,passed', [(1.5, False), (2.0, True)], ids=['outside', 'boundary'])
def test_complex_norm_overflow_preserves_finite_and_nonfinite_cells(tmp_path, lead, rtol, passed):
    import torch

    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, value in [(a, complex(-1.7e308, -1.7e308)), (b, complex(1.7e308, 1.7e308))]:
        values = [value, complex(2.0, 3.0)]
        if lead is not None:
            values.append(complex(lead, 0.0))
        torch.save({'model': {'w': torch.tensor([values], dtype=torch.complex128)}},
                   run / 'assets/checkpoints/latest.pt')
    before = {p: p.read_bytes() for run in (a, b) for p in run.rglob('*') if p.is_file()}
    assert compare_runs(a, b, rtol=rtol)['passed'] is passed
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('case', ['exact-drift', 'absolute-outside', 'absolute-boundary', 'identical'])
def test_finite_complex_norm_overflow_keeps_zero_relative_bound(tmp_path, case):
    import math
    import torch

    high = 1.7e308
    low = high if case == 'identical' else math.nextafter(high, 0.0)
    difference = high - low
    atol = {'exact-drift': 0.0, 'absolute-outside': difference / 2,
            'absolute-boundary': difference, 'identical': 0.0}[case]
    passed = case in ('absolute-boundary', 'identical')
    a, b = _run(tmp_path, 'a'), _run(tmp_path, 'b')
    for run, value in [(a, complex(high, high)), (b, complex(high, low))]:
        torch.save({'model': {'w': torch.tensor([value, 2.0], dtype=torch.complex128)}},
                   run / 'assets/checkpoints/latest.pt')
    before = {p: p.read_bytes() for run in (a, b) for p in run.rglob('*') if p.is_file()}
    report = compare_runs(a, b, atol=atol)
    assert report['passed'] is passed
    assert report['items']['checkpoint.latest.model']['max_abs_diff'] == difference
    output = tmp_path / 'comparison.json'
    assert cli.main(['compare', '--legacy', str(a), str(b), f'--atol={atol}', '--out', str(output)]) == (0 if passed else 1)
    assert json.loads(output.read_text())['passed'] is passed
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('kind,sign', [
    (kind, sign) for kind in ('metrics', 'history', 'checkpoint-scalar',
                             'checkpoint-real', 'checkpoint-complex') for sign in (1, -1)
] + [('checkpoint-real-u', 1), ('checkpoint-complex-u', 1)])
@pytest.mark.parametrize('reverse', [False, True], ids=['integer-left', 'integer-right'])
@pytest.mark.parametrize('atol,rtol,passed', [
    (0.0, 0.0, False), (0.5, 0.0, False), (1.0, 0.0, True),
    (0.0, 2.0**-61, False), (0.0, 2.0**-60, True),
], ids=['exact', 'absolute-outside', 'absolute-boundary', 'relative-outside', 'relative-boundary'])
def test_mixed_numeric_compare_preserves_integer_information(tmp_path, capsys, kind, sign, reverse, atol, rtol, passed):
    import torch
    from rpipe.flow import cli

    a, b, peer = (_run(tmp_path, name) for name in ('a', 'b', 'peer'))
    values = [sign * (2**60 + 1), sign * float(2**60)]
    if reverse:
        values.reverse()
    item_name = {'metrics': 'metrics', 'history': 'history'}.get(kind, 'checkpoint.latest.model')
    for run, value in zip((a, b), values):
        if kind == 'metrics':
            path = run / 'result.json'
            body = json.loads(path.read_text())
            body['metrics']['loss'] = value
            path.write_text(json.dumps(body), encoding='utf-8')
        elif kind == 'history':
            path = run / 'assets/tracker/tracker_state.json'
            body = json.loads(path.read_text())
            body['splits']['train']['Loss']['history'] = [value]
            path.write_text(json.dumps(body), encoding='utf-8')
        else:
            path = run / 'assets/checkpoints/latest.pt'
            body = torch.load(path, weights_only=False)
            if kind != 'checkpoint-scalar':
                dtype = (torch.uint64 if kind.endswith('-u') else torch.int64) if isinstance(value, int) else (
                    torch.complex128 if 'complex' in kind else torch.float64)
                value = torch.tensor([[value, value], [value, value]], dtype=dtype).t()
            body['model']['mixed'] = value
            torch.save(body, path)
    before = {p: p.read_bytes() for run in (a, b, peer) for p in run.rglob('*') if p.is_file()}
    report = compare_runs(a, b, atol=atol, rtol=rtol)
    output = tmp_path / 'comparison.json'
    code = cli.main(['compare', '--legacy', str(a), str(b), '--atol', str(atol), '--rtol', str(rtol), '--out', str(output)])
    saved = json.loads(output.read_text(encoding='utf-8'))
    captured = capsys.readouterr()
    assert not captured.err
    assert {p: p.read_bytes() for p in before} == before
    assert report['passed'] is passed
    assert report['items'][item_name]['passed'] is passed
    assert code == (0 if passed else 1)
    assert saved['passed'] is passed
    assert saved['items'][item_name]['passed'] is passed
    if not passed:
        assert report['items'][item_name]['max_abs_diff'] == 1
        assert saved['items'][item_name]['max_abs_diff'] == 1
        assert report['items'][item_name]['n_problems'] > 0


@pytest.mark.parametrize('kind', ['metrics', 'history', 'checkpoint-scalar'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('case', ['huge-finite', 'finite-inf', 'finite-nan'])
def test_mixed_scalar_numeric_extremes_are_comparisons_not_file_errors(tmp_path, capsys, kind, reverse, case):
    import math
    import torch

    a, b = (_run(tmp_path, name) for name in ('a', 'b'))
    values = [10**400 if case == 'huge-finite' else 2**60 + 1,
              {'huge-finite': 1.0, 'finite-inf': math.inf, 'finite-nan': math.nan}[case]]
    if reverse:
        values.reverse()
    for run, value in zip((a, b), values):
        if kind == 'checkpoint-scalar':
            path = run / 'assets/checkpoints/latest.pt'
            body = torch.load(path, weights_only=False)
            body['model']['mixed'] = value
            torch.save(body, path)
        else:
            path = run / ('result.json' if kind == 'metrics' else 'assets/tracker/tracker_state.json')
            body = json.loads(path.read_text())
            if kind == 'metrics':
                body['metrics']['loss'] = value
            else:
                body['splits']['train']['Loss']['history'] = [value]
            path.write_text(json.dumps(body), encoding='utf-8')
    before = {p: p.read_bytes() for run in (a, b) for p in run.rglob('*') if p.is_file()}
    try:
        report = compare_runs(a, b)
    except ValueError:
        report = None
    output = tmp_path / 'comparison.json'
    code = cli.main(['compare', '--legacy', str(a), str(b), '--out', str(output)])
    captured = capsys.readouterr()
    assert {p: p.read_bytes() for p in before} == before
    assert report is not None, 'finite mixed numbers must not become a file error'
    assert report['passed'] is False
    assert code == 1 and not captured.err
    saved = json.loads(output.read_text(encoding='utf-8'))
    item = {'metrics': 'metrics', 'history': 'history'}.get(kind, 'checkpoint.latest.model')
    assert report['items'][item]['max_abs_diff'] == math.inf
    assert saved['items'][item]['max_abs_diff'] is None


@pytest.mark.parametrize('label,expected', [
    pytest.param('left' + chr(0xD800) + 'right', r'left\ud800right', id='high'),
    pytest.param('left' + chr(0xDC00) + 'right', r'left\udc00right', id='low'),
    pytest.param('left' + chr(0xD800) + 'mid' + chr(0xDC00) + 'right', r'left\ud800mid\udc00right', id='mixed'),
    pytest.param('left-ASCII-right', 'left-ASCII-right', id='ascii'),
    pytest.param('left研究😀right', 'left研究😀right', id='unicode'),
    pytest.param(r'left\ud800right', r'left\ud800right', id='literal-backslash'),
])
def test_compare_display_labels_are_utf8_and_keep_peer(label, expected):
    from rpipe.structure.comparison import format_compare
    body = {'passed': False, 'items': {
        label: {'passed': False, 'missing': 'a', 'max_abs_diff': None},
        'peer': {'passed': True, 'max_abs_diff': 0.0},
    }}
    view = format_compare(body)
    assert view.encode('utf-8').decode('utf-8') == view
    assert expected in view
    assert 'pass  peer  max abs diff 0' in view
    assert view.endswith('failed\n')


@pytest.mark.parametrize('label', [
    pytest.param('left' + chr(0xD800) + 'right', id='high'),
    pytest.param('left' + chr(0xDC00) + 'right', id='low'),
    pytest.param('left' + chr(0xD800) + 'mid' + chr(0xDC00) + 'right', id='mixed'),
    pytest.param('left-ASCII-right', id='ascii'),
    pytest.param('left研究😀right', id='unicode'),
    pytest.param(r'left\ud800right', id='literal-backslash'),
])
def test_derived_compare_json_preserves_surrogate_labels_and_peer(tmp_path, label):
    from rpipe.structure.comparison import compare_runs, write_compare
    inputs = {}
    for name in ['a', 'b']:
        run = tmp_path / name
        run.mkdir()
        path = run / 'result.json'
        path.write_text(json.dumps({'status': 'succeeded', 'control': {}, 'paths': {},
                                    'metrics': {label: 0.5, 'peer': 1.0}}), encoding='utf-8')
        inputs[path] = path.read_bytes()
    report = compare_runs(tmp_path / 'a', tmp_path / 'b')
    target = tmp_path / 'comparison.json'
    target.write_bytes(b'{"passed": true, "items": {}}\r\n')
    assert write_compare(target, report) == target
    decoded = json.loads(target.read_text(encoding='utf-8'))
    assert decoded == report
    assert decoded['items']['metrics']['passed'] is True
    assert label in decoded['items']['metrics']['keys']
    assert 'peer' in decoded['items']['metrics']['keys']
    assert {p: p.read_bytes() for p in inputs} == inputs
    assert not list(tmp_path.glob('*.tmp'))
