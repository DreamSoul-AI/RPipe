"""Batch / full metrics compatible with git main names (Loss, Accuracy, MSE, RMSE, GLUE)."""

from __future__ import annotations

from typing import Any

CANON = {
    'loss': 'Loss',
    'accuracy': 'Accuracy',
    'mse': 'MSE',
    'rmse': 'RMSE',
    'glue': 'GLUE',
}

DEFAULT_NAMES = {
    'train': ['Loss', 'Accuracy'],
    'test': ['Loss', 'Accuracy'],
}

BATCH_NAMES = frozenset({'Loss', 'Accuracy', 'MSE'})
FULL_NAMES = frozenset({'RMSE', 'GLUE'})


def canon_name(raw: Any) -> str:
    text = str(raw or '').strip()
    return CANON.get(text.lower(), text or 'Loss')


def resolve_metric_names(raw: Any) -> dict[str, list[str]]:
    if raw is None:
        return {k: list(v) for k, v in DEFAULT_NAMES.items()}
    if isinstance(raw, dict):
        out = {str(split): [canon_name(name) for name in
                            (names if isinstance(names, (list, tuple)) else [names])]
               for split, names in raw.items()}
        if out:
            return out
    if isinstance(raw, (list, tuple)):
        names = [canon_name(name) for name in raw]
        return {'train': list(names), 'test': list(names)}
    name = canon_name(raw)
    return {'train': [name], 'test': [name]}


def pack_io(input: Any, output: Any) -> dict[str, Any]:
    data = target = logits = pred = loss = None
    if isinstance(input, dict):
        data = input.get('data')
        target = input.get('target', input.get('labels'))
    elif isinstance(input, (tuple, list)) and len(input) >= 2:
        data, target = input[0], input[1]
    if isinstance(output, dict):
        logits = output.get('logits')
        pred = output.get('pred')
        loss = output.get('loss')
    else:
        logits = output
    if pred is None:
        pred = logits
    return {'data': data, 'target': target, 'logits': logits, 'pred': pred, 'loss': loss}


def _as_item(value: Any) -> float:
    item = getattr(value, 'item', None)
    if callable(item):
        return float(item())
    return float(value)


def accuracy_value(packed: dict[str, Any], *, topk: int = 1) -> float:
    import torch

    target = packed.get('target')
    pred = packed.get('pred')
    if target is None or pred is None:
        return 0.0
    with torch.no_grad():
        if target.ndim > 1 and target.size(-1) > 1:
            target = target.topk(1, -1, True, True)[1].view(-1)
        if pred.ndim > 1 and pred.size(-1) > 1:
            pred = pred.topk(topk, -1, True, True)[1].reshape(-1, topk)
        else:
            pred = pred.reshape(-1, 1)
        target = target.reshape(-1, 1)
        n = int(target.numel())
        if pred.size(0) != n:
            raise ValueError('prediction and target sample counts differ')
        if n <= 0:
            return 0.0
        correct = (pred == target).any(dim=-1).float().sum()
        return float((correct * (100.0 / n)).item())


def loss_value(packed: dict[str, Any]) -> float:
    import torch
    import torch.nn.functional as F

    if packed.get('loss') is not None:
        return _as_item(packed['loss'])
    logits = packed.get('logits')
    target = packed.get('target')
    if logits is None or target is None:
        logits = packed.get('pred')
        if logits is None or target is None:
            return 0.0
    elif getattr(target, 'dtype', None) == torch.int64:
        return float(F.cross_entropy(logits, target).item())
    return float(F.mse_loss(logits, target).item())


def mse_value(packed: dict[str, Any]) -> float:
    import torch.nn.functional as F

    pred = packed.get('pred')
    target = packed.get('target')
    if pred is None or target is None:
        return 0.0
    return float(F.mse_loss(pred, target).item())


class _RMSE:
    def __init__(self) -> None:
        self.se = 0.0
        self.count = 0

    def add(self, packed: dict[str, Any]) -> None:
        import torch.nn.functional as F

        pred = packed.get('pred')
        target = packed.get('target')
        if pred is None or target is None:
            return
        self.se += float(F.mse_loss(pred, target, reduction='sum').item())
        self.count += int(pred.numel())

    def compute(self) -> float:
        if self.count <= 0:
            return 0.0
        value = (self.se / self.count) ** 0.5
        self.se = 0.0
        self.count = 0
        return float(value)


class _GLUE:
    def __init__(self, subset: str) -> None:
        self.subset = subset
        self._metric = None
        self._ready = False

    def _load(self) -> Any:
        if self._ready:
            return self._metric
        import evaluate

        self._metric = evaluate.load('glue', self.subset)
        self._ready = True
        return self._metric

    def add(self, packed: dict[str, Any]) -> None:
        pred = packed.get('pred')
        target = packed.get('target')
        if pred is None or target is None:
            return
        metric = self._load()
        if self.subset == 'stsb':
            predictions = pred.detach().cpu()
        else:
            predictions = pred.argmax(dim=-1).detach().cpu() if pred.ndim > 1 else pred.detach().cpu()
        metric.add_batch(predictions=predictions, references=target.detach().cpu())

    def compute(self) -> float:
        metric = self._load()
        body = metric.compute()
        key = next(iter(body))
        return float(body[key])


class MetricBundle:
    """Per-split batch metrics plus full-mode accumulators (RMSE / GLUE)."""

    def __init__(
        self,
        names: dict[str, list[str]] | None = None,
        *,
        glue_subset: str = 'cola',
    ) -> None:
        self.names = names or {k: list(v) for k, v in DEFAULT_NAMES.items()}
        self.glue_subset = glue_subset
        self._full: dict[tuple[str, str], Any] = {}

    def names_for(self, split: str) -> list[str]:
        if split in self.names:
            return list(self.names[split])
        if 'test' in self.names and split != 'train':
            return list(self.names['test'])
        return list(self.names.get('train') or DEFAULT_NAMES['train'])

    def _full_metric(self, split: str, name: str) -> Any:
        key = (split, name)
        if key not in self._full:
            if name == 'RMSE':
                self._full[key] = _RMSE()
            elif name == 'GLUE':
                self._full[key] = _GLUE(self.glue_subset)
        return self._full[key]

    def evaluate(self, split: str, mode: str, input: Any, output: Any) -> dict[str, float]:
        packed = pack_io(input, output)
        out: dict[str, float] = {}
        for name in self.names_for(split):
            if name in FULL_NAMES:
                if mode == 'batch':
                    self._full_metric(split, name).add(packed)
                continue
            if mode != 'batch':
                continue
            if name == 'Accuracy':
                out[name] = accuracy_value(packed)
            elif name == 'MSE':
                out[name] = mse_value(packed)
            else:
                out[name] = loss_value(packed)
        return out

    def finish_full(self, split: str) -> dict[str, float]:
        return {name: float(holder.compute()) for name in self.names_for(split)
                if name in FULL_NAMES and (holder := self._full.get((split, name))) is not None}
