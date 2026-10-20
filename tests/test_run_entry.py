"""The public test launcher preserves child diagnostics, arguments and exit status."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.mark.parametrize('exit_code', [0, 3])
def test_captured_launcher_keeps_child_environment_output_and_exit(tmp_path, exit_code):
    """A lightweight pytest stand-in exercises two real Python child processes."""
    module = tmp_path / 'pytest'
    module.mkdir()
    (module / '__init__.py').write_text('', encoding='utf-8')
    (module / '__main__.py').write_text(
        "import json,os,sys\n"
        "print('launcher-child='+json.dumps({'token':os.environ.get('RPIPE_ENTRY_PROBE'), 'args':sys.argv[1:]}),flush=True)\n"
        "print('launcher-child-stderr',file=sys.stderr,flush=True)\n"
        f"raise SystemExit({exit_code})\n", encoding='utf-8',
    )
    env = dict(os.environ)
    env['PYTHONPATH'] = str(tmp_path)
    env['RPIPE_ENTRY_PROBE'] = 'known-test-value'
    launcher = Path(__file__).resolve().parent / 'run.py'
    result = subprocess.run([sys.executable, str(launcher), '--core', '--', '--tb=no'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code
    line = next(line for line in result.stdout.splitlines() if line.startswith('launcher-child='))
    observed = json.loads(line.removeprefix('launcher-child='))
    assert observed['token'] == 'known-test-value'
    assert observed['args'] == ['-m', '(unit and not slow and not external and not gpu)',
                                '--cost-class', 'c1', '--cost-class', 'c2', '--tb=no']
    assert 'launcher-child-stderr' in result.stderr
