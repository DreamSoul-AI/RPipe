"""Scientific stages and the sole owner-checked publication operation."""
from __future__ import annotations

import copy
from pathlib import Path
from threading import RLock

from rpipe.structure.control import (AdmissionError, SourceMismatch, AlgorithmConfig, DataConfig,
                                    ModelConfig, SystemConfig, resolve_control, validate_control)
from rpipe.structure.artifact import io, provenance
from .context import Cancelled, validate_draft
from .study import RecipeContext, apply_recipe, run_study_phase

_registry_lock = RLock()


def configure_science(ctx, *, logging_context, request_id):
    ctx.state['study'] = io.load_config(ctx.study_dir / 'study.yaml')
    ctx.control = resolve_control(ctx.config)
    ctx.state['logging_context'] = {
        **logging_context, 'logger': 'rpipe.flow.stages', 'stdout': False,
        'task_id': ctx.spec.id, 'attempt_id': ctx.state['attempt'], 'request_id': request_id,
        'workflow_id': ctx.spec.experiment, 'layer': 'flow'}


def _phase(ctx, name):
    ctx.state['phase'] = name
    logger = ctx.state.get('logger')
    if logger is not None:
        logger.bind(phase=name)
        logger.info('Run phase started', event='task.phase_changed')


def _cancel(ctx):
    if ctx.cancellation.is_set():
        raise Cancelled('scientific cancellation requested')


def _check(ctx):
    ctx.validate_config(ctx.spec)
    _cancel(ctx)


def _hook(ctx, name):
    run_study_phase(ctx.study_dir, name, ctx, ctx.state['study'])
    _check(ctx)

def prepare(ctx):
    # NumPy must precede Torch on affected Windows OpenMP installations.
    import numpy  # noqa: F401

    from rpipe.structure.algorithm.factory import build_algorithm
    from rpipe.structure.system.factory import build_system
    from rpipe.structure.data.factory import build_data, vision_root
    from rpipe.structure.model.factory import build_model
    from rpipe.structure.system.origin import apply_model_origin, normalize_origin
    from rpipe.structure.system.runtime import apply_runtime
    from rpipe.structure.algorithm.tracker import make_tracker
    from rpipe.structure.algorithm.factory import AlgorithmRegistry
    from rpipe.structure.data.factory import DataRegistry
    from rpipe.structure.model.factory import ModelRegistry

    _registry_lock.acquire()
    ctx.state['_registry_lock'] = True
    ctx.state['_registries'] = [(registry, None if registry._items is None else dict(registry._items))
                                for registry in (AlgorithmRegistry, DataRegistry, ModelRegistry)]

    cfg = copy.deepcopy(ctx.config)
    ctx.control = resolve_control(cfg)
    validate_control(ctx.control)
    system_config = SystemConfig.from_mapping(ctx.control.system)
    ctx.state['runtime'] = apply_runtime(ctx.control.seed, system_config)
    system = build_system(system_config, ctx.layout.assets_dir,
                          logging_context=ctx.state['logging_context'])
    ctx.register_resource(system)
    system.meta.update(ctx.state['runtime'])
    ctx.state.update(system=system, logger=system.logger, seed=ctx.control.seed)
    _phase(ctx, 'prepare')
    _cancel(ctx)
    run_study_phase(ctx.study_dir, 'prepare', ctx, ctx.state['study'], entry='before')
    ctx.validate_config(ctx.spec)
    ctx.state['recipe'] = apply_recipe(ctx.study_dir, ctx.state['study'], RecipeContext(
        ctx.study_dir, ctx.spec.id, ctx.spec.seed, ctx.config, ctx.layout.shared_data_dir,
        ctx.layout.shared_model_dir, ctx.layout.assets_dir, study_root=ctx.layout.study_root,
        run_root=ctx.spec.run_root))
    ctx.validate_config(ctx.spec)
    origin = cfg.get('origin')
    if origin not in (None, ''):
        apply_model_origin(normalize_origin(origin))
    data_mapping = copy.deepcopy(ctx.control.data)
    data_config = DataConfig.from_mapping(data_mapping)
    for name in ('stats_path',):
        value = data_config.config.get(name)
        if value is not None:
            path = Path(value)
            data_config.config[name] = str(path if path.is_absolute() else ctx.study_dir / path)
    if data_config.path:
        path = Path(data_config.path)
        data_config.path = str(path if path.is_absolute() else ctx.study_dir / path)
    profile = None
    name = vision_root(data_config.name)
    if name is not None:
        profile = ctx.layout.shared_dir / 'data' / name / 'stats.yaml'
    data = build_data(data_config, ctx.layout.shared_data_dir,
                             seed=ctx.control.seed, origin=origin, profile_path=profile)
    ctx.state['data'] = ctx.register_resource(data)
    _cancel(ctx)
    model_config = ModelConfig.from_mapping(ctx.control.model)
    if model_config.path:
        path = Path(model_config.path)
        model_config.path = str(path if path.is_absolute() else ctx.study_dir / path)
    model = build_model(model_config, ctx.layout.shared_model_dir,
                               data_meta=data.meta, system=system, origin=origin)
    ctx.state['model'] = ctx.register_resource(model)
    algorithm_config = AlgorithmConfig.from_mapping(ctx.control.algorithm)
    train_id = ctx.spec.train_id
    train_checkpoints = (ctx.spec.study_root / 'runs' / train_id / 'assets' / 'checkpoints'
                         if train_id else None)
    ctx.state['algorithm'] = ctx.register_resource(build_algorithm(
        algorithm_config, checkpoint_dir=ctx.layout.assets_dir / 'checkpoints',
        train_checkpoint_dir=train_checkpoints))
    ctx.state['tracker'] = ctx.register_resource(make_tracker(ctx.layout.assets_dir, algorithm_config))
    ctx.state.setdefault('observations', [])
    system.logger.info(f'prepared control={ctx.control.id}')


def execute(ctx):
    algorithm, tracker = ctx.state.get('algorithm'), ctx.state.get('tracker')
    if algorithm is None or tracker is None:
        raise RuntimeError('prepare must land algorithm and tracker')
    _cancel(ctx)
    try:
        result = algorithm.compute(ctx.state['data'], ctx.state['model'], ctx.state['system'], tracker,
                                   cancel_check=lambda: _cancel(ctx))
        ctx.state['execute'] = result
        ctx.state.setdefault('observations', []).append({'phase': 'execute', 'mode': algorithm.mode})
    except BaseException:
        try:
            tracker.flush_state()
        except Exception as error:
            ctx.state.setdefault('science_cleanup_errors', []).append(error)
        raise
    else:
        tracker.flush_state()

def collect(ctx):
    metrics = {}
    tracker = ctx.state.get('tracker')
    if tracker is not None:
        for split in ('train', 'test'):
            for name, value in tracker.segment_mean(split).items():
                metrics[f'{split}_{name.lower()}'] = float(value)
        accuracy = metrics.get('test_accuracy', metrics.get('train_accuracy'))
        if accuracy is not None:
            metrics['accuracy'] = accuracy
    execute = ctx.state.get('execute') or {}
    for name in ('train_loss', 'accuracy', 'best_accuracy', 'best_value', 'elapsed_seconds'):
        if execute.get(name) is not None:
            metrics.setdefault(name, float(execute[name]))
    for name in ('best_metric', 'elapsed'):
        if execute.get(name) is not None:
            metrics[name] = execute[name]
    if execute.get('mode') == 'eval' and 'accuracy' in metrics:
        metrics['eval_accuracy'] = metrics['accuracy']
    snapshots = {}
    for name in ('data', 'model', 'system'):
        item = ctx.state.get(name)
        if callable(getattr(item, 'to_result_snapshot', None)):
            snapshots[name] = item.to_result_snapshot()
    result = {'metrics': metrics, 'observations': list(ctx.state.get('observations') or []),
              'execute': dict(execute), 'structure': snapshots}
    ctx.state['collected'] = result
    return result


def summarize(ctx):
    ctx.state['result_draft'] = dict(ctx.state['collected'])
    return ctx.state['result_draft']


def compute(ctx):
    for name, operation in (('prepare', prepare), ('execute', execute), ('collect', collect),
                            ('summarize', summarize)):
        _phase(ctx, name)
        value = operation(ctx)
        _check(ctx)
        if name == 'collect':
            if not isinstance(value, dict):
                raise AdmissionError('collect must return a mapping')
            ctx.state['collected'] = value
        _hook(ctx, name)
    _phase(ctx, 'write')
    _hook(ctx, 'write')
    draft = ctx.state['result_draft']
    validate_draft(draft)
    return dict(draft)




def close_science(ctx):
    errors = [io.safe_error_record(error) for error in ctx.state.pop('science_cleanup_errors', [])]
    for resource in reversed(ctx.resources):
        close = getattr(resource, 'close', None)
        if callable(close):
            try:
                close()
            except BaseException as error:
                errors.append(io.safe_error_record(error))
                ctx.state['unknown_resources'] = True
    ctx.resources.clear()
    for name in ('data', 'model', 'algorithm', 'tracker', 'system'):
        ctx.state.pop(name, None)
    try:
        for registry, previous in ctx.state.pop('_registries', []):
            registry._items = previous
    except BaseException as error:
        errors.append(io.safe_error_record(error))
        ctx.state['unknown_resources'] = True
    finally:
        if ctx.state.pop('_registry_lock', False):
            _registry_lock.release()
    supplied = ctx.state.get('cleanup_error_records', [])
    if not isinstance(supplied, (list, tuple)):
        supplied = [AdmissionError('cleanup error records must be a sequence')]
    ctx.state['cleanup_error_records'] = [io.safe_error_record(item) for item in supplied] + errors
    ctx.state['science_closed'] = not ctx.state.get('unknown_resources', False)


def write(ctx, record, draft=None):
    """Only a verified owner records this attempt or commits its scientific result."""
    if ctx.owner is None:
        raise AdmissionError('formal write requires this attempt owner')
    ctx.owner.verify()
    if record.get('identity') != io.identity(ctx.spec) or record.get('attempt') != ctx.state['attempt']:
        raise SourceMismatch('execution record belongs to another Run or attempt')
    if draft is not None:
        if (record.get('candidate_status') != 'succeeded' or not ctx.state.get('science_closed')
                or ctx.state.get('unknown_resources') or record.get('error') or record.get('cleanup_errors')):
            raise AdmissionError('scientific success requires verified response and completed resource closure')
        provenance.validate_source(ctx.spec)
        ctx.validate_config(ctx.spec, 'before result publication')
        validate_draft(draft)
        if ctx.spec.result_path.exists():
            raise AdmissionError('scientific result is immutable; choose a new version')
        structure = draft.get('structure')
        data = structure.get('data') if isinstance(structure, dict) else None
        observed = data if isinstance(data, dict) else {}
        record['input_provenance'] = provenance.input_provenance(
            ctx.spec, observed_stats_source=observed.get('stats_source'),
            observed_stats_reference=observed.get('stats_reference'))
        result = {**draft, 'id': ctx.spec.id, 'status': 'succeeded', 'quality': draft.get('quality'),
                  'evidence_complete': draft.get('evidence_complete', True),
                  'identity': io.identity(ctx.spec), 'attempt': ctx.state['attempt'],
                  'input_provenance': record['input_provenance'],
                  'environment': ctx.state.get('worker_environment', record.get('environment'))}
        io.validate_run_record(result, expected_identity=io.identity(ctx.spec), attempt=ctx.state['attempt'])
        prospective = {**record, 'scientific_result_published': True,
                       'quality': result['quality'], 'evidence_complete': result['evidence_complete']}
        io.validate_execution_record(prospective, expected_identity=io.identity(ctx.spec))
        ctx.owner.verify()
        io.write_json(ctx.spec.result_path, result)
        ctx.state['published_result'] = result
        record.update(scientific_result_published=True, quality=result['quality'],
                      evidence_complete=result['evidence_complete'], scientific_result='result.json')
    io.validate_execution_record(record, expected_identity=io.identity(ctx.spec))
    ctx.owner.verify()
    if record['phase'] == 'closing':
        io.write_json(ctx.spec.run_dir / 'attempts' / ctx.state['attempt'] / 'outcome.json', record)
    io.write_json(ctx.spec.run_dir / 'execution.json', record)
    return ctx.state.get('published_result')


def process(ctx):
    from .process import process_run
    return process_run(ctx.spec, ctx.state['published_result'])
