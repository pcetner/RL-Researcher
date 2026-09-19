import json
import shutil
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import pytest
from rl_researcher import atomic, board_view, checkpoint, experiment, runner, serve, runlog

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "deterministic"


@pytest.fixture
def project(tmp_path):
    for name in ("research.json", "definition.json", "experiment.py"):
        shutil.copy2(EXAMPLE / name, tmp_path / name)
    d = atomic.read_json(tmp_path / "definition.json")
    for t in d["trials"]:
        t.update(decisions=12, delay=0.03, seconds=120)
    atomic.write_json(tmp_path / "definition.json", d)
    return tmp_path


def wait(directory, predicate, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        s = runner.state(directory)
        if predicate(s):
            return s
        time.sleep(0.03)
    raise AssertionError(runner.state(directory))


def start(project, request="test"):
    resolved = experiment.validate(project, "deterministic")
    identity = runner.start(project, "deterministic", resolved["revision"], request)
    return project / ".research" / "executions" / identity, resolved


def test_discovery_and_stale_launch(project):
    p = project / "experiment.py"
    p.write_text("raise RuntimeError('Must not import during discovery')\n")
    assert board_view.catalog(project)["experiments"][0]["validation"] == "Validation required"
    shutil.copy2(EXAMPLE / "experiment.py", p)
    resolved = experiment.validate(project, "deterministic")
    p.write_text(p.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="Definition changed"):
        runner.start(project, "deterministic", resolved["revision"], "stale")
    assert not list((project / ".research" / "executions").glob("*/manifest.json"))


def test_campaign_deadline_terminates_uncooperative_worker(project):
    (project/'experiment.py').write_text(
        'import time\ndef resolve(d): return d\n'
        'def execute(ctx, trial, checkpoint):\n'
        '    while True: time.sleep(.1)\n'
        'def finalize(ctx, results): pass\n')
    resolved = experiment.validate(project,'deterministic')
    authority = {'campaign':'fixture','job':'one','deadline':time.time()+3}
    identity = runner.start(project,'deterministic',resolved['revision'],'bounded',authorization=authority)
    directory = project/'.research/executions'/identity
    finished = wait(directory,lambda s:s['state']=='Incomplete',seconds=10)
    assert finished['worker_exited'] and finished['forced']
    assert time.time() < authority['deadline']+5
    with pytest.raises(ValueError,match='authorization'):
        runner.resume(directory)
    with pytest.raises(ValueError,match='authorization'):
        runner.resume(directory,authorization=authority)
    with pytest.raises(ValueError,match='different experiment'):
        runner.start(project,'deterministic',resolved['revision'],'bounded',
                     authorization=dict(authority,deadline=time.time()+60))


def test_duplicate_and_saved_reconstruction(project):
    directory, resolved = start(project)
    assert runner.start(project, "deterministic", resolved["revision"], "test") == directory.name
    with pytest.raises(ValueError, match="active"):
        runner.start(project, "deterministic", resolved["revision"], "other")
    s = wait(directory, lambda s: s["state"] == "Completed")
    assert len([e for e in runlog.events(directory) if e["kind"] == "attempt"]) == 1
    assert all(t["result"]["measurements"]["sum"] == 78 for t in s["trials"].values())
    (directory / "state.json").unlink()
    rebuilt = board_view.execution(directory)
    assert rebuilt["state"]["state"] == "Completed"
    assert rebuilt["state"]["artifacts"]


def test_duplicate_request_cannot_change_inputs(project):
    directory, resolved = start(project, "stable-request")
    try:
        with pytest.raises(ValueError, match="different experiment or revision"):
            runner.start(project, "deterministic", "changed-revision", "stable-request")
        with pytest.raises(ValueError, match="different experiment or revision"):
            runner.start(project, "another-experiment", resolved["revision"], "stable-request")
        assert runner.start(project, "deterministic", resolved["revision"], "stable-request") == directory.name
        wait(directory, lambda s: s["state"] == "Completed")
    finally:
        if runner.state(directory)["state"] in ("Running", "Stopping"):
            runner.request_stop(directory)


def test_resume_frozen_source_and_inputs(project):
    d = atomic.read_json(project / "definition.json")
    d["trials"][0].update(decisions=100, delay=0.04)
    payload = project / "input.txt"
    payload.write_text("original")
    d["inputs"] = {"data": {"path": "input.txt", "sha256": atomic.digest(payload)}}
    atomic.write_json(project / "definition.json", d)
    directory, _ = start(project)
    wait(directory, lambda s: s["trials"]["first"]["progress"] >= 6)
    runner.request_stop(directory)
    stopped = wait(directory, lambda s: s["state"] == "Stopped")
    original_deadline = stopped["trials"]["first"]["deadline"]
    payload.write_text("changed")
    with pytest.raises(ValueError, match="input has changed"):
        runner.resume(directory)
    payload.write_text("original")
    # Editing current project implementation cannot replace the captured execution source.
    (project / "experiment.py").write_text("raise RuntimeError('Changed live project')")
    runner.resume(directory)
    final = wait(directory, lambda s: s["state"] == "Completed")
    assert final["trials"]["first"]["deadline"] > original_deadline
    assert final["trials"]["first"]["result"]["measurements"]["sum"] == 5050
    assert final["attempt"] == 2
    recovered = [
        e for e in runlog.events(directory) if e["kind"] == "trial" and e["data"].get("recovered")
    ]
    assert recovered and recovered[0]["data"]["recovered"]["decision"] >= 6


def test_partial_checkpoint_and_corruption(project, monkeypatch):
    directory = project / "execution"
    directory.mkdir()
    payload = directory / "trials" / "first" / "work" / "state.txt"
    payload.parent.mkdir(parents=True)
    payload.write_text("valid")
    m = {"revision": "abc"}
    atomic.write_json(directory/'manifest.json',m)
    committed = checkpoint.publish(directory, m, "first", payload, 2)

    def fail(*args, **kwargs):
        raise OSError("partial write")

    monkeypatch.setattr(shutil, "copy2", fail)
    with pytest.raises(OSError):
        checkpoint.publish(directory, m, "first", payload, 3)
    restored = checkpoint.validate(directory, m, committed)
    assert (restored / "state.txt").read_text() == "valid"
    (restored / "state.txt").write_text("corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        checkpoint.validate(directory, m, committed)


def test_finalization_retry_only(project):
    with (project / "experiment.py").open("a") as f:
        f.write("""
original_finalize=finalize
def finalize(ctx, results):
    flag=ctx.directory/'finalizer-attempted'
    if not flag.exists():
        flag.write_text('attempted')
        raise RuntimeError('Deliberate analysis failure')
    original_finalize(ctx, results)
""")
    directory, _ = start(project)
    wait(directory, lambda s: s["state"] == "Failed")
    before = {p: atomic.digest(p) for p in directory.glob("trials/*/result.json")}
    assert len(before) == 2
    runner.resume(directory)
    wait(directory, lambda s: s["state"] == "Completed")
    assert before == {p: atomic.digest(p) for p in before}
    assert len([e for e in runlog.events(directory) if e["kind"] == "trial"]) == 2


def test_fixed_budget(project):
    d = atomic.read_json(project / "definition.json")
    d["limits"]["overall_seconds"] = 1
    for t in d["trials"]:
        t.update(decisions=100, delay=0.1)
    atomic.write_json(project / "definition.json", d)
    directory, _ = start(project)
    wait(directory, lambda s: s["state"] == "Incomplete")
    with pytest.raises(ValueError, match="Overall execution budget has been exhausted"):
        runner.resume(directory)


def test_http_server_restart_and_routes(project):
    d = atomic.read_json(project / "definition.json")
    d["trials"][0].update(decisions=100, delay=0.04)
    atomic.write_json(project / "definition.json", d)
    server = serve.create_server(project, 0)
    with pytest.raises(OSError):
        serve.create_server(project, server.server_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:" + str(server.server_port)

    def get(path):
        return json.load(urllib.request.urlopen(url + path))

    token = get("/api/catalog")["token"]

    def post(op, body):
        return json.load(
            urllib.request.urlopen(
                urllib.request.Request(
                    url + "/api/" + op,
                    json.dumps(body).encode(),
                    headers={"X-Research-Token": token},
                )
            )
        )

    revision = post("validate", {"experiment": "deterministic"})["revision"]
    identity = post(
        "start", {"experiment": "deterministic", "revision": revision, "request_id": "http"}
    )["id"]
    directory = project / ".research" / "executions" / identity
    wait(directory, lambda s: s["trials"]["first"]["progress"] >= 1)
    server.shutdown()
    server.server_close()
    continued = wait(directory, lambda s: s["trials"]["first"]["progress"] >= 10)
    assert continued["state"] == "Running"
    server = serve.create_server(project, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:" + str(server.server_port)
    token = get("/api/catalog")["token"]
    post("stop", {"id": identity})
    wait(directory, lambda s: s["state"] == "Stopped")
    samples = get("/api/samples?id=" + identity + "&trial=first")
    assert samples and all(s["trial"] == "first" for s in samples)
    assert get("/api/execution?id=" + identity + "&after=1")["state"]["state"] == "Stopped"
    with pytest.raises(urllib.error.HTTPError):
        post("force", {"id": identity})
    server.shutdown()
    server.server_close()


def test_force_stop_owns_only_its_tree(project):
    with (project / "experiment.py").open("a") as f:
        f.write("""
def execute(ctx,trial,checkpoint):
    import subprocess,sys,time
    code='import time,pathlib'+chr(10)+"p=pathlib.Path('child-heartbeat')"+chr(10)+'while True:'+chr(10)+' p.write_text(str(time.time()))'+chr(10)+' time.sleep(.1)'
    child=subprocess.Popen([sys.executable,'-c',code],cwd=ctx.work)
    ctx.progress(1)
    while True: time.sleep(.1)
""")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        directory, _ = start(project)
        wait(directory, lambda s: s["trials"]["first"]["progress"] == 1)
        heartbeat = directory / "trials/first/work/child-heartbeat"
        until = time.time() + 5
        while not heartbeat.exists() and time.time() < until:
            time.sleep(0.05)
        assert heartbeat.exists()
        runner.request_stop(directory)
        with pytest.raises(ValueError, match="60 seconds"):
            runner.request_stop(directory, True)
        atomic.write_json(directory / "stop-request.json", {"time": time.time() - 61})
        runner.request_stop(directory, True)
        result = wait(directory, lambda s: s["state"] == "Failed")
        assert result["forced"]
        assert unrelated.poll() is None
        before = heartbeat.read_text()
        time.sleep(0.4)
        assert heartbeat.read_text() == before
    finally:
        unrelated.terminate()
        unrelated.wait()


def test_corrupt_latest_checkpoint_falls_back_without_deleting_evidence(tmp_path):
    directory=tmp_path/'execution';directory.mkdir()
    manifest={'id':'fixture','revision':'frozen','started':0,'definition':{'trials':[{'id':'trial','label':'fixture','seconds':100}]}}
    atomic.write_json(directory/'manifest.json',manifest)
    source=directory/'trials/trial/work/state';source.mkdir(parents=True)
    (source/'state.txt').write_text('first')
    first=checkpoint.publish(directory,manifest,'trial',source,1)
    (source/'state.txt').write_text('second')
    second=checkpoint.publish(directory,manifest,'trial',source,2)
    journal=runlog.Journal(directory)
    journal.append('checkpoint',first);journal.append('checkpoint',second)
    journal.append('state',{'state':'Failed','error':'synthetic fault'})
    (directory/second['path']/'payload/state.txt').write_text('corrupt')
    checkpoint.recover_committed(directory)
    state=runlog.project(manifest,runlog.events(directory))
    assert state['trials']['trial']['checkpoint']==first
    assert (directory/second['path']).exists()
    assert any(e['kind']=='checkpoint_quarantined' for e in runlog.events(directory))
