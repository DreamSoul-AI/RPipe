"""Endpoint binding owns process-global dataset class state for its context."""

import threading

import pytest

from rpipe.structure.system.origin import bind_endpoint, domestic_endpoint, foreign_endpoint

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_system,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('name,field', [('CIFAR10', 'url'), ('MNIST', 'mirrors')])
@pytest.mark.parametrize('body_failure', [False, True], ids=['success', 'body-error'])
def test_concurrent_endpoint_bindings_keep_own_origin_and_restore(name, field, body_failure):
    class Dataset:
        url = 'original-url'
        mirrors = ['original-mirror']

    original = getattr(Dataset, field)
    endpoints = [domestic_endpoint(name), foreign_endpoint(name)]
    expected = [endpoint.url if field == 'url' else list(endpoint.mirrors) for endpoint in endpoints]
    first_in, second_attempt, second_in, first_out = [threading.Event() for _ in range(4)]
    observed, errors = {}, []
    primary = RuntimeError('constructor refused')

    def worker(index):
        try:
            if index:
                assert first_in.wait(3)
                second_attempt.set()
            try:
                with bind_endpoint(Dataset, endpoints[index]):
                    if index == 0:
                        first_in.set()
                        assert second_attempt.wait(3)
                        # Give the competing context a chance to enter; a guarded binding waits.
                        second_in.wait(0.2)
                    else:
                        second_in.set()
                        assert first_out.wait(3)
                    value = getattr(Dataset, field)
                    observed[index] = list(value) if isinstance(value, list) else value
                    if body_failure:
                        raise primary
            except RuntimeError as exc:
                assert body_failure and exc is primary
        except BaseException as exc:
            errors.append(exc)
        finally:
            if index == 0:
                first_out.set()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert observed == dict(enumerate(expected))
    assert getattr(Dataset, field) == original


@pytest.mark.parametrize('name,field', [('CIFAR10', 'url'), ('MNIST', 'mirrors')])
@pytest.mark.parametrize('body_failure', [False, True], ids=['success', 'body-error'])
def test_nested_endpoint_bindings_restore_each_context(name, field, body_failure):
    class Dataset:
        url = 'original-url'
        mirrors = ['original-mirror']

    original = getattr(Dataset, field)
    outer, inner = domestic_endpoint(name), foreign_endpoint(name)
    expected = lambda endpoint: endpoint.url if field == 'url' else list(endpoint.mirrors)
    with bind_endpoint(Dataset, outer):
        assert getattr(Dataset, field) == expected(outer)
        try:
            with bind_endpoint(Dataset, inner):
                assert getattr(Dataset, field) == expected(inner)
                if body_failure:
                    raise RuntimeError('constructor refused')
        except RuntimeError:
            assert body_failure
        assert getattr(Dataset, field) == expected(outer)
    assert getattr(Dataset, field) == original
