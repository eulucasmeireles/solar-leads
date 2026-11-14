// Modo apresentação: ?demo=1 desfoca dados pessoais (ver style.css).
if (new URLSearchParams(location.search).get('demo') === '1') document.documentElement.setAttribute('data-demo', '');
'use strict';
/* Solar Leads - roteador (SPA com hash) */

function rotaAtual() {
  const hash = (location.hash || '#/').split('?')[0];
  let m;
  if ((m = /^#\/lista\/([^/]+)(?:\/(painel))?\/?$/.exec(hash))) return { nome: 'listas', tela: 'lista', id: decodeURIComponent(m[1]), sub: m[2] };
  if ((m = /^#\/pasta\/([^/]+)\/?$/.exec(hash))) return { nome: 'pastas', tela: 'pasta', id: decodeURIComponent(m[1]) };
  if (hash === '#/pastas') return { nome: 'pastas', tela: 'pastas' };
  if (hash === '#/listas') return { nome: 'listas', tela: 'listas' };
  if (hash === '#/whatsapp') return { nome: 'whatsapp', tela: 'whatsapp' };
  if (hash === '#/base') return { nome: 'base', tela: 'base' };
  return { nome: 'inicio', tela: 'inicio' };
}

function marcarNavegacao(nome) {
  document.querySelectorAll('#nav a').forEach((a) => {
    if (a.dataset.rota === nome) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

function rotear() {
  sairDaTela();
  document.getElementById('modais').replaceChildren();
  document.getElementById('casca').inert = false;
  document.body.classList.remove('sem-rolagem');
  app.rota++;
  const r = rotaAtual();
  const raiz = document.getElementById('conteudo');
  raiz.replaceChildren();
  raiz.className = 'conteudo tela-' + r.tela;
  marcarNavegacao(r.nome);
  const telas = { inicio: telaInicio, listas: telaListas, lista: (el) => telaLista(el, r.id, r.sub), pastas: telaPastas, pasta: (el) => telaPasta(el, r.id), whatsapp: telaWhatsapp, base: telaBase };
  telas[r.tela](raiz);
  window.scrollTo(0, 0);
}

function desenharBotaoTema() {
  const b = document.getElementById('btn-tema');
  const escuro = temaAtual() === 'escuro';
  b.replaceChildren(icone(escuro ? 'sol' : 'lua', 18));
  b.title = escuro ? 'Usar tema claro' : 'Usar tema escuro';
  b.setAttribute('aria-pressed', String(escuro));
}

function iniciar() {
  document.getElementById('marca-ic').append(icone('sol', 22));
  desenharBotaoTema();
  // Com senha configurada, mostra "Sair".
  fetch('/api/sessao').then((r) => r.json()).then((s) => {
    const b = document.getElementById('btn-sair');
    if (!s.protegido) return;
    b.hidden = false;
    b.addEventListener('click', async () => { try { await fetch('/api/logout', { method: 'POST' }); } finally { location.href = '/login'; } });
  }).catch(() => { /* sem senha configurada */ });
  document.getElementById('btn-tema').addEventListener('click', () => { definirTema(temaAtual() === 'escuro' ? 'claro' : 'escuro'); desenharBotaoTema(); });
  document.querySelectorAll('#nav a').forEach((a) => a.querySelector('.nav-ic').append(icone(a.dataset.icone, 22)));
  document.getElementById('pular').addEventListener('click', (e) => { e.preventDefault(); document.getElementById('conteudo').focus(); });
  window.addEventListener('hashchange', rotear);
  rotear();
}
iniciar();
