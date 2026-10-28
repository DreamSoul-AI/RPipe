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


def test_structure_never_imports_flow_or_the_public_assembly_api():
    violations = []
    for path in (SOURCE / 'structure').rglob('*.py'):
        for line, names in _imports(path):
            if any(name == 'rpipe.api' or name == 'rpipe.flow' or name.startswith('rpipe.flow.')
                   for name in names):
                violations.append(f'{path.relative_to(SOURCE)}:{line}: {names}')
    assert not violations, '\n'.join(violations)


def test_control_depends_only_on_the_standard_library():
    for line, names in _imports(SOURCE / 'structure' / 'control.py'):
        assert all(name.split('.')[0] in sys.stdlib_module_names for name in names), (line, names)


def test_planning_and_publication_cannot_reach_execution_or_science():
    allowed = {'rpipe.structure.control', 'rpipe.structure.artifact',
               'rpipe.structure.artifact.io', 'rpipe.structure.artifact.provenance',
               'rpipe.__version__'}
    paths = [SOURCE / 'structure' / 'make.py',
             *(SOURCE / 'structure' / 'artifact').glob('*.py')]
    for path in paths:
        for line, names in _imports(path):
            for name in names:
                if name.startswith('rpipe.'):
                    assert any(name == target or name.startswith(target + '.')
                               for target in allowed), (path, line, name)
                elif name != 'rpipe':
                    assert name.split('.')[0] in sys.stdlib_module_names | {'yaml'}, (path, line, name)


def test_artifact_io_does_not_depend_on_provenance():
    for line, names in _imports(SOURCE / 'structure' / 'artifact' / 'io.py'):
        assert not any(name.startswith('rpipe.structure.artifact.provenance') for name in names), (line, names)
