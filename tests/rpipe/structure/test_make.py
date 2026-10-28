"""Pure experiment expansion and explicit, protected configuration publication."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import copy
import os
import subprocess
import sys

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_make,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _base():
    return {'data': {'name': 'Toy', 'source': 'stub'},
            'model': {'name': 'linear'}, 'algorithm': {'mode': 'train'},
            'system': {'device': 'cpu'}}


def _expand(study, base, source, run_root):
    from rpipe.structure.make import expand

    return expand(study, base, source_dir=source, run_root=run_root,
                  source_digest='a' * 64, version='test-v1')


def test_explicit_zero_version_is_not_silently_replaced_by_default(tmp_path):
    from rpipe.structure.make import expand

    base = {**_base(), 'version': 0}
    spec = expand({'study': 'toy'}, base, source_dir=tmp_path / 'source',
                  run_root=tmp_path / 'output', source_digest='a' * 64)[0]
    assert spec.version == '0' and spec.config['version'] == 0
    assert not (tmp_path / 'output').exists()


def test_expand_is_pure_and_preserves_effective_conditions(tmp_path, monkeypatch):
    source, output = tmp_path / 'source', tmp_path / 'output'
    declaration = {'study': 'toy', 'seeds': [0, 1],
                   'fixed': {'data': {'config': {'batch_size': 2, 'train_size': 8}}},
                   'axes': {'algorithm.config.lr': [.1, .2],
                            'data.config.batch_size': [4, 8]},
                   'recipe': 'not-loaded.py'}
    base = _base()
    before_study, before_base = copy.deepcopy(declaration), copy.deepcopy(base)
    from rpipe.structure.make import expand

    def forbidden(*args, **kwargs):
        raise AssertionError('pure expansion attempted filesystem access')

    with monkeypatch.context() as guard:
        for name in ('resolve', 'open', 'read_text', 'read_bytes', 'write_text',
                     'write_bytes', 'mkdir', 'touch'):
            guard.setattr(Path, name, forbidden)
        specs = expand(declaration, base, source_dir=source, run_root=output,
                       source_digest='a' * 64, version='test-v1')
    assert len(specs) == 8 and len({spec.id for spec in specs}) == 8
    assert len({spec.experiment for spec in specs}) == 4
    assert {(spec.config['algorithm']['config']['lr'],
             spec.config['data']['config']['batch_size'], spec.seed) for spec in specs} == {
                 (lr, batch, seed) for lr in [.1, .2] for batch in [4, 8] for seed in [0, 1]}
    assert all(spec.config['data']['config']['train_size'] == 8 for spec in specs)
    assert declaration == before_study and base == before_base
    assert not source.exists() and not output.exists()


@pytest.mark.parametrize('declaration', [
    {'study': 'toy', 'seeds': [0, 0]},
    {'study': 'toy', 'seeds': [True]},
    {'study': 'toy', 'seeds': [-1]},
    {'study': 'toy', 'axes': {'algorithm.config.lr': [.1, .1]}},
    {'study': 'toy', 'axes': {'algorithm.config.lr': []}},
    {'study': 'toy', 'unknown': True},
    {'study': 'toy', 'flow': False},
    {'study': 'toy', 'flow': []},
    {'study': 'toy', 'fixed': []},
    {'study': 'toy', 'fixed': {'tags': 'baseline'}},
    {'study': 'toy', 'axes': {'tags': ['baseline']}},
    {'study': 'toy', 'axes': {'tags': [{'baseline': True}]}},
    {'study': 'toy', 'tags': [{'when': {}, 'tags': 'baseline'}]},
    {'study': 'toy', 'flow': {'prepare_shared': False}},
    {'study': 'toy', 'flow': {'prepare_shared': True}},
], ids=['duplicate-seed', 'boolean-seed', 'negative-seed', 'duplicate-factor',
        'empty-axis', 'unknown-field', 'false-flow', 'list-flow', 'list-fixed',
        'string-fixed-tags', 'string-axis-tags', 'mapping-axis-tags',
        'string-rule-tags', 'ignored-false-key', 'unsupported-true-key'])
def test_invalid_declaration_rejects_before_output(tmp_path, declaration):
    source, output = tmp_path / 'source', tmp_path / 'output'
    with pytest.raises((TypeError, ValueError)):
        _expand(declaration, _base(), source, output)
    assert not source.exists() and not output.exists()


def test_explicit_train_dependencies_match_their_effective_conditions(tmp_path):
    specs = _expand({'study': 'toy', 'seeds': [0, 1],
                     'axes': {'algorithm.mode': ['train', 'eval'],
                              'data.config.train_size': [4, 8]}},
                    _base(), tmp_path / 'source', tmp_path / 'output')
    trains = {spec.id: spec for spec in specs if spec.config['algorithm']['mode'] == 'train'}
    evaluations = [spec for spec in specs if spec.config['algorithm']['mode'] == 'eval']
    assert len(trains) == len(evaluations) == 4
    for spec in evaluations:
        train = trains[spec.train_id]
        assert train.seed == spec.seed
        assert train.config['data'] == spec.config['data']


def test_ambiguous_train_dependency_is_not_arbitrarily_selected(tmp_path):
    declaration = {'study': 'toy', 'seeds': [0],
                   'axes': {'algorithm.mode': ['train', 'eval'],
                            'tags': [['first'], ['second']]}}
    with pytest.raises((TypeError, ValueError)):
        _expand(declaration, _base(), tmp_path / 'source', tmp_path / 'output')


def test_plan_loads_only_explicit_inputs_without_execution_or_output(source_study, run_root, file_bytes):
    from rpipe.structure.make import plan

    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('recipe: missing.py\n')
    before = file_bytes(source_study)
    specs = plan(source_study, run_root=run_root)
    assert len(specs) == 2
    assert file_bytes(source_study) == before
    assert not run_root.exists()


def test_planning_modules_do_not_import_scientific_implementations(source_study, run_root):
    script = '''import importlib.abc, sys
class RejectScience(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'torchvision', 'transformers', 'datasets', 'kornia'}:
            raise AssertionError('unexpected science import: ' + fullname)
sys.meta_path.insert(0, RejectScience())
from rpipe.structure.control import resolve_control
from rpipe.structure.make import plan
assert len(plan(sys.argv[1], run_root=sys.argv[2])) == 2
assert not any(name.startswith('rpipe.structure.' + part)
               for name in sys.modules for part in ('data', 'model', 'algorithm', 'system'))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3] / 'src'),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', '-c', script,
                             str(source_study), str(run_root)],
                            env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not run_root.exists()


def test_publication_reuses_config_bytes_and_lists_all_runs(source_study, run_root):
    from rpipe.structure import make
    from rpipe.structure.artifact import io

    specs = make.plan(source_study, run_root=run_root)
    first = make.publish(specs)
    original = {spec.id: (spec.run_dir / 'config.yaml').read_bytes() for spec in specs}
    second = make.publish(specs)
    assert second == first
    runs = [row for experiment in second['experiments'] for row in experiment['runs']]
    assert {row['id'] for row in runs} == {spec.id for spec in specs}
    assert len(runs) == len(specs)
    for spec in specs:
        path = spec.run_dir / 'config.yaml'
        assert path.read_bytes() == original[spec.id]
        assert io.load_config(path) == spec.to_config()
    assert io.read_json(specs[0].study_root / 'index.json') == second


@pytest.mark.parametrize('case', ['duplicate', 'foreign-root', 'foreign-version', 'missing-dependency'])
def test_invalid_publication_rejects_before_any_output(source_study, run_root, case):
    from rpipe.structure import make

    first = make.plan(source_study, run_root=run_root)[0]
    if case == 'duplicate':
        specs = [first, first]
    elif case == 'foreign-root':
        specs = [first, replace(first, run_root=run_root.parent / 'foreign')]
    elif case == 'foreign-version':
        specs = [first, replace(first, version='different')]
    else:
        specs = [replace(first, train_id='not-in-this-plan')]
    with pytest.raises((TypeError, ValueError)):
        make.publish(specs)
    assert not run_root.exists()
    assert not (run_root.parent / 'foreign').exists()


def test_publication_rejects_cyclic_dependencies_before_output(source_study, run_root):
    from rpipe.structure import make

    first, second = make.plan(source_study, run_root=run_root)
    specs = [replace(first, train_id=second.id), replace(second, train_id=first.id)]
    with pytest.raises((TypeError, ValueError)):
        make.publish(specs)
    assert not run_root.exists()


def test_publication_rejects_same_seed_train_with_different_conditions(source_study, run_root):
    import yaml

    from rpipe.structure import make

    declaration = {'study': 'toy', 'seeds': [0],
                   'axes': {'algorithm.mode': ['train', 'eval'],
                            'algorithm.config.lr': [.1, .2]}}
    (source_study / 'study.yaml').write_text(yaml.safe_dump(declaration), encoding='utf-8')
    specs = make.plan(source_study, run_root=run_root)
    wrong_parent = next(spec for spec in specs
                        if spec.config['algorithm']['mode'] == 'train'
                        and spec.config['algorithm']['config']['lr'] == .2)
    target = next(spec for spec in specs
                  if spec.config['algorithm']['mode'] == 'eval'
                  and spec.config['algorithm']['config']['lr'] == .1)
    assert wrong_parent.seed == target.seed
    mismatched = [replace(spec, train_id=wrong_parent.id) if spec.id == target.id else spec
                  for spec in specs]
    with pytest.raises((TypeError, ValueError)):
        make.publish(mismatched)
    assert not run_root.exists()


def test_index_failure_preserves_prior_plan_configs_and_peer(source_study, run_root, monkeypatch):
    from rpipe.structure import make
    from rpipe.structure.artifact import io

    specs = make.plan(source_study, run_root=run_root)
    make.publish(specs)
    index = specs[0].study_root / 'index.json'
    old_index = index.read_bytes()
    configs = {spec.id: (spec.run_dir / 'config.yaml').read_bytes() for spec in specs}
    peer = specs[0].study_root / 'peer.bin'
    peer.write_bytes(b'another owner')
    write_json = io.write_json

    def fail_index(path, value, *args, **kwargs):
        if Path(path) == index:
            raise OSError('index publication failed')
        return write_json(path, value, *args, **kwargs)

    monkeypatch.setattr(io, 'write_json', fail_index)
    with pytest.raises(OSError, match='index publication failed'):
        make.publish(specs)
    assert index.read_bytes() == old_index
    assert peer.read_bytes() == b'another owner'
    assert all((spec.run_dir / 'config.yaml').read_bytes() == configs[spec.id] for spec in specs)


def test_every_run_is_owned_until_index_publication_finishes(source_study, run_root, monkeypatch):
    from rpipe.structure import make
    from rpipe.structure.artifact import io
    from rpipe.structure.control import BusyError

    specs = make.plan(source_study, run_root=run_root)
    write_json = io.write_json
    checked = []

    def inspect_index(path, value, *args, **kwargs):
        if Path(path) == specs[0].study_root / 'index.json':
            for spec in specs:
                assert io.load_config(spec.run_dir / 'config.yaml') == spec.to_config()
                with pytest.raises(BusyError):
                    io.Ownership(spec).acquire()
            checked.append(True)
        return write_json(path, value, *args, **kwargs)

    monkeypatch.setattr(io, 'write_json', inspect_index)
    make.publish(specs)
    assert checked == [True]


def test_source_is_rechecked_after_publication_lock_is_acquired(source_study, run_root, monkeypatch):
    from rpipe.structure import make
    from rpipe.structure.artifact import io

    specs = make.plan(source_study, run_root=run_root)
    make.publish(specs)
    index = specs[0].study_root / 'index.json'
    before_index = index.read_bytes()
    before_configs = {spec.id: (spec.run_dir / 'config.yaml').read_bytes() for spec in specs}
    cache_lock = io.cache_lock
    changed = []

    @contextmanager
    def change_after_acquiring(path, *args, **kwargs):
        with cache_lock(path, *args, **kwargs):
            if not changed:
                (source_study / 'changed.py').write_text('VALUE = 2\n', encoding='utf-8')
                changed.append(True)
            yield

    monkeypatch.setattr(io, 'cache_lock', change_after_acquiring)
    with pytest.raises((TypeError, ValueError)):
        make.publish(specs)
    assert changed == [True]
    assert index.read_bytes() == before_index
    assert all((spec.run_dir / 'config.yaml').read_bytes() == before_configs[spec.id] for spec in specs)


@pytest.mark.parametrize('indexed', [True, False], ids=['existing-index', 'unindexed-execution'])
def test_new_source_cannot_resign_prior_execution_without_scientific_result(
        source_study, run_root, indexed):
    from rpipe.structure import make
    from rpipe.structure.artifact import io
    from rpipe.structure.control import SourceMismatch

    original = make.plan(source_study, run_root=run_root)
    make.publish(original)
    index = original[0].study_root / 'index.json'
    if not indexed:
        index.unlink()  # This test owns the newly published fixture index.
    spec = original[0]
    execution = spec.run_dir / 'execution.json'
    io.write_json(execution, {
        'schema': 1, 'identity': io.identity(spec), 'attempt': 'prior-attempt',
        'phase': 'closing', 'candidate_status': 'failed', 'evidence_complete': False,
        'quality': None, 'scientific_result_published': False, 'cancelled': False,
        'cleanup_errors': [], 'error': {'type': 'RuntimeError', 'message': 'prior failure'}})
    assert not spec.result_path.exists()
    before_execution = execution.read_bytes()
    before_configs = {item.id: (item.run_dir / 'config.yaml').read_bytes() for item in original}
    before_index = index.read_bytes() if indexed else None
    with (source_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('# source changed after the recorded attempt\n')
    changed = make.plan(source_study, run_root=run_root)
    assert changed[0].source_digest != spec.source_digest
    assert changed[0].version == spec.version and changed[0].id == spec.id
    with pytest.raises(SourceMismatch):
        make.publish(changed)
    assert execution.read_bytes() == before_execution
    assert all((item.run_dir / 'config.yaml').read_bytes() == before_configs[item.id]
               for item in original)
    if indexed:
        assert index.read_bytes() == before_index
    else:
        assert not index.exists()
    assert not spec.result_path.exists()


def test_prior_index_format_is_rejected_without_republishing_it(source_study, run_root):
    from rpipe.structure import make
    from rpipe.structure.artifact import io
    from rpipe.structure.control import SourceMismatch

    specs = make.plan(source_study, run_root=run_root)
    make.publish(specs)
    index = specs[0].study_root / 'index.json'
    prior = io.read_json(index)
    prior['schema'] = 2
    prior.pop('provenance', None)
    io.write_json(index, prior)
    before_index = index.read_bytes()
    configs = {spec.id: (spec.run_dir / 'config.yaml').read_bytes() for spec in specs}
    with pytest.raises(SourceMismatch):
        make.publish(specs)
    assert index.read_bytes() == before_index
    assert all((spec.run_dir / 'config.yaml').read_bytes() == configs[spec.id] for spec in specs)
