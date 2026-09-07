"""One stylesheet for every page the framework renders.

``BASE_CSS`` is the ground every page stands on: the colour tokens (light and dark, from
``plotstyle`` so charts and pages agree), the reset, the type scale, the header, the state
chips and the theme switch. A block (``rl_researcher.blocks``) owns the CSS for the classes it
emits; a page concatenates ``BASE_CSS`` and the CSS of exactly the blocks it uses. Until every
block owns its rules, and what ``charts`` draws is styled by ``charts.CHART_CSS``
been claimed by a block.
"""

from __future__ import annotations

from rl_researcher import plotstyle as ps


def tint(token: str, pct: int) -> str:
    """``token`` at ``pct`` percent over the page, for borders and fills.

    A custom property cannot take an alpha byte the way a hex literal can, so a tint of a
    themed colour is mixed.
    """
    return f"color-mix(in srgb, var({token}) {pct}%, transparent)"


#: The body of the theme-and-scroll script, with no ``<script>`` tags of its own: every caller
#: wraps it. It used to carry its own tags, and because the callers wrapped it anyway the page
#: shipped three opens and two closes, so the browser read the literal text ``<script>`` as the
#: first token of the program and threw a SyntaxError. The theme toggle and the scroll restore
#: were dead in every page this package rendered, silently, because a dead script is invisible.
THEME_SCRIPT = """
(function () {
  var KEY = 'rl-theme', POS = 'rl-scroll-' + location.pathname;
  function apply(mode) {
    if (mode === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', mode);
    var b = document.querySelectorAll('.theme button');
    for (var i = 0; i < b.length; i++)
      b[i].setAttribute('aria-pressed', String(b[i].getAttribute('data-mode') === mode));
  }
  var saved = 'system';
  try { saved = localStorage.getItem(KEY) || 'system'; } catch (e) {}
  apply(saved);
  addEventListener('DOMContentLoaded', function () {
    apply(saved);
    var b = document.querySelectorAll('.theme button');
    for (var i = 0; i < b.length; i++) b[i].addEventListener('click', function () {
      var m = this.getAttribute('data-mode');
      try { localStorage.setItem(KEY, m); } catch (e) {}
      apply(m);
    });
    // The page reloads itself every few seconds; put the reader back where they were.
    try {
      var y = sessionStorage.getItem(POS);
      if (y) scrollTo(0, parseInt(y, 10));
    } catch (e) {}
    addEventListener('scroll', function () {
      try { sessionStorage.setItem(POS, String(scrollY)); } catch (e) {}
    }, { passive: true });
  });
})();
"""

THEME_BUTTONS = ('<div class="theme">'
                 '<button type="button" data-mode="system" aria-pressed="true">auto</button>'
                 '<button type="button" data-mode="light" aria-pressed="false">light</button>'
                 '<button type="button" data-mode="dark" aria-pressed="false">dark</button></div>')

BASE_CSS = f"""
  :root {{
    --ink:{ps.INK}; --muted:{ps.MUTED}; --line:{ps.LINE}; --surface:{ps.SURFACE};
    --ground:#F7F9F8; --code:{ps.CODE_BG}; --accent:{ps.ACCENT};
    /* Every colour a chart or a status chip draws in. These were baked light-mode hexes, so
       on dark the target line, its label, the shaded passing side and the failure chips all
       came out near-black red: one cause, four symptoms. */
    --crit:{ps.CRIT}; --ok:{ps.OK}; --warn:{ps.WARN};
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ink:#E8EDEB; --muted:#93A09C; --line:#2A332F; --surface:#151A18; --ground:#0E1211;
      --code:#1B2220; --accent:#7C9BFF;
      --crit:#E8776A; --ok:#4BAE7A; --warn:#D9A233;
    }}
  }}
  :root[data-theme="dark"] {{
    --ink:#E8EDEB; --muted:#93A09C; --line:#2A332F; --surface:#151A18; --ground:#0E1211;
    --code:#1B2220; --accent:#7C9BFF;
    --crit:#E8776A; --ok:#4BAE7A; --warn:#D9A233;
  }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:var(--ground); color:var(--ink);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
  /* No width cap: the panels here are tables and axes, which use whatever room there
     is. A measure limit belongs on running prose, and there is none on this page. */
  .wrap {{ margin:0 auto; padding:24px 20px 56px }}
  a {{ color:var(--accent) }}
  h1 {{ font-size:19px; margin:0; letter-spacing:-0.01em }}
  h2 {{ font-size:11px; text-transform:uppercase; letter-spacing:0.07em; color:var(--muted);
    margin:0; padding:10px 14px; border-bottom:1px solid var(--line); font-weight:600 }}
  .chart.grid .colhead {{ font-size:10px; fill:var(--muted); text-transform:uppercase;
    letter-spacing:0.05em }}
  .head {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:16px }}
  .head .spacer {{ flex:1 }}
  .chip {{ display:inline-block; padding:1px 9px; border-radius:999px; border:1px solid;
    font-size:11.5px; font-weight:600; white-space:nowrap }}
  .theme {{ display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden }}
  .theme button {{ background:none; border:0; color:var(--muted); font:inherit; font-size:11.5px;
    padding:4px 10px; cursor:pointer }}
  .theme button + button {{ border-left:1px solid var(--line) }}
  .theme button[aria-pressed="true"] {{ background:var(--code); color:var(--ink); font-weight:600 }}
  .chip.t-crit {{ color:var(--crit); border-color:{tint("--crit", 34)}; background:{tint("--crit", 12)} }}
  .chip.t-ok {{ color:var(--ok); border-color:{tint("--ok", 34)}; background:{tint("--ok", 12)} }}
  .chip.t-warn {{ color:var(--warn); border-color:{tint("--warn", 34)}; background:{tint("--warn", 12)} }}
  .chip.t-accent {{ color:var(--accent); border-color:{tint("--accent", 34)};
    background:{tint("--accent", 12)} }}
  .chip.t-muted {{ color:var(--muted); border-color:{tint("--muted", 34)} }}
"""
