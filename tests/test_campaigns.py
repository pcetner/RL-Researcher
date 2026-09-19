import json
import shutil
import threading
import urllib.error
import urllib.request
import uuid
import time

import pytest

pytest.importorskip("haws_core")
from rl_researcher import atomic, experiment, runner, serve
from rl_researcher.campaigns import Campaigns
from test_execution import EXAMPLE, wait


@pytest.fixture
def project(tmp_path):
    for name in ("research.json", "definition.json", "experiment.py"):
        shutil.copy2(EXAMPLE / name, tmp_path / name)
    definition = atomic.read_json(tmp_path / "definition.json")
    definition["limits"]["overall_seconds"] = 60
    for trial in definition["trials"]:
        trial.update(decisions=12, delay=.02, seconds=20)
    atomic.write_json(tmp_path / "definition.json", definition)
    return tmp_path


def setup_job(project):
    broker = Campaigns(project)
    prepared = broker.command({"request_id": "prepare", "operation": "prepare",
                              "objective": "Synthetic control acceptance", "constraints": ["Deterministic fixture only"]})
    campaign = prepared["campaign"]
    def command(op, token=None, **inputs):
        return broker.store.command(token or broker.owner, uuid.uuid4().hex, op, campaign,
                                    broker.store.inspect(broker.owner, campaign)["revision"], **inputs)
    runtime = {k: {"passed": True, "evidence": "TEST FIXTURE, not runtime approval"}
               for k in ("isolation", "identity", "usage_bound", "stop", "recovery")}
    command("authorize", runtime=dict(runtime, mode="deterministic"))
    command("start")
    subject = command("propose", question="Does 1..12 sum to 78?", explanation="Exact integer sum",
                      alternative="A restart loses or repeats an integer", decision="Accept tested prefix",
                      method_revision="test-method", execution="fixture-author")["hypothesis"]
    reviewer = broker.store.provision("fixture-independent-identity", "Reviewer", ["review"],
                                      campaign, broker.owner)
    command("review", reviewer["token"], hypothesis=subject, method_revision="test-method",
            execution="fixture-review", result="pass", rationale="n(n+1)/2 establishes expected result",
            evidence=["Synthetic reviewer fixture; not an actual AI review"])
    resolved = experiment.validate(project, "deterministic")
    manifest = {"root": str(project), "experiment": "deterministic", "revision": resolved["revision"]}
    job = command("prepare_job", hypothesis=subject, seconds=60, manifest=manifest)["job"]
    reservation = command("reserve", resource="artifact_bytes", amount=1024**2,
                          activity=job, bound_evidence="Synthetic fixture output allowance")["reservation"]
    return broker, campaign, job, reservation, command


@pytest.mark.parametrize('interrupt_release',[False,True])
def test_two_brokers_cannot_release_before_other_meter(project, monkeypatch,interrupt_release):
    from rl_researcher.campaign_execution import Execution
    broker, campaign, job, reservation, command = setup_job(project)
    other = Campaigns(project)
    command('dispatch',job=job,manifest_hash=broker.store.inspect(broker.owner,campaign)['jobs'][0]['manifest_hash'],reservation=reservation)
    command('job_update',job=job,state='running',external_id='fixture',evidence='Fixture')
    entered, resume = threading.Event(), threading.Event()
    calls = []
    def snapshot(self, identity):
        calls.append(identity)
        entered.set()
        assert resume.wait(5)
        return {'state':{'state':'Completed','attempt':1},'checkpoints':[],
                'directory':'fixture','retained_bytes':100+len(calls)}
    monkeypatch.setattr(Execution,'snapshot',snapshot)
    original=broker.tool_command
    def interrupted(campaign, operation, **inputs):
        if operation=='release' and interrupt_release:
            raise RuntimeError('Supervisor lost before releasing liability')
        return original(campaign,operation,**inputs)
    monkeypatch.setattr(broker,'tool_command',interrupted)
    errors=[]
    def reconcile():
        try: broker.reconcile(campaign)
        except Exception as error: errors.append(error)
    thread=threading.Thread(target=reconcile)
    thread.start()
    assert entered.wait(5)
    other.reconcile(campaign)
    resume.set()
    thread.join(5)
    other.reconcile(campaign)
    state=other.store.inspect(other.owner,campaign)
    assert len(errors)==int(interrupt_release) and not thread.is_alive()
    assert calls==['fixture']*(2 if interrupt_release else 1)
    assert not state['blockers']
    assert sum(u['amount'] for u in state['usage'])==101+int(interrupt_release)
    assert state['reservations'][0]['remaining']==0


def test_real_runner_under_campaign_control(project):
    broker, campaign, job, reservation, command = setup_job(project)
    identity = broker.dispatch(campaign, job, reservation)
    directory = project / ".research/executions" / identity
    finished = wait(directory, lambda s: s["state"] == "Completed")
    broker.reconcile(campaign)
    state = broker.store.inspect(broker.owner, campaign)
    assert state["jobs"][0]["state"] == "succeeded"
    assert state["jobs"][0]["external_id"] == identity
    assert all(t["result"]["measurements"]["sum"] == 78 for t in finished["trials"].values())
    assert state["jobs"][0]["result"] is None  # Process completion is not a scientific conclusion.
    assert state["reservations"][0]["remaining"] == 1024**2  # Needs actual liability reconciliation.


def test_lost_dispatch_response_reconciles_without_relaunch(project, monkeypatch):
    broker, campaign, job, reservation, command = setup_job(project)
    original = runner.start
    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("Reply lost after process start")
    monkeypatch.setattr(runner, "start", lost_response)
    with pytest.raises(ConnectionError):
        broker.dispatch(campaign, job, reservation)
    assert broker.store.inspect(broker.owner, campaign)["jobs"][0]["state"] == "unknown"
    paths = list((project / ".research/executions").iterdir())
    assert len(paths) == 1
    wait(paths[0], lambda s: s["state"] == "Completed")
    broker.reconcile(campaign)
    assert broker.store.inspect(broker.owner, campaign)["jobs"][0]["state"] == "succeeded"
    assert len(list((project / ".research/executions").iterdir())) == 1


def test_campaign_http_draft_instruction_and_runtime_gate(project):
    server = serve.create_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def get(path):
        return json.load(urllib.request.urlopen(base + path))
    try:
        initial = get("/api/campaigns")
        assert not initial["runtime"]["ready"]
        token = initial["token"]
        def post(data):
            request = urllib.request.Request(base + "/api/campaign-command", json.dumps(data).encode(),
                                             headers={"X-Research-Token": token})
            return json.load(urllib.request.urlopen(request))
        draft = post({"request_id": "ui-draft", "operation": "prepare", "objective": "Read evidence",
                      "constraints": ["No launch"]})
        updated = post({"request_id": "ui-direction", "operation": "instruct", "campaign": draft["campaign"],
                        "expected_revision": draft["revision"], "text": "Use existing evidence", "effect": "future"})
        state = get("/api/campaigns")["campaigns"][0]
        assert state["instructions"][0]["text"] == "Use existing evidence"
        with pytest.raises(urllib.error.HTTPError) as error:
            post({"request_id": "no-launch", "operation": "start", "campaign": draft["campaign"],
                  "expected_revision": updated["revision"]})
        assert "Runtime checks" in error.value.read().decode()
        assert not list((project / ".research/executions").glob("*/manifest.json"))
        request = urllib.request.Request(base + "/api/campaigns", headers={"Host": "untrusted.example"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_stop_reaches_worker_without_manual_reconciliation(project):
    definition = atomic.read_json(project/'definition.json')
    definition['trials'][0].update(decisions=200,delay=.04)
    atomic.write_json(project/'definition.json',definition)
    broker,campaign,job,reservation,command = setup_job(project)
    server = serve.create_server(project,0)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        identity = broker.dispatch(campaign,job,reservation)
        directory = project/'.research/executions'/identity
        wait(directory,lambda s:bool(s['trials']['first']['checkpoint']))
        base = f'http://127.0.0.1:{server.server_port}'
        data = json.load(urllib.request.urlopen(base+'/api/campaigns'))
        state = broker.store.inspect(broker.owner,campaign)
        request = urllib.request.Request(base+'/api/campaign-command',
                    json.dumps({'request_id':'http-stop','operation':'stop','campaign':campaign,
                                'expected_revision':state['revision'],'reason':'Human dashboard stop'}).encode(),
                    headers={'X-Research-Token':data['token']})
        json.load(urllib.request.urlopen(request))
        until = time.monotonic()+15
        while time.monotonic()<until:
            state = broker.store.inspect(broker.owner,campaign)
            if state['jobs'][0]['state']=='cancelled':
                break
            time.sleep(.1)
        assert state['jobs'][0]['state']=='cancelled'
        assert state['jobs'][0]['checkpoints']
    finally:
        server.shutdown()
        server.server_close()
