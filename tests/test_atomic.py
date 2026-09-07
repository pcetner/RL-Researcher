"""Atomic writes must survive a transient Windows lock (rl_researcher/atomic.py).

Study 5 died five hours in, with eleven cells still queued, because os.replace raised
PermissionError on the first write into a freshly created directory -- Defender's realtime
scanner holding the file for the instant it took to read it. The runner aborts the whole study
on any exception, which is right for a real error and wrong for a lock that clears in 150 ms.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher import atomic  # noqa: E402


def test_a_transient_lock_is_waited_out_not_raised(tmp_path, monkeypatch):
    target = tmp_path / "progress.json"
    calls = {"n": 0}
    real = Path.replace

    def flaky(self, dst):
        calls["n"] += 1
        if calls["n"] < 3:                      # fails twice, then the scanner lets go
            raise PermissionError(5, "Access is denied")
        return real(self, dst)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(atomic.time, "sleep", lambda _: None)

    atomic.write_text(target, json.dumps({"status": "training"}))
    assert calls["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "training"
    assert not list(tmp_path.glob("*.tmp"))


def test_a_real_permission_fault_still_raises(tmp_path, monkeypatch):
    """The retry must not turn a genuine fault into a silent no-op."""
    monkeypatch.setattr(Path, "replace",
                        lambda self, dst: (_ for _ in ()).throw(PermissionError(5, "denied")))
    slept = []
    monkeypatch.setattr(atomic.time, "sleep", slept.append)

    with pytest.raises(PermissionError):
        atomic.write_text(tmp_path / "x.json", "{}")
    assert len(slept) == atomic.ATTEMPTS - 1          # waited between every attempt, not after
    assert slept == sorted(slept)                     # ... and backed off rather than spinning


def test_the_backoff_is_bounded(tmp_path):
    """A study must not hang for minutes on a lock that is never going to clear."""
    total = sum(atomic.DELAY * 2 ** i for i in range(atomic.ATTEMPTS - 1))
    assert 1.0 < total < 30.0



def test_two_files_with_one_stem_do_not_share_a_temporary(tmp_path):
    """`with_suffix('.tmp')` gave `report.md` and `report.html` the same temporary path, and on
    Windows `STATE.md` and `state.json` the same one again, case folded. Every caller today is
    sequential, so this removes a trap rather than a bug -- but the trap is under the writer
    that exists so no reader ever sees a partial file."""
    from rl_researcher import atomic

    names = ["report.md", "report.html", "STATE.md", "state.json", "run.log.1", "noextension"]
    temps = [atomic.temp_for(tmp_path / n) for n in names]
    folded = [str(t).lower() for t in temps]
    assert len(set(folded)) == len(names), f"two targets share a temporary: {sorted(folded)}"
    for name, tmp in zip(names, temps):
        assert tmp.parent == (tmp_path / name).parent      # same directory, so replace is atomic
