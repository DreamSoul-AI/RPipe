"""RPipe implementations stay inside structure and flow, with one-way imports."""
import ast
import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.location, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]

SOURCE = Path(__file__).resolve().parents[2] / 'src' / 'rpipe'


def test_package_has_only_structure_and_flow_implementation_trees():
    packages = {p.name for p in SOURCE.iterdir() if p.is_dir() and p.name != '__pycache__'}
    assert packages == {'structure', 'flow'}
    assert not (SOURCE / 'structure' / 'payload.py').exists()
    assert not any(path.name == 'payloads' for path in (SOURCE / 'structure').rglob('*')
                   if path.is_dir())


def _imports(path):
    module = 'rpipe.' + '.'.join(path.relative_to(SOURCE).with_suffix('').parts)
    package = module.removesuffix('.__init__') if path.name == '__init__.py' else module.rpartition('.')[0]
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name('.' * node.level + (node.module or ''), package)
            names = [base, *(base + '.' + alias.name for alias in node.names)]
        if names:
            yield node.lineno, names


@pytest.mark.parametrize('path', (SOURCE / 'structure').rglob('*.py'), ids=lambda path: path.relative_to(SOURCE).as_posix())
def test_structure_never_imports_flow_or_the_public_assembly_api(path):
    violations = []
    for line, names in _imports(path):
        if any(name == 'rpipe.api' or name == 'rpipe.flow' or name.startswith('rpipe.flow.')
               for name in names):
            violations.append(f'{path.relative_to(SOURCE)}:{line}: {names}')
    assert not violations, '\n'.join(violations)


def test_control_depends_only_on_the_standard_library():
    for line, names in _imports(SOURCE / 'structure' / 'control.py'):
        assert all(name.split('.')[0] in sys.stdlib_module_names for name in names), (line, names)


@pytest.mark.parametrize('path', [SOURCE / 'structure/make.py', SOURCE / 'structure/artifact/io.py', SOURCE / 'structure/artifact/provenance.py'], ids=lambda path: path.relative_to(SOURCE).as_posix())
def test_planning_and_publication_cannot_reach_execution_or_science(path):
    allowed = {'rpipe.structure.control',
               'rpipe.structure.artifact.io', 'rpipe.structure.artifact.provenance',
               'rpipe.__version__'}
    for line, names in _imports(path):
        for name in names:
            if name.startswith('rpipe.'):
                assert name in {'rpipe.structure', 'rpipe.structure.artifact'} or any(
                    name == target or name.startswith(target + '.') for target in allowed), (path, line, name)
            elif name != 'rpipe':
                assert name.split('.')[0] in sys.stdlib_module_names | {'yaml'}, (path, line, name)


def test_artifact_io_does_not_depend_on_provenance():
    for line, names in _imports(SOURCE / 'structure' / 'artifact' / 'io.py'):
        assert not any(name.startswith('rpipe.structure.artifact.provenance') for name in names), (line, names)


def test_runtime_context_depends_only_on_standard_library_and_pure_control():
    for line, names in _imports(SOURCE / 'flow' / 'context.py'):
        assert all(name.split('.')[0] in sys.stdlib_module_names
                   or name == 'rpipe.structure.control'
                   or name.startswith('rpipe.structure.control.')
                   for name in names), (line, names)


def test_worker_delegates_scientific_construction_to_stages():
    forbidden = tuple('rpipe.structure.' + name for name in ('data', 'model', 'algorithm', 'system'))
    for line, names in _imports(SOURCE / 'flow' / 'worker.py'):
        assert not any(name == prefix or name.startswith(prefix + '.')
                       for prefix in forbidden for name in names), (line, names)


def test_stages_write_is_the_only_formal_result_publisher():
    publishers = []
    for path in SOURCE.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(function):
                if not isinstance(call, ast.Call) or not call.args:
                    continue
                name = call.func.id if isinstance(call.func, ast.Name) else (
                    call.func.attr if isinstance(call.func, ast.Attribute) else '')
                target = call.args[0]
                if name == 'write_json' and isinstance(target, ast.Attribute) and target.attr == 'result_path':
                    publishers.append((path.relative_to(SOURCE).as_posix(), function.name))
    assert publishers == [('flow/stages.py', 'write')]


def test_retired_runtime_adapters_and_api_execution_wrappers_are_absent():
    assert not (SOURCE / 'api.py').exists()
    assert not any((SOURCE / name).exists() for name in (
        'structure/execution.py', 'structure/contracts.py', 'flow/native.py', 'flow/huggingface.py'))


def test_retired_prepare_and_empty_alias_surfaces_are_absent():
    from rpipe.structure.artifact import io
    from rpipe.flow.context import Cancellation
    assert not (SOURCE / 'structure/data/prepare.py').exists()
    assert not hasattr(io, 'kinds')
    token = Cancellation()
    assert not hasattr(token, 'requested')


@pytest.mark.parametrize('name', ('data', 'model', 'algorithm', 'system'), ids=str)
def test_component_builders_replace_static_factory_classes(name):
    tree = ast.parse((SOURCE / 'structure' / name / 'factory.py').read_text(encoding='utf-8'))
    assert any(isinstance(node, ast.FunctionDef) and node.name == 'build_' + name
               for node in tree.body)
    assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef))
                   and node.name == name.title() + 'Factory' for node in tree.body)


@pytest.mark.parametrize('path', (SOURCE / 'structure' / 'algorithm').glob('*.py'), ids=lambda path: path.relative_to(SOURCE).as_posix())
def test_algorithms_consume_ports_without_constructing_other_components(path):
    forbidden = {'rpipe.structure.data.factory', 'rpipe.structure.model.factory',
                 'rpipe.structure.system.factory'}
    for line, names in _imports(path):
        assert not any(name == target or name.startswith(target + '.')
                       for name in names for target in forbidden), (path, line, names)


@pytest.mark.parametrize('path', (SOURCE / 'structure' / 'artifact').glob('*.py'), ids=lambda path: path.relative_to(SOURCE).as_posix())
def test_artifact_storage_readout_and_comparison_do_not_reach_factories_or_flow(path):
    forbidden = {'rpipe.flow', 'rpipe.structure.data.factory', 'rpipe.structure.model.factory',
                 'rpipe.structure.algorithm.factory', 'rpipe.structure.system.factory'}
    for line, names in _imports(path):
        assert not any(name == target or name.startswith(target + '.')
                       for name in names for target in forbidden), (path, line, names)


@pytest.mark.parametrize('path', (SOURCE / 'structure' / 'system').glob('*.py'), ids=lambda path: path.relative_to(SOURCE).as_posix())
def test_system_primitives_do_not_reach_checkpoint_policy_or_algorithm(path):
    for line, names in _imports(path):
        assert not any(name.startswith('rpipe.structure.algorithm')
                       or name.startswith('rpipe.structure.artifact.checkpoint')
                       for name in names), (path, line, names)


@pytest.fixture(scope='module')
def module_dependency_graph():
    modules = {}
    for path in SOURCE.rglob('*.py'):
        relative = path.relative_to(SOURCE).with_suffix('')
        parts = relative.parts[:-1] if path.name == '__init__.py' else relative.parts
        modules['.'.join(('rpipe', *parts))] = path
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        for _, targets in _imports(path):
            for target in targets:
                while target and target not in modules:
                    target = target.rpartition('.')[0]
                if target and target != name:
                    graph[name].add(target)
    return graph


@pytest.mark.parametrize('path', sorted(SOURCE.rglob('*.py')),
                         ids=lambda path: path.relative_to(SOURCE).as_posix())
def test_module_dependency_has_no_cycle(path, module_dependency_graph):
    parts = path.relative_to(SOURCE).with_suffix('').parts
    if path.name == '__init__.py':
        parts = parts[:-1]
    start = '.'.join(('rpipe', *parts))
    pending = [(target, [start, target]) for target in module_dependency_graph[start]]
    visited = set()
    while pending:
        target, chain = pending.pop()
        assert target != start, ' -> '.join(chain)
        if target not in visited:
            visited.add(target)
            pending.extend((child, [*chain, child]) for child in module_dependency_graph[target])
