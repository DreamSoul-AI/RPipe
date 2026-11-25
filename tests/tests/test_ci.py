"""CI triggers and receipts preserve the mandatory-check and source contracts."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import zipfile

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]
TESTS = Path(__file__).parents[1]


@pytest.fixture
def ci(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS))
    spec = importlib.util.spec_from_file_location('rpipe_ci_test', TESTS / 'ci.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'git', lambda *args: 'tree' if args == ('rev-parse', 'HEAD^{tree}')
                        else 'candidate' if args == ('rev-parse', 'HEAD') else 'README.md\0')
    return module


def pr_event():
    return {'pull_request': {'base': {'sha': 'base', 'ref': 'dev'},
                             'head': {'sha': 'head'}, 'number': 7}}


@pytest.mark.parametrize('path,scope,cells,package', [
    ('README.md', 'docs', 4, False),
    ('src/rpipe/structure/model/factory.py', 'affected', 2, True),
    ('src/rpipe/flow/worker.py', 'full', 4, True),
    ('studies/_template/study.yaml', 'full', 4, True),
    ('pyproject.toml', 'full', 4, True),
    ('.github/workflows/unit-tests.yml', 'full', 4, True),
    ('tests/conftest.py', 'full', 4, True),
])
def test_pr_impact_selects_cpu_and_package_independently(ci, monkeypatch, path, scope, cells, package):
    native = ci.git
    monkeypatch.setattr(ci, 'git', lambda *args: path + '\0' if args[0] == 'diff' else native(*args))
    plan = ci.configure(ci.build_plan('pull_request', pr_event(), 'refs/pull/7/merge'), 'unit', 'owner/repo')
    assert plan['scope'] == scope and len(plan['matrix']['include']) == cells
    assert plan['cpu'] == (scope != 'docs') and plan['package'] == package


@pytest.mark.parametrize('event,ref', [('push', 'refs/heads/main'), ('push', 'refs/heads/dev'),
                                     ('workflow_dispatch', 'refs/heads/dev')])
def test_release_manual_and_unproven_push_use_full_matrix(ci, monkeypatch, event, ref):
    monkeypatch.setattr(ci, 'get_json', lambda path: [])
    plan = ci.configure(ci.build_plan(event, {'before': 'base'}, ref), 'unit', 'owner/repo')
    assert plan['scope'] == 'full' and plan['cpu'] and plan['package']
    assert len(plan['matrix']['include']) == 4


def test_missing_base_expands_selection(ci, monkeypatch):
    native = ci.git
    def git(*args):
        if args[0] == 'diff':
            raise subprocess.CalledProcessError(128, 'git diff')
        return native(*args)
    monkeypatch.setattr(ci, 'git', git)
    plan = ci.build_plan('pull_request', pr_event(), 'refs/pull/7/merge')
    assert plan['scope'] == 'full' and plan['package_required']


def reuse_fixture(ci, monkeypatch):
    plan = ci.build_plan('push', {'before': 'base'}, 'refs/heads/dev')
    pr = {'number': 7, 'merged_at': '2026-11-26T00:00:00Z', 'merge_commit_sha': 'candidate',
          'head': {'sha': 'head'}, 'base': {'ref': 'dev', 'repo': {'full_name': 'owner/repo'}}}
    run = {'id': 100, 'run_attempt': 2, 'status': 'completed', 'conclusion': 'success',
           'event': 'pull_request', 'head_sha': 'head', 'repository': {'full_name': 'owner/repo'}}
    proof = {'schema': 1, 'repository': 'owner/repo', 'kind': 'unit', 'event': 'pull_request',
             'base_sha': 'base', 'tree': 'tree', 'fingerprint': plan['fingerprint'],
             'head_sha': 'head', 'pr': 7, 'run_id': 100, 'run_attempt': 2,
             'source_sha': 'tested-merge-candidate', 'scope': 'docs'}
    monkeypatch.setattr(ci, 'get_json', lambda path: [pr] if '/pulls?' in path else {'workflow_runs': [run]})
    monkeypatch.setattr(ci, 'read_receipt', lambda *args: proof)
    return plan, pr, run, proof


def test_same_tree_success_receipt_reuses_without_second_cpu_or_package(ci, monkeypatch):
    plan, _, _, _ = reuse_fixture(ci, monkeypatch)
    result = ci.configure(plan, 'unit', 'owner/repo')
    assert result['reused']['run_id'] == 100 and result['scope'] == 'docs'
    assert not result['cpu'] and not result['package']


@pytest.mark.parametrize('field,value', [('tree', 'other'), ('base_sha', 'new-base'),
                                       ('head_sha', 'new-head'), ('run_attempt', 1),
                                       ('fingerprint', 'changed-policy'), ('kind', 'package'),
                                       ('scope', 'affected'), ('pr', 8)])
def test_mismatched_receipt_requires_second_full_run(ci, monkeypatch, field, value):
    plan, _, _, proof = reuse_fixture(ci, monkeypatch)
    proof[field] = value
    result = ci.configure(plan, 'unit', 'owner/repo')
    assert result['reused'] is None and result['cpu'] and result['package']
    assert result['scope'] == 'full'


@pytest.mark.parametrize('state', ['failure', 'cancelled', 'in_progress'])
def test_latest_unsuccessful_run_cannot_be_replaced_by_old_success(ci, monkeypatch, state):
    plan, pr, run, _ = reuse_fixture(ci, monkeypatch)
    old = dict(run)
    run['conclusion'] = state
    if state == 'in_progress':
        run['status'] = state
    monkeypatch.setattr(ci, 'get_json', lambda path: [pr] if '/pulls?' in path else {'workflow_runs': [run, old]})
    assert ci.configure(plan, 'unit', 'owner/repo')['cpu']


def test_expired_or_absent_receipt_and_api_failure_fall_back(ci, monkeypatch):
    plan, _, _, _ = reuse_fixture(ci, monkeypatch)
    monkeypatch.setattr(ci, 'read_receipt', lambda *args: None)
    assert ci.configure(dict(plan), 'unit', 'owner/repo')['cpu']
    monkeypatch.setattr(ci, 'get_json', lambda *args: (_ for _ in ()).throw(OSError('offline')))
    result = ci.configure(dict(plan), 'unit', 'owner/repo')
    assert result['cpu'] and result['reuse_unavailable'] == 'OSError'


@pytest.mark.parametrize('kind', ['unit', 'package'])
@pytest.mark.parametrize('scope,job', [('docs', 'skipped'), ('full', 'success')])
def test_mandatory_gate_accepts_only_planned_success(ci, kind, scope, job):
    plan = ci.build_plan('pull_request', pr_event(), 'refs/pull/7/merge')
    plan.update(cpu=scope == 'full', package=scope == 'full', reused=None)
    context = {'repository': 'owner/repo', 'run_id': 100, 'run_attempt': 1}
    assert ci.gate(plan, kind, 'success', job, context)['tree'] == 'tree'
    for invalid in ('failure', 'cancelled', 'success' if job == 'skipped' else 'skipped'):
        with pytest.raises(ValueError):
            ci.gate(plan, kind, 'success', invalid, context)
    with pytest.raises(ValueError):
        ci.gate(plan, kind, 'failure', job, context)
    plan['tree'] = 'different'
    with pytest.raises(ValueError):
        ci.gate(plan, kind, 'success', job, context)


@pytest.mark.parametrize('filename,gate,worker', [('unit-tests.yml', 'unit-gate', 'unit'),
                                               ('package-check.yml', 'package-gate', 'build-package')])
def test_workflow_always_finishes_required_gate_for_docs_and_reuse(filename, gate, worker):
    # BaseLoader keeps the GitHub 'on' key as a string under YAML 1.1 readers.
    data = yaml.load((TESTS.parent / '.github/workflows' / filename).read_text(), Loader=yaml.BaseLoader)
    assert set(data['on']) == {'pull_request', 'push', 'workflow_dispatch'}
    assert data['on']['push']['branches'] == ['dev', 'main']
    assert data['jobs'][gate]['if'] == 'always()'
    assert data['jobs'][gate]['name'] == ('Unit tests' if filename == 'unit-tests.yml' else 'Build package')
    assert data['jobs'][gate]['needs'] == ['plan', worker]
    assert data['jobs'][worker]['needs'] == 'plan'
    assert 'paths' not in data['on']['pull_request'] and 'paths-ignore' not in data['on']['pull_request']
    assert data['jobs']['plan']['steps'][0]['with']['fetch-depth'] == '0'
    assert data['permissions'] == {'contents': 'read'}
    assert data['jobs']['plan']['permissions'] == {
        'contents': 'read', 'actions': 'read', 'pull-requests': 'read'}
    if filename == 'package-check.yml':
        text = (TESTS.parent / '.github/workflows' / filename).read_text()
        assert text.count('pip==26.2.2') == 2
        assert text.count('tests/package_smoke.py --artifact') == 2


@pytest.mark.parametrize('changed', ['README.md', 'src/rpipe/flow/worker.py',
                                    'src/rpipe/structure/model/factory.py'])
@pytest.mark.parametrize('kind', ['unit', 'package'])
def test_real_merge_reuses_only_matching_tested_candidate(ci, monkeypatch, tmp_path, changed, kind):
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c', f'safe.directory={repo}', '-c', 'user.name=ci-test',
                                        '-c', 'user.email=ci@example.invalid', '-c', 'commit.gpgsign=false',
                                        '-C', str(repo), *args]).decode().strip()
    git('init', '-q')
    (repo / 'README.md').write_text('base\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    target = repo / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('updated\n')
    git('add', '.')
    git('commit', '-qm', 'head')
    head = git('rev-parse', 'HEAD')
    tree = git('rev-parse', 'HEAD^{tree}')
    tested = git('commit-tree', tree, '-p', base, '-p', head, '-m', 'test merge')
    merged = git('commit-tree', tree, '-p', base, '-p', head, '-m', 'actual merge')
    assert tested != merged
    assert git('rev-parse', tested + '^{tree}') == git('rev-parse', merged + '^{tree}')
    monkeypatch.setattr(ci, 'git', git)
    git('checkout', '--detach', '-q', tested)
    event = {'pull_request': {'number': 7, 'base': {'sha': base, 'ref': 'dev'}, 'head': {'sha': head}}}
    prior = ci.configure(ci.build_plan('pull_request', event, 'refs/pull/7/merge'), kind, 'owner/repo')
    required = prior['cpu'] if kind == 'unit' else prior['package']
    proof = ci.gate(prior, kind, 'success', 'success' if required else 'skipped',
                    {'repository': 'owner/repo', 'head_sha': head, 'pr': 7, 'run_id': 100, 'run_attempt': 1})
    pr = dict(number=7, merged_at='2026-11-26', merge_commit_sha=merged, head={'sha': head},
              base={'ref': 'dev', 'repo': {'full_name': 'owner/repo'}})
    run = dict(id=100, run_attempt=1, status='completed', conclusion='success', event='pull_request',
               head_sha=head, repository={'full_name': 'owner/repo'})
    monkeypatch.setattr(ci, 'get_json', lambda path: [pr] if '/pulls?' in path else {'workflow_runs': [run]})
    monkeypatch.setattr(ci, 'read_receipt', lambda *args: proof)
    git('checkout', '--detach', '-q', merged)
    push = ci.configure(ci.build_plan('push', {'before': base}, 'refs/heads/dev'), kind, 'owner/repo')
    assert not push['cpu'] and not push['package']
    receipt = ci.gate(push, kind, 'success', 'skipped', {'repository': 'owner/repo', 'run_id': 101, 'run_attempt': 1})
    assert receipt['source_sha'] == merged and receipt['reused']['source_sha'] == tested
    # A changed integration baseline must validate again, even with the same final tree.
    changed_base = ci.configure(ci.build_plan('push', {'before': head}, 'refs/heads/dev'), kind, 'owner/repo')
    assert changed_base['cpu'] and changed_base['package'] and changed_base['reused'] is None


def test_artifact_redirect_keeps_token_on_github_api_only(ci, monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('receipt.json', json.dumps({'schema': 1}))
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.setenv('GITHUB_API_URL', 'https://api.github.com')
    monkeypatch.setattr(ci, 'get_json', lambda path: {'artifacts': [
        {'id': 20, 'name': 'rpipe-unit-proof-100-2', 'expired': False, 'size_in_bytes': 500}]})
    calls = []
    class API:
        def open(self, request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 302, 'redirect',
                                         {'Location': 'https://storage.example/receipt.zip'}, None)
    monkeypatch.setattr(ci.urllib.request, 'build_opener', lambda *args: API())
    def download(location, timeout):
        assert isinstance(location, str) and location.startswith('https://storage.example/')
        return io.BytesIO(payload.getvalue())
    monkeypatch.setattr(ci.urllib.request, 'urlopen', download)
    assert ci.read_receipt('owner/repo', {'id': 100, 'run_attempt': 2}, 'unit') == {'schema': 1}
    assert calls[0].get_header('Authorization') == 'Bearer test-token'


def test_document_plan_and_required_gate_run_without_site_packages(tmp_path):
    repo = tmp_path / 'repo'
    tests = repo / 'tests'
    tests.mkdir(parents=True)
    for name in ('ci.py', 'impact.py'):
        (tests / name).write_bytes((TESTS / name).read_bytes())
    def git(*args):
        return subprocess.check_output(['git', '-c', f'safe.directory={repo}', '-c', 'user.name=ci-test',
                                       '-c', 'user.email=ci@example.invalid', '-c', 'commit.gpgsign=false',
                                       '-C', str(repo), *args]).decode().strip()
    git('init', '-q')
    (repo / 'README.md').write_text('# Base\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    (repo / 'README.md').write_text('# Updated\n')
    git('commit', '-qam', 'documentation')
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {'number': 7, 'base': {'sha': base, 'ref': 'dev'},
                                                'head': {'sha': git('rev-parse', 'HEAD')}}}))
    output = tmp_path / 'outputs.txt'
    env = dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_EVENT_NAME='pull_request',
               GITHUB_REF='refs/pull/7/merge', GITHUB_REPOSITORY='owner/repo',
               GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(tmp_path / 'steps.md'),
               GITHUB_RUN_ID='100', GITHUB_RUN_ATTEMPT='1', RUNNER_TEMP=str(tmp_path))
    started = time.perf_counter()
    plan_result = subprocess.run([sys.executable, '-S', str(tests / 'ci.py'), 'plan', '--kind', 'unit'],
                                 cwd=repo, env=env, capture_output=True, text=True, timeout=15)
    assert plan_result.returncode == 0, plan_result.stderr
    plan = json.loads((tmp_path / 'rpipe-ci/plan.json').read_text())
    assert not plan['cpu'] and not plan['package']
    env.update(PLAN_JSON=json.dumps(plan), PLAN_RESULT='success', JOB_RESULT='skipped')
    result = subprocess.run([sys.executable, '-S', str(tests / 'ci.py'), 'gate', '--kind', 'unit'],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    proof = json.loads((tmp_path / 'rpipe-ci/receipt.json').read_text())
    assert proof['tree'] == git('rev-parse', 'HEAD^{tree}')
    print(f'document plan and gate seconds: {time.perf_counter() - started:.3f}')
