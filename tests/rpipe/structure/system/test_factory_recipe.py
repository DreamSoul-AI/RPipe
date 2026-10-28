"""Real recipe-owned classes restore from legacy and relocated checkpoint bundles."""
from pathlib import Path
import pytest
pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'), pytest.mark.result_type('categorical', detail='summary')]

def _study(parent, recipe):
    source = parent / 'source'
    source.mkdir()
    (source / 'recipe.py').write_text(recipe, encoding='utf-8')
    (source / 'study.yaml').write_text('study: checkpoint_recipe\nseeds: [0]\nrecipe: recipe.py\n', encoding='utf-8')
    (source / 'experiment_config.yaml').write_text(
        'data: {name: Toy, source: stub}\nmodel: {name: linear, source: recipe_src}\n'
        'algorithm: {mode: train, num_steps: 2}\nsystem: {device: cpu}\n', encoding='utf-8')
    return source

@pytest.mark.parametrize('form', ['bundle', 'pieces', 'payload'])
@pytest.mark.parametrize('relocated', [False, True], ids=['legacy-helper-state', 'relocated-new-helper-state'])
def test_recipe_checkpoint_state_keeps_legacy_and_relocated_imports(tmp_path, monkeypatch, request, relocated, form):
    import importlib.util
    import sys
    from rpipe.structure.make import plan
    from rpipe.structure.execution import RunContext
    from rpipe.structure.contracts import Cancellation
    from rpipe.flow.run import configure
    from rpipe.flow.native import NativeRun
    from rpipe.structure.control import SystemConfig
    from rpipe.structure.system.factory import SystemFactory
    from rpipe.structure.model.factory import ModelRegistry
    monkeypatch.setattr(ModelRegistry, '_items', dict(ModelRegistry._items))

    recipe = (
        '''from helper import State
from rpipe.structure.model.factory import ModelRegistry, _build_linear
def register(ctx):
    def build(model_config, assets_dir, data_meta=None):
        model = _build_linear(model_config, assets_dir, data_meta)
        model._recipe_state_type = State
        return model
    ModelRegistry.register("linear", "recipe_src", build)
'''
    )

    def create(parent, label):
        parent.mkdir()
        study = _study(parent, recipe=recipe)
        (study / 'helper.py').write_text(
            'import torch\nfrom dataclasses import dataclass\n@dataclass\nclass State:\n'
            f'    value: int\n    tensor_type = torch.Tensor\n    label = {label!r}\n', encoding='utf-8')
        (study / 'torch.py').write_text('MARK = "unused local source"\n', encoding='utf-8')
        (study / 'rpipe.py').write_text('MARK = "unused local source"\n', encoding='utf-8')
        declaration = study / 'study.yaml'
        declaration.write_text(declaration.read_text(encoding='utf-8').replace(
            'notes/*.txt', 'notes/*.txt, helper.py'), encoding='utf-8')
        return study

    study = create(tmp_path / 'original', 'original')
    spec = importlib.util.spec_from_file_location('helper', study / 'helper.py')
    legacy_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'helper', legacy_module)
    spec.loader.exec_module(legacy_module)
    legacy = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}),
                                 tmp_path / 'legacy-inputs' / 'assets')
    import torch
    bundle = legacy.save_checkpoint({'model': {'_extra_state': legacy_module.State(7),
                                                'tensor': torch.tensor([1, 2])},
                                     'metadata': legacy_module.State(9)}, 'legacy')
    if form != 'bundle':
        import shutil
        folder = legacy.checkpoint_dir() / ('legacy-' + form)
        if form == 'pieces':
            shutil.copytree(bundle.with_suffix(''), folder)
        else:
            folder.mkdir()
            shutil.copy2(bundle, folder / 'payload.pt')
        bundle = folder
    monkeypatch.delitem(sys.modules, 'helper')
    input_root = legacy.checkpoint_dir()
    before = {path.relative_to(input_root).as_posix(): path.read_bytes()
              for path in input_root.rglob('*') if path.is_file()}

    def prepared(study):
        spec = plan(study, run_root=tmp_path / 'outputs')[0]
        from rpipe.structure.artifact.io import ArtifactLayout
        ctx = RunContext(spec=spec, cancellation=Cancellation(), layout=ArtifactLayout(spec))
        configure(ctx)
        payload = NativeRun()
        request.addfinalizer(lambda: payload.close(ctx))
        payload.prepare(ctx)
        return ctx

    ctx = prepared(study)
    loaded = ctx.state['system'].load_checkpoint(str(bundle))
    assert loaded['model']['_extra_state'].value == 7
    assert loaded['metadata'].value == 9
    assert loaded['model']['_extra_state'].tensor_type is torch.Tensor
    assert torch.equal(loaded['model']['tensor'], torch.tensor([1, 2]))
    assert type(loaded['model']['_extra_state']) is ctx.state['model']._recipe_state_type
    if relocated:
        bundle = ctx.state['system'].save_checkpoint(loaded, 'with-recipe-state')
        target = create(tmp_path / 'relocated', 'relocated')
        target_ctx = prepared(target)
        original_namespace = ctx.state['model']._recipe_state_type.__module__.partition('.')[0]
        original_modules = {name: module for name, module in list(sys.modules.items())
                            if name == original_namespace or name.startswith(original_namespace + '.')}
        for name in original_modules:
            sys.modules.pop(name)
        try:
            restored = target_ctx.state['system'].load_checkpoint(str(bundle))
            assert restored['model']['_extra_state'].value == 7
            assert type(restored['model']['_extra_state']) is target_ctx.state['model']._recipe_state_type
            assert restored['model']['_extra_state'].label == 'relocated'
            import shutil
            shutil.copy2(bundle, target_ctx.state['system'].checkpoint_dir() / 'copied.pt')
            copied = target_ctx.state['system'].checkpoint_dir() / 'copied.pt'
            copied_before = copied.read_bytes()
            displayed = target_ctx.state['system'].load_checkpoint('copied')
            assert type(displayed['model']['_extra_state']) is target_ctx.state['model']._recipe_state_type
            assert copied.read_bytes() == copied_before
        finally:
            sys.modules.update(original_modules)
    assert {path.relative_to(input_root).as_posix(): path.read_bytes()
            for path in input_root.rglob('*') if path.is_file()} == before
