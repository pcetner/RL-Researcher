"""One page you can work from, over the files the rest of the package already writes.

    python -m rl_researcher.serve [--port 7777] [--open]

Everything this package produces ends in a person deciding something, and the surface for
deciding was a markdown file and a line number: open ``README.md``, find the authored decision
region, change one character, save, then type a second command in a terminal. Approving a gated
run was a third command; starting one, a fourth; and what was happening was spread across four
different generated pages. A check that fires into a document nobody opens is not a check, and
the way a document goes unopened is that opening it costs something.

So: a local page with the board on the left and one run on the right, from which each of those
is a button.

The rule that keeps it honest is that **the markdown on disk stays the source of truth**. This
server is a view and an editor over those files, never a database. Turn it off and nothing is
lost and every command still works -- which is what keeps decisions in git, reviewable, and
keeps an exported page readable from a ``file://`` path years from now.

The corollary is that **there is no research logic here**. Every endpoint is a shim over
something that already existed: :func:`artefacts.state.build_state` for the board,
:func:`regions.set_region` for a write, :func:`decide.record` for a decision, :mod:`gate` for an
approval, and a subprocess of ``python -m rl_researcher.run`` for a run. If an endpoint can do
something the command line cannot, that is a bug and not a feature.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import mimetypes
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from rl_researcher import atomic
from rl_researcher.artefacts import page_for
from rl_researcher.artefacts.state import (_decision_region, build_state, options, ticked,
                                           write_state)
from rl_researcher.artefacts.writer import render_page
from rl_researcher.cli import console
from rl_researcher.config import Config, load_config
from rl_researcher.gate import decide as gate_decide
from rl_researcher.gate import refusal_message, write_approval
from rl_researcher.lock import lock_holder
from rl_researcher.workflow_store import WorkflowError
from rl_researcher.workflow import view as workflow_view
from rl_researcher.regions import RegionError, find, set_region
from rl_researcher.render import DOC_CSS, editable_article
from rl_researcher.style import BASE_CSS, EDIT_CSS, THEME_BUTTONS, THEME_SCRIPT
from rl_researcher.units import stamp_now
from rl_researcher.board_view import content, overview, revision

DEFAULT_PORT = 7777

#: Every name a browser may call this machine while talking to us.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

WINDOWS_STOP = (
    "there is no clean stop on Windows: the hot-stop path is a SIGTERM the unit checks between "
    "steps, and Windows delivers no SIGTERM to a console process. Stopping from here kills the "
    "process instead -- the last cadence checkpoint survives and the run resumes from it, but "
    "the step in flight is lost. Send it again with kill=true if that is what you want, or "
    "press Ctrl-C in the window the run is in, which a unit does turn into a clean stop.")


class NoRun(LookupError):
    """A name that does not name a spec in this project."""


def _signal(pid: int, sig: int) -> None:
    """``os.kill``, reached through a name of this module's own.

    Not indirection for its own sake. On POSIX `lock.process_alive` asks whether a pid exists
    with ``os.kill(pid, 0)`` -- the same call -- so a test that watches `os.kill` to see what
    this page signalled also catches every liveness probe the page made on the way, and a test
    that refuses the call outright refuses the probe. The seam separates "what did we ask this
    process to do" from "did we ask whether it exists".
    """
    os.kill(pid, sig)


def _windows() -> bool:
    """Whether stopping a run here can be clean.

    Its own function so a test can ask the Windows question without answering the different
    question `lock.process_alive` asks of the same module -- which is how it decides between
    ``tasklist`` and a signal, and is not something a test about this page should be moving.
    """
    return platform.system() == "Windows"


@dataclass
class Launch:
    """A run this server started, and whatever it said on the way out.

    The runner writes its own ``run.log``, so the log panel reads that. What a run cannot write
    there is the reason it never started -- a refusal at the gate, a locked directory, a check
    that came back an error -- because all three are printed and then the process exits. That is
    what ``said`` is holding.
    """

    proc: Any
    started: str = ""
    said: List[str] = field(default_factory=list)

    @property
    def exit(self) -> Optional[int]:
        return self.proc.poll()


#: Runs launched from this process. A dict passed in rather than a global reached for, so a test
#: can bring its own and two servers cannot see each other's children.
_RUNS: Dict[str, Launch] = {}


# --------------------------------------------------------------------------- who may ask


def _hostname(value: str) -> str:
    v = (value or "").strip()
    if v.startswith("["):                                    # [::1]:7777
        return v[1:v.index("]")].lower() if "]" in v else v.lower()
    return (v.rsplit(":", 1)[0] if ":" in v else v).lower()


def allowed(host: str, origin: str, *, port: int) -> Optional[str]:
    """``None`` when a request may proceed, or the sentence saying why it may not.

    A server on localhost is not private: a page open in another tab can POST to it, and two of
    the things POSTed here are "a person made this decision" and "start a run that costs an hour
    of GPU". Neither is something another site gets to assert on your behalf. Two headers settle
    it -- a browser sets ``Origin`` on a cross-site request and cannot be talked out of it, and
    checking ``Host`` is what stops a name on the internet resolving to 127.0.0.1 and arriving
    here looking local.

    Pure, and outside the handler, because this is the rule most worth testing with no socket in
    the way.
    """
    if _hostname(host) not in LOCAL_HOSTS:
        return f"Host {host!r} is not this machine; the dashboard answers to localhost only"
    if origin:
        try:
            parsed = urlparse(origin)
            same_port = (parsed.port or (443 if parsed.scheme == "https" else 80)) == port
        except ValueError:
            return f"unreadable Origin {origin!r}"
        if _hostname(parsed.netloc) not in LOCAL_HOSTS or not same_port:
            return (f"this request came from {origin}, which is not this dashboard. Deciding, "
                    f"approving and launching are yours to do, not another page's.")
    return None


# --------------------------------------------------------------------------- the endpoints


def _resolve(config: Config, name: str) -> Tuple[Any, Any, Path]:
    """``(kind, spec, out)`` for a run named the way the command line names one."""
    from rl_researcher.workflow import resolve
    return resolve(config, name)


def _rel(config: Config, p: Path) -> str:
    try:
        return Path(p).relative_to(config.root).as_posix()
    except ValueError:
        return Path(p).as_posix()


def _busy(spec: Any, out: Path) -> Optional[Dict[str, Any]]:
    """The refusal to write into a run's directory while a run owns it, or ``None``.

    Not by taking the run lock: that lock means "a process is running here", and holding it for
    a text edit would make the next launch report itself locked. The question here is the
    narrower one -- is something rewriting this file right now -- and a live holder answers it.
    """
    held = lock_holder(out)
    if held and held.get("alive"):
        return {"error": f"{spec.name} is running (pid {held.get('pid')} on {held.get('host')}), "
                         f"and a run rewrites this file when it finishes. Wait for it, or stop "
                         f"it first."}
    return None


def research_hold(config: Config, name: str) -> str:
    """Queue holds are research constraints, independent of compute approval."""
    from rl_researcher.research_queue import hold
    return hold(config, name)


def _run_view(config: Config, name: str, *, light: bool = False) -> Tuple[int, Dict[str, Any]]:
    """One run, whole: its document as something editable, its status, and its gate."""
    kind, spec, out = _resolve(config, name)
    md = out / "README.md"
    text = md.read_text(encoding="utf-8") if md.is_file() else ""
    spec_path = Path(getattr(spec, "source_path", "") or "")
    try:
        authored = [r.arg for r in find(text) if r.kind == "authored"]
        article = editable_article(text, kind=getattr(kind, "artefact_kind", "report"),
                                   run=spec.name, embed_images_from=md.parent) if text and not light else ""
    except RegionError as exc:
        return 200, {"run": spec.name, "kind": spec.kind, "broken": str(exc),
                     "out": _rel(config, out), "authored": [], "article": ""}
    gate = gate_decide(spec, kind, config, out=out)
    body = _decision_region(md)
    dash = out / "dashboard.html"
    return 200, {
        "run": spec.name,
        "report_source": _rel(config, md) if md.is_file() else "",
        "kind": spec.kind,
        "artefact_kind": getattr(kind, "artefact_kind", "report"),
        "spec": _rel(config, spec_path) if spec_path else "",
        "spec_text": spec_path.read_text(encoding="utf-8") if spec_path.is_file() and not light else "",
        "out": _rel(config, out),
        "article": article,
        "authored": authored,
        "options": options(body), "ticked": ticked(body),
        "gated": gate.gated, "approved": gate.approved,
        "hold": research_hold(config, spec.name),
        "wall_limit_minutes": config.gate.ungated_wall_minutes,
        "estimate": gate.cost.describe(),
        "cost": gate.cost.to_dict(),
        "why_gated": list(gate.reasons),
        "refusal": refusal_message(gate, spec) if gate.gated and not gate.approved else "",
        "holder": lock_holder(out),
        "page": _rel(config, page_for(md, kind)) if md.is_file() else "",
        "dashboard": _rel(config, dash) if dash.is_file() else "",
        "results": (out / "results.json").is_file(),
        "decision_body": body,
        **overview(config, spec, kind, out, text),
    }


def _subtitle_of(page: Path) -> str:
    """The line under a page's title, as whichever writer made the page put it there.

    Read back rather than reinvented: the subtitle says which run and which data snapshot the
    numbers came from, and re-rendering a page because somebody edited one paragraph is no
    reason to replace that with today's date.
    """
    if not page.is_file():
        return ""
    m = re.search(r'<p class="sub">(.*?)</p>', page.read_text(encoding="utf-8"), re.S)
    return html_mod.unescape(m.group(1)) if m else ""


def _write_region(config: Config, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Put one authored region's body back, exactly as it was handed over.

    The body arrives as text and is written as text. Nothing here parses the tick grammar, which
    is why a decision stub a kind wrote itself, a reading, and a table all edit the same way, and
    why :func:`artefacts.state.options` stays the only reader of what a box means.
    """
    kind, spec, out = _resolve(config, str(payload.get("run", "")))
    arg, body = str(payload.get("region", "")), payload.get("body")
    if not isinstance(body, str):
        return 400, {"error": "a region write needs a body"}
    md = out / "README.md"
    if not md.is_file():
        return 404, {"error": f"{_rel(config, md)} is not there yet"}
    busy = _busy(spec, out)
    if busy:
        return 409, busy

    text = md.read_text(encoding="utf-8")
    if payload.get("revision") and payload["revision"] != revision(text):
        return 409, {"error": "This report changed. Reload it before saving your edits.",
                     "conflict": True}
    try:
        fresh = set_region(text, "authored", arg, body.replace("\r\n", "\n").strip("\n"))
    except RegionError as exc:
        return 400, {"error": str(exc)}
    if fresh == text:
        return 200, {"ok": True, "changed": False, "region": arg, "message": "no change",
                     "ticked": ticked(_decision_region(md))}
    atomic.write_text(md, fresh)

    # The page beside it, brought back into step -- only the page. Regenerating the document
    # would re-derive every table around the paragraph just typed, which is `report`'s job and
    # has a button of its own.
    page = page_for(md, kind)
    title = next((m.group(1) for m in re.finditer(r"(?m)^#\s+(.+?)\s*$", fresh)), spec.name)
    render_page(md, fresh, kind=getattr(kind, "artefact_kind", "report"), title=title,
                subtitle=_subtitle_of(page), html_path=page)
    return 200, {"ok": True, "changed": True, "region": arg,
                 "revision": revision(fresh),
                 "ticked": ticked(_decision_region(md)),
                 "message": f"{arg} saved to {_rel(config, md)}"}


_EDIT_LOCK = threading.RLock()


def _decide(config: Config, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    from rl_researcher.workflow import submit
    return 200, submit(config, "decide", payload)


def _approve(config: Config, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Write the approval a gated run needs, quoting what the human said.

    The quote is required and must not be blank, which is not a rule invented here: it is what
    ``approve --quote`` has always enforced. It is the only artefact showing that a person
    weighed the cost, and it is bound to the spec's fingerprint, so editing the spec afterwards
    voids it with no further rule.
    """
    kind, spec, out = _resolve(config, str(payload.get("run", "")))
    d = gate_decide(spec, kind, config, out=out)
    if not d.gated:
        return 200, {"ok": True, "gated": False,
                     "message": f"{spec.name} is under the gate line ({d.cost.describe()}); "
                                f"no approval needed, nothing written"}
    quote = str(payload.get("quote") or "")
    if not quote.strip():
        return 400, {"error": "an approval needs the quote of what the human said"}
    path = write_approval(config, spec, d.cost, quote=quote,
                          session=str(payload.get("session") or ""),
                          approved_by=str(payload.get("by") or "the human, at the dashboard"),
                          note=str(payload.get("note") or ""))
    return 200, {"ok": True, "gated": True, "approval": _rel(config, path),
                 "message": f"approval written: {_rel(config, path)}\n"
                            f"  estimate at approval: {d.cost.describe()}"}


def _cli(config: Config, module: str, args: List[str], *, timeout: float = 900.0
         ) -> Tuple[int, str]:
    """Run one of this package's own commands and hand back what it said."""
    done = subprocess.run([sys.executable, "-m", f"rl_researcher.{module}", *args],
                          cwd=str(config.root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _report(config: Config, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Regenerate the document, through the command rather than around it.

    ``report`` does more than write the page: it writes the registered ledger rows and records
    the canary. A second caller doing nine tenths of that would be a second answer to the
    question of what regenerating means.
    """
    kind, spec, out = _resolve(config, str(payload.get("run", "")))
    busy = _busy(spec, out)
    if busy:
        return 409, busy
    code, said = _cli(config, "report", [spec.name])
    return (200 if code == 0 else 409), {"ok": code == 0, "message": said.strip(), "exit": code}


def _launch(config: Config, payload: Dict[str, Any], runs: Dict[str, Launch]) -> Tuple[int, Dict[str, Any]]:
    from rl_researcher.workflow_store import exclusive, pending, read, launch_path, clear_launch
    from rl_researcher import atomic
    with exclusive(config):
        kind, spec, out = _resolve(config, str(payload.get("run", "")))
        if pending(config, spec.name):
            return 409, {"error": "This run is already starting."}
        cap = workflow_view(config, spec, kind, out)["capabilities"]["run"]
        if not cap["enabled"]:
            return 409, {"error": cap["reason"], **cap}
        reservations = read(launch_path(config), {})
        reservations[spec.name] = {"pid": None, "parent_pid": os.getpid(), "date": stamp_now()}
        atomic.write_json(launch_path(config), reservations)
        try:
            code, data = _launch_unlocked(config, payload, runs)
            if code == 200:
                reservations[spec.name]["pid"] = data["pid"]
                atomic.write_json(launch_path(config), reservations)
            else:
                clear_launch(config, spec.name)
            return code, data
        except Exception:
            clear_launch(config, spec.name)
            raise


def _launch_unlocked(config: Config, payload: Dict[str, Any], runs: Dict[str, Launch]
            ) -> Tuple[int, Dict[str, Any]]:
    """Start a run as its own process, gate and all.

    A subprocess because ``runner.run`` blocks, installs signal handlers and can take an hour,
    and because running it in-process would put the gate on the same side of the fence as the
    thing it gates. It is the command a person would type, with no flag this page invented:
    there is no ``--no-gate`` here, and a refusal comes back looking exactly like a refusal in a
    terminal.
    """
    kind, spec, out = _resolve(config, str(payload.get("run", "")))
    live = runs.get(spec.name)
    if live is not None and live.exit is None:
        return 409, {"error": f"{spec.name} was already started from here (pid {live.proc.pid})"}
    held = lock_holder(out)
    if held and held.get("alive"):
        return 409, {"error": f"{spec.name} is already running (pid {held.get('pid')} on "
                              f"{held.get('host')})"}
    spec_path = Path(getattr(spec, "source_path", "") or "")
    arg = str(spec_path) if spec_path.is_file() else spec.name
    proc = subprocess.Popen([sys.executable, "-m", "rl_researcher.run", arg],
                            cwd=str(config.root), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    launch = Launch(proc=proc, started=stamp_now())
    runs[spec.name] = launch
    threading.Thread(target=_drain, args=(launch, config, spec.name), daemon=True).start()
    return 200, {"ok": True, "pid": proc.pid,
                 "message": f"{spec.name}: started (pid {proc.pid}). The gate is applied inside "
                            f"that process, exactly as it is from a terminal."}


def _drain(launch: Launch, config: Optional[Config] = None, name: str = "") -> None:
    """Keep the tail of what a launched run printed, without letting it fill memory."""
    try:
        for line in launch.proc.stdout:                      # type: ignore[union-attr]
            launch.said.append(line.rstrip("\n"))
            del launch.said[:-400]
    except (OSError, ValueError):                            # pragma: no cover - closed early
        pass
    finally:
        launch.proc.wait()
        if config is not None:
            from rl_researcher.workflow_store import exclusive, read, launch_path, clear_launch
            with exclusive(config):
                reservation = read(launch_path(config), {}).get(name, {})
                if reservation.get("pid") == launch.proc.pid:
                    clear_launch(config, name)


def _stop(config: Config, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Ask the process holding this directory to stop -- or, on Windows, say what that costs."""
    kind, spec, out = _resolve(config, str(payload.get("run", "")))
    held = lock_holder(out)
    if held is None:
        return 409, {"error": f"nothing holds {_rel(config, out)}"}
    if held.get("host") != platform.node():
        return 409, {"error": f"{spec.name} is running on {held.get('host')}, not here; this "
                              f"page can only signal a process on this machine"}
    if not held.get("alive"):
        return 409, {"error": f"the lock on {_rel(config, out)} is stale (pid {held.get('pid')} "
                              f"is gone); the next launch takes it over"}
    if _windows() and not payload.get("kill"):
        return 409, {"error": WINDOWS_STOP, "needs": "kill"}
    pid = int(held.get("pid", -1))
    try:
        _signal(pid, signal.SIGTERM)
    except (OSError, ValueError) as exc:
        return 409, {"error": f"could not signal pid {pid}: {exc}"}
    clean = not _windows()
    return 200, {"ok": True, "message": (
        f"{spec.name}: asked pid {pid} to checkpoint and stop; re-run it to continue" if clean
        else f"{spec.name}: killed pid {pid}. The last checkpoint survives; re-run it to "
             f"continue from there.")}


def _log(config: Config, body: Dict[str, Any], runs: Dict[str, Launch]
         ) -> Tuple[int, Dict[str, Any]]:
    """The run log from a byte offset, so the panel appends rather than reloads.

    Byte offsets over a binary read: the log is written a line at a time and flushed, and text
    mode's ``tell`` is an opaque cookie rather than a position -- which is the difference between
    appending the new lines and appending the whole file again.
    """
    kind, spec, out = _resolve(config, str(body.get("run", "")))
    from rl_researcher.artefacts.dashboard import tail, vocab_of

    path = out / str(getattr(kind, "log_name", "run.log"))
    marks = vocab_of(kind).marks
    events = [line for line in tail(path) if any(token in line for token in marks)][-5:]
    offset = max(0, int(body.get("offset") or 0))
    restarted = False
    text, size = "", offset
    if path.is_file():
        if body.get("tail") and offset == 0:
            offset = max(0, path.stat().st_size - 65536)
        if offset > path.stat().st_size:      # the run started over and the log was replaced
            offset, restarted, size = 0, True, 0
        with path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read(65536)
        text, size = chunk.decode("utf-8", "replace"), offset + len(chunk)
    launch = runs.get(spec.name)
    over = None if launch is None else launch.exit
    held = lock_holder(out)
    # Two different questions, and the panel needs both: `running` is "a run this server started
    # is still alive", `live` is "something owns this directory" -- which is also true of a run
    # started in a terminal, and that is a run worth following too.
    return 200, {"text": text, "offset": size, "restarted": restarted, "events": events,
                 "running": launch is not None and over is None,
                 "live": bool(held and held.get("alive")), "exit": over,
                 "said": list(launch.said) if launch is not None and over is not None else []}


def api(config: Config, method: str, path: str, payload: Optional[Dict[str, Any]] = None, *,
        runs: Optional[Dict[str, Launch]] = None) -> Tuple[int, Dict[str, Any]]:
    """One request, as data in and data out, with no socket anywhere near it.

    Split from the handler so the whole surface is exercised the way this package's tests
    exercise a command: call it, read what came back, then look at the files.
    """
    runs = _RUNS if runs is None else runs
    parsed = urlparse(path)
    body = dict(payload or {})
    for key, values in parse_qs(parsed.query).items():
        body.setdefault(key, values[0])
    parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2 or parts[0] != "api":
        return 404, {"error": f"no endpoint {parsed.path}"}
    verb = parts[1]
    if len(parts) > 2 and verb != "queue":
        body.setdefault("run", "/".join(parts[2:]))

    try:
        from rl_researcher.workflow_store import exclusive
        if method == "GET":
            if verb == "document":
                from rl_researcher.presentation import document
                return document(config.root, str(body.get("path", "")))
            if verb == "state":
                return 200, build_state(config, history_limit=None).to_json()
            if verb == "run":
                name = str(body.get("run", ""))
                code, data = _run_view(config, name, light=body.get("light") == "1")
                launch = runs.get(name)
                if launch is not None:
                    data["starting"] = launch.exit is None and not bool(data.get("holder"))
                    if launch.exit:
                        data.setdefault("alerts", []).append({"unit": "Launch failed",
                            "message": "\n".join(list(launch.said)[-4:]) or f"Exit {launch.exit}"})
                return code, data
            if verb == "content":
                kind, spec, out = _resolve(config, str(body.get("run", "")))
                view = str(body.get("view", "results"))
                if view not in ("results", "units", "progress"):
                    return 400, {"error": "Unknown view."}
                return 200, content(spec, kind, out, view,
                                    stale_factor=float(config.watcher.stale_factor))
            if verb == "log":
                return _log(config, body, runs)
        elif method == "POST":
            if verb == "queue" and len(parts) == 3:
                from rl_researcher.research_queue import mutate, snapshot
                result = mutate(config, parts[2], body)
                return 200, {**result, "revision": snapshot(config)["revision"]}
            if verb == "review" or (verb == "decide" and "operation_id" in body):
                from rl_researcher.workflow import submit
                return 200, submit(config, verb, body)
            if verb == "state":
                return 200, {"ok": True, "state": _rel(config, write_state(config))}
            if verb == "region":
                with exclusive(config), _EDIT_LOCK:
                    from rl_researcher.workflow import active
                    kind, spec, out = _resolve(config, str(body.get("run", "")))
                    if active(config, spec, out):
                        return 409, {"error": "This run is running or starting; wait before editing."}
                    return _write_region(config, body)
            if verb == "decide":
                return _decide(config, body)
            if verb == "approve":
                return _approve(config, body)
            if verb == "report":
                return _report(config, body)
            if verb == "run":
                hold = research_hold(config, str(body.get("run", "")))
                if hold:
                    return 409, {"error": "Research hold: " + hold}
                return _launch(config, body, runs)
            if verb == "stop":
                return _stop(config, body)
    except WorkflowError as exc:
        return exc.status, exc.data
    except NoRun as exc:
        return 404, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - one broken spec must not take the page down
        return 500, {"error": f"{type(exc).__name__}: {exc}"}
    return 404, {"error": f"no {method} {parsed.path}"}


# --------------------------------------------------------------------------- the page

#: The board's own layout. Everything else the page wears -- the colour tokens, the type, the
#: chips, the theme switch, the document rules and the authored-region rules -- comes from
#: `style` and `render`, so this page and every exported page are the same page in two moods.
BOARD_CSS = (Path(__file__).parent / "ui" / "board.css").read_text(encoding="utf-8")
BOARD_SCRIPT = "\n".join((Path(__file__).parent / "ui" / name).read_text(encoding="utf-8")
                         for name in ("workflow.js", "board.js"))


def page(config: Config) -> str:
    """The whole dashboard, in one document with nothing fetched from anywhere.

    Not because it has to open from a ``file://`` path -- it does not, it is served -- but
    because every other page this package writes obeys that rule, and a dashboard that quietly
    needed a CDN would be the one page that stopped working on the aeroplane.
    """
    name = html_mod.escape(config.name)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{name}</title>\n"
        f"<style>{BASE_CSS}{DOC_CSS}{EDIT_CSS}{BOARD_CSS}</style>\n"
        "</head><body>\n"
        # The project's own bar sits above the board and outside the pane: the pane is
        # replaced whole each time a run is opened, and the theme switch must survive that.
        f'<div class="bar"><a class="brand" id="brand-home" href="#home">{name}</a>'
        '<span class="stamp" id="connection" role="status">Connecting…</span><span class="spacer"></span>'
        '<button class="act" id="reload">Refresh</button>'
        f"{THEME_BUTTONS}</div>\n"
        '<div class="board">\n'
        '<aside class="rail" id="rail" aria-label="Runs"></aside>\n'
        '<main class="pane" id="pane">\n'
        '<div class="empty"><p>Loading project home…</p></div>\n'
        "</main>\n</div>\n"
        f"<script>{THEME_SCRIPT}\n{BOARD_SCRIPT}</script>\n"
        "</body></html>\n"
    )

# --------------------------------------------------------------------------- the socket


class Board(ThreadingHTTPServer):
    """The server, holding the project it serves and the runs it has started."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], handler: Any, config: Config) -> None:
        super().__init__(addr, handler)
        self.config = config
        self.runs: Dict[str, Launch] = {}


class Handler(BaseHTTPRequestHandler):
    """JSON under ``/api``, the page at ``/``, and the project's own files under everything else.

    Exported reports and standalone dashboards stay available as files. The workspace
    requests shared blocks without embedding another page shell.
    """

    server_version = "rl-researcher"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ plumbing

    @property
    def board(self) -> Board:
        return self.server                                   # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        """Quiet. A request log per poll would bury the one line that matters."""

    def _guard(self) -> bool:
        why = allowed(self.headers.get("Host", ""), self.headers.get("Origin", ""),
                      port=self.board.server_address[1])
        if why is None:
            return True
        self._send(403, json.dumps({"error": why}).encode("utf-8"), "application/json")
        return False

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Nothing here is for anyone else to read, cache or embed.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Dict[str, Any]) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json")

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:                                # noqa: N802 - the base class name
        if not self._guard():
            return
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, page(self.board.config).encode("utf-8"), "text/html; charset=utf-8")
            return
        if route.startswith("/api/"):
            code, obj = api(self.board.config, "GET", self.path, runs=self.board.runs)
            self._json(code, obj)
            return
        self._file(route)

    def do_POST(self) -> None:                               # noqa: N802 - the base class name
        if not self._guard():
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": f"the request body is not JSON: {exc}"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "the request body must be an object"})
            return
        code, obj = api(self.board.config, "POST", self.path, payload, runs=self.board.runs)
        self._json(code, obj)

    def _file(self, route: str) -> None:
        """A file from inside the project, and only from inside it."""
        root = self.board.config.root.resolve()
        target = (root / unquote(route).lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._json(404, {"error": f"no {route} in {root.name}"})
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/json":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def main(argv: Optional[List[str]] = None) -> int:
    console()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--open", action="store_true", help="open the page in your browser")
    a = p.parse_args(argv)
    config = load_config()

    # 127.0.0.1 and not a configurable interface. A dashboard that can decide, approve and launch
    # is not a thing to expose by a flag someone sets once and forgets.
    server = Board(("127.0.0.1", a.port), Handler, config)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    view = build_state(config)
    print(f"{config.name} -> {url}")
    print(f"  {len(view.waiting)} awaiting a decision, {len(view.running)} running, "
          f"{len(view.queued)} queued")
    for w in view.waiting:
        print(f"  awaiting: {w.run} — {w.outcome}")
    print("  ctrl-c to stop; the files on disk are the truth and nothing here caches them")
    if a.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
