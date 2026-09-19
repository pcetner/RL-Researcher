"""Trusted HAWS adapter. Workers never receive this broker's credentials.

Runtime activation is separate from preparation. A dashboard cannot manufacture
independent review or runtime evidence through a launch button.
"""
import threading
import uuid
import time
from pathlib import Path

from . import atomic, lock
from .campaign_execution import Execution, authorization


class Campaigns:
    def __init__(self, root):
        from haws_core import Store
        self.root = Path(root).resolve()
        self.directory = self.root / ".haws"
        self.directory.mkdir(exist_ok=True)
        self.store = Store(self.directory / "control.db")
        self.lock = threading.RLock()
        self.reconciliation_errors = {}
        self.views = {}
        owner_path = self.directory / "owner-token"
        if owner_path.exists():
            self.owner = owner_path.read_text(encoding="utf-8").strip()
        else:
            principal = self.store.provision("local-human-owner", "Owner", ["*"])
            self.owner = principal["token"]
            atomic.write_bytes(owner_path, self.owner.encode())
        tool_path = self.directory / "broker-token"
        if tool_path.exists():
            self.tool = tool_path.read_text(encoding="utf-8").strip()
        else:
            principal = self.store.provision("research-broker", "Tool", [
                "tick", "dispatch", "job_update", "checkpoint", "reserve", "meter", "release", "resume_job", "summarize"],
                owner_token=self.owner)
            self.tool = principal["token"]
            atomic.write_bytes(tool_path, self.tool.encode())

    def inspect(self):
        runtime = self.runtime_status()
        if self.reconciliation_errors:
            runtime = dict(runtime,ready=False,blockers=list(runtime.get('blockers',[]))+
                           [f'Campaign {key} reconciliation needs attention: {value}'
                            for key,value in self.reconciliation_errors.items()])
        research = {}
        for path in (self.directory/'research').glob('*/state.json'):
            state = atomic.read_json(path)
            research[path.parent.name] = {key:state.get(key) for key in ('phase','cycle','error')}
        from . import agent_activity
        campaigns = self.store.inspect(self.owner)
        for c in campaigns:
            for op in ('authorize','start','renew','resume_session'):
                if not runtime.get('ready'):
                    c['eligibility'][op]['allowed']=False
                    c['eligibility'][op]['reasons']+=runtime.get('blockers',[])
                if op in {'start','renew','resume_session'} and any(other['id']!=c['id'] and other['status'] in {'active','closing'} for other in campaigns):
                    c['eligibility'][op]['allowed']=False
                    c['eligibility'][op]['reasons'].append('Another campaign is active')
        agents = {c['id']:agent_activity.project(self.directory,c) for c in campaigns}
        return {"campaigns": campaigns, "runtime": runtime,'live':self.views,'research':research,'agents':agents,
                "history_policy": "Archived outcomes preserved; no historical restart migration"}

    def refresh(self):
        """Reconcile connected campaigns; failures stay visible and never trigger redispatch."""
        for state in self.store.inspect(self.owner):
            if state['status'] not in {'active','closing'}:
                continue
            try:
                self.reconcile(state['id'])
                if state['id'] not in self.views:
                    latest = next((j for j in reversed(state['jobs']) if j.get('external_id')),None)
                    if latest:
                        self.views[state['id']] = {'execution':latest['external_id'],'observed_at':time.time(),
                                                  **Execution(self.root,latest['manifest']).snapshot(latest['external_id'])}
                self.reconciliation_errors.pop(state['id'],None)
            except Exception as error:
                self.reconciliation_errors[state['id']] = str(error)

    def runtime_status(self):
        path = self.directory / "runtime-assessment.json"
        if path.exists():
            assessment = atomic.read_json(path)
            if assessment.get('ready'):
                from .deployment import verify
                try:
                    verify(assessment)
                except (ValueError,OSError) as error:
                    return dict(assessment,ready=False,blockers=[str(error)])
            return assessment
        return {"ready": False, "mode": "unverified",
                "blockers": ["Unattended runtime, usage bounds, and recovery require a recorded capability assessment"],
                "explanation": "Prepare and inspect work now. Autonomous execution stays disabled until verified."}

    def command(self, envelope):
        operation = envelope.get("operation")
        allowed = {"prepare", "authorize", "instruct", "stop", "resolve", "start", "renew", "resume_session", "decide"}
        if operation not in allowed:
            raise ValueError("This operation belongs to the trusted broker or an independent reviewer")
        if operation in {"authorize", "start", "renew", "resume_session"} and (not self.runtime_status().get("ready") or self.reconciliation_errors):
            raise ValueError("Runtime checks have not established unattended execution readiness")
        with self.lock:
            if operation in {'authorize','start','renew','resume_session'}:
                from .deployment import install,preflight,host_preflight
                if operation!='authorize':host_preflight(self.runtime_status().get('host_baseline'))
                deployment = install(self,envelope['campaign'])
                if deployment.get('qualification'):
                    raise ValueError('Qualification campaigns cannot be launched from overnight controls')
                if operation!='authorize':preflight(deployment)
                if operation=='authorize':
                    envelope = dict(envelope,runtime=self.runtime_status()['capabilities'])
                if operation in {'start','renew','resume_session'} and any(c['id']!=envelope['campaign'] and c['status'] in {'active','closing'} for c in self.store.inspect(self.owner)):
                    raise ValueError('Finish or stop the existing campaign before launching another')
            return self.store.command(self.owner, **envelope)

    def tool_command(self, campaign, operation, **inputs):
        from haws_core import ControlError
        for _ in range(8):
            current = self.store.inspect(self.tool, campaign)
            revision = current['revision']
            if operation=='job_update':
                observed = next(j for j in current['jobs'] if j['id']==inputs['job'])
                if observed['state']==inputs['state'] and (not inputs.get('external_id') or observed['external_id']==inputs['external_id']):
                    return {'campaign':campaign,'revision':revision}
            if operation=='meter' and any(u['id']==inputs['usage_id'] for u in current['usage']):
                return {'campaign':campaign,'revision':revision}
            try:
                return self.store.command(self.tool, uuid.uuid4().hex, operation,
                                          campaign, revision, **inputs)
            except ControlError as error:
                if error.code != "stale":
                    raise
        raise ValueError("Concurrent changes prevented broker operation; retry after reconciliation")

    def resource_incident(self,campaign,facts,evidence):
        """Trusted physical measurement, distinct from reservation arithmetic."""
        state=self.store.inspect(self.owner,campaign)
        existing=next((r for r in state.get('incidents',[]) if r['category']=='capacity' and r['scope']=='campaign' and r['status']!='resolved' and r.get('facts')==facts),None)
        if existing:return
        return self.store.command(self.owner,uuid.uuid4().hex,'observe_incident',campaign,state['revision'],
            category='capacity',scope='campaign',reason='Physical artifact capacity cannot fit the required publication while preserving its reserve',
            facts=facts,evidence=evidence,status='human_decision_required',next_action='Archive retained evidence or approve a storage change; verify measured capacity before resuming game work')

    def dispatch(self, campaign, job_id, reservation):
        with self.lock:
            runtime=self.runtime_status()
            deployment_path=self.directory/'research'/campaign/'deployment.json'
            if deployment_path.exists():
                from .deployment import preflight
                deployment=atomic.read_json(deployment_path)
                if not deployment.get('qualification') and not runtime.get('ready'):raise ValueError('Execution qualification is not current')
                preflight(deployment)
            state = self.store.inspect(self.tool, campaign)
            job = next(j for j in state["jobs"] if j["id"] == job_id)
            manifest = job["manifest"]
            if Path(manifest["root"]).resolve() != self.root:
                raise ValueError("Job belongs to another project")
            # A committed intent must precede the external call. A lost response is reconciled below.
            self.tool_command(campaign, "dispatch", job=job_id,
                              manifest_hash=job["manifest_hash"], reservation=reservation)
            dispatched = next(j for j in self.store.inspect(self.tool,campaign)['jobs'] if j['id']==job_id)
            try:
                execution = Execution(self.root,manifest)
                request = f"haws:{campaign}:{job_id}:{len(dispatched['attempts'])}"
                if dispatched.get('recovery'):
                    identity = dispatched['external_id']
                    execution.resume(identity,request,authorization(campaign,dispatched))
                else:
                    identity = execution.start(request,authorization(campaign,dispatched))
            except Exception as error:
                self.tool_command(campaign, "job_update", job=job_id, state="unknown",
                                  evidence="Dispatch result requires reconciliation: " + str(error))
                raise
            self.tool_command(campaign, "job_update", job=job_id, state="running",
                              external_id=identity, evidence="Runner returned its saved execution identity")
            return identity

    def prepare_resume(self, campaign, job_id, seconds):
        """Validate the same committed checkpoint before requesting fresh authority."""
        with self.lock:
            job = next(j for j in self.store.inspect(self.tool,campaign)['jobs'] if j['id']==job_id)
            if not job['external_id'] or not job['checkpoints']:
                raise ValueError('No registered execution and committed checkpoint to recover')
            snapshot = Execution(self.root,job['manifest']).snapshot(job['external_id'])
            from haws_core.store import digest
            if digest(snapshot['checkpoints']) != job['checkpoints'][-1]['hash']:
                raise ValueError('Recovery checkpoint changed since reconciliation')
            if snapshot['state']['state'] not in {'Stopped','Failed','Incomplete'}:
                raise ValueError('Confirm external termination before recovery')
            return self.tool_command(campaign,'resume_job',job=job_id,remaining_seconds=seconds,
                                     checkpoint_hash=job['checkpoints'][-1]['hash'],
                                     manifest_hash=job['manifest_hash'],
                                     validation_evidence='Trusted transport validated committed checkpoint hashes; worker checks domain continuation')

    def reconcile(self, campaign):
        # Separate brokers (service and reconnecting client) share this lease.
        # Metering and releasing liability must not interleave across processes.
        try:
            with lock.exclusive(self.directory/'reconciliation'/f'{campaign}.lock'):
                self._reconcile(campaign)
        except lock.LockBusy:
            return

    def _reconcile(self, campaign):
        with self.lock:
            state = self.store.inspect(self.tool, campaign)
            if ((state['status']=='active' and time.time()>=state['work_deadline'])
                    or (state['status']=='closing' and time.time()>=state['deadline'] and not state.get('deadline_reached'))):
                self.tool_command(campaign, "tick")
            state = self.store.inspect(self.tool, campaign)
            for job in state["jobs"]:
                pending_liability = any(r['activity']==job['id'] and r['remaining'] for r in state['reservations'])
                if job["state"] not in {"dispatching", "running", "unknown", "cancelling"} and not pending_liability:
                    continue
                identity = job["external_id"]
                execution = Execution(self.root,job['manifest'])
                if not identity:
                    request = f"haws:{campaign}:{job['id']}:{len(job['attempts'])}"
                    matches = execution.find(request)
                    if len(matches) != 1:
                        if job["state"] != "unknown":
                            self.tool_command(campaign, "job_update", job=job["id"], state="unknown",
                                              evidence="No unique saved external identity; no redispatch permitted")
                        continue
                    identity = matches[0]
                snapshot = execution.snapshot(identity)
                external = snapshot['state']
                if external.get('resource_fault'):
                    self.resource_incident(campaign,external['resource_fault'],'Trusted runner resource measurement for '+identity)
                self.views[campaign] = {'execution':identity,'observed_at':time.time(),**snapshot}
                if job.get('recovery') and external['attempt'] < len(job['attempts']):
                    if job['state'] != 'unknown':
                        self.tool_command(campaign,'job_update',job=job['id'],state='unknown',
                                          evidence='Recovery dispatch is unconfirmed; prior attempt cannot resolve this attempt')
                    continue
                if snapshot['checkpoints']:
                    from haws_core.store import digest
                    point_hash = digest(snapshot['checkpoints'])
                    if not job['checkpoints'] or job['checkpoints'][-1]['hash'] != point_hash:
                        self.tool_command(campaign,'checkpoint',job=job['id'],hash=point_hash,
                                          path=snapshot['directory'], manifest_hash=job['manifest_hash'],complete=True,reconciled=True)
                if job["state"] == "cancelling" and external["state"] in {"Running", "Stopping"}:
                    request = f"haws-stop:{campaign}:{job['id']}:{len(job['attempts'])}"
                    execution.stop(identity,request)
                    if external.get('stop_requested') and time.time() - external['stop_requested'] >= 60:
                        execution.stop(identity,request+':force',True)
                    continue
                translated = {"Running": "running", "Completed": "succeeded", "Stopped": "cancelled",
                              "Failed": "failed", "Incomplete": "failed"}.get(external["state"])
                if translated in {'succeeded','failed','cancelled'} and 'retained_bytes' in snapshot:
                    latest = self.store.inspect(self.tool,campaign)
                    reservations = {r['id'] for r in latest['reservations'] if r['activity']==job['id'] and r['resource']=='artifact_bytes'}
                    measured = sum(u['amount'] for u in latest['usage'] if u['reservation'] in reservations)
                    if job['reservation'] in reservations and snapshot['retained_bytes'] > measured:
                        self.tool_command(campaign,'meter',reservation=job['reservation'],
                                          amount=snapshot['retained_bytes']-measured,
                                          usage_id=f"retained:{job['id']}:{snapshot['retained_bytes']}",
                                          evidence='Trusted Linux retained-byte measurement; cumulative high-water accounting')
                latest_state=self.store.inspect(self.owner,campaign)
                for incident in latest_state.get('incidents',[]):
                    if incident['category']=='resource' and incident['scope']==job['id'] and incident['status']!='resolved' and incident['facts']['measured_total']<=incident['facts']['limit']:
                        self.store.command(self.owner,uuid.uuid4().hex,'reconcile_incident',campaign,latest_state['revision'],incident=incident['id'],evidence='Trusted retained-byte measurement fits cumulative authorized capacity')
                        latest_state=self.store.inspect(self.owner,campaign)
                if translated and (translated != job["state"] or not job["external_id"]):
                    self.tool_command(campaign, "job_update", job=job["id"], state=translated,
                                      external_id=identity, evidence=f"Reconciled runner journal: {external['state']}")
                if translated in {'succeeded','failed','cancelled'} and 'retained_bytes' in snapshot:
                    for reserved in latest['reservations']:
                        if reserved['id'] in reservations and reserved['remaining']:
                            self.tool_command(campaign,'release',reservation=reserved['id'],remaining_liability=0,
                                              evidence='External termination confirmed and retained bytes measured')

    def close(self):
        """No autonomous worker is started by constructing this adapter."""
