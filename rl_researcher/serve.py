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
from rl_researcher.config import Config, kind_for, load_config, out_dir_for, resolve_spec
from rl_researcher.decide import record
from rl_researcher.gate import decide as gate_decide
from rl_researcher.gate import refusal_message, write_approval
from rl_researcher.lock import lock_holder
from rl_researcher.regions import RegionError, find, set_region
from rl_researcher.render import DOC_CSS, editable_article
from rl_researcher.status import run_status
from rl_researcher.style import BASE_CSS, EDIT_CSS, THEME_BUTTONS, THEME_SCRIPT
from rl_researcher.units import stamp_now

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
    if not name:
        raise NoRun("no run was named")
    try:
        spec_path = resolve_spec(name, config)
        kind = kind_for(spec_path, config)
        spec = kind.load(spec_path)
    except (OSError, LookupError, ValueError) as exc:
        raise NoRun(f"no run {name!r} in this project ({exc})") from exc
    return kind, spec, out_dir_for(spec, config)


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


def _run_view(config: Config, name: str) -> Tuple[int, Dict[str, Any]]:
    """One run, whole: its document as something editable, its status, and its gate."""
    kind, spec, out = _resolve(config, name)
    md = out / "README.md"
    text = md.read_text(encoding="utf-8") if md.is_file() else ""
    spec_path = Path(getattr(spec, "source_path", "") or "")
    try:
        authored = [r.arg for r in find(text) if r.kind == "authored"]
        article = editable_article(text, kind=getattr(kind, "artefact_kind", "report"),
                                   run=spec.name, embed_images_from=md.parent) if text else ""
    except RegionError as exc:
        return 200, {"run": spec.name, "kind": spec.kind, "broken": str(exc),
                     "out": _rel(config, out), "authored": [], "article": ""}
    st = run_status(spec, kind, out, stale_factor=float(config.watcher.stale_factor))
    gate = gate_decide(spec, kind, config, out=out)
    body = _decision_region(md)
    dash = out / "dashboard.html"
    return 200, {
        "run": spec.name,
        "kind": spec.kind,
        "artefact_kind": getattr(kind, "artefact_kind", "report"),
        "spec": _rel(config, spec_path) if spec_path else "",
        "spec_text": spec_path.read_text(encoding="utf-8") if spec_path.is_file() else "",
        "out": _rel(config, out),
        "article": article,
        "authored": authored,
        "state": st.state, "done": st.done, "total": len(st.units),
        "options": options(body), "ticked": ticked(body),
        "gated": gate.gated, "approved": gate.approved,
        "estimate": gate.cost.describe(),
        "why_gated": list(gate.reasons),
        "refusal": refusal_message(gate, spec) if gate.gated and not gate.approved else "",
        "holder": lock_holder(out),
        "page": _rel(config, page_for(md, kind)) if md.is_file() else "",
        "dashboard": _rel(config, dash) if dash.is_file() else "",
        "results": (out / "results.json").is_file(),
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
                 "ticked": ticked(_decision_region(md)),
                 "message": f"{arg} saved to {_rel(config, md)}"}


def _decide(config: Config, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    kind, spec, out = _resolve(config, str(payload.get("run", "")))
    busy = _busy(spec, out)
    if busy:
        return 409, busy
    d = record(config, spec, out, note=str(payload.get("note") or ""), via="dashboard")
    return (200 if d.code == 0 else 409), {
        "ok": d.code == 0, "message": d.message, "chose": d.chose,
        "options": d.options, "finding": d.finding}


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


def _launch(config: Config, payload: Dict[str, Any], runs: Dict[str, Launch]
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
    threading.Thread(target=_drain, args=(launch,), daemon=True).start()
    return 200, {"ok": True, "pid": proc.pid,
                 "message": f"{spec.name}: started (pid {proc.pid}). The gate is applied inside "
                            f"that process, exactly as it is from a terminal."}


def _drain(launch: Launch) -> None:
    """Keep the tail of what a launched run printed, without letting it fill memory."""
    try:
        for line in launch.proc.stdout:                      # type: ignore[union-attr]
            launch.said.append(line.rstrip("\n"))
            del launch.said[:-400]
    except (OSError, ValueError):                            # pragma: no cover - closed early
        pass
    finally:
        launch.proc.wait()


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
        os.kill(pid, signal.SIGTERM)
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
    path = out / str(getattr(kind, "log_name", "run.log"))
    offset = max(0, int(body.get("offset") or 0))
    restarted = False
    text, size = "", offset
    if path.is_file():
        if offset > path.stat().st_size:      # the run started over and the log was replaced
            offset, restarted, size = 0, True, 0
        with path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
        text, size = chunk.decode("utf-8", "replace"), offset + len(chunk)
    launch = runs.get(spec.name)
    over = None if launch is None else launch.exit
    held = lock_holder(out)
    # Two different questions, and the panel needs both: `running` is "a run this server started
    # is still alive", `live` is "something owns this directory" -- which is also true of a run
    # started in a terminal, and that is a run worth following too.
    return 200, {"text": text, "offset": size, "restarted": restarted,
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
    if len(parts) > 2:
        body.setdefault("run", "/".join(parts[2:]))

    try:
        if method == "GET":
            if verb == "state":
                return 200, build_state(config).to_json()
            if verb == "run":
                return _run_view(config, str(body.get("run", "")))
            if verb == "log":
                return _log(config, body, runs)
        elif method == "POST":
            if verb == "state":
                return 200, {"ok": True, "state": _rel(config, write_state(config))}
            if verb == "region":
                return _write_region(config, body)
            if verb == "decide":
                return _decide(config, body)
            if verb == "approve":
                return _approve(config, body)
            if verb == "report":
                return _report(config, body)
            if verb == "run":
                return _launch(config, body, runs)
            if verb == "stop":
                return _stop(config, body)
    except NoRun as exc:
        return 404, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - one broken spec must not take the page down
        return 500, {"error": f"{type(exc).__name__}: {exc}"}
    return 404, {"error": f"no {method} {parsed.path}"}


# --------------------------------------------------------------------------- the page

#: The board's own layout. Everything else the page wears -- the colour tokens, the type, the
#: chips, the theme switch, the document rules and the authored-region rules -- comes from
#: `style` and `render`, so this page and every exported page are the same page in two moods.
BOARD_CSS = """
  html { height:100% }
  body { height:100vh; overflow:hidden; display:flex; flex-direction:column }
  .board { flex:1; display:flex; min-height:0 }
  .rail { width:22rem; min-width:16rem; flex:0 0 auto; border-right:1px solid var(--line);
    overflow-y:auto; background:var(--panel, var(--ground)) }
  .pane { flex:1; overflow-y:auto; position:relative }
  .bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:10px 16px;
    border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5;
    background:var(--ground) }
  .bar .spacer { flex:1 }
  /* The buttons stay together when the bar wraps: a "stop" that has drifted onto its own line
     away from "run" is a button whose meaning you have to work out from its label alone. */
  .bar .acts { display:inline-flex; gap:8px; flex-wrap:nowrap }
  .brand { font-weight:700; letter-spacing:-0.01em }
  .stamp { font-size:11.5px; color:var(--muted) }
  button.act { font:inherit; font-size:12.5px; padding:5px 11px; border-radius:7px;
    border:1px solid var(--line); background:var(--code); color:var(--ink); cursor:pointer }
  button.act:hover { border-color:var(--accent); color:var(--accent) }
  button.act[disabled] { opacity:.45; cursor:default; border-color:var(--line);
    color:var(--muted) }
  button.act.go { border-color:var(--accent); color:var(--accent); font-weight:600 }
  button.act.warn { border-color:var(--crit); color:var(--crit) }
  .group { border-bottom:1px solid var(--line) }
  .group > h2 { position:sticky; top:0; background:var(--ground); z-index:2 }
  .item { display:block; width:100%; text-align:left; font:inherit; cursor:pointer;
    background:none; border:0; border-bottom:1px solid var(--line); padding:9px 14px;
    color:var(--ink) }
  .item:hover { background:var(--code) }
  .item[aria-current="true"] { background:var(--code); box-shadow:inset 3px 0 0 var(--accent) }
  .item .name { font-weight:600; font-size:13px }
  /* Three lines of why, then an ellipsis. The rail is a list of things waiting on you and has
     to stay scannable; the whole of it is on the right the moment you click. */
  .item .why { color:var(--muted); font-size:11.5px; margin-top:2px; line-height:1.45;
    display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden }
  .item .why b { color:var(--ink); font-weight:600 }
  .kv { padding:8px 14px; font-size:11.5px; color:var(--muted); line-height:1.7 }
  .kv b { color:var(--ink); font-weight:600 }
  .panel { display:none; padding:12px 16px; border-bottom:1px solid var(--line);
    background:var(--code) }
  .panel.open { display:block }
  .panel label { display:block; font-size:11.5px; color:var(--muted); margin-bottom:5px;
    white-space:pre-wrap }
  .panel textarea, .panel input[type="text"] { width:100%; box-sizing:border-box; font:inherit;
    font-size:13px; color:var(--ink); background:var(--ground); border:1px solid var(--line);
    border-radius:7px; padding:8px 10px; resize:vertical }
  .panel textarea { min-height:4.5rem }
  .panel .row { display:flex; gap:8px; align-items:center; margin-top:9px; flex-wrap:wrap }
  .said { font-size:12px; white-space:pre-wrap; margin:0; padding:10px 16px;
    border-bottom:1px solid var(--line); color:var(--muted) }
  .said.bad { color:var(--crit) }
  .said.good { color:var(--ok) }
  .said:empty { display:none }
  pre.log:empty { display:none }
  pre.log { margin:0; padding:12px 16px; max-height:22rem; overflow:auto; font-size:12px;
    line-height:1.5; white-space:pre-wrap; background:var(--code);
    border-bottom:1px solid var(--line) }
  iframe.live { display:block; width:100%; height:38rem; border:0;
    border-bottom:1px solid var(--line); background:var(--ground) }
  .empty { padding:3rem 1.5rem; color:var(--muted); max-width:34rem }
  .doc { max-width:58rem }
"""

#: One program, at the end of the body. It does four things: keeps the rail in step with
#: `state.json`, swaps the right pane between runs, keeps a ticked checkbox and the region source
#: it belongs to saying the same thing, and posts. It deliberately never decides anything: every
#: refusal a button can hit is worded on the server, once, and printed here as it arrives.
BOARD_SCRIPT = r"""
(function () {
  var pane = document.getElementById('pane'), rail = document.getElementById('rail');
  var stamp = document.getElementById('stamp');
  var current = null, logAt = 0, logTimer = null;
  // What the server last said about this run. Held here rather than only in the element, because
  // the pane is repainted whole and a message wiped by the repaint that followed it is a message
  // nobody read -- which was true of every refusal the buttons could produce.
  var said = {text: '', tone: ''};

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function say(what, tone) {
    said = {text: what || '', tone: tone || ''};
    showSaid();
  }
  function showSaid() {
    var box = document.getElementById('said');
    if (!box) return;
    box.textContent = said.text;
    box.className = 'said' + (said.tone ? ' ' + said.tone : '');
  }
  function ask(method, url, body) {
    return fetch(url, {
      method: method, credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : null
    }).then(function (r) {
      return r.json().then(function (j) { return {ok: r.ok, data: j}; });
    });
  }
  function dirty() { return !!pane.querySelector('.authored.dirty, .authored.editing'); }

  // ---------------------------------------------------------------- the rail

  function line(name, why, why2) {
    var b = el('button', 'item');
    b.type = 'button';
    b.dataset.run = name;
    b.appendChild(el('div', 'name', name));
    if (why) { var d = el('div', 'why'); d.innerHTML = why; b.appendChild(d); }
    if (why2) b.appendChild(el('div', 'why', why2));
    b.addEventListener('click', function () { open(name); });
    return b;
  }
  function group(title, nodes) {
    var g = el('div', 'group');
    g.appendChild(el('h2', null, title));
    if (!nodes.length) { g.appendChild(el('div', 'kv', 'nothing')); return g; }
    nodes.forEach(function (n) { g.appendChild(n); });
    return g;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c];
    });
  }
  // An option is a line of markdown -- `**go** — build on it` -- because the file it lives in is
  // markdown. The rail is not markdown, so the emphasis marks come off for display only: what
  // gets written back, and what the ledger records, is the line exactly as it is on disk.
  function plain(s) { return String(s == null ? '' : s).replace(/\*\*/g, ''); }
  function paintRail(s) {
    var keep = document.activeElement, at = rail.scrollTop;
    rail.textContent = '';
    rail.appendChild(group('Awaiting you', (s.waiting || []).map(function (w) {
      return line(w.run, '<b>' + esc(w.outcome || 'finished') + '</b>',
                  plain((w.options || []).join(' · ')));
    })));
    rail.appendChild(group('Running', (s.running || []).map(function (r) {
      var trouble = (r.failed || []).length ? 'FAILED: ' + r.failed.join(', ')
                  : (r.stale || []).length ? 'stale: ' + r.stale.join(', ') : '';
      return line(r.run, esc(r.done + '/' + r.total + ' units · beat ' + r.heartbeat_age +
                             ' ago · eta ' + r.eta), trouble);
    })));
    rail.appendChild(group('Queued', (s.queued || []).map(function (q) {
      return line(q.run || '?', esc((q.estimate || '') + (q.hold ? ' · on hold' : '')));
    })));
    // A registered spec nobody has run yet. It is not news, so the state page does not print it
    // -- but leaving it off the board would mean the one thing you cannot do from here is start
    // a study, and the point of the board is that there is nowhere else you have to go.
    rail.appendChild(group('Registered, not started', (s.ready || []).map(function (q) {
      return line(q.run, esc(q.kind + ' · ' + q.units + ' unit(s)'));
    })));
    rail.appendChild(group('Recently decided', (s.decided || []).map(function (d) {
      return line(d.run, esc(plain((d.chose || []).join(', ')) || 'decided') +
                         (d.id ? ' <b>[' + esc(d.id) + ']</b>' : ''));
    })));
    var h = s.health || {}, kv = el('div', 'kv');
    kv.innerHTML = '<b>' + esc(h.findings) + '</b> findings · <b>' +
      esc((h.throughput_devices || []).join(', ') || 'no devices') + '</b><br>watcher ' +
      esc(h.watcher_last_tick || 'never') + '<br>canary ' +
      esc((h.canary || {}).commit || 'never run') + '<br>approvals ' +
      esc((h.approvals || []).join(', ') || 'none');
    var g = el('div', 'group');
    g.appendChild(el('h2', null, 'Health'));
    g.appendChild(kv);
    (h.unreadable_specs || []).forEach(function (bad) {
      var n = el('div', 'kv');
      n.appendChild(el('span', 'chip t-crit', 'spec'));
      n.appendChild(document.createTextNode(' ' + bad));
      g.appendChild(n);
    });
    rail.appendChild(g);
    if (stamp) stamp.textContent = 'generated ' + (s.generated || '');
    mark();
    rail.scrollTop = at;
    if (keep && keep.dataset && keep.dataset.run) {
      var again = rail.querySelector('[data-run="' + keep.dataset.run + '"]');
      if (again) again.focus();
    }
  }
  function mark() {
    Array.prototype.forEach.call(rail.querySelectorAll('.item'), function (b) {
      b.setAttribute('aria-current', b.dataset.run === current ? 'true' : 'false');
    });
  }
  function board() {
    return ask('GET', '/api/state').then(function (r) { paintRail(r.data); });
  }

  // ---------------------------------------------------------------- one run

  function open(name) {
    if (name !== current && dirty() &&
        !window.confirm('There are unsaved edits on this run. Leave them?')) return;
    if (name !== current) said = {text: '', tone: ''};
    current = name;
    mark();
    return refresh();
  }
  function refresh() {
    if (!current) return Promise.resolve();
    return ask('GET', '/api/run/' + encodeURIComponent(current)).then(function (r) {
      paintRun(r.data);
    });
  }
  function chip(text, tone) { return '<span class="chip t-' + tone + '">' + esc(text) + '</span>'; }

  function paintRun(d) {
    logAt = 0;
    stopFollowing();
    if (d.error || d.broken) {
      pane.innerHTML = '<div class="empty"><h1>' + esc(d.run || current) + '</h1><p>' +
        esc(d.error || d.broken) + '</p></div>';
      return;
    }
    var running = d.state === 'running' || (d.holder && d.holder.alive);
    var tone = d.state === 'finished' ? 'ok' : running ? 'accent' : 'muted';
    var bits = [chip(d.state, tone), chip(d.done + '/' + d.total + ' units', 'muted')];
    if (d.gated) bits.push(chip(d.approved ? 'approved' : 'needs approval',
                                d.approved ? 'ok' : 'crit'));
    // The estimate is what the gate is about, so it belongs in the bar while there is still
    // something to spend. On a finished run it reads "nothing to run", which is noise beside a
    // result, and the point of this bar is that the one thing waiting on you is easy to see.
    if (d.gated || !d.results) bits.push('<span class="stamp">' + esc(d.estimate) + '</span>');

    var links = [];
    if (d.page) links.push('<a href="/' + d.page + '" target="_blank">page</a>');
    if (d.spec) links.push('<a href="/' + d.spec + '" target="_blank">spec</a>');
    if (d.dashboard) links.push('<a href="/' + d.dashboard + '" target="_blank">live</a>');

    pane.innerHTML =
      '<div class="bar"><span class="brand">' + esc(d.run) + '</span>' + bits.join(' ') +
        '<span class="stamp">' + links.join(' · ') + '</span>' +
        '<span class="spacer"></span>' +
        '<span class="acts">' +
          '<button class="act" data-open="decide">decide</button>' +
          (d.gated && !d.approved ? '<button class="act go" data-open="approve">approve</button>'
                                  : '') +
          (running ? '<button class="act warn" data-do="stop">stop</button>'
                   : '<button class="act" data-do="run">run</button>') +
          '<button class="act" data-do="report">regenerate</button>' +
        '</span>' +
      '</div>' +
      '<p class="said" id="said"></p>' +
      '<div class="panel" id="p-decide">' +
        '<label>Why. This is the note the ledger keeps, and the only thing that explains the ' +
        'tick when it is read back in six weeks.</label>' +
        '<textarea id="note" placeholder="what the numbers made you do"></textarea>' +
        '<div class="row"><button class="act go" data-do="decide">record the decision</button>' +
        '<span class="stamp" id="ticked"></span></div>' +
      '</div>' +
      (d.gated && !d.approved ?
      '<div class="panel" id="p-approve">' +
        '<label>' + esc(d.refusal) + '</label>' +
        '<label>The sentence in which you approved it, in your words.</label>' +
        '<textarea id="quote" placeholder="yes, run it — worth an hour to settle the ' +
        'comparison"></textarea>' +
        '<div class="row"><button class="act go" data-do="approve">write the approval</button>' +
        '</div>' +
      '</div>' : '') +
      '<pre class="log" id="log"></pre>' +
      (running && d.dashboard ? '<iframe class="live" src="/' + d.dashboard + '"></iframe>' : '') +
      (d.article || '<div class="empty"><p>No document yet. This run has not been reported.</p>' +
                    '</div>');

    setTicked(d.ticked);
    showSaid();
    wire(d);
    // The log is read once whether or not anything is live -- the tail of the last run is the
    // first thing you want when a run is not running and you are asking why -- and then followed
    // only while something is actually writing to it.
    pollLog();
    if (running) follow();
  }
  function follow() {
    if (!logTimer) logTimer = setInterval(pollLog, 1500);
  }
  function setTicked(list) {
    var box = document.getElementById('ticked');
    if (box) box.textContent = (list && list.length)
      ? 'ticked: ' + plain(list.join(', ')) : 'nothing is ticked yet';
  }

  // ---------------------------------------------------------------- the regions

  // A box and the source it came from must never disagree, so the box edits the source and the
  // source is the only thing sent. The nth box is the nth `- [ ]` line, which is the order the
  // server reads them in too.
  var BOX = /^(\s*[-*] \[)([ xX])(\].*)$/;
  function setBox(area, index, on) {
    var lines = area.value.split('\n'), n = 0;
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(BOX);
      if (!m) continue;
      if (n === index) { lines[i] = m[1] + (on ? 'x' : ' ') + m[3]; break; }
      n++;
    }
    area.value = lines.join('\n');
  }
  function wire(d) {
    Array.prototype.forEach.call(pane.querySelectorAll('.authored'), function (sec) {
      var area = sec.querySelector('.authored-src');
      var note = sec.querySelector('.authored-said');
      var was = area.value;
      function touched() { sec.classList.add('dirty'); note.textContent = 'unsaved'; }
      Array.prototype.forEach.call(sec.querySelectorAll('input.tick'), function (box) {
        box.addEventListener('change', function () {
          setBox(area, parseInt(box.dataset.option, 10), box.checked);
          touched();
        });
      });
      area.addEventListener('input', touched);
      sec.querySelector('.authored-edit').addEventListener('click', function () {
        sec.classList.toggle('editing');
        if (sec.classList.contains('editing')) area.focus();
      });
      sec.querySelector('.authored-revert').addEventListener('click', function () {
        area.value = was;
        sec.classList.remove('dirty', 'editing');
        note.textContent = '';
        refresh();
      });
      sec.querySelector('.authored-save').addEventListener('click', function () {
        note.textContent = 'saving…';
        ask('POST', '/api/region', {run: d.run, region: sec.dataset.region, body: area.value})
          .then(function (r) {
            if (!r.ok) { note.textContent = r.data.error; note.classList.add('bad'); return; }
            was = area.value;
            sec.classList.remove('dirty', 'editing');
            note.classList.remove('bad');
            note.textContent = '';
            setTicked(r.data.ticked);
            say(r.data.message, 'good');
            board();
            if (!dirty()) refresh();
          });
      });
    });
    Array.prototype.forEach.call(pane.querySelectorAll('[data-open]'), function (b) {
      b.addEventListener('click', function () {
        var p = document.getElementById('p-' + b.dataset.open);
        p.classList.toggle('open');
        var field = p.querySelector('textarea');
        if (p.classList.contains('open') && field) field.focus();
      });
    });
    Array.prototype.forEach.call(pane.querySelectorAll('[data-do]'), function (b) {
      b.addEventListener('click', function () { act(b, d); });
    });
  }

  // ---------------------------------------------------------------- the buttons

  function act(button, d) {
    var what = button.dataset.do, body = {run: d.run};
    if (what === 'decide') body.note = (document.getElementById('note') || {}).value || '';
    if (what === 'approve') body.quote = (document.getElementById('quote') || {}).value || '';
    if (what === 'run' && d.gated && !d.approved &&
        !window.confirm('This run is over the gate line and has no approval. It will refuse. ' +
                        'Send it anyway?')) return;
    button.disabled = true;
    say('working…');
    ask('POST', '/api/' + what, body).then(function (r) {
      button.disabled = false;
      if (!r.ok && r.data.needs === 'kill') {
        if (!window.confirm(r.data.error)) { say(r.data.error); return; }
        body.kill = true;
        return ask('POST', '/api/stop', body).then(function (again) {
          say(again.data.message || again.data.error, again.ok ? 'good' : 'bad');
          board();
          refresh();
        });
      }
      say(r.data.message || r.data.error, r.ok ? 'good' : 'bad');
      board();
      if (!r.ok || dirty()) return;
      // A launch returns the moment the process exists, which is before it has taken the lock
      // or written a line -- and a toy run can be over before the next repaint. So the log is
      // followed from here rather than from whatever the repaint happened to see.
      var launched = r.ok && what === 'run';
      return refresh().then(function () { if (launched) { pollLog(); follow(); } });
    });
  }

  // ---------------------------------------------------------------- the log

  function pollLog() {
    var box = document.getElementById('log');
    if (!box || !current) { stopFollowing(); return; }
    ask('GET', '/api/log/' + encodeURIComponent(current) + '?offset=' + logAt)
      .then(function (r) {
        if (!r.ok) return;
        if (r.data.restarted) box.textContent = '';
        logAt = r.data.offset;
        if (r.data.text) {
          var atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
          box.textContent += r.data.text;
          if (atEnd) box.scrollTop = box.scrollHeight;
        }
        // Only on a non-zero exit. A run that never started wrote nothing to the log -- refused
        // at the gate, locked, a check that came back an error -- and said why on the way out,
        // which is the one thing the log cannot tell you. A run that succeeded put all of it in
        // the log already, and appending its stdout as well would print every line twice.
        if (r.data.exit && r.data.said.length && !box.dataset.said) {
          box.dataset.said = '1';
          box.textContent += (box.textContent ? '\n' : '') + r.data.said.join('\n') + '\n';
          box.scrollTop = box.scrollHeight;
        }
        if (logTimer && !r.data.running && !r.data.live) { stopFollowing(); refresh(); }
      });
  }
  function stopFollowing() {
    if (logTimer) { clearInterval(logTimer); logTimer = null; }
  }

  // ---------------------------------------------------------------- go

  document.getElementById('reload').addEventListener('click', function () {
    ask('POST', '/api/state').then(board);
  });
  board().then(function () {
    var first = rail.querySelector('.item');
    if (first) open(first.dataset.run);
  });
  // The board, not the document: a page that reloaded itself would throw away what you were
  // typing, which is the one thing the static index does that this cannot.
  setInterval(board, 5000);
})();
"""


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
        f'<div class="bar"><span class="brand">{name}</span>'
        '<span class="stamp" id="stamp"></span><span class="spacer"></span>'
        '<button class="act" id="reload">rebuild the state page</button>'
        f"{THEME_BUTTONS}</div>\n"
        '<div class="board">\n'
        '<div class="rail" id="rail"></div>\n'
        '<div class="pane" id="pane">\n'
        '<div class="empty"><p>Pick a run on the left.</p></div>\n'
        "</div>\n</div>\n"
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

    The files matter: the run's live ``dashboard.html`` is shown in a frame rather than rebuilt,
    because it is already a complete self-refreshing page with the charts and the unit tables in
    it, and a second copy of that would be a second thing to keep in step.
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
