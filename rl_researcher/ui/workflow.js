/* Explicit queue and evidence workflow UI. No research writes in saved previews. */
const Workflow = (() => {
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const requests = new Map(), drafts = new Map(), messages = new Map();
  let state = null, saving = false;
  function operation(action, body) {
    const key = JSON.stringify([action,body]);
    if (!requests.has(key)) requests.set(key,crypto.randomUUID());
    return requests.get(key);
  }
  const source = (path, label="Open specification") => path ? '<a href="/' + path.split('/').map(encodeURIComponent).join('/') + '" data-document="' + esc(path) + '">' + esc(label) + '</a>' : '';
  function control(action, name) {
    if (state.context?.snapshot) return '<p>Saved preview; actions unavailable.</p>';
    return '<form data-queue="' + action + '" data-target="' + esc(name) + '">' + (action === 'release' ?
      '<label>Release explanation<textarea data-queue-note required>' + esc(drafts.get(action + name) || '') + '</textarea></label>' : '') +
      '<button class="act" type="submit"' + (saving ? ' disabled' : '') + '>' + ({release:'Release hold',add:'Add to queue',remove:'Remove from queue'}[action]) + '</button><p role="status">' + esc(messages.get(action + name) || '') + '</p></form>';
  }
  function blockers(r) {
    if (r.diagnostic) return '<p class="alert">' + esc(r.diagnostic) + '</p><p>' + esc(r.next_step || '') + '</p>';
    return Object.entries(r.capabilities || {}).filter(([name,c]) => !c.enabled &&
      (name === 'run' || name === 'report' && !r.evidence?.report || name === 'decide' && r.decision_pending)).map(([name,c]) =>
      '<p class="muted">' + esc(name + ': ' + c.reason + ' ' + c.next_step) + '</p>').join('');
  }
  function paintHome(data, view, openRun) {
    state = data;
    const pane = document.getElementById('pane');
    if (pane.querySelector('.workflow-home') && document.activeElement?.matches('[data-queue-note]')) return;
    const catalog = data.catalog, registered = new Map(catalog.map(r => [r.run,r]));
    const row = (r, label, action='') => '<article class="workflow-row" data-item="' + esc(r.run) + '"><strong>' + esc(r.run) + '</strong><p>' + esc(label) + '</p>' + blockers(r) + source(r.spec) + (r.artefact ? ' ' + source(r.artefact,'Open historical report') : '') + (r.diagnostic ? ' ' + source('rl-researcher.toml','Open configuration') : '') + (r.source_files || []).map(p => ' ' + source(p,'Open evidence')).join('') +
      (registered.get(r.run)?.valid && !registered.get(r.run)?.diagnostic ? ' <button class="act" data-open-run="' + esc(r.run) + '">Open run</button>' : '') + action + '</article>';
    const section = (title, body, empty) => '<section><h2>' + title + '</h2>' + (body || '<p class="muted">' + empty + '</p>') + '</section>';
    const held = data.on_hold.map(q => row({...registered.get(q.run),...q},q.why || 'Research hold requires release.',control('release',q.run))).join('');
    const next = data.queued.filter(q => !q.hold && !['running','finished'].includes(registered.get(q.run)?.state)).map(q => row({...registered.get(q.run),...q},q.summary || q.why || 'Explicitly queued',control('remove',q.run))).join('');
    const attention = catalog.filter(r => r.diagnostic || r.review_required || r.decision_pending || ['FAILED','STALE','stopped'].includes(r.state)).map(r => row(r,
      [r.review_required ? 'Evidence needs review' : '',r.decision_pending ? 'Decision needed' : '',r.diagnostic ? 'Needs repair' : ''].filter(Boolean).join(' · '))).join('');
    const active = data.running.filter(r => r.state === 'running').map(r => row(r,r.done + '/' + r.total + ' trials complete')).join('');
    const query = (document.getElementById('search').value || '').toLowerCase();
    const specs = catalog.filter(r => (r.run + ' ' + r.spec).toLowerCase().includes(query)).map(r => row(r,r.diagnostic || r.state || 'Registered',
      data.queued.some(q => q.run === r.run && !q.hold) ? control('remove',r.run) : r.valid && !data.queued.some(q => q.run === r.run) ? control('add',r.run) : '')).join('');
    const history = catalog.flatMap(r => [...(r.reviews || []).map(x => ({...x,run:r.run,label:'Reviewed'})),...(r.decisions || []).map(x => ({...x,run:r.run,label:x.choices.join(' · ')}))]);
    const ids = new Set(history.map(r => r.id));
    data.decided.filter(r => !ids.has(r.id)).forEach(r => history.push({...r,label:(r.chose || []).join(' · ')}));
    const historical = history.map(r => row(r,r.label + (r.id ? ' [' + r.id + ']' : '') + (r.note ? ' · ' + r.note : '') + ' · ' + (r.applicability || 'Evidence revision unknown') + ' · ' + (r.evidence_revision?.slice(0,12) || 'unversioned') + ' · ' + (r.date || 'Unknown date'))).join('');
    const html = '<div class="home workflow-home"><h1>' + esc(data.project) + '</h1><p>' + esc(data.context.goal || '') + '</p><p>' + esc(data.context.focus || '') + '</p>' +
      '<nav><a href="#home">Overview</a> · <a href="#catalog">Specifications</a> · <a href="#activity">Happening now</a></nav>' +
      (data.health.queue_error ? '<p class="alert">' + esc(data.health.queue_error) + '</p>' : '') +
      (view === 'catalog' ? section('Specifications',specs,'No registered specifications.') : view === 'activity' ? section('Running',active,'No active runs.') :
        section('On hold',held,'No research holds.') + section('Needs attention',attention,'Nothing needs attention.') + section('Next work',next,'No unblocked queued work.') + section('Running',active,'No active runs.') + section('History',historical,'No reviews or decisions.')) + '</div>';
    if (pane.dataset.workflowHtml === html && pane.querySelector('.workflow-home')) return;
    const prepared = document.createElement('div'); prepared.innerHTML = html;
    prepared.querySelectorAll('[data-open-run]').forEach(b => b.onclick = () => openRun(b.dataset.openRun));
    const scroll = pane.scrollTop; pane.replaceChildren(...prepared.childNodes); pane.scrollTop = scroll; pane.dataset.workflowHtml = html;
  }
  function reviewCard(d) {
    if (!d.evidence) return '';
    let html = '<div class="review-card"><h2>Evidence</h2><p>' + esc(d.evidence.explanation || d.evidence.completion) + ' · revision ' + esc(d.evidence_revision.slice(0,12)) + '</p>';
    html += (d.evidence.sources || []).map(x => source(d.out + '/' + x.path,'Open evidence: ' + x.path)).join(' · ');
    if (d.review) html += '<p>Reviewed · ' + esc(d.review.date) + ' · ' + esc(d.review.actor || 'Local user') + '</p>';
    else if (d.capabilities.review.enabled && !d.snapshot) html += '<label>Review note (optional)<textarea id="review-note" data-review-run="' + esc(d.run) + '">' + esc(drafts.get('review:' + d.run) || '') + '</textarea></label><button class="act" data-action="review">Mark reviewed</button>';
    else html += '<p>' + esc(d.capabilities.review.reason + ' ' + d.capabilities.review.next_step) + '</p>';
    if (d.decision_required && !d.decision && !d.capabilities.decide.enabled) html += '<p>Decision needed: ' + esc(d.capabilities.decide.reason + ' ' + d.capabilities.decide.next_step) + '</p>';
    (d.decisions || []).filter(x => x.applicability !== 'Current').forEach(x => { html += '<p>Previous decision: ' + esc(x.choices.join(' · ') + ' · ' + x.applicability + ' · ' + (x.evidence_revision || 'unversioned')) + '</p>'; });
    return html + '</div>';
  }
  document.addEventListener('input', e => {
    if (e.target.matches('[data-review-run]')) drafts.set('review:' + e.target.dataset.reviewRun,e.target.value);
    if (e.target.matches('[data-queue-note]')) { const f = e.target.closest('[data-queue]'); drafts.set(f.dataset.queue + f.dataset.target,e.target.value); }
  });
  document.addEventListener('submit', async e => {
    const f = e.target.closest('[data-queue]'); if (!f) return; e.preventDefault();
    if (!f.reportValidity() || saving || state.context.snapshot) return;
    const action = f.dataset.queue, body = {run:f.dataset.target, revision:state.queue_revision};
    if (action === 'release') body.note = f.querySelector('textarea').value;
    body.operation_id = operation(action,body);
    const button = f.querySelector('button'), message = f.querySelector('[role=status]'); button.disabled = true; saving = true;
    try {
      const response = await fetch('/api/queue/' + action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const data = await response.json();
      if (!response.ok) { const error = new Error(data.error || 'Request failed.'); error.conflict = response.status === 409; throw error; }
      state.queue_revision = data.revision; messages.delete(action + body.run); drafts.delete(action + body.run); document.activeElement?.blur(); window.dispatchEvent(new Event('workflowchange'));
    } catch(error) { messages.set(action + body.run,error.message); message.textContent = error.message; if (error.conflict) window.dispatchEvent(new Event('workflowchange')); }
    finally { button.disabled = false; saving = false; document.querySelectorAll("[data-queue] button").forEach(b => { b.disabled = false; }); }
  });
  return {paintHome,reviewCard,operation};
})();
