'use strict';
/* Garimpo - busca por descrição: a IA monta o perfil e a pessoa ajusta antes de gerar a lista */

const PESOS = [{ valor: '3', rotulo: 'Ideal' }, { valor: '2', rotulo: 'Bom' }, { valor: '1', rotulo: 'Aceita' }];
const PORTES_BUSCA = [['01', 'Micro'], ['03', 'Pequeno'], ['05', 'Médio ou grande']];
const IDADES = [0, 1, 2, 3, 5, 10];
const ONDE = [{ valor: 'cidade', rotulo: 'Uma cidade' }, { valor: 'estado', rotulo: 'Um estado' }, { valor: 'brasil', rotulo: 'Brasil todo' }];

/* seletor de estado (UF); com `brasil`, a primeira opção é "Brasil todo" */
function seletorUF({ valor = '', brasil = false, aoMudar }) {
  const sel = h('select', { id: uid('uf'), 'aria-label': 'Estado' }, brasil ? h('option', { value: '', text: 'Brasil todo' }) : h('option', { value: '', text: 'Escolha o estado', disabled: true }));
  (ufsCache ? Promise.resolve(ufsCache) : api('/api/ufs')).then((u) => { ufsCache = u; sel.append(...u.map((x) => h('option', { value: x, text: x }))); sel.value = valor; }).catch(() => {});
  sel.addEventListener('change', () => aoMudar(sel.value));
  return sel;
}
const textoOnde = (onde, cidade, uf) => (onde === 'cidade' && cidade ? `em ${titleCase(cidade.nome)}` : onde === 'estado' && uf ? `em ${uf}` : onde === 'brasil' ? 'no Brasil' : '');

const usd = (v) => `US$ ${Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/* caixa de descrição do Início: manda o texto para a IA e abre a folha da proposta */
function caixaDescricao({ aoEnviar }) {
  const id = uid('desc');
  const campo = h('textarea', { id, class: 'busca-ia-texto', rows: '3', maxlength: '2000', placeholder: 'Ex.: Vendo sistema de agendamento. Quero donos de clínicas de estética e salões com mais de 2 anos em Uberlândia.', 'aria-label': 'Descreva o que você vende e quem quer encontrar' });
  const botao = h('button', { class: 'btn btn-prim', type: 'button' }, icone('sparkle', 18), 'Montar busca');
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  async function enviar() {
    const texto = campo.value.trim();
    erro.textContent = '';
    if (texto.length < 10) { erro.textContent = 'Conte um pouco mais: o que você vende e quem quer encontrar.'; campo.focus(); return; }
    botao.disabled = true;
    try { await aoEnviar(texto); } catch (e) { erro.textContent = e.message; } finally { botao.disabled = false; }
  }
  botao.addEventListener('click', enviar);
  campo.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); enviar(); } });
  const el = h('div', { class: 'busca-ia' }, h('label', { class: 'so-leitor', for: id, text: 'Descreva o que você vende e quem quer encontrar' }), campo,
    h('div', { class: 'busca-ia-rodape' }, h('span', { class: 'dica', text: 'A IA escolhe as atividades e os filtros. Você confere antes de gerar.' }), botao), erro);
  return { el, campo };
}

async function iniciarBuscaIA(texto) {
  const r = await api('/api/ia/busca', { method: 'POST', corpo: { texto } });
  folhaProposta(r.id, texto);
}

/* folha da proposta: acompanha a IA e, quando ela termina, mostra o perfil para ajustar */
function folhaProposta(buscaId, texto) {
  const idT = uid('folha');
  let vivo = true, timer = null;
  const corpo = h('div', { class: 'folha-corpo' });
  const rodape = h('footer', { class: 'folha-rodape' });
  const titulo = h('h2', { id: idT, text: 'Montando sua busca' });
  const sub = h('p', { class: 'apoio', text: texto.length > 140 ? texto.slice(0, 140) + '…' : texto });
  const painel = h('div', { class: 'folha folha-proposta', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('header', { class: 'folha-cab' }, h('div', {}, titulo, sub),
      h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar', onclick: () => m.fechar() }, icone('x', 20))),
    corpo, rodape);
  const m = abrirModal(painel, { aoFechar: () => { vivo = false; clearTimeout(timer); } });
  const etapa = h('p', { class: 'gerando-etapa', 'aria-live': 'polite', text: 'Começando…' });
  corpo.replaceChildren(h('div', { class: 'proposta-pensando' }, h('span', { class: 'ia-selo' }, icone('sparkle', 22)), progresso(null, 'A IA está montando a busca'), etapa,
    h('p', { class: 'dica', text: 'A IA consulta a base de empresas para escolher as atividades. Leva cerca de um minuto.' })));
  async function vigiar() {
    try {
      const b = await api(`/api/ia/busca/${encodeURIComponent(buscaId)}`);
      if (!vivo) return;
      if (b.status === 'pronta') { desenharProposta(b.proposta, b.custo_usd); return; }
      if (b.status === 'erro') {
        titulo.textContent = 'Não deu para montar a busca';
        corpo.replaceChildren(h('div', { class: 'nota nota-laranja', role: 'alert' }, icone('alerta', 18), h('span', { text: b.erro })));
        rodape.replaceChildren(h('button', { class: 'btn btn-sec', type: 'button', onclick: () => m.fechar() }, 'Fechar'));
        return;
      }
      etapa.textContent = `${b.etapa || 'Pensando'}…`;
    } catch (e) { if (!vivo) return; etapa.textContent = e.message; }
    timer = setTimeout(vigiar, 1500);
  }
  timer = setTimeout(vigiar, 1200);

  function desenharProposta(p, custo) {
    titulo.textContent = 'Sua busca';
    sub.textContent = p.oferta || sub.textContent;
    const P = {
      nome: p.nome, cidade: p.cidade, uf: p.cidade ? p.cidade.uf : (p.uf || ''),
      onde: p.cidade ? 'cidade' : p.uf ? 'estado' : 'brasil', atividades: p.atividades.map((a) => ({ ...a, marcada: true })), palavras: p.palavras || [],
      filtros: { ...p.filtros }, criterio: p.criterio_ia || '', contagem: 0,
    };
    const nome = h('input', { type: 'text', id: uid('nome'), maxlength: '80', value: P.nome, 'aria-label': 'Nome do perfil' });
    nome.addEventListener('input', () => { P.nome = nome.value; });

    /* onde procurar: a cidade que a IA achou, um estado inteiro ou o Brasil todo */
    const zonaCidade = h('div', {});
    const zonaUF = h('div', { class: 'campo' }, h('label', { text: 'Estado' }), seletorUF({ valor: P.uf, aoMudar: (v) => { P.uf = v; recontar(); } }));
    zonaUF.querySelector('label').htmlFor = zonaUF.querySelector('select').id;
    const dicaOnde = h('p', { class: 'dica' });
    function desenharOnde() {
      zonaCidade.hidden = P.onde !== 'cidade'; zonaUF.hidden = P.onde !== 'estado';
      dicaOnde.textContent = P.onde === 'brasil' ? 'Entram as empresas dessas atividades no país inteiro. Listas muito grandes são recusadas: aí escolha um estado.'
        : P.onde === 'estado' ? 'Entram as empresas dessas atividades em todas as cidades do estado.' : '';
      dicaOnde.hidden = P.onde === 'cidade';
    }
    const segOnde = segmentado({ opcoes: ONDE, valor: P.onde, rotulo: 'Onde procurar', papel: 'radio', aoMudar: (v) => {
      P.onde = v; desenharOnde();
      if (v === 'cidade' && !P.cidade) desenharCidade(true);
      recontar();
    } });
    function desenharCidade(editando) {
      if (P.cidade && !editando) {
        zonaCidade.replaceChildren(h('div', { class: 'proposta-cidade' }, icone('pin', 18), h('strong', { text: `${titleCase(P.cidade.nome)}` }), h('span', { class: 'uf', text: P.cidade.uf }),
          P.cidade.empresas ? h('span', { class: 'apoio-peq', text: `${num(P.cidade.empresas)} empresas na base` }) : null,
          h('button', { class: 'link-btn', type: 'button', onclick: () => desenharCidade(true) }, 'Trocar')));
        return;
      }
      const combo = criarCombo({
        rotulo: 'Cidade', placeholder: 'Buscar cidade…', minimo: 2, vazio: 'Nenhuma cidade encontrada.', inline: true,
        buscar: (q, signal) => api(`/api/cidades?q=${encodeURIComponent(q)}`, { signal }),
        render: (c) => [h('span', { class: 'ci-nome', text: titleCase(c.nome) }), h('span', { class: 'ci-n', text: `${c.uf} · ${num(c.empresas)} empresas` })],
        aoEscolher: (c) => { P.cidade = { nome: titleCase(c.nome), uf: c.uf, empresas: c.empresas }; desenharCidade(false); recontar(); },
      });
      zonaCidade.replaceChildren(combo.el);
      setTimeout(() => combo.input.focus(), 30);
    }
    if (P.cidade) desenharCidade(false);
    desenharOnde();

    /* atividades: marcar, peso e contagem */
    const total = h('div', { class: 'prev-total', 'aria-live': 'polite' });
    const ulAtiv = h('ul', { class: 'proposta-ativ' });
    function desenharAtividades() {
      ulAtiv.replaceChildren(...P.atividades.map((a) => {
        const idc = uid('a');
        const cb = h('input', { type: 'checkbox', id: idc });
        cb.checked = a.marcada;
        cb.addEventListener('change', () => { a.marcada = cb.checked; li.classList.toggle('desmarcada', !a.marcada); recontar(); });
        const peso = h('select', { 'aria-label': `Peso de ${a.descricao}` }, PESOS.map((o) => h('option', { value: o.valor, text: o.rotulo })));
        peso.value = String(a.peso);
        peso.addEventListener('change', () => { a.peso = Number(peso.value); });
        a.nEl = h('span', { class: 'opt-n', title: 'Empresas na base com os filtros', text: num(a.empresas) });
        const li = h('li', { class: a.marcada ? '' : 'desmarcada' },
          h('label', { for: idc }, cb, h('span', { class: 'opt-tx' }, h('span', { class: 'pa-desc', text: a.descricao }), a.motivo ? h('span', { class: 'pa-motivo', text: a.motivo }) : null)),
          a.nEl, peso);
        return li;
      }));
    }
    desenharAtividades();
    const maisAtiv = criarCombo({
      rotulo: 'Acrescentar atividade', placeholder: 'Acrescentar atividade…', minimo: 3, vazio: 'Nenhuma atividade encontrada.', inline: true,
      buscar: async (q, signal) => (await api(`/api/atividades?q=${encodeURIComponent(q)}`, { signal })).filter((a) => !P.atividades.some((x) => x.codigo === a.codigo)),
      render: (a) => [h('span', { class: 'ci-nome', text: a.descricao }), h('span', { class: 'ci-n', text: a.codigo })],
      aoEscolher: (a) => { P.atividades.push({ ...a, peso: 2, motivo: '', empresas: 0, marcada: true }); desenharAtividades(); recontar(); },
    });

    /* filtros */
    const sw = (rotulo, desc, chave) => interruptor({ rotulo, descricao: desc, checked: P.filtros[chave], aoMudar: (v) => { P.filtros[chave] = v; recontar(); } }).el;
    const idade = h('select', { id: uid('idade'), 'aria-label': 'Anos mínimos de empresa' }, IDADES.map((n) => h('option', { value: String(n), text: n ? `${n} ${n === 1 ? 'ano' : 'anos'} ou mais` : 'Qualquer idade' })));
    idade.value = String(P.filtros.idade_min || 0);
    idade.addEventListener('change', () => { P.filtros.idade_min = Number(idade.value); recontar(); });
    const portes = h('div', { class: 'pilulas', role: 'group', 'aria-label': 'Porte' }, PORTES_BUSCA.map(([v, r]) => {
      const b = h('button', { type: 'button', class: 'pilula', 'aria-pressed': String((P.filtros.portes || []).includes(v)) }, r);
      b.addEventListener('click', () => {
        const atuais = new Set(P.filtros.portes || []);
        if (atuais.has(v)) atuais.delete(v); else atuais.add(v);
        P.filtros.portes = [...atuais]; b.setAttribute('aria-pressed', String(atuais.has(v))); recontar();
      });
      return b;
    }));

    const criterio = h('textarea', { class: 'textarea', id: uid('crit'), rows: '4', maxlength: '2000', 'aria-label': 'Critério da IA' });
    criterio.value = P.criterio;
    criterio.addEventListener('input', () => { P.criterio = criterio.value; });

    /* contagem com os ajustes (sem IA, direto na base) */
    let pedido = 0;
    const recontar = debounce(async () => {
      const marcadas = P.atividades.filter((a) => a.marcada);
      const meu = ++pedido;
      if (!marcadas.length) { total.replaceChildren(h('span', { text: 'Marque ao menos uma atividade.' })); return; }
      try {
        const r = await api('/api/perfis/contar', { method: 'POST', corpo: {
          cidade: P.onde === 'cidade' && P.cidade ? P.cidade.nome : '', uf: P.onde === 'cidade' && P.cidade ? P.cidade.uf : P.onde === 'estado' ? P.uf : '', codigos: marcadas.map((a) => a.codigo),
          so_ativas: P.filtros.so_ativas, incluir_mei: P.filtros.incluir_mei, idade_min: P.filtros.idade_min || 0, portes: P.filtros.portes || [],
        } });
        if (meu !== pedido) return;
        const n = Object.fromEntries((r.por_atividade || []).map((x) => [x.codigo, x.empresas]));
        P.atividades.forEach((a) => { a.empresas = n[a.codigo] || 0; if (a.nEl) a.nEl.textContent = num(a.empresas); });
        P.contagem = r.total || 0;
        total.replaceChildren(h('strong', { text: num(P.contagem) }), h('span', { text: `${P.contagem === 1 ? 'empresa' : 'empresas'} ${textoOnde(P.onde, P.cidade, P.uf)}` }));
      } catch (e) { if (meu === pedido) total.replaceChildren(h('span', { text: e.message })); }
    }, 350);
    total.replaceChildren(h('strong', { text: num(p.total) }), h('span', { text: `${p.total === 1 ? 'empresa' : 'empresas'} ${textoOnde(P.onde, P.cidade, P.uf)}` }));

    corpo.replaceChildren(
      p.explicacao ? h('div', { class: 'nota nota-ia' }, icone('sparkle', 18), h('p', { text: p.explicacao })) : null,
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Nome do perfil' }), nome, h('p', { class: 'dica', text: 'O perfil fica salvo para gerar a mesma busca em outras cidades ou estados.' })),
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Onde procurar' }), segOnde.el, zonaCidade, zonaUF, dicaOnde),
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Atividades' }), total, ulAtiv, maisAtiv.el,
        h('p', { class: 'dica', text: 'O peso entra na nota de encaixe de cada empresa: as “ideais” vêm primeiro na lista.' })),
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Filtros' }),
        h('div', { class: 'grupo-linhas' }, sw('Só empresas ativas', 'Deixa de fora CNPJs baixados, suspensos ou inaptos.', 'so_ativas'), sw('Incluir MEI', 'Microempreendedores individuais entram na lista.', 'incluir_mei')),
        h('div', { class: 'campo' }, h('label', { for: idade.id, text: 'Anos de empresa' }), idade),
        h('div', { class: 'campo' }, h('span', { class: 'campo-rot', text: 'Porte (nenhum marcado: todos)' }), portes)),
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Como a IA vai avaliar cada empresa' }), criterio,
        h('p', { class: 'dica', text: 'Depois de gerar a lista, use “Avaliar com IA” para dar uma nota de 0 a 10 a cada empresa com este critério.' })));
    const erro = h('p', { class: 'erro-inline', role: 'alert' });
    const gerar = h('button', { class: 'btn btn-prim btn-grande', type: 'button' }, 'Salvar perfil e gerar lista');
    gerar.addEventListener('click', async () => {
      erro.textContent = '';
      const marcadas = P.atividades.filter((a) => a.marcada);
      if (P.onde === 'cidade' && !P.cidade) { erro.textContent = 'Escolha a cidade.'; return; }
      if (P.onde === 'estado' && !P.uf) { erro.textContent = 'Escolha o estado.'; return; }
      if (!marcadas.length && !P.palavras.length) { erro.textContent = 'Marque ao menos uma atividade.'; return; }
      if (!P.nome.trim()) { erro.textContent = 'Dê um nome ao perfil.'; nome.focus(); return; }
      gerar.disabled = true; gerar.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Gerando…');
      try {
        const perfil = await api('/api/perfis', { method: 'POST', corpo: { nome: P.nome.trim(), receita: {
          oferta: p.oferta, cliente_ideal: p.cliente_ideal, criterio_ia: P.criterio, explicacao: p.explicacao,
          atividades: marcadas.map((a) => ({ codigo: a.codigo, descricao: a.descricao, peso: a.peso })), palavras: P.palavras, filtros: P.filtros,
        } } });
        await carregarPerfis(true);
        const onde = P.onde === 'cidade' ? { uf: P.cidade.uf, cidade: P.cidade.nome } : { uf: P.onde === 'estado' ? P.uf : '' };
        const r = await api('/api/listas', { method: 'POST', corpo: { ...onde, perfil: perfil.id } });
        m.fechar();
        location.hash = `#/lista/${encodeURIComponent(r.id)}`;
      } catch (e) { erro.textContent = e.message; gerar.disabled = false; gerar.replaceChildren('Salvar perfil e gerar lista'); }
    });
    rodape.replaceChildren(h('span', { class: 'custo-busca', text: custo ? `Esta busca custou ${usd(custo)}` : '' }), erro, gerar);
    rodape.classList.add('rodape-proposta');
  }
}

/* gerar um perfil salvo num estado inteiro ou no Brasil todo (o caminho da cidade fica no Início) */
function folhaRegiao(perfil) {
  const idT = uid('folha');
  const r = perfil.receita || {}, f = r.filtros || {};
  const codigos = (r.atividades || []).map((a) => a.codigo);
  let uf = '', pedido = 0;
  const total = h('div', { class: 'prev-total', 'aria-live': 'polite' }, esqueleto('60%', '20px'));
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const gerar = h('button', { class: 'btn btn-prim btn-grande', type: 'button', disabled: !codigos.length }, 'Gerar lista');
  async function contar() {
    const meu = ++pedido;
    if (!codigos.length) { total.replaceChildren(h('span', { text: 'Este perfil não tem atividades. Ele só gera listas de uma cidade.' })); return; }
    total.replaceChildren(esqueleto('60%', '20px'));
    try {
      const c = await api('/api/perfis/contar', { method: 'POST', corpo: { cidade: '', uf, codigos, so_ativas: f.so_ativas !== false, incluir_mei: !!f.incluir_mei, idade_min: f.idade_min || 0, portes: f.portes || [] } });
      if (meu !== pedido) return;
      total.replaceChildren(h('strong', { text: num(c.total) }), h('span', { text: `${c.total === 1 ? 'empresa' : 'empresas'} ${uf ? 'em ' + uf : 'no Brasil'}` }));
    } catch (e) { if (meu === pedido) total.replaceChildren(h('span', { text: e.message })); }
  }
  const sel = seletorUF({ valor: '', brasil: true, aoMudar: (v) => { uf = v; erro.textContent = ''; contar(); } });
  gerar.addEventListener('click', async () => {
    erro.textContent = ''; gerar.disabled = true; gerar.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Enviando…');
    try {
      const l = await api('/api/listas', { method: 'POST', corpo: { uf, perfil: perfil.id } });
      m.fechar();
      location.hash = `#/lista/${encodeURIComponent(l.id)}`;
    } catch (e) { erro.textContent = e.message; gerar.disabled = false; gerar.replaceChildren('Gerar lista'); }
  });
  const painel = h('div', { class: 'folha', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('header', { class: 'folha-cab' },
      h('div', {}, h('h2', { id: idT, text: 'Lista num estado ou no Brasil' }), h('p', { class: 'apoio', text: `Perfil: ${perfil.nome}` })),
      h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar', onclick: () => m.fechar() }, icone('x', 20))),
    h('div', { class: 'folha-corpo' },
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Onde procurar' }),
        h('div', { class: 'campo' }, h('label', { for: sel.id, text: 'Região' }), sel), total,
        h('p', { class: 'dica', text: 'Entram as empresas das atividades do perfil em todas as cidades, com os filtros dele. Listas acima de 300 mil empresas são recusadas: aí escolha um estado.' }))),
    h('footer', { class: 'folha-rodape' }, erro, gerar));
  const m = abrirModal(painel);
  contar();
}
