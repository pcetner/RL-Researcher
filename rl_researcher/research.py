"""Durable, bounded research cycle. Model output is data until reviewed and sandboxed."""
import json
import time

from haws_core.store import digest
from haws_core.subscription import Subscription
from . import atomic, lock
from .recovery import RecoveryPending, controller_failure
from .campaign_execution import Execution
from .wsl_control import WslRunner
from .research_policy import (context_text, authority_context, DESIGN_SCHEMA, validate_design,
                              validate_review, remaining_calls, required_calls)
import uuid

CONTRACT = '''The primary learner starts from random weights and a fresh save at the human-approved post-intro castle entrance. Menu/dialog initialization is permitted. It learns only from its own images and actions. No demonstrations, routes, game-specific macros/rewards/curricula, privileged inputs, or pretrained weights. Evaluation-only game signals must never enter rewards, replay prioritization or control. Proxy changes are not stars or behavioral capabilities. Preserve negative evidence. Do not change platform controls, limits or this contract. All tools are disabled: return JSON data only.'''


class Research:
    def __init__(self, broker, campaign, deployment):
        self.broker,self.campaign,self.deployment = broker,campaign,deployment
        self.directory = broker.directory/'research'/campaign
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path = self.directory/'state.json'
        if not self.path.exists():
            atomic.write_json(self.path,{'cycle':0,'phase':'plan','calls':{},'operations':{},'recovery_count':0})
        credentials = self.directory/'actors.json'
        if not credentials.exists():
            actors = {}
            for role,kind,caps in [('coordinator','Orchestrator',['propose','prepare_job','conclude','reassess','stop','summarize','begin_investigation','end_investigation']),
                                   ('worker','Worker',['propose']),('reviewer','Reviewer',['review','review_conclusion'])]:
                actors[role] = broker.store.provision('research-'+role+'-'+campaign,kind,caps,campaign,broker.owner)['token']
            atomic.write_json(credentials,actors)
        self.actors = atomic.read_json(credentials)
        if self.actors.get('_version')!=2:
            self.actors['coordinator']=broker.store.provision('research-coordinator-'+campaign,'Orchestrator',
                ['propose','prepare_job','conclude','reassess','stop','summarize','begin_investigation','end_investigation'],campaign,broker.owner)['token']
            self.actors['_version']=2
            atomic.write_json(credentials,self.actors)
        self.actors['tool'] = broker.tool
        self.ai = Subscription(broker.store,broker.owner,self.directory/'invocations',[broker.directory],
                               deployment['model'],deployment['pricing'])
        self.client = WslRunner(deployment['distribution'],deployment['base'])

    def save(self,state):
        atomic.write_json(self.path,state)

    def command(self,state,key,role,operation,**inputs):
        if operation in {'summarize','stop','begin_investigation','end_investigation'}:
            key = state.get('session','initial')+':'+key
        key = str(state['cycle'])+':'+str(state.get('revision_attempt',0))+':'+key
        if key not in state['operations']:
            state['operations'][key] = {'request_id':self.campaign+':research:'+key,
                                       'expected_revision':self.broker.store.inspect(self.broker.owner,self.campaign)['revision'],
                                       'operation':operation,'campaign':self.campaign,**inputs}
            self.save(state)
        request = state['operations'][key]
        try:
            return self.broker.store.command(self.actors[role],**request)
        except Exception as error:
            if getattr(error,'code',None)=='stale':
                request['request_id'] += ':retry'
                request['expected_revision'] = self.broker.store.inspect(self.broker.owner,self.campaign)['revision']
                self.save(state)
            raise

    def call(self,state,phase,role,prompt):
        from .recovery import call
        return call(self,state,phase,role,prompt)

    def step(self):
        with lock.exclusive(self.directory/'cycle.lock'):
            state = atomic.read_json(self.path)
            campaign = self.broker.store.inspect(self.broker.owner,self.campaign)
            session = campaign['sessions'][-1]['id'] if campaign['sessions'] else None
            if session and state.get('session') != session:
                if state.get('session') and state.get('error'):
                    # Preserve the failed operation and start a new research question on renewal.
                    state.setdefault('prior_errors',[]).append(state.pop('error'))
                    state.update(cycle=state['cycle']+1,phase='plan',revision_attempt=0,reassessment=0)
                state.pop('closeout_attempted',None)
                state.pop('closeout_report',None)
                state['session'] = session
                self.save(state)
            if campaign['status']=='closing':
                if not any(j['state'] in {'dispatching','running','unknown','cancelling'} for j in campaign['jobs']) and not any(e['state']=='running' or (e['state']=='unknown' and not e.get('termination_confirmed')) for e in campaign['ai_executions']):
                    if not state.get('closeout_attempted') and not campaign.get('human_stopped'):
                        state['closeout_attempted']=True
                        self.save(state)
                        try:
                            report=self.ai.run(self.campaign,'closeout:'+session,'coordinator',CONTRACT+'\nSummarize only these recorded results and incidents. Return JSON with learned,progress,next,limitations (strings). '+json.dumps({'jobs':[j.get('result') for j in campaign['jobs']],'incidents':campaign.get('incidents',[]),'calls':campaign['ai_executions']}),min(180,max(1,campaign['deadline']-time.time()-5)),purpose='closeout',task='closeout:'+session)
                            if report['state']=='completed':state['closeout_report']=json.loads(report['text'])
                        except Exception as error:
                            state['closeout_fallback']=str(error)
                        self.save(state)
                    synthesis=state.get('closeout_report',{})
                    self.command(state,'summary','coordinator','summarize',
                                 learned=str(synthesis.get('learned') or ('; '.join(j['result']['finding'] for j in campaign['jobs'] if j['result']) or 'No completed scientific conclusion')),
                                 progress='Behavioral progress was reported; inspect its reviewed evidence' if any(j.get('result',{}).get('behavioral_progress') for j in campaign['jobs'] if j.get('result')) else 'No behavioral improvement demonstrated',
                                 next='Research call allowance exhausted; a new explicit authorization is required' if remaining_calls(campaign)<=0 else 'Review the recorded incident and required decision' if any(i['status']=='human_decision_required' for i in campaign.get('incidents',[])) or campaign['blockers'] else 'Review results and current resumption eligibility',
                                 limitations=state.get('error','Bounded campaign; full-game capability and repeatability are not established')+
                                 ('; Reported usage is a lower bound: some provider receipts are missing.' if any(e.get('usage') is None for e in campaign['ai_executions']) else ''))
                return
            if campaign['status']=='active' and campaign['blockers']:
                if all(b['kind'] in {'plateau','operational_plateau'} for b in campaign['blockers']):
                    self.command(state,'investigate','coordinator','begin_investigation')
                    state['phase']='investigate'
                    self.save(state)
                else:
                    self.command(state,'blocked-close','coordinator','stop',reason='Campaign requires human attention')
                return
            if campaign['status']!='active':
                return
            # Do not start an AI call which cannot fit the remaining authorization.
            if campaign['work_deadline']-time.time()<190:
                self.command(state,'close','coordinator','stop',reason='Insufficient remaining time for another bounded research step')
                return
            used=[e for e in campaign['ai_executions'] if e['session']==session]
            if len(used)>=campaign['policy']['ai_call_limit']-1 or sum(e.get('equivalent_usd') or 0 for e in used)>=campaign['policy']['ai_equivalent_usd']:
                self.command(state,'allowance-close','coordinator','stop',reason='Research invocation or known accounting allowance reached')
                return
            if campaign.get('rethink') and campaign['rethink']['status']=='active' and (time.time()+180>=campaign['rethink']['deadline'] or len(used)-campaign['rethink']['initial_calls']>=campaign['rethink']['call_limit']):
                self.command(state,'investigation-exhausted','coordinator','end_investigation',findings='Investigation allowance exhausted before a supported recovery',alternatives='Review bounded evidence and select a different approach',evidence='Authoritative time and invocation ledger')
                return
            from .recovery import probe
            if probe(self,state,campaign):return
            phase = state['phase']
            if phase not in {'execute','recover'} and remaining_calls(campaign)<required_calls(phase):
                self.command(state,'cycle-allowance-close','coordinator','stop',reason='Insufficient research calls to finish this task and independently review its conclusion')
                return
            operation={'prepare':'prepare_job','dispatch':'dispatch','recover':'dispatch'}.get(phase)
            if operation and not campaign['eligibility'][operation]['allowed']:return
            if self.deployment.get('max_cycles') and state['cycle']>=self.deployment['max_cycles']:
                self.command(state,'bounded-complete','coordinator','stop',reason='Bounded research-cycle qualification completed')
                return
            if phase=='investigate':
                finding,record = self.call(state,'investigation','coordinator',
                    'A plateau was triggered. Diagnose the lack of decision-changing evidence from these records. '
                    'Identify a materially different explanation and concrete alternatives for the human. '
                    'Do not claim that an unexecuted test succeeded. Return JSON with findings, alternatives, action (experiment or escalate), and optional proposal. A proposal must use the existing implement-phase schema and fit the remaining investigation time. '
                    'This invocation is bounded to at most 20% of the remaining campaign allowance. '
                    'Remaining investigation: '+json.dumps(campaign.get('rethink'))+'\nEvidence: '+json.dumps([j['result'] for j in campaign['jobs'] if j['result']])+'\nOperational failures: '+json.dumps(state.get('abandoned',[]))+'\nBaseline interfaces: '+self.deployment['method_context'])
                rethink=campaign['rethink']
                used=sum(e['session']==campaign['sessions'][-1]['id'] for e in self.broker.store.inspect(self.broker.owner,self.campaign)['ai_executions'])-rethink['initial_calls']
                proposal=finding.get('proposal')
                if finding.get('action')=='experiment' and isinstance(proposal,dict) and rethink['call_limit']-used>=3 and time.time()+600<rethink['deadline']:
                    state.update(proposal=proposal,producer_execution=record['thread_id'],phase='prepare',investigating=True)
                else:
                    self.command(state,'investigation-end','coordinator','end_investigation',findings=str(finding['findings']),alternatives=str(finding['alternatives']),evidence=record['evidence'])
                    self.command(state,'plateau-stop','coordinator','stop',reason='Bounded investigation could not establish a supported recovery')
            elif phase=='plan':
                context = {'objective':campaign['objective'],'instructions':campaign['instructions'],
                           'authority':authority_context(campaign),
                           'historical_results_not_current_instructions':[j['result'] for j in campaign['jobs'] if j['result'] and j['result']['reviewed']], 'abandoned_tasks':state.get('abandoned',[])[-3:]}
                plan,record = self.call(state,'plan','coordinator',
                    'Select one decision-changing experiment. Output JSON with question, rationale, alternative, decision. '+DESIGN_SCHEMA+
                    'Current context: '+json.dumps(context))
                state.update(plan=plan,plan_execution=record['thread_id'],phase='implement')
            elif phase=='implement':
                proposal,record = self.call(state,'implement','worker',
                    'Implement the proposed experiment using the supplied recoverable baseline or provide a complete replacement '
                    'candidate module with resolve(d), execute(ctx,trial,checkpoint), finalize(ctx,results). '
                    'Return JSON with question,rationale,alternative,decision (all four strings), conditions (online/frozen/random), seeds (integer list), '
                    'decisions (16..4096), chunk (8..decisions), updates (0..200), trial_seconds (30..1800), '
                    'implementation (null to use baseline or full module text). At most 3 conditions and 3 seeds. '+DESIGN_SCHEMA+
                    'The initial campaign should use a small matched online/frozen/random comparison and avoid repeatability claims from one seed. '
                    'Use bounded trials that can finish; the 16-decision/two-update integration run took about 45 seconds. '
                    + ('Qualification constraint: use baseline (implementation null), conditions online/frozen/random, seeds [0], decisions 16, chunk 8, updates 2, trial_seconds 180. This tests execution/evidence validity only; no behavioral improvement claim. ' if self.deployment.get('qualification') else '')+
                    'Plan: '+json.dumps(state['plan'])+'\nBaseline source and API:\n'+self.deployment['method_context'])
                for key in ('question','rationale','alternative','decision'):
                    if not isinstance(proposal.get(key),str):
                        if not proposal.get(key):
                            raise ValueError('Proposal is missing '+key)
                        proposal[key]=json.dumps(proposal[key])
                state.update(proposal=proposal,producer_execution=record['thread_id'],phase='prepare')
            elif phase=='prepare':
                validate_design(state['proposal'],campaign)
                key = self.campaign+':package:'+str(state['cycle'])+':'+str(state.get('revision_attempt',0))
                package = self.client.call(self.deployment['root'],'prepare_package',key,
                                           proposal=state['proposal'],isolation=self.deployment['isolation'])
                validated = self.client.call(package['root'],'validate',key+':validate',experiment='candidate',
                                             isolation=self.deployment['isolation'])
                if validated['revision'] in state.get('rejected_revisions',[]):
                    if state.get('revision_attempt',0)>=2:
                        from .recovery import abandon
                        abandon(self,state,'implement','Two method revisions failed to correct the rejected executable method')
                        return
                    raise ValueError('Executable method is unchanged since rejection; make a substantive correction before requesting another review')
                state['package'] = package
                state['validated'] = validated
                state['revision'] = validated['revision']
                state['hypothesis'] = self.command(state,'propose','worker','propose',
                    **{k:state['proposal'][k] for k in ('question','alternative','decision')},
                    explanation=state['proposal']['rationale'],method_revision=state['revision'],
                    execution=state['producer_execution'])['hypothesis']
                state['phase']='review'
            elif phase=='review':
                review,record = self.call(state,'review','reviewer',
                    'This is PRE-EXECUTION method review: decide whether this bounded experiment is eligible to run, '
                    'not whether it has already succeeded. Execution and recovery results are assessed after the run. '
                    'Do not require future results as a prerequisite for collecting them. Reject actual scientific-contract '
                    'violations, unsupported inputs, unsafe resource bounds, or missing recovery design. '
                    'Independently review the exact proposed method for scientific eligibility, matched controls, bounded runtime, '
                    'checkpoint compatibility and whether the proposed question is answerable. '
                    'A safe no-op is not an eligible experiment. Reject methods that only list files, raise an '
                    'intentional error, or stop without measuring the proposed effect. For a pass, include '
                    'answerability: {executes_test:true, collects_measurements:true, distinguishes_outcomes:true, '
                    'code_evidence:"cite the exact execution and measurement functions and what they do"}. '
                    'Return JSON with result (pass/fail/inconclusive) and rationale. Require conservative conclusions, not a star claim. '
                    'Exact revision: '+state['revision']+'\nProposal: '+json.dumps(state['proposal'])+
                    '\nValidated resolved configuration and provenance: '+json.dumps(state.get('validated',state['package'].get('resolved',{})))+
                    '\nBaseline source and API:\n'+self.deployment['method_context'])
                if not isinstance(review['rationale'],str):
                    review['rationale'] = json.dumps(review['rationale'])
                self.command(state,'review','reviewer','review',hypothesis=state['hypothesis'],
                             method_revision=state['revision'],execution=record['thread_id'],result=review['result'],
                             rationale=review['rationale'],evidence=[record['evidence']])
                if review['result']!='pass':
                    state.setdefault('rejected_revisions',[]).append(state['revision'])
                    if state.get('revision_attempt',0)>=2:
                        from .recovery import abandon
                        abandon(self,state,'implement','Method rejected after two revisions: '+review['rationale'])
                        return
                    task=state.setdefault('tasks',{}).setdefault(str(state['cycle'])+':implement',{'attempt':0,'failures':[]})
                    if task['attempt']>=2:
                        from .recovery import abandon
                        abandon(self,state,'implement','Implementation repair allowance exhausted after review rejection')
                        return
                    task['attempt']+=1
                    task['failures'].append({'reason':review['rationale'],'evidence':record['evidence']})
                    state['plan']['review_feedback']=review['rationale']
                    state.update(revision_attempt=state.get('revision_attempt',0)+1,phase='implement')
                else:
                    state['phase']='dispatch'
            elif phase=='dispatch':
                validate_design(state['proposal'],campaign)
                definition = state['package']['resolved']['definition']
                seconds = definition['limits']['overall_seconds']
                if seconds > campaign['work_deadline']-time.time():
                    raise ValueError('Reviewed experiment does not fit remaining campaign time')
                manifest = {'root':str(self.broker.root),'experiment':'candidate','revision':state['revision'],
                            'execution_backend':{'kind':'wsl',**{k:self.deployment[k] for k in ('distribution','base','isolation')},
                                                 'root':state['package']['root']}}
                job = self.command(state,'job','coordinator','prepare_job',hypothesis=state['hypothesis'],seconds=seconds,manifest=manifest)['job']
                state['job'] = job
                self.save(state)
                current = next(j for j in self.broker.store.inspect(self.broker.owner,self.campaign)['jobs'] if j['id']==job)
                if current['state']=='prepared':
                    reservation = self.command(state,'reserve','tool','reserve',resource='artifact_bytes',amount=1024**3,
                                               activity=job,bound_evidence='Fixed 4 GiB campaign volume; 1 GiB experiment reservation')['reservation']
                    self.broker.dispatch(self.campaign,job,reservation)
                state['phase']='execute'
            elif phase=='execute':
                self.broker.reconcile(self.campaign)
                campaign = self.broker.store.inspect(self.broker.owner,self.campaign)
                job = next(j for j in campaign['jobs'] if j['id']==state['job'])
                if job['state']=='prepared' and job.get('recovery'):
                    state['phase']='recover'
                    self.save(state)
                    return
                if job['state'] in {'prepared','dispatching','running','unknown','cancelling'}:
                    return
                snapshot = Execution(self.broker.root,job['manifest']).snapshot(job['external_id'])
                state['observed'] = snapshot['state']
                if job['state']=='failed' and job['checkpoints'] and len(job['attempts'])<3 and not snapshot['state'].get('resource_fault'):
                    remaining = min(job['seconds'],campaign['work_deadline']-time.time()-10)
                    self.broker.prepare_resume(self.campaign,job['id'],remaining)
                    state['phase']='recover'
                else:
                    state['phase']='assess'
            elif phase=='recover':
                job = next(j for j in campaign['jobs'] if j['id']==state['job'])
                if job['state']=='prepared':
                    reservation = self.command(state,'recovery-reserve-'+str(len(job['attempts'])),'tool','reserve',
                                               resource='artifact_bytes',amount=1024**3,activity=job['id'],
                                               bound_evidence='Bounded continuation reservation')['reservation']
                    self.broker.dispatch(self.campaign,job['id'],reservation)
                state['phase']='execute'
            elif phase=='assess':
                conclusion,record = self.call(state,'assess','coordinator',
                    'Assess this completed experiment. Return JSON with tested,finding,limitations,next_decision (strings), '
                    'validity (valid/invalid/incomplete), behavioral_progress and decision_value (booleans). '
                    'Separate execution validity, hypothesis evidence and behavioral improvement. Count negative evidence only if it changes a decision. '
                    'No repeatability claim without independent seeds, no star claim from proxies. '
                    'Proposal: '+json.dumps(state['proposal'])+'\nObserved evidence: '+json.dumps(state['observed']))
                fields = {key:conclusion[key] for key in ('tested','finding','limitations','next_decision','validity','behavioral_progress','decision_value')}
                for key in ('tested','finding','limitations','next_decision'):
                    if not isinstance(fields[key],str):
                        fields[key] = json.dumps(fields[key])
                prior=next(j for j in campaign['jobs'] if j['id']==state['job']).get('result')
                extra={'subject_hash':digest(prior)} if prior else {}
                self.command(state,'conclude-'+str(state.get('reassessment',0)),'coordinator','reassess' if prior else 'conclude',job=state['job'],**fields,evidence=[record['evidence']],**extra)
                state['phase']='review_conclusion'
            elif phase=='review_conclusion':
                job = next(j for j in campaign['jobs'] if j['id']==state['job'])
                review,record = self.call(state,'review_conclusion','reviewer',
                    'Independently assess whether the conclusion is supported. Return JSON with result (pass/fail/inconclusive), rationale. '
                    'Conclusion: '+json.dumps(job['result'])+'\nEvidence: '+json.dumps(state['observed']))
                if not isinstance(review['rationale'],str):
                    review['rationale'] = json.dumps(review['rationale'])
                self.command(state,'conclusion_review-'+str(state.get('reassessment',0)),'reviewer','review_conclusion',job=job['id'],
                             subject_hash=digest(job['result']),execution=record['thread_id'],result=review['result'],evidence=record['evidence'])
                if review['result']!='pass':
                    if state.get('reassessment',0)<2:
                        state['reassessment']=state.get('reassessment',0)+1
                        state['revision_attempt']=state.get('revision_attempt',0)+1
                        state['proposal']['conclusion_feedback']=review['rationale']
                        state['phase']='assess'
                        self.save(state)
                    else:
                        from .recovery import abandon
                        abandon(self,state,'assess','Conclusion remains unsupported: '+review['rationale'])
                    return
                if state.get('investigating'):
                    supported=job['result']['validity']=='valid' and job['result']['decision_value']
                    self.command(state,'investigation-end','coordinator','end_investigation',resolved=supported,findings=job['result']['finding'],alternatives=job['result']['next_decision'],evidence=record['evidence'])
                    state['investigating']=False
                    if not supported:self.command(state,'plateau-stop','coordinator','stop',reason='Investigation did not resolve the plateau')
                if job['result']['validity']!='valid' and not job['result']['decision_value']:
                    self.ai.command(self.campaign,'task_abandoned',task='evidence:'+job['id'],task_class='evidence',
                                    evidence='Reviewed incomplete/invalid execution produced no decision-changing evidence: '+record['evidence'])
                elif job['result']['validity']=='valid' and (job['result']['decision_value'] or job['result']['behavioral_progress']):
                    self.ai.command(self.campaign,'task_recovered',task='evidence:'+job['id'],task_class='evidence')
                state.update(cycle=state['cycle']+1,phase='plan',revision_attempt=0,reassessment=0)
            if phase in {'plan','assess','review','review_conclusion'}:
                self.ai.command(self.campaign,'task_recovered',task=str(state['cycle'])+':'+phase,task_class=phase)
            if phase=='dispatch':
                self.ai.command(self.campaign,'task_recovered',task=str(state['cycle'])+':implement',task_class='implement')
            self.save(state)


def refresh_all(broker):
    for campaign in broker.store.inspect(broker.owner):
        if campaign['status'] not in {'active','closing'}:
            continue
        directory = broker.directory/'research'/campaign['id']
        deployment = directory/'deployment.json'
        if not deployment.exists():
            continue
        research = Research(broker,campaign['id'],atomic.read_json(deployment))
        try:
            research.ai.reconcile(campaign['id'])
            research.step()
        except (lock.LockBusy, RecoveryPending):
            continue  # Another process may own the nonblocking cycle lock.
        except Exception as error:
            if getattr(error,'code',None)=='stale':
                continue
            local = atomic.read_json(research.path)
            from .resources import ResourceUnavailable
            if isinstance(error,ResourceUnavailable):
                broker.resource_incident(campaign['id'],error.facts,str(error))
                continue
            if isinstance(error,(KeyError,TypeError,json.JSONDecodeError)) or (isinstance(error,ValueError) and local.get('phase') in {'prepare','implement','plan','assess'}):
                controller_failure(research,local,error)
                continue
            research.ai.command(campaign['id'],'observe_incident',category='isolation' if isinstance(error,PermissionError) else 'integrity',scope='campaign',reason='Research platform could not safely complete an operation',evidence=str(error),status='human_decision_required',next_action='Repair and qualify the affected platform component outside the campaign')
            local['error']=str(error)
            atomic.write_json(research.path,local)
            current = broker.store.inspect(broker.owner,campaign['id'])
            if current['status']=='active':
                broker.store.command(broker.owner,uuid.uuid4().hex,'stop',campaign['id'],current['revision'],
                                     reason='Platform or unrecoverable research fault: '+str(error)[:1000],system_stop=True)
