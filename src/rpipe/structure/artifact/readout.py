"""Read explicit saved Run references and current records without executing or repairing."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any

from ..control import AdmissionError, RunRef, SourceMismatch, RunConfig, compute_run_id
from . import io


def _reference(ref: RunRef) -> RunRef:
    if not isinstance(ref, RunRef):
        raise AdmissionError('readout requires an explicit saved RunRef')
    io.contained(ref.study_root, ref.run_root)
    io.contained(ref.run_dir, ref.study_root)
    for path in (ref.result_path, ref.assets_dir,
                 ref.run_dir / 'config.yaml', ref.run_dir / 'execution.json',
                 ref.run_dir / '.execution', ref.run_dir / 'attempts'):
        io.contained(path, ref.run_dir)
    return ref


def refs_from_index(study_root: Path | str, index: dict | None = None) -> tuple[RunRef, ...]:
    """Validate the saved schema-3 plan; current source files are irrelevant to reading."""
    root = Path(study_root).resolve()
    output = root.parent.parent
    body = io.load_index(root) if index is None else index
    if (not isinstance(body, dict) or type(body.get('schema')) is not int or body['schema'] != 3
            or not isinstance(body.get('experiments'), list)):
        raise AdmissionError('a formal schema-3 Study index is required')
    try:
        json.dumps(body, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise AdmissionError('Study index must contain finite JSON values') from error
    provenance = body.get('provenance')
    if not isinstance(provenance, dict) or not isinstance(provenance.get('files'), dict):
        raise AdmissionError('Study index requires its saved source manifest')
    files = provenance['files']
    if not files or any(not isinstance(name, str) or not isinstance(digest, str)
           or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)
           for name, digest in files.items()):
        raise AdmissionError('invalid saved source manifest')
    digest = hashlib.sha256(json.dumps(sorted(files.items()), separators=(',', ':')).encode()).hexdigest()
    if provenance.get('digest') != digest or provenance.get('version') != root.name:
        raise SourceMismatch('Study index source manifest or version is inconsistent')
    refs, groups, ids, seeds = [], set(), set(), set()
    for group in body['experiments']:
        if (not isinstance(group, dict) or not isinstance(group.get('id'), str) or not group['id']
                or not isinstance(group.get('factors'), dict)
                or not isinstance(group.get('runs'), list) or not group['runs']):
            raise AdmissionError('index requires explicit Experiment groups and Run rows')
        if group['id'] in groups:
            raise AdmissionError('duplicate Experiment identity in index')
        groups.add(group['id'])
        for row in group['runs']:
            if not isinstance(row, dict) or not {'id','run_dir','seed','train_id','identity'}.issubset(row):
                raise AdmissionError('index Run row is missing its explicit identity or dependency')
            observed = io.validate_identity(row['identity'])
            if row['run_dir'] != row['id'] or row['id'] != observed['id']:
                raise SourceMismatch('index Run location does not match its identity')
            if observed['source_digest'] != digest or observed['version'] != root.name:
                raise SourceMismatch('index Run belongs to a different source or version')
            ref = RunRef(row['id'], row['seed'], group['id'], root, output, digest,
                         observed['config_digest'], root.name, row['train_id'])
            _reference(ref)
            if (ref.run_dir / 'config.yaml').exists():
                config = read_config(ref)
                for name, expected in group['factors'].items():
                    if not isinstance(name, str) or not all(name.split('.')):
                        raise AdmissionError('Experiment factors require explicit field paths')
                    value = config
                    for part in name.split('.'):
                        value = value.get(part) if isinstance(value, dict) else None
                    if json.dumps(value, sort_keys=True) != json.dumps(expected, sort_keys=True):
                        raise SourceMismatch('Experiment factors differ from the saved Run configuration')
            if ref.id in ids or (ref.experiment,ref.seed) in seeds:
                raise AdmissionError('duplicate Run identity or seed in index')
            ids.add(ref.id); seeds.add((ref.experiment,ref.seed)); refs.append(ref)
    by_id = {ref.id:ref for ref in refs}
    visiting, done = set(), set()
    def visit(ref):
        if ref.id in visiting:
            raise AdmissionError('cyclic training dependency in index')
        if ref.id in done:
            return
        visiting.add(ref.id)
        if ref.train_id is not None:
            if ref.train_id not in by_id:
                raise AdmissionError('index training dependency is missing')
            visit(by_id[ref.train_id])
        visiting.remove(ref.id); done.add(ref.id)
    for ref in refs:
        visit(ref)
    return tuple(refs)


def ref_from_run_dir(path: Path | str) -> RunRef:
    run = Path(path).resolve()
    if run.parent.name != 'runs':
        raise AdmissionError('Run directory must have a formal Study/runs/id location')
    refs = refs_from_index(run.parent.parent)
    selected = [ref for ref in refs if ref.run_dir == run]
    if len(selected) != 1:
        raise AdmissionError('Run directory is not listed in its formal index')
    return selected[0]


def read_config(ref: RunRef) -> dict:
    _reference(ref)
    config = io.load_config(io.contained(ref.run_dir / 'config.yaml', ref.run_root))
    encoded = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if hashlib.sha256(encoded.encode()).hexdigest() != ref.config_digest:
        raise SourceMismatch('saved configuration does not match the Run reference')
    validated = RunConfig.from_mapping(config)
    if validated.id != ref.id or type(config.get('seed')) is not int or config['seed'] != ref.seed:
        raise SourceMismatch('saved Run configuration identity or seed differs')
    conditions = dict(config)
    for key in ('id','seed','description','tags'):
        conditions.pop(key,None)
    group = compute_run_id({'conditions':conditions,'source_digest':ref.source_digest,'version':ref.version})
    experiment = str(conditions.get('experiment',ref.study_root.parent.name))+':'+group
    if experiment != ref.experiment:
        raise SourceMismatch('saved Run belongs to a different Experiment group')
    return config


def read_run(ref: RunRef) -> dict[str, Any]:
    _reference(ref)
    result_path = io.contained(ref.result_path, ref.run_root)
    execution_path = io.contained(ref.run_dir / 'execution.json', ref.run_root)
    config_path = io.contained(ref.run_dir / 'config.yaml', ref.run_root)
    busy = io.contained(ref.run_dir / '.execution', ref.run_root).exists()
    result = {}
    if config_path.exists():
        read_config(ref)
    elif result_path.exists() or execution_path.exists() or busy:
        return {'id':ref.id,'identity':ref.identity,'status':'unknown','quality':None,
                'scientific_status':'missing','evidence_complete':False,'busy':busy,
                'metadata_status':'missing_config'}
    if result_path.exists():
        try:
            result = io.read_json(result_path)
            io.validate_run_record(result,expected_identity=ref.identity)
        except SourceMismatch:
            raise
        except (OSError,ValueError,TypeError,RecursionError) as error:
            return {'id':ref.id,'identity':ref.identity,'status':'unknown','quality':None,
                    'scientific_status':'invalid','evidence_complete':False,'busy':busy,
                    'metadata_status':'invalid','record_error':io.safe_error_record(error)}
    if execution_path.exists():
        try:
            record, complete = io.read_execution(ref)
        except SourceMismatch:
            raise
        except (OSError,ValueError,TypeError,RecursionError) as error:
            return {**result,'id':ref.id,'status':'unknown','quality':None,
                    'evidence_complete':False,'busy':busy,'metadata_status':'invalid',
                    'execution_error':io.safe_error_record(error)}
        return io.project_execution(record,result,busy=busy,complete=complete)
    attempts = io.contained(ref.run_dir / 'attempts',ref.run_root)
    incomplete = bool(result or busy or attempts.is_dir() and any(attempts.iterdir()))
    return {**result,'id':ref.id,'identity':ref.identity,
            'status':'unknown' if incomplete else 'missing','quality':None,
            'scientific_status':result.get('status','missing'),'derived_status':'not_run',
            'evidence_complete':False,'busy':busy,
            **({'metadata_status':'missing_execution'} if incomplete else {})}


def aggregate(refs) -> dict[str, Any]:
    rows, missing, groups, identities, seeds = [], [], {}, set(), set()
    for ref in refs:
        _reference(ref)
        key = (ref.source_digest,ref.version,ref.id)
        if key in identities or (ref.experiment,ref.seed) in seeds:
            raise AdmissionError('duplicate Run or seed in aggregation request')
        identities.add(key); seeds.add((ref.experiment,ref.seed))
        try:
            row = read_run(ref)
        except (OSError,ValueError) as error:
            row = {'id':ref.id,'status':'invalid','evidence_complete':False,
                   'error':io.safe_error_record(error)}
        metrics = row.get('metrics',{})
        if not isinstance(metrics,dict) or any(isinstance(value,bool) or isinstance(value,(int,float))
                                              and not _finite(value) for value in metrics.values()):
            row = {**row,'metadata_status':'invalid','evidence_complete':False,'metrics':{}}
        rows.append(row); groups.setdefault(ref.experiment,[]).append(row)
        if row.get('status') != 'succeeded' or not row.get('evidence_complete'):
            missing.append(ref.id)
    summaries = {}
    for group, members in groups.items():
        eligible = [row.get('metrics',{}) for row in members if row.get('status') == 'succeeded']
        names = set().union(*(row.keys() for row in eligible)) if eligible else set()
        metrics = {}
        for name in sorted(names):
            values = [row[name] for row in eligible if _finite(row.get(name))]
            if values:
                metrics[name] = {**summarize_numbers(values),'complete':len(values)==len(members)}
        summaries[group] = {'metrics':metrics,'complete':all(row.get('status')=='succeeded'
                                                           and row.get('evidence_complete') for row in members)}
    return {'runs':rows,'experiments':summaries,'missing_runs':missing,'complete':bool(rows) and not missing}


def read_study(study_root: Path | str) -> dict[str, Any]:
    return aggregate(refs_from_index(study_root))


def logs(ref: RunRef) -> str:
    read_config(ref)
    folder = io.contained(ref.assets_dir / 'logs',ref.run_dir)
    paths = sorted(folder.glob('events*.jsonl')) if folder.is_dir() else []
    return '\n'.join(io.contained(path,folder).read_text(encoding='utf-8',errors='replace')
                     for path in paths if path.is_file())


def report(refs) -> str:
    body = aggregate(refs)
    lines = ['| Run | Execution | Scientific | Derived | Quality | Evidence complete |',
             '| --- | --- | --- | --- | --- | --- |']
    lines += [f"| {row['id']} | {row['status']} | {row.get('scientific_status','missing')} | "
              f"{row.get('derived_status','not_run')} | {row.get('quality')} | {row.get('evidence_complete',False)} |"
              for row in body['runs']]
    return '\n'.join(lines)+'\n'


def _finite(value: Any) -> bool:
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def load_curve_series(ref: RunRef) -> dict[str, dict[str, dict[str, Any]]]:
    read_config(ref)
    return _jsonl_curve_series(io.contained(ref.assets_dir / io.TRACKER_JSONL,ref.run_dir))


def _jsonl_curve_series(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    records = []
    try:
        with io._owned_stream(path.open('rb')) as handle:
            while (line := handle.readline()):
                end = handle.tell()
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    records.append((end - len(line), end, row))
    except OSError:
        pass
    active = []
    limit = math.inf
    for start, end, row in reversed(records):
        if end > limit:
            continue
        if row.get('event') == 'start':
            keep = row.get('keep_until')
            if type(keep) is int and 0 <= keep <= start:
                limit = keep
            continue
        active.append(row)
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in reversed(active):
        split, name = (row.get('split'), row.get('name'))
        if isinstance(split, str) and isinstance(name, str) and _finite(row.get('mean')):
            buckets.setdefault(split, {}).setdefault(name, []).append(row)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for split, names in buckets.items():
        for name, rows in names.items():
            if all((type(row.get('optimizer_step')) is int and row['optimizer_step'] >= 0 for row in rows)):
                unit, key = ('step', 'optimizer_step')
            elif all(('optimizer_step' not in row and _finite(row.get('epoch')) and (row['epoch'] >= 0) for row in rows)):
                unit, key = ('epoch', 'epoch')
            else:
                unit, key = ('observation', None)
            if key is None:
                x, values = (list(range(1, len(rows) + 1)), [row['mean'] for row in rows])
            else:
                points = {row[key]: row['mean'] for row in rows}
                x = sorted(points)
                values = [points[point] for point in x]
            out.setdefault(split, {})[name] = {'unit': unit, 'x': x, 'values': values}
    return out


def summarize_curve_series(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    units = sorted({row['unit'] for row in series if row['values']})
    summaries = {}
    for unit in units:
        rows = [row for row in series if row['unit'] == unit and row['values']]
        if unit == 'observation':
            summary = summarize_histories([row['values'] for row in rows])
            summary.update(unit=unit, x=list(range(1, summary['length'] + 1)), n_at_point=[summary['n']] * summary['length'])
        else:
            points = [dict(zip(row['x'], row['values'])) for row in rows]
            x = sorted({point for row in points for point in row})
            stats = [summarize_numbers([row[point] for row in points if point in row]) for point in x]
            summary = {key: [stat[key] for stat in stats] for key in ('mean', 'std', 'min', 'max')}
            summary.update(unit=unit, x=x, n=len(rows), length=len(x), n_at_point=[stat['n'] for stat in stats])
        summaries[unit] = summary
    if not summaries:
        return None
    return next(iter(summaries.values())) if len(summaries) == 1 else {'by_unit': summaries}


def summarize_numbers(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {'mean': None, 'std': None, 'min': None, 'max': None, 'n': 0}
    return {'mean': float(statistics.fmean(values)), 'std': float(statistics.stdev(values)) if len(values) >= 2 else 0.0, 'min': float(min(values)), 'max': float(max(values)), 'n': len(values)}

def summarize_histories(series: list[list[float]]) -> dict[str, Any] | None:
    rows = [row for row in series if row]
    if not rows:
        return None
    length = min((len(row) for row in rows))
    means: list[float] = []
    stds: list[float] = []
    mins: list[float] = []
    maxs: list[float] = []
    for i in range(length):
        col = [row[i] for row in rows]
        means.append(float(statistics.fmean(col)))
        stds.append(float(statistics.stdev(col)) if len(col) >= 2 else 0.0)
        mins.append(float(min(col)))
        maxs.append(float(max(col)))
    return {'mean': means, 'std': stds, 'min': mins, 'max': maxs, 'n': len(rows), 'length': length}
