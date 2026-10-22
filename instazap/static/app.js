'use strict';
const $ = (id) => document.getElementById(id);
let csrf = '', busy = false, signature = '', failures = 0;
// Responses from requests started before the latest mutation are stale and never rendered.
let epoch = 0, requested = 0, applied = 0;
const states = {receiving: 'Recebendo arquivo', queued: 'Na fila', processing: 'Preparando as partes', ready: 'Pronto para o Status', error: 'Não foi possível preparar', awaiting_cut: 'Aguardando você escolher o ponto de corte'};
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, {signal:AbortSignal.timeout(15000), ...options, headers: {'X-CSRF-Token': csrf, ...options.headers}}); }
  catch { throw new Error('Conexão interrompida ou tempo limite excedido. Tente novamente.'); }
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || 'Falha de conexão. Tente novamente.'); }
  return response.status === 204 ? null : response.json();
}
async function start(secret = '') {
  try {
    const session = await api('/api/session', {headers: {'X-Access-Secret': secret}});
    csrf = session.csrf;
    $('access-form').hidden = true; $('input-area').hidden = false;
    error(); restoreMode(); await refresh();
  } catch (e) {
    $('access-form').hidden = false; $('input-area').hidden = true;
    error(e.message);
  }
}
$('access-form').addEventListener('submit', (e) => { e.preventDefault(); start($('access').value); $('access').value = ''; });
function node(tag, text, cls) { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; }
function render(jobs) {
  syncCutter(jobs);
  const next = JSON.stringify(jobs);
  if (signature === next) return; // Keep playing previews intact while polling.
  signature = next;
  $('empty').hidden = jobs.length > 0;
  $('job-count').textContent = `${jobs.length} vídeo${jobs.length === 1 ? '' : 's'}`;
  const cards = jobs.map((job) => {
    const existing = Array.from($('jobs').children).find(card => card.dataset.id === job.id);
    if (existing && existing.dataset.signature === JSON.stringify(job)) return existing;
    const card = node('article', '', 'job'); card.dataset.id = job.id; card.dataset.signature = JSON.stringify(job);
    card.setAttribute('aria-busy', ['receiving', 'queued', 'processing'].includes(job.state));
    const head = node('div', '', 'job-top');
    head.append(node('h3', `Vídeo de ${new Date(job.created * 1000).toLocaleString('pt-BR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}`));
    const remove = node('button', 'Excluir agora', 'delete');
    remove.addEventListener('click', async () => { remove.disabled = true; try { await api(`/api/jobs/${job.id}`, {method:'DELETE'}); await mutated(); } catch (e) { error(e.message); remove.disabled = false; } });
    head.append(remove); card.append(head);
    const preparing = job.mode === 'manual' && job.cut_start === null && ['queued', 'processing'].includes(job.state);
    const label = preparing ? 'Carregando o vídeo para você escolher o corte' : states[job.state];
    const status = node('p', job.error || `${label}${job.state === 'processing' && !preparing ? ` · ${job.progress}%` : ''}`, job.error ? 'error' : 'job-status'); status.setAttribute('role', 'status'); card.append(status);
    if (job.mode === 'manual' && job.cut_start !== null) card.append(node('p', `Corte em ${clock(job.cut_start)} · somente dali até o fim.`, 'job-status'));
    if (job.mode === 'manual' && job.state === 'ready' && job.parts.some((part) => part.duration < 59.9)) {
      const total = job.parts.reduce((sum, part) => sum + part.duration, 0);
      card.append(node('p', job.parts.length === 1
        ? `Parte com menos de 60 s: restavam só ${decimal(total)} s depois do corte.`
        : `Partes com menos de 60 s: ${decimal(total)} s não cabem em uma parte de até 90 s, e ${job.parts.length} partes iguais ficam abaixo de 60 s.`, 'job-status'));
    }
    if (['queued', 'processing'].includes(job.state) && !preparing) { const progress = node('progress'); progress.max = 100; progress.value = job.progress; progress.setAttribute('aria-label', 'Progresso do processamento'); card.append(progress); }
    if (job.state === 'ready') {
      const parts = node('div', '', 'parts');
      job.parts.forEach((part, i) => {
        const panel = node('div', '', 'part');
        const url = `/api/jobs/${job.id}/files/${encodeURIComponent(part.name)}`;
        const video = node('video'); video.controls = true; video.playsInline = true; video.preload = 'none'; video.src = `${url}?preview=1`; video.setAttribute('aria-label', `Prévia da parte ${i + 1}`);
        panel.append(video, node('p', `Parte ${i + 1} de ${job.parts.length} · ${part.duration.toFixed(1)} s`));
        const link = node('a', `Baixar parte ${i + 1}`, 'download'); link.href = url; link.download = part.name; panel.append(link); parts.append(panel);
      });
      card.append(parts);
      const zip = node('a', '↓ Baixar todas em ZIP', 'zip'); zip.href = `/api/jobs/${job.id}/files/partes.zip`; zip.download = 'partes.zip'; card.append(zip);
    }
    card.append(node('p', `Disponível até ${new Date(job.expires * 1000).toLocaleString('pt-BR')}.`, 'expires'));
    return card;
  });
  for (const card of Array.from($('jobs').children)) if (!cards.includes(card)) card.remove();
  cards.forEach((card, index) => { if ($('jobs').children[index] !== card) $('jobs').insertBefore(card, $('jobs').children[index] || null); });
}
async function refresh() {
  if (!csrf) return;
  const mine = ++requested, started = epoch;
  try {
    const jobs = await api('/api/jobs');
    if (mine < applied || started !== epoch) return;
    applied = mine; render(jobs); failures = 0;
  } catch (e) { if (++failures === 3) error(e.message); }
}
async function mutated() { epoch++; await refresh(); }
$('video-file').addEventListener('change', () => {
  const file = $('video-file').files[0];
  $('file-label').textContent = file ? `${file.name} · ${(file.size / 1000000).toFixed(1)} MB` : 'MP4, MOV ou outro formato de vídeo';
  $('upload-button').disabled = !file || busy;
});
function firstPart() { return Number(document.querySelector('input[name="first-part"]:checked').value); }
function mode() { return document.querySelector('input[name="mode"]:checked').value; }
function applyMode() {
  const manual = mode() === 'manual';
  $('first-part-control').hidden = manual; $('manual-hint').hidden = !manual;
  const replacing = manual && cutter.id && cutter.state !== 'cutting';
  const link = $('link-form').querySelector('button');
  link.textContent = manual ? (replacing ? 'Trocar vídeo →' : 'Carregar vídeo →') : 'Preparar →';
  link.setAttribute('aria-label', manual ? 'Carregar vídeo pelo link para escolher o corte' : 'Preparar vídeo pelo link');
  $('upload-button').firstChild.textContent = manual ? 'Carregar vídeo para escolher o corte ' : 'Dividir meu vídeo ';
  try { sessionStorage.setItem('instazap-mode', mode()); } catch { /* Private mode: choice is not restored. */ }
}
function restoreMode() {
  let saved = null;
  try { saved = sessionStorage.getItem('instazap-mode'); } catch { /* Ignore. */ }
  const radio = document.querySelector(`input[name="mode"][value="${saved === 'manual' ? 'manual' : 'full'}"]`);
  radio.checked = true; applyMode();
}
for (const radio of document.querySelectorAll('input[name="mode"]')) radio.addEventListener('change', applyMode);
function disable(value) {
  busy = value;
  $('input-area').setAttribute('aria-busy', String(value));
  for (const control of document.querySelectorAll('#input-area button, #input-area input')) control.disabled = value;
  if (!value) $('upload-button').disabled = !$('video-file').files[0];
}
function beginSubmission(kind) {
  error(); disable(true);
  $('submission-message').textContent = `Aguarde… recebendo ${kind === 'link' ? 'o link' : 'o arquivo'}.`;
  $('submission-status').hidden = false;
  $('upload-progress').hidden = kind !== 'upload'; $('upload-progress').value = 0;
  $('submission-status').focus({preventScroll:true});
  $('submission-status').scrollIntoView({block:'center', behavior:'instant'});
}
function endSubmission() {
  disable(false); $('submission-status').hidden = true; $('upload-progress').hidden = true;
}
async function accepted(id, manual) {
  $('submission-message').textContent = manual ? 'Recebido… carregando o vídeo para você assistir.' : 'Na fila… aguardando o processamento.';
  if (manual) showCutter(id, 'queued', null, true);
  // Keep feedback visible until the first queue listing settles.
  epoch++;
  const mine = ++requested;
  try { const jobs = await api('/api/jobs'); if (mine >= applied) { applied = mine; render(jobs); } }
  catch (e) { error('Vídeo recebido. Não foi possível atualizar a fila. A página tentará novamente.'); }
}
$('upload-form').addEventListener('submit', (e) => {
  e.preventDefault(); if (busy) return;
  const file = $('video-file').files[0]; if (!file) return;
  if (file.size > 200000000) { error('O arquivo ultrapassa 200 MB. Envie um arquivo menor.'); return; }
  const duration = firstPart(), manual = mode() === 'manual'; beginSubmission('upload');
  const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/jobs/upload'); xhr.setRequestHeader('X-CSRF-Token', csrf);
  xhr.setRequestHeader('X-First-Part-Seconds', String(duration)); xhr.timeout = 125000;
  if (manual) xhr.setRequestHeader('X-Mode', 'manual');
  xhr.upload.onprogress = (event) => { if (event.lengthComputable) $('upload-progress').value = event.loaded / event.total * 100; };
  xhr.onload = async () => {
    try {
      if (xhr.status === 202) {
        $('video-file').value = ''; $('video-file').dispatchEvent(new Event('change'));
        await accepted(JSON.parse(xhr.responseText).id, manual);
      } else {
        let message = 'O envio falhou. Tente novamente.';
        try { message = JSON.parse(xhr.responseText).detail || message; } catch { /* Use fallback. */ }
        error(message);
      }
    } finally { endSubmission(); }
  };
  xhr.onerror = xhr.ontimeout = xhr.onabort = () => { error('Conexão interrompida. Envie o arquivo novamente.'); endSubmission(); };
  try { xhr.send(file); } catch { error('O envio falhou. Tente novamente.'); endSubmission(); }
});
$('link-form').addEventListener('submit', async (e) => {
  e.preventDefault(); if (busy) return;
  const manual = mode() === 'manual';
  const body = JSON.stringify(manual ? {url:$('url').value.trim(), mode:'manual'} : {url:$('url').value.trim(), first_part_seconds:firstPart()});
  const replaced = manual && cutter.id && cutter.state !== 'cutting' ? cutter.id : null;
  beginSubmission('link');
  try {
    if (replaced) {
      // Explicit "Trocar vídeo": discard the waiting preview, then load the new link.
      $('submission-message').textContent = 'Aguarde… descartando o vídeo anterior.';
      await api(`/api/jobs/${replaced}`, {method:'DELETE'});
      forgetCut(replaced); hideCutter(); epoch++;
      $('submission-message').textContent = 'Aguarde… recebendo o link.';
    }
    const job = await api('/api/jobs/link', {method:'POST',headers:{'Content-Type':'application/json'}, body, signal:AbortSignal.timeout(125000)});
    $('url').value = ''; await accepted(job.id, manual);
  } catch (e) { error(e.message); if (replaced) await mutated(); } finally { endSubmission(); applyMode(); }
});

/* Manual cut: one persistent player. Polling updates text only; src changes only with the job. */
const FRAME = 1 / 30;
const cutter = {id: null, state: null, duration: 0, position: 0, dragging: false, loaded: false};
const video = $('cutter-video'), range = $('cut-range');
function clock(value) {
  const ms = Math.max(0, Math.round(value * 1000)), m = Math.floor(ms / 60000), rest = ms % 60000;
  return `${m}:${String(Math.floor(rest / 1000)).padStart(2, '0')},${String(rest % 1000).padStart(3, '0')}`;
}
function decimal(value, digits = 1) { return value.toLocaleString('pt-BR', {minimumFractionDigits:digits, maximumFractionDigits:digits}); }
function storageKey(id) { return `instazap-cut-${id}`; }
function forgetCut(id) { try { sessionStorage.removeItem(storageKey(id)); } catch { /* Ignore. */ } }
function maxCut() { return Math.max(0, cutter.duration - 0.001); }
// Mirrors the server: microsecond cut, down to the 30 fps grid, fewest equal parts of at most 90 s.
function plan(cut, duration) {
  const frames = Math.floor(Math.round(cut * 1e6) * 3 / 100000);
  const remainder = duration - frames / 30;
  const count = Math.max(1, Math.ceil((remainder - 1e-7) / 90));
  return {remainder, count, each: remainder / count};
}
function planText(cut) {
  const {remainder, count, each} = plan(cut, cutter.duration);
  const rest = `${decimal(remainder)} s`;
  if (count === 1 && remainder < 60) return `Restam ${rest} depois deste ponto: sai 1 parte, mais curta que 60 s, porque não há mais conteúdo. Nada é descartado.`;
  if (count === 1) return `Restam ${rest} depois deste ponto: sai 1 parte de ${rest}.`;
  if (each < 60) return `Restam ${rest}: numa parte só passaria do limite de 90 s, então saem ${count} partes iguais de ${decimal(each)} s (menos de 60 s cada). Nada é descartado.`;
  return `Restam ${rest}: saem ${count} partes iguais de cerca de ${decimal(each)} s (entre 60 e 90 s).`;
}
function setPosition(value, {seek = true, save = true} = {}) {
  if (!Number.isFinite(value)) return;
  cutter.position = Math.min(Math.max(0, value), maxCut());
  range.value = String(cutter.position);
  const label = clock(cutter.position);
  $('cut-time').textContent = label;
  range.setAttribute('aria-valuetext', `${label} de ${clock(cutter.duration)}`);
  $('cut-button').textContent = `Gerar partes a partir de ${label}`;
  $('cut-plan').textContent = planText(cutter.position);
  if (seek && cutter.loaded && Math.abs(video.currentTime - cutter.position) > 1e-4) video.currentTime = cutter.position;
  if (save) { try { sessionStorage.setItem(storageKey(cutter.id), String(cutter.position)); } catch { /* Ignore. */ } }
}
function cutterStatus(message, spinning) {
  $('cutter-message').textContent = message; $('cutter-spinner').hidden = !spinning;
  $('cutter-status').hidden = !message;
}
function cutControls(enabled) {
  for (const control of $('cutter-stage').querySelectorAll('button, input')) control.disabled = !enabled;
  $('cutter').setAttribute('aria-busy', String(!enabled));
}
function hideCutter() {
  cutter.id = null; cutter.state = null; cutter.loaded = false;
  video.removeAttribute('src'); video.load();
  $('cutter').hidden = true; $('cutter-stage').hidden = true; $('cutter-error').hidden = true;
  applyMode();
}
function showCutter(id, state, duration, announce) {
  if (cutter.id !== id) {
    hideCutter(); cutter.id = id; cutter.position = 0;
    $('cutter').hidden = false;
    if (announce) $('cutter').scrollIntoView({block:'start', behavior:'instant'});
  }
  if (cutter.state === 'cutting') return; // Never re-enable controls while a cut request is pending.
  cutter.state = state;
  if (state !== 'awaiting_cut') {
    $('cutter-stage').hidden = true;
    cutterStatus('Baixando e medindo o vídeo com segurança… Isso pode levar alguns minutos. Nada será cortado ainda.', true);
  } else if ($('cutter-stage').hidden) {
    cutter.duration = duration;
    range.max = String(maxCut());
    $('cut-total').textContent = ` de ${clock(duration)}`;
    let saved = 0;
    try { saved = Number(sessionStorage.getItem(storageKey(id))) || 0; } catch { /* Ignore. */ }
    setPosition(saved, {seek:false, save:false});
    cutterStatus('Carregando a prévia…', true);
    $('cutter-stage').hidden = false; cutControls(true);
    video.src = `/api/jobs/${id}/source`;
  }
  applyMode();
}
function syncCutter(jobs) {
  if (cutter.state === 'cutting') return;
  const job = jobs.find((j) => j.mode === 'manual' && (j.state === 'awaiting_cut' ||
    (j.cut_start === null && ['queued', 'processing'].includes(j.state))));
  if (job) showCutter(job.id, job.state, job.source_duration, false);
  else if (cutter.id) { forgetCut(cutter.id); hideCutter(); }
}
video.addEventListener('loadedmetadata', () => {
  cutter.loaded = true; $('cutter-error').hidden = true;
  cutterStatus('Prévia pronta. Escolha o ponto e toque em “Gerar partes”.', false);
  video.currentTime = cutter.position;
});
video.addEventListener('error', () => {
  if (!cutter.id || !video.getAttribute('src')) return;
  cutterStatus('', false);
  $('cutter-error').textContent = 'Não foi possível reproduzir a prévia neste navegador. Você ainda pode digitar o tempo do corte, ou descartar e usar “Dividir o vídeo inteiro”.';
  $('cutter-error').hidden = false;
});
for (const name of ['timeupdate', 'seeked']) {
  video.addEventListener(name, () => { if (!cutter.dragging && cutter.state === 'awaiting_cut') setPosition(video.currentTime, {seek:false}); });
}
range.addEventListener('pointerdown', () => { cutter.dragging = true; video.pause(); });
for (const name of ['pointerup', 'pointercancel', 'change']) range.addEventListener(name, () => { cutter.dragging = false; });
range.addEventListener('input', () => { video.pause(); setPosition(Number(range.value)); });
range.addEventListener('keydown', (e) => {
  const steps = {ArrowLeft:-FRAME, ArrowDown:-FRAME, ArrowRight:FRAME, ArrowUp:FRAME, PageDown:-1, PageUp:1};
  let next = null;
  if (e.key in steps) next = cutter.position + steps[e.key] * (e.shiftKey && Math.abs(steps[e.key]) < 1 ? 30 : 1);
  if (e.key === 'Home') next = 0;
  if (e.key === 'End') next = maxCut();
  if (next === null) return;
  e.preventDefault(); video.pause(); setPosition(next);
});
for (const button of $('cutter-stage').querySelectorAll('.nudges button')) {
  button.addEventListener('click', () => {
    video.pause();
    setPosition(cutter.position + (Number(button.dataset.seconds) || Number(button.dataset.frames) * FRAME));
  });
}
function parseTime(text) {
  const match = /^\s*(?:(\d{1,2}):)?(\d{1,3}(?:[.,]\d{1,6})?)\s*$/.exec(text);
  if (!match) return NaN;
  return Number(match[1] || 0) * 60 + Number(match[2].replace(',', '.'));
}
$('cut-goto').addEventListener('submit', (e) => {
  e.preventDefault();
  const value = parseTime($('cut-input').value);
  if (!(value >= 0 && value < cutter.duration)) {
    $('cutter-error').textContent = `Digite um tempo entre 0:00,000 e ${clock(maxCut())}.`; $('cutter-error').hidden = false; return;
  }
  $('cutter-error').hidden = true; video.pause(); setPosition(value); $('cut-input').value = '';
});
$('cut-button').addEventListener('click', async () => {
  if (cutter.state !== 'awaiting_cut') return;
  const id = cutter.id;
  video.pause();
  const start = cutter.position;
  cutter.state = 'cutting'; cutControls(false); $('cutter-error').hidden = true;
  cutterStatus(`Enviando o corte em ${clock(start)}…`, true);
  try {
    const job = await api(`/api/jobs/${id}/cut`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({start})});
    forgetCut(id); hideCutter();
    signature = ''; epoch++;
    await refresh();
    const card = Array.from($('jobs').children).find((c) => c.dataset.id === job.id);
    if (card) { card.tabIndex = -1; card.scrollIntoView({block:'start', behavior:'instant'}); card.focus({preventScroll:true}); }
  } catch (e) {
    if (cutter.id !== id) return;
    cutter.state = 'awaiting_cut'; cutControls(true);
    cutterStatus('', false);
    $('cutter-error').textContent = e.message; $('cutter-error').hidden = false;
    await mutated();
  }
});
$('cutter-discard').addEventListener('click', async () => {
  const id = cutter.id; if (!id) return;
  $('cutter-discard').disabled = true;
  try { await api(`/api/jobs/${id}`, {method:'DELETE'}); forgetCut(id); hideCutter(); await mutated(); }
  catch (e) { $('cutter-error').textContent = e.message; $('cutter-error').hidden = false; }
  finally { $('cutter-discard').disabled = false; }
});
start(); setInterval(refresh, 2000);
