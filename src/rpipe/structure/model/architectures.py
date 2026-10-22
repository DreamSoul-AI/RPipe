"""custom_torch models from git main: linear, mlp, cnn, resnet10/18, wresnet."""

from __future__ import annotations

import math

import torch.nn as nn
import torch.nn.functional as F


class Linear(nn.Module):
    def __init__(self, data_size: tuple[int, ...], target_size: int) -> None:
        super().__init__()
        self.output_proj = nn.Linear(math.prod(data_size), target_size)

    def forward(self, x):  # noqa: ANN001
        x = x.reshape(x.size(0), -1)
        return self.output_proj(x)


class MLP(nn.Module):
    def __init__(
        self,
        data_size: tuple[int, ...],
        hidden_size: int,
        scale_factor: float,
        num_layers: int,
        activation: str,
        target_size: int,
    ) -> None:
        super().__init__()
        input_size = math.prod(data_size)
        blocks: list[nn.Module] = []
        width = int(hidden_size)
        for _ in range(int(num_layers)):
            blocks.append(nn.Linear(input_size, width))
            if activation == 'relu':
                blocks.append(nn.ReLU())
            elif activation == 'sigmoid':
                blocks.append(nn.Sigmoid())
            else:
                raise ValueError(f'unknown mlp activation: {activation}')
            input_size = width
            width = int(width * scale_factor)
        self.blocks = nn.Sequential(*blocks)
        self.output_proj = nn.Linear(input_size, target_size)

    def forward(self, x):  # noqa: ANN001
        x = x.reshape(x.size(0), -1)
        return self.output_proj(self.blocks(x))


class CNN(nn.Module):
    def __init__(self, data_size: tuple[int, ...], hidden_size: list[int], target_size: int) -> None:
        super().__init__()
        blocks: list[nn.Module] = [
            nn.Conv2d(data_size[0], hidden_size[0], 3, 1, 1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        ]
        for i in range(len(hidden_size) - 1):
            blocks.extend(
                [
                    nn.Conv2d(hidden_size[i], hidden_size[i + 1], 3, 1, 1),
                    nn.ReLU(inplace=True),
                    nn.MaxPool2d(2),
                ]
            )
        blocks = blocks[:-1]
        blocks.extend([nn.AdaptiveAvgPool2d(1), nn.Flatten()])
        self.blocks = nn.Sequential(*blocks)
        self.output_proj = nn.Linear(hidden_size[-1], target_size)

    def forward(self, x):  # noqa: ANN001
        return self.output_proj(self.blocks(x))


class _ResBlock(nn.Module):
    expansion = 1

    def __init__(self, in_planes: int, planes: int, stride: int) -> None:
        super().__init__()
        self.n1 = nn.BatchNorm2d(in_planes)
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.n2 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        if stride != 1 or in_planes != self.expansion * planes:
            self.shortcut = nn.Conv2d(
                in_planes, self.expansion * planes, kernel_size=1, stride=stride, bias=False
            )

    def forward(self, x):  # noqa: ANN001
        out = F.relu(self.n1(x))
        shortcut = self.shortcut(out) if hasattr(self, 'shortcut') else x
        out = self.conv1(out)
        out = self.conv2(F.relu(self.n2(out)))
        out += shortcut
        return out


class ResNet(nn.Module):
    def __init__(
        self,
        data_size: tuple[int, ...],
        hidden_size: list[int],
        num_blocks: list[int],
        target_size: int,
        block: type[_ResBlock] = _ResBlock,
    ) -> None:
        super().__init__()
        self.in_planes = hidden_size[0]
        self.conv1 = nn.Conv2d(data_size[0], hidden_size[0], kernel_size=3, stride=1, padding=1, bias=False)
        self.layer1 = self._make_layer(block, hidden_size[0], num_blocks[0], stride=1)
        self.layer2 = self._make_layer(block, hidden_size[1], num_blocks[1], stride=2)
        self.layer3 = self._make_layer(block, hidden_size[2], num_blocks[2], stride=2)
        self.layer4 = self._make_layer(block, hidden_size[3], num_blocks[3], stride=2)
        self.n4 = nn.BatchNorm2d(hidden_size[3] * block.expansion)
        self.output_proj = nn.Linear(hidden_size[3] * block.expansion, target_size)

    def _make_layer(self, block: type[_ResBlock], planes: int, num_blocks: int, stride: int) -> nn.Sequential:
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for layer_stride in strides:
            layers.append(block(self.in_planes, planes, layer_stride))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):  # noqa: ANN001
        x = self.conv1(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = F.relu(self.n4(x))
        x = F.adaptive_avg_pool2d(x, 1)
        x = x.view(x.size(0), -1)
        return self.output_proj(x)


class _WideBlock(nn.Module):
    def __init__(self, in_planes: int, out_planes: int, stride: int, drop_rate: float) -> None:
        super().__init__()
        self.n1 = nn.BatchNorm2d(in_planes)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv1 = nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.n2 = nn.BatchNorm2d(out_planes)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(out_planes, out_planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.drop_rate = drop_rate
        self.equal_inout = in_planes == out_planes
        self.shortcut = None
        if not self.equal_inout:
            self.shortcut = nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, padding=0, bias=False)

    def forward(self, x):  # noqa: ANN001
        if not self.equal_inout:
            x = self.relu1(self.n1(x))
            out = x
        else:
            out = self.relu1(self.n1(x))
        out = self.relu2(self.n2(self.conv1(out if self.equal_inout else x)))
        if self.drop_rate > 0:
            out = F.dropout(out, p=self.drop_rate, training=self.training)
        out = self.conv2(out)
        skip = x if self.equal_inout else self.shortcut(x)
        return skip + out


class _WideNetwork(nn.Module):
    def __init__(
        self,
        nb_layers: int,
        in_planes: int,
        out_planes: int,
        stride: int,
        drop_rate: float,
    ) -> None:
        super().__init__()
        layers = []
        for i in range(int(nb_layers)):
            layers.append(
                _WideBlock(
                    in_planes if i == 0 else out_planes,
                    out_planes,
                    stride if i == 0 else 1,
                    drop_rate,
                )
            )
        self.layer = nn.Sequential(*layers)

    def forward(self, x):  # noqa: ANN001
        return self.layer(x)


class WideResNet(nn.Module):
    """Wide ResNet from git main ``src/model/wresnet.py``."""

    def __init__(
        self,
        data_size: tuple[int, ...],
        target_size: int,
        depth: int,
        widen_factor: int,
        drop_rate: float,
    ) -> None:
        super().__init__()
        num_down = int(min(round(math.log2(data_size[1])), round(math.log2(data_size[2])))) - 3
        hidden_size = [16]
        for i in range(num_down + 1):
            hidden_size.append(16 * (2**i) * int(widen_factor))
        n = ((int(depth) - 1) / (num_down + 1) - 1) / 2
        blocks: list[nn.Module] = [
            nn.Conv2d(data_size[0], hidden_size[0], kernel_size=3, stride=1, padding=1, bias=False),
            _WideNetwork(n, hidden_size[0], hidden_size[1], 1, drop_rate),
        ]
        for i in range(num_down):
            blocks.append(
                _WideNetwork(n, hidden_size[i + 1], hidden_size[i + 2], 2, drop_rate)
            )
        blocks.extend(
            [
                nn.BatchNorm2d(hidden_size[-1]),
                nn.ReLU(inplace=True),
                nn.AdaptiveAvgPool2d(1),
                nn.Flatten(),
            ]
        )
        self.blocks = nn.Sequential(*blocks)
        self.output_proj = nn.Linear(hidden_size[-1], target_size)

    def forward(self, x):  # noqa: ANN001
        return self.output_proj(self.blocks(x))
