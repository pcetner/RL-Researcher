"""LLM-authored launchable example: known arithmetic, observable progress, exact recovery."""
import json
import time

def resolve(definition):
    assert all(t['decisions'] > 0 for t in definition['trials'])
    return definition

def execute(ctx, trial, checkpoint):
    saved = json.loads((checkpoint/'state.json').read_text()) if checkpoint else {'decision':0,'sum':0}
    ctx.phase('Accumulating deterministic measurements')
    try:
        for i in range(saved['decision'],trial['decisions']):
            ctx.check()
            time.sleep(trial.get('delay',.1))
            saved = {'decision':i+1,'sum':saved['sum']+i+1}
            if (i+1)%5==0:
                (ctx.work/'state.json').write_text(json.dumps(saved))
                ctx.publish_checkpoint(ctx.work/'state.json',i+1)
            ctx.progress(i+1)
            ctx.sample(i+1,{'sum':{'value':saved['sum'],'count':i+1,'kind':'cumulative'}},force=i+1==trial['decisions'])
    finally:
        (ctx.work/'state.json').write_text(json.dumps(saved))
        ctx.publish_checkpoint(ctx.work/'state.json',saved['decision'])
    return {'measurements':{'sum':saved['sum']},'decisions':saved['decision']}

def finalize(ctx, results):
    ctx.check()
    path=ctx.work/'analysis.txt'
    path.write_text('Deterministic tooling evidence: '+json.dumps(results,indent=2))
    ctx.publish_artifact(path,'Deterministic comparison','text/plain')
