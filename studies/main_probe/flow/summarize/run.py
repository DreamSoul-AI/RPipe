def run(ctx):
    from ...structure.algorithm.paired import save_json

    workspace = ctx.state['algorithm'].workspace
    report = ctx.state['probe']
    save_json(workspace / 'COMPARISON.json', report)
    if report.get('selected_passed') is not True:
        raise RuntimeError(f'paired numerical gate failed; evidence: {workspace / "COMPARISON.json"}')
    ctx.state['result_draft'].setdefault('paths', {})['probe'] = str(ctx.state['algorithm'].workspace)
    ctx.state['result_draft']['probe'] = ctx.state['probe']
