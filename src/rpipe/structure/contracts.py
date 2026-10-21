"""Pure execution and scientific configuration contracts. No runtime imports."""
from __future__ import annotations
import copy
import math
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from threading import Event
from types import MappingProxyType
from typing import Protocol

import hashlib
import json
from typing import Any
from dataclasses import fields as _fields
from dataclasses import dataclass, field, fields

def get_setting(mapping: dict[str, Any], key: str, default: Any=None) -> Any:
    """Read raw settings with the same config-before-extras precedence as runtime."""
    config = mapping.get('config') or {}
    if not isinstance(config, dict):
        raise ValueError('layer config must be a mapping')
    return config[key] if key in config else mapping.get(key, default)

def split_known(mapping: dict[str, Any], known: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    body = dict(mapping or {})
    extras = {k: body.pop(k) for k in list(body) if k not in known}
    return (body, extras)

def emit(known: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in known.items() if not (value is None or value == {} or value == [])}
    out.update(extras)
    return out

def _layer_from_mapping(cls: Any, mapping: dict[str, Any] | None, names: tuple[str, ...]) -> Any:
    body, extras = split_known(mapping, {f.name for f in _fields(cls) if f.name != 'extras'})
    return cls(**{name: body.get(name) for name in names}, config=dict(body.get('config') or {}), extras=extras)

def _layer_mapping(layer: Any, names: tuple[str, ...]) -> dict[str, Any]:
    known = {name: getattr(layer, name) for name in names}
    known['config'] = dict(layer.config) if layer.config else None
    return emit(known, layer.extras)

def deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge patch onto base. Patch wins on conflicts; lists replace."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = dict(base)
    for key, value in patch.items():
        out[key] = deep_merge(out[key], value) if key in out else value
    return out

_HASH_EXCLUDE = frozenset({'id', 'description'})

def canonical_json(mapping: dict[str, Any]) -> str:
    """Stable JSON: sorted keys, exclude id/description, compact separators."""
    body = {k: v for k, v in mapping.items() if k not in _HASH_EXCLUDE}
    return json.dumps(body, sort_keys=True, separators=(',', ':'), default=str)

def compute_content_id(mapping: dict[str, Any], *, length: int=16) -> str:
    """SHA-256 hex digest of canonical content, truncated to ``length`` chars."""
    digest = hashlib.sha256(canonical_json(mapping).encode('utf-8')).hexdigest()
    return digest[:length]

def compute_run_id(mapping: dict[str, Any], *, length: int=16) -> str:
    return compute_content_id(mapping, length=length)

def compute_index_id(mapping: dict[str, Any], *, length: int=16) -> str:
    return compute_content_id(mapping, length=length)

@dataclass
class DataConfig:
    name: str | None = None
    source: str | None = None
    path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> DataConfig:
        known_names = {f.name for f in fields(cls) if f.name != 'extras'}
        body, extras = split_known(mapping, known_names)
        if body.get('origin') not in (None, '') or extras.get('origin') not in (None, ''):
            raise ValueError('origin is set on the Study, not under data')
        return cls(name=body.get('name'), source=body.get('source'), path=body.get('path'), config=dict(body.get('config') or {}), extras=extras)

    def to_mapping(self) -> dict[str, Any]:
        return _layer_mapping(self, ('name', 'source', 'path'))

@dataclass
class ModelConfig:
    name: str | None = None
    source: str | None = None
    path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> ModelConfig:
        return _layer_from_mapping(cls, mapping, ('name', 'source', 'path'))

    def to_mapping(self) -> dict[str, Any]:
        return _layer_mapping(self, ('name', 'source', 'path'))

@dataclass
class AlgorithmConfig:
    mode: str | None = None
    source: str | None = None
    path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> AlgorithmConfig:
        return _layer_from_mapping(cls, mapping, ('mode', 'source', 'path'))

    def to_mapping(self) -> dict[str, Any]:
        return _layer_mapping(self, ('mode', 'source', 'path'))

    def setting(self, key: str, default: Any=None) -> Any:
        if key in self.config:
            return self.config[key]
        return self.extras.get(key, default)

@dataclass
class SystemConfig:
    source: str | None = None
    path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> SystemConfig:
        return _layer_from_mapping(cls, mapping, ('source', 'path'))

    def to_mapping(self) -> dict[str, Any]:
        return _layer_mapping(self, ('source', 'path'))

    def setting(self, key: str, default: Any=None) -> Any:
        if key in self.config:
            return self.config[key]
        return self.extras.get(key, default)

_IGNORE_TOP = frozenset({'slug', 'control_slug', 'raw'})

_LAYER_KEYS = frozenset({'seed', 'experiment', 'description', 'tags', 'data', 'model', 'algorithm', 'system'})

def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value] if value else []
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise TypeError(f'tags must be a list of strings, got {type(value)!r}')
    return sorted(set(items))

def _config_mapping(config: Any, out: dict[str, Any]) -> dict[str, Any]:
    if config.seed is not None:
        out['seed'] = config.seed
    if config.experiment is not None:
        out['experiment'] = config.experiment
    if config.description is not None:
        out['description'] = config.description
    if config.tags:
        out['tags'] = list(config.tags)
    out['data'] = config.data.to_mapping()
    out['model'] = config.model.to_mapping()
    out['algorithm'] = config.algorithm.to_mapping()
    out['system'] = config.system.to_mapping()
    out.update(config.extras)
    return out

@dataclass
class ExperimentConfig:
    """Experiment-level base config (no run id)."""
    seed: int | None = None
    experiment: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    algorithm: AlgorithmConfig = field(default_factory=AlgorithmConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> ExperimentConfig:
        body = {k: v for k, v in dict(mapping or {}).items() if k not in _IGNORE_TOP and k != 'id'}
        extras = {k: body.pop(k) for k in list(body) if k not in _LAYER_KEYS}
        return cls(seed=body.get('seed'), experiment=body.get('experiment'), description=body.get('description'), tags=_normalize_tags(body.get('tags')), data=DataConfig.from_mapping(body.get('data') or {}), model=ModelConfig.from_mapping(body.get('model') or {}), algorithm=AlgorithmConfig.from_mapping(body.get('algorithm') or {}), system=SystemConfig.from_mapping(body.get('system') or {}), extras=extras)

    def to_mapping(self) -> dict[str, Any]:
        return _config_mapping(self, {})

@dataclass
class RunConfig:
    """Run-level config; id is content hash of fields except id/description."""
    id: str = ''
    seed: int | None = None
    experiment: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    algorithm: AlgorithmConfig = field(default_factory=AlgorithmConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None, *, assign_id: bool=True) -> RunConfig:
        body = {k: v for k, v in dict(mapping or {}).items() if k not in _IGNORE_TOP}
        run_id = str(body.pop('id')) if body.get('id') not in (None, '') else ''
        extras = {k: body.pop(k) for k in list(body) if k not in _LAYER_KEYS}
        run = cls(id=run_id, seed=body.get('seed'), experiment=body.get('experiment'), description=body.get('description'), tags=_normalize_tags(body.get('tags')), data=DataConfig.from_mapping(body.get('data') or {}), model=ModelConfig.from_mapping(body.get('model') or {}), algorithm=AlgorithmConfig.from_mapping(body.get('algorithm') or {}), system=SystemConfig.from_mapping(body.get('system') or {}), extras=extras)
        if assign_id and (not run.id):
            run.id = compute_run_id(run.to_mapping())
        return run

    def to_mapping(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.id:
            out['id'] = self.id
        return _config_mapping(self, out)

    def with_computed_id(self) -> RunConfig:
        mapping = self.to_mapping()
        mapping.pop('id', None)
        return RunConfig.from_mapping(mapping, assign_id=True)

def experiment_config_from_mapping(mapping: dict[str, Any]) -> ExperimentConfig:
    return ExperimentConfig.from_mapping(mapping)

def run_config_from_merge(experiment: ExperimentConfig | dict[str, Any], patch: dict[str, Any] | None=None) -> RunConfig:
    """Merge experiment base ⊕ patch, then hash id."""
    base = experiment.to_mapping() if isinstance(experiment, ExperimentConfig) else ExperimentConfig.from_mapping(experiment).to_mapping()
    merged = deep_merge(base, dict(patch or {}))
    merged.pop('id', None)
    return RunConfig.from_mapping(merged, assign_id=True)

def run_config_to_mapping(run: RunConfig) -> dict[str, Any]:
    return run.to_mapping()

@dataclass
class Control:
    """Structure-side handle for one Run's resolved RunConfig."""
    run: RunConfig
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.run.id

    @property
    def seed(self) -> int | None:
        return self.run.seed

    @property
    def experiment(self) -> str | None:
        return self.run.experiment

    @property
    def data(self) -> dict[str, Any]:
        return self.run.data.to_mapping()

    @property
    def model(self) -> dict[str, Any]:
        return self.run.model.to_mapping()

    @property
    def algorithm(self) -> dict[str, Any]:
        return self.run.algorithm.to_mapping()

    @property
    def system(self) -> dict[str, Any]:
        return self.run.system.to_mapping()

    def to_dict(self) -> dict[str, Any]:
        return run_config_to_mapping(self.run)

def control_from_config(cfg: dict[str, Any], run_dir: str | None=None) -> Control:
    """Build Control from Artifact Config mapping (prepare reads Config).

    ``run_dir`` is accepted for call-site compatibility (Artifact directory name)
    but does not override content-hash ``id``.
    """
    del run_dir
    run = RunConfig.from_mapping(cfg, assign_id=True)
    return Control(run=run, raw=dict(cfg))

def control_from_run_config(run: RunConfig) -> Control:
    return Control(run=run, raw=run.to_mapping())

def control_to_config(control: Control) -> dict[str, Any]:
    return control.to_dict()

_VALID_MODES = frozenset({'train', 'eval', 'inference'})

class ContractError(ValueError):
    """RunConfig / Control failed contract checks."""

def validate_run_config(run: RunConfig, *, require_mode: bool=False) -> None:
    if not run.id:
        raise ContractError('run id is required (content hash)')
    if require_mode:
        if not run.algorithm.mode:
            raise ContractError('algorithm.mode is required')
        if run.algorithm.mode not in _VALID_MODES:
            raise ContractError(f'invalid algorithm.mode: {run.algorithm.mode!r}')
    elif run.algorithm.mode is not None and run.algorithm.mode not in _VALID_MODES:
        raise ContractError(f'invalid algorithm.mode: {run.algorithm.mode!r}')

def validate_control(control: Control, *, require_mode: bool=False) -> None:
    validate_run_config(control.run, require_mode=require_mode)

def validate_result_draft(result: dict[str, Any]) -> None:
    """Minimal Result shape check (object with str keys + status)."""
    if not isinstance(result, dict):
        raise ContractError('result must be a mapping')
    for key in result:
        if not isinstance(key, str):
            raise ContractError('result keys must be strings')
    status = result.get('status')
    if status not in {'succeeded', 'failed'}:
        raise ContractError(f'invalid result status: {status!r}')
    if status == 'failed' and (not result.get('error')):
        raise ContractError('failed result requires error')


class AdmissionError(ValueError):
    """An execution request cannot safely be admitted."""


class SourceMismatch(AdmissionError):
    """Stored or planned source/configuration differs from the actual input."""


class BusyError(AdmissionError):
    """Another or unresolved execution owns this Run."""


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return copy.deepcopy(value)


@dataclass(frozen=True)
class Budget:
    """Hard limits. An unsupported limit is rejected before execution."""

    timeout_seconds: float | None = None
    memory_bytes: int | None = None

    def validate(self) -> None:
        for name, value in vars(self).items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value <= 0):
                raise AdmissionError(f'{name} must be finite and positive')
        if self.memory_bytes is not None and not isinstance(self.memory_bytes, int):
            raise AdmissionError('memory_bytes must be an integer')


class Cancellation:
    def __init__(self) -> None:
        self._event = Event()

    def request(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def requested(self) -> bool:
        return self.is_set()


@dataclass(frozen=True)
class RunSpec:
    id: str
    experiment: str
    seed: int
    config: Mapping[str, Any]
    source_dir: Path
    run_root: Path
    source_digest: str
    version: str = 'default'
    train_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'config', _freeze(self.config))
        object.__setattr__(self, 'source_dir', Path(self.source_dir).resolve())
        object.__setattr__(self, 'run_root', Path(self.run_root).resolve())
        for name in ('id', 'version', 'train_id'):
            value = getattr(self, name)
            if name == 'train_id' and value is None:
                continue
            if (not isinstance(value, str) or not value or value in {'.', '..'}
                    or value != value.strip() or PureWindowsPath(value).is_reserved()
                    or PureWindowsPath(value).drive or value[-1] in {' ', '.'}
                    or any(c in value for c in '/\\\x00:*?"<>|')
                    or any(ord(c) < 32 for c in value)):
                raise AdmissionError(f'invalid {name}')
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise AdmissionError('seed must be a nonnegative integer')
        if self.config.get('id') != self.id or self.config.get('seed') != self.seed:
            raise AdmissionError('Run identity/seed must match the effective configuration')
        if self.run_root.is_relative_to(self.source_dir) or self.source_dir.is_relative_to(self.run_root):
            raise AdmissionError('run_root must be separate from the source Study')
        conditions = self.to_config()
        for key in ('id', 'seed', 'description', 'tags'):
            conditions.pop(key, None)
        group = compute_run_id({'conditions': conditions, 'source_digest': self.source_digest, 'version': self.version})
        object.__setattr__(self, 'experiment', str(conditions.get('experiment', self.source_dir.name)) + ':' + group)

    @property
    def study_root(self) -> Path:
        return self.run_root / self.source_dir.name / self.version

    @property
    def run_dir(self) -> Path:
        return self.study_root / 'runs' / self.id

    @property
    def result_path(self) -> Path:
        return self.run_dir / 'result.json'

    @property
    def assets_dir(self) -> Path:
        return self.run_dir / 'assets'

    def to_config(self) -> dict[str, Any]:
        return thaw(self.config)


@dataclass
class RunOutcome:
    status: str
    quality: bool | None = None
    evidence_complete: bool = False
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    cleanup_errors: tuple[dict[str, Any], ...] = ()
    cancelled: bool = False


class Payload(Protocol):
    def prepare(self, ctx: Any) -> None: ...
    def execute(self, ctx: Any) -> None: ...
    def collect(self, ctx: Any) -> dict[str, Any]: ...
    def close(self, ctx: Any) -> None: ...
