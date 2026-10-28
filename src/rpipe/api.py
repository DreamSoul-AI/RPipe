"""Explicit public use cases. Planning and reading never select a science backend."""
from __future__ import annotations


from .structure.readout import read_run, aggregate, report, logs, compare, read_legacy


def compare_runs(*args, study_dir=None, **kwargs):
    """Explicit scientific directory/checkpoint comparison; may load trusted tensors."""
    from .structure.comparison import compare_runs as compare_files
    if study_dir is not None:
        from .flow.study import _recipe_pickle_module
        kwargs['checkpoint_reader'] = _recipe_pickle_module(study_dir)
    return compare_files(*args, **kwargs)


def write_compare(path, body):
    from .structure.comparison import write_compare as write_comparison
    return write_comparison(path, body)
