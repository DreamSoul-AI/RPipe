"""Register the paired Algorithm with the existing RPipe factories."""
from . import paired


def register(ctx, pair):
    from rpipe.payloads.algorithm import Algorithm
    from rpipe.payloads.algorithm import AlgorithmRegistry
    from rpipe.payloads.data import DataRegistry

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
            self.initial_rng = engine.rng(device)

        def run(self, data, model, system, tracker):
            self.report = engine.run_pair(workspace, device, pair,
                (data, model, system, tracker, self.initial_rng))
            row = self.report['runs'][0]
            return dict(row['summary'], elapsed_seconds=row['elapsed_seconds'])

    DataRegistry.register(pair[0], 'main_probe', build_data)
    AlgorithmRegistry.register('train', 'main_probe', PairedAlgorithm)
