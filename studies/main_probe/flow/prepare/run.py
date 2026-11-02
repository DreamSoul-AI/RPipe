"""Prepare isolated evidence before Factory construction, then check objects."""
from .data import prepare
from ...structure.algorithm.paired import seed_runtime
from ...structure.algorithm import paired
from ...recipe import validate


def before(ctx):
    pair = validate(ctx.config, ctx.control.seed, paired)
    workspace = ctx.layout.assets_dir / 'probe'
    source = ctx.layout.shared_data_dir
    try:
        prepared = prepare(workspace, source, pair)
    except BaseException as error:
        ctx.state.setdefault('payload_cleanup_errors', []).extend(getattr(error, 'cleanup_errors', ()))
        raise
    if prepared.get('passed') is not True:
        raise RuntimeError(f'CPU preparation failed; evidence: {workspace}')
    ctx.state['probe_prepared'] = prepared
    seed_runtime(str(ctx.control.system.get('device', 'cpu')))


def run(ctx):
    if ctx.state.get('probe_prepared', {}).get('passed') is not True:
        raise RuntimeError('probe preparation evidence missing')
    if ctx.state['algorithm'].workspace != ctx.layout.assets_dir / 'probe':
        raise RuntimeError('paired Algorithm must use the prepared Run workspace')
