"""Native batch prep: flatten only when the module is a linear-style head."""

from __future__ import annotations

from typing import Any


def maybe_flatten_images(images: Any, module: Any) -> Any:
    if images.dim() <= 2:
        return images
    flat = images.reshape(images.size(0), -1)
    in_features = getattr(module, 'in_features', None)
    if in_features is not None and int(flat.size(1)) == int(in_features):
        return flat
    return images


def prepare_tensors(batch: Any, module: Any, device: Any) -> tuple[Any, Any]:
    images, targets = batch
    images = maybe_flatten_images(images.to(device), module)
    targets = targets.to(device)
    return images, targets
