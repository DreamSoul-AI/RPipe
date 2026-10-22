"""Historical model adapter registered through the existing ModelRegistry."""
from pathlib import Path
from typing import Any
from ..api import COMMIT, SOURCE, MODEL_NAMES


def register(archive, *, seed):
    from rpipe.structure.model.factory import Model, ModelRegistry

    register_seed = int(seed)

    def build_model(model_config: Any, assets_dir: Path, data_meta: dict[str, Any] | None = None):
        if not data_meta:
            raise ValueError('historical model requires dataset metadata')
        shape = list(data_meta['data_size'])
        data_name = 'MNIST' if shape == [1, 28, 28] else 'CIFAR10'
        run_seed = int(data_meta.get('seed', register_seed))
        archive.configure(data_name, model_config.name, run_seed)
        module = archive.model.make_model(model_config.name)
        # The original instance, weights, buffers and state_dict keys survive.
        # Forward adapts the current loop's tensor input to its original f(x).
        module.forward = module.f
        return Model(name=model_config.name, source=SOURCE, module=module,
                     meta={'ready': True, 'assets_dir': str(assets_dir), 'historical_commit': COMMIT,
                           'data_size': shape, 'target_size': 10,
                           'config': dict(model_config.config), 'logits_method': 'f'})


    for name in MODEL_NAMES:
        ModelRegistry.register(name, SOURCE, build_model)
