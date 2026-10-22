"""Runtime Model object, registry, and factory."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from rpipe.structure.contracts import ModelConfig
from rpipe.structure.artifacts import cache_lock
from rpipe.structure.system.origin import apply_model_origin, normalize_origin


def init_param(m: Any) -> Any:
    import torch.nn as nn

    if isinstance(m, (nn.Conv2d, nn.ConvTranspose2d)):
        nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.BatchNorm2d):
        if m.weight is not None:
            m.weight.data.fill_(1)
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.Linear):
        if m.bias is not None:
            m.bias.data.zero_()
    return m


def resolve_shape(cfg: dict[str, Any], data_meta: dict[str, Any] | None) -> tuple[tuple[int, ...], int]:
    meta = dict(data_meta or {})
    target_size = int(cfg.get('target_size') or cfg.get('out_features') or meta.get('target_size') or 10)
    if cfg.get('data_size') is not None:
        data_size = tuple(int(x) for x in cfg['data_size'])
    elif meta.get('data_size') is not None:
        data_size = tuple(int(x) for x in meta['data_size'])
    elif cfg.get('in_features') is not None:
        data_size = (int(cfg['in_features']),)
    else:
        data_size = (1, 28, 28)
    return data_size, target_size


class Model:
    def __init__(
        self,
        *,
        name: str,
        source: str,
        module: Any | None,
        meta: dict[str, Any],
    ) -> None:
        self.name = name
        self.source = source
        self.module = module
        self.meta = meta

    def to_result_snapshot(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'source': self.source,
            **{k: v for k, v in self.meta.items() if k != 'module'},
        }


class ModelRegistry:
    _items: dict[tuple[str, str], Callable[..., Model]] = {}

    @classmethod
    def register(cls, name: str, source: str, builder: Callable[..., Model]) -> None:
        cls._items[(name, source)] = builder

    @classmethod
    def get(cls, name: str, source: str) -> Callable[..., Model] | None:
        return cls._items.get((name, source))

    @classmethod
    def list(cls) -> list[tuple[str, str]]:
        return sorted(cls._items)


class ModelFactory:
    @staticmethod
    def build(
        model_config: ModelConfig,
        assets_dir: Path | str,
        data_meta: dict[str, Any] | None = None,
        origin: str | None = None,
    ) -> Model:
        name = model_config.name
        source = model_config.source if model_config.source is not None else 'custom_torch'
        builder = ModelRegistry.get(name, source)
        if builder is None:
            raise ValueError(f'unknown model name/source: {name!r}/{source!r}')
        if origin not in (None, ''):
            apply_model_origin(origin)
        with cache_lock(assets_dir):
            model = builder(model_config, Path(assets_dir), data_meta=data_meta)
        model.module = _attach_input_norm(model.module, data_meta)
        if origin not in (None, ''):
            chosen = normalize_origin(origin)
            model.meta['origin'] = chosen
            model.meta['hub'] = apply_model_origin(chosen)
        return model


def _attach_input_norm(module: Any, data_meta: dict[str, Any] | None) -> Any:
    """Wrap with kornia Normalize, and train-only crop / flip when augment is on."""
    if module is None or not isinstance(data_meta, dict):
        return module
    mean = data_meta.get('mean')
    std = data_meta.get('std')
    if not mean or not std:
        return module
    from rpipe.structure.model.input_norm import InputNorm

    data_size = data_meta.get('data_size') or []
    spatial = int(data_size[-1]) if data_size else None
    kind = data_meta.get('train_aug') if data_meta.get('augment') else None
    return InputNorm(module, tuple(mean), tuple(std), augment=kind, spatial=spatial)


def _wrap(name: str, model_config: ModelConfig, assets_dir: Path, module: Any, extra: dict[str, Any]) -> Model:
    module.apply(init_param)
    return Model(
        name=name,
        source=model_config.source or 'custom_torch',
        module=module,
        meta={
            'ready': True,
            'assets_dir': str(assets_dir),
            'config': dict(model_config.config),
            **extra,
        },
    )


def _build_linear(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    from rpipe.structure.model.architectures import Linear

    cfg = dict(model_config.config)
    data_size, target_size = resolve_shape(cfg, data_meta)
    module = Linear(data_size, target_size)
    return _wrap(
        'linear',
        model_config,
        assets_dir,
        module,
        {'data_size': list(data_size), 'target_size': target_size, 'in_features': int(math.prod(data_size))},
    )


def _build_mlp(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    from rpipe.structure.model.architectures import MLP

    cfg = dict(model_config.config)
    data_size, target_size = resolve_shape(cfg, data_meta)
    module = MLP(
        data_size,
        hidden_size=int(cfg.get('hidden_size', 128)),
        scale_factor=float(cfg.get('scale_factor', 2)),
        num_layers=int(cfg.get('num_layers', 2)),
        activation=str(cfg.get('activation', 'relu')),
        target_size=target_size,
    )
    return _wrap(
        'mlp',
        model_config,
        assets_dir,
        module,
        {'data_size': list(data_size), 'target_size': target_size},
    )


def _build_cnn(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    from rpipe.structure.model.architectures import CNN

    cfg = dict(model_config.config)
    data_size, target_size = resolve_shape(cfg, data_meta)
    hidden = [int(x) for x in (cfg.get('hidden_size') or [64, 128, 256, 512])]
    module = CNN(data_size, hidden, target_size)
    return _wrap(
        'cnn',
        model_config,
        assets_dir,
        module,
        {'data_size': list(data_size), 'target_size': target_size, 'hidden_size': hidden},
    )


def _build_resnet(
    model_config: ModelConfig,
    assets_dir: Path,
    data_meta: dict[str, Any] | None = None,
    *,
    name: str,
    num_blocks: list[int],
) -> Model:
    from rpipe.structure.model.architectures import ResNet

    cfg = dict(model_config.config)
    data_size, target_size = resolve_shape(cfg, data_meta)
    hidden = [int(x) for x in (cfg.get('hidden_size') or [64, 128, 256, 512])]
    module = ResNet(data_size, hidden, num_blocks, target_size)
    return _wrap(
        name,
        model_config,
        assets_dir,
        module,
        {'data_size': list(data_size), 'target_size': target_size, 'hidden_size': hidden},
    )


def _build_resnet18(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    return _build_resnet(model_config, assets_dir, data_meta, name='resnet18', num_blocks=[2, 2, 2, 2])


def _build_resnet10(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    return _build_resnet(model_config, assets_dir, data_meta, name='resnet10', num_blocks=[1, 1, 1, 1])


def _build_wresnet(
    model_config: ModelConfig,
    assets_dir: Path,
    data_meta: dict[str, Any] | None = None,
    *,
    name: str,
    depth: int,
    widen_factor: int,
) -> Model:
    from rpipe.structure.model.architectures import WideResNet

    cfg = dict(model_config.config)
    data_size, target_size = resolve_shape(cfg, data_meta)
    depth_v = int(cfg.get('depth', depth))
    widen = int(cfg.get('widen_factor', widen_factor))
    drop = float(cfg.get('drop_rate', 0.0) or 0.0)
    module = WideResNet(data_size, target_size, depth_v, widen, drop)
    return _wrap(
        name,
        model_config,
        assets_dir,
        module,
        {
            'data_size': list(data_size),
            'target_size': target_size,
            'depth': depth_v,
            'widen_factor': widen,
            'drop_rate': drop,
        },
    )


def _build_wresnet28x2(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    return _build_wresnet(model_config, assets_dir, data_meta, name='wresnet28x2', depth=28, widen_factor=2)


def _build_wresnet28x8(model_config: ModelConfig, assets_dir: Path, data_meta: dict[str, Any] | None = None) -> Model:
    return _build_wresnet(model_config, assets_dir, data_meta, name='wresnet28x8', depth=28, widen_factor=8)


ModelRegistry.register('linear', 'custom_torch', _build_linear)
ModelRegistry.register('mlp', 'custom_torch', _build_mlp)
ModelRegistry.register('cnn', 'custom_torch', _build_cnn)
ModelRegistry.register('resnet18', 'custom_torch', _build_resnet18)
ModelRegistry.register('resnet', 'custom_torch', _build_resnet18)
ModelRegistry.register('resnet10', 'custom_torch', _build_resnet10)
ModelRegistry.register('wresnet28x2', 'custom_torch', _build_wresnet28x2)
ModelRegistry.register('wresnet28x8', 'custom_torch', _build_wresnet28x8)
ModelRegistry.register('wresnet', 'custom_torch', _build_wresnet28x2)
