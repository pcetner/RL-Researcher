(function () {
  'use strict';
  const pane = document.getElementById('pane');
  const rail = document.getElementById('rail');
  const sessions = new Map();
  let current = null, generation = 0, boardData = null, busy = false, polling = false;
  let railSignature = '', railInteracting = false, homeView = 'home', homeSignature = '';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const plain = (s) => String(s || '').replace(/\*\*|`/g, '');
  const duration = (s) => s == null ? 'Unknown' : s < 90 ? Math.round(s) + 's' :
    s < 5400 ? Math.round(s / 60) + 'm' : Math.floor(s / 3600) + 'h ' + Math.round(s % 3600 / 60) + 'm';
  const session = () => sessions.get(current);
  const isRunning = d => d.state === 'running' || d.starting || !!(d.holder && d.holder.alive);
  const chip = (text, tone='muted') => '<span class="chip t-' + tone + '">' + esc(text) + '</span>';
  const button = (label, action, cls='') => '<button class="act ' + cls + '" data-action="' + action + '">' + label + '</button>';
  const link = (path, label) => path ? '<a href="/' + String(path).split('/').map(encodeURIComponent).join('/') + '"' +
    (/\.(md|markdown|toml|txt|json)$/i.test(path) ? ' data-document="' + esc(path) + '"' : '') + '>' + label + '</a>' : '';
  const inline = text => esc(String(text || '').replace(/\\_/g, '_')).replace(/`([^`]+)`|\*\*(.+?)\*\*/g,
    (_, code, strong) => code ? '<code>' + code + '</code>' : '<strong>' + strong + '</strong>');
  const age = date => { const t = Date.parse(date); if (!Number.isFinite(t)) return '—';
    if (/^\d{4}-\d{2}-\d{2}$/.test(date)) { const days = Math.floor(Math.max(0, Date.now() - t) / 86400000); return days ? days + 'd' : '<1d'; }
    const hours = Math.max(0, (Date.now() - t) / 3600000); return hours < 1 ? '<1hr' : hours < 24 ? Math.floor(hours) + 'hr' : Math.floor(hours / 24) + 'd'; };
  const since = r => r.since || r.finished || r.date || '';
  const ageTag = date => '<time class="waiting-age" title="' + esc(date ? 'Since ' + date : 'Waiting time unavailable') + '">' + esc(age(date)) + '</time>';
  function outcomeText(research) {
    const identifiers = new Set((research.rows || []).flatMap(r => [r.arm, r.metric]));
    return String(research.headline || '').split(/(`[^`]+`|\*\*[^*]+\*\*|[A-Za-z_][A-Za-z_0-9-]*)/g).map(token =>
      identifiers.has(token) ? '<code>' + esc(token) + '</code>' : inline(token)).join('');
  }


  const resourceErrors = new Map(), lastUpdates = new Map();
  function resource(name, error=null) {
    if (error) resourceErrors.set(name,error); else { resourceErrors.delete(name); lastUpdates.set(name,new Date()); }
    const failure = resourceErrors.values().next().value;
    const label = failure ? ({transport:'Connection lost · retrying',http:'Server request failed',response:'Invalid server response',render:'Could not display updates'}[failure.kind || 'render']) : boardData?.context?.snapshot ? 'Preview' : 'Live · 5s';
    $('connection').textContent = label + (failure?.kind === 'http' ? ' · ' + failure.message : '');
    $('connection').title = (failure ? failure.message + '. ' : '') + 'Last successful board refresh: ' + (lastUpdates.get('board')?.toLocaleTimeString() || 'Never');
    $('connection').className = failure ? 'stamp bad' : 'stamp';
    $('reload').hidden = !failure; $('reload').textContent = failure?.kind === 'transport' ? 'Reconnect' : 'Retry';
  }
  async function request(method, url, body) {
    let response;
    try { response = await fetch(url,{method,credentials:'same-origin',headers:body ? {'Content-Type':'application/json'} : {},body:body ? JSON.stringify(body) : undefined}); }
    catch (error) { error.kind = 'transport'; throw error; }
    let data;
    try { data = await response.json(); }
    catch (_) { const error = new Error(response.ok ? 'Expected a JSON response.' : 'HTTP ' + response.status); error.kind = response.ok ? 'response' : 'http'; throw error; }
    if (!response.ok) { const error = new Error('HTTP ' + response.status + ': ' + (data.error || data.message || 'Request failed')); error.kind = 'http'; error.data = data; throw error; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) { const error = new Error('Expected a response object.'); error.kind = 'response'; throw error; }
    return data;
  }
  function say(message, bad=false) {
    if (!$('message')) return;
    $('message').textContent = message;
    $('message').className = 'message ' + (bad ? 'bad' : 'good');
    if (session()) session().message = {message, bad};
  }
  function hasEdits() {
    return !!pane.querySelector('.authored.dirty, .authored.editing');
  }

  function paintRail(data) {
    if (railInteracting) return;
    const query = ($('search').value || '').toLowerCase();
    const target = $('run-list');
    const seen = new Set();
    const groups = [[], [], [], []];
    function add(index, name, note, date) {
      if (!name || seen.has(name)) return;
      seen.add(name);
      if (!name.toLowerCase().includes(query)) return;
      groups[index].push({name, note, date});
    }
    (data.running || []).filter(r => (r.failed || []).length || (r.stale || []).length).forEach(r =>
      add(0, r.run, (r.failed || []).length ? 'Failed · ' + r.failed.length + ' units' : 'Stale heartbeat', since(r)));
    (data.waiting || []).forEach(r => add(0, r.run, r.options && r.options.length ? 'Decision' : 'Report', since(r)));
    (data.running || []).forEach(r => add(r.state === 'running' ? 1 : 0, r.run,
      r.state === 'running' ? r.done + '/' + r.total + ' units · active ETA ' + r.eta : plain(r.state || 'Stopped'), since(r)));
    (data.queued || []).forEach(r => add(2, r.run, r.hold ? 'On hold' : 'Queued', since(r)));

    (data.decided || []).forEach(r => add(3, r.run, plain((r.chose || []).join(', ')) || 'Decided', since(r)));
    const historyOpen = !!target.querySelector('details[open]');
    const focused = document.activeElement && document.activeElement.dataset.run;
    const titles = ['Needs attention', 'Running', 'Queued / On hold', 'History'];
    const signature = JSON.stringify([groups, current, query]);
    if (signature !== railSignature) {
      const scroll = rail.scrollTop;
      railSignature = signature;
      target.innerHTML = groups.map((items, i) => {
        if (!items.length) return '';
        const rows = items.map(r => '<button class="item" data-run="' + esc(r.name) + '" aria-current="' +
          (r.name === current) + '"><span class="name">' + esc(r.name) + '</span><span class="why">' +
          esc(r.note) + '</span>' + ageTag(r.date) + '</button>').join('');
        return i === 3 ? '<details class="group"' + (historyOpen || query ? ' open' : '') + '><summary>' +
          titles[i] + ' · ' + items.length + '</summary>' + rows + '</details>' :
          '<section class="group"><h2>' + titles[i] + ' · ' + items.length + '</h2><div class="rail-columns"><span>Run</span><span>Needs</span><span>Waiting</span></div>' + rows + '</section>';
      }).join('') || '<p class="health">' + (query ? 'No matching runs.' : 'No registered runs.') + '</p>';
      target.querySelectorAll('[data-run]').forEach(b => {
        b.onclick = () => openRun(b.dataset.run);
        if (focused === b.dataset.run) b.focus({preventScroll:true});
      });
      rail.scrollTop = scroll;
    }
    const h = data.health || {};
    $('health').innerHTML = '<summary>Project health' + ((h.unreadable_specs || []).length ? ' · needs attention' : '') +
      '</summary><p>' + esc(h.findings || 0) + ' findings</p><p>Watcher: ' + esc(h.watcher_last_tick || 'Not started') +
      '</p>' + (h.unreadable_specs || []).map(x => '<p class="fail">' + esc(x) + '</p>').join('');
  }
  async function board() {
    const previous = boardData, oldPane = [...pane.childNodes], oldList = [...$('run-list').childNodes], oldHealth = [...$('health').childNodes], oldSignature = railSignature;
    let rendering = false;
    try {
      const data = await request('GET','/api/state');
      if (['running','waiting','ready','queued','decided'].some(k => !Array.isArray(data[k])) ||
          data.catalog && (!Array.isArray(data.catalog) || !Array.isArray(data.on_hold))) {
        const error = new Error('Invalid board collections.'); error.kind = 'response'; throw error;
      }
      rendering = true; boardData = data; paintRail(data); if (!current) paintHome(); resource('board');
    } catch (error) {
      boardData = previous;
      if (rendering) { pane.replaceChildren(...oldPane); $('run-list').replaceChildren(...oldList); $('health').replaceChildren(...oldHealth); railSignature = oldSignature; }
      resource('board',error);
    }
  }
  function route() {
    const hash = current ? '#run=' + encodeURIComponent(current) + '&view=' + session().view : '#' + homeView;
    if (location.hash !== hash) history.pushState(null, '', hash);
  }
  function readRoute() {
    const q = new URLSearchParams(location.hash.slice(1)), name = q.get('run');
    const view = ['overview','results','units','logs'].includes(q.get('view')) ? q.get('view') : 'overview';
    if (name) openRun(name, view, true); else openHome(true, location.hash === '#activity' ? 'activity' : location.hash === '#catalog' ? 'catalog' : 'home');
  }
  function openHome(fromHistory=false, view='home') {
    homeView = view;
    if (hasEdits() && !window.confirm('Leave unsaved report edits?')) { route(); return; }
    if (session()) session().scroll = pane.scrollTop;
    current = null; ++generation; rail.classList.remove('mobile-open'); $('toggle-runs').setAttribute('aria-expanded', false);
    if (!fromHistory) route();
    $('home-link').setAttribute('aria-current', homeView === 'home' ? 'page' : 'false');
    if (boardData) { paintRail(boardData); paintHome(); }
  }
  function paintHome() {
    if (boardData.catalog) { Workflow.paintHome(boardData, homeView, openRun); return; }
    const focused = pane.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused ? {run:focused.dataset.run, href:focused.getAttribute('href'), text:focused.textContent} : null;
    const openDetails = [...pane.querySelectorAll('.home details')].map(d => d.open);
    const scroll = pane.scrollTop;
    const d = boardData, c = d.context || {}, seen = new Set();
    const concise = text => plain(text).trim().split(/(?<=[.!?])\s+/).slice(0,2).join(' ');
    const dates = new Map([...(d.running || []), ...(d.waiting || []), ...(d.queued || []), ...(d.decided || [])].filter(r => since(r)).map(r => [r.run, since(r)]));
    const row = (name, reason, action='Open run', detail='', date=dates.get(name)) => '<div class="home-row"><div><strong>' + esc(name) + '</strong><p>' + inline(reason) + '</p>' +
      (detail ? '<details><summary>Full context</summary><p>' + esc(detail) + '</p></details>' : '') + '</div>' +
      ageTag(date) + (action ? '<button class="act" data-run="' + esc(name) + '">' + action + '</button>' : '') + '</div>';
    const registered = new Set([...(d.running || []), ...(d.waiting || []), ...(d.ready || []), ...(d.decided || [])].map(r => r.run));
    let attention = '';
    (d.queued || []).filter(r => r.hold).forEach(r => { seen.add(r.run); const why = plain(r.why || 'Research decision required.').trim();
      attention += row(r.run, 'Research hold · ' + (r.summary || concise(why)), registered.has(r.run) ? 'Open run' : '', why !== (r.summary || concise(why)) ? why : ''); });
    (d.running || []).filter(r => r.state !== 'running' || r.failed?.length || r.stale?.length).forEach(r => {
      if (!seen.has(r.run)) { seen.add(r.run); attention += row(r.run, r.failed?.length ? 'Failed units need attention' : 'Interrupted or stale work', 'View status'); } });
    (d.waiting || []).forEach(r => { if (!seen.has(r.run)) { seen.add(r.run); attention += row(r.run, plain(r.outcome || 'Review the completed evidence').split(/(?<=[.!?])\s+/)[0], 'Review result'); } });
    (d.ready || []).filter(r => r.approval_needed && !seen.has(r.run)).forEach(r => { seen.add(r.run); attention += row(r.run, 'Compute approval needed · ' + duration(r.wall_seconds), 'Review estimate'); });
    const active = (d.running || []).filter(r => r.state === 'running');
    const next = [...(d.queued || []), ...(d.ready || []).filter(r => !(d.queued || []).some(q => q.run === r.run) && !seen.has(r.run))];
    let html = '<div class="home"><div class="home-heading"><span class="eyebrow">Project home</span><h1>' + esc(d.project) + '</h1>' +
      '<p class="project-goal">' + esc(c.goal || 'Add a project goal in rl-researcher.toml to give this work context.') + '</p>' +
      (c.focus ? '<p><strong>Current focus</strong> · ' + esc(c.focus) + '</p>' : '') + link(c.plan, 'Project plan') +
      '</div>' +
      '<section><h2>Needs attention</h2>' + (attention || '<p class="muted">Nothing needs your attention.</p>') + '</section>' +
      '<section><h2><a href="#activity" data-activity>Happening now →</a></h2>' + (active.map(r => row(r.run, r.done + '/' + r.total + ' units complete · active unit ETA ' + (r.eta === '?' ? 'unknown' : r.eta) + ' · heartbeat ' + (r.heartbeat_age === '?' ? 'unknown' : r.heartbeat_age + ' ago'))).join('') || '<p class="muted">No runs are active.</p>') + '</section>' +
      '<section><h2>Next work</h2>' + (next.filter(r => !r.hold && !(d.decided || []).some(x => x.run === r.run) && !(d.waiting || []).some(x => x.run === r.run)).map(r => row(r.run, registered.has(r.run) ? r.summary || concise(r.why || 'Queued') : 'No matching run specification. Review the project queue.', registered.has(r.run) ? 'Open run' : '')).join('') || '<p class="muted">No unblocked work in the queue.</p>') + '</section>' +
      '<section><h2>Recent decisions</h2>' + ((d.decided || []).slice(0,5).map(r => row(r.run, plain((r.chose || []).join(' · ')), 'View evidence', '', r.date)).join('') || '<p class="muted">No recorded decisions yet.</p>') + '</section>' + '<section><h2>Recent findings</h2>' + ((d.findings || []).map(r => row(r.run, r.summary, registered.has(r.run) ? 'View evidence' : '', '', r.date)).join('') || '<p class="muted">No registered findings yet.</p>') + '</section></div>';
    if (homeView === 'activity') html = '<div class="home activity"><div class="home-heading"><h1>Happening now</h1><p>Active experiments, progress, and recent heartbeats.</p></div><section>' + (active.map(r => row(r.run, r.done + '/' + r.total + ' trials complete · heartbeat ' + r.heartbeat_age + ' ago', 'View progress')).join('') || '<p>No runs are active.</p>') + '</section></div>';
    if (homeSignature === html && pane.querySelector('.home')) return;
    homeSignature = html; pane.innerHTML = html;
    pane.querySelectorAll('.home details').forEach((d,i) => d.open = !!openDetails[i]);
    pane.scrollTop = scroll;
    pane.querySelectorAll('[data-activity]').forEach(a => a.onclick = e => { e.preventDefault(); openHome(false, 'activity'); });
    pane.querySelectorAll('[data-run]').forEach(b => b.onclick = () => openRun(b.dataset.run));
    if (focusKey) [...pane.querySelectorAll('a,button')].find(e => e.textContent === focusKey.text && e.dataset.run === focusKey.run && e.getAttribute('href') === focusKey.href)?.focus({preventScroll:true});
  }
  function metricHelp(r) {
    return '<button class="metric-trigger" data-help="' + esc([r.definition, r.formula, r.why].filter(Boolean).join('\n\n')) + '">' + esc(r.title === r.metric ? r.metric.replace(/_/g, ' ') : (r.title || r.metric)) + '</button>';
  }
  const help = document.createElement('div'); help.className = 'metric-popover'; help.id = 'metric-popover'; help.setAttribute('role','tooltip'); help.hidden = true; document.body.append(help);
  let helpOwner = null, helpPinned = false, helpTimer;
  function hideHelp() { help.hidden = true; helpOwner?.removeAttribute('aria-describedby'); helpOwner = null; helpPinned = false; }
  function showHelp(owner) {
    clearTimeout(helpTimer); if (!owner.dataset.help) return;
    helpOwner?.removeAttribute('aria-describedby'); helpOwner = owner; owner.setAttribute('aria-describedby', help.id);
    help.textContent = owner.dataset.help; help.hidden = false;
    const box = owner.getBoundingClientRect();
    help.style.left = Math.max(8, Math.min(box.left, window.innerWidth - help.offsetWidth - 8)) + 'px';
    help.style.top = Math.max(8, box.bottom + help.offsetHeight + 8 < window.innerHeight ? box.bottom + 4 : box.top - help.offsetHeight - 4) + 'px';
  }
  document.addEventListener('pointerover', e => { const owner = e.target.closest('[data-help]'); if (owner && !helpPinned) showHelp(owner); if (help.contains(e.target)) clearTimeout(helpTimer); });
  document.addEventListener('pointerout', e => { if (!helpPinned && (e.target.closest('[data-help]') || help.contains(e.target))) helpTimer = setTimeout(hideHelp, 180); });
  document.addEventListener('focusin', e => { if (e.target.matches('[data-help]')) showHelp(e.target); else if (!helpPinned) hideHelp(); });
  document.addEventListener('click', e => { const owner = e.target.closest('[data-help]'); if (owner) { if (helpPinned && helpOwner === owner) hideHelp(); else { showHelp(owner); helpPinned = true; } } else if (!help.contains(e.target)) hideHelp(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hideHelp(); });
  pane.addEventListener('scroll', hideHelp); window.addEventListener('resize', hideHelp);
  function shell(d) {

    pane.innerHTML = '<div class="run-header"><div class="workspace-head"><div class="title-row"><h1>' + esc(d.run) + '</h1>' +
      '<span id="status"></span><span id="next-action"></span><details class="tools"><summary aria-label="Run tools">More</summary>' +
      '<div class="tool-menu" id="tools"></div></details></div>' +
      '<nav class="tabs" role="tablist" aria-label="Run views">' +
      ['Overview','Results','Units','Logs'].map(t => '<button role="tab" id="tab-' + t.toLowerCase() +
        '" data-view="' + t.toLowerCase() + '" aria-controls="view">' + (t === 'Units' ? 'Run breakdown' : t) + '</button>').join('') +
      '</nav></div><div id="form-slot" class="header-form"></div></div><p id="message" class="message" role="status" aria-live="polite"></p>' +
      '<div class="content" id="view" role="tabpanel"></div>';
    pane.querySelectorAll('[data-view]').forEach(b => b.onclick = () => changeView(b.dataset.view));
    pane.querySelector('.tabs').onkeydown = event => {
      const tabs = Array.from(pane.querySelectorAll('[role=tab]'));
      const at = tabs.indexOf(document.activeElement);
      if (at < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 3 : (at + (event.key === 'ArrowRight' ? 1 : 3)) % 4;
      tabs[next].focus(); tabs[next].click();
    };
    $('form-slot').innerHTML = renderForm(d); session().formKind = formKind(d); if (!session().dirty || !session().formEvidenceRevision) session().formEvidenceRevision = d.evidence_revision; wireForm(); wireActions($('form-slot'));
    updateHeader(d);
  }
  function updateHeader(d) {
    const bad = ['FAILED','STALE'].includes(d.state) || (d.alerts || []).length;
    const label = d.snapshot ? 'Finished' : d.starting ? 'Starting' : d.decision ? 'Decided' : d.state === 'not started' ? 'Ready' : d.state;
    $('status').innerHTML = chip(label, bad ? 'crit' : isRunning(d) ? 'accent' : d.state === 'finished' ? 'ok' : 'muted');
    if (!$('next-action').contains(document.activeElement)) {
      $('next-action').innerHTML = '';
      wireActions($('next-action'));
    }
    const tools = $('tools');
    // Do not replace a focused/open menu during background updates.
    if (!tools.parentElement.open) {
      tools.innerHTML = link(d.report_source || d.page, 'Full report') + link(d.spec, 'Specification') + link(d.dashboard, 'Standalone dashboard') +
        (!d.snapshot && (d.capabilities ? d.capabilities.report.enabled : d.results && !isRunning(d)) ? button('Regenerate report', 'report') : '') +
        (isRunning(d) ? button('Stop run', 'stop', 'warn') : '');
      wireActions(tools);
    }
  }
  async function openRun(name, view, fromHistory=false) {
    if (hasEdits() && !window.confirm('Leave unsaved report edits?')) { if (fromHistory) route(); return; }
    if (session()) session().scroll = pane.scrollTop;
    current = name;
    rail.classList.remove('mobile-open'); $('toggle-runs').setAttribute('aria-expanded', false);
    const ticket = ++generation;
    if (!sessions.has(name)) sessions.set(name, {view:'overview', selected:null, note:'', quote:'', dirty:false, log:'', offset:0, follow:true});
    if (view) session().view = view;
    if (!fromHistory) route();
    $('home-link').setAttribute('aria-current', 'false');
    pane.innerHTML = '<p class="empty">Loading run…</p>';
    if (boardData) paintRail(boardData);
    try {
      const d = await request('GET', '/api/run/' + encodeURIComponent(name) + '?light=1');
      if (ticket !== generation) return;
      if (d.broken || d.error) throw new Error(d.broken || d.error);
      session().data = d;
      if (!session().dirty) session().revision = d.revision;
      shell(d); await renderView(); pane.scrollTop = session().scroll || 0;
      if (session().message) say(session().message.message, session().message.bad);
    } catch (e) { if (ticket === generation) { const entry = boardData?.catalog?.find(r => r.run === name); pane.innerHTML = '<p class="empty">' + esc(e.message) + '</p><p>' + esc(e.data?.next_step || entry?.next_step || 'Open Specifications to inspect this item.') + '</p>' + link(entry?.spec, 'Open specification') + ' ' + link('rl-researcher.toml', 'Open configuration'); } }
  }
  function metrics(research, all=false) {
    let rows = research.rows || [];
    const criteria = rows.filter(r => r.criterion);
    if (!all && criteria.length) rows = criteria;
    if (!rows.length) return '';
    {
      const arms = Array.from(new Set(rows.map(r => r.arm)));
      const names = Array.from(new Set(rows.map(r => r.metric)));
      return '<div class="table-wrap"><table class="results-table compact"><thead><tr><th scope="col">Metric / target</th>' +
        arms.map(a => '<th scope="col">' + esc(a) + '</th>').join('') + '</tr></thead><tbody>' + names.map(name => {
          const subset = rows.filter(r => r.metric === name);
          return '<tr><th scope="row">' + metricHelp(subset[0]) + '<span class="muted subline">' + esc(plain(subset[0].target)) + '</span></th>' + arms.map(arm => {
            const r = subset.find(x => x.arm === arm);
            if (!r) return '<td>Not measured</td>';
            return '<td>' + esc(r.result) + '<span class="subline"><span class="' +
              (r.status === 'Pass' ? 'pass' : r.status === 'Fail' ? 'fail' : '') + '">' + esc(r.status) +
              '</span><span class="muted"> · n=' + r.seeds + (r.diverged ? ' · ' + r.diverged + ' diverged' : '') + '</span></span></td>';
          }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>';
    }
  }

  function overviewBody(d) {
    const p = d.progress, r = d.research;
    const alerts = (d.alerts || []).map(a => '<div class="alert"><strong>' + esc(a.unit.replace('/seed', ' · seed ')) + '</strong><p class="error-text">' + esc(a.message) + '</p>' + (a.message.startsWith('Test fixture:') ? '<p class="muted">Simulated failure for interface testing.</p>' : '<p class="muted">Recorded error or status; cause may require investigation.</p>') + '</div>').join('');
    let body = d.hold ? '<div class="alert"><strong>Research hold</strong><p>' + esc(d.hold) + '</p><p>Compute approval does not release this hold. Open Overview, then On hold, to release it with an explanation.</p></div>' : '';
    body += alerts;
    body += (r.warnings || []).map(w => '<div class="alert"><p>' + esc(w) + '</p></div>').join('');
    if (isRunning(d) || !d.results) {
      const label = d.starting ? 'Waiting for the first heartbeat' : isRunning(d) ? 'Run in progress' :
        d.state === 'not started' ? 'Ready to run' : 'Run interrupted';
      body += '<div class="summary-card"><h2>' + label + '</h2>';
      if (d.alerts.length && !isRunning(d)) body += '<details><summary>View error details</summary>' + (d.recovery?.units || []).map(u => '<p><strong>' + esc(u.unit) + '</strong> · ' + esc(u.reason) + '</p>').join('') + button('View logs', 'logs') + '</details>';
      if (d.total && d.state !== 'not started') body += '<progress aria-label="Overall progress" max="' +
        Math.max(p.total, 1) + '" value="' + p.done + '"></progress><div class="facts">' +
        fact(d.done + '/' + d.total, 'units complete') + fact(duration(p.eta_seconds), 'estimated remaining') +
        fact(p.heartbeat_seconds == null ? 'Unknown' : duration(p.heartbeat_seconds) + ' ago', 'last heartbeat') + '</div>';
      if (!isRunning(d)) {
        body += '<div class="facts">' + fact(duration(d.cost.wall_seconds), d.cost.basis === 'budget-cap' ? 'maximum configured runtime' : 'estimated runtime') +
          fact(d.cost.money_usd == null ? 'Unknown' : '$' + d.cost.money_usd.toFixed(2), 'estimated cost') +
          fact(d.recovery?.status || 'Unknown', 'Recoverable?') + '</div><p class="muted">' +
          esc(d.cost.basis === 'measured' ? 'Based on ' + d.cost.samples + ' measured samples.' :
            d.cost.basis === 'budget-cap' ? 'Runtime not measured yet. The time above is the configured upper bound.' : 'Conservative estimate for the remaining work.') +
          ' ' + esc(d.cost.device?.name || '') + '</p>' +
          (d.recovery?.units?.length ? '<p class="muted">' + esc(d.recovery.reason) + '</p>' : '');
        if (d.gated && !d.approved) body += '<p>Compute approval needed. ' +
          (d.cost.wall_seconds > d.wall_limit_minutes * 60 ? 'This run ' + (d.cost.basis === 'budget-cap' ? 'may use up to ' : 'is estimated to take ') + duration(d.cost.wall_seconds) + '. Your automatic-start limit is ' + d.wall_limit_minutes + ' minutes. ' : '') +
          'Approval authorizes compute; it does not accept the research result.</p><ul class="estimate-reasons">' +
          d.why_gated.filter(w => !/wall time is over|no throughput record/.test(w)).map(w => '<li>' + esc(w.replace(/ line\b/g, ' limit').replace(/budget.cap/g, 'configured maximum')) + '</li>').join('') + '</ul>';
        else if (!d.hold) body += button(d.resumable ? 'Continue run (check checkpoint)' : d.done || d.alerts.length ? 'Retry remaining work' : 'Start run', 'run', 'go');
      }
      else body += button('Run breakdown', 'units');
      body += '</div>';
    }
    if (r.rows.length || d.results) body += '<div class="summary-card"><div class="eyebrow">' +
      (r.provisional ? 'Provisional results' : 'Registered outcome') + '</div><p class="outcome">' +
      outcomeText(r) + '</p><p class="muted">' + d.done + '/' + d.total + ' units complete' +
      ' · spread is population standard deviation</p>' + metrics(r) + '</div>';
    body += '<section class="question"><h2>Hypothesis</h2><p>' + inline(d.hypothesis_summary || d.question) + '</p>' + link(d.spec, 'Read the registration') + '</section>';
    if (d.results && !d.page && !isRunning(d)) body += button('Generate report', 'report', 'go');
    return '<p class="question-lead">' + inline(d.question_summary || d.question?.split(/(?<=[.!?])\s+/)[0]) + '</p>' + body;
  }
  function fact(value, label) { return '<div class="fact"><strong>' + esc(value) + '</strong><span>' + label + '</span></div>'; }
  function formKind(d) {
    if (d.snapshot && d.options.length) return 'decision';
    return d.decision ? 'recorded' : !isRunning(d) && (d.capabilities ? d.capabilities.decide.enabled : d.state === 'finished' && d.results && d.page && d.options.length) ? 'decision' :
      !isRunning(d) && d.gated && !d.approved ? 'approval' : '';
  }
  function renderForm(d) { return Workflow.reviewCard(d) + renderDecisionForm(d); }
  function renderDecisionForm(d) {
    const s = session(), kind = formKind(d);
    if (kind === 'recorded') return '<div class="decision-card"><p>Current evidence revision ' + esc(d.decision.evidence_revision || 'unknown') + '</p><h2>Decision recorded ' +
      chip(d.decision.id) + '</h2>' + ((d.decision.choices || []).length ? '<p><strong>' +
        esc(plain(d.decision.choices.join(' · '))) + '</strong></p>' : '') + '<p>' + esc(d.decision.note) + '</p></div>';
    if (kind === 'decision') {
      if (!d.evidence && !d.snapshot && !d.authored.some(r => r.startsWith('decision'))) return '<div class="decision-card"><h2>Decision</h2><p>Update this legacy report to enable the decision form.</p>' + button('Regenerate report','report','go') + '</div>';
      return '<form id="decision-form" class="decision-bar"><label for="reason">Decision</label>' +
        '<textarea id="reason" required rows="1" placeholder="Why this decision?">' + esc(s.note) + '</textarea><div class="decision-buttons">' +
        d.options.map((o,i) => '<button type="button" class="act" data-choice="' + i + '" title="' + esc(plain(o)) + '">' +
          esc(plain(o).split(' — ')[0].replace(/^(go|iterate|stop)$/, word => word[0].toUpperCase() + word.slice(1))) + '</button>').join('') + '</div><span id="draft-state" class="muted" role="status"></span></form>';
    }
    if (kind === 'approval') return '<form id="approval-form" class="approval-card"><h2>Approve compute</h2><p>Approval is recorded for this specification. Starting the run is a separate action.</p>' +
      '<label class="field" for="quote">Approval note</label><textarea id="quote" required placeholder="Why is this run worth the cost?">' +
      esc(s.quote) + '</textarea><div class="form-actions">' + button('Approve compute', 'approve', 'go') + '</div></form>';
    return '';
  }
  function wireForm() {
    const s = session();
    const form = $('decision-form') || $('approval-form');
    if (!form) { if ($('form-slot')) wireActions($('form-slot')); return; }
    form.onsubmit = e => e.preventDefault();
    form.oninput = () => {
      s.dirty = true;
      if ($('reason')) { s.note = $('reason').value; $('reason').setCustomValidity(''); $('reason').removeAttribute('aria-invalid'); }
      if ($('quote')) s.quote = $('quote').value;
      if ($('draft-state')) $('draft-state').textContent = 'Draft';
    };
    form.querySelectorAll('[data-choice]').forEach(b => b.onclick = () => {
      const reason = $('reason');
      if (!reason.value.trim()) {
        reason.setCustomValidity('Explain your decision first.'); reason.setAttribute('aria-invalid', 'true'); reason.reportValidity(); reason.focus();
        $('draft-state').textContent = 'A reason is required.'; return;
      }
      s.selected = [Number(b.dataset.choice)]; s.note = reason.value;
      if (s.data.snapshot) { $('draft-state').textContent = 'Preview: ' + b.textContent + ' selected; not saved.'; s.dirty = false; return; }
      act('decide', b);
    });
    wireActions(form);
  }
  async function changeView(view) {
    if (hasEdits() && !window.confirm('Leave unsaved report edits?')) return;
    session().view = view; ++generation; route();
    await renderView();
  }
  async function renderView() {
    const s = session(), d = s.data, ticket = generation;
    if (!d) return;
    pane.querySelectorAll('[data-view]').forEach(b => { const on = b.dataset.view === s.view;
      b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; });
    $('view').setAttribute('aria-labelledby', 'tab-' + s.view);
    if (s.view === 'overview') {
      $('view').innerHTML = '<div id="overview-data">' + overviewBody(d) + '</div><div id="live-progress"></div>';
      wireActions($('overview-data'));
      if (isRunning(d)) await loadContent('progress', $('live-progress'), ticket);
    } else if (s.view === 'logs') {
      $('view').innerHTML = '<div class="log-tools"><h2>Recent events</h2>' + button(s.follow ? 'Pause' : 'Follow', 'follow') +
        '</div><div id="events"></div><details><summary>Raw log</summary><pre id="log" class="log"></pre></details>';
      $('log').textContent = s.log || 'No log output yet.'; wireActions($('view')); await pollLog();
    } else {
      $('view').innerHTML = (s.view === 'results' ? '<h2>' + (d.research.provisional ? 'Provisional results' : 'Registered outcome') +
        '</h2><p>' + outcomeText(d.research) + '</p>' +
        (d.research.warnings || []).map(w => '<div class="alert">' + esc(w) + '</div>').join('') + metrics(d.research, true) : '<p class="muted">Each row is one trial: a variant and random seed. Use this view to find stalled trials, failures, or saved progress.</p>') +
        '<div id="detail-content"><p class="muted">Loading…</p></div>';
      await loadContent(s.view, $('detail-content'), ticket);
    }
  }
  async function loadContent(view, target, ticket) {
    const name = current;
    const sequence = Number(target.dataset.sequence || 0) + 1;
    target.dataset.sequence = sequence;
    const openDetails = Array.from(target.querySelectorAll('details')).map(x => x.open);
    try {
      const result = await request('GET', '/api/content/' + encodeURIComponent(name) + '?view=' + view);
      if (ticket !== generation || name !== current || !target.isConnected || Number(target.dataset.sequence) !== sequence) return;
      target.innerHTML = result.html || '<p class="muted">No ' + (view === 'results' ? 'report' : 'unit data') + ' yet.</p>';
      target.querySelectorAll('a[href]').forEach(a => { const url = new URL(a.getAttribute('href'), location.origin + '/' + (session()?.data.report_source || session()?.data.page || '')); if (url.origin === location.origin && /\.(md|markdown|toml|txt|json)$/i.test(url.pathname)) { a.dataset.document = decodeURIComponent(url.pathname.slice(1)); a.href = url.href; } });
      target.querySelectorAll('details').forEach((x,i) => { if (i < openDetails.length) x.open = openDetails[i]; });
      let css = $('detail-style');
      if (!css) { css = document.createElement('style'); css.id = 'detail-style'; document.head.appendChild(css); }
      css.textContent = result.css;
      target.dataset.revision = result.revision;
      if (session()?.data.snapshot) { target.querySelectorAll('.authored-tools, .authored-edit, .authored-save, .authored-revert, .authored-src, .authored-said').forEach(e => e.remove()); target.querySelectorAll('input').forEach(e => e.disabled = true); } else wireEditors(target);
    } catch (e) { if (ticket === generation && target.isConnected) target.textContent = e.message; }
  }
  function wireEditors(target) {
    target.querySelectorAll('.authored').forEach(sec => {
      const area = sec.querySelector('.authored-src'), note = sec.querySelector('.authored-said');
      let original = area.value;
      area.oninput = () => { sec.classList.add('dirty'); note.textContent = 'Unsaved'; };
      sec.querySelectorAll('input.tick').forEach(box => box.onchange = () => {
        let index = 0;
        area.value = area.value.replace(/^(\s*[-*] \[)[ xX](\].*)$/gm, (line,a,b) =>
          index++ === Number(box.dataset.option) ? a + (box.checked ? 'x' : ' ') + b : line);
        area.oninput();
      });
      sec.querySelector('.authored-edit').onclick = () => { sec.classList.toggle('editing'); if (sec.classList.contains('editing')) area.focus(); };
      sec.querySelector('.authored-revert').onclick = () => {
        area.value = original;
        const checks = Array.from(original.matchAll(/^\s*[-*] \[([ xX])\]/gm)).map(m => m[1].toLowerCase() === 'x');
        sec.querySelectorAll('input.tick').forEach((box,i) => { box.checked = !!checks[i]; });
        sec.classList.remove('dirty','editing'); note.textContent = '';
      };
      sec.querySelector('.authored-save').onclick = async () => {
        const name = current, ticket = generation, save = sec.querySelector('.authored-save');
        save.disabled = true;
        try {
          const result = await request('POST', '/api/region', {run:name, region:sec.dataset.region,
            body:area.value, revision:target.dataset.revision});
          if (ticket !== generation) return;
          target.dataset.revision = result.revision || target.dataset.revision;
          original = area.value;
          sec.classList.remove('dirty','editing'); note.textContent = 'Saved';
          if (!hasEdits()) { await refresh(); await renderView(); }
        } catch (e) { if (ticket === generation) note.textContent = e.message; }
        finally { save.disabled = false; }
      };
    });
  }
  function wireActions(target) {
    target.querySelectorAll('[data-action]').forEach(b => { b.type = 'button'; b.onclick = () => act(b.dataset.action, b); });
    if (busy) disableBusyActions(target);
  }
  function disableBusyActions(target) {
    target.querySelectorAll('[data-action]:not(:disabled)').forEach(b => {
      b.dataset.busyTitle = b.title; b.title = 'Saving and refreshing. Please wait.';
      b.dataset.workflowBusy = 'true'; b.disabled = true;
    });
  }
  async function act(action, b) {
    if (action === 'review-latest') { const s = session(); s.dirty = false; s.selected = null; await refresh(); await renderView(); say('Review the current evidence before submitting. Your note was kept.'); return; }
    if (action === 'reset-draft') {
      const s = session(); s.dirty = false; s.selected = null; s.note = ''; s.quote = '';
      await refresh(); await renderView(); say('Draft reset to the saved report.'); return;
    }
    if (action === 'units' || action === 'logs') return changeView(action);
    if (action === 'decision') {
      if (session().view !== 'overview') await changeView('overview');
      if ($('reason')) $('reason').focus();
      return;
    }
    if (action === 'follow') { session().follow = !session().follow; b.textContent = session().follow ? 'Pause' : 'Follow'; if (session().follow) await pollLog(); return; }
    if (busy) return;
    if (hasEdits()) { say('Save or discard your report edits first.', true); return; }
    const name = current, s = session(), ticket = generation;
    const body = {run:name};
    if (action === 'decide') { body.selected = s.selected; body.note = s.note; body.revision = s.revision; }
    if (action === 'decide' || action === 'review') { body.evidence_revision = s.formEvidenceRevision; if (action === 'review') body.note = $('review-note')?.value || ''; body.operation_id = Workflow.operation(action,body); }
    if (action === 'approve') { body.quote = s.quote; if (!s.quote.trim()) { $('quote').reportValidity(); return; } }
    busy = true; disableBusyActions(pane);
    say(action === 'report' ? 'Generating report…' : 'Saving…');
    try {
      let result;
      try { result = await request('POST', '/api/' + action, body); }
      catch (e) {
        if (e.data && e.data.needs === 'kill' && window.confirm('Terminate the run? Windows cannot perform a clean stop here. The last checkpoint is kept, but work since it was saved is lost.')) result = await request('POST', '/api/stop', {...body, kill:true});
        else throw e;
      }
      if (action === 'decide' || action === 'approve') s.dirty = false;
      if (action === 'decide') s.selected = null;
      if (action === 'run') { s.log = ''; s.offset = 0; s.exitShown = false; }
      let message = {review:'Evidence reviewed.', decide:'Decision recorded.', approve:'Compute approved. Research holds and launch checks still apply.', run:'Run starting…', report:'Report updated.', stop:'Stop requested.'}[action];
      if (action === 'approve' && result.gated === false) message = 'No approval needed. You can start the run.';
      s.message = {message, bad:false};
      if (ticket !== generation) return;
      say(message);
      await refresh(); await renderView(); await board();
    } catch (e) {
      if (e.data && e.data.revision) s.revision = e.data.revision;
      s.message = {message:e.message, bad:true};
      if (ticket !== generation) return;
      say(e.message, true);
      if (e.data?.reason_code === 'evidence_changed') { const review = document.createElement('button'); review.className = 'act'; review.textContent = 'Review latest evidence'; review.onclick = () => act('review-latest', review); $('message').append(review); }
    } finally {
      busy = false;
      pane.querySelectorAll('[data-workflow-busy]').forEach(control => {
        control.disabled = false; control.title = control.dataset.busyTitle;
        delete control.dataset.workflowBusy; delete control.dataset.busyTitle;
      });
    }
  }
  async function refresh() {
    if (!current) return;
    const name = current, ticket = generation, s = session();
    const sequence = s.requestSequence = (s.requestSequence || 0) + 1;
    try {
      const d = await request('GET', '/api/run/' + encodeURIComponent(name) + '?light=1');
      if (ticket !== generation || name !== current || s.requestSequence !== sequence) return;
      if (d.broken || d.error) { say(d.broken || d.error, true); return; }

      if (s.message && s.message.message === 'Run starting…' && !isRunning(d)) {
        say(d.state === 'finished' ? 'Run finished.' : 'Run needs attention.', d.state !== 'finished');
      }
      s.data = d;
      if (!s.dirty) s.revision = d.revision;
      updateHeader(d);
      if (!s.dirty && document.activeElement?.id !== 'review-note') { $('form-slot').innerHTML = renderForm(d); s.formKind = formKind(d); s.formEvidenceRevision = d.evidence_revision; wireForm(); wireActions($('form-slot')); }
      if (s.view === 'overview' && $('overview-data')) {
        // Only measured data changes while the user is editing a decision.
        const expanded = Array.from($('overview-data').querySelectorAll('details')).map(x => x.open);
        if (!$('overview-data').contains(document.activeElement)) {
          $('overview-data').innerHTML = overviewBody(d); wireActions($('overview-data'));
          $('overview-data').querySelectorAll('details').forEach((x,i) => { x.open = !!expanded[i]; });
        }
        if (isRunning(d)) await loadContent('progress', $('live-progress'), ticket);
        else $('live-progress').textContent = '';
      } else if (s.view === 'units' && !$('detail-content').contains(document.activeElement)) {
        await loadContent('units', $('detail-content'), ticket);
      } else if (s.view === 'results' && !hasEdits() && $('detail-content') &&
                 $('detail-content').dataset.revision && $('detail-content').dataset.revision !== d.revision) {
        const at = pane.scrollTop;
        await renderView(); pane.scrollTop = at;
      }
      resource("run");
    } catch (error) { if (ticket === generation) resource("run",error); }
  }
  async function pollLog() {
    const s = session();
    if (!s || !s.follow || s.view !== 'logs' || s.logBusy) return;
    s.logBusy = true;
    const name = current, ticket = generation;
    try {
      const d = await request('GET', '/api/log/' + encodeURIComponent(name) + '?offset=' + s.offset + (s.offset ? '' : '&tail=1'));
      if (ticket !== generation || name !== current) return;
      if (d.restarted) s.log = '';
      s.offset = d.offset; s.log = (s.log + d.text).slice(-200000);
      if (d.exit && d.said && d.said.length && !s.exitShown) { s.log += '\n' + d.said.join('\n'); s.exitShown = true; }
      if ($('events')) $('events').innerHTML = d.events && d.events.length ?
        '<ul class="events">' + d.events.map(e => '<li>' + esc(e) + '</li>').join('') + '</ul>' :
        '<p class="muted">No notable events yet.</p>';
      if ($('log')) {
        const log = $('log'), atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 12;
        log.textContent = s.log || 'No log output yet.';
        if (atEnd) log.scrollTop = log.scrollHeight;
      }
      resource("log");
    } catch (error) { resource("log",error); }
    finally { s.logBusy = false; }
  }
  const drawer = document.createElement('dialog'); drawer.className = 'document-drawer'; drawer.setAttribute('aria-labelledby', 'document-title');
  drawer.innerHTML = '<header><h2 id="document-title">Document</h2><button class="act" id="close-document">Close</button></header><div id="document-body"></div>';
  document.body.append(drawer);
  let documentOwner = null, documentSequence = 0;
  $('close-document').onclick = () => drawer.close();
  drawer.addEventListener('close', () => { ++documentSequence; (documentOwner?.isConnected ? documentOwner : $('brand-home')).focus(); });
  drawer.addEventListener('click', e => { if (e.target === drawer && e.clientX < drawer.getBoundingClientRect().left) drawer.close(); });
  async function openDocument(path, owner) {
    const ticket = ++documentSequence;
    if (!drawer.open) { documentOwner = owner; drawer.showModal(); }
    $('document-title').textContent = path.split('/').pop(); $('document-body').innerHTML = '<p class="empty">Loading…</p>';
    try {
      const data = await request('GET', '/api/document?path=' + encodeURIComponent(path));
      if (ticket !== documentSequence || !drawer.open) return;
      $('document-title').textContent = data.title; $('document-body').innerHTML = data.html;
      $('document-body').querySelectorAll('h1,h2,h3,h4').forEach(h => { if (!h.id) h.id = h.textContent.toLowerCase().replace(/[^a-z0-9 -]/g,'').replace(/\s+/g,'-'); });
      $('document-body').scrollTop = 0;
    } catch(e) { if (ticket === documentSequence) $('document-body').textContent = e.message; }
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a'); if (!a) return;
    if (a.dataset.document) { e.preventDefault(); openDocument(a.dataset.document, a); }
    else if (drawer.contains(a) && a.getAttribute('href')?.startsWith('#')) {
      e.preventDefault(); const id = decodeURIComponent(a.getAttribute('href').slice(1));
      const target = [...$('document-body').querySelectorAll('[id]')].find(el => el.id === id); target?.scrollIntoView();
    }
  });
  rail.innerHTML = '<a class="home-link" id="home-link" href="#home">Overview</a><button class="act mobile-runs" id="toggle-runs" aria-expanded="false" aria-controls="run-list">Runs</button><label class="muted" for="search">Find a run</label><input id="search" type="search" placeholder="Search runs…">' +
    '<div id="run-list"></div><details class="health" id="health"><summary>Project health</summary></details>';
  rail.addEventListener('pointerdown', () => { railInteracting = true; });
  window.addEventListener('pointerup', () => { railInteracting = false; });
  window.addEventListener('pointercancel', () => { railInteracting = false; });
  $('search').oninput = () => { if (boardData) { paintRail(boardData); if (!current) paintHome(); } };
  $('reload').onclick = async () => { await board(); await refresh(); };
  window.addEventListener('beforeunload', e => {
    if (hasEdits() || Array.from(sessions.values()).some(s => s.dirty)) { e.preventDefault(); e.returnValue = ''; }
  });
  $('home-link').onclick = e => { e.preventDefault(); openHome(); };
  $('brand-home').onclick = e => { e.preventDefault(); openHome(); };
  $('toggle-runs').onclick = () => { const open = rail.classList.toggle('mobile-open'); $('toggle-runs').setAttribute('aria-expanded', open); };
  window.addEventListener('popstate', readRoute);
  window.addEventListener('hashchange', readRoute);
  window.addEventListener('workflowchange', async () => { await board(); await refresh(); });
  board().then(readRoute);
  setInterval(async () => {
    if (polling || busy || document.hidden || boardData?.context?.snapshot) return;
    polling = true;
    try { await board(); await refresh(); } finally { polling = false; }
  }, 5000);
  setInterval(() => { if (!busy && session() && isRunning(session().data || {})) pollLog(); }, 1500);
})();
