"""Explicit public use cases. Planning and reading never select a science backend."""
from __future__ import annotations

from pathlib import Path

from rpipe.structure.control import AdmissionError, BusyError, SourceMismatch, RunSpec
from rpipe.structure.contracts import Budget, Cancellation, RunOutcome, SupervisionLimits
from rpipe.structure.artifact.io import validate_input_paths, write_json
from rpipe.structure.artifact.provenance import validate_source
from .structure.readout import read_run, aggregate, report, logs, compare, read_legacy




def run(spec: RunSpec, *, payload=None, budget: Budget | None = None,
        cancellation: Cancellation | None = None, retry: bool = False) -> RunOutcome:
    from .flow.run import run as execute_run
    return execute_run(spec, payload=payload, budget=budget, cancellation=cancellation, retry=retry)


def cancel(cancellation: Cancellation) -> None:
    cancellation.request()


def supervise(specs, *, limits: SupervisionLimits, evidence_dir: Path | str,
              cancellation: Cancellation | None = None, retry: bool = False) -> dict:
    """Supervise an explicitly bounded Windows batch; no experimental defaults."""
    from .flow.supervision import supervise as supervise_batch

    return supervise_batch(specs, limits=limits, evidence_dir=evidence_dir,
                           cancellation=cancellation, retry=retry)




def _one_root(specs) -> None:
    roots = {(s.study_root, s.source_dir, s.source_digest) for s in specs}
    if len(roots) > 1:
        raise AdmissionError('one Study source/version/output root is required')


def launch(specs, *, budget: Budget | None = None, cancellation: Cancellation | None = None,
           retry: bool = False) -> tuple[RunOutcome, ...]:
    specs = tuple(specs)
    _one_root(specs)
    outcomes = []
    for spec in specs:
        if cancellation and cancellation.is_set():
            break
        outcomes.append(run(spec, budget=budget, cancellation=cancellation, retry=retry))
        if outcomes[-1].status == 'unknown':
            break
    return tuple(outcomes)


def process(specs) -> dict:
    """Explicit derived write, preserving every scientific result byte."""
    specs = tuple(specs)
    _one_root(specs)
    result = aggregate(specs)
    if specs:
        for spec in specs:
            validate_source(spec)
            validate_input_paths(spec, output_root=spec.study_root / 'summary')
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
