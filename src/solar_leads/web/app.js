'use strict';
/* Solar Leads - roteador (SPA com hash) */

function rotaAtual() {
  const partes = (location.hash || '#/').split('?');
  const hash = partes[0], qs = partes.slice(1).join('?');
  let m;
  if ((m = /^#\/lista\/([^/]+)(?:\/(painel))?\/?$/.exec(hash))) return { nome: 'listas', tela: 'lista', id: decodeURIComponent(m[1]), sub: m[2], qs };
  if ((m = /^#\/pasta\/([^/]+)(?:\/(resultados))?\/?$/.exec(hash))) return { nome: 'pastas', tela: 'pasta', id: decodeURIComponent(m[1]), sub: m[2], qs };
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
  const telas = { inicio: telaInicio, listas: telaListas, lista: (el) => telaLista(el, r.id, r.sub, r.qs), pastas: telaPastas, pasta: (el) => telaPasta(el, r.id, r.sub, r.qs), whatsapp: telaWhatsapp, base: telaBase };
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
  // Botão de olho: borra nomes, telefones e demais dados pessoais (efeito de vidro) e lembra a escolha.
  const olho = document.getElementById('btn-olho');
  const desenharOlho = () => {
    const borrado = document.documentElement.hasAttribute('data-demo');
    olho.replaceChildren(icone(borrado ? 'olhofechado' : 'olho', 18));
    olho.setAttribute('aria-pressed', String(borrado));
    olho.title = borrado ? 'Mostrar dados sensíveis' : 'Borrar dados sensíveis';
    olho.setAttribute('aria-label', olho.title);
  };
  olho.addEventListener('click', () => {
    const borrar = !document.documentElement.hasAttribute('data-demo');
    document.documentElement.toggleAttribute('data-demo', borrar);
    try { localStorage.setItem('solar-privado', borrar ? '1' : '0'); } catch (e) { /* sem armazenamento: vale só nesta visita */ }
    desenharOlho();
  });
  desenharOlho();
  document.getElementById('btn-tema').addEventListener('click', () => { definirTema(temaAtual() === 'escuro' ? 'claro' : 'escuro'); desenharBotaoTema(); });
  document.querySelectorAll('#nav a').forEach((a) => a.querySelector('.nav-ic').append(icone(a.dataset.icone, 22)));
  document.getElementById('pular').addEventListener('click', (e) => { e.preventDefault(); document.getElementById('conteudo').focus(); });
  window.addEventListener('hashchange', rotear);
  rotear();
}
iniciar();
