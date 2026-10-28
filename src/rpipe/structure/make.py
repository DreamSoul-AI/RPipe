"""Pure declaration expansion: never import recipes, models or execution."""
from __future__ import annotations

import itertools
import json
import math
from string import Formatter
from dataclasses import replace
from pathlib import Path
from collections.abc import Mapping

from .control import AdmissionError, RunSpec, SourceMismatch, BusyError, deep_merge, get_setting, resolve_control
from .artifact import io, provenance
from contextlib import ExitStack




def _finite(value) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise AdmissionError('nonfinite configuration value')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AdmissionError('configuration keys must be strings')
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise AdmissionError(f'unsupported configuration value: {type(value).__name__}')


def _set(mapping: dict, key: str, value) -> None:
    parts = key.split('.')
    if not all(parts):
        raise AdmissionError(f'invalid factor: {key}')
    for part in parts[:-1]:
        mapping = mapping.setdefault(part, {})
        if not isinstance(mapping, dict):
            raise AdmissionError(f'factor conflicts with a scalar: {key}')
    mapping[parts[-1]] = value


def _description(template, config):
    if not isinstance(template, str) or len(template) > 1024:
        raise AdmissionError('run_description must be a string of at most 1024 characters')
    data, model, algorithm = (config.get(key) or {} for key in ('data', 'model', 'algorithm'))
    values = {'data_name': data.get('name'), 'model_name': model.get('name'),
              'mode': algorithm.get('mode') or 'train', 'seed': config.get('seed'),
              'train_size': get_setting(data, 'train_size'),
              'source': algorithm.get('source') or 'custom_torch'}
    parts = []
    try:
        for literal, field, spec, conversion in Formatter().parse(template):
            parts.append(literal)
            if field is not None:
                if field not in values or spec or conversion:
                    raise ValueError('unsupported placeholder')
                value = values[field]
                if not isinstance(value, (str, bool, int, float, type(None))):
                    raise ValueError('placeholder is not scalar')
                parts.append(str(value))
    except (ValueError, TypeError) as exc:
        raise AdmissionError('run_description supports only the documented bare scalar placeholders') from exc
    result = ''.join(parts)
    if len(result) > 4096:
        raise AdmissionError('run_description output exceeds 4096 characters')
    return result


def expand(study: dict, base: dict, *, source_dir: Path | str, run_root: Path | str,
           source_digest: str, version: str | None = None) -> tuple[RunSpec, ...]:
    if not isinstance(study, dict) or not isinstance(base, dict):
        raise AdmissionError('Study declaration and base must be mappings')
    _finite(study)
    _finite(base)
    if version is not None and (not isinstance(version, str) or not version):
        raise AdmissionError('version override must be a nonempty string')
    source = Path(source_dir)
    if 'freeze' in study:
        raise AdmissionError('freeze is unsupported; remove it: source identity is checked, no archive is created')
    allowed = {'study', 'description', 'origin', 'experiment', 'fixed', 'axes', 'seeds', 'tags',
               'run_description', 'recipe', 'flow', 'provenance'}
    if set(study) - allowed:
        raise AdmissionError(f'unknown Study fields: {sorted(set(study) - allowed)}')
    flow = study.get('flow', {})
    if not isinstance(flow, dict) or set(flow) - {'study_phases'}:
        raise AdmissionError('flow must contain only study_phases; prepare_shared is not a supported field')
    for key, value in flow.items():
        if type(value) is not bool:
            raise AdmissionError(f'flow.{key} must be a boolean')
    fixed = study.get('fixed', {})
    raw_axes = study.get('axes', {})
    if not isinstance(raw_axes, dict):
        raise AdmissionError('axes must be a mapping')
    axes = dict(raw_axes)
    if 'seed' in axes:
        raise AdmissionError('declare repetitions with seeds, not axes.seed')
    if not isinstance(fixed, dict):
        raise AdmissionError('fixed must be a mapping')
    seeds = study.get('seeds', [fixed.get('seed', 0)])
    if not isinstance(seeds, list) or not seeds or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in seeds):
        raise AdmissionError('seeds must be nonempty nonnegative integers')
    if len(set(seeds)) != len(seeds):
        raise AdmissionError('duplicate seeds')
    for key, values in axes.items():
        if not isinstance(key, str) or not isinstance(values, list) or not values:
            raise AdmissionError('axes must contain nonempty lists')
        if len({json.dumps(v, sort_keys=True) for v in values}) != len(values):
            raise AdmissionError(f'duplicate factor values: {key}')
    tag_rules = study.get('tags', [])
    if not isinstance(tag_rules, list):
        raise AdmissionError('Study tags must be a list of rules')
    for rule in tag_rules:
        if not isinstance(rule, dict) or set(rule) - {'when', 'tags'}:
            raise AdmissionError('tag rule must contain only when and tags')
        when = rule.get('when', {})
        tags = rule.get('tags', [])
        if not isinstance(when, dict) or set(when) - (set(axes) | {'seed'}):
            raise AdmissionError('tag rule conditions must name declared factors or seed')
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise AdmissionError('tag rule tags must be a list of strings')
    fixed_tags = fixed.get('tags', [])
    if not isinstance(fixed_tags, list) or any(not isinstance(tag, str) for tag in fixed_tags):
        raise AdmissionError('fixed.tags must be a list of strings')
    exp = study.get('experiment') or study.get('study') or source.name
    exp = str(exp.get('name', source.name) if isinstance(exp, dict) else exp)
    selected_version = next((value for value in (version, fixed.get('version'), base.get('version'))
                             if value is not None), 'default')
    digest = source_digest
    specs = []
    for values in itertools.product(*axes.values()):
        factors = dict(zip(axes, values))
        for seed in seeds:
            patch = deep_merge({}, fixed)
            # Deep copy: neither expansion nor later caller mutation may alter the declaration.
            patch = json.loads(json.dumps(patch))
            for key, value in factors.items():
                _set(patch, key, value)
            patch['seed'] = seed
            patch['experiment'] = exp
            if study.get('origin') is not None:
                patch['origin'] = study['origin']
            tags = patch.get('tags', [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise AdmissionError('effective tags must be a list of strings')
            tags = list(tags)
            for rule in tag_rules:
                if all(({**factors, 'seed': seed}).get(k) == v for k, v in rule.get('when', {}).items()):
                    tags.extend(rule.get('tags', []))
            if tags:
                patch['tags'] = sorted(set(tags))
            effective = deep_merge(base, patch)
            if 'run_description' in study:
                effective['description'] = _description(study['run_description'], effective)
            if version is not None:
                effective['version'] = version
            cfg = resolve_control(effective, seed=seed).to_dict()
            specs.append(RunSpec(id=str(cfg['id']), seed=seed, config=cfg, source_dir=source,
                                 run_root=Path(run_root), source_digest=digest, version=str(selected_version)))
    if len({s.id for s in specs}) != len(specs):
        raise AdmissionError('different declarations expand to the same Run id')
    # Explicit own-train relation. No checkpoint directory guessing during planning.
    trains = [s for s in specs if s.config.get('algorithm', {}).get('mode') == 'train']
    resolved = []
    for spec in specs:
        if spec.config.get('algorithm', {}).get('mode') == 'eval':
            candidates = [train.id for train in trains if _training_conditions(train) == _training_conditions(spec)]
            if len(candidates) > 1:
                raise AdmissionError('ambiguous training dependency: multiple trains match the eval conditions')
            spec = replace(spec, train_id=candidates[0] if candidates else None)
        resolved.append(spec)
    return tuple(sorted(resolved, key=lambda s: s.config.get('algorithm', {}).get('mode') == 'eval'))


def _training_conditions(spec):
    body = spec.to_config()
    for key in ('id', 'description', 'tags'):
        body.pop(key, None)
    body['algorithm'].pop('mode', None)
    return body


def plan(study_dir: Path | str, *, run_root: Path | str, version: str | None = None) -> tuple[RunSpec, ...]:
    """Read declarations and source explicitly; never run a recipe or write output."""
    source, output = Path(study_dir).resolve(), Path(run_root).resolve()
    io.validate_run_root(source, output)
    study = io.load_config(source / 'study.yaml')
    base = io.load_config(source / 'experiment_config.yaml')
    specs = expand(study, base, source_dir=source, run_root=output,
                   source_digest=provenance.source_digest(source), version=version)
    for spec in specs:
        io.validate_input_paths(spec)
        io.contained(spec.run_dir, spec.run_root)
    return specs


def _validate_set(specs: tuple[RunSpec, ...]) -> None:
    if any(not isinstance(spec, RunSpec) for spec in specs):
        raise AdmissionError('publication requires RunSpecs')
    domains = {(spec.source_dir, spec.run_root, spec.source_digest, spec.version) for spec in specs}
    if len(domains) > 1:
        raise AdmissionError('one Study source/version/output root is required')
    by_id = {spec.id: spec for spec in specs}
    if len(by_id) != len(specs):
        raise AdmissionError('duplicate Run identity in publication')
    active, done = set(), set()
    def visit(spec):
        if spec.id in active:
            raise AdmissionError('cyclic training dependency')
        if spec.id in done:
            return
        active.add(spec.id)
        if spec.train_id:
            if spec.train_id not in by_id:
                raise AdmissionError('training dependency is absent from the publication set')
            visit(by_id[spec.train_id])
        active.remove(spec.id)
        done.add(spec.id)
    for spec in specs:
        visit(spec)
        if spec.train_id:
            parent = by_id[spec.train_id]
            if (parent.config.get('algorithm', {}).get('mode') != 'train'
                    or _training_conditions(parent) != _training_conditions(spec)):
                raise AdmissionError('training dependency must identify a train with matching conditions and seed')


def _check_existing(spec: RunSpec) -> None:
    config = spec.run_dir / 'config.yaml'
    if config.exists() and io.load_config(config) != spec.to_config():
        raise SourceMismatch(f'existing config differs: {spec.id}')
    if spec.result_path.exists() and io.read_json(spec.result_path).get('identity') != io.identity(spec):
        raise SourceMismatch(f'existing result differs: {spec.id}')
    execution = spec.run_dir / 'execution.json'
    if execution.exists() and io.read_json(execution).get('identity') != io.identity(spec):
        raise SourceMismatch(f'existing execution differs: {spec.id}')


def _check_index(spec: RunSpec) -> None:
    path = spec.study_root / 'index.json'
    if not path.exists():
        return
    index = io.read_json(path)
    observed = index.get('provenance')
    if (index.get('schema') != 3 or not isinstance(index.get('experiments'), list)
            or not isinstance(observed, dict) or observed.get('digest') != spec.source_digest
            or observed.get('version') != spec.version):
        raise SourceMismatch('existing plan belongs to another source or format; use a new version')


def publish(specs) -> dict:
    """Publish complete configs first and atomically commit their index last."""
    specs = tuple(specs)
    _validate_set(specs)
    if not specs:
        return {'schema': 3, 'experiments': []}
    first = specs[0]
    for spec in specs:
        provenance.validate_source(spec)
        if (spec.run_dir / '.execution').exists():
            raise BusyError(f'cannot publish a busy Run: {spec.id}')
        _check_existing(spec)
    _check_index(first)
    study = io.load_config(first.source_dir / 'study.yaml')
    factor_keys = set(study.get('axes', {})) | {'algorithm.mode'}
    def factor(config, key):
        for part in key.split('.'):
            config = config.get(part) if isinstance(config, dict) else None
        return config
    experiments = {}
    with io.cache_lock(first.study_root / '.planning'), ExitStack() as owners:
        # Stable acquisition order; every target stays owned through the index commit.
        for spec in sorted(specs, key=lambda item: item.id):
            provenance.validate_source(spec)
            owners.enter_context(io.Ownership(spec))
        for spec in specs:
            provenance.validate_source(spec)
            _check_existing(spec)
        _check_index(first)
        observed = provenance.source_manifest(first.source_dir)
        if observed['digest'] != first.source_digest:
            raise SourceMismatch('source changed before plan publication')
        for spec in specs:
            config = spec.run_dir / 'config.yaml'
            if not config.exists():
                io.write_config(config, spec.to_config())
            row = experiments.setdefault(spec.experiment, {
                'id': spec.experiment,
                'factors': {key: factor(spec.to_config(), key) for key in sorted(factor_keys)},
                'runs': []})
            row['runs'].append({'id': spec.id, 'run_dir': spec.id, 'seed': spec.seed,
                                'train_id': spec.train_id, 'identity': io.identity(spec)})
        # Source and plan are committed in one record, never two independently mutable files.
        result = {'schema': 3, 'provenance': {**observed, 'version': first.version},
                  'experiments': list(experiments.values())}
        for spec in specs:
            provenance.validate_source(spec)
            _check_existing(spec)
        io.write_json(first.study_root / 'index.json', result)
    return result
