"""Input normalize and train augmentation in front of the network.

Same place as git main's model wrapper: pixels arrive as ``ToTensor`` values
in 0–1. Normalize always runs. Random flip / crop run only while training.
"""

from __future__ import annotations

from typing import Any

import torch.nn as nn


class InputNorm(nn.Module):
    """kornia ``Normalize``, plus optional train-only crop / flip."""

    def __init__(
        self,
        net: nn.Module,
        mean: tuple[float, ...],
        std: tuple[float, ...],
        *,
        augment: str | None = None,
        spatial: int | None = None,
    ) -> None:
        super().__init__()
        from kornia.augmentation import Normalize, RandomCrop, RandomHorizontalFlip

        self.net = net
        self.norm = Normalize(mean=tuple(float(x) for x in mean), std=tuple(float(x) for x in std), p=1.0)
        self.train_aug = _train_aug(
            augment,
            spatial,
            RandomCrop=RandomCrop,
            RandomHorizontalFlip=RandomHorizontalFlip,
        )

    def forward(self, x: Any) -> Any:
        if self.training and self.train_aug is not None:
            x = self.train_aug(x)
        return self.net(self.norm(x))


def _train_aug(kind: str | None, spatial: int | None, *, RandomCrop: Any, RandomHorizontalFlip: Any) -> nn.Module | None:
    if not kind or spatial is None:
        return None
    size = (int(spatial), int(spatial))
    if kind == 'cifar':
        return nn.Sequential(
            RandomHorizontalFlip(p=0.5),
            RandomCrop(size, padding=4, padding_mode='reflect', p=1.0),
        )
    if kind == 'svhn':
        return RandomCrop(size, padding=4, padding_mode='reflect', p=1.0)
    return None
