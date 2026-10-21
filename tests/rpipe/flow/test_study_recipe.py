"""Study-local recipe imports must match fresh-process imports."""

import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rpipe.flow.study import apply_recipe

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_data,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _recipe(root, helper, value, package, at_register, fail=False):
    root.mkdir()
    if package:
        directory = root / helper
        directory.mkdir()
        (directory / '__init__.py').write_text('from .value import VALUE\n', encoding='utf-8')
        (directory / 'value.py').write_text(f'VALUE = {value!r}\n', encoding='utf-8')
    else:
        (root / f'{helper}.py').write_text(f'VALUE = {value!r}\n', encoding='utf-8')
    prefix = '' if at_register else f'from {helper} import VALUE\n'
    body = f'    from {helper} import VALUE\n' if at_register else ''
    body += '    ctx.observed.append(VALUE)\n'
    if fail:
        body += '    raise RuntimeError("registration refused")\n'
    (root / 'recipe.py').write_text(prefix + 'def register(ctx):\n' + body, encoding='utf-8')


def test_recipe_and_relative_helpers_leave_source_tree_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'dont_write_bytecode', False)
    (tmp_path / 'helper.py').write_text('VALUE = 7\n', encoding='utf-8')
    (tmp_path / 'recipe.py').write_text(
        'from .helper import VALUE\ndef register(ctx):\n    ctx.observed.append(VALUE)\n',
        encoding='utf-8')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    observed = []
    assert apply_recipe(tmp_path, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed))
    assert observed == [7]
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('package', [False, True], ids=['module', 'package'])
@pytest.mark.parametrize('at_register', [False, True], ids=['import-on-load', 'import-on-register'])
@pytest.mark.parametrize('first_failure', [False, True], ids=['first-success', 'first-error'])
def test_sequential_studies_import_their_own_recipe_helpers(tmp_path, package, at_register, first_failure):
    helper = '_recipe_isolation_' + uuid4().hex
    paths = list(sys.path)
    observed = []
    try:
        for index, value in enumerate(('first', 'second')):
            study = tmp_path / value
            fail = first_failure and index == 0
            _recipe(study, helper, value, package, at_register, fail)
            if fail:
                with pytest.raises(RuntimeError, match='registration refused'):
                    apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed))
            else:
                assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed))
            assert sys.path == paths
        assert observed == ['first', 'second']
    finally:
        for name in list(sys.modules):
            if name == helper or name.startswith(helper + '.'):
                sys.modules.pop(name)


@pytest.mark.parametrize('package', [False, True], ids=['module', 'package'])
@pytest.mark.parametrize('at_register', [False, True], ids=['import-on-load', 'import-on-register'])
def test_single_study_recipe_helper_control(tmp_path, package, at_register):
    helper = '_recipe_isolation_' + uuid4().hex
    study = tmp_path / 'single'
    paths = list(sys.path)
    observed = []
    try:
        _recipe(study, helper, 'single', package, at_register)
        assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed))
        assert observed == ['single']
        assert sys.path == paths
    finally:
        for name in list(sys.modules):
            if name == helper or name.startswith(helper + '.'):
                sys.modules.pop(name)


@pytest.mark.parametrize('package', [False, True], ids=['module', 'package'])
@pytest.mark.parametrize('at_register', [False, True], ids=['import-on-load', 'import-on-register'])
def test_local_recipe_import_preserves_preexisting_foreign_module(tmp_path, package, at_register):
    from types import ModuleType
    import json

    helper = '_recipe_isolation_' + uuid4().hex
    previous = ModuleType(helper)
    previous.VALUE = 'foreign'
    sys.modules[helper] = previous
    json_module = sys.modules['json']
    paths = list(sys.path)
    observed = []
    try:
        study = tmp_path / 'local'
        _recipe(study, helper, 'local', package, at_register)
        assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed))
        assert observed == ['local']
        assert sys.modules[helper] is previous
        assert sys.modules['json'] is json_module is json
        assert sys.path == paths
    finally:
        for name in list(sys.modules):
            if name == helper or name.startswith(helper + '.'):
                sys.modules.pop(name)


@pytest.mark.parametrize('package', [False, True], ids=['module', 'package'])
def test_registered_functions_keep_each_studies_helper_globals(tmp_path, package):
    helper = '_recipe_isolation_' + uuid4().hex
    registered = []
    try:
        for value in ('first', 'second'):
            study = tmp_path / value
            _recipe(study, helper, value, package, False)
            (study / 'recipe.py').write_text(
                f'from {helper} import VALUE\n'
                'def build():\n    return VALUE\n'
                'def register(ctx):\n    ctx.registered.append(build)\n', encoding='utf-8')
            assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(registered=registered))
        assert [build() for build in registered] == ['first', 'second']
    finally:
        for name in list(sys.modules):
            if name == helper or name.startswith(helper + '.'):
                sys.modules.pop(name)


@pytest.mark.parametrize('nested', [False, True], ids=['root-recipe', 'nested-recipe'])
@pytest.mark.parametrize('alias', [False, True], ids=['direct-dataclass', 'module-alias'])
@pytest.mark.parametrize('postponed', [False, True], ids=['eager-annotations', 'postponed-annotations'])
def test_recipe_dataclass_resolves_annotations_in_defining_module(tmp_path, nested, alias, postponed):
    from dataclasses import fields
    from rpipe.flow.study import load_recipe

    study = tmp_path / 'dataclass-study'
    folder = study / 'recipes' if nested else study
    folder.mkdir(parents=True)
    prefix = 'from __future__ import annotations\n' if postponed else ''
    prefix += 'import dataclasses as dc\n' if alias else 'from dataclasses import dataclass\n'
    decorator = '@dc.dataclass' if alias else '@dataclass'
    (folder / 'recipe.py').write_text(
        prefix + 'from typing import ClassVar\n' + decorator + '\n'
        'class Payload:\n    value: int\n    label: ClassVar[str] = "tag"\n'
        'def register(ctx):\n    pass\n', encoding='utf-8')
    paths = list(sys.path)
    name = 'rpipe_recipe_' + study.resolve().name
    previous = sys.modules.get(name)
    module = load_recipe(study, {'recipe': ('recipes/' if nested else '') + 'recipe.py'})
    assert module.Payload(7).value == 7
    assert module.Payload.label == 'tag'
    assert [field.name for field in fields(module.Payload)] == ['value']
    assert sys.path == paths
    assert sys.modules.get(name) is previous


@pytest.mark.parametrize('nested', [False, True], ids=['root-recipe', 'nested-recipe'])
@pytest.mark.parametrize('postponed', [False, True], ids=['eager-annotations', 'postponed-annotations'])
def test_dataclass_defined_during_register_has_defining_module(tmp_path, nested, postponed):
    study = tmp_path / 'registration-dataclass'
    folder = study / 'recipes' if nested else study
    folder.mkdir(parents=True)
    prefix = 'from __future__ import annotations\n' if postponed else ''
    (folder / 'recipe.py').write_text(
        prefix + 'from dataclasses import dataclass, fields\nfrom typing import ClassVar\n'
        'def register(ctx):\n'
        '    @dataclass\n    class Payload:\n        value: int\n        label: ClassVar[str] = "tag"\n'
        '    ctx.observed.append((Payload(7).value, [f.name for f in fields(Payload)]))\n',
        encoding='utf-8')
    observed = []
    assert apply_recipe(study, {'recipe': ('recipes/' if nested else '') + 'recipe.py'},
                        SimpleNamespace(observed=observed))
    assert observed == [(7, ['value'])]


def test_recipe_import_scope_preserves_all_preexisting_unrelated_cache_entries(tmp_path):
    helper = '_recipe_isolation_' + uuid4().hex
    study = tmp_path / 'cache-owner'
    _recipe(study, helper, 'local', False, False)
    before = dict(sys.modules)
    changed = []
    try:
        assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(observed=[]))
    finally:
        changed = [name for name, module in before.items() if sys.modules.get(name) is not module]
        # Keep failure reporting usable even if a candidate damages interpreter caches.
        sys.modules.update(before)
        for name in list(sys.modules):
            if name == helper or name.startswith(helper + '.'):
                sys.modules.pop(name)
    assert changed == []


@pytest.mark.parametrize('package', [False, True], ids=['module', 'package'])
def test_registered_functions_can_later_import_their_studies_helper(tmp_path, package):
    helper = '_recipe_isolation_' + uuid4().hex
    registered = []
    try:
        for value in ('first', 'second'):
            study = tmp_path / value
            _recipe(study, helper, value, package, False)
            (study / 'recipe.py').write_text(
                f'import {helper}\n'
                f'def build():\n    from {helper} import VALUE\n    return VALUE\n'
                'def register(ctx):\n    ctx.registered.append(build)\n', encoding='utf-8')
            assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(registered=registered))
        assert [build() for build in registered] == ['first', 'second']
    finally:
        for name in list(sys.modules):
            if name == helper or name.startswith(helper + '.'):
                sys.modules.pop(name)


def test_internal_recipe_identity_does_not_shadow_valid_local_helper_filename(tmp_path):
    from rpipe.flow.study import load_recipe

    study = tmp_path / 'reserved-helper'
    study.mkdir()
    (study / '_recipe.py').write_text('VALUE = "helper"\n', encoding='utf-8')
    (study / 'recipe.py').write_text('from _recipe import VALUE\ndef register(ctx):\n    pass\n',
                                   encoding='utf-8')
    assert load_recipe(study, {'recipe': 'recipe.py'}).VALUE == 'helper'


@pytest.mark.parametrize('package', [False, True], ids=['absolute-sibling', 'relative-package-child'])
def test_helper_functions_keep_their_own_late_imports(tmp_path, package):
    helper = '_recipe_isolation_' + uuid4().hex
    registered = []
    for value in ('first', 'second'):
        study = tmp_path / value
        _recipe(study, helper, value, package, False)
        if package:
            (study / helper / '__init__.py').write_text(
                'def build():\n    from .value import VALUE\n    return VALUE\n', encoding='utf-8')
        else:
            sibling = helper + '_sibling'
            (study / f'{sibling}.py').write_text(f'VALUE = {value!r}\n', encoding='utf-8')
            (study / f'{helper}.py').write_text(
                f'def build():\n    from {sibling} import VALUE\n    return VALUE\n', encoding='utf-8')
        (study / 'recipe.py').write_text(
            f'from {helper} import build\n'
            'def register(ctx):\n    ctx.registered.append(build)\n', encoding='utf-8')
        assert apply_recipe(study, {'recipe': 'recipe.py'}, SimpleNamespace(registered=registered))
    assert [build() for build in registered] == ['first', 'second']


def test_repeated_recipe_load_does_not_add_per_run_module_names(tmp_path):
    from rpipe.flow.study import load_recipe

    helper = '_recipe_isolation_' + uuid4().hex
    study = tmp_path / 'repeated'
    _recipe(study, helper, 'stable', False, False)
    first = load_recipe(study, {'recipe': 'recipe.py'})
    prefix = first.__package__ + '.'
    original_names = {name for name in sys.modules if name.startswith(prefix)}
    assert original_names
    for _ in range(4):
        assert load_recipe(study, {'recipe': 'recipe.py'}).VALUE == 'stable'
        assert {name for name in sys.modules if name.startswith(prefix)} == original_names


def test_equal_study_basenames_in_different_projects_keep_local_code(tmp_path):
    from rpipe.flow.study import load_recipe

    helper = '_recipe_isolation_' + uuid4().hex
    modules = []
    for value in ('first', 'second'):
        parent = tmp_path / value
        parent.mkdir()
        study = parent / 'same-name'
        _recipe(study, helper, value, False, False)
        modules.append(load_recipe(study, {'recipe': 'recipe.py'}))
    assert [module.VALUE for module in modules] == ['first', 'second']
    assert modules[0].__package__ != modules[1].__package__


def test_recipe_exec_failure_preserves_previous_defining_module(tmp_path):
    from rpipe.flow.study import load_recipe

    helper = '_recipe_isolation_' + uuid4().hex
    study = tmp_path / 'reload'
    _recipe(study, helper, 'kept', False, False)
    previous = load_recipe(study, {'recipe': 'recipe.py'})
    (study / 'recipe.py').write_text('raise RuntimeError("replacement refused")\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='replacement refused'):
        load_recipe(study, {'recipe': 'recipe.py'})
    assert sys.modules[previous.__name__] is previous
    assert previous.VALUE == 'kept'


@pytest.mark.parametrize('operation', ['annotations', 'pickle'])
def test_recipe_class_metadata_remains_usable_after_loading(tmp_path, operation):
    import pickle
    from typing import get_type_hints
    from rpipe.flow.study import load_recipe

    study = tmp_path / 'metadata'
    study.mkdir()
    (study / 'recipe.py').write_text(
        'from __future__ import annotations\nfrom dataclasses import dataclass\n'
        '@dataclass\nclass State:\n    value: int\n'
        'def register(ctx):\n    pass\n', encoding='utf-8')
    module = load_recipe(study, {'recipe': 'recipe.py'})
    if operation == 'annotations':
        assert get_type_hints(module.State) == {'value': int}
    else:
        restored = pickle.loads(pickle.dumps(module.State(7)))
        assert type(restored) is module.State
        assert restored.value == 7


@pytest.mark.parametrize('first_error', [False, True], ids=['success', 'registration-error'])
def test_concurrent_recipe_helpers_keep_study_identity(tmp_path, first_error):
    import threading

    helper = '_recipe_isolation_' + uuid4().hex
    entered, attempted, second_entered = [threading.Event() for _ in range(3)]
    observed, errors = {}, []
    for index, value in enumerate(('first', 'second')):
        study = tmp_path / value
        _recipe(study, helper, value, False, True)
        (study / 'recipe.py').write_text(
            'def register(ctx):\n'
            f'    from {helper} import VALUE\n'
            '    if ctx.index == 0:\n'
            '        ctx.entered.set()\n        assert ctx.attempted.wait(3)\n'
            '        ctx.second_entered.wait(0.2)\n'
            '    else:\n        ctx.second_entered.set()\n'
            '    ctx.observed[ctx.index] = VALUE\n'
            '    if ctx.index == 0 and ctx.first_error:\n'
            '        raise RuntimeError("registration refused")\n', encoding='utf-8')

    def worker(index):
        try:
            if index:
                assert entered.wait(3)
                attempted.set()
            ctx = SimpleNamespace(index=index, entered=entered, attempted=attempted,
                                  second_entered=second_entered, observed=observed, first_error=first_error)
            try:
                apply_recipe(tmp_path / ('first' if index == 0 else 'second'), {'recipe': 'recipe.py'}, ctx)
            except RuntimeError as exc:
                assert index == 0 and first_error and str(exc) == 'registration refused'
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert observed == {0: 'first', 1: 'second'}


def test_recipe_nonlocal_imports_keep_current_python_import_hook(tmp_path, monkeypatch):
    import builtins
    from rpipe.flow.study import load_recipe

    study = tmp_path / 'external-hook'
    study.mkdir()
    external = '_external_import_' + uuid4().hex
    (study / 'recipe.py').write_text(
        f'def build():\n    import {external}\n'
        'def register(ctx):\n    pass\n', encoding='utf-8')
    module = load_recipe(study, {'recipe': 'recipe.py'})
    original = builtins.__import__
    refusal = ImportError('external import hook refused')

    def guarded(name, *args, **kwargs):
        if name == external:
            raise refusal
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    with pytest.raises(ImportError) as caught:
        module.build()
    assert caught.value is refusal


def test_missing_study_checkpoint_class_does_not_use_foreign_cache(tmp_path, monkeypatch):
    import importlib.util
    import pickle
    from rpipe.flow.study import load_recipe, _recipe_pickle_module

    helper = '_recipe_isolation_' + uuid4().hex
    study = tmp_path / 'strict-class'
    _recipe(study, helper, 'current', False, False)
    (study / f'{helper}.py').write_text('class CurrentState:\n    pass\n', encoding='utf-8')
    (study / 'recipe.py').write_text(
        f'from {helper} import CurrentState\ndef register(ctx):\n    pass\n', encoding='utf-8')
    foreign_file = tmp_path / 'foreign.py'
    foreign_file.write_text('class RemovedState:\n    pass\n', encoding='utf-8')
    spec = importlib.util.spec_from_file_location(helper, foreign_file)
    foreign = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, helper, foreign)
    spec.loader.exec_module(foreign)
    blob = pickle.dumps(foreign.RemovedState())
    load_recipe(study, {'recipe': 'recipe.py'})
    reader = _recipe_pickle_module(study)
    import io
    with pytest.raises(AttributeError):
        reader.Unpickler(io.BytesIO(blob)).load()
    assert sys.modules[helper] is foreign


@pytest.mark.parametrize('from_import', [False, True], ids=['importlib-module', 'import-module-function'])
@pytest.mark.parametrize('stage', ['load', 'register', 'late', 'helper'])
def test_dynamic_imports_keep_their_study_after_registration(tmp_path, from_import, stage):
    target = '_recipe_dynamic_' + uuid4().hex
    helper = '_recipe_dynamic_helper_' + uuid4().hex
    binding = ('from importlib import import_module as dynamic\n' if from_import else
               'import importlib\ndynamic = importlib.import_module\n')
    observed, functions = [], []
    for value in ('first', 'second'):
        root = tmp_path / value
        root.mkdir()
        (root / f'{target}.py').write_text(f'VALUE = {value!r}\n', encoding='utf-8')
        expression = f'dynamic({target!r}).VALUE'
        if stage == 'helper':
            (root / f'{helper}.py').write_text(binding + f'def read():\n    return {expression}\n', encoding='utf-8')
            source = f'from {helper} import read\ndef register(ctx):\n    ctx.functions.append(read)\n'
        elif stage == 'load':
            source = binding + f'VALUE = {expression}\ndef register(ctx):\n    ctx.observed.append(VALUE)\n'
        elif stage == 'register':
            source = binding + f'def register(ctx):\n    ctx.observed.append({expression})\n'
        else:
            source = binding + f'def register(ctx):\n    ctx.functions.append(lambda: {expression})\n'
        (root / 'recipe.py').write_text(source, encoding='utf-8')
        assert apply_recipe(root, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed, functions=functions))
    if stage in ('late', 'helper'):
        observed = [function() for function in functions]
    assert observed == ['first', 'second']
    assert target not in sys.modules


@pytest.mark.parametrize('from_import', [False, True])
def test_dynamic_nonlocal_import_uses_canonical_runtime(tmp_path, from_import):
    import json
    root = tmp_path / 'runtime'
    root.mkdir()
    binding = ('from importlib import import_module as dynamic\n' if from_import else
               'import importlib\ndynamic = importlib.import_module\n')
    (root / 'json.py').write_text('raise AssertionError("unused local stdlib shadow")\n', encoding='utf-8')
    (root / 'recipe.py').write_text(binding + 'def register(ctx):\n    ctx.observed.append(dynamic("json"))\n', encoding='utf-8')
    observed = []
    apply_recipe(root, {'recipe': 'recipe.py'}, SimpleNamespace(observed=observed))
    assert observed == [json]
