"""Apply the declared curve gates once after Study aggregation."""

def run(ctx):
    if ctx.scope != 'study':
        return
    from .curves import main
    args = ['--study-root', str(ctx.layout.study_root), '--reference', str(ctx.study_dir / 'docs' / 'REFERENCE_CURVES.json'),
            '--output', str(ctx.layout.study_root / 'summary')]
    if not ctx.state['process'].get('complete'):
        args.append('--partial')
    code = main(args)
    if code:
        raise RuntimeError(f'main_exp curve validation failed (exit {code}); see the explicit Study version summary/COMPARISON.json')
