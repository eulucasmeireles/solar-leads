'use strict';
/* Solar Leads - pastas: telas, folhas de nome e de adicionar contatos */

/* ---------- folha: nome da pasta (criar e renomear) ---------- */
function folhaNomePasta({ titulo, nome, rotulo, aoSalvar }) {
  const idT = uid('folha');
  const campo = h('input', { type: 'text', id: uid('nome'), maxlength: '80', autocomplete: 'off', placeholder: 'Por exemplo: Clínicas de Montes Claros', 'aria-label': 'Nome da pasta', 'data-foco': '' });
  campo.value = nome || '';
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const botao = h('button', { class: 'btn btn-prim', type: 'button' }, rotulo || 'Salvar');
  async function salvar() {
    const v = campo.value.trim();
    if (!v) { erro.textContent = 'Escreva um nome para a pasta.'; campo.focus(); return; }
    botao.disabled = true; erro.textContent = '';
    try { await aoSalvar(v); m.fechar(); }
    catch (e) { erro.textContent = e.message; botao.disabled = false; }
  }
  botao.addEventListener('click', salvar);
  campo.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); salvar(); } });
  const painel = h('div', { class: 'folha folha-estreita', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('header', { class: 'folha-cab' }, h('h2', { id: idT, text: titulo }), h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar', onclick: () => m.fechar() }, icone('x', 20))),
    h('div', { class: 'folha-corpo' }, h('div', { class: 'campo' }, h('label', { for: campo.id, text: 'Nome' }), campo)),
    h('footer', { class: 'folha-rodape' }, erro, botao));
  const m = abrirModal(painel);
  setTimeout(() => campo.select(), 60);
}

/* ---------- folha: adicionar contatos a uma pasta ---------- */
function folhaAdicionarPasta({ listaId, total: totalFiltro, totalComWhats, telefones, queryFiltros, aoConcluir }) {
  const unico = Array.isArray(telefones) && telefones.length > 0;
  const idT = uid('folha');
  const S = { pastas: [], escolha: null, nome: '', qtd: 0, pular: true, soWhats: !unico && (totalComWhats || 0) > 0, carregou: false, criada: null };
  const totalAtual = () => (S.soWhats ? (totalComWhats == null ? totalFiltro : totalComWhats) : totalFiltro) || 0;
  const maxAtual = () => Math.max(100, Math.ceil(totalAtual() / 100) * 100);
  S.qtd = totalAtual() > 100 ? 100 : 0;

  const zonaPastas = h('div', { class: 'radios', role: 'radiogroup', 'aria-label': 'Pasta de destino' });
  const campoNome = h('input', { type: 'text', maxlength: '80', autocomplete: 'off', placeholder: 'Nome da nova pasta', 'aria-label': 'Nome da nova pasta' });
  campoNome.addEventListener('input', () => { S.nome = campoNome.value; atualizar(); });
  const zonaQtd = h('section', { class: 'folha-sec', hidden: unico });
  const resumo = h('p', { class: 'resumo-add', 'aria-live': 'polite' });
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const botao = h('button', { class: 'btn btn-prim', type: 'button' }, 'Adicionar');

  const efetivo = () => (unico ? telefones.length : (S.qtd === 0 || S.qtd >= totalAtual() ? totalAtual() : S.qtd));

  function desenharPastas() {
    const itens = S.pastas.map((p) => ({ v: String(p.id), nome: p.nome, n: `${num(p.contatos)} ${p.contatos === 1 ? 'contato' : 'contatos'}` }));
    itens.push({ v: 'nova', nome: 'Nova pasta', n: null });
    zonaPastas.replaceChildren(...itens.map((it) => {
      const id = uid('rd');
      const r = h('input', { type: 'radio', name: 'pasta-destino', id, value: it.v });
      r.checked = S.escolha === it.v;
      r.addEventListener('change', () => { S.escolha = it.v; desenharPastas(); atualizar(); if (it.v === 'nova') setTimeout(() => campoNome.focus(), 20); });
      return h('label', { class: 'radio-linha' + (S.escolha === it.v ? ' on' : ''), for: id }, r,
        h('span', { class: 'radio-ic' }, icone(it.v === 'nova' ? 'mais' : 'pasta', 18)),
        h('span', { class: 'radio-tx' }, h('span', { class: 'radio-nome', text: it.nome }), it.n ? h('span', { class: 'radio-n', text: it.n }) : null),
        h('span', { class: 'radio-marca', 'aria-hidden': 'true' }, icone('check', 16)));
    }), campoNome);
    campoNome.hidden = S.escolha !== 'nova';
  }
  function desenharQtd() {
    if (unico) return;
    const max = maxAtual(), total = totalAtual();
    const chips = [100, 200, 300, 500, 1000].filter((q) => q <= max).map((q) => h('button', { type: 'button', class: 'pilula', 'aria-pressed': String(S.qtd === q), onclick: () => { S.qtd = q; desenharQtd(); atualizar(); } }, num(q)));
    chips.push(h('button', { type: 'button', class: 'pilula', 'aria-pressed': String(S.qtd === 0), onclick: () => { S.qtd = 0; desenharQtd(); atualizar(); } }, 'Todos do filtro'));
    const passo = (d) => { const base = S.qtd === 0 ? max : S.qtd; S.qtd = Math.max(100, Math.min(max, base + d)); desenharQtd(); atualizar(); };
    const menos = h('button', { class: 'btn-icone btn-passo', type: 'button', 'aria-label': 'Diminuir 100', disabled: S.qtd !== 0 && S.qtd <= 100, onclick: () => passo(-100) }, icone('menos', 18));
    const mais = h('button', { class: 'btn-icone btn-passo', type: 'button', 'aria-label': 'Aumentar 100', disabled: S.qtd === 0 || S.qtd >= max, onclick: () => passo(100) }, icone('mais', 18));
    zonaQtd.replaceChildren(h('h3', { text: 'Quantos contatos?' }),
      h('div', { class: 'pilulas' }, chips),
      h('div', { class: 'passo' }, menos, h('output', { class: 'passo-v', 'aria-live': 'polite', text: S.qtd === 0 ? 'Todos' : num(S.qtd) }), mais, h('span', { class: 'apoio-peq', text: 'de 100 em 100' })));
  }
  function atualizar() {
    const total = totalAtual(), n = efetivo();
    const rot = `Adicionar ${num(n)} ${n === 1 ? 'contato' : 'contatos'}`;
    if (!botao.dataset.ocupado) botao.textContent = rot;
    if (unico) resumo.textContent = telefones.length === 1 ? 'Será adicionado só este contato.' : `Serão adicionados ${num(telefones.length)} contatos.`;
    else if (S.qtd === 0 || S.qtd >= total) resumo.textContent = `Serão adicionados todos os ${num(total)} contatos do filtro atual${S.soWhats ? ', só com WhatsApp confirmado' : ''}.`;
    else resumo.textContent = `Serão adicionados os ${num(S.qtd)} primeiros dos ${num(total)} contatos do filtro atual${S.soWhats ? ' com WhatsApp confirmado' : ''}, na ordem em que aparecem.`;
    const pronto = S.escolha && (S.escolha !== 'nova' || S.nome.trim()) && n > 0;
    botao.disabled = !pronto;
  }
  const swWhats = interruptor({ rotulo: 'Só quem tem WhatsApp confirmado', descricao: 'Inclui contas pessoais e comerciais já verificadas.', checked: S.soWhats, aoMudar: (v) => { S.soWhats = v; if (S.qtd > maxAtual()) S.qtd = 0; desenharQtd(); atualizar(); } });
  const sw = interruptor({ rotulo: 'Pular quem já está em alguma pasta', descricao: 'Evita repetir contatos que você já separou.', checked: true, aoMudar: (v) => { S.pular = v; } });

  botao.addEventListener('click', async () => {
    botao.disabled = true; botao.dataset.ocupado = '1'; erro.textContent = '';
    botao.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Adicionando…');
    try {
      let id = S.escolha, nomePasta = (S.pastas.find((p) => String(p.id) === S.escolha) || {}).nome;
      if (S.escolha === 'nova') {
        if (!S.criada) { nomePasta = S.nome.trim(); S.criada = { id: (await api('/api/pastas', { method: 'POST', corpo: { nome: nomePasta } })).id, nome: nomePasta }; }
        id = S.criada.id; nomePasta = S.criada.nome;
      }
      const q = new URLSearchParams(unico ? '' : queryFiltros());
      if (!unico && S.soWhats) { q.delete('tipo_whatsapp'); q.append('tipo_whatsapp', 'pessoal'); q.append('tipo_whatsapp', 'business'); }
      q.set('lista_id', listaId);
      if (unico) { telefones.forEach((t) => q.append('telefone', t)); q.set('pular_em_pastas', '0'); }
      else { q.set('quantidade', String(S.qtd === 0 || S.qtd >= totalAtual() ? 0 : S.qtd)); q.set('pular_em_pastas', S.pular ? '1' : '0'); }
      const r = await api(`/api/pastas/${encodeURIComponent(id)}/adicionar?${q.toString()}`, { method: 'POST' });
      m.fechar();
      const ad = r.adicionados || 0;
      if (ad > 0) avisar(`${num(ad)} ${ad === 1 ? 'contato adicionado' : 'contatos adicionados'} à pasta ${nomePasta}${r.ja_estavam ? ` (${num(r.ja_estavam)} já estavam)` : ''}`, null, { href: `#/pasta/${encodeURIComponent(id)}`, rotulo: 'Abrir pasta' });
      else avisar(r.ja_estavam ? 'Nenhum contato novo: todos já estavam em pastas.' : 'Nenhum contato foi adicionado.', null, { href: `#/pasta/${encodeURIComponent(id)}`, rotulo: 'Abrir pasta' });
      if (aoConcluir) aoConcluir(r);
    } catch (e) {
      erro.textContent = e.message; delete botao.dataset.ocupado; botao.replaceChildren(); atualizar();
    }
  });

  const painel = h('div', { class: 'folha', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('header', { class: 'folha-cab' },
      h('div', {}, h('h2', { id: idT, text: 'Adicionar a uma pasta' }), h('p', { class: 'apoio', text: unico ? 'Escolha a pasta para este contato.' : 'Escolha a pasta e quantos contatos entram.' })),
      h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar', onclick: () => m.fechar() }, icone('x', 20))),
    h('div', { class: 'folha-corpo' },
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Pasta' }), zonaPastas),
      zonaQtd,
      unico ? null : h('section', { class: 'folha-sec' }, h('div', { class: 'grupo-linhas' }, swWhats.el, sw.el)),
      resumo),
    h('footer', { class: 'folha-rodape' }, erro, botao));
  const m = abrirModal(painel);
  desenharQtd(); desenharPastas(); atualizar();
  zonaPastas.prepend(h('div', { class: 'sk-pilha' }, esqueleto('100%', '52px'), esqueleto('100%', '52px')));
  api('/api/pastas').then((ps) => { S.pastas = ps; S.escolha = ps.length ? String(ps[0].id) : 'nova'; S.carregou = true; desenharPastas(); atualizar(); })
    .catch((e) => { S.pastas = []; S.escolha = 'nova'; erro.textContent = e.status === 404 ? 'As pastas ainda não estão disponíveis neste servidor.' : e.message; desenharPastas(); atualizar(); });
}

/* ---------- resultados: peças compartilhadas ---------- */
const VAR_COR = { verde: '--verde', azul: '--acento', cinza: '--cinza-graf', laranja: '--laranja', vermelho: '--vermelho' };
const taxaTxt = (t) => (t == null ? '—' : pct(t));

function faixaResultados(r, contatos) {
  const itens = [['Contatos', num(contatos)], ['Com resultado', num(r.com_resultado)], ['Responderam', num(r.responderam)], ['Interessados', num(r.interessados)], ['Taxa de resposta', taxaTxt(r.taxa_resposta)]];
  return h('div', { class: 'numeros-faixa' }, itens.map(([rot, v]) => h('div', { class: 'numero-m' }, h('div', { class: 'numero-m-v', text: v }), h('div', { class: 'numero-m-r', text: rot }))));
}

function tabelaGrupo(titulo, linhas) {
  const corpo = linhas && linhas.length
    ? h('table', { class: 'tabela-leve' },
      h('thead', {}, h('tr', {}, ['Grupo', 'Enviados', 'Responderam', 'Interessados', 'Taxa de resposta'].map((c, i) => h('th', { scope: 'col', class: i ? 'num' : '', text: c })))),
      h('tbody', {}, linhas.map((g) => h('tr', {}, h('th', { scope: 'row', text: g.rotulo || g.valor || '—' }), h('td', { class: 'num', text: num(g.enviados) }), h('td', { class: 'num', text: num(g.responderam) }), h('td', { class: 'num', text: num(g.interessados) }), h('td', { class: 'num', text: taxaTxt(g.taxa) })))))
    : h('p', { class: 'graf-vazio', text: 'Ainda sem dados.' });
  return cartaoGrafico(titulo, null, h('div', { class: 'rolagem-x' }, corpo));
}

function abaResultados(r) {
  if (!r || !r.com_resultado) return h('p', { class: 'convite', text: 'Depois do disparo, registre aqui o que aconteceu. É isso que ensina o sistema a pontuar melhor.' });
  const g = r.grupos || {};
  const raiz = h('div', { class: 'graficos' },
    cartaoGrafico('O que aconteceu', null, donut({ dados: r.por_resultado, rotulo: (d) => d.rotulo || rotuloResultado(d.valor), cor: (d) => VAR_COR[corResultado(d.valor, d.rotulo)], centroRotulo: 'com resultado', centro: num(r.com_resultado) }), 'graf-larga'),
    tabelaGrupo('Quem respondeu mais: por força do contato', g.forca),
    tabelaGrupo('Quem respondeu mais: por oportunidade', g.oportunidade),
    tabelaGrupo('Quem respondeu mais: por tipo de WhatsApp', g.whatsapp));
  return raiz;
}

/* ---------- folha: registrar resultados ---------- */
async function folhaRegistrarResultados({ pastaId, aoConcluir }) {
  const idT = uid('folha');
  const opcoes = await opcoesResultado();
  const S = { resultado: null, modo: 'colar' };
  const zonaRes = h('div', { class: 'radios', role: 'radiogroup', 'aria-label': 'Resultado' });
  const texto = h('textarea', { class: 'textarea', rows: '6', placeholder: 'Cole os números, um por linha (com ou sem +55)', 'aria-label': 'Números, um por linha' });
  const zonaTexto = h('div', { class: 'campo' }, texto);
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const botao = h('button', { class: 'btn btn-prim', type: 'button', disabled: true }, 'Registrar');
  const modo = segmentado({ opcoes: [{ valor: 'colar', rotulo: 'Colar os números' }, { valor: 'todos', rotulo: 'Todos sem resultado' }], valor: 'colar', rotulo: 'A quem aplicar', papel: 'radio', aoMudar: (v) => { S.modo = v; zonaTexto.hidden = v !== 'colar'; atualizar(); } });
  function desenharRes() {
    zonaRes.replaceChildren(...opcoes.map((o) => {
      const id = uid('rd'), cor = corResultado(o.valor, o.rotulo);
      const r = h('input', { type: 'radio', name: 'resultado', id, value: o.valor });
      r.checked = S.resultado === o.valor;
      r.addEventListener('change', () => { S.resultado = o.valor; desenharRes(); atualizar(); });
      return h('label', { class: 'radio-linha' + (S.resultado === o.valor ? ' on' : ''), for: id }, r, h('span', { class: `dot-res dot-${cor} dot-g`, 'aria-hidden': 'true' }), h('span', { class: 'radio-tx' }, h('span', { class: 'radio-nome', text: o.rotulo })), h('span', { class: 'radio-marca', 'aria-hidden': 'true' }, icone('check', 16)));
    }));
  }
  function atualizar() { botao.disabled = !(S.resultado && (S.modo === 'todos' || texto.value.trim())); }
  texto.addEventListener('input', atualizar);
  botao.addEventListener('click', async () => {
    botao.disabled = true; erro.textContent = '';
    botao.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Registrando…');
    try {
      const corpo = S.modo === 'todos' ? { resultado: S.resultado, todos_sem_resultado: true } : { resultado: S.resultado, texto: texto.value };
      const r = await api(`/api/pastas/${encodeURIComponent(pastaId)}/resultados`, { method: 'POST', corpo });
      m.fechar();
      const nao = r.nao_encontrados || 0;
      avisar(`${num(r.marcados)} ${r.marcados === 1 ? 'marcado' : 'marcados'}${nao ? `; ${num(nao)} ${nao === 1 ? 'número não está' : 'números não estão'} nesta pasta` : ''}`);
      if (aoConcluir) aoConcluir(r);
    } catch (e) { erro.textContent = e.message; botao.replaceChildren('Registrar'); atualizar(); }
  });
  const painel = h('div', { class: 'folha', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('header', { class: 'folha-cab' }, h('div', {}, h('h2', { id: idT, text: 'Registrar resultados' }), h('p', { class: 'apoio', text: 'Conte o que aconteceu depois do disparo.' })),
      h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar', onclick: () => m.fechar() }, icone('x', 20))),
    h('div', { class: 'folha-corpo' },
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Resultado' }), zonaRes),
      h('section', { class: 'folha-sec' }, h('h3', { text: 'A quem aplicar' }), modo.el, zonaTexto)),
    h('footer', { class: 'folha-rodape' }, erro, botao));
  const m = abrirModal(painel);
  desenharRes(); atualizar();
}

/* ---------- Pastas ---------- */
function textoExportacoes(p) {
  const n = p.exportacoes || 0;
  return n > 0 ? `Exportada ${n === 1 ? '1 vez' : `${n} vezes`}${p.ultima_exportacao ? ` · última ${dataRelativa(p.ultima_exportacao)}` : ''}` : '';
}

function cartaoPasta(p, aoRenomear, aoExcluir) {
  const cidades = (p.cidades || []).join(' · ');
  const menu = criarMenu(icone('pontos', 20), [
    { rotulo: 'Renomear', icone: 'listas', aoClicar: () => aoRenomear(p) },
    { rotulo: 'Excluir pasta', icone: 'lixeira', perigo: true, aoClicar: () => aoExcluir(p) },
  ], { classeBotao: 'btn-icone', rotulo: `Mais opções da pasta ${p.nome}` });
  menu.classList.add('cl-menu');
  const exp = textoExportacoes(p);
  return h('article', { class: 'cartao cartao-lista cartao-pasta' },
    h('header', { class: 'cl-topo' }, h('span', { class: 'pasta-ic' }, icone('pasta', 22)), menu),
    h('h3', {}, h('a', { class: 'cl-link', href: `#/pasta/${encodeURIComponent(p.id)}`, text: p.nome })),
    h('div', { class: 'cl-contatos', text: `${num(p.contatos)} ${p.contatos === 1 ? 'contato' : 'contatos'}` }),
    h('p', { class: 'cl-ativ', text: cidades || 'Ainda sem contatos' }),
    h('footer', { class: 'cl-rodape' }, h('time', { datetime: p.criada_em, text: `criada ${dataRelativa(p.criada_em)}` }), exp ? h('span', { class: 'cl-exp', text: exp }) : null));
}

async function excluirPasta(p, aposExcluir) {
  const ok = await confirmar({ titulo: 'Excluir esta pasta?', texto: `A pasta "${p.nome}" será apagada. Os contatos continuam nas listas de origem.`, ok: 'Excluir', perigo: true });
  if (!ok) return;
  try { await api(`/api/pastas/${encodeURIComponent(p.id)}`, { method: 'DELETE' }); avisar('Pasta excluída.'); aposExcluir(); }
  catch (e) { avisar(e.message, 'erro'); }
}
function renomearPasta(p, aposSalvar) {
  folhaNomePasta({ titulo: 'Renomear pasta', nome: p.nome, rotulo: 'Salvar', aoSalvar: async (nome) => { await api(`/api/pastas/${encodeURIComponent(p.id)}`, { method: 'PATCH', corpo: { nome } }); avisar('Pasta renomeada.'); aposSalvar(nome); } });
}

function telaPastas(raiz) {
  document.title = 'Pastas · Solar Leads';
  const grade = h('div', { class: 'grade-listas' }, esqueletoCartoes(3));
  const zonaGeral = h('div', {});
  const busca = h('input', { type: 'search', placeholder: 'Buscar pasta…', autocomplete: 'off', 'aria-label': 'Buscar pasta' });
  let todas = [];
  const norm = (t) => String(t).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  function desenhar() {
    const q = norm(busca.value.trim());
    const ls = todas.filter((p) => !q || norm(p.nome).includes(q));
    if (!ls.length) { grade.replaceChildren(todas.length ? estadoVazio('Nenhuma pasta com esse nome', 'Tente outro nome.', null, 'pasta')
      : estadoVazio('Nenhuma pasta ainda', 'Crie uma pasta e separe os melhores contatos de cada lista.', h('button', { class: 'btn btn-prim', type: 'button', onclick: nova }, icone('mais', 18), 'Nova pasta'), 'pasta')); return; }
    grade.replaceChildren(...ls.map((p) => cartaoPasta(p, (x) => renomearPasta(x, carregar), (x) => excluirPasta(x, carregar))));
  }
  async function carregar() {
    try { todas = await api('/api/pastas'); desenhar(); }
    catch (e) { grade.replaceChildren(estadoErro(e.status === 404 ? 'As pastas ainda não estão disponíveis neste servidor.' : e.message, carregar)); }
  }
  async function carregarGeral() {
    try {
      const r = await api('/api/resultados');
      if (!r || !r.com_resultado) return;
      zonaGeral.replaceChildren(h('section', { class: 'cartao cartao-geral' },
        h('header', { class: 'cartao-cab' }, h('div', {}, h('h2', { text: 'Resultado geral dos disparos' }), h('p', { class: 'apoio', text: 'Somando todas as pastas.' })),
          h('button', { class: 'link-btn', type: 'button', onclick: () => grade.scrollIntoView({ behavior: 'smooth', block: 'start' }) }, 'Ver por pasta')),
        faixaResultados(r, r.total)));
    } catch (e) { /* sem resultados ainda */ }
  }
  function nova() {
    folhaNomePasta({ titulo: 'Nova pasta', rotulo: 'Criar pasta', aoSalvar: async (nome) => { const r = await api('/api/pastas', { method: 'POST', corpo: { nome } }); avisar('Pasta criada.', null, { href: `#/pasta/${encodeURIComponent(r.id)}`, rotulo: 'Abrir' }); carregar(); } });
  }
  busca.addEventListener('input', debounce(desenhar, 150));
  raiz.append(cabecalhoPagina('Pastas', 'Separe os contatos que você vai trabalhar.', h('button', { class: 'btn btn-prim', type: 'button', onclick: nova }, icone('mais', 18), 'Nova pasta')),
    zonaGeral, h('div', { class: 'combo combo-pilula busca-listas' }, icone('busca', 18), busca), grade);
  carregar(); carregarGeral();
}

/* ---------- Uma pasta ---------- */
async function telaPasta(raiz, id, subaba, qs) {
  document.title = 'Pasta · Solar Leads';
  const rota = app.rota;
  const idUrl = encodeURIComponent(id);
  raiz.append(h('div', { class: 'lista-cab', 'aria-busy': 'true' }, esqueleto('260px', '38px'), esqueleto('320px', '16px', 'mt')));
  let pasta;
  try { pasta = await api(`/api/pastas/${idUrl}`); } catch (e) {
    if (rota !== app.rota) return;
    raiz.replaceChildren(h('a', { class: 'link-volta', href: '#/pastas' }, icone('esquerda', 16), 'Pastas'), estadoErro(e.status === 404 ? 'Não encontramos esta pasta. Ela pode ter sido excluída.' : e.message, e.status === 404 ? null : () => rotear()));
    return;
  }
  if (rota !== app.rota) return;
  await Promise.all([carregarCampos(), opcoesResultado()]);
  if (rota !== app.rota) return;
  raiz.replaceChildren();

  let termo = new URLSearchParams(qs || '').get('busca') || '';
  let aba = subaba === 'resultados' ? 'resultados' : 'contatos';
  let R = null;
  const titulo = h('h1', { text: pasta.nome });
  const apoio = h('p', { class: 'apoio' });
  const btnExp = h('a', { class: 'btn btn-prim' }, icone('baixar', 18), 'Exportar pasta');
  const faixa = h('div', { class: 'zona-faixa' });
  function desenharCab() {
    apoio.textContent = [`${num(pasta.contatos)} ${pasta.contatos === 1 ? 'contato' : 'contatos'}`, (pasta.cidades || []).join(' · '), `criada ${dataRelativa(pasta.criada_em)}`, textoExportacoes(pasta)].filter(Boolean).join(' · ');
    titulo.textContent = pasta.nome; document.title = `${pasta.nome} · Solar Leads`;
    if (pasta.contatos) { btnExp.href = `/api/pastas/${idUrl}/exportar`; btnExp.removeAttribute('aria-disabled'); } else { btnExp.removeAttribute('href'); btnExp.setAttribute('aria-disabled', 'true'); }
  }
  desenharCab();
  const menu = criarMenu(icone('pontos', 20), [
    { rotulo: 'Renomear', icone: 'listas', aoClicar: () => renomearPasta(pasta, (nome) => { pasta.nome = nome; desenharCab(); }) },
    { rotulo: 'Excluir pasta', icone: 'lixeira', perigo: true, aoClicar: () => excluirPasta(pasta, () => { location.hash = '#/pastas'; }) },
  ], { classeBotao: 'btn btn-sec btn-redondo', rotulo: 'Mais ações da pasta' });
  const btnReg = h('button', { class: 'btn btn-sec', type: 'button', onclick: () => folhaRegistrarResultados({ pastaId: id, aoConcluir: recarregarTudo }) }, icone('check', 18), 'Registrar resultados');
  raiz.append(h('header', { class: 'lista-cab' },
    h('a', { class: 'link-volta', href: '#/pastas' }, icone('esquerda', 16), 'Pastas'),
    h('div', { class: 'lista-cab-linha' }, h('div', { class: 'lista-titulo' }, titulo, apoio), h('div', { class: 'pag-acoes' }, btnReg, btnExp, menu))), faixa);

  function desenharFaixa() {
    if (!R) { faixa.replaceChildren(); return; }
    faixa.replaceChildren(R.com_resultado ? faixaResultados(R, pasta.contatos) : h('p', { class: 'convite', text: 'Depois do disparo, registre aqui o que aconteceu. É isso que ensina o sistema a pontuar melhor.' }));
  }
  const painelRes = h('div', { class: 'aba-painel', id: 'painel-res', role: 'tabpanel', 'aria-labelledby': 'aba-res', hidden: true });
  async function carregarResultados() {
    try { R = await api(`/api/pastas/${idUrl}/resultados`); } catch (e) { R = null; }
    desenharFaixa(); painelRes.replaceChildren(R ? abaResultados(R) : estadoErro('Os resultados ainda não estão disponíveis neste servidor.', carregarResultados));
  }
  async function recarregarPasta() {
    try { pasta = await api(`/api/pastas/${idUrl}`); desenharCab(); } catch (e) { /* mantém */ }
    md.carregar(true);
  }
  async function recarregarTudo() { await recarregarPasta(); carregarResultados(); }

  const resultado = h('p', { class: 'resultado', 'aria-live': 'polite' });
  const md = criarMestreDetalhe({
    url: (pagina) => `/api/pastas/${idUrl}/contatos?${new URLSearchParams({ ...(termo ? { busca: termo } : {}), pagina: String(pagina), por_pagina: '50' }).toString()}`,
    perfil: {
      blocoExtra: (l) => {
        const sel = h('select', { id: uid('res'), 'aria-label': 'Resultado do contato' }, h('option', { value: '', text: 'Sem resultado' }), RES.opcoes.map((o) => h('option', { value: o.valor, text: o.rotulo })));
        sel.value = l.resultado || '';
        sel.addEventListener('change', async () => {
          sel.disabled = true;
          try { await api(`/api/pastas/${idUrl}/resultados`, { method: 'POST', corpo: { resultado: sel.value, telefones: [l.telefone] } }); l.resultado = sel.value || null; avisar(sel.value ? 'Resultado registrado.' : 'Resultado removido.'); md.redesenhar(); carregarResultados(); }
          catch (e) { avisar(e.message, 'erro'); sel.value = l.resultado || ''; }
          sel.disabled = false;
        });
        return h('div', { class: 'pf-resultado' }, h('label', { for: sel.id, text: 'Resultado' }), sel);
      },
      acoesExtra: (l, ctx) => h('button', { class: 'btn btn-cinza', type: 'button', onclick: async (ev) => {
        const b = ev.currentTarget;
        const ok = await confirmar({ titulo: 'Remover este contato da pasta?', texto: 'Ele continua na lista de origem.', ok: 'Remover', perigo: true });
        if (!ok) return;
        b.disabled = true;
        try { await api(`/api/pastas/${idUrl}/contatos?${new URLSearchParams({ telefone: l.telefone }).toString()}`, { method: 'DELETE' }); avisar('Contato removido da pasta.'); ctx.fechar(); recarregarTudo(); }
        catch (e) { avisar(e.message, 'erro'); b.disabled = false; }
      } }, icone('lixeira', 18), 'Remover da pasta'),
    },
    vazio: () => estadoVazio(termo ? 'Nenhum contato com essa busca' : 'Esta pasta está vazia', termo ? 'Tente outro nome ou telefone.' : 'Abra uma lista e use "Adicionar a uma pasta" para trazer contatos.', termo ? null : h('a', { class: 'btn btn-prim', href: '#/listas' }, 'Ver listas'), 'pasta'),
    aoCarregar: (res) => { resultado.textContent = res.total ? `${num(res.total)} contatos` : ''; },
  });
  aoSair(() => md.cancelar());
  const busca = h('input', { type: 'search', placeholder: 'Buscar nome, empresa ou telefone…', autocomplete: 'off', 'aria-label': 'Buscar na pasta' });
  busca.value = termo;
  function sincronizarHash() {
    const q = termo ? `?busca=${encodeURIComponent(termo)}` : '';
    history.replaceState(null, '', `#/pasta/${idUrl}${aba === 'resultados' ? '/resultados' : ''}${q}`);
  }
  busca.addEventListener('input', debounce(() => { termo = busca.value.trim(); sincronizarHash(); md.carregar(true); }, 300));
  const painelContatos = h('div', { class: 'aba-painel', id: 'painel-contatos', role: 'tabpanel', 'aria-labelledby': 'aba-contatos' },
    h('div', { class: 'barra-ferramentas' }, h('div', { class: 'combo combo-pilula busca-contatos' }, icone('busca', 18), busca)), resultado, md.el);
  const seg = segmentado({
    opcoes: [{ valor: 'contatos', rotulo: 'Contatos', id: 'aba-contatos', controla: 'painel-contatos' }, { valor: 'resultados', rotulo: 'Resultados', id: 'aba-res', controla: 'painel-res' }],
    valor: aba, rotulo: 'Visões da pasta', papel: 'tab', classe: 'seg-abas', aoMudar: (v) => definirAba(v),
  });
  function definirAba(a) { aba = a; painelContatos.hidden = a !== 'contatos'; painelRes.hidden = a !== 'resultados'; sincronizarHash(); }
  raiz.append(h('div', { class: 'abas-linha' }, seg.el), painelContatos, painelRes);
  definirAba(aba);
  md.carregar(true);
  carregarResultados();
}
