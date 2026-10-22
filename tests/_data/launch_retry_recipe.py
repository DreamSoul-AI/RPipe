"""Synthetic single-use Algorithm for native CLI retry-policy tests."""
from rpipe.payloads.algorithm import Algorithm
from rpipe.payloads.algorithm import AlgorithmRegistry
from rpipe.payloads.models import Model, ModelRegistry


class Once(Algorithm):
    def run(self, data, model, system, tracker):
        with (system.assets_dir / 'attempts.txt').open('a', encoding='utf-8') as stream:
            stream.write('attempt\n')
        (system.assets_dir / 'exclusive').mkdir()
        if self.config.setting('fail', True):
            raise RuntimeError('ORIGINAL_FAILURE')
        return {}


def register(ctx):
    AlgorithmRegistry.register('inference', 'retry_fixture', Once)
    ModelRegistry.register('none', 'retry_fixture', lambda config, root, **kwargs:
                           Model(name='none', source='retry_fixture', module=None, meta={}))
