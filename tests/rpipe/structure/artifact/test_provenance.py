"""Explicit source declarations reject malformed requests instead of coercing them."""

import pytest
import yaml


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('declaration', [
    {'include': 'x'},
    {'include': [7]},
    {'unknown': True},
    False,
    [],
], ids=['string-include', 'numeric-include-member', 'unknown-key',
        'false-provenance', 'list-provenance'])
def test_malformed_provenance_rejects_before_output_even_when_named_files_exist(
        source_study, run_root, file_bytes, declaration):
    from rpipe.structure import make
    from rpipe.structure.artifact import provenance
    from rpipe.structure.control import AdmissionError

    # Both names exist, so string iteration/coercion would incorrectly accept them.
    (source_study / 'x').write_bytes(b'explicit source x')
    (source_study / '7').write_bytes(b'explicit source seven')
    study = {'study': 'toy', 'seeds': [0], 'provenance': declaration}
    (source_study / 'study.yaml').write_text(yaml.safe_dump(study), encoding='utf-8')
    before = file_bytes(source_study)
    with pytest.raises(AdmissionError):
        provenance.source_digest(source_study)
    with pytest.raises(AdmissionError):
        make.plan(source_study, run_root=run_root)
    assert file_bytes(source_study) == before
    assert not run_root.exists()
