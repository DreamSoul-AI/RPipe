"""Exact comparison kernels and formal saved-Run comparison boundaries."""
import json
import math
import pytest

from rpipe.structure.artifact.comparison import compare,compare_state,format_compare,write_compare
from rpipe.structure.artifact import io,readout
from rpipe.structure.control import AdmissionError,SourceMismatch

pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,pytest.mark.structure_layer,
            pytest.mark.module_artifact,pytest.mark.cost(cost_class='c1'),
            pytest.mark.result_type('categorical',detail='summary')]

def test_saved_comparison_reports_declared_condition_changes_without_claiming_equivalence(saved_run):
    left = saved_run('a', seed=0)
    right = saved_run('b', seed=1, source='another-source', version='v2')
    result = compare(left, right)
    assert result['passed'] is True and result['conditions_equal'] is False
    assert {'seed', 'source_digest', 'version'} <= result['condition_differences'].keys()
    with pytest.raises(AdmissionError, match='undeclared'):
        compare(left, right, differences=())
    assert compare(left, right, differences=('seed', 'source_digest', 'version'))['passed'] is True


def test_saved_comparison_checks_union_of_metric_fields(saved_run):
    left = saved_run('a', metrics={'loss': .5, 'required': 3})
    right = saved_run('b', metrics={'loss': .5})
    result = compare(left, right)
    assert result['passed'] is False
    assert result['items']['metrics']['keys'] == ['loss', 'required']
    assert result['items']['metrics']['n_problems'] == 1


def test_comparison_requires_explicit_reference_and_complete_saved_records(saved_run):
    ref = saved_run()
    with pytest.raises(AdmissionError, match='RunRef'):
        compare(ref.run_dir, ref.run_dir)
    (ref.run_dir / 'execution.json').unlink()
    assert compare(ref, ref)['passed'] is False


def test_explicit_snapshot_comparison_requires_missing_parts_instead_of_skipping_them(saved_run):
    import torch
    ref = saved_run(checkpoint={'model': {'weight': torch.tensor([1.0])}})
    result = compare(ref, ref, checkpoints=['latest'])
    assert result['items']['checkpoint.latest.model']['passed'] is True
    assert result['items']['checkpoint.latest.optimizer']['missing'] == 'a'
    assert result['passed'] is False


def test_explicit_snapshot_comparison_uses_the_complete_current_envelope(saved_run):
    import torch
    state = {'model': {'w': torch.tensor([1.0])}, 'optimizer': {}, 'scheduler': None}
    a, b = saved_run('a', checkpoint=state), saved_run('b', checkpoint=state)
    assert compare(a, b, checkpoints=['latest'])['passed'] is True


@pytest.mark.parametrize('name', ['../peer', '/absolute/path', 'nested/child', 'C:\\peer'])
def test_checkpoint_names_cannot_select_another_location(saved_run, name):
    ref = saved_run()
    with pytest.raises(AdmissionError):
        compare(ref, ref, checkpoints=[name])


def test_checkpoint_symlink_cannot_read_a_peer_run(saved_run, tmp_path):
    import torch
    a = saved_run('a')
    b = saved_run('b', checkpoint={'model': {'w': torch.tensor([1])}})
    root = a.assets_dir / io.CHECKPOINTS
    root.mkdir()
    (root / 'alias.pt').symlink_to(b.assets_dir / io.CHECKPOINTS / 'latest.pt')
    with pytest.raises(AdmissionError, match='escapes'):
        compare(a, a, checkpoints=['alias'])


@pytest.mark.parametrize('a,b', [(True, 1), (True, False), ({'flag': True}, {'flag': 1})])
def test_boolean_states_are_never_relaxed_by_numeric_tolerance(a, b):
    assert compare_state(a, b, atol=100, rtol=100)['passed'] is False


@pytest.mark.parametrize('other_dtype', ['bool', 'int64', 'float64'])
def test_boolean_tensor_state_is_type_and_value_strict(other_dtype):
    import torch
    left = torch.tensor([True, False])
    right = torch.tensor([True, True] if other_dtype == 'bool' else [1, 0],
                         dtype=getattr(torch, other_dtype))
    assert compare_state(left, right, atol=100, rtol=100)['passed'] is False
    assert compare_state(left, left.clone(), atol=100)['passed'] is True


@pytest.mark.parametrize('atol,rtol', [(True, 0), (-1, 0), (0, math.inf), (0, math.nan)])
def test_invalid_tolerances_are_rejected_at_the_shared_state_entry(atol, rtol):
    with pytest.raises(AdmissionError):
        compare_state(1, 1, atol=atol, rtol=rtol)


def test_corrupt_optional_history_does_not_create_a_successful_comparison(saved_run):
    ref = saved_run()
    (ref.assets_dir / io.TRACKER_JSONL).write_bytes(b'not-json\n')
    result = compare(ref, ref)
    assert result['passed'] is False and result['items']['history']['missing'] == 'a'


def test_report_output_preserves_unicode_labels_and_inputs(saved_run, tmp_path):
    label = 'left' + chr(0xD800) + '研究'
    a, b = saved_run('a'), saved_run('b')
    for ref in (a, b):
        result = io.read_json(ref.result_path)
        result['metrics'] = {label: .5, 'peer': 1.0}
        ref.result_path.write_text(json.dumps(result), encoding='utf-8')
    report = compare(a, b)
    before = {ref.result_path: ref.result_path.read_bytes() for ref in (a, b)}
    target = tmp_path / 'compare.json'
    write_compare(target, report)
    decoded = json.loads(target.read_text(encoding='utf-8'))
    assert decoded == report and label in decoded['items']['metrics']['keys']
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('destination', ['result', 'index', 'peer', 'new', 'symlink'])
def test_report_writer_protects_the_entire_declared_input_studies(saved_run, tmp_path, destination):
    left, right = saved_run('a'), saved_run('b')
    peer = saved_run('a', seed=1)
    report = compare(left, right)
    index = left.study_root / 'index.json'
    before = {path: path.read_bytes() for path in (left.result_path, right.result_path,
                                                   peer.result_path, index)}
    targets = {'result': left.result_path, 'index': index, 'peer': peer.result_path,
               'new': right.study_root / 'new' / 'comparison.json'}
    if destination == 'symlink':
        alias = tmp_path / 'study-alias'
        alias.symlink_to(left.study_root, target_is_directory=True)
        targets[destination] = alias / 'new' / 'comparison.json'
    with pytest.raises(AdmissionError, match='outside input Study'):
        write_compare(targets[destination], report)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (left.study_root / 'new').exists()
    assert not (right.study_root / 'new').exists()


@pytest.mark.parametrize('alias', ['direct', 'symlink', 'hardlink'])
def test_observation_report_cannot_replace_its_source_or_same_file_alias(tmp_path, alias):
    source = tmp_path / 'observed.pt'
    source.write_bytes(b'owned original observation')
    target = source
    if alias != 'direct':
        target = tmp_path / f'{alias}.pt'
        if alias == 'symlink':
            target.symlink_to(source)
        else:
            target.hardlink_to(source)
    report = {'source': {'path': str(source)}, 'passed': True,
              'items': {'state': compare_state([1], [1])}}
    with pytest.raises(AdmissionError, match='observation source'):
        write_compare(target, report)
    assert source.read_bytes() == target.read_bytes() == b'owned original observation'
    output = tmp_path / 'RUN_COMPARISON.json'
    write_compare(output, report)
    assert json.loads(output.read_text(encoding='utf-8')) == report
    assert source.read_bytes() == b'owned original observation'

@pytest.mark.parametrize('kind', ['metrics', 'history', 'tensor', 'complex-tensor'])
@pytest.mark.parametrize('rtol', [0.0, 1e-05], ids=['exact', 'relative'])
@pytest.mark.parametrize('left,right,passed', [(1.0, float('inf'), False), (float('inf'), 1.0, False), (1.0, -float('inf'), False), (-float('inf'), 1.0, False), (float('inf'), -float('inf'), False), (-float('inf'), float('inf'), False), (float('inf'), float('inf'), True), (-float('inf'), -float('inf'), True), (float('nan'), float('nan'), True), (float('nan'), 1.0, False), (1.0, float('nan'), False), (1.0, 1.0, True)], ids=['finite-posinf', 'posinf-finite', 'finite-neginf', 'neginf-finite', 'posinf-neginf', 'neginf-posinf', 'same-posinf', 'same-neginf', 'paired-nan', 'nan-finite', 'finite-nan', 'finite-control'])
def test_compare_nonfinite_evidence_keeps_value_identity(tmp_path, capsys, kind, rtol, left, right, passed):
    import torch
    if kind == 'metrics':
        a, b = ({'value': left}, {'value': right})
    elif kind == 'history':
        a, b = ({'values': [left]}, {'values': [right]})
    else:
        dtype = torch.complex128 if kind == 'complex-tensor' else torch.float64
        a, b = (torch.tensor([left, 2.0], dtype=dtype), torch.tensor([right, 2.0], dtype=dtype))
    assert compare_state(a, b, rtol=rtol)['passed'] is passed

@pytest.mark.parametrize('dtype', ['float64', 'complex128'])
@pytest.mark.parametrize('lead', [float('inf'), -float('inf'), float('nan')], ids=['posinf', 'neginf', 'nan'])
@pytest.mark.parametrize('delta,passed', [(1e-07, True), (1.0, False)], ids=['finite-inside', 'finite-outside'])
def test_compare_nonfinite_matches_do_not_hide_finite_tensor_drift(tmp_path, dtype, lead, delta, passed):
    import torch
    a = torch.tensor([lead, 1.0], dtype=getattr(torch, dtype))
    b = torch.tensor([lead, 1.0 + delta], dtype=getattr(torch, dtype))
    assert compare_state(a, b, rtol=1e-05)['passed'] is passed

@pytest.mark.parametrize('shape', [(), (1,), (1, 1)], ids=['scalar-tensor', 'vector', 'matrix'])
@pytest.mark.parametrize('base,delta,atol,rtol,passed', [(2 ** 60, 1, 0.5, 0.0, False), (2 ** 60, 1, 1.0, 0.0, True), (-2 ** 60, 2, 1.0, 0.0, False), (-2 ** 63, 2 ** 64 - 1, 1.0, 0.0, False), (2 ** 60, 2, 0.0, 2 ** (-60), False), (2 ** 60, 1, 0.0, 2 ** (-60), True)], ids=['absolute-outside', 'absolute-boundary', 'negative-outside', 'signed-range', 'relative-outside', 'relative-boundary'])
def test_integer_tensor_tolerance_matches_exact_scalar_contract(tmp_path, capsys, shape, base, delta, atol, rtol, passed):
    import torch
    a = torch.tensor(base + delta, dtype=torch.int64).reshape(shape)
    b = torch.tensor(base, dtype=torch.int64).reshape(shape)
    result = compare_state(a, b, atol=atol, rtol=rtol)
    assert result['passed'] is passed
    assert result['max_abs_diff'] == delta

@pytest.mark.parametrize('dtype,left,right,atol,passed,difference', [('bool', True, False, 1.0, False, math.inf), ('bool', True, False, 0.5, False, math.inf), ('int32', 11, 10, 1.0, True, 1), ('int32', 12, 10, 1.0, False, 2), ('int64', 2 ** 60, 2 ** 60, 0.5, True, 0), ('uint64', 2 ** 64 - 1, 2 ** 64 - 2, 0.5, False, 1), ('uint64', 2 ** 64 - 1, 2 ** 64 - 2, 1.0, True, 1)], ids=['bool-within', 'bool-outside', 'small-boundary', 'small-outside', 'identical-large', 'unsigned-outside', 'unsigned-boundary'])
def test_integer_tensor_tolerance_keeps_other_numeric_contracts(tmp_path, dtype, left, right, atol, passed, difference):
    import torch
    a = torch.tensor([left], dtype=getattr(torch, dtype))
    b = torch.tensor([right], dtype=getattr(torch, dtype))
    result = compare_state(a, b, atol=atol)
    assert result['passed'] is passed
    assert result['max_abs_diff'] == difference

@pytest.mark.parametrize('kind', ['metrics', 'history', 'tensor', 'scalar-tensor', 'complex-tensor', 'scalar-complex-tensor'])
@pytest.mark.parametrize('atol,rtol,passed', [(0.0, 1.5, False), (0.0, 2.0, True), (1.7e+308, 0.8, False), (1.7e+308, 1.0, True)], ids=['relative-outside', 'relative-boundary', 'sum-outside', 'sum-boundary'])
def test_finite_comparison_overflow_keeps_mathematical_tolerance(tmp_path, capsys, kind, atol, rtol, passed):
    import torch
    values = (-1.7e+308, 1.7e+308)
    if 'tensor' in kind:
        dtype = torch.complex128 if 'complex' in kind else torch.float64
        a, b = (torch.tensor(value if kind.startswith('scalar') else [value, 2.0], dtype=dtype) for value in values)
    elif kind == 'history':
        a, b = ([value] for value in values)
    else:
        a, b = ({'value': value} for value in values)
    assert compare_state(a, b, atol=atol, rtol=rtol)['passed'] is passed

@pytest.mark.parametrize('lead', [None, float('inf'), float('nan')], ids=['finite-only', 'paired-inf', 'paired-nan'])
@pytest.mark.parametrize('rtol,passed', [(1.5, False), (2.0, True)], ids=['outside', 'boundary'])
def test_complex_norm_overflow_preserves_finite_and_nonfinite_cells(tmp_path, lead, rtol, passed):
    import torch
    values = [complex(-1.7e+308, -1.7e+308), complex(1.7e+308, 1.7e+308)]
    a, b = (torch.tensor([[value, complex(2, 3)] + ([] if lead is None else [complex(lead, 0)])], dtype=torch.complex128) for value in values)
    assert compare_state(a, b, rtol=rtol)['passed'] is passed

@pytest.mark.parametrize('case', ['exact-drift', 'absolute-outside', 'absolute-boundary', 'identical'])
def test_finite_complex_norm_overflow_keeps_zero_relative_bound(tmp_path, case):
    import math
    import torch
    high = 1.7e+308
    low = high if case == 'identical' else math.nextafter(high, 0.0)
    difference = high - low
    atol = {'exact-drift': 0.0, 'absolute-outside': difference / 2, 'absolute-boundary': difference, 'identical': 0.0}[case]
    a, b = (torch.tensor([value, 2.0], dtype=torch.complex128) for value in [complex(high, high), complex(high, low)])
    result = compare_state(a, b, atol=atol)
    assert result['passed'] is (case in ('absolute-boundary', 'identical'))
    assert result['max_abs_diff'] == difference

@pytest.mark.parametrize('kind,sign', [(kind, sign) for kind in ('metrics', 'history', 'checkpoint-scalar', 'checkpoint-real', 'checkpoint-complex') for sign in (1, -1)] + [('checkpoint-real-u', 1), ('checkpoint-complex-u', 1)])
@pytest.mark.parametrize('reverse', [False, True], ids=['integer-left', 'integer-right'])
@pytest.mark.parametrize('atol,rtol,passed', [(0.0, 0.0, False), (0.5, 0.0, False), (1.0, 0.0, True), (0.0, 2.0 ** (-61), False), (0.0, 2.0 ** (-60), True)], ids=['exact', 'absolute-outside', 'absolute-boundary', 'relative-outside', 'relative-boundary'])
def test_mixed_numeric_compare_preserves_integer_information(tmp_path, capsys, kind, sign, reverse, atol, rtol, passed):
    import torch
    values = [sign * (2 ** 60 + 1), sign * float(2 ** 60)]
    if reverse:
        values.reverse()
    if kind.startswith('checkpoint-') and kind != 'checkpoint-scalar':
        values = [torch.tensor([[value, value], [value, value]], dtype=(torch.uint64 if kind.endswith('-u') else torch.int64) if isinstance(value, int) else torch.complex128 if 'complex' in kind else torch.float64).t() for value in values]
    elif kind == 'metrics':
        values = [{'value': value} for value in values]
    elif kind == 'history':
        values = [[value] for value in values]
    result = compare_state(*values, atol=atol, rtol=rtol)
    assert result['passed'] is passed
    if not passed:
        assert result['max_abs_diff'] == 1 and result['n_problems'] > 0

@pytest.mark.parametrize('kind', ['metrics', 'history', 'checkpoint-scalar'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('case', ['huge-finite', 'finite-inf', 'finite-nan'])
def test_mixed_scalar_numeric_extremes_are_comparisons_not_file_errors(tmp_path, capsys, kind, reverse, case):
    import math
    values = [10 ** 400 if case == 'huge-finite' else 2 ** 60 + 1, {'huge-finite': 1.0, 'finite-inf': math.inf, 'finite-nan': math.nan}[case]]
    if reverse:
        values.reverse()
    if kind == 'metrics':
        values = [{'value': value} for value in values]
    elif kind == 'history':
        values = [[value] for value in values]
    result = compare_state(*values)
    assert result['passed'] is False and result['max_abs_diff'] == math.inf
    report = {'passed': False, 'items': {'state': result}}
    output = tmp_path / 'comparison.json'
    write_compare(output, report)
    assert json.loads(output.read_text(encoding='utf-8'))['items']['state']['max_abs_diff'] is None

@pytest.mark.parametrize('label,expected', [pytest.param('left' + chr(55296) + 'right', 'left\\ud800right', id='high'), pytest.param('left' + chr(56320) + 'right', 'left\\udc00right', id='low'), pytest.param('left' + chr(55296) + 'mid' + chr(56320) + 'right', 'left\\ud800mid\\udc00right', id='mixed'), pytest.param('left-ASCII-right', 'left-ASCII-right', id='ascii'), pytest.param('left研究😀right', 'left研究😀right', id='unicode'), pytest.param('left\\ud800right', 'left\\ud800right', id='literal-backslash')])
def test_compare_display_labels_are_utf8_and_keep_peer(label, expected):
    from rpipe.structure.artifact.comparison import format_compare
    body = {'passed': False, 'items': {label: {'passed': False, 'missing': 'a', 'max_abs_diff': None}, 'peer': {'passed': True, 'max_abs_diff': 0.0}}}
    view = format_compare(body)
    assert view.encode('utf-8').decode('utf-8') == view
    assert expected in view
    assert 'pass  peer  max abs diff 0' in view
    assert view.endswith('failed\n')
