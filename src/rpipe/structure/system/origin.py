"""Study ``origin``: one switch for data hosts and model hubs.

``foreign`` uses the upstream hosts. ``domestic`` uses mirrors reachable from
mainland China. A Study sets this once; data and model downloads both read it.
Missing origin means ``foreign`` and does not change ``HF_ENDPOINT``.
"""

from __future__ import annotations

import hashlib
import os
import sys
from contextlib import contextmanager
from inspect import signature
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from urllib.parse import urlsplit

from .transfers import _domestic_transfers, _bind_dataset_media, _bind_dataset_transfers

FOREIGN = 'foreign'
DOMESTIC = 'domestic'
FOREIGN_MODEL_HUB = 'https://huggingface.co'
DOMESTIC_MODEL_HUB = 'https://hf-mirror.com'

_CIFAR_ARCHIVES = (
    ('CIFAR10', 'cifar-10-python.tar.gz', 'c58f30108f718f92721af3b95e74349a'),
    ('CIFAR100', 'cifar-100-python.tar.gz', 'eb9058c3a382ffc7106e4002c42a8d85'),
)
_BCEBOS = 'https://dataset.bj.bcebos.com'


@dataclass(frozen=True)
class DownloadEndpoint:
    name: str
    origin: str
    url: str | None = None
    mirrors: tuple[str, ...] = ()
    archive: str | None = None
    archive_md5: str | None = None

    @property
    def location(self) -> str:
        if self.url:
            return self.url
        if self.mirrors:
            return self.mirrors[0]
        return self.origin


def normalize_origin(value: Any) -> str:
    if value in (None, ''):
        return FOREIGN
    text = str(value).strip().lower()
    if text not in (FOREIGN, DOMESTIC):
        raise ValueError("origin must be 'foreign' or 'domestic'")
    return text


def model_hub(origin: Any) -> str:
    if normalize_origin(origin) == DOMESTIC:
        return DOMESTIC_MODEL_HUB
    return FOREIGN_MODEL_HUB


def apply_model_origin(origin: Any) -> str:
    """Point this process at the Study's model hub. Returns the hub URL."""
    hub = model_hub(origin)
    os.environ['HF_ENDPOINT'] = hub
    return hub


def foreign_endpoint(name: str) -> DownloadEndpoint:
    """Official torchvision hosts."""
    for dataset, archive, checksum in _CIFAR_ARCHIVES:
        if name == dataset:
            return DownloadEndpoint(name, FOREIGN, url=f'https://www.cs.toronto.edu/~kriz/{archive}',
                                    archive=archive, archive_md5=checksum)
    if name == 'MNIST':
        return DownloadEndpoint(
            name,
            FOREIGN,
            mirrors=(
                'https://ossci-datasets.s3.amazonaws.com/mnist/',
                'http://yann.lecun.com/exdb/mnist/',
            ),
        )
    if name in ('FashionMNIST', 'SVHN'):
        return DownloadEndpoint(name, FOREIGN)
    raise ValueError(f'no foreign download endpoint for {name}')


def domestic_endpoint(name: str) -> DownloadEndpoint:
    """Mainland data mirrors. Same archives and md5 as the foreign files."""
    for dataset, archive, checksum in _CIFAR_ARCHIVES:
        if name == dataset:
            return DownloadEndpoint(name, DOMESTIC, url=f'{_BCEBOS}/cifar/{archive}',
                                    archive=archive, archive_md5=checksum)
    if name == 'MNIST':
        return DownloadEndpoint(
            name,
            DOMESTIC,
            mirrors=(f'{_BCEBOS}/mnist/',),
        )
    raise ValueError(f'no domestic download endpoint for {name}')


def endpoint_for(name: str, origin: Any) -> DownloadEndpoint:
    chosen = normalize_origin(origin)
    if chosen == DOMESTIC:
        return domestic_endpoint(name)
    return foreign_endpoint(name)


def discard_bad_archive(root: Path, endpoint: DownloadEndpoint) -> None:
    """Drop a partial archive so the next download does not keep a bad file."""
    if not endpoint.archive or not endpoint.archive_md5:
        return
    path = Path(root) / endpoint.archive
    if not path.is_file():
        return
    if _md5(path) == endpoint.archive_md5:
        return
    path.unlink()


_endpoint_lock = RLock()


@contextmanager
def bind_endpoint(ctor: Any, endpoint: DownloadEndpoint) -> Iterator[DownloadEndpoint]:
    """Point one torchvision dataset class at this endpoint, then restore it."""
    with _endpoint_lock:
        saved_url = getattr(ctor, 'url', None)
        saved_mirrors = getattr(ctor, 'mirrors', None)
        if endpoint.url is not None and saved_url is not None:
            ctor.url = endpoint.url
        if endpoint.mirrors and saved_mirrors is not None:
            ctor.mirrors = list(endpoint.mirrors)
        try:
            yield endpoint
        finally:
            if endpoint.url is not None and saved_url is not None:
                ctor.url = saved_url
            if endpoint.mirrors and saved_mirrors is not None:
                ctor.mirrors = saved_mirrors


def _md5(path: Path) -> str:
    from rpipe.structure.artifacts import _owned_stream

    digest = hashlib.md5()
    with _owned_stream(path.open('rb')) as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def download_model(repo_id: str, cache_dir: Path | str, *, revision: str | None = None) -> str:
    """Resolve a local input or download via ModelScope, then hf-mirror only."""
    if Path(repo_id).exists():
        return str(repo_id)
    try:
        with _domestic_transfers('modelscope'):
            from modelscope import snapshot_download

            options = dict(model_id=repo_id, revision=revision, cache_dir=str(cache_dir))
            if 'endpoint' in signature(snapshot_download).parameters:
                options['endpoint'] = 'https://modelscope.cn'
            return snapshot_download(**options)
    except Exception as modelscope_error:
        # SDK failures permit a domestic fallback; cancellation still propagates.
        try:
            from huggingface_hub import snapshot_download

            with _domestic_transfers('mirror'):
                return snapshot_download(repo_id=repo_id, revision=revision,
                                         cache_dir=str(cache_dir), endpoint=DOMESTIC_MODEL_HUB)
        except Exception as mirror_error:
            raise RuntimeError(
                f'ModelScope failed ({modelscope_error}); hf-mirror failed ({mirror_error}); '
                'no overseas fallback is permitted'
            ) from mirror_error


def load_hf_dataset(*args: Any, **kwargs: Any) -> Any:
    """Load HF datasets after selecting the domestic endpoint before import."""
    _check_dataset_urls((args, kwargs.get('path'), kwargs.get('data_files'), kwargs.get('data_dir')))
    apply_model_origin(DOMESTIC)
    _check_hf_endpoint()
    from datasets import load_dataset

    _check_hf_endpoint()
    with _domestic_transfers('mirror'):
        result = load_dataset(*args, **kwargs)
    _bind_dataset_media(result)
    return _bind_dataset_transfers(result) if kwargs.get('streaming') else result


def _check_hf_endpoint() -> None:
    for name, attr in (('huggingface_hub.constants', 'ENDPOINT'),
                       ('datasets.config', 'HF_ENDPOINT')):
        module = sys.modules.get(name)
        if module is not None and getattr(module, attr, DOMESTIC_MODEL_HUB) != DOMESTIC_MODEL_HUB:
            raise RuntimeError(f'select domestic origin before importing {name}; start a fresh worker')


def _check_dataset_urls(value: Any) -> None:
    if isinstance(value, dict):
        value = tuple(value.values())
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_dataset_urls(item)
    elif isinstance(value, str) and '://' in value:
        url = urlsplit(value)
        if url.scheme != 'hf' and (url.scheme != 'https' or url.netloc != 'hf-mirror.com'):
            raise ValueError('HF dataset URLs must use domestic hf-mirror; prepare other inputs locally')
