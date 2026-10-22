"""Optional real MNIST journey; pre-staged data only, never downloads."""
import json
import os
from pathlib import Path
import shutil
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.content, pytest.mark.p2, pytest.mark.slow,
              pytest.mark.external, pytest.mark.cost(cost_class='c3'),
              pytest.mark.result_type('categorical', detail='summary'), pytest.mark.gpu]

def test_real_mnist_public_cli_preserves_sources_and_completes_expected_runs(tmp_path):
    prepared = os.environ.get('RPIPE_TEST_MNIST_ROOT')
    if not prepared or not Path(prepared).is_dir():
        pytest.skip('requires explicitly pre-staged read-only RPIPE_TEST_MNIST_ROOT; no downloads')
    import yaml
    from rpipe.api import plan, read_run
    from rpipe.flow.cli import main
    import torch
    if not torch.cuda.is_available():
        pytest.skip('requires explicitly selected CUDA resource')
    repo = Path(__file__).resolve().parents[3]
    study = tmp_path / 'source' / 'mnist_train_size'
    shutil.copytree(repo / 'tests' / '_data' / 'studies' / 'mnist_train_size', study)
    declaration = yaml.safe_load((study / 'study.yaml').read_text())
    declaration['seeds'] = [0]
    declaration['axes']['data.config.train_size'] = [500]
    declaration['fixed']['algorithm']['num_epochs'] = 2
    (study / 'study.yaml').write_text(yaml.safe_dump(declaration), encoding='utf-8')
    config = yaml.safe_load((study / 'experiment_config.yaml').read_text())
    config['data']['path'] = str(Path(prepared).resolve())
    config['data'].setdefault('config', {})['download'] = False
    (study / 'experiment_config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    before = {p.relative_to(study): p.read_bytes() for p in study.rglob('*') if p.is_file()}
    root = tmp_path / 'artifacts'
    specs = plan(study, run_root=root)
    assert len(specs) == 2
    assert [s.config['algorithm']['mode'] for s in specs] == ['train', 'eval']
    for command in ('make', 'launch', 'process'):
        assert main([command, str(study), '--run-root', str(root)]) == 0
    results = [read_run(spec) for spec in specs]
    assert all(row['status'] == 'succeeded' for row in results)
    assert results[1]['execute']['step'] is not None
    assert all('accuracy' in row['metrics'] for row in results)
    derived = json.loads((specs[0].study_root / 'summary/process.json').read_text())
    assert derived['complete'] and len(derived['experiments']) == 2
    assert {p.relative_to(study): p.read_bytes() for p in study.rglob('*') if p.is_file()} == before
