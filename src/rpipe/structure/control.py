"""Pure experiment conditions, configuration resolution and content identity."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any


class AdmissionError(ValueError):
    """A declaration or request cannot safely be accepted."""


class SourceMismatch(AdmissionError):
    """The declared and observed configuration or source differ."""


class BusyError(AdmissionError):
    """Another or unresolved execution owns the target Run."""


class ContractError(AdmissionError):
    """An effective experiment condition violates its contract."""


def _mapping(value: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(f'{name} must be a mapping')
    if any(not isinstance(key, str) for key in value):
        raise ContractError(f'{name} keys must be strings')
    return dict(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in _mapping(value, 'configuration').items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError('configuration numbers must be finite')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ContractError(f'unsupported configuration value: {type(value).__name__}')


def get_setting(mapping: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Nested content configuration takes precedence over source-specific extras."""
    body = _mapping(mapping, 'layer')
    config = _mapping(body.get('config'), 'layer config')
    return config[key] if key in config else body.get(key, default)


def emit(known: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in known.items() if not (value is None or value == {} or value == [])}
    out.update(extras)
    return out


def _layer_from_mapping(cls: Any, mapping: Mapping[str, Any] | None, names: tuple[str, ...]) -> Any:
    body = _mapping(mapping, 'layer')
    _json_value(body)
    known = {*names, 'config'}
    extras = {key: value for key, value in body.items() if key not in known}
    return cls(**{name: body.get(name) for name in names},
               config=_mapping(body.get('config'), 'layer config'), extras=extras)


def _layer_mapping(layer: Any, names: tuple[str, ...]) -> dict[str, Any]:
    known = {name: getattr(layer, name) for name in names}
    known['config'] = dict(layer.config) if layer.config else None
    result = emit(known, layer.extras)
    _json_value(result)
    return result


def deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge mappings; explicit leaves and lists replace base values."""
    if not isinstance(base, Mapping) or not isinstance(patch, Mapping):
        return patch
    out = dict(base)
    for key, value in patch.items():
        out[key] = deep_merge(out[key], value) if key in out else value
    return out


def compute_run_id(mapping: Mapping[str, Any], *, length: int = 16) -> str:
    """Hash finite JSON content, excluding only id and human description."""
    if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= 64:
        raise ContractError('identity length must be an integer from 1 to 64')
    body = _mapping(mapping, 'configuration')
    _json_value(body)
    content = {key: value for key, value in body.items() if key not in {'id', 'description'}}
    encoded = json.dumps(_json_value(content), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:length]


@dataclass
class DataConfig:
    name: str | None = None
    source: str | None = None
    path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> DataConfig:
        body = _mapping(mapping, 'data')
        if 'origin' in body:
            raise ContractError('origin is set on the Study, not under data')
        return _layer_from_mapping(cls, body, ('name', 'source', 'path'))

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
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> ModelConfig:
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
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> AlgorithmConfig:
        return _layer_from_mapping(cls, mapping, ('mode', 'source', 'path'))

    def to_mapping(self) -> dict[str, Any]:
        return _layer_mapping(self, ('mode', 'source', 'path'))

    def setting(self, key: str, default: Any = None) -> Any:
        return self.config[key] if key in self.config else self.extras.get(key, default)


@dataclass
class SystemConfig:
    source: str | None = None
    path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> SystemConfig:
        return _layer_from_mapping(cls, mapping, ('source', 'path'))

    def to_mapping(self) -> dict[str, Any]:
        return _layer_mapping(self, ('source', 'path'))

    def setting(self, key: str, default: Any = None) -> Any:
        return self.config[key] if key in self.config else self.extras.get(key, default)


_LAYER_KEYS = frozenset({'seed', 'experiment', 'description', 'tags', 'data', 'model', 'algorithm', 'system'})
_EXTRA_KEYS = frozenset({'version', 'origin'})
_VALID_MODES = frozenset({'train', 'eval', 'inference'})


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ContractError('tags must be a list of strings')
    return sorted(set(value))


@dataclass
class RunConfig:
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
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> RunConfig:
        body = _mapping(mapping, 'Run configuration')
        _json_value(body)
        unknown = set(body) - _LAYER_KEYS - _EXTRA_KEYS - {'id'}
        if unknown:
            raise ContractError(f'unknown Run configuration fields: {sorted(unknown)}')
        identifier = body.get('id', '')
        if not isinstance(identifier, str):
            raise ContractError('run id must be a string')
        run = cls(id=identifier, seed=body.get('seed'), experiment=body.get('experiment'),
                  description=body.get('description'), tags=_normalize_tags(body.get('tags')),
                  data=DataConfig.from_mapping(body.get('data')),
                  model=ModelConfig.from_mapping(body.get('model')),
                  algorithm=AlgorithmConfig.from_mapping(body.get('algorithm')),
                  system=SystemConfig.from_mapping(body.get('system')),
                  extras={key: body[key] for key in body if key in _EXTRA_KEYS})
        expected = compute_run_id(run.to_mapping())
        if identifier and identifier != expected:
            raise ContractError('run id does not match its effective configuration')
        run.id = expected
        validate_run_config(run)
        return run

    def to_mapping(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.id:
            out['id'] = self.id
        for name in ('seed', 'experiment', 'description'):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.tags:
            out['tags'] = list(self.tags)
        for name in ('data', 'model', 'algorithm', 'system'):
            out[name] = getattr(self, name).to_mapping()
        out.update(self.extras)
        return out


@dataclass
class Control:
    """One resolved Run configuration, separate from scientific runtime objects."""

    run: RunConfig

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
        return self.run.to_mapping()


def resolve_control(base: Mapping[str, Any], factors: Mapping[str, Any] | None = None,
                    *, seed: int | None = None) -> Control:
    """Merge explicit overrides onto base, without mutating either input."""
    base = copy.deepcopy(_json_value(_mapping(base, 'base configuration')))
    patch = copy.deepcopy(_json_value(_mapping(factors, 'factors')))
    for key in list(patch):
        if '.' not in key:
            continue
        value = patch.pop(key)
        parts = key.split('.')
        if any(not part for part in parts):
            raise ContractError(f'invalid factor path: {key}')
        target = patch
        for part in parts[:-1]:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise ContractError(f'factor conflicts with a scalar: {key}')
        target[parts[-1]] = value
    effective = deep_merge(base, patch)
    if seed is not None:
        effective['seed'] = seed
    if factors is not None or seed is not None:
        effective.pop('id', None)
    control = Control(RunConfig.from_mapping(effective))
    validate_control(control)
    return control


def validate_run_config(run: RunConfig, *, require_mode: bool = False) -> None:
    body = run.to_mapping()
    _json_value(body)
    if not run.id or run.id != compute_run_id(body):
        raise ContractError('run id must match its effective configuration')
    if run.seed is not None and (isinstance(run.seed, bool) or not isinstance(run.seed, int) or run.seed < 0):
        raise ContractError('seed must be a nonnegative integer')
    for name in ('experiment', 'description'):
        value = getattr(run, name)
        if value is not None and not isinstance(value, str):
            raise ContractError(f'{name} must be a string')
    if require_mode and not run.algorithm.mode:
        raise ContractError('algorithm.mode is required')
    if run.algorithm.mode is not None and run.algorithm.mode not in _VALID_MODES:
        raise ContractError(f'invalid algorithm.mode: {run.algorithm.mode!r}')


def validate_control(control: Control, *, require_mode: bool = False) -> None:
    validate_run_config(control.run, require_mode=require_mode)


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
    return value


def _component(value: Any, name: str) -> None:
    if (not isinstance(value, str) or not value or value in {'.', '..'}
            or value != value.strip() or PureWindowsPath(value).is_reserved()
            or PureWindowsPath(value).drive or value[-1] in {' ', '.'}
            or any(char in value for char in '/\\\x00:*?"<>|')
            or any(ord(char) < 32 for char in value)):
        raise AdmissionError(f'invalid {name}')


@dataclass(frozen=True)
class RunRef:
    """A saved Run identity and location; construction never accesses storage."""

    id: str
    seed: int
    experiment: str
    study_root: Path
    run_root: Path
    source_digest: str
    config_digest: str
    version: str = 'default'
    train_id: str | None = None

    def __post_init__(self) -> None:
        for name in ('id', 'version'):
            _component(getattr(self, name), name)
        if self.train_id is not None:
            _component(self.train_id, 'train_id')
        if type(self.seed) is not int or self.seed < 0:
            raise AdmissionError('seed must be a nonnegative integer')
        if not isinstance(self.experiment, str) or not self.experiment:
            raise AdmissionError('experiment must be an explicit group identity')
        for name in ('study_root', 'run_root'):
            path = Path(getattr(self, name))
            if not path.is_absolute() or '..' in path.parts:
                raise AdmissionError(f'{name} must be an explicit absolute path without parent traversal')
            object.__setattr__(self, name, path)
        if self.study_root.parent.parent != self.run_root or self.study_root.name != self.version:
            raise AdmissionError('Study location must belong to its explicit output root and version')
        for name in ('source_digest', 'config_digest'):
            value = getattr(self, name)
            if (not isinstance(value, str) or len(value) != 64
                    or any(character not in '0123456789abcdef' for character in value)):
                raise AdmissionError(f'{name} must be a SHA-256 hex digest')

    @property
    def identity(self) -> dict[str, str]:
        return {'id': self.id, 'source_digest': self.source_digest,
                'config_digest': self.config_digest, 'version': self.version}

    @property
    def run_dir(self) -> Path:
        return self.study_root / 'runs' / self.id

    @property
    def result_path(self) -> Path:
        return self.run_dir / 'result.json'

    @property
    def assets_dir(self) -> Path:
        return self.run_dir / 'assets'


@dataclass(frozen=True)
class RunSpec:
    id: str
    seed: int
    config: Mapping[str, Any]
    source_dir: Path
    run_root: Path
    source_digest: str
    version: str = 'default'
    train_id: str | None = None
    experiment: str = field(init=False)

    def __post_init__(self) -> None:
        configuration = _json_value(_mapping(self.config, 'Run configuration'))
        validated = RunConfig.from_mapping(configuration)
        if (validated.id != self.id or configuration.get('id') != self.id
                or configuration.get('seed') != self.seed):
            raise AdmissionError('Run identity/seed must match the effective configuration')
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise AdmissionError('seed must be a nonnegative integer')
        for name in ('id', 'version'):
            _component(getattr(self, name), name)
        if self.train_id is not None:
            _component(self.train_id, 'train_id')
        for name in ('source_dir', 'run_root'):
            path = Path(getattr(self, name))
            if not path.is_absolute() or '..' in path.parts:
                raise AdmissionError(f'{name} must be an explicit absolute path without parent traversal')
            object.__setattr__(self, name, path)
        if (not isinstance(self.source_digest, str) or len(self.source_digest) != 64
                or any(char not in '0123456789abcdef' for char in self.source_digest)):
            raise AdmissionError('source_digest must be a SHA-256 hex digest')
        object.__setattr__(self, 'config', _freeze(configuration))
        conditions = self.to_config()
        for key in ('id', 'seed', 'description', 'tags'):
            conditions.pop(key, None)
        group = compute_run_id({'conditions': conditions, 'source_digest': self.source_digest,
                                'version': self.version})
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

    @property
    def ref(self) -> RunRef:
        config = json.dumps(self.to_config(), sort_keys=True, separators=(',', ':'), allow_nan=False)
        return RunRef(self.id, self.seed, self.experiment, self.study_root, self.run_root,
                      self.source_digest, hashlib.sha256(config.encode()).hexdigest(),
                      self.version, self.train_id)

    def to_config(self) -> dict[str, Any]:
        return thaw(self.config)
