"""Collect numerical comparisons from the completed paired execution."""
import copy
import json
from pathlib import Path
from .comparison import compare_observations
from ...structure.algorithm.paired import source_manifest, MainArchive


def run(ctx):
    import torch

    report = copy.deepcopy(ctx.state['probe_execution'])
    workspace = ctx.state['algorithm'].workspace
    expected = (ctx.control.data['name'], ctx.control.model['name'])
    rows = []
    for record in report['runs']:
        raw = torch.load(Path(record['observations']), map_location='cpu', weights_only=True)
        row = compare_observations(raw, record['data'], record['model'])
        row['observations'] = record['observations']
        rows.append(row)
    report['runs'] = rows
    manifest = json.loads((workspace / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    report['source_unchanged'] = manifest['files'] == source_manifest()
    report['archive_after'] = MainArchive(workspace).verify_exports()
    provenance_ok = report['source_unchanged'] and report['archive_after']['unchanged']
    for row in rows:
        row['source_unchanged'] = report['source_unchanged']
        row['archive_verification'] = report['archive_after']
        row['passed'] &= provenance_ok
    report['selected_complete'] = len(rows) == 1 and (rows[0]['data'], rows[0]['model']) == expected
    report['selected_passed'] = report['selected_complete'] and all(row['passed'] for row in rows)
    ctx.state['probe'] = report
    ctx.state['collected']['quality'] = report['selected_passed']
