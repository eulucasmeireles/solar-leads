'use strict';
/* Garimpo - gráficos leves em HTML/SVG (sem biblioteca): anéis, barras e colunas arredondadas */

const ROTULOS_WHATS = { pessoal: 'WhatsApp pessoal', business: 'WhatsApp comercial', 'não verificado': 'Não verificado', '': 'Não verificado' };
const ROTULO_SOLAR = {
  'sem registro': 'Sem registro', 'dono: indício fraco': 'Indício no nome do dono', 'empresa já tem': 'A empresa já tem',
  'outra unidade da empresa tem': 'Outra unidade tem', 'outra empresa do dono tem': 'Outra empresa do dono tem', '': 'Não informado',
};

function cartaoGrafico(titulo, sub, corpo, classe) {
  return h('section', { class: 'graf ' + (classe || '') },
    h('header', { class: 'graf-cab' }, h('h3', { text: titulo }), sub ? h('span', { class: 'graf-sub', text: sub }) : null), corpo);
}

const vazioGraf = () => h('p', { class: 'graf-vazio', text: 'Nada para mostrar com os filtros atuais.' });

/* anel de proporções com legenda ao lado */
function donut({ dados, rotulo, cor, centro, centroRotulo }) {
  const linhas = (dados || []).filter((d) => Number(d.n) > 0);
  if (!linhas.length) return vazioGraf();
  const soma = linhas.reduce((t, d) => t + d.n, 0);
  let acc = 0;
  const arcos = linhas.map((d, i) => {
    const len = d.n / soma * 100, gap = linhas.length > 1 ? Math.min(0.9, len / 3) : 0;
    const a = s('circle', { class: 'arco', cx: 21, cy: 21, r: 15.9155, fill: 'none', 'stroke-width': 4.2, style: `--d:${Math.max(0.1, len - gap).toFixed(3)};--g:${(100 - Math.max(0.1, len - gap)).toFixed(3)};stroke:var(${cor(d, i)});stroke-dashoffset:${(25 - acc).toFixed(3)}` });
    acc += len; return a;
  });
  const svg = s('svg', { viewBox: '0 0 42 42', class: 'donut-svg', role: 'img', 'aria-label': linhas.map((d) => `${rotulo(d)}: ${num(d.n)}`).join('; ') },
    s('circle', { cx: 21, cy: 21, r: 15.9155, fill: 'none', class: 'anel-fundo', 'stroke-width': 4.2 }), arcos);
  return h('div', { class: 'donut' },
    h('div', { class: 'donut-fig' }, svg, h('div', { class: 'donut-centro' }, h('strong', { text: centro != null ? centro : num(soma) }), h('span', { text: centroRotulo || 'contatos' }))),
    h('ul', { class: 'legenda' }, linhas.map((d, i) => h('li', {},
      h('i', { class: 'lg-ponto', style: { background: `var(${cor(d, i)})` }, 'aria-hidden': 'true' }),
      h('span', { class: 'lg-rot', text: rotulo(d) }), h('span', { class: 'lg-val', text: num(d.n) }), h('span', { class: 'lg-pct', text: pct(d.n / soma * 100) })))));
}

/* barras horizontais finas e arredondadas, número à direita */
function barras({ dados, rotulo, ordenado }) {
  let linhas = (dados || []).filter((d) => Number(d.n) > 0);
  if (!ordenado) linhas = linhas.slice().sort((a, b) => b.n - a.n);
  if (!linhas.length) return vazioGraf();
  const max = Math.max(...linhas.map((d) => d.n)) || 1;
  return h('ul', { class: 'barras' }, linhas.map((d) => {
    const nome = rotulo ? rotulo(d) : String(d.valor);
    return h('li', { title: `${nome}: ${num(d.n)}` },
      h('span', { class: 'b-rot', text: nome }),
      h('span', { class: 'b-trilho' }, h('i', { class: 'b-barra', style: { '--p': String(d.n / max) }, 'aria-hidden': 'true' })),
      h('span', { class: 'b-val', text: num(d.n) }));
  }));
}

/* colunas arredondadas; rótulo de valor só quando cabe */
function colunas({ dados, rotulo, cor, legenda }) {
  const linhas = dados || [];
  const max = Math.max(0, ...linhas.map((d) => d.n));
  if (!linhas.length || max === 0) return vazioGraf();
  const muitas = linhas.length > 12;
  const idxMax = linhas.findIndex((d) => d.n === max);
  const eixo = h('div', { class: 'colunas' + (muitas ? ' densa' : ''), 'aria-hidden': 'true' }, linhas.map((d, i) => {
    const nome = rotulo ? rotulo(d) : String(d.valor);
    return h('div', { class: 'col', title: `${nome}: ${num(d.n)}` },
      h('span', { class: 'col-faixa' }, h('i', { class: 'col-barra', 'data-v': (!muitas || i === idxMax) && d.n > 0 ? num(d.n) : '', style: { '--p': String(d.n / max), '--c': cor ? `var(${cor})` : null } })),
      h('span', { class: 'col-rot', text: !muitas || i % 2 === (linhas.length - 1) % 2 ? nome : '' }));
  }));
  const tabela = h('table', { class: 'so-leitor' }, h('caption', { text: legenda || 'Dados do gráfico' }),
    h('thead', {}, h('tr', {}, h('th', { text: 'Categoria' }), h('th', { text: 'Contatos' }))),
    h('tbody', {}, linhas.map((d) => h('tr', {}, h('td', { text: rotulo ? rotulo(d) : String(d.valor) }), h('td', { text: num(d.n) })))));
  return h('div', {}, eixo, tabela);
}

const COR_FAIXA = { A: '--verde', B: '--laranja', C: '--cinza-graf' };
const COR_WHATS = { pessoal: '--verde', business: '--acento', 'não verificado': '--cinza-graf', '': '--cinza-graf' };
const COR_SOLAR = ['--cinza-graf', '--acento', '--azul-2', '--azul-3', '--laranja'];

function montarPainel(p, empresa) {
  const total = p.total || 0;
  const ct = `${num(total)} contatos`;
  const aptidao = Array.from({ length: 11 }, (_, i) => {
    const f = (p.aptidao || []).find((d) => Number(d.valor) === i);
    return { valor: i, n: f ? f.n : 0 };
  });
  const notaIa = Array.from({ length: 11 }, (_, i) => {
    const f = (p.nota_ia || []).find((d) => Number(d.valor) === i);
    return { valor: i, n: f ? f.n : 0 };
  });
  const comAnos = (p.solar_por_ano || []).filter((d) => d.n > 0);
  const totalAnos = comAnos.reduce((t, d) => t + d.n, 0);
  const solarOrdem = ['sem registro', 'dono: indício fraco', 'empresa já tem', 'outra unidade da empresa tem', 'outra empresa do dono tem'];
  const solar = (p.solar || []).slice().sort((a, b) => solarOrdem.indexOf(a.valor) - solarOrdem.indexOf(b.valor));
  const COR_OPORT = { 'Casa e empresa': '--verde', Casa: '--acento', Empresa: '--azul-2', 'Ampliação': '--laranja', 'Baixo potencial': '--cinza-graf' };
  const so = (card) => (empresa ? null : card);
  const raiz = h('div', { class: 'graficos' },
    so(cartaoGrafico('Onde está a oportunidade', null, donut({ dados: p.oportunidade, rotulo: (d) => d.valor || 'Não informada', cor: (d) => COR_OPORT[d.valor] || '--cinza-graf' }))),
    cartaoGrafico('Força dos contatos', null, donut({ dados: p.faixa, rotulo: (d) => FAIXA_ROTULO[d.valor] || 'Sem faixa', cor: (d) => COR_FAIXA[d.valor] || '--cinza-graf' })),
    so(cartaoGrafico('Energia solar', null, donut({ dados: solar, rotulo: (d) => ROTULO_SOLAR[d.valor] || titleCase(d.valor), cor: (d, i) => (d.valor === 'sem registro' ? '--cinza-graf' : COR_SOLAR[(i % 4) + 1]) }))),
    cartaoGrafico('Tipo de WhatsApp', null, donut({ dados: p.whatsapp, rotulo: (d) => ROTULOS_WHATS[d.valor] || d.valor, cor: (d) => COR_WHATS[d.valor] || '--cinza-graf' })),
    cartaoGrafico('Situação do CNPJ', null, donut({ dados: p.situacao, rotulo: (d) => titleCase(d.valor || 'não informada'), cor: (d, i) => (['--verde', '--cinza-graf', '--laranja', '--acento', '--vermelho'])[i % 5] })),
    cartaoGrafico('Atividades mais comuns', 'as 10 principais', barras({ dados: p.atividades, rotulo: (d) => d.valor || 'Não informada' })),
    cartaoGrafico('Bairros com mais contatos', 'os 10 principais', barras({ dados: p.bairros, rotulo: (d) => d.valor || 'Não informado' })),
    cartaoGrafico('Idade do dono', null, colunas({ dados: (p.idade || []).map((d) => ({ ...d, valor: String(d.valor) })), rotulo: (d) => d.valor || '—', cor: '--acento', legenda: 'Contatos por faixa de idade do dono' })),
    cartaoGrafico(empresa ? 'Encaixe no perfil' : 'Nota de potencial', 'de 0 a 10', colunas({ dados: aptidao, rotulo: (d) => String(d.valor), cor: '--acento', legenda: empresa ? 'Contatos por encaixe no perfil' : 'Contatos por nota de potencial' })),
    notaIa.some((d) => d.n) ? cartaoGrafico('Nota da IA', 'de 0 a 10', colunas({ dados: notaIa, rotulo: (d) => String(d.valor), cor: '--roxo', legenda: 'Contatos por nota da IA' })) : null,
    so(cartaoGrafico('Empresas que instalaram solar', totalAnos ? `${num(totalAnos)} empresas` : null, colunas({ dados: comAnos, rotulo: (d) => String(d.valor), cor: '--laranja', legenda: 'Empresas que instalaram energia solar por ano' }))));
  /* preenche com animação ao aparecer */
  requestAnimationFrame(() => requestAnimationFrame(() => raiz.classList.add('pronto')));
  return raiz;
}

function esqueletoPainel() {
  return h('div', { class: 'graficos', 'aria-busy': 'true' }, Array.from({ length: 6 }, () =>
    h('section', { class: 'graf' }, h('header', { class: 'graf-cab' }, esqueleto('40%', '18px')),
      h('div', { class: 'sk-pilha' }, esqueleto('90%', '12px'), esqueleto('70%', '12px'), esqueleto('55%', '12px'), esqueleto('35%', '12px')))));
}
