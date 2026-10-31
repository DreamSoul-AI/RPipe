"""Reference-side observation checks cancellation before pulling or recording."""
import importlib.util
from pathlib import Path

import pytest

from rpipe.flow.context import Cancellation, Cancelled

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def observer():
    path = Path(__file__).resolve().parents[5] / 'studies/main_probe/structure/algorithm/paired.py'
    spec = importlib.util.spec_from_file_location('_probe_reference_observer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stream(observer):
    import torch

    token = Cancellation()
    primary = Cancelled('reference observation cancelled')
    batches = [{'data': torch.arange(4).reshape(2, 2) + number,
                'target': torch.tensor([0, 1]), 'id': torch.tensor([number * 2, number * 2 + 1])}
               for number in range(2)]
    class Source:
        iterations = requests = position = 0
        cancel_on_iter = cancel_on_fetch = False
        def __len__(self):
            return len(batches)
        def __iter__(self):
            self.iterations += 1
            if self.cancel_on_iter:
                token.request()
            return self
        def __next__(self):
            self.requests += 1
            if self.position == len(batches):
                raise StopIteration
            batch = batches[self.position]
            self.position += 1
            if self.cancel_on_fetch:
                token.request()
            return batch
    def check():
        if token.is_set():
            raise primary
    source = Source()
    observation = observer.Observations()
    wrapped = observer.observed_original(source, observation, cancel_check=check)
    return source, observation, wrapped, token, primary, batches


def test_no_cancel_preserves_batch_identity_input_hashes_and_rng(observer, stream):
    import torch

    source, observation, wrapped, _, _, batches = stream
    reference = observer.Observations()
    for batch in batches:
        reference.batch(batch['data'], batch['target'], batch['id'])
    rng = torch.get_rng_state().clone()
    assert len(wrapped) == len(batches)
    actual = list(wrapped)
    assert len(actual) == len(batches) and all(a is b for a, b in zip(actual, batches))
    assert source.iterations == 1 and source.requests == 3
    assert observation.result() == reference.result()
    assert torch.equal(torch.get_rng_state(), rng)


@pytest.mark.parametrize('when', ['already', 'during_iter', 'during_fetch', 'between_batches'])
def test_cancellation_prevents_new_requests_or_committing_an_inflight_batch(stream, when):
    source, observation, wrapped, token, primary, _ = stream
    if when == 'already':
        token.request()
    elif when == 'during_iter':
        source.cancel_on_iter = True
    elif when == 'during_fetch':
        source.cancel_on_fetch = True
    iterator = iter(wrapped)
    if when == 'between_batches':
        next(iterator)
        token.request()
    with pytest.raises(Cancelled) as caught:
        next(iterator)
    assert caught.value is primary
    assert source.iterations == (0 if when == 'already' else 1)
    assert source.requests == (1 if when in ('during_fetch', 'between_batches') else 0)
    assert observation.samples == (2 if when == 'between_batches' else 0)
    assert list(iterator) == []


@pytest.mark.parametrize('where', ['check', 'source'])
def test_primary_error_survives_without_recording_or_retrying(observer, where):
    error = OSError('first reference failure')
    def fail():
        raise error
    class Source:
        def __iter__(self):
            return self
        def __next__(self):
            return fail()
    observation = observer.Observations()
    iterator = iter(observer.observed_original(Source(), observation,
                    cancel_check=fail if where == 'check' else lambda: None))
    with pytest.raises(OSError) as caught:
        next(iterator)
    assert caught.value is error and observation.samples == 0
