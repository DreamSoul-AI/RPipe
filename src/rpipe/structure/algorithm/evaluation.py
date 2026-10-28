"""Eval mode: independent algorithm; resume then score."""

from __future__ import annotations

from typing import Any

from rpipe.structure.algorithm.factory import Algorithm
from rpipe.structure.control import AlgorithmConfig
from rpipe.structure.algorithm.evaluation_support import eval_batch_limit, eval_test_split
from rpipe.structure.algorithm.progress import format_hms
from rpipe.structure.algorithm.tracker import AlgorithmTracker


class EvalAlgorithm(Algorithm):
    def run(self, data: Any, model: Any, system: Any, tracker: AlgorithmTracker) -> dict[str, Any]:
        logger = getattr(system, 'logger', None)
        extra: dict[str, Any] = {}
        if getattr(data, 'source', None) == 'stub' and (getattr(data, 'meta', None) or {}).get('stub') is True:
            tracker.append('test', n=1, values={'Accuracy': 0.0})
            tracker.save('test')
            tracker.flush('test')
            if logger is not None:
                logger.report(tracker, 'test')
            return {'mode': 'eval', 'stub': True, 'resume_count': 0, 'resume_path': None}
        if getattr(model, 'module', None) is None or not callable(getattr(data, 'iter_batches', None)):
            raise ValueError('eval requires a model module and data.iter_batches; use explicit stub data for flow checks')
        if getattr(system, 'place_module', None):
            model.module = system.place_module(model.module)
        restored = self.resume(data, model, system, tracker, extra)
        tracker.begin_run()
        import time

        started = time.perf_counter()
        report_extra = {
            'resume_stem': extra.get('resume_stem'),
            'resume_path': extra.get('resume_path'),
            'epoch': (restored or {}).get('epoch'),
            'step': (restored or {}).get('step'),
        }
        recorded_progress = ((restored or {}).get('tracker') or {}).get('progress')
        if recorded_progress is not None:
            report_extra['progress'] = recorded_progress
        # Pass logger here: eval_test_split reports then reset(); a later report would print 0.
        metrics = eval_test_split(
            tracker,
            logger,
            data,
            model,
            system,
            report_extra,
            num_steps=eval_batch_limit(self.config),
        )
        elapsed = time.perf_counter() - started
        report_extra['elapsed'] = format_hms(elapsed)
        if logger is not None:
            logger.info(f"eval elapsed={report_extra['elapsed']}")
        result = {
            'mode': 'eval',
            'resume_stem': extra.get('resume_stem'),
            'resume_path': extra.get('resume_path') if restored is not None else None,
            'resume_count': extra['resume_count'],
            'step': (restored or {}).get('step'),
            'epoch': (restored or {}).get('epoch'),
            'elapsed': format_hms(elapsed),
            'elapsed_seconds': float(elapsed),
        }
        if 'Accuracy' in metrics:
            result['accuracy'] = metrics['Accuracy']
        checkpoint = restored or {}
        if checkpoint.get('best_metric', 'Accuracy') == 'Accuracy':
            if checkpoint.get('best_accuracy') is not None:
                result['best_accuracy'] = checkpoint['best_accuracy']
            elif 'Accuracy' in metrics:
                result['best_accuracy'] = metrics['Accuracy']
        for name in ('best_metric', 'best_value'):
            if name in checkpoint:
                result[name] = checkpoint[name]
        return result


def run(control_algorithm: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    algo = EvalAlgorithm(AlgorithmConfig.from_mapping(control_algorithm))
    return algo.run(state.get('data'), state.get('model'), state.get('system'), state['tracker'])
