"""CI planning, success receipts and conservative dev integration reuse."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

from impact import REPO, check_documents, selection

WORKFLOWS = {'unit': 'unit-tests.yml', 'package': 'package-check.yml'}


def git(*args: str) -> str:
    return subprocess.check_output(['git', '-c', f'safe.directory={REPO}', '-C', str(REPO), *args]).decode().strip()


def fingerprint(plan: dict) -> str:
    fields = {key: plan[key] for key in ('scope', 'changed', 'targets', 'package_required')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def build_plan(event_name: str, event: dict, ref: str) -> dict:
    base = event.get('pull_request', {}).get('base', {}).get('sha') if event_name == 'pull_request' else event.get('before')
    force_full = event_name != 'pull_request' or event.get('pull_request', {}).get('base', {}).get('ref') == 'main'
    reason = ''
    if base and set(base) != {'0'}:
        try:
            paths = git('diff', '--name-only', '--no-renames', '-z', base, 'HEAD', '--').split('\0')
            plan = selection([path for path in paths if path])
        except subprocess.CalledProcessError:
            plan, reason = selection([]), 'base unavailable'
            force_full = True
    else:
        plan, reason = selection([]), 'new branch or manual full regression'
        force_full = True
    # Before changing the policy to full on push, retain the original PR scope.
    plan.update(base_sha=base, source_sha=git('rev-parse', 'HEAD'),
                tree=git('rev-parse', 'HEAD^{tree}'), ref=ref, event=event_name,
                reason=reason or plan['reason'])
    plan['fingerprint'] = fingerprint(plan)
    plan['impact_scope'] = plan['scope']
    plan['impact_targets'] = list(plan['targets'])
    plan['impact_package_required'] = plan['package_required']
    if force_full:
        plan.update(scope='full', targets=[], package_required=True)
    return plan


def get_json(path: str) -> dict | list:
    url = os.environ.get('GITHUB_API_URL', 'https://api.github.com') + '/' + path
    request = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json', 'User-Agent': 'RPipe-CI'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def read_receipt(repository: str, run: dict, kind: str) -> dict | None:
    artifacts = get_json(f'repos/{repository}/actions/runs/{run["id"]}/artifacts?per_page=100')
    name = f'rpipe-{kind}-proof-{run["id"]}-{run["run_attempt"]}'
    matches = [a for a in artifacts['artifacts'] if a['name'] == name and not a['expired']]
    if len(matches) != 1:
        return None
    artifact = matches[0]
    if artifact['size_in_bytes'] > 65536:
        return None
    request = urllib.request.Request(
        os.environ.get('GITHUB_API_URL', 'https://api.github.com') +
        f'/repos/{repository}/actions/artifacts/{artifact["id"]}/zip',
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'User-Agent': 'RPipe-CI'})
    # Resolve the authenticated API redirect first; do not forward the token to blob storage.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            payload = response.read(65537)
    except urllib.error.HTTPError as error:
        if error.code != 302:
            raise
        location = error.headers['Location']
        if not location.startswith('https://'):
            raise ValueError('artifact location must use HTTPS')
        with urllib.request.urlopen(location, timeout=15) as response:
            payload = response.read(65537)
    if len(payload) > 65536:
        return None
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != 'receipt.json' or entries[0].file_size > 16384:
            return None
        return json.loads(archive.read('receipt.json'))


def matching_receipt(proof: dict, plan: dict, pr: dict, run: dict, kind: str, repository: str) -> bool:
    expected = {'schema': 1, 'repository': repository, 'kind': kind,
                'event': 'pull_request', 'base_sha': plan['base_sha'],
                'tree': plan['tree'], 'fingerprint': plan['fingerprint'],
                'head_sha': pr['head']['sha'], 'pr': pr['number'],
                'run_id': run['id'], 'run_attempt': run['run_attempt'],
                'scope': plan['impact_scope']}
    return all(proof.get(key) == value for key, value in expected.items())


def reuse(plan: dict, kind: str, repository: str) -> dict | None:
    if plan['event'] != 'push' or plan['ref'] != 'refs/heads/dev' or not plan['base_sha']:
        return None
    pulls = get_json(f'repos/{repository}/commits/{plan["source_sha"]}/pulls?per_page=100')
    for pr in pulls:
        if (not pr.get('merged_at') or pr['merge_commit_sha'] != plan['source_sha']
                or pr['base']['ref'] != 'dev' or pr['base']['repo']['full_name'] != repository):
            continue
        runs = get_json(f'repos/{repository}/actions/workflows/{WORKFLOWS[kind]}/runs'
                        f'?event=pull_request&head_sha={pr["head"]["sha"]}&per_page=20')
        for run in runs['workflow_runs']:
            if (run['event'] != 'pull_request' or run['head_sha'] != pr['head']['sha']
                    or run['repository']['full_name'] != repository):
                continue
            if run['status'] != 'completed' or run['conclusion'] != 'success':
                return None
            proof = read_receipt(repository, run, kind)
            if proof and matching_receipt(proof, plan, pr, run, kind, repository):
                return {'pr': pr['number'], 'run_id': run['id'], 'run_attempt': run['run_attempt'],
                        'source_sha': proof['source_sha'], 'tree': proof['tree']}
            # A missing or stale latest receipt cannot be replaced by an older run.
            return None
    return None


def configure(plan: dict, kind: str, repository: str) -> dict:
    plan['reused'] = None
    try:
        plan['reused'] = reuse(plan, kind, repository)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        plan['reuse_unavailable'] = type(error).__name__
    if plan['reused']:
        plan.update(scope=plan['impact_scope'], targets=plan['impact_targets'],
                    package_required=plan['impact_package_required'])
    plan['cpu'] = not plan['reused'] and plan['scope'] in ('full', 'affected')
    plan['package'] = not plan['reused'] and plan['package_required']
    # All source changes affect the delivered package, even when CPU tests are targeted.
    if any(path.startswith('src/') for path in plan['changed']) and not plan['reused']:
        plan['package'] = True
    cells = [{'os': operating_system, 'python-version': version}
             for operating_system in ('ubuntu-latest', 'windows-latest')
             for version in ('3.10', '3.13')]
    if plan['scope'] == 'affected':
        cells = [cells[0], cells[-1]]
    plan['matrix'] = {'include': cells}
    return plan


def gate(plan: dict, kind: str, plan_result: str, job_result: str, context: dict) -> dict:
    required = plan['cpu'] if kind == 'unit' else plan['package']
    if plan_result != 'success' or job_result != ('success' if required else 'skipped'):
        raise ValueError('required CI job did not complete successfully')
    if git('rev-parse', 'HEAD^{tree}') != plan['tree'] or git('rev-parse', 'HEAD') != plan['source_sha']:
        raise ValueError('gate checkout differs from the selected candidate')
    return {'schema': 1, 'repository': context['repository'], 'kind': kind,
            'source_sha': plan['source_sha'], 'tree': plan['tree'], 'base_sha': plan['base_sha'],
            'fingerprint': plan['fingerprint'], 'event': plan['event'],
            'head_sha': context.get('head_sha'), 'pr': context.get('pr'),
            'run_id': context['run_id'], 'run_attempt': context['run_attempt'],
            'scope': plan['scope'], 'reused': plan['reused']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'gate', 'run'))
    parser.add_argument('--kind', choices=('unit', 'package'), required=True)
    args = parser.parse_args()
    output = Path(os.environ['RUNNER_TEMP']) / 'rpipe-ci'
    output.mkdir(exist_ok=True)
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    repository = os.environ['GITHUB_REPOSITORY']
    if args.action == 'plan':
        plan = configure(build_plan(os.environ['GITHUB_EVENT_NAME'], event, os.environ['GITHUB_REF']),
                         args.kind, repository)
        errors = check_documents(plan['documents'])
        plan['document_errors'] = errors
        (output / 'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
        print(json.dumps(plan, indent=2))
        if errors:
            raise ValueError('\n'.join(errors))
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
            for key in ('cpu', 'package', 'matrix'):
                handle.write(key + '=' + json.dumps(plan[key]) + '\n')
            handle.write('plan=' + json.dumps(plan) + '\n')
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write('```json\n' + json.dumps(plan, indent=2) + '\n```\n')
    else:
        plan = json.loads(os.environ['PLAN_JSON'])
        if args.action == 'run':
            command = [sys.executable, str(REPO / 'tests/run.py'), '--offline', '--timeout-seconds', '1200',
                       '--', '--basetemp', str(Path(os.environ['RUNNER_TEMP']) / 'pytest-offline'), *plan['targets']]
            return subprocess.call(command, cwd=REPO)
        pr = event.get('pull_request', {})
        proof = gate(plan, args.kind, os.environ['PLAN_RESULT'], os.environ['JOB_RESULT'],
                     {'repository': repository, 'head_sha': pr.get('head', {}).get('sha'),
                      'pr': pr.get('number'), 'run_id': int(os.environ['GITHUB_RUN_ID']),
                      'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT'])})
        (output / 'receipt.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        print(json.dumps(proof, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
