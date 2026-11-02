"""Precision coordination consumes diagnosis JSON, without collecting worker text."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('worker_result', ['success', 'failure', 'missing'])
def test_precision_parent_discards_raw_output_and_preserves_diagnosis(tmp_path, worker_result):
    source = Path(__file__).resolve().parents[5] / 'studies/main_exp/flow/execute/precision.py'
    spec = importlib.util.spec_from_file_location('_precision_output_test', source)
    precision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(precision)
    script = tmp_path / 'cpu_worker.py'
    script.write_text('''import json
from pathlib import Path
import sys
output = Path(sys.argv[sys.argv.index('--output') + 1])
print('token=PRIVATE_PRECISION_STDOUT' * 256, flush=True)
print('token=PRIVATE_PRECISION_STDERR' * 256, file=sys.stderr, flush=True)
mode = WORKER_RESULT
if mode == 'success':
    body = {'samples': 10000, 'correct_samples': 7777, 'checkpoint_correct_samples': 7777,
            'prediction_sha256': 'synthetic_predictions', 'input_sha256': 'synthetic_input',
            'model_sha256': 'synthetic_model'}
elif mode == 'failure':
    body = {'error': {'type': 'SyntheticWorkerFailure'}}
if mode != 'missing':
    (output / 'diagnosis.json').write_text(json.dumps(body), encoding='utf-8')
raise SystemExit(0 if mode == 'success' else 7)
'''.replace('WORKER_RESULT', repr(worker_result)), encoding='utf-8')
    checkpoint = tmp_path / 'checkpoint.bin'
    checkpoint.write_bytes(b'synthetic checkpoint fixture')
    recipe = SimpleNamespace(__file__=str(script), external_root=lambda value: Path(value).resolve(),
                             bootstrap=lambda: None, source_manifest=lambda: {'fixture': 'unchanged'})
    root = tmp_path / 'runs'
    assert precision.main(recipe, ['--run-root', str(root), '--checkpoint', str(checkpoint)]) == (0 if worker_result == 'success' else 1)
    report_path, = root.glob('validation/*/REPORT.json')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['complete'] is True and report['source_unchanged'] is True
    assert report['checkpoint_unchanged'] is True and checkpoint.read_bytes() == b'synthetic checkpoint fixture'
    assert len(report['groups']) == 4
    rows = [row for group in report['groups'] for row in group['runs']]
    assert len(rows) == 32
    if worker_result == 'success':
        assert report['status'] == 'passed' and report['fp32_passed'] is True
        assert all(row['exit_code'] == 0 and row['correct_samples'] == 7777 for row in rows)
        assert len(list(root.rglob('diagnosis.json'))) == 32
    else:
        assert report['status'] == 'failed' and report['fp32_passed'] is False
        assert all(row['exit_code'] == 7 for row in rows)
        expected = {'type': 'SyntheticWorkerFailure'} if worker_result == 'failure' else 'missing diagnosis'
        assert all(row['error'] == expected for row in rows)
    assert not list(root.rglob('worker.log'))
    assert all(b'PRIVATE_PRECISION_STDOUT' not in path.read_bytes() and
               b'PRIVATE_PRECISION_STDERR' not in path.read_bytes()
               for path in root.rglob('*') if path.is_file())
