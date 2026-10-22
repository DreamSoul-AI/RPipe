"""Study-level curves from report observations, falling back to tracker history."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from rpipe.structure.readout import collect_from_index, load_curve_series, summarize_curve_series
from rpipe.structure.artifacts import atomic_replace
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


def _history(study_dir: Path, run_dir: str, split: str, name: str) -> list[float]:
    return load_curve_series(study_dir, run_dir).get(split, {}).get(name, {}).get('values', [])


def collect_curve_groups(study_dir: Path, index: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not index:
        return []
    groups: list[dict[str, Any]] = []
    for exp in collect_from_index(study_dir, index):
        factors = dict(exp.get('factors') or {})
        if str(factors.get('algorithm.mode') or '').lower() == 'eval':
            continue
        run_dirs = [
            str(run.get('run_dir') or run.get('id') or '')
            for run in (exp.get('runs') or []) if run.get('status') == STATUS_SUCCEEDED
        ]
        run_dirs = [name for name in run_dirs if name]
        if not run_dirs:
            continue
        groups.append({'label': _label(factors), 'run_dirs': run_dirs})
    return groups


def write_learning_curves(
    study_dir: Path | str,
    index: dict[str, Any] | None,
    *,
    title: str | None = None,
) -> Path | None:
    """Write output ``summary/figures/learning_curves.png``; never source docs."""
    study_dir = Path(study_dir)
    groups = collect_curve_groups(study_dir, index)
    panels = {}
    for group in groups:
        for split, name, _title in _PANELS:
            series = [
                row for run_dir in group['run_dirs']
                if (row := load_curve_series(study_dir, run_dir).get(split, {}).get(name))
            ]
            summary = summarize_curve_series(series)
            if summary:
                for unit, values in (summary.get('by_unit') or {summary['unit']: summary}).items():
                    panels.setdefault((unit, split, name), []).append((group['label'], values))
    units = [unit for unit in ('step', 'epoch', 'observation') if any(key[0] == unit for key in panels)]
    if not units:
        return None

    import matplotlib.pyplot as plt

    dest = learning_curves_path(study_dir)
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
            fig.savefig(stream, format='png', dpi=140)
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
    from rpipe.structure.readout import _jsonl_curve_series
    from rpipe.structure.artifacts import contained, kinds
    from rpipe.structure.contracts import validate_run_root
    RUNS_DIRNAME = "runs"

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
    runs = contained(study / RUNS_DIRNAME, study)
    for path in sorted(runs.glob(f'*/assets/{kinds.TRACKER_JSONL}')):
        run = path.parents[2].name
        if selected is not None and run not in selected:
            continue
        path = contained(path, study)
        seen.add(run)
        for split, names in _jsonl_curve_series(path).items():
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

    # A new directory prevents previous exports from reviving a rolled-back tail.
    dest.mkdir(parents=True, exist_ok=False)
    for run in dict.fromkeys(run for run, _, _ in series):
        with SummaryWriter(log_dir=str(dest / run)) as writer:
            for run_name, tag, row in series:
                if run_name == run:
                    for step, value in zip(row['x'], row['values']):
                        writer.add_scalar(tag, value, global_step=int(step),
                                          new_style=True, double_precision=True)
    return dest
