"""Explicit public use cases. Planning and reading never select a science backend."""
from __future__ import annotations

from pathlib import Path

from .structure.contracts import AdmissionError, BusyError, SourceMismatch, Budget, Cancellation, RunOutcome, RunSpec
from .structure.artifacts import Ownership, cache_lock, identity, load_config, source_digest, validate_source, write_config, write_json
from .structure.plan import expand
from .structure.readout import read_run, aggregate, report, logs, compare, read_legacy


def plan(study_dir: Path | str, *, run_root: Path | str, version: str | None = None) -> tuple[RunSpec, ...]:
    source = Path(study_dir).resolve()
    return expand(load_config(source / 'study.yaml'), load_config(source / 'experiment_config.yaml'),
                  source_dir=source, run_root=run_root, source_digest=source_digest(source), version=version)


def run(spec: RunSpec, *, payload=None, budget: Budget | None = None,
        cancellation: Cancellation | None = None) -> RunOutcome:
    from .flow.run import run as execute_run
    return execute_run(spec, payload=payload, budget=budget, cancellation=cancellation)


def cancel(cancellation: Cancellation) -> None:
    cancellation.request()


def make(specs) -> dict:
    """Explicit configuration publication; identical existing configs stay byte exact."""
    specs = tuple(specs)
    _one_root(specs)
    for spec in specs:
        validate_source(spec)
        if (spec.run_dir / '.execution').exists():
            raise BusyError(f'cannot write a busy Run: {spec.id}')
        config = spec.run_dir / 'config.yaml'
        if config.exists() and load_config(config) != spec.to_config():
            raise SourceMismatch(f'existing config differs: {spec.id}')
        if spec.result_path.exists() and read_run(spec).get('identity') != identity(spec):
            raise SourceMismatch(f'existing result differs: {spec.id}')
    experiments = {}
    result = {'schema': 2, 'experiments': []}
    if not specs:
        return result
    declaration = load_config(specs[0].source_dir / 'study.yaml')
    keys = set((declaration.get('axes') or {}).keys()) - {'seed'}
    keys.add('algorithm.mode')
    def factor(cfg, key):
        for part in key.split('.'):
            cfg = cfg.get(part, {}) if isinstance(cfg, dict) else None
        return cfg
    with cache_lock(specs[0].study_root / '.planning'):
        for spec in specs:
            with Ownership(spec):
                config = spec.run_dir / 'config.yaml'
                if config.exists() and load_config(config) != spec.to_config():
                    raise SourceMismatch('configuration changed before publication')
                if not config.exists():
                    write_config(config, spec.to_config())
                factors = {key: factor(spec.to_config(), key) for key in sorted(keys)}
                row = experiments.setdefault(spec.experiment, {'id': spec.experiment, 'factors': factors, 'runs': []})
                row['runs'].append({'id': spec.id, 'run_dir': spec.id, 'seed': spec.seed})
        result['experiments'] = list(experiments.values())
        write_json(specs[0].study_root / 'index.json', result)
    return result


def _one_root(specs) -> None:
    roots = {(s.study_root, s.source_dir, s.source_digest) for s in specs}
    if len(roots) > 1:
        raise AdmissionError('one Study source/version/output root is required')


def launch(specs, *, budget: Budget | None = None, cancellation: Cancellation | None = None) -> tuple[RunOutcome, ...]:
    specs = tuple(specs)
    _one_root(specs)
    outcomes = []
    for spec in specs:
        if cancellation and cancellation.is_set():
            break
        outcomes.append(run(spec, budget=budget, cancellation=cancellation))
        if outcomes[-1].status == 'unknown':
            break
    return tuple(outcomes)


def process(specs) -> dict:
    """Explicit derived write, preserving every scientific result byte."""
    specs = tuple(specs)
    _one_root(specs)
    result = aggregate(specs)
    if specs:
        root = specs[0].study_root
        write_json(root / 'summary' / 'process.json', result)
        from types import SimpleNamespace
        from .flow.study import run_study_phase
        study = load_config(specs[0].source_dir / 'study.yaml')
        ctx = SimpleNamespace(scope='study', study_dir=specs[0].source_dir,
                              layout=SimpleNamespace(study_root=root, root=root),
                              state={'study': study, 'process': result, 'run_root': root}, specs=specs)
        run_study_phase(ctx.study_dir, 'process', ctx, study)
    return result


def compare_runs(*args, study_dir=None, **kwargs):
    """Explicit scientific directory/checkpoint comparison; may load trusted tensors."""
    from .structure.comparison import compare_runs as compare_files
    if study_dir is not None:
        from .flow.study import _recipe_pickle_module
        kwargs['checkpoint_reader'] = _recipe_pickle_module(study_dir)
    return compare_files(*args, **kwargs)


def write_compare(path, body):
    from .structure.comparison import write_compare as write_comparison
    return write_comparison(path, body)
