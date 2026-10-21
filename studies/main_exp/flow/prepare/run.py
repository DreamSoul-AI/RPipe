"""Check that the built Run uses the declared historical adapters."""

from ...recipe import COMMIT, SOURCE


def validate_runtime(ctx):
    import json
    import torch
    from rpipe.structure.artifacts import atomic_write_text

    expected = {'deterministic': True, 'cudnn_deterministic': True, 'cudnn_benchmark': False,
                'cudnn_allow_tf32': False, 'matmul_allow_tf32': False}
    actual = {'deterministic': torch.are_deterministic_algorithms_enabled(),
              'cudnn_deterministic': torch.backends.cudnn.deterministic,
              'cudnn_benchmark': torch.backends.cudnn.benchmark,
              'cudnn_allow_tf32': torch.backends.cudnn.allow_tf32,
              'matmul_allow_tf32': torch.backends.cuda.matmul.allow_tf32}
    evidence = {'expected': expected, 'actual': actual, 'passed': actual == expected}
    ctx.state['runtime_validation'] = evidence
    atomic_write_text(ctx.layout.assets_dir / 'runtime_validation.json', json.dumps(evidence, indent=2) + '\n')
    if not evidence['passed']:
        raise ValueError(f'main_exp actual runtime differs from declared controls: {actual}')


def run(ctx):
    for layer in ('data', 'model'):
        obj = ctx.state[layer]
        if obj.source != SOURCE or obj.meta.get('historical_commit') != COMMIT:
            raise ValueError(f'main_exp {layer} does not use the declared historical recipe')
    validate_runtime(ctx)
