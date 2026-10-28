"""Explicit derived processing using saved results, never live scientific objects."""
from __future__ import annotations

from types import SimpleNamespace

from rpipe.structure.control import AdmissionError
from rpipe.structure.artifact import io, provenance
from rpipe.structure.readout import aggregate
from .context import StudyProcessContext
from .study import run_study_phase


def process_run(spec, record):
    provenance.validate_source(spec)
    saved = io.read_json(spec.result_path)
    if saved != record or saved.get('identity') != io.identity(spec):
        raise AdmissionError('Run process requires its saved scientific result')
    study = io.load_config(spec.source_dir / 'study.yaml')
    ctx = StudyProcessContext(spec.source_dir, io.ArtifactLayout(spec), specs=(spec,),
                              records=(saved,), target=spec.run_dir, scope='run',
                              state={'study': study, 'result': saved})
    run_study_phase(ctx.study_dir, 'process', ctx, study)
    return saved


def process_study(specs):
    specs = tuple(specs)
    if len({(s.study_root, s.source_dir, s.source_digest) for s in specs}) > 1:
        raise AdmissionError('one Study source/version/output root is required')
    for spec in specs:
        provenance.validate_source(spec)
        io.validate_input_paths(spec, output_root=spec.study_root / 'summary')
    result = aggregate(specs)
    if specs:
        for spec in specs:
            provenance.validate_source(spec)
        root = specs[0].study_root
        io.write_json(root / 'summary' / 'process.json', result)
        study = io.load_config(specs[0].source_dir / 'study.yaml')
        ctx = StudyProcessContext(specs[0].source_dir, SimpleNamespace(study_root=root, root=root),
                                  specs=specs, records=tuple(result.get('runs', ())),
                                  target=root / 'summary',
                                  state={'study': study, 'process': result, 'run_root': root})
        run_study_phase(ctx.study_dir, 'process', ctx, study)
    return result
