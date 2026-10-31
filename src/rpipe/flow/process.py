"""Explicit derived processing using saved results, never live scientific objects."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from rpipe.structure.control import AdmissionError, SourceMismatch
from rpipe.structure.artifact import io, provenance
from rpipe.structure.artifact.readout import aggregate, refs_from_index
from .context import StudyProcessContext
from .study import run_study_phase


def _digest(path, root):
    path = io.contained(path, root)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _inputs(specs, *, study):
    return {
        'index_sha256': (_digest(specs[0].study_root / 'index.json', specs[0].run_root)
                         if study else None),
        'runs': [io.record_inputs(spec.ref) for spec in specs],
    }


def _check_inputs(specs, expected, *, study):
    for spec in specs:
        provenance.validate_source(spec)
        io.validate_input_paths(spec, output_root=spec.study_root / 'summary' if study else spec.run_dir)
    if _inputs(specs, study=study) != expected:
        raise SourceMismatch('derived inputs changed while processing')


def process_run(spec, record):
    provenance.validate_source(spec)
    inputs = _inputs((spec,), study=False)
    saved = io.read_json(spec.result_path)
    # JSON spelling distinguishes booleans from integers at this persisted boundary.
    if (json.dumps(saved, sort_keys=True, allow_nan=False)
            != json.dumps(record, sort_keys=True, allow_nan=False)
            or saved.get('identity') != io.identity(spec)):
        raise AdmissionError('Run process requires its saved scientific result')
    study = io.load_config(spec.source_dir / 'study.yaml')
    ctx = StudyProcessContext(spec.source_dir, io.ArtifactLayout(spec), specs=(spec,),
                              records=(saved,), target=spec.run_dir, scope='run',
                              state={'study': study, 'result': saved, 'inputs': inputs})
    _check_inputs((spec,), inputs, study=False)
    run_study_phase(ctx.study_dir, 'process', ctx, study)
    return saved


def process_study(specs):
    specs = tuple(specs)
    if not specs:
        return aggregate(())
    if len({(s.study_root, s.source_dir, s.source_digest) for s in specs}) > 1:
        raise AdmissionError('one Study source/version/output root is required')
    for spec in specs:
        provenance.validate_source(spec)
        io.validate_input_paths(spec, output_root=spec.study_root / 'summary')
    inputs = _inputs(specs, study=True)
    published = {ref.id: ref for ref in refs_from_index(specs[0].study_root)}
    if any(published.get(spec.id) != spec.ref for spec in specs):
        raise SourceMismatch('current plan differs from the published Run identities or dependencies')
    result = aggregate(tuple(spec.ref for spec in specs))
    result['inputs'] = inputs
    _check_inputs(specs, inputs, study=True)
    root = specs[0].study_root
    io.write_json(root / 'summary' / 'process.json', result)
    study = io.load_config(specs[0].source_dir / 'study.yaml')
    ctx = StudyProcessContext(specs[0].source_dir, SimpleNamespace(study_root=root, root=root),
                              specs=specs, records=tuple(result.get('runs', ())),
                              target=root / 'summary',
                              state={'study': study, 'process': result, 'run_root': root,
                                     'inputs': inputs})
    run_study_phase(ctx.study_dir, 'process', ctx, study)
    return result
