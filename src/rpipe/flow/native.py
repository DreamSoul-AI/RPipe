"""Native scientific lifecycle; execution ownership stays with the coordinator."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from threading import RLock

from rpipe.structure.system.runtime import check_cancelled

_registry_lock = RLock()

from rpipe.structure.control import AlgorithmConfig, DataConfig, ModelConfig, SystemConfig, resolve_control, validate_control


class NativeRun:
    # This adapter invokes prepare.before after system construction, before recipe.
    manages_prepare_hook = True
    def prepare(self, ctx: Any) -> None:
        # NumPy must precede Torch on affected Windows OpenMP installations.
        import numpy  # noqa: F401

        from rpipe.structure.algorithm.factory import AlgorithmFactory
        from rpipe.structure.system.factory import SystemFactory
        from rpipe.structure.data.factory import DataFactory, vision_root
        from rpipe.structure.model.factory import ModelFactory
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
        system = SystemFactory.build(system_config, ctx.layout.assets_dir, logger=ctx.state.get('logger'))
        system.meta.update(ctx.state['runtime'])
        system.study_root = Path(ctx.layout.study_root)
        system.source_dir = Path(ctx.study_dir)
        system.run_id = str(ctx.spec.id)
        train_id = getattr(ctx.spec, 'train_id', None)
        system.train_run_dir = system.study_root / 'runs' / train_id if train_id else None
        system.checkpoint_pickle_module = ctx.state.get('checkpoint_pickle_module')
        system.cancellation = ctx.cancellation
        system.logger.run_id = system.run_id
        ctx.state.update(system=system, logger=system.logger, seed=ctx.control.seed)
        check_cancelled(system)
        before = ctx.state.get('before_prepare')
        if before is not None:
            before(ctx)
        ctx.validate_config(ctx.spec)
        recipe = ctx.state.get('apply_recipe')
        ctx.state['recipe'] = bool(recipe(ctx)) if recipe is not None else False
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
        data = DataFactory.build(data_config, ctx.layout.shared_data_dir,
                                 seed=ctx.control.seed, origin=origin, profile_path=profile)
        ctx.state['data'] = data
        check_cancelled(system)
        model_config = ModelConfig.from_mapping(ctx.control.model)
        if model_config.path:
            path = Path(model_config.path)
            model_config.path = str(path if path.is_absolute() else ctx.study_dir / path)
        model = ModelFactory.build(model_config, ctx.layout.shared_model_dir,
                                   data_meta=getattr(data, 'meta', None), origin=origin)
        ctx.state['model'] = model
        if model.module is not None:
            model.module = system.place_module(model.module)
        algorithm_config = AlgorithmConfig.from_mapping(ctx.control.algorithm)
        ctx.state['algorithm'] = AlgorithmFactory.build(algorithm_config)
        ctx.state['tracker'] = make_tracker(ctx.layout.assets_dir, algorithm_config)
        ctx.state.setdefault('observations', [])
        system.logger.info(f'prepared control={ctx.control.id}')

    def execute(self, ctx: Any) -> None:
        algorithm, tracker = ctx.state.get('algorithm'), ctx.state.get('tracker')
        if algorithm is None or tracker is None:
            raise RuntimeError('prepare must land algorithm and tracker')
        check_cancelled(ctx.state['system'])
        try:
            result = algorithm.run(ctx.state['data'], ctx.state['model'], ctx.state['system'], tracker)
            ctx.state['execute'] = result
            ctx.state.setdefault('observations', []).append({'phase': 'execute', 'mode': algorithm.mode})
        except BaseException:
            try:
                tracker.flush_state()
            except Exception as error:
                ctx.state.setdefault('payload_cleanup_errors', []).append(error)
            raise
        else:
            tracker.flush_state()

    def collect(self, ctx: Any) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
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

    def close(self, ctx: Any) -> None:
        errors = list(ctx.state.pop('payload_cleanup_errors', []))
        for name in ('data', 'model', 'algorithm', 'tracker', 'system'):
            item = ctx.state.pop(name, None)
            close = getattr(item, 'close', None)
            if callable(close):
                try:
                    close()
                except BaseException as error:
                    errors.append(error)
        for registry, previous in ctx.state.pop('_registries', []):
            registry._items = previous
        if ctx.state.pop('_registry_lock', False):
            _registry_lock.release()
        if errors:
            error = RuntimeError('scientific payload cleanup failed: ' + '; '.join(str(item) for item in errors))
            error.errors = tuple(errors)
            raise error from errors[0]
