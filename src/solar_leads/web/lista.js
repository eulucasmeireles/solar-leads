'use strict';
/* Solar Leads - tela de uma lista (contatos em mestre-detalhe e visão geral) */

const SOLAR_JA_TEM = ['empresa já tem', 'outra unidade da empresa tem', 'outra empresa do dono tem'];
const mesmo = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
const tem = (arr, v) => arr.includes(v);
const sem = (arr, v) => arr.filter((x) => x !== v);
/* atalhos combináveis: cada um liga e desliga o próprio conjunto de filtros */
const ATALHOS_BASE = [
  { id: 'whats', rotulo: 'Com WhatsApp', dica: 'Só quem tem WhatsApp confirmado (pessoal ou comercial)', exigeWhats: true,
    ativo: (f) => mesmo(f.tipo_whatsapp, ['pessoal', 'business']), liga: (f) => { f.tipo_whatsapp = ['pessoal', 'business']; }, desliga: (f) => { f.tipo_whatsapp = []; } },
  { id: 'melhores', rotulo: 'Melhores contatos', dica: 'CNPJ ativo, contato forte, potencial 4 ou mais e sem energia solar',
    ativo: (f) => tem(f.situacao, 'ativa') && tem(f.faixa, 'A') && f.aptidao_min >= 4 && tem(f.solar, 'sem registro'),
    liga: (f) => { if (!tem(f.situacao, 'ativa')) f.situacao.push('ativa'); if (!tem(f.faixa, 'A')) f.faixa.push('A'); f.aptidao_min = Math.max(f.aptidao_min, 4); f.solar = ['sem registro']; },
    desliga: (f) => { f.situacao = sem(f.situacao, 'ativa'); f.faixa = sem(f.faixa, 'A'); f.aptidao_min = 0; f.solar = sem(f.solar, 'sem registro'); } },
  { id: 'casa', rotulo: 'Casa do dono', dica: 'Contatos em que a casa do dono é uma boa oportunidade',
    ativo: (f) => mesmo(f.oportunidade, ['Casa', 'Casa e empresa']), liga: (f) => { f.oportunidade = ['Casa', 'Casa e empresa']; }, desliga: (f) => { f.oportunidade = []; } },
  { id: 'pessoal', rotulo: 'WhatsApp pessoal', dica: 'Só números com WhatsApp pessoal', exigeWhats: true,
    ativo: (f) => mesmo(f.tipo_whatsapp, ['pessoal']), liga: (f) => { f.tipo_whatsapp = ['pessoal']; }, desliga: (f) => { f.tipo_whatsapp = []; } },
  { id: 'sem-solar', rotulo: 'Sem energia solar', dica: 'Sem registro na ANEEL',
    ativo: (f) => mesmo(f.solar, ['sem registro']), liga: (f) => { f.solar = ['sem registro']; }, desliga: (f) => { f.solar = []; } },
  { id: 'ja-tem', rotulo: 'Já têm solar', dica: 'A empresa, outra unidade ou outra empresa do dono já tem',
    ativo: (f) => mesmo(f.solar, SOLAR_JA_TEM), liga: (f) => { f.solar = SOLAR_JA_TEM.slice(); }, desliga: (f) => { f.solar = []; } },
  { id: 'comercial', rotulo: 'WhatsApp comercial', dica: 'Só números com WhatsApp comercial (conta business)', exigeWhats: true,
    ativo: (f) => mesmo(f.tipo_whatsapp, ['business']), liga: (f) => { f.tipo_whatsapp = ['business']; }, desliga: (f) => { f.tipo_whatsapp = []; } },
  { id: 'fora', rotulo: 'Fora de pastas', dica: 'Contatos que ainda não estão em nenhuma pasta',
    ativo: (f) => f.pasta === 'fora', liga: (f) => { f.pasta = 'fora'; }, desliga: (f) => { f.pasta = ''; } },
];
/* atalhos de solar somem no público "empresa"; "Melhores contatos" ali não usa potencial nem solar */
const ATALHOS_SO_DONO = ['casa', 'sem-solar', 'ja-tem'];
const MELHORES_EMPRESA = {
  dica: 'CNPJ ativo e contato forte',
  ativo: (f) => tem(f.situacao, 'ativa') && tem(f.faixa, 'A'),
  liga: (f) => { if (!tem(f.situacao, 'ativa')) f.situacao.push('ativa'); if (!tem(f.faixa, 'A')) f.faixa.push('A'); },
  desliga: (f) => { f.situacao = sem(f.situacao, 'ativa'); f.faixa = sem(f.faixa, 'A'); },
};
function atalhosDaLista(meta) {
  if (!publicoEmpresa(meta)) return ATALHOS_BASE.filter((a) => a.id !== 'comercial');
  return ATALHOS_BASE.filter((a) => !ATALHOS_SO_DONO.includes(a.id)).map((a) => (a.id === 'melhores' ? { ...a, ...MELHORES_EMPRESA } : a));
}
const ORDENS = [['relevancia', 'Mais relevantes'], ['aptidao', 'Maior potencial'], ['chance', 'Maior chance de ser o dono'], ['nome', 'Nome (A a Z)'], ['empresa', 'Empresa (A a Z)']];
const CHAVES_LISTA = ['faixa', 'situacao', 'papel', 'uf', 'cidade', 'bairro', 'atividade', 'tipo_whatsapp', 'solar', 'oportunidade'];

const filtrosPadrao = () => ({ busca: '', faixa: [], situacao: [], solar: [], tipo_whatsapp: [], papel: [], uf: [], cidade: [], bairro: [], atividade: [], oportunidade: [], pasta: '', aptidao_min: 0, com_email: false, so_disparo: true });
const copiaFiltros = (f) => JSON.parse(JSON.stringify(f));
const igualPadrao = (f) => JSON.stringify(f) === JSON.stringify(filtrosPadrao());

/* filtros, ordem e busca viajam no hash: #/lista/<id>?faixa=A&ordem=aptidao */
function filtrosDoHash(qs, meta) {
  const q = new URLSearchParams(qs || '');
  const f = filtrosPadrao();
  CHAVES_LISTA.forEach((k) => { f[k] = q.getAll(k); });
  const apt = Number(q.get('aptidao_min')); if (apt > 0 && apt <= 10) f.aptidao_min = Math.round(apt);
  f.com_email = q.get('com_email') === '1';
  f.so_disparo = q.get('so_disparo') !== '0';
  f.busca = q.get('busca') || '';
  f.pasta = q.get('pasta') || '';
  const ordem = limparOcultosDoPublico(meta, f, ORDENS.some(([v]) => v === q.get('ordem')) ? q.get('ordem') : 'relevancia');
  return { f, ordem };
}
function queryDoHash(f, ordem) {
  const q = new URLSearchParams(queryDosFiltros(f));
  if (q.get('so_disparo') === '1') q.delete('so_disparo');
  if (ordem && ordem !== 'relevancia') q.set('ordem', ordem);
  return q.toString();
}

/* ÚNICA função que monta a query de /contatos, /painel e /exportar */
function queryDosFiltros(f, extras = {}) {
  const q = new URLSearchParams();
  CHAVES_LISTA.forEach((k) => f[k].forEach((v) => q.append(k, v)));
  if (f.aptidao_min > 0) q.set('aptidao_min', String(f.aptidao_min));
  if (f.com_email) q.set('com_email', '1');
  q.set('so_disparo', f.so_disparo ? '1' : '0');
  if (f.busca.trim()) q.set('busca', f.busca.trim());
  if (f.pasta) q.set('pasta', f.pasta);
  Object.entries(extras).forEach(([k, v]) => q.set(k, String(v)));
  return q.toString();
}

const ROTULO_FILTRO = {
  faixa: (v) => FAIXA_ROTULO[v] || `Faixa ${v}`,
  situacao: (v) => titleCase(v || 'não informada'),
  solar: (v) => ROTULO_SOLAR[v] || titleCase(v),
  tipo_whatsapp: (v) => ({ pessoal: 'Pessoal', business: 'Comercial' })[v] || 'Não verificado',
  papel: (v) => (v ? titleCase(v) : 'Não informado'),
  oportunidade: (v) => v || 'Não informada',
  uf: (v) => v || 'Não informado',
  cidade: (v) => (v ? titleCase(v) : 'Não informada'),
  bairro: (v) => v || 'Não informado',
  atividade: (v) => v || 'Não informada',
};
const TITULO_FILTRO = { oportunidade: 'Onde está a oportunidade', faixa: 'Força do contato', situacao: 'Situação do CNPJ', solar: 'Energia solar', tipo_whatsapp: 'WhatsApp', papel: 'Papel do contato', uf: 'Estado', cidade: 'Cidade', bairro: 'Bairro', atividade: 'Atividade' };

/* ---------- entrada ---------- */
async function telaLista(raiz, id, subaba, qs) {
  document.title = 'Lista · Solar Leads';
  const rota = app.rota;
  raiz.append(h('div', { class: 'lista-cab', 'aria-busy': 'true' }, esqueleto('260px', '38px'), esqueleto('420px', '16px', 'mt'), esqueleto('100%', '90px', 'mt')));
  let meta;
  try { meta = await api(`/api/listas/${encodeURIComponent(id)}`); } catch (e) {
    if (rota !== app.rota) return;
    raiz.replaceChildren(h('a', { class: 'link-volta', href: '#/listas' }, icone('esquerda', 16), 'Listas'), estadoErro(e.status === 404 ? 'Não encontramos esta lista. Ela pode ter sido excluída.' : e.message, e.status === 404 ? null : () => rotear()));
    return;
  }
  if (rota !== app.rota) return;
  raiz.replaceChildren();
  document.title = `${nomeDaLista(meta)} · Solar Leads`;
  if (EM_ANDAMENTO(meta)) return viewGerando(raiz, meta, qs);
  if (meta.status === 'erro') return viewErro(raiz, meta);
  return montarLista(raiz, meta, subaba === 'painel' ? 'geral' : 'contatos', qs);
}

async function gerarDeNovo(meta) {
  const ok = await confirmar({ titulo: 'Gerar esta lista de novo?', texto: `Vamos criar uma nova ${rotuloLista(meta)} com as mesmas escolhas. A lista atual continua guardada.`, ok: 'Gerar de novo' });
  if (!ok) return;
  const p = meta.parametros || {};
  try {
    const r = await api('/api/listas', { method: 'POST', corpo: { uf: meta.uf, ...(meta.publico ? { publico: meta.publico } : {}), ...(listaPorNome(meta) ? { palavras: meta.palavras } : { cidade: meta.cidade }), cnaes: (meta.atividades || []).map((a) => a.codigo), so_ativas: !!p.so_ativas, incluir_mei: !!p.incluir_mei, so_locais: p.so_locais !== false, validar_whatsapp: !!p.validar_whatsapp } });
    location.hash = `#/lista/${encodeURIComponent(r.id)}`;
  } catch (e) { avisar(e.message, 'erro'); }
}

function cabecalhoDaLista(meta, acoes) {
  const r = meta.resumo;
  const partes = [nomeAtividades(meta), `gerada ${dataRelativa(meta.criada_em)}`];
  if (r) partes.push(`${num(r.total_de_empresas)} empresas analisadas`);
  partes.push(`Público: ${rotuloPublico(meta)}`);
  return h('header', { class: 'lista-cab' },
    h('a', { class: 'link-volta', href: '#/listas' }, icone('esquerda', 16), 'Listas'),
    h('div', { class: 'lista-cab-linha' },
      h('div', { class: 'lista-titulo' },
        h('h1', {}, nomeDaLista(meta), h('span', { class: 'uf', text: regiaoDaLista(meta) })),
        h('p', { class: 'apoio', text: partes.join(' · ') })),
      acoes ? h('div', { class: 'pag-acoes' }, acoes) : null));
}

function viewGerando(raiz, meta, qs) {
  const etapa = h('p', { class: 'gerando-etapa', 'aria-live': 'polite', text: meta.etapa || (meta.status === 'na_fila' ? 'Aguardando a vez na fila…' : 'Começando…') });
  const titulo = h('h2', { text: meta.status === 'na_fila' ? 'Sua lista está na fila' : 'Montando sua lista' });
  raiz.append(cabecalhoDaLista(meta), h('section', { class: 'cartao gerando' },
    h('span', { class: 'gerando-sol', 'aria-hidden': 'true' }, icone('sol', 30)), titulo, etapa, progresso(null, 'Gerando a lista'),
    h('p', { class: 'dica', text: 'Pode levar alguns minutos em cidades grandes. Você pode sair desta tela: a lista continua sendo gerada.' })));
  let vivo = true, t = null, falhas = 0;
  const cartao = raiz.querySelector('.gerando');
  async function ciclo() {
    try {
      const m = await api(`/api/listas/${encodeURIComponent(meta.id)}`);
      if (!vivo) return;
      falhas = 0;
      if (!EM_ANDAMENTO(m)) { raiz.replaceChildren(); document.title = `${nomeDaLista(m)} · Solar Leads`; return m.status === 'erro' ? viewErro(raiz, m) : montarLista(raiz, m, 'contatos', qs); }
      etapa.textContent = m.etapa || (m.status === 'na_fila' ? 'Aguardando a vez na fila…' : 'Gerando…');
      titulo.textContent = m.status === 'na_fila' ? 'Sua lista está na fila' : 'Montando sua lista';
    } catch (e) {
      if (!vivo) return;
      if (e.status === 404) { avisar('Esta lista foi apagada.', 'erro'); location.hash = '#/listas'; return; }
      falhas += 1;
      if (falhas >= 5) {
        cartao.replaceChildren(h('span', { class: 'vazio-ic' }, icone('alerta', 26)), h('h2', { text: 'Perdemos a conexão' }),
          h('p', { class: 'apoio', role: 'alert', text: `Não conseguimos saber como está a lista. ${e.message}` }),
          h('button', { class: 'btn btn-prim', type: 'button', onclick: () => rotear() }, icone('atualizar', 18), 'Tentar de novo'));
        return;
      }
    }
    if (vivo) t = setTimeout(ciclo, 2000);
  }
  t = setTimeout(ciclo, 2000);
  aoSair(() => { vivo = false; clearTimeout(t); });
}

function viewErro(raiz, meta) {
  raiz.append(cabecalhoDaLista(meta), h('div', { class: 'vazio vazio-erro', role: 'alert' },
    h('span', { class: 'vazio-ic' }, icone('alerta', 26)), h('h3', { text: 'Não deu para gerar esta lista' }),
    h('p', { text: meta.erro || 'Algo deu errado. Tente gerar de novo.' }),
    h('div', { class: 'linha-botoes' },
      h('button', { class: 'btn btn-prim', type: 'button', onclick: () => gerarDeNovo(meta) }, icone('atualizar', 18), 'Gerar de novo'),
      h('button', { class: 'btn btn-sec', type: 'button', onclick: () => excluirLista(meta, () => { location.hash = '#/listas'; }) }, icone('lixeira', 18), 'Excluir'))));
}

/* ---------- lista pronta ---------- */
const ICONE_GRUPO = { Contato: 'telefone', Empresa: 'predio', Dono: 'pessoa', 'Energia solar': 'sol', 'Qualificação': 'estrela' };
const OCULTOS_PERFIL = new Set(['chance_dono', 'aptidao_solar', 'potencial_casa', 'potencial_empresa', 'oportunidade']);
const OPORT = { 'Casa e empresa': { ic: 'casapredio', tom: 'verde' }, Casa: { ic: 'casa', tom: 'azul' }, Empresa: { ic: 'predio', tom: 'azul' }, 'Ampliação': { ic: 'raio', tom: 'laranja' }, 'Baixo potencial': { ic: null, tom: 'cinza' } };

async function montarLista(raiz, meta, abaInicial, qs) {
  const inicial = filtrosDoHash(qs, meta);
  const empresa = publicoEmpresa(meta);
  const ATALHOS = atalhosDaLista(meta);
  const ordens = ORDENS.filter(([v]) => !(empresa && ORDENS_SO_DONO.includes(v)));
  const L = {
    meta, f: inicial.f, ordem: inicial.ordem, pagina: 1, filtros: null,
    aba: abaInicial, painelVelho: true, pessoal: null, temWhats: false,
  };
  const idUrl = encodeURIComponent(meta.id);
  L.pastas = [];
  const buscaPainel = criarBuscador(), buscaPessoal = criarBuscador();
  aoSair(() => { buscaPainel.cancelar(); buscaPessoal.cancelar(); });

  /* --- cabeçalho --- */
  const btnExportar = h('a', { class: 'btn btn-prim', 'aria-disabled': 'true' }, icone('baixar', 18), h('span', { class: 'rot-exp', text: 'Exportar contatos' }));
  const menu = criarMenu(icone('pontos', 20), [
    { rotulo: 'Exportar tudo', nota: 'Todos os contatos prontos, sem filtros', icone: 'baixar', href: `/api/listas/${idUrl}/exportar` },
    { rotulo: 'Mudar público…', nota: 'Escolha para quem é esta lista', icone: 'pessoa', aoClicar: () => dialogoMudarPublico(meta) },
    { rotulo: 'Gerar de novo', nota: 'Cria outra lista com as mesmas escolhas', icone: 'atualizar', aoClicar: () => gerarDeNovo(meta) },
    { rotulo: 'Excluir lista', icone: 'lixeira', perigo: true, aoClicar: () => excluirLista(meta, () => { location.hash = '#/listas'; }) },
  ], { classeBotao: 'btn btn-sec btn-redondo', rotulo: 'Mais ações da lista' });
  const btnPasta = h('button', { class: 'btn btn-sec', type: 'button', disabled: true }, icone('pasta', 18), 'Adicionar a uma pasta');
  btnPasta.addEventListener('click', () => abrirAdicionar({}));
  raiz.append(cabecalhoDaLista(meta, [btnPasta, btnExportar, menu]));
  if (meta.aviso) raiz.append(h('div', { class: 'nota nota-laranja', role: 'note' }, icone('info', 18), h('span', { text: meta.aviso })));

  /* --- três números --- */
  const numeros = h('div', { class: 'tres-numeros', role: 'group', 'aria-label': 'Resumo dos contatos' });
  function desenharNumeros() {
    const r = md.S.res && md.S.res.resumo;
    const terceiro = ['Com WhatsApp confirmado', L.pessoal];
    const itens = [['Contatos', r ? r.contatos : null], ['Contatos fortes', r ? r.faixa_A : null], terceiro];
    numeros.replaceChildren(...itens.map(([rot, v]) => h('div', { class: 'numero-g' },
      v == null ? esqueleto('110px', '40px') : h('div', { class: 'numero-g-v', text: num(v) }), h('div', { class: 'numero-g-r', text: rot }),
      v === 0 && rot === 'Com WhatsApp confirmado' ? h('a', { class: 'link-peq', href: '#/whatsapp' }, 'Verificar números') : null)));
    numeros.classList.toggle('atualizando', Boolean(md.S.carregando && r));
  }
  raiz.append(numeros);

  /* --- atalhos (combináveis) e botão de filtros --- */
  const barraAtalhos = h('div', { class: 'atalhos', role: 'group', 'aria-label': 'Atalhos de filtro' });
  const btnFiltros = h('button', { class: 'btn btn-sec btn-filtros', type: 'button', disabled: true, 'aria-haspopup': 'dialog' }, icone('filtro', 18), 'Filtros', h('span', { class: 'cont', hidden: true }));
  const algoAtivo = () => !igualPadrao(L.f);
  function desenharAtalhos() {
    const temWhats = L.filtros && (L.filtros.tipo_whatsapp || []).some((o) => o.valor === 'pessoal' || o.valor === 'business');
    const botoes = ATALHOS.map((a) => {
      const on = a.ativo(L.f);
      const desativado = a.exigeWhats && L.filtros && !temWhats;
      return h('button', { class: 'atalho' + (on ? ' ativo' : ''), type: 'button', 'aria-pressed': String(on), disabled: desativado, title: desativado ? 'Ainda não há números com WhatsApp verificado nesta lista' : a.dica, onclick: () => alternarAtalho(a) }, a.rotulo);
    });
    if (algoAtivo()) botoes.push(h('button', { class: 'atalho atalho-limpar', type: 'button', onclick: limparTudo }, icone('x', 14), 'Limpar'));
    barraAtalhos.replaceChildren(...botoes);
  }
  function alternarAtalho(a) {
    if (a.ativo(L.f)) a.desliga(L.f); else a.liga(L.f);
    mudou({ sync: true });
  }
  raiz.append(barraAtalhos);

  /* --- busca e ordem --- */
  const busca = h('input', { type: 'search', id: 'busca-contatos', placeholder: 'Buscar nome, empresa ou telefone…', autocomplete: 'off', 'aria-label': 'Buscar nos contatos' });
  const seletorOrdem = h('select', { id: 'ordem', 'aria-label': 'Ordenar por' }, ordens.map(([v, r]) => h('option', { value: v, text: r })));
  busca.addEventListener('input', debounce(() => { L.f.busca = busca.value; mudou(); }, 300));
  busca.value = L.f.busca; seletorOrdem.value = L.ordem;
  seletorOrdem.addEventListener('change', () => { L.ordem = seletorOrdem.value; sincronizarHash(); carregar(true); });

  /* --- filtros (folha lateral) --- */
  const painelFiltros = h('aside', { class: 'filtros', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Filtros' });
  const corpoFiltros = h('div', { class: 'filtros-corpo' });
  const btnVer = h('button', { class: 'btn btn-prim', type: 'button' }, 'Ver contatos');
  let modalFiltros = null;
  painelFiltros.append(
    h('header', { class: 'filtros-cab' }, h('h2', { text: 'Filtros' }), h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar filtros', onclick: () => modalFiltros && modalFiltros.fechar() }, icone('x', 20))),
    corpoFiltros,
    h('footer', { class: 'filtros-rodape' }, h('button', { class: 'btn btn-sec', type: 'button', onclick: limparTudo }, 'Limpar'), btnVer));
  btnVer.addEventListener('click', () => modalFiltros && modalFiltros.fechar());
  btnFiltros.addEventListener('click', () => { modalFiltros = abrirModal(painelFiltros, { lateral: true }); });
  let sincs = [];

  function grupoPilulas(chave, opcoes) {
    const botoes = opcoes.map((o) => {
      const b = h('button', { type: 'button', class: 'pilula', 'aria-pressed': 'false' }, ROTULO_FILTRO[chave](o.valor), h('span', { class: 'pil-n', text: num(o.n) }));
      b.addEventListener('click', () => {
        const arr = L.f[chave];
        if (arr.includes(o.valor)) L.f[chave] = arr.filter((x) => x !== o.valor); else arr.push(o.valor);
        mudou();
      });
      return [b, o.valor];
    });
    sincs.push(() => botoes.forEach(([b, v]) => b.setAttribute('aria-pressed', String(L.f[chave].includes(v)))));
    return h('fieldset', { class: 'grupo' }, h('legend', { text: TITULO_FILTRO[chave] }), h('div', { class: 'pilulas' }, botoes.map(([b]) => b)));
  }
  function grupoLista(chave, opcoes) {
    const ul = h('ul', { class: 'opcoes-lista' });
    const inputs = [];
    opcoes.forEach((o) => {
      const idc = uid('f');
      const cb = h('input', { type: 'checkbox', id: idc });
      cb.addEventListener('change', () => {
        const arr = L.f[chave];
        if (cb.checked) { if (!arr.includes(o.valor)) arr.push(o.valor); } else L.f[chave] = arr.filter((x) => x !== o.valor);
        mudou();
      });
      inputs.push([cb, o.valor]);
      const rot = ROTULO_FILTRO[chave](o.valor);
      ul.append(h('li', { 'data-t': rot.toLowerCase() }, h('label', { for: idc }, cb, h('span', { class: 'opt-tx', text: rot }), h('span', { class: 'opt-n', text: num(o.n) }))));
    });
    sincs.push(() => inputs.forEach(([cb, v]) => { cb.checked = L.f[chave].includes(v); }));
    const b = h('input', { type: 'search', class: 'mini-busca', placeholder: `Buscar ${TITULO_FILTRO[chave].toLowerCase()}…`, 'aria-label': `Buscar ${TITULO_FILTRO[chave].toLowerCase()}`, autocomplete: 'off' });
    b.addEventListener('input', () => { const q = b.value.trim().toLowerCase(); ul.querySelectorAll('li').forEach((li) => { li.hidden = q && !li.dataset.t.includes(q); }); });
    return h('fieldset', { class: 'grupo' }, h('legend', { text: TITULO_FILTRO[chave] }), b, ul);
  }
  function grupoPasta() {
    const ops = [['', 'Todos'], ['fora', 'Fora de pastas'], ...L.pastas.map((p) => [String(p.id), p.nome])];
    const botoes = ops.map(([v, r]) => {
      const b = h('button', { type: 'button', class: 'pilula', 'aria-pressed': 'false', onclick: () => { L.f.pasta = v; mudou({ sync: true }); } }, icone(v ? 'pasta' : 'listas', 14), r);
      return [b, v];
    });
    sincs.push(() => botoes.forEach(([b, v]) => b.setAttribute('aria-pressed', String(L.f.pasta === v))));
    return h('fieldset', { class: 'grupo' }, h('legend', { text: 'Pastas' }), h('div', { class: 'pilulas' }, botoes.map(([b]) => b)));
  }
  function construirFiltros() {
    sincs = [];
    const fo = { ...(L.filtros || {}) };
    if (fo.faixa) fo.faixa = fo.faixa.slice().sort((a, b) => String(a.valor).localeCompare(String(b.valor)));
    const pil = (k) => ((fo[k] || []).length ? grupoPilulas(k, fo[k]) : null);
    const apt = h('input', { type: 'range', min: '0', max: '10', step: '1', value: String(L.f.aptidao_min), id: 'apt-min', 'aria-describedby': 'apt-saida' });
    const aptSaida = h('output', { id: 'apt-saida', for: 'apt-min' });
    const atualizarApt = () => { aptSaida.textContent = Number(apt.value) === 0 ? 'Qualquer nota' : `${apt.value} ou mais`; };
    const aplicaApt = debounce(() => { L.f.aptidao_min = Number(apt.value); mudou(); }, 300);
    apt.addEventListener('input', () => { atualizarApt(); aplicaApt(); });
    sincs.push(() => { apt.value = String(L.f.aptidao_min); atualizarApt(); });
    const swEmail = interruptor({ rotulo: 'Só com e-mail', checked: L.f.com_email, aoMudar: (v) => { L.f.com_email = v; mudou(); } });
    const swDisp = interruptor({ rotulo: 'Só contatos prontos para disparo', descricao: 'Esconde números que não devem receber mensagem.', checked: L.f.so_disparo, aoMudar: (v) => { L.f.so_disparo = v; mudou(); } });
    sincs.push(() => { swEmail.input.checked = L.f.com_email; swDisp.input.checked = L.f.so_disparo; });
    atualizarApt();
    const comWhats = (fo.tipo_whatsapp || []).some((o) => o.valor === 'pessoal' || o.valor === 'business');
    corpoFiltros.replaceChildren(
      empresa ? null : pil('oportunidade'), pil('faixa'), grupoPasta(), empresa ? null : pil('solar'), comWhats ? pil('tipo_whatsapp') : null, pil('situacao'), pil('papel'),
      (fo.uf || []).length > 1 ? grupoLista('uf', fo.uf) : null, (fo.cidade || []).length > 1 ? grupoLista('cidade', fo.cidade) : null,
      empresa ? null : h('fieldset', { class: 'grupo' }, h('legend', { text: 'Potencial mínimo' }), h('div', { class: 'slider' }, apt, aptSaida), h('p', { class: 'dica', text: 'Nota de 0 a 10 do potencial para energia solar.' })),
      (fo.bairro || []).length ? grupoLista('bairro', fo.bairro) : null, (fo.atividade || []).length ? grupoLista('atividade', fo.atividade) : null,
      h('fieldset', { class: 'grupo' }, h('legend', { text: 'Outros' }), h('div', { class: 'grupo-linhas' }, swEmail.el, swDisp.el)));
    sincs.forEach((f) => f());
    btnFiltros.disabled = false;
  }
  function limparTudo() {
    L.f = filtrosPadrao();
    mudou({ sync: true });
  }

  /* --- chips ativos --- */
  const zonaChips = h('div', { class: 'chips-ativos', 'aria-label': 'Filtros ativos' });
  function desenharChips() {
    const cs = [];
    CHAVES_LISTA.forEach((k) => L.f[k].forEach((v) => cs.push(chip(ROTULO_FILTRO[k](v), () => { L.f[k] = L.f[k].filter((x) => x !== v); mudou({ sync: true }); }, `Remover filtro ${ROTULO_FILTRO[k](v)}`))));
    if (L.f.pasta) cs.push(chip(L.f.pasta === 'fora' ? 'Fora de pastas' : `Pasta: ${(L.pastas.find((p) => String(p.id) === L.f.pasta) || {}).nome || L.f.pasta}`, () => { L.f.pasta = ''; mudou({ sync: true }); }, 'Remover filtro de pasta'));
    if (L.f.aptidao_min > 0) cs.push(chip(`Potencial ${L.f.aptidao_min}+`, () => { L.f.aptidao_min = 0; mudou({ sync: true }); }));
    if (L.f.com_email) cs.push(chip('Só com e-mail', () => { L.f.com_email = false; mudou({ sync: true }); }));
    if (!L.f.so_disparo) cs.push(chip('Inclui fora do disparo', () => { L.f.so_disparo = true; mudou({ sync: true }); }));
    if (L.f.busca.trim()) cs.push(chip(`“${L.f.busca.trim()}”`, () => { L.f.busca = ''; busca.value = ''; mudou({ sync: true }); }, 'Remover busca'));
    zonaChips.replaceChildren(...cs, cs.length ? h('button', { class: 'link-btn', type: 'button', onclick: limparTudo }, 'Limpar tudo') : null);
    zonaChips.hidden = !cs.length;
    const c = btnFiltros.querySelector('.cont');
    c.textContent = String(cs.length); c.hidden = !cs.length;
    btnFiltros.setAttribute('aria-label', cs.length ? `Filtros, ${cs.length} ativos` : 'Filtros');
  }

  /* --- mestre-detalhe (componente compartilhado com as pastas) --- */
  const resultado = h('p', { class: 'resultado', 'aria-live': 'polite' });
  const md = criarMestreDetalhe({
    local: listaPorNome(meta), publico: meta.publico,
    url: (pagina) => `/api/listas/${idUrl}/contatos?${queryDosFiltros(L.f, { ordem: L.ordem, pagina, por_pagina: 50 })}`,
    perfil: { local: listaPorNome(meta), aoAdicionarPasta: (l, ctx) => { if (ctx && ctx.fechar) ctx.fechar(); abrirAdicionar({ telefones: [l.telefone] }); } },
    vazio: () => estadoVazio('Nenhum contato com esses filtros', 'Afrouxe algum filtro para ver mais contatos.', h('button', { class: 'btn btn-sec', type: 'button', onclick: limparTudo }, 'Limpar filtros')),
    aoMudar: () => { desenharNumeros(); atualizarExportar(); },
    aoCarregar: (res) => { resultado.textContent = res.total ? `${num(res.total)} contatos` : ''; },
  });
  const carregar = (reset) => md.carregar(reset);
  aoSair(() => md.cancelar());

  function atualizarExportar() {
    const total = md.S.res ? md.S.res.total : null;
    const rot = btnExportar.querySelector('.rot-exp');
    btnExportar.removeAttribute('href');
    if (total == null) { rot.textContent = 'Exportar contatos'; btnExportar.setAttribute('aria-disabled', 'true'); }
    else if (total === 0) { rot.textContent = 'Nenhum contato para exportar'; btnExportar.setAttribute('aria-disabled', 'true'); }
    else {
      rot.textContent = `Exportar ${num(total)} ${total === 1 ? 'contato' : 'contatos'}`;
      btnExportar.removeAttribute('aria-disabled');
      btnExportar.href = `/api/listas/${idUrl}/exportar?${queryDosFiltros(L.f)}`;
    }
    btnPasta.disabled = !total;
    btnVer.textContent = total == null ? 'Ver contatos' : total === 0 ? 'Nenhum contato' : `Ver ${num(total)} ${total === 1 ? 'contato' : 'contatos'}`;
  }
  function abrirAdicionar({ telefones }) {
    folhaAdicionarPasta({
      listaId: meta.id, total: md.S.res ? md.S.res.total : 0, totalComWhats: L.pessoal, telefones: telefones || null,
      queryFiltros: () => queryDosFiltros(L.f, { ordem: L.ordem }),
      aoConcluir: () => mudou({ manter: true }),
    });
  }
  function filtroConfirmados() {
    const f = copiaFiltros(L.f);
    const base = ['pessoal', 'business'];
    f.tipo_whatsapp = f.tipo_whatsapp.length ? f.tipo_whatsapp.filter((v) => base.includes(v)) : base;
    return f;
  }
  async function carregarPessoal() {
    const f = filtroConfirmados();
    if (!L.temWhats || !f.tipo_whatsapp.length) { L.pessoal = 0; desenharNumeros(); return; }
    try { const r = await buscaPessoal(`/api/listas/${idUrl}/contatos?${queryDosFiltros(f, { por_pagina: 1 })}`); L.pessoal = r.total; desenharNumeros(); } catch (e) { /* mantém o anterior */ }
  }
  /* exportar: avisa quando parte do filtro não tem WhatsApp confirmado */
  btnExportar.addEventListener('click', async (ev) => {
    if (btnExportar.getAttribute('aria-disabled') === 'true') { ev.preventDefault(); return; }
    ev.preventDefault();
    const total = md.S.res ? md.S.res.total : 0;
    let conf = 0;
    const f = filtroConfirmados();
    if (L.temWhats && f.tipo_whatsapp.length) {
      try { conf = (await api(`/api/listas/${idUrl}/contatos?${queryDosFiltros(f, { por_pagina: 1 })}`)).total; } catch (e) { conf = L.pessoal || 0; }
    }
    const todos = `/api/listas/${idUrl}/exportar?${queryDosFiltros(L.f)}`;
    if (conf >= total) { location.href = todos; return; }
    dialogoExportar({ total, conf, todos, soConfirmados: `/api/listas/${idUrl}/exportar?${queryDosFiltros(f)}` });
  });
  const painelEl = h('div', { class: 'painel-area' });
  async function carregarPainel() {
    L.painelVelho = false;
    painelEl.classList.add('atualizando');
    if (!painelEl.children.length) painelEl.replaceChildren(esqueletoPainel());
    try {
      const p = await buscaPainel(`/api/listas/${idUrl}/painel?${queryDosFiltros(L.f)}`);
      painelEl.classList.remove('atualizando'); painelEl.replaceChildren(montarPainel(p, empresa));
    } catch (e) {
      if (ehAborto(e)) return;
      L.painelVelho = true; painelEl.classList.remove('atualizando');
      painelEl.replaceChildren(estadoErro(e.status === 404 ? 'A visão geral ainda não está disponível neste servidor.' : e.message, carregarPainel));
    }
  }
  function mudou(op = {}) {
    if (op.sync) { sincs.forEach((f) => f()); busca.value = L.f.busca; }
    sincronizarHash();
    L.painelVelho = true;
    desenharAtalhos(); desenharChips();
    carregar(true); carregarPessoal();
    if (L.aba === 'geral') carregarPainel();
  }

  /* --- abas --- */
  const ordemEl = h('label', { class: 'ordem', for: 'ordem' }, h('span', { class: 'so-leitor', text: 'Ordenar por' }), seletorOrdem);
  const barraFerramentas = h('div', { class: 'barra-ferramentas' },
    h('div', { class: 'combo combo-pilula busca-contatos' }, icone('busca', 18), busca), ordemEl, btnFiltros);
  const painelContatos = h('div', { class: 'aba-painel', id: 'painel-contatos', role: 'tabpanel', 'aria-labelledby': 'aba-contatos' },
    resultado, md.el);
  const painelGeral = h('div', { class: 'aba-painel', id: 'painel-geral', role: 'tabpanel', 'aria-labelledby': 'aba-geral', hidden: true }, painelEl);
  const seg = segmentado({
    opcoes: [{ valor: 'contatos', rotulo: 'Contatos', id: 'aba-contatos', controla: 'painel-contatos' }, { valor: 'geral', rotulo: 'Visão geral', id: 'aba-geral', controla: 'painel-geral' }],
    valor: L.aba, rotulo: 'Visões da lista', papel: 'tab', classe: 'seg-abas', aoMudar: (v) => definirAba(v),
  });
  function sincronizarHash() {
    const q = queryDoHash(L.f, L.ordem);
    history.replaceState(null, '', `#/lista/${idUrl}${L.aba === 'geral' ? '/painel' : ''}${q ? '?' + q : ''}`);
  }
  function definirAba(a) {
    L.aba = a;
    painelContatos.hidden = a !== 'contatos'; painelGeral.hidden = a !== 'geral';
    ordemEl.hidden = a === 'geral';
    sincronizarHash();
    if (a === 'geral' && (L.painelVelho || !painelEl.children.length)) carregarPainel();
  }
  raiz.append(h('div', { class: 'abas-linha' }, seg.el), barraFerramentas, zonaChips, painelContatos, painelGeral);
  desenharNumeros(); desenharAtalhos(); desenharChips(); atualizarExportar();
  definirAba(L.aba);
  carregarCampos().then(() => md.redesenhar());

  carregar(true);
  try {
    L.filtros = await api(`/api/listas/${idUrl}/filtros`);
    try { L.pastas = await api('/api/pastas'); } catch (e) { L.pastas = []; }
    L.temWhats = (L.filtros.tipo_whatsapp || []).some((o) => o.valor === 'pessoal' || o.valor === 'business');
    L.pessoal = (L.filtros.tipo_whatsapp || []).filter((o) => o.valor === 'pessoal' || o.valor === 'business').reduce((s, o) => s + o.n, 0);
  } catch (e) { L.filtros = {}; avisar('Não foi possível carregar as opções de filtro.', 'erro'); }
  construirFiltros(); desenharAtalhos(); carregarPessoal();
}

/* ---------- valores do perfil, montados a partir de /api/campos ---------- */
function elementoValor(tipo, v) {
  const t = String(v);
  switch (tipo) {
    case 'lista': return h('ul', { class: 'det-lista' }, t.split(';').map((x) => x.trim()).filter(Boolean).map((x) => h('li', { text: x })));
    case 'telefone': return document.createTextNode(telBR(t));
    case 'cnpj': return document.createTextNode(cnpjBR(t));
    case 'data': return document.createTextNode(dataBR(t));
    case 'dinheiro': return document.createTextNode(dinheiroBR(t));
    case 'numero': return document.createTextNode(Number.isFinite(Number(t)) ? num(t) : t);
    case 'simnao': return document.createTextNode(/^(true|1|sim)$/i.test(t) ? 'Sim' : /^(false|0|não|nao)$/i.test(t) ? 'Não' : t);
    case 'link': {
      const seg = /^(https?:\/\/|mailto:)/i.test(t) ? t : (/^[\w.+-]+@[\w-]+\.[\w.-]+$/.test(t) ? 'mailto:' + t : (/^www\./i.test(t) ? 'https://' + t : ''));
      return seg ? h('a', { href: seg, target: '_blank', rel: 'noopener noreferrer', text: t }) : document.createTextNode(t);
    }
    default: return document.createTextNode(t);
  }
}
const vazio = (v) => v == null || String(v).trim() === '';

function gruposDoDetalhe(linha, empresa) {
  const grupos = new Map(), conhecidas = new Set();
  // Já aparecem no topo do perfil (nome, selo e anéis); repetir embaixo só polui.
  const noTopo = new Set(['primeiro_nome', 'sobrenome', 'faixa', 'chance_dono', 'aptidao_solar']);
  for (const c of app.campos || []) {
    conhecidas.add(c.chave);
    if (vazio(linha[c.chave]) || noTopo.has(c.chave) || (empresa && c.so_dono)) continue;
    const g = c.grupo || 'Outros dados';
    if (!grupos.has(g)) grupos.set(g, []);
    grupos.get(g).push({ chave: c.chave, rotulo: c.rotulo || c.chave, tipo: c.tipo || 'texto', valor: linha[c.chave] });
  }
  for (const k of Object.keys(linha)) {
    if (conhecidas.has(k) || vazio(linha[k]) || (empresa && campoSoDono(k))) continue;
    if (!grupos.has('Outros dados')) grupos.set('Outros dados', []);
    grupos.get('Outros dados').push({ chave: k, rotulo: k, tipo: 'texto', valor: linha[k] });
  }
  return grupos;
}

/* ---------- resultados dos disparos ---------- */
const RES = { opcoes: null, mapa: {} };
const OPCOES_PADRAO = [{ valor: 'interessado', rotulo: 'Interessado' }, { valor: 'respondeu', rotulo: 'Respondeu' }, { valor: 'sem_interesse', rotulo: 'Não tem interesse' }, { valor: 'nao_e_o_dono', rotulo: 'Não é o dono' }, { valor: 'nao_entregue', rotulo: 'Não entregue' }, { valor: 'sem_resposta', rotulo: 'Sem resposta' }];
async function opcoesResultado() {
  if (!RES.opcoes) {
    try { RES.opcoes = await api('/api/resultados/opcoes'); } catch (e) { RES.opcoes = OPCOES_PADRAO; }
    RES.mapa = Object.fromEntries(RES.opcoes.map((o) => [o.valor, o.rotulo]));
  }
  return RES.opcoes;
}
function corResultado(valor, rotulo) {
  const n = `${valor} ${rotulo || ''}`.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/_/g, ' ');
  if (/entreg/.test(n)) return 'vermelho';
  if (/(dono|interesse)/.test(n) && /nao|sem/.test(n)) return 'laranja';
  if (/sem resposta/.test(n)) return 'cinza';
  if (/interessad/.test(n)) return 'verde';
  if (/respondeu/.test(n)) return 'azul';
  return 'cinza';
}
const rotuloResultado = (v) => RES.mapa[v] || String(v).replace(/_/g, ' ');

/* ---------- mestre-detalhe de contatos (usado na lista e nas pastas) ---------- */
function sinaisDoContato(l, empresa) {
  const out = [];
  if (l.tipo_whatsapp === 'pessoal') out.push(h('span', { class: 'sinal sinal-whats verde', title: 'WhatsApp confirmado · conta pessoal', role: 'img', 'aria-label': 'WhatsApp confirmado, conta pessoal' }, icone('whatsapp', 15)));
  else if (l.tipo_whatsapp === 'business') out.push(h('span', { class: 'sinal sinal-whats azul', title: 'WhatsApp confirmado · conta comercial', role: 'img', 'aria-label': 'WhatsApp confirmado, conta comercial' }, icone('whatsapp', 15)));
  out.push(seloFaixa(l.faixa));
  const o = empresa ? null : OPORT[l.oportunidade];
  if (o && o.ic) out.push(h('span', { class: 'sinal sinal-oport', title: `Oportunidade: ${l.oportunidade}`, role: 'img', 'aria-label': `Oportunidade: ${l.oportunidade}` }, icone(o.ic, 16)));
  if (l.resultado) { const r = rotuloResultado(l.resultado); out.push(h('span', { class: `dot-res dot-${corResultado(l.resultado, r)}`, title: `Resultado: ${r}`, role: 'img', 'aria-label': `Resultado: ${r}` })); }
  const ps = Array.isArray(l.pastas) ? l.pastas : [];
  if (ps.length) { const nomes = ps.map((p) => p.nome).join(', '); out.push(h('span', { class: 'sinal sinal-pasta', title: `Em pasta: ${nomes}`, role: 'img', 'aria-label': `Em pasta: ${nomes}` }, icone('pasta', 16))); }
  return h('span', { class: 'lc-sinais' }, out);
}

function criarMestreDetalhe(opc) {
  const S = { linhas: [], sel: 0, pagina: 1, fim: false, carregando: false, res: null, modal: null };
  const buscar = criarBuscador();
  const estreito = matchMedia('(max-width: 899px)');
  const listaEl = h('div', { class: 'md-lista', role: 'list', 'aria-label': 'Contatos' });
  const rodape = h('div', { class: 'md-rodape' });
  const perfilEl = h('aside', { class: 'md-perfil', 'aria-label': 'Perfil do contato' });
  const el = h('div', { class: 'md' }, h('div', { class: 'md-col' }, listaEl, rodape), perfilEl);
  let observador = null;
  /* público do contato: o da lista (opc.publico) ou, nas pastas, o que o contato traz */
  const ehEmpresa = (l) => (opc.publico ? opc.publico === 'empresa' : publicoEmpresa(l));
  const ctx = () => ({ fechar: () => S.modal && S.modal.fechar(), remover: (l) => remover(l) });

  function linha(l, i) {
    return h('button', { class: 'linha-contato' + (i === S.sel ? ' sel' : ''), type: 'button', role: 'listitem', 'aria-current': i === S.sel ? 'true' : null, onclick: () => selecionar(i, true) },
      avatar(l), h('span', { class: 'lc-tx' }, h('span', { class: 'lc-nome', text: nomeCompleto(l) || 'Contato sem nome' }), h('span', { class: 'lc-emp', text: l.empresa || l.razao_social || '' }), opc.local && localDoContato(l) ? h('span', { class: 'lc-loc', text: localDoContato(l) }) : null), sinaisDoContato(l, ehEmpresa(l)));
  }
  function desenharLista() {
    if (observador) observador.disconnect();
    if (!S.res) {
      listaEl.replaceChildren(...Array.from({ length: 7 }, () => h('div', { class: 'linha-contato', 'aria-hidden': 'true' }, esqueleto('44px', '44px', 'redondo'), h('span', { class: 'lc-tx' }, esqueleto('55%', '14px'), esqueleto('75%', '12px')))));
      rodape.replaceChildren(); return;
    }
    if (!S.linhas.length) { listaEl.replaceChildren(opc.vazio()); rodape.replaceChildren(); return; }
    listaEl.replaceChildren(...S.linhas.map(linha));
    if (S.fim) { rodape.replaceChildren(h('p', { class: 'apoio-peq', text: 'Fim da lista.' })); return; }
    const mais = h('button', { class: 'btn btn-sec', type: 'button', onclick: () => carregar(false) }, S.carregando ? h('i', { class: 'giro', 'aria-hidden': 'true' }) : null, 'Carregar mais');
    rodape.replaceChildren(mais);
    if ('IntersectionObserver' in window) {
      observador = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting) && !S.carregando && !S.fim) carregar(false); }, { rootMargin: '300px' });
      observador.observe(mais);
    }
  }
  function selecionar(i, clique) {
    S.sel = i;
    [...listaEl.children].forEach((e, k) => { e.classList.toggle('sel', k === i); if (k === i) e.setAttribute('aria-current', 'true'); else e.removeAttribute('aria-current'); });
    if (clique && estreito.matches) abrirFolha(S.linhas[i]); else desenharPerfil();
  }
  function desenharPerfil() {
    const l = S.linhas[S.sel];
    perfilEl.hidden = estreito.matches;
    if (!l) {
      perfilEl.replaceChildren(S.res ? estadoVazio('Selecione um contato', 'O perfil aparece aqui.', null, 'pessoa') : h('div', { class: 'sk-pilha' }, esqueleto('72px', '72px', 'redondo'), esqueleto('50%', '22px'), esqueleto('80%', '14px'), esqueleto('100%', '120px')));
      return;
    }
    perfilEl.replaceChildren(perfilContato(l, opc.perfil || {}, ctx(), ehEmpresa(l)));
    perfilEl.scrollTop = 0;
  }
  function abrirFolha(l) {
    if (!l) return;
    const painel = h('div', { class: 'folha folha-perfil', role: 'dialog', 'aria-modal': 'true', 'aria-label': `Perfil de ${nomeCompleto(l) || 'contato'}`, tabindex: '-1' },
      h('button', { class: 'btn-icone folha-fechar', type: 'button', 'aria-label': 'Fechar perfil', 'data-foco': '', onclick: () => S.modal && S.modal.fechar() }, icone('x', 20)), perfilContato(l, opc.perfil || {}, ctx(), ehEmpresa(l)));
    S.modal = abrirModal(painel, { baixo: true, aoFechar: () => { S.modal = null; } });
  }
  estreito.addEventListener('change', desenharPerfil);
  async function carregar(reset) {
    if (reset) { S.pagina = 1; S.linhas = []; S.sel = 0; S.fim = false; S.res = null; desenharLista(); desenharPerfil(); }
    S.carregando = true; if (opc.aoMudar) opc.aoMudar();
    try {
      const r = await buscar(opc.url(S.pagina));
      S.res = r; S.linhas.push(...r.linhas); S.fim = !r.linhas.length || S.linhas.length >= r.total; S.pagina += 1;
    } catch (e) {
      if (ehAborto(e)) return;
      S.carregando = false; if (opc.aoMudar) opc.aoMudar();
      listaEl.replaceChildren(estadoErro(e.message, () => carregar(true))); return;
    }
    S.carregando = false;
    desenharLista(); if (S.pagina === 2) desenharPerfil();
    if (opc.aoMudar) opc.aoMudar();
    if (opc.aoCarregar) opc.aoCarregar(S.res);
  }
  function remover(l) {
    const i = S.linhas.indexOf(l);
    if (i < 0) return;
    S.linhas.splice(i, 1);
    if (S.res) S.res.total = Math.max(0, S.res.total - 1);
    S.sel = Math.min(S.sel, Math.max(0, S.linhas.length - 1));
    desenharLista(); desenharPerfil();
    if (opc.aoMudar) opc.aoMudar();
    if (opc.aoCarregar) opc.aoCarregar(S.res);
  }
  function cancelar() { buscar.cancelar(); if (observador) observador.disconnect(); estreito.removeEventListener('change', desenharPerfil); }
  return { el, S, carregar, cancelar, remover, redesenhar: () => { desenharLista(); desenharPerfil(); } };
}

function linhaWhats(l) {
  const [cls, txt] = l.tipo_whatsapp === 'pessoal' ? ['verde', 'WhatsApp confirmado · conta pessoal']
    : l.tipo_whatsapp === 'business' ? ['azul', 'WhatsApp confirmado · conta comercial'] : ['cinza', 'WhatsApp ainda não verificado'];
  return h('p', { class: `pf-whats ${cls}` }, icone(cls === 'cinza' ? 'info' : 'whatsapp', 16), txt);
}

/* perfil do contato: anéis, oportunidade, pastas e grupos de /api/campos */
/* até 3 fatos da empresa, só com o que já vem nos dados do contato */
function fatosDaEmpresa(l) {
  const fatos = [];
  const idade = Number(l.idade_empresa);
  if (!vazio(l.idade_empresa) && Number.isFinite(idade)) fatos.push({ v: num(idade), r: idade === 1 ? 'ano de empresa' : 'anos de empresa' });
  const socios = Number(l.numero_socios);
  if (!vazio(l.numero_socios) && Number.isFinite(socios) && socios > 0) fatos.push({ v: num(socios), r: socios === 1 ? 'sócio' : 'sócios' });
  const ativas = Number(l.empresas_ativas_do_dono);
  if (!vazio(l.empresas_ativas_do_dono) && Number.isFinite(ativas) && ativas > 1) fatos.push({ v: num(ativas), r: 'empresas ativas do dono' });
  /* dois números ao lado do anel: cabem numa linha só; o porte continua na seção Empresa */
  return fatos.slice(0, 2);
}

function perfilContato(l, opc, ctx, empresa) {
  const nome = nomeCompleto(l), dig = soDigitos(l.telefone);
  const chance = Number(l.chance_dono) || 0;
  const casa = Number(l.potencial_casa), emp = Number(l.potencial_empresa), apt = Number(l.aptidao_solar);
  const o = empresa ? null : OPORT[l.oportunidade];
  const grupos = [];
  gruposDoDetalhe(l, empresa).forEach((itens, g) => {
    const linhas = itens.filter((it) => !OCULTOS_PERFIL.has(it.chave)).map((it) => h('div', { class: 'ir' },
      h('dt', { text: it.rotulo }), h('dd', {}, it.chave === 'faixa' ? (FAIXA_ROTULO[it.valor] || it.valor) : elementoValor(it.tipo, it.valor))));
    if (linhas.length) grupos.push(h('section', { class: 'pf-grupo' }, h('h3', {}, icone(ICONE_GRUPO[g] || 'info', 18), g), h('dl', { class: 'ir-grupo' }, linhas)));
  });
  const aneis = [h('div', { class: 'pf-anel' }, anel({ valor: chance, texto: `${chance}%`, cor: '--verde', tam: 76, espessura: 7, rotulo: `Chance de ser o dono: ${chance}%` }), h('span', { text: 'Chance de ser o dono' }))];
  if (empresa) fatosDaEmpresa(l).forEach((f) => aneis.push(h('div', { class: 'pf-anel' }, h('span', { class: 'pf-fato-v' + (f.txt ? ' txt' : ''), text: f.v }), h('span', { text: f.r }))));
  if (!empresa && Number.isFinite(casa)) aneis.push(h('div', { class: 'pf-anel' }, anel({ valor: casa * 10, texto: String(casa), cor: '--acento', tam: 76, espessura: 7, rotulo: `Potencial da casa: ${casa} de 10` }), h('span', { text: 'Potencial da casa' })));
  if (!empresa && Number.isFinite(emp)) aneis.push(h('div', { class: 'pf-anel' }, anel({ valor: emp * 10, texto: String(emp), cor: '--laranja', tam: 76, espessura: 7, rotulo: `Potencial da empresa: ${emp} de 10` }), h('span', { text: 'Potencial da empresa' })));
  if (!empresa && !Number.isFinite(casa) && !Number.isFinite(emp) && Number.isFinite(apt)) aneis.push(h('div', { class: 'pf-anel' }, anel({ valor: apt * 10, texto: String(apt), cor: '--acento', tam: 76, espessura: 7, rotulo: `Potencial: ${apt} de 10` }), h('span', { text: 'Potencial' })));
  const ps = Array.isArray(l.pastas) ? l.pastas : [];
  const linhaPastas = (ps.length || opc.aoAdicionarPasta) ? h('div', { class: 'pf-pastas' },
    h('span', { class: 'pf-pastas-t' }, icone('pasta', 16), 'Pastas'),
    h('div', { class: 'chips' }, ps.length ? ps.map((p) => h('a', { class: 'chip chip-pasta', href: `#/pasta/${encodeURIComponent(p.id)}` }, h('span', { class: 'chip-tx', text: p.nome }))) : h('span', { class: 'apoio-peq', text: 'Em nenhuma pasta' })),
    opc.aoAdicionarPasta ? h('button', { class: 'link-btn', type: 'button', onclick: () => opc.aoAdicionarPasta(l, ctx) }, 'Adicionar a uma pasta') : null) : null;
  const el = h('div', { class: 'perfil' },
    h('header', { class: 'pf-cab' }, avatar(l, true),
      h('div', {}, h('h2', { text: nome || 'Contato sem nome' }), linhaWhats(l), h('p', { class: 'apoio', text: [l.qualificacao_dono, l.empresa || l.razao_social].filter(Boolean).join(' · ') }),
        opc.local && localDoContato(l) ? h('p', { class: 'pf-local' }, icone('pin', 14), localDoContato(l)) : null,
        h('div', { class: 'pf-selos' }, seloFaixa(l.faixa, true), o ? selo(l.oportunidade, o.tom, { icone: o.ic }) : null))),
    h('div', { class: 'pf-acoes' },
      dig ? h('a', { class: 'btn btn-prim', href: `https://wa.me/${dig}`, target: '_blank', rel: 'noopener noreferrer' }, icone('whatsapp', 18), 'Abrir no WhatsApp') : null,
      h('button', { class: 'btn btn-cinza', type: 'button', disabled: !l.telefone, onclick: async () => avisar(await copiarTexto(telBR(l.telefone)) ? 'Telefone copiado.' : 'Não foi possível copiar.') }, icone('copiar', 18), 'Copiar telefone'),
      opc.acoesExtra ? opc.acoesExtra(l, ctx) : null),
    h('div', { class: 'pf-aneis' }, aneis),
    opc.blocoExtra ? opc.blocoExtra(l, ctx) : null,
    linhaPastas, grupos);
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('pronto')));
  return el;
}

/* ---------- diálogo de exportação com aviso de WhatsApp ---------- */
function dialogoExportar({ total, conf, todos, soConfirmados }) {
  const idT = uid('dlg');
  const nenhum = conf === 0;
  const fora = total - conf;
  const baixar = (url) => { m.fechar(); location.href = url; };
  const acoes = nenhum
    ? [h('a', { class: 'btn btn-prim', href: '#/whatsapp', onclick: () => m.fechar() }, 'Ir para a verificação'),
      h('button', { class: 'btn btn-sec', type: 'button', onclick: () => baixar(todos) }, 'Exportar mesmo assim')]
    : [h('button', { class: 'btn btn-prim', type: 'button', 'data-foco': '', onclick: () => baixar(soConfirmados) }, `Exportar só os confirmados (${num(conf)})`),
      h('button', { class: 'btn btn-sec', type: 'button', onclick: () => baixar(todos) }, `Exportar todos (${num(total)})`)];
  const painel = h('div', { class: 'dialogo', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': idT, 'aria-describedby': idT + '-d', tabindex: '-1' },
    h('h2', { id: idT, text: nenhum ? `Nenhum dos ${num(total)} contatos tem WhatsApp confirmado.` : `${num(fora)} dos ${num(total)} contatos ainda não têm WhatsApp confirmado.` }),
    h('p', { id: idT + '-d', text: nenhum ? 'Verifique os números antes de exportar para disparar só para quem tem WhatsApp.' : 'Para o disparo, o melhor é exportar só quem já tem WhatsApp confirmado.' }),
    h('div', { class: 'dialogo-acoes dialogo-acoes-col' }, acoes, h('button', { class: 'btn btn-texto', type: 'button', onclick: () => m.fechar() }, 'Cancelar')));
  const m = abrirModal(painel);
}

/* ---------- mudar o público de uma lista pronta ---------- */
function dialogoMudarPublico(meta) {
  const idT = uid('dlg');
  const atual = publicoEmpresa(meta) ? 'empresa' : 'dono';
  const escolha = opcoesPublico({ valor: atual });
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const aplicar = h('button', { class: 'btn btn-prim', type: 'button' }, 'Aplicar');
  const cancelar = h('button', { class: 'btn btn-sec', type: 'button', 'data-foco': '', onclick: () => m.fechar() }, 'Cancelar');
  aplicar.addEventListener('click', async () => {
    const novo = escolha.valor();
    if (novo === atual) { m.fechar(); return; }
    aplicar.disabled = true; cancelar.disabled = true; erro.textContent = '';
    aplicar.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Aplicando…');
    try {
      await api(`/api/listas/${encodeURIComponent(meta.id)}`, { method: 'PATCH', corpo: { publico: novo } });
      avisar('Público da lista alterado.');
      rotear();
    } catch (e) {
      erro.textContent = e.status === 409 ? 'Esta lista ainda não está pronta. Espere terminar de gerar.' : e.message;
      aplicar.disabled = false; cancelar.disabled = false; aplicar.replaceChildren('Aplicar');
    }
  });
  const painel = h('div', { class: 'dialogo', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('h2', { id: idT, text: 'Mudar público' }),
    h('p', { text: 'Os contatos são reordenados para o novo público. Filtros que deixarem de valer são desligados.' }),
    escolha.el, erro,
    h('div', { class: 'dialogo-acoes' }, cancelar, aplicar));
  const m = abrirModal(painel);
}
