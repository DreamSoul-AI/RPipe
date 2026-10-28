"""Share one processed / fixed-Stats input for each pinned definition."""
from rpipe.structure.artifact.io import cache_lock, prepared_resource, link_input
from ...structure.algorithm.paired import MAIN, cwd, sha


def prepared_inputs(archive, source_data, name, raw_files):
    import numpy as np
    import torch
    import yaml

    definition = {'schema': 1, 'main': MAIN, 'dataset': name, 'raw': raw_files,
                  'torch': torch.__version__, 'numpy': np.__version__,
                  'stats': {'dim': 1, 'batch': 250, 'threads': 2, 'device': 'cpu'}}
    def build(staging):
        from rpipe.structure.artifact.io import atomic_write_text

        for row in raw_files:
            link_input(source_data / name / 'raw' / row['relative'],
                       staging / 'data' / name / 'raw' / row['relative'])
        with archive.aliases(), cwd(staging):
            datasets = archive.dataset.make_dataset(name, verbose=False)
            loaders = archive.dataset.make_data_loader(datasets, {'train': 250, 'test': 1000}, shuffle=False)
            stats = archive.module.Stats(dim=1)
            with torch.no_grad():
                for batch in loaders['train']:
                    stats.update(batch['data'])
            archive.module.save(stats, str(staging / 'stats' / name), 'torch')
        atomic_write_text(staging / 'stats' / 'stats.yaml', yaml.safe_dump(
            {'mean': stats.mean.tolist(), 'std': stats.std.tolist()}, sort_keys=False), encoding='utf-8')

    files = [f'data/{name}/processed/{filename}' for filename in ('train', 'test', 'meta')]
    files += [f'stats/{name}', 'stats/stats.yaml']
    with cache_lock(source_data):
        for row in raw_files:
            if sha(source_data / name / 'raw' / row['relative']) != row['sha256']:
                raise ValueError('raw inputs changed during prepare; use a new Run')
        return prepared_resource(source_data / 'derived' / f'main-{MAIN[:7]}', definition, files, build)
