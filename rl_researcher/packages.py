"""Trusted materialization of proposed research methods inside the bounded volume."""
import hashlib
import shutil

from . import atomic, experiment, resources


def prepare(root, request, proposal, policy):
    resources.preflight(root,policy)
    key = hashlib.sha256(request.encode()).hexdigest()
    target = root/'packages'/key
    identity = hashlib.sha256(__import__('json').dumps(proposal,sort_keys=True).encode()).hexdigest()
    if target.exists():
        if atomic.read_json(target/'proposal.json')['hash'] != identity:
            raise ValueError('Package request identity reused with changed proposal')
        return {'root':str(target),'resolved':experiment.inspect(target,'candidate')}
    conditions = proposal['conditions']
    seeds = proposal['seeds']
    if not conditions or len(conditions)>3 or any(c not in {'online','frozen','random'} for c in conditions):
        raise ValueError('Unsupported experimental conditions')
    if not seeds or len(seeds)>3 or any(type(s) is not int or not 0<=s<100000 for s in seeds):
        raise ValueError('Seeds must be explicit and bounded')
    decisions,chunk,updates = (proposal[k] for k in ('decisions','chunk','updates'))
    if any(type(n) is not int for n in (decisions,chunk,updates)) or not (16<=decisions<=4096 and 8<=chunk<=decisions and 0<=updates<=200):
        raise ValueError('Proposal exceeds the reviewed deployment parameter envelope')
    seconds = proposal['trial_seconds']
    if type(seconds) is not int or not 30<=seconds<=1800:
        raise ValueError('Trial allowance exceeds deployment bounds')
    source = root/'template'
    target.mkdir(parents=True)
    shutil.copytree(source/'python',target/'python')
    shutil.copytree(source/'configs',target/'configs')
    d = atomic.read_json(source/'definition.json')
    method = proposal.get('implementation')
    if method:
        if not isinstance(method,str) or len(method.encode())>128*1024:
            raise ValueError('Implementation exceeds source allowance')
        (target/'python/autosm64/research/candidate.py').write_text(method,encoding='utf-8')
        for role,symbol in [('resolver','resolve'),('executor','execute'),('finalizer','finalize')]:
            d[role] = 'python/autosm64/research/candidate.py:'+symbol
    environment = target/'configs/castle_k20.toml'
    d['inputs']['environment'] = {'path':str(environment),'sha256':atomic.digest(environment)}
    d['trials'] = [dict(d['trials'][0],id=f'{condition}-{seed}',label=f'{condition}, seed {seed}',
                        condition=condition,action_seed=seed,decisions=decisions,chunk=chunk,
                        updates=updates,seconds=seconds,deterministic=True)
                   for condition in conditions for seed in seeds]
    d['limits']['overall_seconds'] = seconds*len(d['trials'])+60
    d.update(name=proposal['question'],question=proposal['question'],setup=proposal['rationale'])
    atomic.write_json(target/'definition.json',d)
    atomic.write_json(target/'research.json',{'experiments':[{'id':'candidate','definition':'definition.json'}],'history':[]})
    atomic.write_json(target/'proposal.json',{'hash':identity,'proposal':proposal})
    return {'root':str(target),'resolved':experiment.inspect(target,'candidate')}
