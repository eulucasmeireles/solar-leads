'use strict';
/* Solar Leads - utilitários. Todo dado da API entra no DOM por textContent/atributos. */

const nfNum = new Intl.NumberFormat('pt-BR');
const nfPct = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
const nfBRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

/* estado global da aplicação */
const app = {
  rota: 0,           // contador de navegações (descarta respostas atrasadas)
  campos: null,      // de /api/campos (carregado sob demanda)
  limpar: [],        // limpezas da tela atual (timers, requisições)
};

function aoSair(fn) { app.limpar.push(fn); }
function sairDaTela() { app.limpar.splice(0).forEach((f) => { try { f(); } catch (e) { /* ignora */ } }); }

/* ---------- DOM ---------- */
function h(tag, attrs, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k === 'style' && typeof v === 'object') { for (const [p, x] of Object.entries(v)) { if (x == null) continue; if (p.startsWith('--')) e.style.setProperty(p, x); else e.style[p] = x; } }
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : String(v));
  }
  for (const f of filhos.flat(Infinity)) {
    if (f == null || f === false) continue;
    e.append(f.nodeType ? f : document.createTextNode(String(f)));
  }
  return e;
}

const NS = 'http://www.w3.org/2000/svg';
function s(tag, attrs, ...filhos) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) e.setAttribute(k, String(v));
  for (const f of filhos.flat()) if (f) e.append(f);
  return e;
}

const ICONES = {
  sol: ['M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z', 'M12 2.5v2M12 19.5v2M4.8 4.8l1.4 1.4M17.8 17.8l1.4 1.4M2.5 12h2M19.5 12h2M4.8 19.2l1.4-1.4M17.8 6.2l1.4-1.4'],
  olho: ['M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z', 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z'],
  olhofechado: ['M3 3l18 18', 'M10.7 5.1c.4-.1.9-.1 1.3-.1 6.4 0 10 7 10 7a17 17 0 0 1-3.1 4.1', 'M6.5 6.6C3.7 8.5 2 12 2 12s3.6 7 10 7c1.8 0 3.5-.5 4.9-1.3', 'M9.9 9.9a3 3 0 0 0 4.2 4.2'],
  lua: ['M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z'],
  casa: ['M4 11l8-7 8 7', 'M6 9.5V20h12V9.5', 'M10 20v-5h4v5'],
  listas: ['M9 6h11M9 12h11M9 18h11', 'M4.5 6h.01M4.5 12h.01M4.5 18h.01'],
  whatsapp: ['M4 20l1.3-4.2A8 8 0 1 1 8.4 18.8L4 20z', 'M9.2 9.2c.2 2.6 2.2 4.8 5 5.6l1.3-1.5-1.9-1-.8.8a4.4 4.4 0 0 1-1.8-1.8l.8-.8-1-1.9-1.6 1.1z'],
  base: ['M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z', 'M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6', 'M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6'],
  busca: ['M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z', 'M20.5 20.5l-4.5-4.5'],
  x: ['M6 6l12 12M18 6L6 18'],
  mais: ['M12 5v14M5 12h14'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  baixo: ['M6 9l6 6 6-6'],
  direita: ['M9 6l6 6-6 6'],
  esquerda: ['M15 6l-6 6 6 6'],
  baixar: ['M12 4v11M7 10l5 5 5-5M5 20h14'],
  filtro: ['M4 8h9M17 8h3M4 16h3M11 16h9', 'M13 8a2 2 0 1 0 4 0a2 2 0 1 0-4 0', 'M7 16a2 2 0 1 0 4 0a2 2 0 1 0-4 0'],
  lixeira: ['M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3'],
  copiar: ['M9 9h11v11H9z', 'M5 15V4h11'],
  externo: ['M14 4h6v6M20 4l-9 9', 'M18 14v6H4V6h6'],
  pin: ['M12 21s7-6.2 7-11.5a7 7 0 1 0-14 0C5 14.8 12 21 12 21z', 'M12 7.5a2.2 2.2 0 1 0 0 4.4a2.2 2.2 0 1 0 0-4.4z'],
  atualizar: ['M20 11a8 8 0 1 0-2.3 5.7', 'M20 4v7h-7'],
  alerta: ['M12 3l10 18H2L12 3z', 'M12 10v5M12 18h.01'],
  email: ['M3 6h18v12H3z', 'M3 7l9 7 9-7'],
  pontos: ['M5 12h.01M12 12h.01M19 12h.01'],
  parar: ['M7 7h10v10H7z'],
  info: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M12 11v5M12 8h.01'],
  predio: ['M5 21V4h9v17M14 9h5v12M3 21h18', 'M8 8h3M8 12h3M8 16h3'],
  telefone: ['M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z'],
  pessoa: ['M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8z', 'M4 20c0-4 3.6-6 8-6s8 2 8 6'],
  estrela: ['M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3z'],
  grafico: ['M4 20V10M10 20V4M16 20v-8M22 20H2'],
  raio: ['M13 3L5 14h6l-1 7 8-11h-6l1-7z'],
  pasta: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z'],
  casapredio: ['M2.5 12l4-3.5 4 3.5', 'M4 11.5V19h5v-7.5', 'M13 19V6h7.5v13', 'M16.7 9.5h.01M16.7 12.5h.01M16.7 15.5h.01', 'M1.5 19h21'],
  menos: ['M5 12h14'],
  sparkle: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z'],
};

function icone(nome, tam = 20) {
  return s('svg', {
    viewBox: '0 0 24 24', width: tam, height: tam, fill: 'none', stroke: 'currentColor',
    'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false', class: 'ic',
  }, (ICONES[nome] || []).map((d) => s('path', { d })));
}

/* replaceChildren ignora null/false (evita o texto "null" na tela) */
(function () {
  const original = Element.prototype.replaceChildren;
  Element.prototype.replaceChildren = function (...nos) { return original.apply(this, nos.flat(Infinity).filter((x) => x != null && x !== false)); };
})();

function debounce(fn, ms = 300) {
  let t;
  const f = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  f.cancelar = () => clearTimeout(t);
  return f;
}

/* ---------- API ---------- */
async function api(caminho, opcoes = {}) {
  const init = { method: opcoes.method || 'GET', headers: { Accept: 'application/json' }, signal: opcoes.signal };
  if (opcoes.corpo !== undefined) {
    init.body = JSON.stringify(opcoes.corpo);
    init.headers['Content-Type'] = 'application/json';
  }
  let r;
  try { r = await fetch(caminho, init); } catch (e) {
    if (e && e.name === 'AbortError') throw e;
    throw new Error('Sem conexão com o servidor. Tente de novo.');
  }
  let dados = null;
  try { dados = await r.json(); } catch (e) { /* corpo vazio */ }
  if (r.status === 401) { location.href = '/login'; throw new Error('Sessão expirada. Entre de novo.'); }
  if (!r.ok) {
    let msg = dados && dados.detail;
    if (Array.isArray(msg)) msg = msg.map((x) => x.msg || '').filter(Boolean).join('; ');
    const err = new Error(msg || (r.status === 404 ? 'Este recurso ainda não está disponível.' : `Algo deu errado (código ${r.status}).`));
    err.status = r.status;
    throw err;
  }
  return dados;
}
const ehAborto = (e) => e && e.name === 'AbortError';

/* requisição "mais recente vence": cada chamada cancela a anterior da mesma chave */
function criarBuscador() {
  let ctl = null;
  const f = async (caminho, opcoes = {}) => {
    if (ctl) ctl.abort();
    ctl = new AbortController();
    return api(caminho, { ...opcoes, signal: ctl.signal });
  };
  f.cancelar = () => { if (ctl) ctl.abort(); };
  return f;
}

/* ---------- formatação ---------- */
const num = (n) => nfNum.format(Number(n) || 0);
const pct = (n) => nfPct.format(Number(n) || 0) + '%';

function dataBR(v) {
  if (!v) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v));
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(v);
}
function dataHoraBR(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(v || ''));
  return m ? `${m[3]}/${m[2]}/${m[1]} às ${m[4]}h${m[5]}` : dataBR(v);
}
function mesBR(v) {
  const m = /^(\d{4})-(\d{2})/.exec(String(v || ''));
  if (!m) return String(v || '');
  const nomes = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
  return `${nomes[Number(m[2]) - 1] || m[2]} de ${m[1]}`;
}
function titleCase(t) {
  const peq = new Set(['de', 'da', 'do', 'das', 'dos', 'e']);
  return String(t || '').toLowerCase().split(' ').map((p, i) =>
    (i > 0 && peq.has(p)) ? p : p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
}
function cnpjBR(v) {
  const d = String(v || '').replace(/\D/g, '');
  return d.length === 14 ? d.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5') : String(v || '');
}
function telBR(v) {
  const d = String(v || '').replace(/\D/g, '');
  const m = /^55(\d{2})(\d{4,5})(\d{4})$/.exec(d);
  return m ? `+55 (${m[1]}) ${m[2]}-${m[3]}` : String(v || '');
}
function dinheiroBR(v) {
  const n = Number(String(v).replace(',', '.'));
  return Number.isFinite(n) ? nfBRL.format(n) : String(v);
}
const soDigitos = (v) => String(v || '').replace(/\D/g, '');

function nomeCompleto(l) { return [l.primeiro_nome, l.sobrenome].map((x) => String(x || '').trim()).filter(Boolean).join(' '); }
function iniciais(l) {
  const n = nomeCompleto(l) || String(l.empresa || l.razao_social || '').trim();
  const p = n.split(/\s+/).filter((x) => !/^(de|da|do|das|dos|e)$/i.test(x));
  if (!p.length) return '?';
  const a = p[0].charAt(0), b = p.length > 1 ? p[p.length - 1].charAt(0) : '';
  return (a + b).toUpperCase();
}
function nomeAtividades(meta) {
  const a = (meta && meta.atividades) || [];
  if (!a.length) return 'Todas as atividades';
  if (a.length <= 2) return a.map((x) => x.descricao).join(' · ');
  return `${a[0].descricao} e mais ${a.length - 1}`;
}
const cidadeUF = (m) => `${titleCase(m.cidade)} · ${m.uf}`;

function saudacao() {
  const hr = new Date().getHours();
  return hr < 12 ? 'Bom dia' : hr < 18 ? 'Boa tarde' : 'Boa noite';
}

async function copiarTexto(texto) {
  try { await navigator.clipboard.writeText(texto); return true; } catch (e) { /* tenta alternativa */ }
  const ta = h('textarea', { style: { position: 'fixed', opacity: '0' }, 'aria-hidden': 'true' });
  ta.value = texto; document.body.append(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  ta.remove();
  return ok;
}

async function carregarCampos() {
  if (!app.campos) {
    try { app.campos = await api('/api/campos'); } catch (e) { app.campos = []; }
  }
  return app.campos;
}

/* ---------- apoio de interface ---------- */
const FAIXA_ROTULO = { A: 'Contato forte', B: 'Contato médio', C: 'Fraco' };
const PASTEIS = [['#E3F0FF', '#0A5FCC'], ['#FFE9D6', '#B25E00'], ['#E6F4EA', '#1E8E3E'], ['#EFE7FF', '#5B3FC4'], ['#FFE4EC', '#B3264D'], ['#E1F5F5', '#117A7A']];
const PASTEIS_ESCURO = [['#12294A', '#7DB4FF'], ['#3A2A14', '#FFB366'], ['#143222', '#6FD08A'], ['#2A2150', '#B7A4FF'], ['#40202C', '#FF9DB6'], ['#12363A', '#6FD3D3']];
function pastelDe(txt) {
  let n = 0; for (const c of String(txt || '?')) n = (n * 31 + c.charCodeAt(0)) >>> 0;
  return n % PASTEIS.length;
}
function dataRelativa(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(v || ''));
  if (!m) return dataBR(v);
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  const hoje = new Date(); hoje.setHours(0, 0, 0, 0);
  const dia = new Date(d); dia.setHours(0, 0, 0, 0);
  const diff = Math.round((hoje - dia) / 86400000);
  const hora = `${m[4]}:${m[5]}`;
  if (diff === 0) return `hoje, ${hora}`;
  if (diff === 1) return `ontem, ${hora}`;
  return dataBR(v);
}
function temaAtual() { return document.documentElement.getAttribute('data-tema') === 'escuro' ? 'escuro' : 'claro'; }
function definirTema(t) {
  if (t === 'escuro') document.documentElement.setAttribute('data-tema', 'escuro'); else document.documentElement.removeAttribute('data-tema');
  try { localStorage.setItem('solar-tema', t); } catch (e) { /* sem armazenamento */ }
  document.querySelectorAll('.avatar').forEach((a) => a.dispatchEvent(new Event('tema')));
}
