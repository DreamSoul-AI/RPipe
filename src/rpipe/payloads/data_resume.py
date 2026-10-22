"""Replay sampler indices, never consumed dataset transforms, on CPU-loader resume."""

from itertools import islice


class BatchCursor:
    def __init__(self, batches):
        self.batches = batches
        self.consumed = 0
        self.exhausted = False

    def __len__(self):
        return len(self.batches)

    def __iter__(self):
        start = self.consumed
        for batch in islice(iter(self.batches), start, None):
            self.consumed += 1
            yield batch
        self.exhausted = True


def loader_spec(loader):
    from torch.utils.data import BatchSampler, DataLoader, RandomSampler, SequentialSampler

    if not isinstance(loader, DataLoader) or loader.num_workers != 0:
        return None
    batches = loader.batch_sampler
    if isinstance(batches, BatchCursor):
        batches = batches.batches
    if type(batches) is not BatchSampler or type(batches.sampler) not in (RandomSampler, SequentialSampler):
        return None
    sampler = batches.sampler
    if isinstance(sampler, RandomSampler) and sampler.generator is None:
        return None
    return {'dataset_size': len(loader.dataset), 'batch_size': batches.batch_size,
            'drop_last': batches.drop_last, 'sampler': type(sampler).__name__,
            'num_samples': len(sampler), 'replacement': getattr(sampler, 'replacement', False)}


def generators(loader):
    sampler = loader.batch_sampler
    if isinstance(sampler, BatchCursor):
        sampler = sampler.batches
    return {'sampler': getattr(sampler.sampler, 'generator', None), 'loader': loader.generator}


def generator_states(loader):
    return {key: value.get_state().clone() if value is not None else None
            for key, value in generators(loader).items()}


def restore_generators(loader, states):
    for key, generator in generators(loader).items():
        state = states[key]
        if (generator is None) != (state is None):
            raise ValueError('checkpoint loader generator does not match current data')
        if generator is not None:
            generator.set_state(state.cpu())


def cursor_loader(loader):
    from torch.utils.data import DataLoader

    cursor = BatchCursor(loader.batch_sampler)
    return DataLoader(loader.dataset, batch_sampler=cursor, collate_fn=loader.collate_fn,
                      num_workers=0, pin_memory=loader.pin_memory, generator=loader.generator)
