"""Prepare the unchanged MNIST/CIFAR10 raw files used by historical main.

Run with the same Python executable as the study, for example::

    python -B studies/main_exp/flow/prepare_data.py
    python -B studies/main_exp/flow/prepare_data.py --verify-only

Registered domestic mirrors serve the unchanged archives from pinned main 4ccb28d.
Foreign redirects are refused; unavailable mirrors require verified local raw files.
The legacy datasets create their own pickle-format ``processed`` caches later.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import shutil
import struct
import tarfile
import urllib.request
from urllib.parse import urlsplit

from rpipe.structure.system.origin import domestic_endpoint


STUDY = Path(__file__).resolve().parents[1]
HISTORICAL_REF = '4ccb28d0496110253e9f8e3f3df658853f07996b'
MNIST_URL = domestic_endpoint('MNIST').mirrors[0]
MNIST_RESOURCES = (
    ('train-images-idx3-ubyte.gz', 'f68b3c2dcbeaaa9fbdd348bbdeb94873'),
    ('train-labels-idx1-ubyte.gz', 'd53e105ee54ea40749a09fcbcd1e9432'),
    ('t10k-images-idx3-ubyte.gz', '9fb629c4189551a2d022fa330f9573f3'),
    ('t10k-labels-idx1-ubyte.gz', 'ec29112dd5afa0611ce80d1b7f02629c'),
)
CIFAR_URL = domestic_endpoint('CIFAR10').url
CIFAR_MD5 = 'c58f30108f718f92721af3b95e74349a'


def digest(path: Path, algorithm: str = 'sha256') -> str:
    hasher = hashlib.new(algorithm)
    with _owned_stream(path.open('rb')) as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def expected_files(manifest: Path) -> dict[str, str]:
    source = json.loads(manifest.read_text(encoding='utf-8'))
    result = {}
    for relative, expected in source.items():
        relative = relative.replace('\\', '/')
        if not relative.startswith('data/'):
            raise ValueError(f'unexpected historical manifest path: {relative}')
        result[relative.removeprefix('data/')] = expected
    return result


def verify_file(path: Path, expected: str, algorithm: str = 'sha256') -> bool:
    return path.is_file() and digest(path, algorithm) == expected


def _require_domestic_url(url: str) -> None:
    target = urlsplit(url)
    hosts = {urlsplit(source).hostname for source in (MNIST_URL, CIFAR_URL)}
    if (target.scheme != 'https' or target.hostname not in hosts
            or target.port not in (None, 443) or target.username is not None or target.password is not None):
        raise ValueError('domestic raw mirror required; no overseas fallback; prepare verified raw files locally if unavailable')


class _DomesticRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _require_domestic_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


@contextlib.contextmanager
def _owned_stream(stream):
    """Preserve a body failure when an owned stream exits with an I/O error."""
    with contextlib.ExitStack() as stack:
        value = stream.__enter__()
        def finish(exc_type, exc, tb):
            try:
                return stream.__exit__(exc_type, exc, tb)
            except OSError:
                if exc_type is None:
                    raise
        stack.push(finish)
        yield value


def download(url: str, destination: Path, md5: str) -> dict[str, str]:
    if verify_file(destination, md5, 'md5'):
        print(f'cached archive: {destination}', flush=True)
        return {'url': url, 'source': 'existing archive', 'md5': md5}
    if destination.exists():
        raise ValueError(f'existing archive checksum mismatch: {destination}')
    _require_domestic_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    print(f'download: {url}', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'RPipe historical data preparation'})
    opener = urllib.request.build_opener(_DomesticRedirect())
    with _owned_stream(opener.open(request, timeout=60)) as response, _owned_stream(partial.open('wb')) as handle:
        actual_url = response.geturl()
        expected_size = response.headers.get('Content-Length')
        total = 0
        next_report = 32 * 1024 * 1024
        while block := response.read(1024 * 1024):
            handle.write(block)
            total += len(block)
            if total >= next_report:
                print(f'downloaded {total / 1024 / 1024:.0f} MiB: {destination.name}', flush=True)
                next_report += 32 * 1024 * 1024
    if expected_size is not None and total != int(expected_size):
        raise ValueError(f'incomplete download: {destination}, got {total} of {expected_size} bytes')
    if digest(partial, 'md5') != md5:
        raise ValueError(f'download checksum mismatch; retained partial file: {partial}')
    partial.replace(destination)
    return {'url': url, 'actual_url': actual_url, 'source': 'download', 'md5': md5}


def reuse_caches(data_root: Path, expected: dict[str, str], cache_roots: list[Path]) -> dict[str, str]:
    from rpipe.structure.artifacts import link_input
    copied = {}
    for relative, expected_hash in expected.items():
        destination = data_root / relative
        if destination.is_file():
            if not verify_file(destination, expected_hash):
                raise ValueError(f'existing raw file checksum mismatch: {destination}')
            continue
        for cache_root in cache_roots:
            match = next((path for path in cache_root.rglob(destination.name)
                          if path.is_file() and path.resolve() != destination.resolve()
                          and verify_file(path, expected_hash)), None)
            if match is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                link_input(match, destination)
                copied[relative] = str(match.resolve())
                print(f'reused raw cache: {match}', flush=True)
                break
    return copied


def prepare_mnist(data_root: Path, expected: dict[str, str]) -> list[dict[str, str]]:
    folder = data_root / 'MNIST' / 'raw'
    folder.mkdir(parents=True, exist_ok=True)
    downloads = []
    for name, md5 in MNIST_RESOURCES:
        archive = folder / name
        downloads.append(download(MNIST_URL + name, archive, md5))
        extracted = archive.with_suffix('')
        relative = extracted.relative_to(data_root).as_posix()
        if verify_file(extracted, expected[relative]):
            continue
        if extracted.exists():
            raise ValueError(f'existing IDX checksum mismatch: {extracted}')
        partial = extracted.with_name(extracted.name + '.part')
        with _owned_stream(gzip.open(archive, 'rb')) as source, _owned_stream(partial.open('wb')) as destination:
            shutil.copyfileobj(source, destination)
        if not verify_file(partial, expected[relative]):
            raise ValueError(f'extracted IDX checksum mismatch: {partial}')
        partial.replace(extracted)
    return downloads


def prepare_cifar(data_root: Path, expected: dict[str, str]) -> list[dict[str, str]]:
    required = {relative: value for relative, value in expected.items() if relative.startswith('CIFAR10/')}
    if all(verify_file(data_root / relative, value) for relative, value in required.items()):
        return [{'url': CIFAR_URL, 'source': 'verified raw cache', 'md5': CIFAR_MD5}]
    folder = data_root / 'CIFAR10' / 'raw'
    archive = folder / 'cifar-10-python.tar.gz'
    record = download(CIFAR_URL, archive, CIFAR_MD5)
    with _owned_stream(tarfile.open(archive, 'r:gz')) as handle:
        for relative, expected_hash in required.items():
            destination = data_root / relative
            if verify_file(destination, expected_hash):
                continue
            if destination.exists():
                raise ValueError(f'existing CIFAR raw checksum mismatch: {destination}')
            member_name = destination.relative_to(folder).as_posix()
            member = handle.getmember(member_name)
            if not member.isfile():
                raise ValueError(f'expected a regular dataset member: {member_name}')
            source = handle.extractfile(member)
            if source is None:
                raise ValueError(f'cannot read dataset member: {member_name}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + '.part')
            with _owned_stream(source), _owned_stream(partial.open('wb')) as target:
                shutil.copyfileobj(source, target)
            if not verify_file(partial, expected_hash):
                raise ValueError(f'extracted CIFAR checksum mismatch: {partial}')
            partial.replace(destination)
    return [record]


def split_counts(data_root: Path) -> dict[str, dict[str, int]]:
    counts = {}
    mnist = data_root / 'MNIST' / 'raw'
    for split, prefix, expected in [('train', 'train', 60000), ('test', 't10k', 10000)]:
        images = mnist / f'{prefix}-images-idx3-ubyte'
        labels = mnist / f'{prefix}-labels-idx1-ubyte'
        with _owned_stream(images.open('rb')) as handle:
            magic, n, rows, cols = struct.unpack('>IIII', handle.read(16))
        with _owned_stream(labels.open('rb')) as handle:
            label_magic, label_n = struct.unpack('>II', handle.read(8))
        if (magic, n, rows, cols, label_magic, label_n) != (2051, expected, 28, 28, 2049, expected):
            raise ValueError(f'invalid MNIST {split} IDX header')
        if images.stat().st_size != 16 + n * rows * cols or labels.stat().st_size != 8 + label_n:
            raise ValueError(f'invalid MNIST {split} IDX length')
    counts['MNIST'] = {'train': 60000, 'test': 10000, 'classes': 10}
    cifar = data_root / 'CIFAR10' / 'raw' / 'cifar-10-batches-py'
    for name in [*(f'data_batch_{i}' for i in range(1, 6)), 'test_batch']:
        with _owned_stream((cifar / name).open('rb')) as handle:
            batch = pickle.load(handle, encoding='latin1')
        if batch['data'].shape != (10000, 3072) or len(batch['labels']) != 10000:
            raise ValueError(f'invalid CIFAR10 batch dimensions: {name}')
        if min(batch['labels']) < 0 or max(batch['labels']) > 9:
            raise ValueError(f'invalid CIFAR10 labels: {name}')
    with _owned_stream((cifar / 'batches.meta').open('rb')) as handle:
        if len(pickle.load(handle, encoding='latin1')['label_names']) != 10:
            raise ValueError('invalid CIFAR10 metadata')
    counts['CIFAR10'] = {'train': 50000, 'test': 10000, 'classes': 10}
    return counts


def main(argv: list[str] | None = None) -> int:
    from rpipe.structure.artifacts import atomic_write_text

    from rpipe.structure.artifacts import cache_lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--expected-manifest', type=Path,
                        default=STUDY / 'docs' / 'EXPECTED_DATA.json')
    parser.add_argument('--cache-root', type=Path, action='append', default=[])
    parser.add_argument('--verify-only', action='store_true', help='validate complete raw files without downloads')
    parser.add_argument('--download', action='store_true', help='explicitly allow missing raw files to be downloaded')
    args = parser.parse_args(argv)
    data_root = args.data_root.resolve()
    checkout = STUDY.parents[1]
    if any(path.is_relative_to(checkout) or checkout.is_relative_to(path)
           for path in (data_root, args.manifest.resolve())):
        parser.error('data and manifest output roots must be separate from the source checkout')
    expected = expected_files(args.expected_manifest)
    reused, downloads = {}, []
    if args.download and not args.verify_only:
        with cache_lock(data_root):
            reused = reuse_caches(data_root, expected, args.cache_root)
            downloads = prepare_mnist(data_root, expected) + prepare_cifar(data_root, expected)
    elif args.manifest.is_file():
        previous = json.loads(args.manifest.read_text(encoding='utf-8'))
        downloads = previous.get('downloads', [])
        reused = previous.get('reused_caches', {})
    files = []
    for relative, expected_hash in sorted(expected.items()):
        path = data_root / relative
        if not verify_file(path, expected_hash):
            raise ValueError(f'raw dataset differs from historical manifest: {path}')
        files.append({'path': relative, 'bytes': path.stat().st_size,
                      'sha256': expected_hash, 'historical_sha256_match': True})
    try:
        expected_path = args.expected_manifest.resolve().relative_to(STUDY.parent.parent).as_posix()
    except ValueError:
        expected_path = str(args.expected_manifest.resolve())
    manifest = {
        'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
        'historical_ref': HISTORICAL_REF,
        'layout': 'shared/data/{MNIST,CIFAR10}/raw; legacy datasets create processed caches',
        'upstream_urls': [MNIST_URL, CIFAR_URL],
        'expected_manifest': expected_path,
        'expected_manifest_sha256': digest(args.expected_manifest),
        'preparation_script_sha256': digest(Path(__file__)),
        'raw_files': len(files),
        'historical_sha256_matches': len(files),
        'split_counts': split_counts(data_root),
        'downloads': downloads,
        'reused_caches': reused,
        'files': files,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.manifest, json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'verified {len(files)}/{len(expected)} historical raw files; manifest: {args.manifest}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
