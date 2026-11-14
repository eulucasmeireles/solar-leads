'use strict';
/* Solar Leads - telas Início e Listas */

const EM_ANDAMENTO = (m) => m.status === 'na_fila' || m.status === 'gerando';
const porData = (a, b) => String(b.criada_em).localeCompare(String(a.criada_em));

/* ---------- cartão de lista ---------- */
function cartaoLista(m, aoExcluir) {
  const andamento = EM_ANDAMENTO(m);
  const r = m.resumo;
  let corpo;
  if (andamento) {
    corpo = h('div', { class: 'cl-andamento' }, progresso(null, `Gerando lista de ${titleCase(m.cidade)}`),
      h('p', { class: 'cl-etapa', text: m.etapa || (m.status === 'na_fila' ? 'Aguardando a vez na fila…' : 'Começando…') }));
  } else if (m.status === 'erro') {
    corpo = h('p', { class: 'cl-erro', text: m.erro || 'Algo deu errado ao gerar esta lista.' });
  } else if (r) {
    const forte = r.contatos_para_disparo ? r.faixa_A / r.contatos_para_disparo * 100 : 0;
    corpo = h('div', { class: 'cl-dados' },
      anel({ valor: forte, texto: `${Math.round(forte)}%`, cor: '--verde', tam: 56, espessura: 5, rotulo: `${Math.round(forte)}% de contatos fortes` }),
      h('div', {}, h('div', { class: 'cl-contatos', text: `${num(r.contatos_para_disparo)} contatos` }), h('div', { class: 'cl-sub', text: `${num(r.faixa_A)} fortes` })));
  }
  const link = h('a', { class: 'cl-link', href: `#/lista/${encodeURIComponent(m.id)}` }, titleCase(m.cidade));
  const menu = criarMenu(icone('pontos', 20), [{ rotulo: 'Excluir lista', icone: 'lixeira', perigo: true, aoClicar: () => aoExcluir(m) }],
    { classeBotao: 'btn-icone', rotulo: `Mais opções da lista de ${titleCase(m.cidade)}` });
  menu.classList.add('cl-menu');
  return h('article', { class: 'cartao cartao-lista' + (andamento ? ' em-andamento' : '') },
    h('header', { class: 'cl-topo' }, h('h3', {}, link, h('span', { class: 'uf', text: m.uf })), menu),
    h('p', { class: 'cl-ativ', text: nomeAtividades(m) }),
    corpo || null,
    h('footer', { class: 'cl-rodape' }, andamento || m.status === 'erro' ? seloStatus(m.status) : null,
      h('time', { datetime: m.criada_em, text: dataRelativa(m.criada_em) })));
}

async function excluirLista(m, aposExcluir) {
  const ok = await confirmar({
    titulo: 'Excluir esta lista?',
    texto: `A lista de ${titleCase(m.cidade)} (${m.uf}) será apagada de vez. Você pode gerá-la de novo quando quiser.`,
    ok: 'Excluir', perigo: true,
  });
  if (!ok) return;
  try { await api(`/api/listas/${encodeURIComponent(m.id)}`, { method: 'DELETE' }); avisar('Lista excluída.'); aposExcluir(); }
  catch (e) { avisar(e.status === 409 ? 'Esta lista ainda está sendo gerada. Espere terminar para excluir.' : e.message, 'erro'); }
}

/* vigia as listas: busca, desenha e repete a cada 2 s enquanto houver alguma em andamento */
function vigiarListas(desenhar, aoErro) {
  let vivo = true, timer = null, ultimo = '', anterior = new Map();
  const live = h('div', { class: 'so-leitor', 'aria-live': 'polite' });
  document.getElementById('conteudo').append(live);
  async function ciclo(primeira) {
    try {
      const lista = (await api('/api/listas')).slice().sort(porData);
      if (!vivo) return;
      const sig = JSON.stringify(lista.map((m) => [m.id, m.status, m.etapa, m.erro, m.resumo && m.resumo.contatos_para_disparo]));
      if (sig !== ultimo) {
        ultimo = sig; desenhar(lista);
        lista.forEach((m) => {
          const a = anterior.get(m.id);
          if (a && a.status !== m.status && m.status === 'pronta') live.textContent = `Lista de ${titleCase(m.cidade)} pronta.`;
          else if (EM_ANDAMENTO(m) && m.etapa && (!a || a.etapa !== m.etapa)) live.textContent = `${titleCase(m.cidade)}: ${m.etapa}`;
        });
        anterior = new Map(lista.map((m) => [m.id, m]));
      }
      if (lista.some(EM_ANDAMENTO)) timer = setTimeout(() => ciclo(false), 2000);
    } catch (e) { if (vivo && primeira) aoErro(e); else if (vivo) timer = setTimeout(() => ciclo(false), 4000); }
  }
  aoSair(() => { vivo = false; clearTimeout(timer); live.remove(); });
  ciclo(true);
  return () => { clearTimeout(timer); ciclo(true); };
}

function esqueletoCartoes(n) {
  return Array.from({ length: n }, () => h('div', { class: 'cartao cartao-lista', 'aria-hidden': 'true' },
    esqueleto('55%', '22px'), esqueleto('80%', '13px'), esqueleto('100%', '56px')));
}

/* ---------- folha "Nova lista" ---------- */
let whatsDisp = { disponivel: false, motivo: '' };

function folhaNovaLista(cidade) {
  const estado = { cnaes: [], modo: 'todas', so_locais: true, so_ativas: false, incluir_mei: false, validar_whatsapp: false };
  const idT = uid('folha');
  const zonaEscolher = h('div', { class: 'folha-escolher', hidden: true });
  const chips = h('div', { class: 'chips' });
  const dica = h('p', { class: 'dica' });
  function desenharChips() {
    chips.replaceChildren(...estado.cnaes.map((a) => chip(a.descricao, () => { estado.cnaes = estado.cnaes.filter((x) => x.codigo !== a.codigo); desenharChips(); }, `Remover atividade ${a.descricao}`)));
    dica.textContent = estado.cnaes.length ? 'Só empresas dessas atividades entram na lista.' : 'Busque por uma atividade, como "supermercado" ou "clínica".';
  }
  const ativ = criarCombo({
    rotulo: 'Buscar atividade', placeholder: 'Buscar atividade…', minimo: 3, vazio: 'Nenhuma atividade encontrada.', inline: true,
    buscar: async (q, signal) => (await api(`/api/atividades?q=${encodeURIComponent(q)}`, { signal })).filter((a) => !estado.cnaes.some((x) => x.codigo === a.codigo)),
    render: (a) => [h('span', { class: 'ci-nome', text: a.descricao }), h('span', { class: 'ci-n', text: a.codigo })],
    aoEscolher: (a) => { estado.cnaes.push(a); desenharChips(); },
  });
  zonaEscolher.append(ativ.el, chips, dica);
  desenharChips();
  const modo = segmentado({
    opcoes: [{ valor: 'todas', rotulo: 'Todas as atividades' }, { valor: 'escolher', rotulo: 'Escolher' }], valor: 'todas', rotulo: 'Atividades', papel: 'radio',
    aoMudar: (v) => { estado.modo = v; zonaEscolher.hidden = v !== 'escolher'; if (v === 'escolher') ativ.input.focus(); },
  });
  const sw = (rotulo, desc, chave, extra = {}) => interruptor({ rotulo, descricao: desc, checked: estado[chave], disabled: extra.disabled, aoMudar: (v) => { estado[chave] = v; } }).el;
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const botao = h('button', { class: 'btn btn-prim btn-grande', type: 'button' }, 'Gerar lista');
  botao.addEventListener('click', async () => {
    botao.disabled = true; erro.textContent = '';
    botao.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Enviando…');
    try {
      const r = await api('/api/listas', { method: 'POST', corpo: {
        uf: cidade.uf, cidade: cidade.nome, cnaes: estado.modo === 'escolher' ? estado.cnaes.map((a) => a.codigo) : [],
        so_ativas: estado.so_ativas, incluir_mei: estado.incluir_mei, so_locais: estado.so_locais, validar_whatsapp: estado.validar_whatsapp && whatsDisp.disponivel,
      } });
      m.fechar();
      location.hash = `#/lista/${encodeURIComponent(r.id)}`;
    } catch (e) { erro.textContent = e.message; botao.disabled = false; botao.replaceChildren('Gerar lista'); }
  });
  const painel = h('div', { class: 'folha', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('header', { class: 'folha-cab' },
      h('div', {}, h('h2', { id: idT, text: `Nova lista em ${titleCase(cidade.nome)}` }), h('p', { class: 'apoio', text: `${cidade.uf} · ${num(cidade.empresas)} empresas na base` })),
      h('button', { class: 'btn-icone', type: 'button', 'aria-label': 'Fechar', onclick: () => m.fechar() }, icone('x', 20))),
    h('div', { class: 'folha-corpo' },
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Atividades' }), modo.el, zonaEscolher),
      h('section', { class: 'folha-sec' }, h('h3', { text: 'Opções' }),
        h('div', { class: 'grupo-linhas' },
          sw('Só empresas da cidade', 'Sede na cidade e telefone da região. Tira filiais e números de fora.', 'so_locais'),
          sw('Só empresas ativas', 'Deixa de fora CNPJs baixados, suspensos ou inaptos.', 'so_ativas'),
          sw('Incluir MEI', 'Microempreendedores individuais entram na lista.', 'incluir_mei'),
          sw('Verificar WhatsApp ao gerar', whatsDisp.disponivel ? 'Mais lento: confere cada número ao montar a lista.' : `Indisponível agora. ${whatsDisp.motivo || 'Conecte um WhatsApp primeiro.'}`, 'validar_whatsapp', { disabled: !whatsDisp.disponivel })))),
    h('footer', { class: 'folha-rodape' }, erro, botao));
  const m = abrirModal(painel);
}

/* ---------- Início ---------- */
function telaInicio(raiz) {
  document.title = 'Início · Solar Leads';
  const gradeRecentes = h('div', { class: 'grade-listas' }, esqueletoCartoes(3));
  const faixaNumeros = h('ul', { class: 'numeros-base' });

  const combo = criarCombo({
    rotulo: 'Cidade', placeholder: 'Buscar cidade…', minimo: 2, grande: true, vazio: 'Nenhuma cidade encontrada.',
    buscar: (q, signal) => api(`/api/cidades?q=${encodeURIComponent(q)}`, { signal }),
    render: (c) => [h('span', { class: 'ci-nome', text: titleCase(c.nome) }), h('span', { class: 'ci-n', text: `${c.uf} · ${num(c.empresas)} empresas` })],
    aoEscolher: (c) => folhaNovaLista(c),
  });

  const topo = h('section', { class: 'heroi' },
    h('div', { class: 'brilho', 'aria-hidden': 'true' }, h('i', { class: 'b1' }), h('i', { class: 'b2' }), h('i', { class: 'b3' })),
    h('p', { class: 'saud', text: saudacao() }),
    h('h1', { text: 'Onde vamos prospectar hoje?' }),
    h('p', { class: 'apoio', text: 'Escolha uma cidade e montamos a lista de contatos.' }),
    h('label', { class: 'so-leitor', for: combo.input.id, text: 'Cidade' }), combo.el, faixaNumeros);

  function desenharRecentes(lista) {
    if (!lista.length) { gradeRecentes.replaceChildren(estadoVazio('Nenhuma lista ainda', 'Escolha uma cidade acima para gerar a primeira.', null, 'listas')); return; }
    gradeRecentes.replaceChildren(...lista.slice(0, 6).map((m) => cartaoLista(m, (x) => excluirLista(x, recarregar))));
  }
  const recarregar = vigiarListas(desenharRecentes, (e) => gradeRecentes.replaceChildren(estadoErro(e.message, () => recarregar())));

  api('/api/whatsapp').then((w) => { whatsDisp = w; }).catch(() => {});
  api('/api/base').then((b) => {
    const it = (ic, v, r) => h('li', {}, icone(ic, 18), h('strong', { text: v }), h('span', { text: r }));
    faixaNumeros.replaceChildren(
      it('predio', num(b.receita.estabelecimentos), 'estabelecimentos'),
      it('pin', num(b.municipios), 'municípios'),
      it('sol', b.aneel ? num(b.aneel.sistemas) : '—', 'sistemas solares'));
  }).catch(() => faixaNumeros.replaceChildren());

  raiz.append(topo,
    h('section', { class: 'secao', 'aria-labelledby': 'tit-rec' },
      h('header', { class: 'secao-cab' }, h('h2', { id: 'tit-rec', text: 'Suas listas' }), h('a', { class: 'link-seta', href: '#/listas' }, 'Ver todas', icone('direita', 16))),
      gradeRecentes));
  setTimeout(() => combo.input.focus({ preventScroll: true }), 80);
}

/* ---------- Listas ---------- */
function telaListas(raiz) {
  document.title = 'Listas · Solar Leads';
  const grade = h('div', { class: 'grade-listas' }, esqueletoCartoes(6));
  let todas = [];
  const busca = h('input', { type: 'search', id: 'busca-listas', placeholder: 'Buscar por cidade…', autocomplete: 'off', 'aria-label': 'Buscar lista por cidade' });
  const norm = (t) => String(t).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  function desenhar() {
    const q = norm(busca.value.trim());
    const lista = todas.filter((m) => !q || norm(`${m.cidade} ${m.uf}`).includes(q));
    if (!lista.length) {
      grade.replaceChildren(todas.length ? estadoVazio('Nenhuma lista com esse nome', 'Tente outra cidade ou limpe a busca.', null) : estadoVazio('Nenhuma lista ainda', 'Gere a primeira escolhendo uma cidade no Início.', h('a', { class: 'btn btn-prim', href: '#/' }, 'Ir para o Início'), 'listas'));
      return;
    }
    grade.replaceChildren(...lista.map((m) => cartaoLista(m, (x) => excluirLista(x, recarregar))));
  }
  busca.addEventListener('input', debounce(desenhar, 150));
  const recarregar = vigiarListas((l) => { todas = l; desenhar(); }, (e) => grade.replaceChildren(estadoErro(e.message, () => recarregar())));
  raiz.append(cabecalhoPagina('Listas', 'Tudo o que você já gerou.', h('a', { class: 'btn btn-prim', href: '#/' }, icone('mais', 18), 'Nova lista')),
    h('div', { class: 'combo combo-pilula busca-listas' }, icone('busca', 18), busca), grade);
}
