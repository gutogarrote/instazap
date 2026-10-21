'use strict';
const $ = (id) => document.getElementById(id);
let csrf = '', busy = false, signature = '', failures = 0;
const states = {receiving: 'Recebendo arquivo', queued: 'Na fila', processing: 'Preparando as partes', ready: 'Pronto para o Status', error: 'Não foi possível preparar'};
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-CSRF-Token': csrf, ...options.headers}});
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || 'Falha de conexão. Tente novamente.'); }
  return response.status === 204 ? null : response.json();
}
async function start(secret = '') {
  try {
    const session = await api('/api/session', {headers: {'X-Access-Secret': secret}});
    csrf = session.csrf;
    $('access-form').hidden = true; $('input-area').hidden = false;
    error(); await refresh();
  } catch (e) {
    $('access-form').hidden = false; $('input-area').hidden = true;
    error(e.message);
  }
}
$('access-form').addEventListener('submit', (e) => { e.preventDefault(); start($('access').value); $('access').value = ''; });
function node(tag, text, cls) { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; }
function render(jobs) {
  const next = JSON.stringify(jobs);
  if (signature === next) return; // Keep playing previews intact while polling.
  signature = next;
  $('empty').hidden = jobs.length > 0;
  $('job-count').textContent = `${jobs.length} vídeo${jobs.length === 1 ? '' : 's'}`;
  const cards = jobs.map((job) => {
    const card = node('article', '', 'job'); card.dataset.id = job.id;
    const head = node('div', '', 'job-top');
    head.append(node('h3', `Vídeo de ${new Date(job.created * 1000).toLocaleString('pt-BR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}`));
    const remove = node('button', 'Excluir agora', 'delete');
    remove.addEventListener('click', async () => { remove.disabled = true; try { await api(`/api/jobs/${job.id}`, {method:'DELETE'}); await refresh(); } catch (e) { error(e.message); remove.disabled = false; } });
    head.append(remove); card.append(head);
    const status = node('p', job.error || `${states[job.state]}${job.state === 'processing' ? ` · ${job.progress}%` : ''}`, job.error ? 'error' : 'job-status'); status.setAttribute('role', 'status'); card.append(status);
    if (['queued', 'processing'].includes(job.state)) { const progress = node('progress'); progress.max = 100; progress.value = job.progress; progress.setAttribute('aria-label', 'Progresso do processamento'); card.append(progress); }
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
  $('jobs').replaceChildren(...cards);
}
async function refresh() { if (!csrf) return; try { render(await api('/api/jobs')); failures = 0; } catch (e) { if (++failures === 3) error(e.message); } }
$('video-file').addEventListener('change', () => {
  const file = $('video-file').files[0];
  $('file-label').textContent = file ? `${file.name} · ${(file.size / 1000000).toFixed(1)} MB` : 'MP4, MOV ou outro formato de vídeo';
  $('upload-button').disabled = !file || busy;
});
function disable(value) { busy = value; for (const b of document.querySelectorAll('#input-area button')) b.disabled = value; if (!value) $('upload-button').disabled = !$('video-file').files[0]; }
$('upload-form').addEventListener('submit', (e) => {
  e.preventDefault(); if (busy) return; error();
  const file = $('video-file').files[0]; if (!file) return;
  if (file.size > 200000000) { error('O arquivo ultrapassa 200 MB. Envie um arquivo menor.'); return; }
  disable(true); $('upload-status').hidden = false; $('upload-progress').value = 0; $('upload-message').textContent = 'Enviando vídeo… mantenha esta página aberta durante o envio.';
  const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/jobs/upload'); xhr.setRequestHeader('X-CSRF-Token', csrf); xhr.timeout = 125000;
  xhr.upload.onprogress = (event) => { if (event.lengthComputable) $('upload-progress').value = event.loaded / event.total * 100; };
  xhr.onload = async () => {
    if (xhr.status === 202) { $('video-file').value = ''; $('video-file').dispatchEvent(new Event('change')); error(); await refresh(); $('results-title').scrollIntoView({behavior:'smooth',block:'start'}); }
    else { try { error(JSON.parse(xhr.responseText).detail); } catch { error('O envio falhou. Tente novamente.'); } }
  };
  xhr.onerror = xhr.ontimeout = () => error('Conexão interrompida. Envie o arquivo novamente.');
  xhr.onloadend = () => { disable(false); $('upload-status').hidden = true; };
  xhr.send(file);
});
$('link-form').addEventListener('submit', async (e) => {
  e.preventDefault(); if (busy) return; error(); disable(true);
  try { await api('/api/jobs/link', {method:'POST',headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:$('url').value.trim()})}); $('url').value = ''; await refresh(); }
  catch (e) { error(e.message); } finally { disable(false); }
});
start(); setInterval(refresh, 2000);
