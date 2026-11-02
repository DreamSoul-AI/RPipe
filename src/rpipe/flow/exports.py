"""Derived curves and exports from the explicit published Run list."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from rpipe.structure.artifact.readout import (refs_from_index, read_run,
                                              load_curve_series, summarize_curve_series)
from rpipe.structure.artifact import io
from rpipe.structure.control import AdmissionError, SourceMismatch
from rpipe.structure.artifact.io import atomic_replace
DOCS_DIRNAME = "summary"
STATUS_SUCCEEDED = "succeeded"

FIGURES_DIRNAME = 'figures'
LEARNING_CURVES_NAME = 'learning_curves.png'

_PANELS = (
    ('test', 'Accuracy', 'Test accuracy'),
    ('train', 'Accuracy', 'Train accuracy'),
    ('test', 'Loss', 'Test loss'),
    ('train', 'Loss', 'Train loss'),
)


def figures_dir(study_dir: Path | str) -> Path:
    return Path(study_dir) / DOCS_DIRNAME / FIGURES_DIRNAME


def learning_curves_path(study_dir: Path | str) -> Path:
    return figures_dir(study_dir) / LEARNING_CURVES_NAME


def _label(factors: dict[str, Any]) -> str:
    if not factors:
        return 'experiment'
    parts: list[str] = []
    for key, value in factors.items():
        parts.append(f'{str(key).removesuffix(".name").split(".")[-1]}={value}')
    return ' '.join(parts)


def _export_snapshot(study, index=None, *, selected=None):
    path = io.contained(study / 'index.json', study)
    raw = path.read_bytes()
    saved = json.loads(raw)
    if index is not None and json.dumps(index, sort_keys=True, allow_nan=False) != json.dumps(saved, sort_keys=True, allow_nan=False):
        raise SourceMismatch('export requires the saved current Study index')
    refs = refs_from_index(study, saved)
    if selected is not None:
        if selected - {ref.id for ref in refs}:
            raise ValueError('selected Run is absent from the published plan')
        refs = tuple(ref for ref in refs if ref.id in selected)
    inputs = _export_inputs(study, refs)
    inputs['index_sha256'] = hashlib.sha256(raw).hexdigest()
    return saved, refs, inputs


def _export_inputs(study, refs):
    path = io.contained(study / 'index.json', study)
    scalars = {}
    for ref in refs:
        scalar = io.contained(ref.assets_dir / io.TRACKER_JSONL, study)
        scalars[ref.id] = hashlib.sha256(scalar.read_bytes()).hexdigest() if scalar.exists() else None
    return {'index_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'runs': [io.record_inputs(ref) for ref in refs], 'scalars_sha256': scalars}


def _check_export_inputs(study, refs, inputs):
    if _export_inputs(study, refs) != inputs:
        raise SourceMismatch('export inputs changed while processing')



def collect_curve_groups(study_dir: Path, index: dict[str, Any] | None) -> list[dict[str, Any]]:
    from rpipe.structure.artifact.io import load_index
    if index is None:
        index = load_index(study_dir)
    refs = refs_from_index(study_dir, index)
    factors = {row['id']: row['factors'] for row in index['experiments']} if index else {}
    groups = {}
    for ref in refs:
        conditions = factors[ref.experiment]
        if str(conditions.get('algorithm.mode', '')).lower() == 'eval':
            continue
        if read_run(ref)['status'] != STATUS_SUCCEEDED:
            continue
        group = groups.setdefault(ref.experiment, {'label': _label(conditions), 'refs': []})
        group['refs'].append(ref)
    return list(groups.values())


def write_learning_curves(
    study_dir: Path | str,
    index: dict[str, Any] | None,
    *,
    title: str | None = None,
) -> Path | None:
    """Write output ``summary/figures/learning_curves.png``; never source docs."""
    study_dir = Path(study_dir).resolve()
    index, refs, inputs = _export_snapshot(study_dir, index)
    groups = collect_curve_groups(study_dir, index)
    panels = {}
    for group in groups:
        for split, name, _title in _PANELS:
            series = [
                row for ref in group['refs']
                if (row := load_curve_series(ref).get(split, {}).get(name))
            ]
            summary = summarize_curve_series(series)
            if summary:
                for unit, values in (summary.get('by_unit') or {summary['unit']: summary}).items():
                    panels.setdefault((unit, split, name), []).append((group['label'], values))
    units = [unit for unit in ('step', 'epoch', 'observation') if any(key[0] == unit for key in panels)]
    if not units:
        return None

    dest = learning_curves_path(study_dir)
    if dest.resolve() != dest:
        raise AdmissionError('curve output must use its own summary path without aliases')
    io.contained(dest, study_dir)
    import matplotlib.pyplot as plt

    dest.parent.mkdir(parents=True, exist_ok=True)
    palette = plt.rcParams['axes.prop_cycle'].by_key()['color']
    tmp = dest.with_name(f'{uuid.uuid4().hex}.tmp')
    owned = False
    fig = plt.figure(figsize=(9.2 * len(units), 6.4))
    try:
        axes = fig.subplots(2, 2 * len(units))
        colors = {group['label']: palette[i % len(palette)] for i, group in enumerate(groups)}
        for column, unit in enumerate(units):
            for panel, (split, name, panel_title) in enumerate(_PANELS):
                ax = axes[panel // 2, column * 2 + panel % 2]
                for label, summary in panels.get((unit, split, name), []):
                    means, stds, points = summary['mean'], summary['std'], summary['x']
                    counts = summary['n_at_point']
                    varying = len(set(counts)) > 1
                    color = colors[label]
                    label = f'{label} · {unit} · n={min(counts)}' + (f'–{max(counts)}' if varying else '')
                    ax.plot(points, means, color=color, label=label, linewidth=1.8,
                            marker='o' if len(means) == 1 or varying else None)
                    ax.fill_between(points, [m - s for m, s in zip(means, stds)],
                                    [m + s for m, s in zip(means, stds)], color=color, alpha=0.18)
                    if varying:
                        for x, y, n in zip(points, means, counts):
                            ax.annotate(f'n={n}', (x, y), xytext=(0, 6), textcoords='offset points',
                                        ha='center', fontsize=7, color=color)
                ax.set_title(panel_title)
                ax.set_xlabel({'step': 'optimizer step', 'epoch': 'epoch', 'observation': 'history point'}[unit])
                ax.grid(True, alpha=0.3)
                if name == 'Accuracy':
                    ax.set_ylim(0.0, 102.0)
                    ax.set_ylabel('Accuracy (%)')
        handles = {}
        for ax in fig.axes:
            lines, labels = ax.get_legend_handles_labels()
            handles.update(zip(labels, lines))
        fig.legend(handles.values(), handles.keys(), loc='lower center', ncol=2, fontsize=8)
        fig.suptitle(title or f'{study_dir.name} · mean ± std', fontsize=11)
        fig.tight_layout(rect=(0, 0.12, 1, 1))
        try:
            mode = dest.stat().st_mode
        except FileNotFoundError:
            mode = None
        stream = tmp.open('xb')
        owned = True
        try:
            fig.savefig(stream, format='png', dpi=140, metadata={'RPipeInputs': json.dumps(inputs)})
        except BaseException:
            try:
                stream.close()
            except OSError:
                pass
            raise
        else:
            stream.close()
        if mode is not None:
            tmp.chmod(mode)
        _check_export_inputs(study_dir, refs, inputs)
        return atomic_replace(tmp, dest)
    finally:
        try:
            if owned:
                try:
                    tmp.unlink(missing_ok=True)
                except PermissionError:
                    try:
                        tmp.chmod(0o600)
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                except OSError:
                    pass
        finally:
            plt.close(fig)



def export_tensorboard(study_dir: Path | str, destination: Path | str, *,
                       run_ids: list[str] | tuple[str, ...] | None = None) -> Path:
    """Export one immutable JSONL trajectory snapshot, separately from formal results."""
    from urllib.parse import quote
    from rpipe.structure.artifact.io import validate_run_root

    study, dest = Path(study_dir).resolve(), Path(destination).resolve()
    validate_run_root(Path(__file__).resolve().parent, dest)
    if dest.is_relative_to(study) or study.is_relative_to(dest):
        raise ValueError('TensorBoard destination must be separate from the Study')
    selected = None
    if run_ids is not None:
        if (not isinstance(run_ids, (list, tuple)) or not run_ids
                or any(not isinstance(name, str) or name in {'', '.', '..'}
                       or any(char in name for char in '/\\\x00:*?"<>|') for name in run_ids)
                or len(set(run_ids)) != len(run_ids)):
            raise ValueError('TensorBoard selection requires distinct safe Run ids')
        selected = set(run_ids)
    series = []
    seen = set()
    index, refs, inputs = _export_snapshot(study, selected=selected)
    if selected is not None and selected - {ref.id for ref in refs}:
        raise ValueError('selected Run is absent from the published plan')
    for ref in refs:
        run = ref.id
        if selected is not None and run not in selected:
            continue
        curves = load_curve_series(ref)
        if curves:
            seen.add(run)
        for split, names in curves.items():
            for name, row in names.items():
                if row['unit'] == 'observation' or any(
                    point != int(point) or not 0 <= point < 2**63 for point in row['x']
                ):
                    raise ValueError(f'{run}/{split}/{name}: TensorBoard needs integer optimizer_step/epoch; use JSONL plots for fractional or unknown coordinates')
                tag = '/'.join((row['unit'], quote(split, safe=''), quote(name, safe='')))
                series.append((run, tag, row))
    if selected is not None and selected - seen:
        raise ValueError('selected Run has no JSONL scalar trajectory')
    if not series:
        raise ValueError('no JSONL scalar trajectories to export')
    from torch.utils.tensorboard import SummaryWriter

    _check_export_inputs(study, refs, inputs)
    # A new directory prevents previous exports from reviving a rolled-back tail.
    dest.mkdir(parents=True, exist_ok=False)
    for run in dict.fromkeys(run for run, _, _ in series):
        with SummaryWriter(log_dir=str(dest / run)) as writer:
            for run_name, tag, row in series:
                if run_name == run:
                    for step, value in zip(row['x'], row['values']):
                        writer.add_scalar(tag, value, global_step=int(step),
                                          new_style=True, double_precision=True)
    io.write_json(dest / 'EXPORT_INPUTS.json', {'inputs': inputs, 'runs': [ref.identity for ref in refs],
                                              'scope': 'saved scalar trajectory export'})
    return dest
