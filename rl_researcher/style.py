"""One stylesheet for every page the framework renders.

``BASE_CSS`` is the ground every page stands on: the colour tokens (light and dark, from
``plotstyle`` so charts and pages agree), the reset, the type scale, the header, the state
chips and the theme switch. A block (``rl_researcher.blocks``) owns the CSS for the classes it
emits; a page concatenates ``BASE_CSS`` and the CSS of exactly the blocks it uses. Until every
block owns its rules, ``LEGACY_DASHBOARD_CSS`` carries the dashboard rules that have not yet
been claimed by a block.
"""

from __future__ import annotations

from rl_researcher import charts
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

LEGACY_DASHBOARD_CSS = f"""  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(118px,1fr)); gap:1px;
    background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden;
    margin-bottom:12px }}
  .tile {{ background:var(--surface); padding:12px 14px }}
  .tile b {{ display:block; font-size:25px; font-weight:650; letter-spacing:-0.02em;
    font-variant-numeric:tabular-nums; line-height:1.15 }}
  .tile span {{ font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted) }}
  .lead {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap;
    background:var(--surface); border:1px solid var(--line); border-radius:10px;
    padding:10px 14px; margin-bottom:18px }}
  .lead .who {{ font-weight:600; font-size:13px; display:inline-flex; align-items:center; gap:7px }}
  .lead .who i {{ font-style:normal; color:var(--muted); font-weight:400; font-size:12px }}
  .lead .k {{ font-size:10.5px; text-transform:uppercase; letter-spacing:0.07em;
    color:var(--muted); font-weight:600 }}
  .stat {{ display:inline-flex; align-items:baseline; gap:7px; padding:3px 10px; border-radius:8px;
    border:1px solid var(--line); font-size:12px }}
  .stat b {{ font-size:15px; font-variant-numeric:tabular-nums }}
  .stat .n {{ color:var(--muted); font-size:10.5px; text-transform:uppercase; letter-spacing:0.05em }}
  .stat.ok {{ border-color:{tint("--ok", 27)}; background:{tint("--ok", 6)} }}
  .stat.ok b {{ color:var(--ok) }}
  .stat.no {{ border-color:{tint("--crit", 27)}; background:{tint("--crit", 6)} }}
  .stat.no b {{ color:var(--crit) }}
  .track {{ height:4px; background:var(--line); border-radius:3px; overflow:hidden; margin-top:5px }}
  .track i {{ display:block; height:100% }}
  .bigtrack {{ height:6px; margin:0 0 18px }}
  .panel {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
    overflow:hidden; margin-bottom:18px }}
  .scroll {{ overflow-x:auto }}
  table {{ width:100%; min-width:880px; border-collapse:collapse; font-size:13px }}
  th {{ text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em;
    color:var(--muted); font-weight:600; padding:9px 12px; border-bottom:1px solid var(--line) }}
  td {{ padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:middle;
    white-space:nowrap }}
  td.metrics, td.note {{ white-space:normal }}
  tr:last-child td {{ border-bottom:0 }}
  .num {{ font-variant-numeric:tabular-nums }}
  .of {{ color:var(--muted); font-size:11.5px }}
  .cell {{ font-weight:600 }}
  .seed {{ color:var(--muted); font-weight:400; margin-left:6px; font-size:12px }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px }}
  .metrics {{ font-variant-numeric:tabular-nums; font-size:12.5px }}
  .note {{ color:var(--muted); font-size:11.5px }}
  .glyph {{ vertical-align:-2px; margin-right:6px }}
  .mid {{ text-align:center }}
  .th2 {{ display:block; font-weight:400; text-transform:none; letter-spacing:0;
    color:var(--muted); font-size:10px; opacity:0.8 }}
  td.ok {{ color:var(--ok) }}
  td.no {{ color:var(--crit) }}
  td.ok .mark, td.no .mark {{ margin-left:4px; font-size:11px }}
  .curve {{ text-align:center; line-height:1 }}
  .curve .range {{ display:block; font-size:10px; color:var(--muted);
    font-variant-numeric:tabular-nums; margin-top:2px }}
  .curve .range b {{ color:var(--ink); font-weight:600 }}
  /* a curve names both of its axes: y at the ends, x underneath */
  /* One colour for the whole plot: the labels take it, the axis lines take it faintly. The
     axis was var(--line) under var(--muted) text, so the numbers were darker than the axis
     they annotate and read as loose digits beside a graph rather than as part of it. */
  .curvebox {{ position:relative; display:inline-block; color:var(--muted);
    padding:4px 0 12px 32px }}
  .curvebox i {{ font-style:normal; font-size:8.5px; line-height:1;
    font-variant-numeric:tabular-nums }}
  /* top and bottom here are this box's own padding, so .yax covers exactly the plot and its
     two labels can be pinned to the ends and centred on them. */
  .curvebox .yax {{ position:absolute; left:0; top:4px; bottom:12px; width:28px }}
  .curvebox .yax i {{ position:absolute; right:0; white-space:nowrap }}
  .curvebox .yax .hi {{ top:0; transform:translateY(-50%) }}
  .curvebox .yax .lo {{ bottom:0; transform:translateY(50%) }}
  .curvebox .xax {{ position:absolute; left:32px; right:0; bottom:0;
    display:flex; justify-content:space-between; align-items:baseline }}
  .curvebox .xax .xname {{ letter-spacing:0.04em; opacity:0.8 }}
  td.curve {{ padding-top:12px; padding-bottom:12px }}
  .legend .seedkey svg {{ margin-right:-3px }}
  .spark .floorlbl {{ font-size:7.5px; fill:var(--crit); opacity:0.85 }}
  .bestrow {{ background:{tint("--ok", 5)} }}
  .best {{ margin-left:7px; padding:0 6px; border-radius:999px; font-size:9.5px; font-weight:700;
    letter-spacing:0.05em; text-transform:uppercase; color:var(--ok);
    border:1px solid {tint("--ok", 27)}; background:{tint("--ok", 8)} }}
  .fails {{ display:flex; flex-direction:column; gap:12px; padding:6px 14px 14px }}
  .failrow {{ border-left:3px solid var(--crit); padding:1px 0 1px 11px }}
  .failwho {{ font-weight:600; display:flex; align-items:center; gap:0 }}
  .failwho .chip {{ margin-left:9px }}
  .failmsg {{ color:var(--crit); font-size:11.5px; margin-top:4px; white-space:pre-wrap;
    word-break:break-word;
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace }}
  .failnote {{ color:var(--muted); font-size:11px; margin-top:4px;
    font-variant-numeric:tabular-nums }}
  .queued {{ display:flex; flex-wrap:wrap; gap:6px; padding:11px 14px }}
  .qchip {{ display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:7px;
    border:1px solid var(--line); font-size:11.5px; color:var(--muted) }}
  .qchip i {{ font-style:normal; opacity:0.75 }}
  /* chart rows: HTML owns the layout, the SVG draws only the track */
  .mrows {{ padding:8px 14px 14px }}
  .mrow {{ display:grid; grid-template-columns:210px 108px 1fr 78px; align-items:center;
    gap:12px; padding:3px 0 }}
  .mname {{ font-size:12.5px; position:relative; cursor:help; display:flex; align-items:center;
    gap:6px; border-bottom:1px dotted var(--line); width:fit-content; max-width:100% }}
  .mname.vname {{ border-bottom:0; cursor:default; font-weight:600 }}
  .mtarget {{ font-size:10.5px; color:var(--muted); font-variant-numeric:tabular-nums;
    white-space:nowrap }}
  .mtarget .logs {{ display:block; font-size:9.5px; opacity:0.8; letter-spacing:0.04em }}
  .mtrack {{ min-width:0 }}
  .mval {{ font-size:12px; font-variant-numeric:tabular-nums; text-align:right;
    white-space:nowrap }}
  .mval .sp {{ color:var(--muted); font-size:10.5px }}
  svg.track {{ width:100%; height:{charts.TRACK_H}px; display:block }}
  /* Column count comes from the metric count so a panel is never orphaned; width only
     narrows it. auto-fit was what put three across and one stranded beneath. */
  .vtable {{ min-width:660px }}
  .vtable td.num {{ line-height:1.4 }}
  .vtable .sp, .vtable .crown {{ display:block; font-weight:400 }}
  .vtable .sp {{ color:var(--muted); font-size:10.5px }}
  .vtable .crown {{ margin:3px auto 0; width:fit-content; padding:0 6px; border-radius:999px;
    font-size:9px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase;
    color:var(--ok); border:1px solid {tint("--ok", 27)}; background:{tint("--ok", 8)} }}
  .vblock {{ min-width:0 }}
  .vhead {{ font-size:11.5px; font-weight:600; padding:8px 10px 2px; display:flex;
    justify-content:space-between; gap:8px }}
  .vhead .t {{ font-weight:400; color:var(--muted); font-size:10.5px;
    font-variant-numeric:tabular-nums }}
  .vgrid .mrow {{ grid-template-columns:150px 1fr 84px }}
  .vgrid .mrows {{ padding:0 10px 6px }}
  /* the explanation bubble */
  .bubble {{ position:absolute; left:0; top:calc(100% + 7px); z-index:20; width:310px;
    /* .up flips it above, for rows the panel would otherwise clip at the bottom */
    display:none; flex-direction:column; gap:5px; padding:10px 12px; border-radius:9px;
    background:var(--surface); border:1px solid var(--line); box-shadow:0 6px 22px #0000002e;
    font-size:11.5px; line-height:1.45; text-transform:none; letter-spacing:0 }}
  .bubble.up {{ top:auto; bottom:calc(100% + 7px) }}
  .mname:hover .bubble, .mname:focus-within .bubble {{ display:flex }}
  .bubble b {{ font-size:12.5px }}
  .bubble .t {{ color:var(--crit); font-variant-numeric:tabular-nums; font-weight:600 }}
  .bmath {{ display:block; margin:4px 0 5px; overflow-x:auto; overflow-y:hidden }}
  .bmath .tex {{ display:block }}
  .bubble .w {{ color:var(--ink) }}
  .bubble .y {{ color:var(--muted) }}
  .legend {{ display:flex; flex-wrap:wrap; gap:6px 16px; padding:10px 14px 2px }}
  .legend .barkey {{ color:var(--muted) }}
  .legend .key {{ display:inline-flex; align-items:center; gap:6px; font-size:11.5px }}
  .legend .key i {{ font-style:normal; color:var(--muted); font-variant-numeric:tabular-nums }}
  /* one metric's axis: stretched SVG behind, markers positioned as HTML on top */
  .tr {{ position:relative; height:{charts.TRACK_H}px }}
  .trbg {{ position:absolute; inset:0; width:100%; height:100% }}
  /* the scale: without it a marker's position means nothing */
  .tr .end {{ position:absolute; bottom:-1px; font-size:9px; color:var(--muted); opacity:0.85;
    font-variant-numeric:tabular-nums; pointer-events:none }}
  .tr .end.lo {{ left:0 }}
  .tr .end.hi {{ right:0 }}
  .mk {{ position:absolute; top:50%; transform:translate(-50%,-50%); line-height:0;
    pointer-events:auto }}
  .axis {{ stroke:var(--line); stroke-width:1 }}
  .bar {{ stroke:var(--crit); stroke-width:1.4; stroke-dasharray:3 3; fill:none }}
  .pass {{ fill:var(--ok); fill-opacity:0.09 }}
  /* Dashed, because an outlined seed-2 marker is hollow too. Grey and broken is what
     keeps "there is no number here" apart from "this is the third seed". */
  .nanpt {{ fill:none; stroke:var(--muted); stroke-width:1.2; stroke-dasharray:2 1.6 }}
  /* Solid and in the critical colour: a diverged rollout is a result, not a missing one. */
  .divpt {{ fill:none; stroke:var(--crit); stroke-width:1.6; stroke-linecap:round }}
  /* curve() draws these; with no stroke they were present and invisible. */
  .spark .cax {{ stroke:currentColor; stroke-width:1; fill:none; opacity:0.28 }}
  .spark .floor {{ stroke:var(--crit); stroke-width:1; stroke-dasharray:2 2; opacity:0.55 }}
  .log {{ padding:10px 14px; background:var(--code); font-size:11.5px; line-height:1.6;
    overflow-x:auto; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace }}
  .log .ln {{ white-space:pre; padding:0.5px 0 }}
  .log .step {{ display:grid; grid-template-columns:66px 150px 118px 1fr; gap:8px;
    white-space:nowrap; color:var(--muted) }}
  .log .cellchip {{ font-weight:600; overflow:hidden; text-overflow:ellipsis }}
  .log .stepn {{ text-align:right; font-variant-numeric:tabular-nums; color:var(--ink) }}
  .log .rest {{ overflow:hidden; text-overflow:ellipsis }}
  .log .info {{ color:var(--muted) }}
  .log .ts {{ color:var(--muted); opacity:0.7 }}
  .log .crit {{ color:var(--crit); font-weight:600 }}
  .log .ok {{ color:var(--ok) }}
  .log .warn {{ color:var(--warn) }}
  /* The horizontal scrollers were light strips in dark mode. */
  .scroll, .log {{ scrollbar-width:thin; scrollbar-color:var(--line) transparent }}
  .scroll::-webkit-scrollbar, .log::-webkit-scrollbar {{ height:9px }}
  .scroll::-webkit-scrollbar-track, .log::-webkit-scrollbar-track {{ background:transparent }}
  .scroll::-webkit-scrollbar-thumb, .log::-webkit-scrollbar-thumb {{
    background:var(--line); border-radius:6px }}
  .scroll::-webkit-scrollbar-thumb:hover, .log::-webkit-scrollbar-thumb:hover {{
    background:var(--muted) }}
  .foot {{ color:var(--muted); font-size:11.5px; margin-top:10px }}
  .foot code {{ background:var(--code); padding:1px 5px; border-radius:4px }}
  /* the ladder: every unit as an arms-by-seeds grid, for a page that draws the shape of the
     run rather than a list of its cells */
  .ladder {{ padding:2px 14px 12px }}
  .lrow {{ display:grid; gap:1px; align-items:stretch }}
  .lrow + .lrow {{ margin-top:1px }}
  .lhead {{ font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em;
    color:var(--muted); font-weight:600; padding:8px 2px }}
  .larm {{ display:flex; align-items:center; gap:7px; font-size:12.5px; font-weight:600;
    padding:9px 2px; min-width:0; overflow-wrap:anywhere }}
  .lcell {{ background:var(--ground); border:1px solid var(--line); border-radius:8px;
    padding:7px 9px; font-size:11.5px; font-variant-numeric:tabular-nums; min-width:0 }}
  .lcell .lwhat {{ display:block; color:var(--muted); font-size:10.5px; margin-top:3px;
    overflow-wrap:anywhere }}
  .lcell.q {{ opacity:0.55 }}
"""
