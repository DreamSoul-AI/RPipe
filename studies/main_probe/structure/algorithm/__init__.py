"""Register the paired Algorithm with the existing RPipe factories."""
from . import paired


def register(ctx, pair):
    from rpipe.structure.algorithm.factory import Algorithm
    from rpipe.structure.algorithm.factory import AlgorithmRegistry
    from rpipe.structure.data.factory import DataRegistry

    engine = paired
    workspace = ctx.assets_dir / 'probe'
    import json
    prepared_path = workspace / 'PREPARED.json'
    if not prepared_path.is_file():
        raise RuntimeError('Study prepare.before must prepare the probe before registration')
    prepared = json.loads(prepared_path.read_text(encoding='utf-8'))
    if prepared.get('passed') is not True:
        raise RuntimeError(f'CPU preparation failed; evidence: {workspace}')
    device = str(ctx.config['system'].get('device', 'cpu'))
    engine.seed_runtime(device)

    def build_data(config, root, *, seed=None, origin=None, **kwargs):
        return engine.native_data(workspace, pair[0], ctx.assets_dir)

    class PairedAlgorithm(Algorithm):
        def __init__(self, config):
            super().__init__(config)
            self.workspace = workspace
            self.report = None
            self.cleanup_errors = ()
            self.initial_rng = engine.rng(device)

        def compute(self, data, model, system, tracker, cancel_check):
            try:
                self.report = engine.run_pair(workspace, device, pair,
                    (data, model, system, tracker, self.initial_rng), cancel_check=cancel_check)
            except BaseException as error:
                self.cleanup_errors = getattr(error, 'cleanup_errors', ())
                raise
            row = self.report['runs'][0]
            return dict(row['summary'], elapsed_seconds=row['elapsed_seconds'])

        def close(self):
            errors, self.cleanup_errors = self.cleanup_errors, ()
            if errors:
                error = RuntimeError('probe resource cleanup failed')
                error.errors = tuple(errors)
                raise error from errors[0]

    DataRegistry.register(pair[0], 'main_probe', build_data)
    AlgorithmRegistry.register('train', 'main_probe', PairedAlgorithm)
