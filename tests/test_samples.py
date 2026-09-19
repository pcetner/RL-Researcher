import pytest

from rl_researcher import atomic, runlog
from test_execution import project, start, wait  # noqa: F401


@pytest.mark.parametrize("wrong_trial", [False, True])
def test_coherent_published_previews(project, wrong_trial):  # noqa: F811
    with (project / "experiment.py").open("a") as f:
        f.write(
            """
def execute(ctx,trial,checkpoint):
    import base64,json,time
    path=ctx.work/'pixel.gif'
    path.write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
    preview=ctx.publish_artifact(path,'Live preview','image/gif')
    previous=ctx.directory/'previous-preview.json'
    if WRONG and previous.exists():preview=json.loads(previous.read_text())
    previous.write_text(json.dumps(preview))
    ctx.sample(1,{'sum':{'value':1,'count':1,'kind':'cumulative'}},preview,force=True)
    time.sleep(.01)
    ctx.sample(2,{'sum':{'value':2,'count':2,'kind':'cumulative'}},preview,force=True)
    return {'measurements':{'sum':2},'decisions':2}
""".replace("WRONG", repr(wrong_trial))
        )
    directory, _ = start(project)
    state = wait(directory, lambda s: s["state"] in ("Completed", "Failed"))
    if wrong_trial:
        assert state["state"] == "Failed"
        assert "Preview must be a committed artifact for this trial" in state["error"]
        return
    assert state["state"] == "Completed"
    samples = [e["data"] for e in runlog.events(directory) if e["kind"] == "sample"]
    assert len(samples) == 4
    assert len({s["preview"]["path"] for s in samples}) == 1
    assert len({s["captured"] for s in samples}) == 4
    for sample in samples:
        image = directory / sample["preview"]["path"]
        assert atomic.digest(image) == sample["preview"]["sha256"]
        assert sample["trial"] == sample["preview"]["trial"]
        assert sample["attempt"] == sample["preview"]["attempt"]
