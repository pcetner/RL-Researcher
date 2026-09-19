"""Durable bounded task repair and provider waiting; model output grants no authority."""
import json
import time


class RecoveryPending(Exception):
    pass


def provider_category(result):
    errors=' '.join(result.get('provider_errors',[])).lower()
    if any(x in errors for x in ('unauthorized','invalid api key','authentication failed','sign in again','access denied')):
        return 'access'
    if any(x in errors for x in ('rate limit','usage exhausted','usage limit','quota exceeded','temporarily unavailable','service unavailable','connection reset','connection timed out')):
        return 'provider'
    return None


def incident(research,**values):
    return research.ai.command(research.campaign,'observe_incident',**values)


def abandon(research,state,phase,reason):
    key=f"{state['cycle']}:{phase}"
    research.ai.command(research.campaign,'task_abandoned',task=key,task_class=phase,evidence=reason)
    state.setdefault('abandoned',[]).append({'task':key,'phase':phase,'reason':reason,'at':time.time()})
    state.update(cycle=state['cycle']+1,phase='plan',revision_attempt=0,replan_reason=reason)
    research.save(state)


def call(research,state,phase,role,prompt):
    key=f"{state['cycle']}:{state.get('revision_attempt',0)}:{phase}"
    logical_key=f"{state['cycle']}:{phase}" if phase in {'implement','plan','assess'} else key
    task=state.setdefault('tasks',{}).setdefault(logical_key,{'attempt':0,'failures':[]})
    if task.get('retry_at',0)>time.time():raise RecoveryPending()
    if key not in state['calls']:
        from .research import CONTRACT
        state['calls'][key]=CONTRACT+'\n'+prompt
        research.save(state)
    attempt=task['attempt']
    message=state['calls'][key]
    # Refresh authority even when a durable task is repaired after a restart. Stored prompts
    # and historical recommendations remain evidence, not a cached permission decision.
    from .research_policy import context_text, validate_design, validate_review
    current=research.broker.store.inspect(research.broker.owner,research.campaign)
    message+=context_text(current)
    if attempt:
        message+='\nREPAIR '+str(attempt)+': '+json.dumps(task['failures'][-2:])+(
            '\nReturn the smallest complete valid JSON for this task. Correct the reported defect; preserve scientific constraints and required fields. Avoid verbose output and unrelated context.' if attempt==1 else
            '\nFurther reduce the proposed scope to a bounded eligible test. Do not remove required scientific inputs, provenance or interfaces. Preserve explicit limitations and return valid JSON.')
    dispatch_key=key+(f':repair:{attempt}' if attempt else '')+(':'+str(task['provider_attempt']) if task.get('provider_attempt') else '')
    result=research.ai.run(research.campaign,dispatch_key,role,message,180,task=logical_key,attempt=attempt)
    evidence=result['evidence']
    if not result.get('termination_confirmed'):
        incident(research,category='termination',scope=key,reason='Previous model process termination is unconfirmed',evidence=evidence,next_action='Reconcile the recorded process before retrying this task')
        raise RecoveryPending()
    category=provider_category(result)
    if category:
        if category=='provider':
            failures=state.get('provider_failures',0)+1
            state['provider_failures']=failures
            retry=time.time()+max(([30,120][failures-1] if failures<=2 else 900),result.get('retry_after_seconds',0))
            state['provider_wait']={'retry_at':retry,'task':key,'evidence':evidence}
            task['provider_attempt']=task.get('provider_attempt',0)+1
            incident(research,category=category,scope='provider',reason='Subscription access is temporarily unavailable',evidence=evidence,status='waiting',retry_at=retry,next_action='Check provider availability at the scheduled time')
        else:
            state['access_required']=evidence
            incident(research,category=category,scope='provider',reason='Subscription authentication or access requires attention',evidence=evidence,status='human_decision_required',next_action='Restore subscription access; then verify it with a bounded probe')
        research.save(state)
        raise RecoveryPending()
    try:
        if result['state']!='completed' or result.get('tool_events'):
            if result.get('tool_events'):raise PermissionError('Research invocation attempted a forbidden tool')
            raise ValueError(result.get('termination_reason') or 'Invocation produced no usable output')
        value=json.loads(result['text'].strip().removeprefix('```json').removesuffix('```').strip())
        if not isinstance(value,dict):raise ValueError('Expected a JSON object')
        required={'plan':('question','rationale','alternative','decision'),
                  'implement':('question','rationale','alternative','decision','conditions','seeds','decisions','chunk','updates','trial_seconds','implementation'),
                  'review':('result','rationale'),'assess':('tested','finding','limitations','next_decision','validity','behavioral_progress','decision_value'),
                  'review_conclusion':('result','rationale'),'investigation':('findings','alternatives')}.get(phase,())
        if any(k not in value for k in required):raise ValueError('Missing required fields: '+','.join(k for k in required if k not in value))
        if phase in {'review','review_conclusion'} and value['result'] not in {'pass','fail','inconclusive'}:raise ValueError('Invalid review judgment')
        if phase in {'plan','implement'}:validate_design(value,current)
        if phase=='review':validate_review(value)
        if phase=='assess' and (value['validity'] not in {'valid','invalid','incomplete'} or any(not isinstance(value[k],bool) for k in ('behavioral_progress','decision_value'))):raise ValueError('Invalid assessment validity or progress fields')
    except (ValueError,AttributeError,TypeError) as error:
        task['failures'].append({'attempt':attempt,'reason':str(error),'evidence':evidence})
        if attempt>=2:
            abandon(research,state,phase,str(error)+'; '+evidence)
        else:
            task.update(attempt=attempt+1,retry_at=time.time()+2)
            incident(research,category='task',scope=logical_key,reason=str(error),evidence=evidence,
                     attempts=attempt+1,next_action=f'Repair {attempt+1} of 2 for {phase}',retry_at=task['retry_at'])
            research.save(state)
        raise RecoveryPending()
    for row in research.broker.store.inspect(research.broker.owner,research.campaign).get('incidents',[]):
        if row['category']=='task' and row['scope']==logical_key and row['status']!='resolved':
            research.ai.command(research.campaign,'reconcile_incident',incident=row['id'],verified=True,evidence=evidence)
    return value,result


def controller_failure(research,state,error):
    phase=state['phase']; key=f"{state['cycle']}:{phase}"
    logical_phase='implement' if phase in {'prepare','dispatch'} else phase
    key=f"{state['cycle']}:{logical_phase}"
    task=state.setdefault('tasks',{}).setdefault(key,{'attempt':0,'failures':[]})
    count=task['attempt']
    if count>=2:
        abandon(research,state,logical_phase,str(error))
        return
    task['attempt']=count+1
    task['failures'].append({'attempt':count,'reason':str(error),'evidence':'Controller validation'})
    state.setdefault('plan',{})['repair_feedback']=str(error)
    state['revision_attempt']=state.get('revision_attempt',0)+1
    if phase in {'prepare','dispatch'}:state['phase']='implement'
    incident(research,category='task',scope=key,reason=str(error),evidence='Recorded controller validation failure',attempts=count+1,next_action='Prepare a corrected bounded task')
    research.save(state)


def probe(research,state,campaign):
    pending=state.get('provider_wait')
    if state.get('access_required'):
        requested=next((r for r in campaign.get('incidents',[]) if r['category']=='access' and r.get('facts',{}).get('probe_requested') and r['status']=='waiting'),None)
        if not requested:return True
        state.pop('access_required',None)
        pending=state['provider_wait']={'retry_at':requested['retry_at'],'task':'access-check','evidence':requested['evidence']}
        state['provider_failures']=state.get('provider_failures',0)+1
        research.save(state)
    if not pending:return False
    if time.time()<pending['retry_at']:return True
    calls=[e for e in campaign['ai_executions'] if e['session']==campaign['sessions'][-1]['id']]
    if time.time()+180>=campaign['work_deadline'] or len(calls)>=campaign['policy']['ai_call_limit']-1:
        research.ai.command(research.campaign,'stop',reason='No remaining allowance for another provider probe',system_stop=True)
        return True
    count=state.get('provider_failures',1)
    result=research.ai.run(research.campaign,f"provider-probe:{state['session']}:{count}",'coordinator',
                           'Availability check only. Return {"available":true}. Do not use tools.',180,purpose='probe',task='provider-probe',attempt=count)
    if not result.get('termination_confirmed'):return True
    if result.get('tool_events'):raise PermissionError('Availability probe attempted a forbidden tool')
    category=provider_category(result)
    if category=='access':
        state['access_required']=result['evidence']
        incident(research,category='access',scope='provider',reason='Provider denied access',evidence=result['evidence'],status='human_decision_required',next_action='Restore subscription authentication before requesting an access verification')
        research.save(state)
        return True
    if result['state']=='completed' and not category:
        for row in research.broker.store.inspect(research.broker.owner,research.campaign).get('incidents',[]):
            if row['category'] in {'provider','access'} and row['status']!='resolved':
                research.ai.command(research.campaign,'reconcile_incident',incident=row['id'],verified=True,evidence=result['evidence'])
        state.pop('provider_wait',None)
    else:
        count+=1;state['provider_failures']=count
        pending['retry_at']=time.time()+max((120 if count==2 else 900),result.get('retry_after_seconds',0))
        incident(research,category=provider_category(result) or 'provider',scope='provider',reason='Subscription availability has not recovered',evidence=result['evidence'],status='waiting',retry_at=pending['retry_at'],next_action='Wait for the next bounded availability check')
    research.save(state)
    return True
