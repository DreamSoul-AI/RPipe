"""The six phase semantics, with actual work and one publication owner."""
from __future__ import annotations

from rpipe.structure.execution import Cancelled
from rpipe.structure.contracts import AdmissionError, SourceMismatch

from .study import run_study_phase


def pipeline(payload, ctx) -> dict:
    study = ctx.state['study']

    def hook(phase):
        run_study_phase(ctx.study_dir, phase, ctx, study)
        check()

    def check():
        if ctx.config != ctx.spec.to_config() or (ctx.control is not None and ctx.control.to_dict() != ctx.spec.to_config()):
            raise SourceMismatch('effective configuration changed after admission')
        if ctx.cancellation.is_set():
            raise Cancelled('cancelled between phases')

    if not getattr(payload, 'manages_prepare_hook', False):
        ctx.state['before_prepare'](ctx)
    payload.prepare(ctx)
    check()
    hook('prepare')
    check()
    payload.execute(ctx)
    check()
    hook('execute')
    check()
    collected = payload.collect(ctx)
    check()
    if not isinstance(collected, dict):
        raise AdmissionError('collect must return a mapping')
    if 'metrics' not in collected:
        special = {'quality', 'evidence_complete', 'observations', 'structure', 'paths'}
        collected = {**{k:v for k,v in collected.items() if k in special},
                     'metrics': {k:v for k,v in collected.items() if k not in special}}
    ctx.state['collected'] = collected
    hook('collect')
    check()
    ctx.state['result_draft'] = dict(ctx.state['collected'])
    hook('summarize')
    check()
    # Original write hook timing: complete before the coordinator publishes.
    hook('write')
    check()
    return dict(ctx.state['result_draft'])
