"""The payload boundary cannot publish nonserializable observations as success."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('observation', [object(), {'value': float('nan')}, {'value': object()}],
                         ids=['not-mapping', 'nonfinite-measurement', 'not-serializable'])
def test_invalid_collected_observation_fails_without_success_result(
        source_study, run_root, payload_factory, observation):
    from rpipe.api import plan, run

    class InvalidObservation(payload_factory):
        def collect(self, ctx):
            self._phase('collect')
            return observation

    spec = plan(source_study, run_root=run_root)[0]
    payload = InvalidObservation()
    outcome = run(spec, payload=payload)
    assert outcome.status == 'failed'
    assert outcome.quality is None
    assert outcome.evidence_complete is False
    assert not spec.result_path.exists()
    assert payload.calls == ['prepare', 'execute', 'collect', 'close']


@pytest.mark.parametrize('quality,complete', [(False, True), (None, False)])
def test_scientific_quality_and_evidence_are_preserved_separately(
        source_study, run_root, payload_factory, quality, complete):
    from rpipe.api import plan, read_run, run

    class ScientificFacts(payload_factory):
        def collect(self, ctx):
            self._phase('collect')
            return {'metrics': {'value': 3.0}, 'quality': quality, 'evidence_complete': complete}

    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=ScientificFacts())
    result = read_run(spec)
    assert outcome.status == 'succeeded'
    assert outcome.quality is result['quality'] is quality
    assert outcome.evidence_complete is result['evidence_complete'] is complete


def test_invalid_quality_does_not_publish_success(source_study, run_root, payload_factory):
    from rpipe.api import plan, run

    class InvalidQuality(payload_factory):
        def collect(self, ctx):
            return {'quality': 'pass'}

    spec = plan(source_study, run_root=run_root)[0]
    outcome = run(spec, payload=InvalidQuality())
    assert outcome.status == 'failed'
    assert not spec.result_path.exists()


@pytest.mark.parametrize('entry', ['public', 'mechanism'])
def test_payload_cannot_change_effective_configuration_after_planning(
        source_study, run_root, payload_factory, entry):
    from rpipe.api import plan, run

    class ChangesConfiguration(payload_factory):
        def prepare(self, ctx):
            self._phase('prepare')
            ctx.config['data']['name'] = 'changed_after_plan'

    spec = plan(source_study, run_root=run_root)[0]
    payload = ChangesConfiguration()
    if entry == 'mechanism':
        from rpipe.structure.execution import execute
        outcome = execute(spec, payload)
    else:
        outcome = run(spec, payload=payload)
    assert outcome.status == 'failed'
    assert 'execute' not in payload.calls
    assert spec.config['data']['name'] == 'Toy'
    assert not spec.result_path.exists()
