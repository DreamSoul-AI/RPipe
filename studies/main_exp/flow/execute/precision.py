"""Fresh-process checkpoint precision diagnosis; reuse native EvalAlgorithm."""
from __future__ import annotations


import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid


def main(recipe, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--tf32', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args(argv)
    args.run_root = recipe.external_root(args.run_root)
    if args.worker and args.output is None:
        parser.error('--worker requires --output')
    if args.output is not None:
        args.output = recipe.external_root(args.output)
    if not args.checkpoint.is_file():
        parser.error('an existing checkpoint is required; do not replace it with retraining')
    recipe.bootstrap()
    if args.worker:
        return _worker(recipe, args)
    ident = 'precision-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    root = args.run_root / 'validation' / ident
    root.mkdir(parents=True, exist_ok=False)
    report = {'measurement_id': ident, 'checkpoint_sha256': hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
              'complete': False, 'status': 'running', 'expected_groups': 4,
              'checkpoint_local_only': str(args.checkpoint.resolve()), 'groups': [],
              'scope': 'same checkpoint/full test; FP32 supported gate, TF32 diagnostic not a replacement baseline'}
    from rpipe.structure.artifacts import atomic_write_text
    original = recipe.source_manifest()
    report['source_hashes'] = original
    for tf32 in (False, True):
        for concurrency in (1, 3):
            group = {'tf32': tf32, 'concurrency': concurrency, 'runs': []}
            def execute(index):
                output = root / f'{tf32}-{concurrency}-{index}'
                output.mkdir()
                command = [sys.executable, '-B', str(Path(recipe.__file__)), 'diagnose-precision',
                           '--run-root', str(args.run_root), '--worker', '--checkpoint', str(args.checkpoint.resolve()), '--output', str(output)]
                if tf32:
                    command.append('--tf32')
                if index == 0:
                    command.append('--profile')
                with (output / 'worker.log').open('w', encoding='utf-8') as log:
                    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
                path = output / 'diagnosis.json'
                body = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'error': 'missing diagnosis'}
                return {'exit_code': completed.returncode, 'output_local_only': str(output), **body}
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                group['runs'] = list(pool.map(execute, range(8)))
            group['execution_passed'] = all(row['exit_code'] == 0 and row.get('samples') == 10000 for row in group['runs'])
            report['groups'].append(group)
            atomic_write_text(root / 'REPORT.json', json.dumps(report, indent=2))
            print(json.dumps({'group': [tf32, concurrency], 'execution_passed': group['execution_passed'],
                              'correct': [r.get('correct_samples') for r in group['runs']]}, ensure_ascii=False), flush=True)
    fp32 = [row for group in report['groups'] if not group['tf32'] for row in group['runs']]
    report['source_unchanged'] = original == recipe.source_manifest()
    report['fp32_passed'] = report['source_unchanged'] and all(group['execution_passed'] for group in report['groups']) and all(
        row['correct_samples'] == row['checkpoint_correct_samples'] and row['prediction_sha256'] == fp32[0]['prediction_sha256']
        and row['input_sha256'] == fp32[0]['input_sha256'] and row['model_sha256'] == fp32[0]['model_sha256'] for row in fp32)
    report['checkpoint_unchanged'] = report['checkpoint_sha256'] == hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    report['fp32_passed'] &= report['checkpoint_unchanged']
    report['complete'] = True
    report['status'] = 'passed' if report['fp32_passed'] else 'failed'
    atomic_write_text(root / 'REPORT.json', json.dumps(report, indent=2))
    return 0 if report['fp32_passed'] else 1


def _worker(recipe, args):
    from rpipe.structure.artifacts import atomic_write_text

    import torch
    from rpipe.structure.contracts import AlgorithmConfig
    from rpipe.payloads.evaluation import EvalAlgorithm
    from rpipe.payloads.metrics import MetricBundle
    from rpipe.payloads.tracker import AlgorithmTracker
    from rpipe.structure.contracts import DataConfig
    from rpipe.payloads.data import DataFactory
    from rpipe.structure.contracts import ModelConfig
    from rpipe.payloads.models import ModelFactory
    from rpipe.structure.contracts import SystemConfig
    from rpipe.payloads.checkpoints import SystemFactory
    from rpipe.payloads.runtime import apply_runtime
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    recipe.install(args.run_root / 'shared' / 'data', 0, archive_root=args.output / 'source-4ccb28d')
    config = SystemConfig.from_mapping({'device': 'cuda', 'deterministic': True, 'cudnn_deterministic': True,
        'cudnn_benchmark': False, 'cudnn_allow_tf32': args.tf32, 'matmul_allow_tf32': False})
    runtime = apply_runtime(0, config)
    assets = args.output / 'assets'
    data = DataFactory.build(DataConfig.from_mapping({'name': 'CIFAR10', 'source': recipe.SOURCE,
        'config': {'batch_size': 250, 'pin_memory': True, 'num_workers': 0}}), assets, seed=0)
    model = ModelFactory.build(ModelConfig.from_mapping({'name': 'cnn', 'source': recipe.SOURCE}), assets, data_meta=data.meta)
    system = SystemFactory.build(config, assets)
    inputs, predictions, logits_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    counts = {'samples': 0, 'correct_samples': 0, 'topk_argmax_disagreements': 0}
    tensors = []
    class Captured(MetricBundle):
        def evaluate(self, split, mode, input, output):
            if split == 'test' and mode == 'batch':
                images, targets = input
                pred = output.topk(1, -1, True, True)[1].reshape(-1)
                counts['samples'] += targets.numel()
                counts['correct_samples'] += int((pred == targets).sum().item())
                counts['topk_argmax_disagreements'] += int((pred != output.argmax(-1)).sum().item())
                inputs.update(images.detach().cpu().contiguous().numpy().tobytes())
                predictions.update(pred.cpu().numpy().tobytes())
                logits_hash.update(output.detach().cpu().contiguous().numpy().tobytes())
                tensors.append(output.detach().cpu())
            return super().evaluate(split, mode, input, output)
    tracker = AlgorithmTracker(assets, Captured())
    algorithm = EvalAlgorithm(AlgorithmConfig.from_mapping({'mode': 'eval', 'resume_from': str(args.checkpoint.resolve()), 'eval_num_steps': -1}))
    activities = [torch.profiler.ProfilerActivity.CPU]
    if torch.profiler.ProfilerActivity.CUDA in torch.profiler.supported_activities():
        activities.append(torch.profiler.ProfilerActivity.CUDA)
    if args.profile:
        with torch.profiler.profile(activities=activities) as profile:
            summary = algorithm.run(data, model, system, tracker)
        profile.export_chrome_trace(str(args.output / 'trace.json'))
        trace = json.loads((args.output / 'trace.json').read_text(encoding='utf-8'))
        kernels = sorted({event['name'] for event in trace['traceEvents'] if event.get('cat') == 'kernel'})
    else:
        summary = algorithm.run(data, model, system, tracker)
        kernels = []
    state = hashlib.sha256()
    for name, tensor in sorted(model.module.state_dict().items()):
        state.update(name.encode())
        state.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    from flow.execute.speed import correct_samples
    body = {**counts, 'input_sha256': inputs.hexdigest(), 'prediction_sha256': predictions.hexdigest(),
            'logits_sha256': logits_hash.hexdigest(), 'model_sha256': state.hexdigest(),
            'checkpoint_correct_samples': correct_samples(summary['best_accuracy'], 10000),
            'runtime': runtime, 'kernels': kernels, 'kernel_trace_available': bool(kernels)}
    torch.save(torch.cat(tensors), args.output / 'logits.pt')
    atomic_write_text(args.output / 'diagnosis.json', json.dumps(body, indent=2) + '\n', encoding='utf-8')
    return 0
