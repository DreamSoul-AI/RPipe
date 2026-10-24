"""Shared raw / derived inputs, original Stats and CPU initialization parity."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
from typing import Any
from .inputs import prepared_inputs
from ...structure.algorithm.paired import (
    ROOT, MAIN, MainArchive, bootstrap, source_manifest, sha, native_data,
    native_model, seed_runtime, rng, model_state, state_compare, rng_equal, save_json, owned,
)

EXPECTED_DATA = ROOT / 'studies' / 'main_exp' / 'docs' / 'EXPECTED_DATA.json'


def verify_runtime() -> str:
    """Require the loaded library and its package search paths to use this checkout."""
    import rpipe

    library = (ROOT / 'src' / 'rpipe').resolve()
    for name, module in tuple(sys.modules.items()):
        if (name != 'rpipe' and not name.startswith('rpipe.')) or module is None:
            continue
        filename = getattr(module, '__file__', None)
        paths = [Path(filename).resolve()] if filename else []
        paths.extend(Path(path).resolve() for path in getattr(module, '__path__', ()))
        if not filename or any(not path.is_relative_to(library) for path in paths):
            raise ValueError(f'main_probe requires RPipe from {library}; loaded {name} from {filename}')
    return str(Path(rpipe.__file__).resolve())


def verify_raw(source_data: Path, name: str) -> list[dict[str, str]]:
    """Read only: verify each fixed raw input used by the selected dataset."""
    table = json.loads(EXPECTED_DATA.read_text(encoding='utf-8'))
    prefix = f'data/{name}/raw/'
    expected = {path.replace('\\', '/').removeprefix(prefix): digest
                for path, digest in table.items() if path.replace('\\', '/').startswith(prefix)}
    if not expected:
        raise ValueError(f'no fixed raw hashes for {name}')
    raw = source_data / name / 'raw'
    rows = []
    for relative, digest in sorted(expected.items()):
        path = raw / relative
        if not path.is_file():
            raise FileNotFoundError(f'missing fixed raw input: {name}/{relative}')
        hasher = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                hasher.update(block)
        if hasher.hexdigest() != digest:
            raise ValueError(f'fixed raw SHA-256 differs: {name}/{relative}')
        rows.append({'dataset': name, 'relative': relative, 'sha256': digest})
    return rows


def prepare(workspace: Path, source_data: Path, pair: tuple[str, str]) -> dict[str, Any]:
    """Reference immutable raw / processed inputs and check CPU parity."""
    runtime_source = verify_runtime()
    source_before = source_manifest()
    copied = verify_raw(source_data, pair[0])
    from rpipe.structure.artifacts import link_input, reference_tree
    bootstrap()
    import numpy as np
    import torch
    head = subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / 'original').mkdir()
    archive = MainArchive(workspace)
    for name in (pair[0],):
        source = source_data / name / 'raw'
        original_raw = workspace / 'original' / 'data' / name / 'raw'
        native_raw = workspace / 'native-data' / name.lower() / ('MNIST' if name == 'MNIST' else '')
        native_raw = native_raw / 'raw' if name == 'MNIST' else native_raw
        for row in copied:
            relative = Path(row['relative'])
            path = source / relative
            destinations = (original_raw / relative, native_raw / relative)
            expected = row['sha256']
            for destination in destinations:
                destination.parent.mkdir(parents=True, exist_ok=True)
                link_input(path, destination)
                if sha(destination) != expected:
                    raise ValueError(f'copied raw file differs: {destination}')
    prepared = {'prepared_at_utc': datetime.now(timezone.utc).isoformat(), 'main_commit': MAIN,
                'dev_commit': head, 'device': 'cpu', 'raw_files': copied, 'data': [], 'models': [],
                'rpipe_source': runtime_source, 'expected_data_sha256': sha(EXPECTED_DATA)}
    for name in (pair[0],):
        archive.configure(name, 'linear')
        cache, cache_digest = prepared_inputs(archive, source_data, name, copied)
        reference_tree(cache / 'data' / name / 'processed',
                       workspace / 'original' / 'data' / name / 'processed')
        prepared.setdefault('shared_derived_inputs', []).append(
            {'dataset': name, 'path': str(cache), 'manifest_sha256': cache_digest})
        original = archive.datasets(name)
        with archive.aliases():
            stats = archive.module.load(str(cache / 'stats' / name), 'torch')
        stats_path = workspace / 'original' / 'output' / 'stats' / name
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        link_input(cache / 'stats' / name, stats_path)
        stats_yaml = workspace / 'native-data' / name.lower() / 'stats.yaml'
        link_input(cache / 'stats' / 'stats.yaml', stats_yaml)
        with owned(native_data(workspace, name, workspace / 'prepare-assets')) as current:
            parity = []
            expected_sizes = (60000, 10000) if name == 'MNIST' else (50000, 10000)
            for split in ('train', 'test'):
                dataset = current._train_set if split == 'train' else current._loaders['test'].dataset
                old_data, new_data = original[split].data, dataset.data
                if hasattr(new_data, 'numpy'):
                    new_data = new_data.numpy()
                pixels = np.array_equal(old_data, new_data)
                labels = np.array_equal(original[split].target, np.asarray(dataset.targets))
                parity.append({'split': split, 'samples': len(original[split]), 'pixels_equal': pixels, 'labels_equal': labels})
                if not pixels or not labels:
                    raise ValueError(f'{name}/{split}: complete original/native data differs')
                if len(original[split]) != expected_sizes[0 if split == 'train' else 1] or len(dataset) != len(original[split]):
                    raise ValueError(f'{name}/{split}: sample count differs from complete official dataset')
            prepared['data'].append({'name': name, 'mean': stats.mean.tolist(), 'std': stats.std.tolist(),
                                     'stats_method': 'original Stats(dim=1), sequential complete train, batch250',
                                     'split_parity': parity})
            for model_name in (pair[1],):
                archive.configure(name, model_name)
                archive.dataset.process_dataset(original)
                seed_runtime('cpu')
                old_model = archive.model.make_model(archive.config.cfg['model'])
                old_rng = rng('cpu')
                old_state = model_state(old_model)
                seed_runtime('cpu')
                with owned(native_model(current, model_name, workspace / 'prepare-assets')) as new_model:
                    initial = state_compare(old_state, model_state(new_model.module))
                    initial_rng = rng_equal(old_rng, rng('cpu'))
                    size = tuple(current.meta['data_size'])
                    x = torch.linspace(0, 1, 2 * int(np.prod(size))).reshape(2, *size)
                    old_model.eval()
                    new_model.module.eval()
                    with torch.no_grad():
                        old_logits = old_model(data=x, target=torch.zeros(2, dtype=torch.int64))['pred']
                        new_logits = new_model.module(x)
                    difference = float((old_logits - new_logits).abs().max())
                    record = {'data': name, 'model': model_name, 'initial_state': initial,
                              'initial_cpu_rng_equal': initial_rng, 'fixed_eval_logits_maximum_difference': difference,
                              'passed': bool(initial['exact'] and initial_rng and difference <= 1e-6)}
                    prepared['models'].append(record)
                    print(f'CPU prepare {name}/{model_name}: {record["passed"]}', flush=True)
    source_after = source_manifest()
    prepared['source_unchanged'] = source_before == source_after
    prepared['archive_verification'] = archive.verify_exports()
    prepared['passed'] = bool(all(row['passed'] for row in prepared['models'])
                             and prepared['source_unchanged'] and prepared['archive_verification']['unchanged'])
    save_json(workspace / 'PREPARED.json', prepared)
    save_json(workspace / 'SOURCE_MANIFEST.json', {'files': source_before, 'after_files': source_after,
                                                'unchanged': prepared['source_unchanged']})
    save_json(workspace / 'ENVIRONMENT.json', {'python': sys.version, 'torch': torch.__version__,
              'numpy': np.__version__, 'cpu_threads': torch.get_num_threads(),
              'CUDA_build': torch.version.cuda, 'gpu_execution_started': False,
              'controls': {'deterministic': True, 'benchmark': False, 'CUBLAS_WORKSPACE_CONFIG': os.environ['CUBLAS_WORKSPACE_CONFIG']}})
    return prepared


