"""One complete, source-versioned processed cache shared by historical consumers."""
import hashlib

from rpipe.structure.artifacts import cache_lock, prepared_resource, reference_tree
from ..api import COMMIT, _working_directory


def datasets(archive, raw_root, name):
    import numpy as np
    import torch

    with cache_lock(raw_root):
        raw = []
        for path in sorted((raw_root / name / 'raw').rglob('*')):
            if path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b''):
                        digest.update(block)
                raw.append({'path': path.relative_to(raw_root).as_posix(), 'sha256': digest.hexdigest()})
        if not raw:
            raise FileNotFoundError(f'prepare complete raw inputs before use: {raw_root / name / "raw"}')
        definition = {'schema': 1, 'main': COMMIT, 'dataset': name, 'raw': raw,
                      'torch': torch.__version__, 'numpy': np.__version__}

        def build(staging):
            reference_tree(raw_root / name / 'raw', staging / 'data' / name / 'raw')
            with _working_directory(staging):
                archive.dataset.make_dataset(name, verbose=False)

        files = [f'data/{name}/processed/{filename}' for filename in ('train', 'test', 'meta')]
        cache, digest = prepared_resource(raw_root / 'derived' / f'main-{COMMIT[:7]}', definition, files, build)
        with _working_directory(cache):
            return archive.dataset.make_dataset(name, verbose=False)
