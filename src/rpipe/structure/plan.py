"""Pure declaration expansion: never import recipes, models or execution."""
from __future__ import annotations

import itertools
import json
import math
from string import Formatter
from dataclasses import replace
from pathlib import Path
from collections.abc import Mapping

from .contracts import AdmissionError, RunConfig, RunSpec, compute_run_id, deep_merge, get_setting, validate_run_config




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
    source = Path(source_dir).resolve()
    if 'freeze' in study:
        raise AdmissionError('freeze is unsupported; remove it: source identity is checked, no archive is created')
    allowed = {'study', 'description', 'origin', 'experiment', 'fixed', 'axes', 'seeds', 'tags',
               'run_description', 'recipe', 'flow', 'provenance'}
    if set(study) - allowed:
        raise AdmissionError(f'unknown Study fields: {sorted(set(study) - allowed)}')
    flow = study.get('flow') or {}
    if not isinstance(flow, dict) or set(flow) - {'study_phases', 'prepare_shared'}:
        raise AdmissionError('flow must contain only study_phases and prepare_shared')
    for key, value in flow.items():
        if type(value) is not bool:
            raise AdmissionError(f'flow.{key} must be a boolean')
    if flow.get('prepare_shared'):
        raise AdmissionError('flow.prepare_shared is unsupported; prepare inputs with an explicit data request')
    fixed = study.get('fixed') or {}
    axes = dict(study.get('axes') or {})
    if not isinstance(fixed, dict):
        raise AdmissionError('fixed must be a mapping')
    seeds = study.get('seeds', axes.pop('seed', [fixed.get('seed', 0)]))
    if not isinstance(seeds, list) or not seeds or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in seeds):
        raise AdmissionError('seeds must be nonempty nonnegative integers')
    if len(set(seeds)) != len(seeds):
        raise AdmissionError('duplicate seeds')
    for key, values in axes.items():
        if not isinstance(key, str) or not isinstance(values, list) or not values:
            raise AdmissionError('axes must contain nonempty lists')
        if len({json.dumps(v, sort_keys=True) for v in values}) != len(values):
            raise AdmissionError(f'duplicate factor values: {key}')
    _finite(study)
    _finite(base)
    exp = study.get('experiment') or study.get('study') or source.name
    exp = str(exp.get('name', source.name) if isinstance(exp, dict) else exp)
    selected_version = version or fixed.get('version') or base.get('version') or 'default'
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
            tags = list(patch.get('tags') or [])
            for rule in study.get('tags') or []:
                if not isinstance(rule, dict):
                    raise AdmissionError('tag rule must be a mapping')
                if all(({**factors, 'seed': seed}).get(k) == v for k, v in (rule.get('when') or {}).items()):
                    tags.extend(rule.get('tags') or [])
            if tags:
                patch['tags'] = sorted(set(tags))
            effective = deep_merge(base, patch)
            if 'run_description' in study:
                effective['description'] = _description(study['run_description'], effective)
            run_config = RunConfig.from_mapping(effective, assign_id=True)
            validate_run_config(run_config)
            cfg = run_config.to_mapping()
            experiment_id = exp + ':' + compute_run_id(factors)
            specs.append(RunSpec(str(cfg['id']), experiment_id, seed, cfg, source, Path(run_root), digest, str(selected_version)))
    if len({s.id for s in specs}) != len(specs):
        raise AdmissionError('different declarations expand to the same Run id')
    # Explicit own-train relation. No checkpoint directory guessing during planning.
    def factors_without_mode(s):
        body = s.to_config()
        body.pop('id', None)
        body.pop('description', None)
        body.pop('tags', None)
        body['algorithm'].pop('mode', None)
        return body
    trains = [s for s in specs if s.config.get('algorithm', {}).get('mode') == 'train']
    specs = [replace(s, train_id=next((t.id for t in trains if factors_without_mode(t) == factors_without_mode(s)), None))
             if s.config.get('algorithm', {}).get('mode') == 'eval' else s for s in specs]
    return tuple(sorted(specs, key=lambda s: s.config.get('algorithm', {}).get('mode') == 'eval'))
