"""Compare declared saved Runs or explicitly supplied in-memory observations."""
from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import sys
from typing import Any

from ..control import AdmissionError, RunRef, _component
from . import io, readout


CHECKPOINT_PARTS = ('model', 'optimizer', 'scheduler')


def _tolerances(atol: float, rtol: float) -> None:
    if any(isinstance(value,bool) or not isinstance(value,(int,float))
           or not math.isfinite(value) or value < 0 for value in (atol,rtol)):
        raise AdmissionError('atol and rtol must be finite and >= 0')


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _close(a: float, b: float, atol: float, rtol: float) -> tuple[bool, float]:
    if isinstance(a, int) and isinstance(b, int):
        diff = abs(a - b)
        return diff <= Fraction(atol) + Fraction(rtol) * abs(b), diff
    if isinstance(a, complex) or isinstance(b, complex):
        # Finite overflowing tensor cells: square the bound without taking a root.
        real = Fraction(a.real) - Fraction(b.real)
        imag = Fraction(a.imag) - Fraction(b.imag)
        distance2 = real**2 + imag**2
        reference2 = Fraction(b.real)**2 + Fraction(b.imag)**2
        absolute, relative = Fraction(atol), Fraction(rtol)
        remainder = distance2 - absolute**2 - relative**2 * reference2
        try:
            diff = math.hypot(float(real), float(imag))
        except OverflowError:
            diff = math.inf
        return remainder <= 0 or remainder**2 <= 4 * absolute**2 * relative**2 * reference2, diff
    if isinstance(a, int) or isinstance(b, int):
        floating = b if isinstance(a, int) else a
        if not math.isfinite(floating):
            return False, math.inf
        distance = abs(Fraction(a) - Fraction(b))
        try:
            diff = float(distance)
        except OverflowError:
            diff = math.inf
        return distance <= Fraction(atol) + Fraction(rtol) * abs(Fraction(b)), diff
    if math.isnan(a) or math.isnan(b):
        same = math.isnan(a) and math.isnan(b)
        return same, 0.0 if same else math.inf
    if math.isinf(a) or math.isinf(b):
        return a == b, 0.0 if a == b else math.inf
    diff = abs(a - b)
    bound = atol + rtol * abs(b)
    if not math.isfinite(diff) or not math.isfinite(bound):
        return abs(Fraction(a) - Fraction(b)) <= Fraction(atol) + Fraction(rtol) * abs(Fraction(b)), diff
    return diff <= bound, diff


def _item(passed: bool, max_abs_diff: float, **extra: Any) -> dict[str, Any]:
    return {'passed': passed, 'max_abs_diff': max_abs_diff, **extra}


def _missing(where: str) -> dict[str, Any]:
    return {'passed': False, 'max_abs_diff': None, 'missing': where}


def _tree(a: Any, b: Any, atol: float, rtol: float, path: str, problems: list[str]) -> float:
    """Return the max abs diff; append mismatch paths to ``problems``."""
    torch = sys.modules.get('torch')
    if torch is not None and isinstance(a, torch.Tensor) and isinstance(b, torch.Tensor):
        if tuple(a.shape) != tuple(b.shape):
            problems.append(f'{path}: shape {tuple(a.shape)} != {tuple(b.shape)}')
            return math.inf
        if a.dtype == torch.bool or b.dtype == torch.bool:
            if a.dtype != b.dtype or not torch.equal(a, b):
                problems.append(f'{path}: boolean tensor state differs')
                return math.inf
            return 0.0
        if a.numel() == 0:
            return 0.0
        if not (a.is_floating_point() or a.is_complex() or b.is_floating_point() or b.is_complex()):
            if atol == rtol == 0:
                if torch.equal(a, b):
                    return 0.0
                problems.append(f'{path}: integer/bool tensor values differ')
                return math.inf
            if a.dtype != torch.bool and b.dtype != torch.bool:
                return _tree(a.tolist(), b.tolist(), atol, rtol, path, problems)
        dtype = torch.complex128 if a.is_complex() or b.is_complex() else torch.float64
        original_left, original_right = a.detach().cpu(), b.detach().cpu()
        left, right = original_left.to(dtype), original_right.to(dtype)
        both_nan = torch.isnan(left) & torch.isnan(right)
        same = both_nan | (left == right)
        nonfinite = ~torch.isfinite(left) | ~torch.isfinite(right)
        diff = (left - right).abs().masked_fill(same, 0.0).masked_fill(nonfinite & ~same, math.inf)
        bound = (atol + rtol * right.abs()).masked_fill(nonfinite, 0.0)
        mixed = (a.dtype != torch.bool and b.dtype != torch.bool
                 and (a.is_floating_point() or a.is_complex()) != (b.is_floating_point() or b.is_complex()))
        precise = ~nonfinite & (~torch.isfinite(diff) | ~torch.isfinite(bound) | mixed)
        different = diff > bound
        for index in precise.reshape(-1).nonzero().reshape(-1).tolist():
            passed, distance = _close(
                original_left.reshape(-1)[index].item(), original_right.reshape(-1)[index].item(), atol, rtol)
            different.reshape(-1)[index] = not passed
            diff.reshape(-1)[index] = distance
        diff = diff.masked_fill(precise & torch.isnan(diff), math.inf)
        worst = float(diff.max())
        if math.isnan(worst) or bool(different.any()):
            problems.append(f'{path}: max abs diff {worst:g}')
        return worst
    if isinstance(a, dict) and isinstance(b, dict):
        worst = 0.0
        for key in sorted(set(a) | set(b), key=str):
            if key not in a or key not in b:
                problems.append(f'{path}.{key}: only in {"a" if key in a else "b"}')
                worst = math.inf
                continue
            worst = max(worst, _tree(a[key], b[key], atol, rtol, f'{path}.{key}', problems))
        return worst
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            problems.append(f'{path}: length {len(a)} != {len(b)}')
            return math.inf
        worst = 0.0
        for i, (x, y) in enumerate(zip(a, b)):
            worst = max(worst, _tree(x, y, atol, rtol, f'{path}[{i}]', problems))
        return worst
    if isinstance(a, bool) or isinstance(b, bool):
        if type(a) is not type(b) or a != b:
            problems.append(f'{path}: boolean state differs')
            return math.inf
        return 0.0
    if _number(a) and _number(b):
        ok, diff = _close(a, b, atol, rtol)
        if not ok:
            problems.append(f'{path}: {a!r} != {b!r}')
        return diff
    if a != b:
        problems.append(f'{path}: {a!r} != {b!r}')
        return math.inf
    return 0.0


def compare_state(left: Any, right: Any, *, atol: float = 0.0, rtol: float = 0.0,
                  label: str = 'state') -> dict[str, Any]:
    _tolerances(atol, rtol)
    problems: list[str] = []
    try:
        worst = _tree(left, right, atol, rtol, label, problems)
    except OverflowError as exc:
        raise ValueError(f'cannot compare {label}: {exc}') from exc
    return _item(not problems, worst, problems=problems[:20], n_problems=len(problems))


def _conditions(ref: RunRef) -> dict:
    config = readout.read_config(ref)
    for key in ('id','description','tags'):
        config.pop(key,None)
    def flatten(value,prefix=''):
        out = {}
        for key,item in value.items():
            name = f'{prefix}.{key}' if prefix else key
            out.update(flatten(item,name) if isinstance(item,dict) else {name:item})
        return out
    return {**flatten(config),'source_digest':ref.source_digest,'version':ref.version}


def _checkpoint(ref: RunRef, name: str) -> dict | None:
    from .checkpoint import load_snapshot
    root = io.contained(ref.assets_dir / io.CHECKPOINTS,ref.run_dir)
    path = io.contained(root / f'{name}.pt',root)
    if not path.is_file():
        if path.exists():
            raise AdmissionError('checkpoint must be a regular snapshot file')
        return None
    return load_snapshot(path,map_location='cpu')


def compare(left: RunRef, right: RunRef, *, atol: float = 0.0, rtol: float = 0.0,
            checkpoints: list[str] | None = None,
            differences: tuple[str,...] | None = None) -> dict[str,Any]:
    _tolerances(atol,rtol)
    if not isinstance(left,RunRef) or not isinstance(right,RunRef):
        raise AdmissionError('comparison requires two explicit saved RunRefs')
    if checkpoints is not None and not isinstance(checkpoints,(list,tuple)):
        raise AdmissionError('checkpoints must be an explicit list of snapshot names')
    selected = list(checkpoints) if checkpoints is not None else []
    for name in selected:
        _component(name,'checkpoint name')
    if len(set(selected)) != len(selected):
        raise AdmissionError('duplicate checkpoint comparison request')
    lc,rc = _conditions(left),_conditions(right)
    changed = {}
    for name in sorted(set(lc)|set(rc)):
        if (name not in lc or name not in rc
                or json.dumps(lc[name],sort_keys=True) != json.dumps(rc[name],sort_keys=True)):
            changed[name] = {'left':lc.get(name),'right':rc.get(name),
                             'left_present':name in lc,'right_present':name in rc}
    if differences is not None:
        if not isinstance(differences,(tuple,list)) or any(not isinstance(name,str) for name in differences):
            raise AdmissionError('condition declarations must be a list of field names')
        if not set(changed).issubset(differences):
            raise AdmissionError('comparison has undeclared condition differences')
    a,b = readout.read_run(left),readout.read_run(right)
    ready_a = a.get('status')=='succeeded' and a.get('evidence_complete') is True
    ready_b = b.get('status')=='succeeded' and b.get('evidence_complete') is True
    items = {'records':_item(bool(ready_a and ready_b),0.0,
                              left_status=a.get('status'),right_status=b.get('status'))}
    ma,mb = a.get('metrics'),b.get('metrics')
    if not isinstance(ma,dict) or not ma or not isinstance(mb,dict) or not mb:
        items['metrics'] = _missing('a' if not isinstance(ma,dict) or not ma else 'b')
    else:
        items['metrics'] = compare_state(ma,mb,atol=atol,rtol=rtol,label='metrics')
        items['metrics']['keys'] = sorted(set(ma)|set(mb))
    history_a,history_b = readout.load_curve_series(left),readout.load_curve_series(right)
    items['history'] = (_missing('a' if not history_a else 'b') if not history_a or not history_b
                        else compare_state(history_a,history_b,atol=atol,rtol=rtol,label='history'))
    for name in selected:
        ca,cb = _checkpoint(left,name),_checkpoint(right,name)
        for part in CHECKPOINT_PARTS:
            label = f'checkpoint.{name}.{part}'
            if ca is None or cb is None or part not in ca or part not in cb:
                items[label] = _missing('a' if ca is None or part not in ca else 'b')
            else:
                items[label] = compare_state(ca[part],cb[part],atol=atol,rtol=rtol,label=part)
    return {'run_a':str(left.run_dir),'run_b':str(right.run_dir),
            'identity_a':left.identity,'identity_b':right.identity,
            'atol':atol,'rtol':rtol,'items':items,'passed':all(item['passed'] for item in items.values()),
            'condition_differences':changed,'conditions_equal':not changed,
            'scope':'selected saved observations; no claim of scientific equivalence'}


def format_compare(report: dict[str, Any]) -> str:
    lines = []
    for key, item in report['items'].items():
        state = 'pass' if item['passed'] else 'FAIL'
        if item.get('missing'):
            detail = f'missing in {item["missing"]}'
        else:
            difference = item['max_abs_diff']
            value = str(difference) if isinstance(difference, int) else f'{difference:g}'
            detail = f'max abs diff {value}'
        lines.append(f'{state}  {_text_cell(key)}  {detail}')
    lines.append('passed' if report['passed'] else 'failed')
    return '\n'.join(lines) + '\n'


def write_compare(path: Path | str, report: dict[str, Any]) -> Path:
    target = Path(path)
    resolved = target.resolve()
    for name in ('run_a', 'run_b'):
        if name in report:
            run = Path(report[name])
            if not run.is_absolute() or run.parent.name != 'runs':
                raise AdmissionError('comparison Run paths must name explicit saved Runs')
            if resolved.is_relative_to(run.resolve().parent.parent):
                raise AdmissionError('comparison output must be outside input Study directories')
    source = report.get('source')
    if source is not None:
        if not isinstance(source, dict) or not isinstance(source.get('path'), str) or not source['path']:
            raise AdmissionError('comparison observation source must name an explicit file')
        observation = Path(source['path'])
        if (resolved == observation.resolve()
                or target.exists() and observation.exists() and target.samefile(observation)):
            raise AdmissionError('comparison output must not overwrite its observation source')

    def finite(value: Any) -> Any:
        return None if isinstance(value, float) and not math.isfinite(value) else value

    clean = {
        **report,
        'items': {
            key: {name: finite(value) for name, value in item.items()}
            for key, item in report['items'].items()
        },
    }
    text = json.dumps(clean, indent=2, ensure_ascii=False).encode('utf-8', errors='backslashreplace').decode('utf-8')
    return io.atomic_write_text(target, text)


def _text_cell(value: Any) -> str:
    return ' '.join(str(value).splitlines()).replace('\t', ' ').encode('utf-8', errors='backslashreplace').decode('utf-8')
