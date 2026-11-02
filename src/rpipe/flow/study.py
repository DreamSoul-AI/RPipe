"""Explicit execution-only recipe and thin Study hook loading."""
from __future__ import annotations
import builtins
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import site
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from threading import RLock
from typing import Any
from importlib import import_module
from uuid import uuid4

STUDY_PHASES = ('prepare', 'execute', 'collect', 'summarize', 'write', 'process')

def study_phases_enabled(study: dict[str, Any]) -> bool:
    block = study.get('flow', {})
    if not isinstance(block, dict):
        raise ValueError('flow must be a mapping')
    enabled = block.get('study_phases', False)
    if not isinstance(enabled, bool):
        raise ValueError('flow.study_phases must be a boolean')
    return enabled

def study_phase_files(study_dir: Path | str, study: dict[str, Any]) -> list[Path]:
    if not study_phases_enabled(study):
        return []
    root = Path(study_dir).resolve()
    flow = root / 'flow'
    if flow.exists() and (not flow.is_dir() or not flow.resolve().is_relative_to(root)):
        raise ValueError(f'Study flow must be a directory inside the Study: {flow}')
    files = []
    for phase in STUDY_PHASES:
        single = flow / f'{phase}.py'
        if single.exists():
            if not single.is_file() or not single.resolve().is_relative_to(root):
                raise ValueError(f'Study phase source must stay inside the Study: {single}')
            if (flow / phase / '__init__.py').is_file():
                raise ValueError(f'ambiguous Study phase: {phase}')
            files.append(single)
        if (root / phase / '__init__.py').is_file():
            raise ValueError(f'move legacy Study phase to flow/{phase}/: {root / phase}')
        directory = flow / phase
        if not directory.exists():
            continue
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            raise ValueError(f'Study phase must be a directory inside the Study: {directory}')
        if not (directory / '__init__.py').is_file():
            continue
        for path in sorted(directory.rglob('*.py')):
            if not path.resolve().is_relative_to(root):
                raise ValueError(f'Study phase source must stay inside the Study: {path}')
            files.append(path)
    return files

@dataclass(frozen=True)
class RecipeContext:
    study_dir: Path
    run_id: str
    seed: int
    config: dict[str, Any] = field(repr=False)
    shared_data_dir: Path
    shared_model_dir: Path
    assets_dir: Path
    study_root: Path | None = None
    run_root: Path | None = None

def recipe_path(study_dir: Path | str, study: dict[str, Any]) -> Path | None:
    raw = study.get('recipe')
    if raw in (None, ''):
        return None
    root = Path(study_dir).resolve()
    path = (root / str(raw)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f'recipe must stay inside the Study: {raw}') from exc
    if not path.is_file():
        raise FileNotFoundError(f'missing recipe: {path}')
    return path

_recipe_import_lock = RLock()

_site_roots = tuple((Path(path) for path in [*site.getsitepackages(), site.getusersitepackages()]))

class _StudySourceLoader(importlib.machinery.SourceFileLoader):

    def get_code(self, fullname):
        # Study sources are inputs; never read or write a bytecode cache beside them.
        return self.source_to_code(self.get_data(self.path), self.path)

class _RecipeLoader(_StudySourceLoader):

    def exec_module(self, module):
        package = sys.modules[module.__name__.partition('.')[0]]
        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=package._recipe_import)
        super().exec_module(module)

class _RecipeFinder(importlib.abc.MetaPathFinder):

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(('_rpipe_recipe_', '_rpipe_study_')):
            return None
        package = sys.modules.get(fullname.partition('.')[0])
        if package is None or not (getattr(package, '_recipe_namespace', False)
                                   or getattr(package, '_study_namespace', False)):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            loader = _RecipeLoader if getattr(package, '_recipe_namespace', False) else _StudySourceLoader
            spec.loader = loader(fullname, spec.origin)
        return spec

def _recipe_namespace(root: Path) -> str:
    return '_rpipe_recipe_' + hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()

def _local_module(package: ModuleType, name: str) -> bool:
    top = name.partition('.')[0]
    existing = sys.modules.get(top)
    filename = vars(existing).get('__file__') if isinstance(existing, ModuleType) else None
    installed = filename and any((Path(filename).is_relative_to(root) for root in _site_roots))
    if top in sys.stdlib_module_names or top == __name__.partition('.')[0] or installed:
        return False
    qualified = package.__name__ + '.' + top
    return qualified in sys.modules or importlib.machinery.PathFinder.find_spec(qualified, package.__path__) is not None

def _recipe_package(root: Path, recipe_root: Path) -> ModuleType:
    name = _recipe_namespace(root)
    package = sys.modules.get(name)
    if package is None:
        package = ModuleType(name)
        package.__package__ = name
        package.__path__ = []
        package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        package.__spec__.submodule_search_locations = package.__path__
        package._recipe_namespace = True
        owner = package
        importlib_view = ModuleType('importlib')

        def dynamic_import(name, package=None):
            if not name.startswith('.') and _local_module(owner, name):
                name = owner.__name__ + '.' + name
            return importlib.import_module(name, package)

        def local_import(module_name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and _local_module(package, module_name):
                imported = builtins.__import__(name + '.' + module_name, globals, locals, fromlist, 0)
                return imported if fromlist else sys.modules[name + '.' + module_name.partition('.')[0]]
            imported = builtins.__import__(module_name, globals, locals, fromlist, level)
            if imported is importlib:
                importlib_view.__dict__.update(vars(importlib))
                importlib_view.import_module = dynamic_import
                return importlib_view
            return imported
        package._recipe_import = local_import
        sys.modules[name] = package
    package.__path__[:] = list(dict.fromkeys([str(recipe_root), str(root)]))
    return package

def load_recipe(study_dir: Path | str, study: dict[str, Any]) -> ModuleType | None:
    if not any(isinstance(finder, _RecipeFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _RecipeFinder())
    path = recipe_path(study_dir, study)
    if path is None:
        return None
    root = Path(study_dir).resolve()
    with _recipe_import_lock:
        package = _recipe_package(root, path.parent)
        name = package.__name__ + '.<recipe>'
        spec = importlib.util.spec_from_file_location(name, path, loader=_RecipeLoader(name, str(path)))
        if spec is None or spec.loader is None:
            raise ImportError(f'cannot load recipe: {path}')
        module = importlib.util.module_from_spec(spec)
        previous, existed = (sys.modules.get(name), name in sys.modules)
        sys.modules[name] = module
        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=package._recipe_import)
        try:
            spec.loader.exec_module(module)
            if not callable(getattr(module, 'register', None)):
                raise AttributeError(f'recipe must define register(ctx): {path}')
        except BaseException:
            if existed:
                sys.modules[name] = previous
            else:
                sys.modules.pop(name, None)
            raise
        return module

def apply_recipe(study_dir: Path | str, study: dict[str, Any], ctx: RecipeContext) -> bool:
    """Return True when a recipe was registered for this Run."""
    module = load_recipe(study_dir, study)
    if module is None:
        return False
    with _recipe_import_lock:
        package = sys.modules[module.__package__]
        package.__path__[:] = list(dict.fromkeys([str(Path(study_dir).resolve()), *package.__path__]))
        module.register(ctx)
    return True

def run_study_phase(study_dir: Path | str, phase: str, ctx: Any, study: dict[str, Any], *, entry: str='run') -> bool:
    if entry not in ('run', 'before') or (entry == 'before' and phase != 'prepare'):
        raise ValueError(f'unsupported Study phase entry: {phase}.{entry}')
    if phase not in STUDY_PHASES:
        raise ValueError(f'unknown Study phase: {phase}')
    if not study_phases_enabled(study):
        return False
    root = Path(study_dir).resolve()
    study_phase_files(root, study)
    single = root / 'flow' / f'{phase}.py'
    path = single if single.is_file() else root / 'flow' / phase / '__init__.py'
    if not path.is_file():
        return False
    if not any(isinstance(finder, _RecipeFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _RecipeFinder())
    name = f'_rpipe_study_{uuid4().hex}'
    package = ModuleType(name)
    package.__path__ = [str(root)]
    package.__package__ = name
    package._study_namespace = True
    sys.modules[name] = package
    try:
        module = import_module(f'{name}.flow.{phase}')
        hook = getattr(module, entry, None)
        if entry == 'before' and hook is None:
            return False
        if not callable(hook):
            raise AttributeError(f'Study phase must define {entry}(ctx): {path}')
        hook(ctx)
    finally:
        for key in list(sys.modules):
            if key == name or key.startswith(name + '.'):
                del sys.modules[key]
    return True
