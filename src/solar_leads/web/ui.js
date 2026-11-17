'use strict';
/* Garimpo - componentes de interface reutilizáveis */

/* ---------- avisos (toast) ---------- */
function avisar(texto, tipo, link) {
  const box = document.getElementById('avisos');
  const el = h('div', { class: 'aviso' + (tipo ? ' aviso-' + tipo : '') },
    icone(tipo === 'erro' ? 'alerta' : 'check', 18), h('span', { text: texto }),
    link ? h('a', { class: 'aviso-link', href: link.href, text: link.rotulo }) : null);
  box.append(el);
  setTimeout(() => { el.classList.add('saindo'); setTimeout(() => el.remove(), 260); }, link ? 7000 : 4200);
}

/* ---------- selos suaves ---------- */
function selo(texto, tom, opcoes = {}) {
  return h('span', { class: `selo selo-${tom || 'cinza'}`, title: opcoes.titulo },
    opcoes.ponto ? h('i', { class: 'ponto', 'aria-hidden': 'true' }) : null,
    opcoes.icone ? icone(opcoes.icone, 14) : null, texto);
}
function seloFaixa(f, longo) {
  const tom = f === 'A' ? 'verde' : f === 'B' ? 'laranja' : 'cinza';
  const nome = FAIXA_ROTULO[f] || 'Sem faixa';
  const el = h('span', { class: `selo selo-${tom} selo-faixa`, title: nome, 'aria-label': nome }, longo ? nome : (f || '—'));
  return el;
}
function seloStatus(status) {
  const mapa = { na_fila: ['Na fila', 'cinza'], gerando: ['Gerando', 'laranja'], pronta: ['Pronta', 'verde'], erro: ['Com erro', 'vermelho'] };
  const [t, tom] = mapa[status] || [status, 'cinza'];
  return selo(t, tom, { ponto: true });
}

function avatar(l, grande) {
  const nome = nomeCompleto(l);
  return h('span', { class: `avatar av-${pastelDe(nome || l.empresa)}` + (grande ? ' avatar-g' : ''), 'aria-hidden': 'true', text: iniciais(l) });
}

function esqueleto(larg, alt, classe) {
  return h('span', { class: 'sk ' + (classe || ''), style: { width: larg || '100%', height: alt || '1em' }, 'aria-hidden': 'true' });
}

function estadoVazio(titulo, texto, acao, nomeIcone) {
  return h('div', { class: 'vazio' },
    h('span', { class: 'vazio-ic' }, icone(nomeIcone || 'busca', 26)),
    h('h3', { text: titulo }), texto ? h('p', { text: texto }) : null, acao || null);
}
function estadoErro(msg, aoTentar) {
  return h('div', { class: 'vazio vazio-erro', role: 'alert' },
    h('span', { class: 'vazio-ic' }, icone('alerta', 26)),
    h('h3', { text: 'Não foi possível carregar' }), h('p', { text: msg }),
    aoTentar ? h('button', { class: 'btn btn-sec', type: 'button', onclick: aoTentar }, icone('atualizar', 18), 'Tentar de novo') : null);
}

let _uid = 0;
const uid = (p) => `${p || 'id'}-${++_uid}`;

/* ---------- interruptor estilo iOS ---------- */
function interruptor({ rotulo, descricao, checked, disabled, aoMudar }) {
  const id = uid('sw');
  const input = h('input', { type: 'checkbox', role: 'switch', id, disabled: !!disabled, 'aria-describedby': descricao ? id + '-d' : null });
  input.checked = !!checked;
  input.addEventListener('change', () => aoMudar && aoMudar(input.checked));
  const el = h('label', { class: 'interruptor' + (disabled ? ' desativado' : ''), for: id },
    h('span', { class: 'int-tx' }, h('span', { class: 'int-rot', text: rotulo }), descricao ? h('span', { class: 'int-desc', id: id + '-d', text: descricao }) : null),
    input, h('span', { class: 'trilho', 'aria-hidden': 'true' }, h('i', {})));
  return { el, input };
}

/* ---------- controle segmentado ---------- */
function segmentado({ opcoes, valor, rotulo, papel, aoMudar, classe }) {
  const tab = papel === 'tab';
  const el = h('div', { class: 'segmentado ' + (classe || ''), role: tab ? 'tablist' : 'radiogroup', 'aria-label': rotulo });
  const botoes = opcoes.map((o) => {
    const b = h('button', { type: 'button', class: 'seg-op', role: tab ? 'tab' : 'radio', id: o.id, 'aria-controls': o.controla, 'data-v': o.valor, onclick: () => definir(o.valor, true) }, o.rotulo);
    b.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault();
      const i = opcoes.findIndex((x) => x.valor === atual);
      const n = opcoes[(i + (e.key === 'ArrowRight' ? 1 : -1) + opcoes.length) % opcoes.length];
      definir(n.valor, true, true);
    });
    return b;
  });
  el.append(...botoes);
  let atual = null;
  function definir(v, notificar, foco) {
    atual = v;
    botoes.forEach((b) => { const on = b.dataset.v === v; b.setAttribute(tab ? 'aria-selected' : 'aria-checked', String(on)); b.tabIndex = on ? 0 : -1; if (on && foco) b.focus(); });
    if (notificar && aoMudar) aoMudar(v);
  }
  definir(valor, false);
  return { el, definir };
}

/* ---------- modal: folha central, lateral ou de baixo; foco preso e Esc ---------- */
const FOCAVEIS = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Modais abertos, do mais antigo ao mais novo: só o de cima responde ao teclado.
const PILHA_MODAIS = [];
function abrirModal(painel, { lateral, baixo, aoFechar } = {}) {
  const anterior = document.activeElement;
  const casca = document.getElementById('casca');
  const veu = h('div', { class: 'veu' + (lateral ? ' veu-lateral' : '') + (baixo ? ' veu-baixo' : '') }, painel);
  document.getElementById('modais').append(veu);
  casca.inert = true;
  document.body.classList.add('sem-rolagem');
  requestAnimationFrame(() => requestAnimationFrame(() => veu.classList.add('aberto')));
  let fechado = false;
  const eu = {};
  PILHA_MODAIS.push(eu);
  function soltar() {
    document.removeEventListener('keydown', tecla, true);
    const i = PILHA_MODAIS.indexOf(eu);
    if (i >= 0) PILHA_MODAIS.splice(i, 1);
  }
  // Se a tela mudar com o modal aberto, o teclado precisa ser devolvido (senão o Tab morre no app inteiro).
  app.limpar.push(() => { fechado = true; soltar(); });
  function fechar(valor) {
    if (fechado) return; fechado = true;
    veu.classList.remove('aberto');
    soltar();
    const fim = () => {
      veu.remove();
      if (!PILHA_MODAIS.length) { casca.inert = false; document.body.classList.remove('sem-rolagem'); }
      if (anterior && anterior.focus && document.contains(anterior)) anterior.focus();
      if (aoFechar) aoFechar(valor);
    };
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) fim(); else setTimeout(fim, 260);
  }
  function tecla(e) {
    if (PILHA_MODAIS[PILHA_MODAIS.length - 1] !== eu) return;
    if (e.key === 'Escape') {
      /* combo aberto: o Esc fecha só a lista de sugestões */
      if (e.target && e.target.getAttribute && e.target.getAttribute('aria-expanded') === 'true') return;
      e.stopPropagation(); fechar(); return;
    }
    if (e.key !== 'Tab') return;
    const fs = [...painel.querySelectorAll(FOCAVEIS)].filter((x) => x.offsetParent !== null);
    if (!fs.length) { e.preventDefault(); return; }
    const a = fs[0], z = fs[fs.length - 1];
    if (e.shiftKey && (document.activeElement === a || !painel.contains(document.activeElement))) { e.preventDefault(); z.focus(); }
    else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
  }
  document.addEventListener('keydown', tecla, true);
  veu.addEventListener('mousedown', (e) => { if (e.target === veu) fechar(); });
  setTimeout(() => {
    const alvo = painel.querySelector('[data-foco]') || painel.querySelector(FOCAVEIS) || painel;
    alvo.focus({ preventScroll: true });
  }, 40);
  return { fechar, veu };
}

function confirmar({ titulo, texto, ok, perigo }) {
  return new Promise((resolve) => {
    const idT = uid('dlg');
    let resposta = false;
    const painel = h('div', { class: 'dialogo', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': idT, 'aria-describedby': idT + '-d', tabindex: '-1' },
      h('h2', { id: idT, text: titulo }), h('p', { id: idT + '-d', text: texto }),
      h('div', { class: 'dialogo-acoes' },
        h('button', { class: 'btn btn-sec', type: 'button', 'data-foco': '', onclick: () => m.fechar() }, 'Cancelar'),
        h('button', { class: 'btn ' + (perigo ? 'btn-perigo' : 'btn-prim'), type: 'button', onclick: () => { resposta = true; m.fechar(); } }, ok || 'Confirmar')));
    const m = abrirModal(painel, { aoFechar: () => resolve(resposta) });
  });
}

/* ---------- menu de ações ---------- */
function criarMenu(conteudoBotao, itens, { classeBotao, rotulo, alinhar } = {}) {
  const id = uid('menu');
  const botao = h('button', { class: classeBotao || 'btn btn-sec', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': id, 'aria-label': rotulo }, conteudoBotao);
  const lista = h('div', { class: 'menu' + (alinhar === 'esq' ? ' menu-esq' : ''), id, role: 'menu', hidden: true });
  const caixa = h('div', { class: 'menu-caixa' }, botao, lista);
  const fechar = (foco) => { lista.hidden = true; botao.setAttribute('aria-expanded', 'false'); if (foco) botao.focus(); };
  const abrir = () => { lista.hidden = false; botao.setAttribute('aria-expanded', 'true'); const p = lista.querySelector('[role=menuitem]'); if (p) p.focus(); };
  itens.forEach((it) => {
    const b = h(it.href ? 'a' : 'button', { class: 'menu-item' + (it.perigo ? ' perigo' : ''), role: 'menuitem', type: it.href ? null : 'button', href: it.href, tabindex: '-1' },
      it.icone ? icone(it.icone, 18) : null, h('span', {}, h('span', { class: 'mi-t', text: it.rotulo }), it.nota ? h('span', { class: 'mi-n', text: it.nota }) : null));
    b.addEventListener('click', () => { fechar(); if (it.aoClicar) it.aoClicar(); });
    lista.append(b);
  });
  botao.addEventListener('click', () => (lista.hidden ? abrir() : fechar()));
  caixa.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !lista.hidden) { e.stopPropagation(); fechar(true); }
    if (lista.hidden && e.key === 'ArrowDown' && e.target === botao) { e.preventDefault(); abrir(); }
    if (!lista.hidden && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      const its = [...lista.querySelectorAll('[role=menuitem]')];
      const i = its.indexOf(document.activeElement);
      its[(i + (e.key === 'ArrowDown' ? 1 : -1) + its.length) % its.length].focus();
    }
  });
  const fora = (e) => { if (!caixa.contains(e.target)) fechar(); };
  document.addEventListener('mousedown', fora);
  aoSair(() => document.removeEventListener('mousedown', fora));
  return caixa;
}

/* ---------- campo de busca com autocompletar ---------- */
function criarCombo({ rotulo, placeholder, minimo, buscar, render, aoEscolher, classe, grande, vazio, inline }) {
  const id = uid('combo');
  const input = h('input', {
    type: 'text', id, role: 'combobox', 'aria-expanded': 'false', 'aria-controls': id + '-l', 'aria-autocomplete': 'list',
    autocomplete: 'off', spellcheck: 'false', placeholder, 'aria-label': rotulo,
  });
  const lista = h('ul', { class: 'combo-lista', id: id + '-l', role: 'listbox', hidden: true });
  const el = h('div', { class: 'combo ' + (grande ? 'combo-grande ' : '') + (inline ? 'combo-inline ' : '') + (classe || '') }, icone('busca', grande ? 22 : 18), input, lista);
  let itens = [], ativo = -1, ctl = null;
  const abrir = (v) => { lista.hidden = !v; input.setAttribute('aria-expanded', String(v)); if (!v) input.removeAttribute('aria-activedescendant'); };
  function marcar(i) {
    ativo = i;
    [...lista.children].forEach((li, k) => {
      const on = k === i; li.setAttribute('aria-selected', String(on));
      if (on) { input.setAttribute('aria-activedescendant', li.id); li.scrollIntoView({ block: 'nearest' }); }
    });
  }
  function info(txt, ocupado) { lista.replaceChildren(h('li', { class: 'combo-info', role: 'presentation' }, ocupado ? h('i', { class: 'giro', 'aria-hidden': 'true' }) : null, txt)); abrir(true); }
  function desenhar() {
    lista.replaceChildren(...itens.map((it, i) => {
      const li = h('li', { role: 'option', id: `${id}-o${i}`, 'aria-selected': 'false' }, render(it));
      li.addEventListener('mousedown', (e) => { e.preventDefault(); escolher(it); });
      li.addEventListener('mousemove', () => { if (ativo !== i) marcar(i); });
      return li;
    }));
    abrir(true); marcar(0);
  }
  function escolher(it) { abrir(false); input.value = ''; itens = []; aoEscolher(it); }
  async function atualizar() {
    const q = input.value.trim();
    if (ctl) ctl.abort();
    if (q.length < (minimo || 1)) { itens = []; if (q.length) info(`Digite pelo menos ${minimo} letras.`); else abrir(false); return; }
    ctl = new AbortController();
    info('Buscando…', true);
    try {
      itens = await buscar(q, ctl.signal);
      if (!itens.length) { info(vazio || 'Nada encontrado.'); return; }
      desenhar();
    } catch (e) { if (!ehAborto(e)) { itens = []; info(e.message); } }
  }
  input.addEventListener('input', debounce(atualizar, 220));
  input.addEventListener('blur', () => abrir(false));
  input.addEventListener('focus', () => { if (itens.length) abrir(true); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && itens.length) { e.preventDefault(); if (lista.hidden) abrir(true); marcar((ativo + 1) % itens.length); }
    else if (e.key === 'ArrowUp' && itens.length) { e.preventDefault(); marcar((ativo - 1 + itens.length) % itens.length); }
    else if (e.key === 'Enter' && !lista.hidden && itens[ativo]) { e.preventDefault(); escolher(itens[ativo]); }
    else if (e.key === 'Escape' && !lista.hidden) { e.stopPropagation(); abrir(false); }
  });
  return { el, input };
}

/* chip em pílula com remoção */
function chip(texto, aoRemover, rotuloRemover) {
  return h('span', { class: 'chip' }, h('span', { class: 'chip-tx', text: texto }),
    h('button', { class: 'chip-x', type: 'button', 'aria-label': rotuloRemover || `Remover ${texto}`, onclick: aoRemover }, icone('x', 14)));
}

/* barra de progresso fina, indeterminada ou com valor */
function progresso(valor, rotulo) {
  const el = h('div', { class: 'progresso' + (valor == null ? ' indet' : ''), role: 'progressbar', 'aria-label': rotulo || 'Andamento' }, h('i', { style: valor == null ? null : { width: Math.max(2, Math.min(100, valor)) + '%' } }));
  if (valor != null) { el.setAttribute('aria-valuemin', '0'); el.setAttribute('aria-valuemax', '100'); el.setAttribute('aria-valuenow', String(Math.round(valor))); }
  return el;
}

/* anel com número no centro (valor de 0 a 100) */
function anel({ valor, texto, cor, tam = 64, espessura = 6, rotulo }) {
  const r = (tam - espessura) / 2, c = 2 * Math.PI * r;
  const svg = s('svg', { viewBox: `0 0 ${tam} ${tam}`, width: tam, height: tam, class: 'anel-svg', role: 'img', 'aria-label': rotulo || String(texto) },
    s('circle', { cx: tam / 2, cy: tam / 2, r, fill: 'none', class: 'anel-fundo', 'stroke-width': espessura }),
    s('circle', { cx: tam / 2, cy: tam / 2, r, fill: 'none', class: 'anel-valor', 'stroke-width': espessura, 'stroke-linecap': 'round', style: `--c:${c.toFixed(2)};--d:${(c * Math.max(0, Math.min(100, valor)) / 100).toFixed(2)};stroke:var(${cor || '--acento'})`, transform: `rotate(-90 ${tam / 2} ${tam / 2})` }));
  return h('span', { class: 'anel', style: { width: tam + 'px', height: tam + 'px' } }, svg, h('span', { class: 'anel-tx', text: String(texto) }));
}

function cabecalhoPagina(titulo, sub, acoes) {
  return h('header', { class: 'pag-cab' }, h('div', {}, h('h1', { text: titulo }), sub ? h('p', { class: 'apoio', text: sub }) : null), acoes ? h('div', { class: 'pag-acoes' }, acoes) : null);
}

/* linha estilo Ajustes do iOS */
function ajuste({ icone: ic, tom, titulo, descricao, valor, acao }) {
  return h('div', { class: 'ajuste' },
    ic ? h('span', { class: `ajuste-ic tom-${tom || 'azul'}` }, icone(ic, 20)) : null,
    h('div', { class: 'ajuste-tx' }, h('div', { class: 'ajuste-t', text: titulo }), descricao ? h('div', { class: 'ajuste-d', text: descricao }) : null),
    valor != null ? h('div', { class: 'ajuste-v' }, valor) : null, acao || null);
}

/* ---------- escolha do perfil da lista (cartões de opção) ---------- */
const APOIO_PRONTOS = {
  solar: 'Mostra o potencial de energia solar da casa e da empresa de cada dono.',
  geral: 'Lista comum, sem nada de energia solar. A ordem é pela força do contato e pelo encaixe.',
};
function apoioDoPerfil(p) {
  if (APOIO_PRONTOS[p.id]) return APOIO_PRONTOS[p.id];
  const r = p.receita || {}, n = (r.atividades || []).length;
  return r.oferta || r.cliente_ideal || `${num(n)} ${n === 1 ? 'atividade' : 'atividades'}`;
}
function opcoesPerfil({ perfis, valor, aoMudar }) {
  const nome = uid('perfil');
  let atual = valor;
  const el = h('div', { class: 'opcoes-publico', role: 'radiogroup', 'aria-label': 'Perfil da lista' });
  perfis.forEach((p) => {
    const idc = uid('pf');
    const input = h('input', { type: 'radio', name: nome, id: idc, value: p.id });
    input.checked = p.id === valor;
    input.addEventListener('change', () => { if (!input.checked) return; atual = p.id; if (aoMudar) aoMudar(p); });
    el.append(h('label', { class: 'opcao-publico', for: idc }, input,
      h('span', { class: 'op-tx' }, h('span', { class: 'op-t', text: p.nome }), h('span', { class: 'op-d', text: apoioDoPerfil(p) }))));
  });
  return { el, valor: () => atual };
}
let perfisCache = null;
async function carregarPerfis(forcar) {
  if (!perfisCache || forcar) perfisCache = await api('/api/perfis');
  return perfisCache;
}

/* ---------- escolha do público da lista (dois cartões de opção) ---------- */
function opcoesPublico({ valor, aoMudar }) {
  const nome = uid('publico');
  let atual = valor;
  const el = h('div', { class: 'opcoes-publico', role: 'radiogroup', 'aria-label': 'Para que é esta lista?' });
  PUBLICOS.forEach((o) => {
    const idc = uid('pub');
    const input = h('input', { type: 'radio', name: nome, id: idc, value: o.valor });
    input.checked = o.valor === valor;
    input.addEventListener('change', () => { if (!input.checked) return; atual = o.valor; if (aoMudar) aoMudar(o.valor); });
    el.append(h('label', { class: 'opcao-publico', for: idc }, input,
      h('span', { class: 'op-tx' }, h('span', { class: 'op-t', text: o.titulo }), h('span', { class: 'op-d', text: o.apoio }))));
  });
  return { el, valor: () => atual };
}
