"""Runtime System object, factory, and Logger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rpipe.structure.artifacts import _read_text
from rpipe.structure.contracts import SystemConfig
from rpipe.structure.system.logger import Logger


class System:
    def __init__(
        self,
        *,
        device: str,
        logger: Logger,
        assets_dir: Path,
        meta: dict[str, Any],
        study_root: Path | str | None = None,
        source_dir: Path | str | None = None,
        run_id: str | None = None,
        train_run_dir: Path | str | None = None,
        checkpoint_pickle_module: Any = None,
    ) -> None:
        self.device = device
        self.logger = logger
        self.assets_dir = assets_dir
        self.meta = meta
        self.study_root = Path(study_root) if study_root is not None else None
        self.source_dir = Path(source_dir) if source_dir is not None else None
        self.run_id = run_id
        self.train_run_dir = Path(train_run_dir) if train_run_dir is not None else None
        self.checkpoint_pickle_module = checkpoint_pickle_module
        self.cancellation = None

    def place_module(self, module: Any) -> Any:
        if module is None:
            return None
        import torch

        return module.to(torch.device(self.device))

    def checkpoint_dir(self) -> Path:
        from rpipe.structure import artifacts as kinds

        directory = Path(self.assets_dir) / kinds.CHECKPOINTS
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_checkpoint(self, payload: dict[str, Any], name: str) -> Path:
        """Publish an atomic bundle after updating its diagnostic piece mirror."""
        import json
        import shutil
        import torch

        from rpipe.structure.artifacts import atomic_replace, atomic_write_text

        stem = str(name).replace('/', '_').replace('\\', '_')
        if not stem or stem in ('.', '..') or Path(stem).name != stem:
            raise ValueError(f'invalid checkpoint name: {name!r}')
        root = self.checkpoint_dir()
        bundle = root / f'{stem}.pt'
        pending_bundle = root / f'.{stem}.pt.writing'
        folder = root / stem
        incomplete = root / f'.{stem}.incomplete'
        if not bundle.is_file() and folder.is_dir() and not incomplete.exists():
            previous = self.load_checkpoint(str(folder))
            if previous is not None:
                torch.save(previous, pending_bundle)
                atomic_replace(pending_bundle, bundle)
        torch.save(payload, pending_bundle)
        tmp = root / f'.{stem}.writing'
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        torch_keys = ('model', 'optimizer', 'scheduler')
        for key in torch_keys:
            value = payload.get(key)
            if value is not None:
                torch.save(value, tmp / f'{key}.pt')
        for key in ('tracker', 'logger'):
            if payload.get(key) is not None:
                atomic_write_text(tmp / f'{key}.json', json.dumps(payload[key], indent=2, ensure_ascii=False))
        meta = {
            key: value
            for key, value in payload.items()
            if key not in torch_keys and key not in ('tracker', 'logger')
        }
        try:
            atomic_write_text(tmp / 'meta.json', json.dumps(meta, indent=2, ensure_ascii=False))
        except TypeError:
            torch.save(meta, tmp / 'meta.pt')
        # shortcut: the coordinator owns one writer per Run; use separate staging for independent concurrent writers.
        incomplete.touch()
        folder.mkdir(exist_ok=True)
        for part in ('model.pt', 'optimizer.pt', 'scheduler.pt', 'tracker.json', 'logger.json', 'meta.json', 'meta.pt', 'payload.pt'):
            source, target = tmp / part, folder / part
            if source.is_file():
                atomic_replace(source, target)
            else:
                target.unlink(missing_ok=True)
        atomic_replace(pending_bundle, bundle)
        try:
            incomplete.unlink()
            tmp.rmdir()
        except OSError:
            pass  # The bundle is committed; leftover diagnostics cannot turn this into a failed save.
        return bundle

    def load_checkpoint(self, name: str) -> dict[str, Any] | None:
        """Read a ``.pt`` bundle, a checkpoint directory, or a path."""
        import json
        import torch

        reader = self.checkpoint_pickle_module
        reader = reader() if callable(reader) else reader
        pickle_options = {} if reader is None else {'pickle_module': reader}

        raw = str(name)
        path = Path(raw)
        if not (path.suffix.lower() == '.pt' or path.is_file() or path.is_dir() or '/' in raw or '\\' in raw):
            bundle = self.checkpoint_dir() / f'{raw}.pt'
            folder = self.checkpoint_dir() / raw
            path = folder if not bundle.is_file() and folder.is_dir() else bundle
        if not path.is_file():
            folder = path if path.is_dir() or path.suffix.lower() != '.pt' else path.with_suffix('')
            bundle = folder.with_name(f'{folder.name}.pt')
            if bundle.is_file():
                path = bundle
            elif (folder.parent / f'.{folder.name}.incomplete').exists():
                raise OSError(f'incomplete checkpoint without a committed bundle: {folder}')
        if path.is_dir():
            payload_path = path / 'payload.pt'
            if payload_path.is_file():
                loaded = torch.load(payload_path, map_location=self.device, weights_only=False, **pickle_options)
                return loaded if isinstance(loaded, dict) else {'model': loaded}
            assembled: dict[str, Any] = {}
            meta_json = path / 'meta.json'
            if meta_json.is_file():
                assembled.update(json.loads(_read_text(meta_json)))
            meta_pt = path / 'meta.pt'
            if meta_pt.is_file():
                extra = torch.load(meta_pt, map_location=self.device, weights_only=False, **pickle_options)
                if isinstance(extra, dict):
                    assembled.update(extra)
            for key in ('model', 'optimizer', 'scheduler'):
                piece = path / f'{key}.pt'
                if piece.is_file():
                    assembled[key] = torch.load(piece, map_location=self.device, weights_only=False, **pickle_options)
            for key in ('tracker', 'logger'):
                piece = path / f'{key}.json'
                if piece.is_file():
                    assembled[key] = json.loads(_read_text(piece))
            return assembled or None
        if not path.is_file():
            return None
        loaded = torch.load(path, map_location=self.device, weights_only=False, **pickle_options)
        return loaded if isinstance(loaded, dict) else {'model': loaded}

    def to_result_snapshot(self) -> dict[str, Any]:
        return {'device': self.device, **self.meta}


class SystemFactory:
    @staticmethod
    def build(system_config: SystemConfig, assets_dir: Path | str) -> System:
        return _build_native(system_config, Path(assets_dir))


def _build_native(system_config: SystemConfig, assets_dir: Path) -> System:
    device = str(system_config.setting('device', 'cpu'))
    logger = Logger(assets_dir)
    deterministic = bool(system_config.setting('deterministic', False))
    return System(
        device=device,
        logger=logger,
        assets_dir=assets_dir,
        study_root=system_config.setting('study_root'),
        source_dir=system_config.setting('source_dir'),
        run_id=system_config.setting('run_id'),
        train_run_dir=system_config.setting('train_run_dir'),
        meta={
            'ready': True,
            'source': system_config.source or 'native',
            'deterministic': deterministic,
            'cudnn_benchmark': system_config.setting('cudnn_benchmark'),
            'cudnn_deterministic': system_config.setting('cudnn_deterministic'),
        },
    )
