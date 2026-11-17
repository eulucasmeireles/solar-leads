'use strict';
/* Garimpo - telas WhatsApp e Base de dados (cartões no estilo Ajustes) */

function grupoAjustes(titulo, linhas, rodape) {
  return h('section', { class: 'ajustes' }, titulo ? h('h2', { class: 'ajustes-tit', text: titulo }) : null,
    h('div', { class: 'cartao cartao-ajustes' }, linhas), rodape ? h('p', { class: 'ajustes-rodape', text: rodape }) : null);
}

/* ---------- WhatsApp ---------- */
/* números consultados pela CheckLeaked: cada consulta gasta 1 do pacote, tenha WhatsApp ou não */
const QUANTIDADES_VERIFICAR = [{ valor: '200', rotulo: '200' }, { valor: '500', rotulo: '500' }, { valor: '1000', rotulo: '1.000' }, { valor: '5000', rotulo: 'Todos' }];
const DICA_VERIFICAR = 'Cada número consultado gasta 1 consulta do pacote, tenha WhatsApp ou não. Quem já foi verificado nos últimos 30 dias não gasta de novo.';

function textoSaldo(sv) {
  if (!sv || !sv.configurado) return 'Falta a chave da CheckLeaked no servidor.';
  if (!sv.saldo) return 'Saldo indisponível agora.';
  return `${num(sv.saldo.restam)} consultas restantes${sv.saldo.vence ? ` · válidas até ${dataBR(sv.saldo.vence)}` : ''}`;
}

/* folha para escolher quantos contatos verificar, com a conta do saldo antes de começar */
function dialogoVerificar(meta, aoIniciar) {
  const idT = uid('dlg');
  let quantidade = '500', pedido = 0;
  const conta = h('p', { class: 'verif-conta', 'aria-live': 'polite' }, esqueleto('80%', '14px'));
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const iniciar = h('button', { class: 'btn btn-prim', type: 'button', disabled: true }, 'Verificar');
  const cancelar = h('button', { class: 'btn btn-sec', type: 'button', onclick: () => m.fechar() }, 'Cancelar');
  async function estimar() {
    const meu = ++pedido;
    iniciar.disabled = true; conta.replaceChildren(esqueleto('80%', '14px'));
    try {
      const e = await api(`/api/listas/${encodeURIComponent(meta.id)}/whatsapp?quantidade=${quantidade}`);
      if (meu !== pedido) return;
      const sv = e.servico || {}, restam = sv.saldo ? sv.saldo.restam : null;
      if (!e.numeros) { conta.textContent = 'Todos os contatos desta lista já têm o WhatsApp verificado.'; return; }
      const partes = [`${num(e.numeros)} ${e.numeros === 1 ? 'contato' : 'contatos'} na fila`];
      if (e.ja_verificados) partes.push(`${num(e.ja_verificados)} já verificados, sem custo`);
      partes.push(`${num(e.consultas)} ${e.consultas === 1 ? 'consulta' : 'consultas'} do saldo`);
      conta.replaceChildren(h('strong', { text: partes.join(' · ') }), h('br'),
        h('span', { text: !sv.configurado ? 'Falta a chave da CheckLeaked no servidor.' : restam == null ? 'Não deu para ler o saldo agora.' : `Saldo: ${num(restam)} consultas.${restam < e.consultas ? ' Não cobre tudo: a verificação para quando o saldo acabar.' : ''}` }));
      iniciar.disabled = !sv.configurado || (restam != null && restam === 0 && e.consultas > 0);
      iniciar.textContent = e.consultas ? `Verificar ${num(e.numeros)}` : 'Atualizar a lista';
    } catch (err) { if (meu === pedido) conta.textContent = err.message; }
  }
  const seg = segmentado({ opcoes: QUANTIDADES_VERIFICAR, valor: quantidade, rotulo: 'Quantos contatos verificar', papel: 'radio', aoMudar: (v) => { quantidade = v; estimar(); } });
  iniciar.addEventListener('click', async () => {
    iniciar.disabled = true; cancelar.disabled = true; erro.textContent = '';
    iniciar.replaceChildren(h('i', { class: 'giro', 'aria-hidden': 'true' }), 'Começando…');
    try {
      await api('/api/whatsapp/verificacao', { method: 'POST', corpo: { lista_id: meta.id, quantidade: Number(quantidade) } });
      m.fechar(); avisar('Verificação iniciada.');
      if (aoIniciar) aoIniciar();
    } catch (e) { erro.textContent = e.message; cancelar.disabled = false; iniciar.disabled = false; iniciar.replaceChildren('Verificar'); }
  });
  const painel = h('div', { class: 'dialogo dialogo-largo', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('h2', { id: idT, text: 'Verificar WhatsApp' }),
    h('p', { text: `Os próximos contatos da ${rotuloLista(meta)}, na ordem da lista, sem WhatsApp verificado ainda.` }),
    seg.el, conta, h('p', { class: 'dica', text: DICA_VERIFICAR }), erro,
    h('div', { class: 'dialogo-acoes' }, cancelar, iniciar));
  const m = abrirModal(painel);
  estimar();
}

async function telaWhatsapp(raiz) {
  document.title = 'WhatsApp · Garimpo';
  const rota = app.rota;
  const S = { w: null, listas: [], timer: null, vivo: true, rodava: false };
  const zonaEstado = h('div', {}, h('div', { class: 'cartao' }, h('div', { class: 'sk-pilha' }, esqueleto('40%', '22px'), esqueleto('70%', '14px'), esqueleto('100%', '56px'))));
  const zonaVerif = h('div', {});
  const live = h('div', { class: 'so-leitor', 'aria-live': 'polite' });
  aoSair(() => { S.vivo = false; clearTimeout(S.timer); });

  function desenharEstado() {
    const w = S.w, sv = (w && w.servico) || {}, t = (w && w.totais) || {};
    const ok = Boolean(sv.configurado && sv.saldo && sv.saldo.restam > 0);
    const dado = (k, r) => h('div', { class: 'mini-num' }, h('div', { class: 'mini-num-v', text: t[k] == null ? '—' : num(t[k]) }), h('div', { class: 'mini-num-r', text: r }));
    zonaEstado.replaceChildren(h('section', { class: 'cartao' },
      ajuste({ icone: 'whatsapp', tom: ok ? 'verde' : 'cinza', titulo: 'Verificação pela CheckLeaked', descricao: textoSaldo(sv),
        valor: h('span', { class: `status-ponto ${ok ? 'on' : ''}`, 'aria-hidden': 'true' }) }),
      h('div', { class: 'mini-nums' }, dado('verificados', 'verificados'), dado('com_whatsapp', 'com WhatsApp'), dado('pessoais', 'pessoais'), dado('comerciais', 'comerciais'))));
  }

  function desenharVerificacao() {
    const v = (S.w && S.w.verificacao) || {}, sv = (S.w && S.w.servico) || {};
    if (v.rodando) {
      const lista = S.listas.find((m) => m.id === v.lista_id);
      const quanto = v.total ? Math.min(100, v.feitos / v.total * 100) : 0;
      zonaVerif.replaceChildren(h('section', { class: 'cartao' },
        ajuste({ icone: 'sparkle', tom: 'laranja', titulo: 'Verificando números', descricao: lista ? `${rotuloLista(lista, true)} · ${regiaoDaLista(lista)}` : 'Em andamento', valor: selo('Rodando', 'laranja', { ponto: true }) }),
        h('div', { class: 'verif-roda' },
          progresso(quanto, 'Números verificados'),
          h('p', { class: 'apoio-peq', text: `${num(v.feitos)} de ${num(v.total)} números` }),
          v.mensagem ? h('p', { class: 'verif-msg', text: v.mensagem }) : null,
          h('div', {}, h('button', { class: 'btn btn-perigo', type: 'button', onclick: parar }, icone('parar', 18), 'Parar')),
          h('p', { class: 'dica', text: 'Cerca de 5 minutos para cada 500 números. Ao terminar, a lista é atualizada sozinha.' }))));
      return;
    }
    const prontas = S.listas.filter((m) => m.status === 'pronta');
    const sel = h('select', { id: 'sel-lista', disabled: !prontas.length }, prontas.map((m) => h('option', { value: m.id, text: `${cidadeUF(m)} · ${num(m.resumo ? m.resumo.contatos_para_disparo : 0)} contatos` })));
    if (v.lista_id && prontas.some((m) => m.id === v.lista_id)) sel.value = v.lista_id;
    const botao = h('button', { class: 'btn btn-prim', type: 'button', disabled: !prontas.length || !sv.configurado,
      onclick: () => { const m = prontas.find((x) => x.id === sel.value); if (m) dialogoVerificar(m, atualizar); } }, 'Escolher quantos verificar');
    const motivo = !sv.configurado ? 'Falta a chave da CheckLeaked no servidor.' : !prontas.length ? 'Gere uma lista primeiro.' : '';
    zonaVerif.replaceChildren(h('section', { class: 'cartao' },
      ajuste({ icone: 'sparkle', tom: 'azul', titulo: 'Verificar números', descricao: 'Descobre quais números têm WhatsApp e se a conta é pessoal ou comercial, sem usar nenhum número seu.' }),
      v.mensagem ? h('p', { class: 'verif-msg', text: v.mensagem }) : null,
      h('div', { class: 'campo' }, h('label', { for: 'sel-lista', text: 'Lista para verificar' }), sel),
      h('div', { class: 'linha-botoes' }, botao, motivo ? h('span', { class: 'dica', text: motivo }) : null),
      h('p', { class: 'dica', text: DICA_VERIFICAR })));
  }

  async function parar() {
    try { await api('/api/whatsapp/verificacao', { method: 'DELETE' }); avisar('Parando ao fim desta rodada.'); await atualizar(); } catch (e) { avisar(e.message, 'erro'); }
  }

  async function atualizar() {
    try {
      S.w = await api('/api/whatsapp');
      if (!S.vivo) return;
      const v = S.w.verificacao || {};
      if (S.rodava && !v.rodando && v.mensagem) live.textContent = v.mensagem;
      S.rodava = !!v.rodando;
      desenharEstado(); desenharVerificacao();
    } catch (e) {
      if (!S.w) zonaEstado.replaceChildren(estadoErro(e.message, atualizar));
    }
    clearTimeout(S.timer);
    if (S.vivo && S.w && S.w.verificacao && S.w.verificacao.rodando) S.timer = setTimeout(atualizar, 2500);
  }

  raiz.append(cabecalhoPagina('WhatsApp', 'Descubra quais números têm WhatsApp, e se são pessoais ou comerciais, antes de disparar.'), live, zonaEstado, zonaVerif);
  try { S.listas = await api('/api/listas'); } catch (e) { S.listas = []; }
  if (rota !== app.rota) return;
  await atualizar();
}

/* gasto de IA do mês (API da Anthropic), com o teto */
const NOMES_FUNCAO_IA = { busca: 'Busca por descrição', avaliacao: 'Avaliação dos contatos', 'avaliacao-lote': 'Avaliação em lote' };
function blocoIA() {
  const zona = h('div', {}, h('div', { class: 'cartao' }, esqueleto('50%', '22px')));
  api('/api/ia').then((u) => {
    const parte = u.teto_usd ? Math.min(100, u.gasto_usd / u.teto_usd * 100) : 0;
    zona.replaceChildren(grupoAjustes('Inteligência artificial', [
      ajuste({ icone: 'sparkle', tom: 'roxo', titulo: u.configurada ? 'Gasto do mês' : 'Chave não configurada',
        descricao: u.configurada ? `Teto de ${usd(u.teto_usd)} por mês · ${num(u.chamadas)} chamadas` : 'Rode `uv run leads segredo ANTHROPIC_API_KEY` no servidor.',
        valor: u.configurada ? usd(u.gasto_usd) : '—' }),
      u.configurada ? h('div', { class: 'ajuste-barra' }, progresso(parte, 'Parte do teto já gasta')) : null,
      ...(u.por_funcao || []).map((f) => ajuste({ icone: f.funcao === 'busca' ? 'busca' : 'estrela', tom: 'roxo', titulo: NOMES_FUNCAO_IA[f.funcao] || f.funcao, descricao: `${num(f.chamadas)} chamadas`, valor: usd(f.gasto_usd) })),
      ajuste({ icone: 'info', tom: 'cinza', titulo: 'Modelos', descricao: `Busca: ${u.modelos.busca} · Avaliação: ${u.modelos.avaliacao}` })],
      'Para a IA vão só dados públicos das empresas. O teto muda em GARIMPO_IA_TETO_USD, no .env do servidor.'));
  }).catch((e) => zona.replaceChildren(grupoAjustes('Inteligência artificial', [ajuste({ icone: 'alerta', tom: 'cinza', titulo: 'Não deu para ler o gasto', descricao: e.message })])));
  return zona;
}

/* ---------- Base de dados ---------- */
async function telaBase(raiz) {
  document.title = 'Base de dados · Garimpo';
  const rota = app.rota;
  const corpo = h('div', { class: 'ajustes-pilha' }, Array.from({ length: 3 }, () => h('div', { class: 'cartao', 'aria-hidden': 'true' }, esqueleto('50%', '22px'), esqueleto('100%', '70px'))));
  raiz.append(cabecalhoPagina('Base de dados', 'De onde vêm os contatos e quando cada fonte foi atualizada.'), corpo);
  async function carregar() {
    let b;
    try { b = await api('/api/base'); } catch (e) { if (rota === app.rota) corpo.replaceChildren(estadoErro(e.status === 404 ? 'Os dados da base ainda não estão disponíveis neste servidor.' : e.message, carregar)); return; }
    if (rota !== app.rota) return;
    const r = b.receita || {}, a = b.aneel, w = b.whatsapp || {};
    corpo.replaceChildren(
      grupoAjustes('Receita Federal', [
        ajuste({ icone: 'predio', tom: 'azul', titulo: 'Base de CNPJ', descricao: 'Mês da base', valor: r.mes ? mesBR(r.mes) : '—' }),
        ajuste({ icone: 'base', tom: 'azul', titulo: 'Estabelecimentos', valor: num(r.estabelecimentos) }),
        ajuste({ icone: 'listas', tom: 'azul', titulo: 'Empresas', valor: num(r.empresas) }),
        ajuste({ icone: 'pessoa', tom: 'azul', titulo: 'Sócios', valor: num(r.socios) }),
        ajuste({ icone: 'pin', tom: 'azul', titulo: 'Municípios cobertos', valor: num(b.municipios) })]),
      grupoAjustes('ANEEL', a ? [
        ajuste({ icone: 'sol', tom: 'laranja', titulo: 'Última atualização', valor: dataBR(a.atualizado_em) }),
        ajuste({ icone: 'raio', tom: 'laranja', titulo: 'Sistemas instalados', valor: num(a.sistemas) }),
        ajuste({ icone: 'predio', tom: 'laranja', titulo: 'Empresas com energia solar', valor: num(a.empresas_com_solar) })]
        : [ajuste({ icone: 'sol', tom: 'laranja', titulo: 'Ainda não carregada', descricao: 'Sem registros da ANEEL na base.' })],
        'O portal da ANEEL só aceita acesso do Brasil; a atualização precisa de um túnel pelo seu computador.'),
      grupoAjustes('WhatsApp', [
        ajuste({ icone: 'whatsapp', tom: 'verde', titulo: 'Números verificados', valor: num(w.verificados) }),
        ajuste({ icone: 'check', tom: 'verde', titulo: 'Com WhatsApp', valor: num(w.com_whatsapp) }),
        ajuste({ icone: 'pessoa', tom: 'verde', titulo: 'Pessoais', valor: num(w.pessoais) }),
        ajuste({ icone: 'predio', tom: 'verde', titulo: 'Comerciais', valor: num(w.comerciais) })]),
      blocoIA(), blocoExportacoes(), blocoChaves(), blocoAcessos());
  }
  carregar();
}

/* ---------- Exportações recentes ---------- */
function blocoExportacoes() {
  const zona = h('div', {});
  api('/api/exportacoes').then((itens) => {
    const ls = (itens || []).slice(0, 20);
    zona.replaceChildren(grupoAjustes('Exportações recentes', ls.length
      ? ls.map((e) => ajuste({ icone: e.origem === 'pasta' ? 'pasta' : 'listas', tom: 'azul', titulo: e.descricao || (e.origem === 'pasta' ? 'Pasta' : 'Lista'), descricao: `${dataRelativa(e.quando)} · ${e.origem === 'pasta' ? 'pasta' : 'lista'}`, valor: `${num(e.contatos)} ${e.contatos === 1 ? 'contato' : 'contatos'}` }))
      : [ajuste({ icone: 'baixar', tom: 'cinza', titulo: 'Nenhuma exportação ainda', descricao: 'Quando você exportar uma lista ou pasta, ela aparece aqui.' })]));
  }).catch(() => { /* sem histórico neste servidor */ });
  return zona;
}

/* ---------- Acessos ao sistema ---------- */
function blocoAcessos() {
  const zona = h('div', {});
  const TIPOS = { entrou: ['check', 'verde', 'Entrou'], saiu: ['esquerda', 'cinza', 'Saiu'], 'senha errada': ['alerta', 'laranja', 'Senha errada'], bloqueado: ['alerta', 'vermelho', 'Bloqueado'],
    'chave criada': ['check', 'verde', 'Chave de API criada'], 'chave revogada': ['alerta', 'laranja', 'Chave de API revogada'],
    'mcp autorizado': ['check', 'verde', 'Ferramenta autorizada no MCP'], 'mcp recusado': ['alerta', 'cinza', 'Ferramenta recusada no MCP'],
    'mcp revogado': ['alerta', 'laranja', 'Ferramenta do MCP revogada'] };
  api('/api/acessos').then((itens) => {
    const ls = (itens || []).slice(0, 30);
    zona.replaceChildren(grupoAjustes('Acessos ao sistema', ls.length
      ? ls.map((a) => { const [ic, tom, rot] = TIPOS[a.tipo] || ['info', 'cinza', a.tipo];
        return ajuste({ icone: ic, tom, titulo: rot, descricao: dataRelativa(a.quando), valor: h('span', { class: 'ip-acesso', text: a.ip || '' }) }); })
      : [ajuste({ icone: 'info', tom: 'cinza', titulo: 'Nenhum acesso registrado ainda' })]));
  }).catch(() => { /* sem registro de acessos neste servidor */ });
  return zona;
}

/* ---------- Chaves de API ---------- */
function blocoChaves() {
  const zona = h('div', {});
  const S = { chaves: null, conexoes: [], erro: '', mostrarRevogadas: false };
  const origem = location.origin;
  const urlApi = origem + '/api/v1';

  function botaoCopiar(texto, rotulo) {
    const b = h('button', { class: 'btn btn-sec btn-pequeno', type: 'button', 'aria-label': rotulo || 'Copiar' }, 'Copiar');
    b.addEventListener('click', async () => {
      const ok = await copiarTexto(texto);
      avisar(ok ? 'Copiado.' : 'Não foi possível copiar. Selecione o texto e copie.', ok ? undefined : 'erro');
    });
    return b;
  }
  const bloco = (texto, rotulo) => h('div', { class: 'api-bloco' }, h('pre', { class: 'api-codigo', tabindex: '0', 'aria-label': rotulo, text: texto }), botaoCopiar(texto, 'Copiar ' + rotulo.toLowerCase()));

  function linhaChave(c, revogada) {
    const meta = [`criada ${dataRelativa(c.criada_em)}`];
    if (revogada) meta.push(`revogada ${dataRelativa(c.revogada_em)}`);
    else meta.push(c.ultimo_uso ? `último uso ${dataRelativa(c.ultimo_uso)}` : 'nunca usada');
    return h('div', { class: 'ajuste chave-linha' },
      h('div', { class: 'ajuste-tx' },
        h('div', { class: 'ajuste-t' }, c.nome, ' ', h('span', { class: 'chave-comeco', text: `${c.comeco}…` })),
        h('div', { class: 'ajuste-d', text: meta.join(' · ') })),
      revogada ? null : h('button', { class: 'btn btn-texto btn-pequeno', type: 'button', 'aria-label': `Revogar a chave ${c.nome}`, onclick: () => revogar(c) }, 'Revogar'));
  }

  async function revogar(c) {
    const ok = await confirmar({ titulo: 'Revogar chave?', texto: `A automação que usa a chave “${c.nome}” para de funcionar na hora.`, ok: 'Revogar', perigo: true });
    if (!ok) return;
    try { await api(`/api/chaves/${encodeURIComponent(c.id)}`, { method: 'DELETE' }); avisar('Chave revogada.'); }
    catch (e) { avisar(e.message, 'erro'); }
    carregar();
  }

  function dialogoCriar() {
    const idT = uid('dlg');
    const campo = h('input', { type: 'text', id: uid('nome-chave'), maxlength: '60', autocomplete: 'off', spellcheck: 'false', placeholder: 'Ex.: n8n, GHL', 'aria-label': 'Nome da chave', 'data-foco': '' });
    const erro = h('p', { class: 'erro-inline', role: 'alert' });
    const corpo = h('div', { class: 'dialogo-corpo' });
    const acoes = h('div', { class: 'dialogo-acoes' });
    const titulo = h('h2', { id: idT, text: 'Nova chave de API' });
    const painel = h('div', { class: 'dialogo dialogo-chave', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' }, titulo, corpo, acoes);
    const m = abrirModal(painel);
    m.veu.addEventListener('mousedown', (e) => { if (e.target === m.veu) e.stopImmediatePropagation(); }, true); /* clique fora não pode perder a chave */

    const cancelar = h('button', { class: 'btn btn-sec', type: 'button', onclick: () => m.fechar() }, 'Cancelar');
    const criar = h('button', { class: 'btn btn-prim', type: 'button' }, 'Criar');
    function etapaNome() {
      corpo.replaceChildren(h('p', { text: 'Dê um nome que lembre onde ela será usada.' }), h('div', { class: 'campo' }, h('label', { for: campo.id, text: 'Nome' }), campo), erro);
      acoes.replaceChildren(cancelar, criar);
    }
    async function enviar() {
      const nome = campo.value.trim();
      if (!nome) { erro.textContent = 'Escreva um nome para a chave.'; campo.focus(); return; }
      criar.disabled = true; erro.textContent = '';
      let r;
      try { r = await api('/api/chaves', { method: 'POST', corpo: { nome } }); }
      catch (e) { erro.textContent = e.message; criar.disabled = false; return; }
      etapaChave(r.chave); /* a chave só existe aqui dentro */
      r = null;
      carregar();
    }
    function etapaChave(chave) {
      titulo.textContent = 'Chave criada';
      const idC = uid('chave');
      const caixa = h('textarea', { id: idC, class: 'textarea chave-completa', readonly: true, rows: '3', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Chave de API completa', 'data-foco': '' });
      caixa.value = chave;
      const copiar = h('button', { class: 'btn btn-sec', type: 'button' }, 'Copiar');
      copiar.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(caixa.value); avisar('Chave copiada.'); }
        catch (e) { caixa.focus(); caixa.select(); avisar('Não foi possível copiar sozinho. O texto está selecionado: copie com Ctrl+C.', 'erro'); }
      });
      corpo.replaceChildren(
        h('p', { class: 'chave-aviso', text: 'Copie agora. Ela não aparece de novo.' }),
        h('div', { class: 'campo' }, h('label', { for: idC, text: 'Sua chave' }), caixa), copiar);
      acoes.replaceChildren(h('button', { class: 'btn btn-prim', type: 'button', onclick: () => m.fechar() }, 'Pronto'));
      setTimeout(() => { caixa.focus(); caixa.select(); }, 60);
    }
    criar.addEventListener('click', enviar);
    campo.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); enviar(); } });
    etapaNome();
  }

  function comoUsar() {
    const det = h('details', { class: 'api-como' }, h('summary', { text: 'Como usar a API' }));
    const rotas = h('div', { class: 'api-rotas' }, h('p', { class: 'dica', text: 'Carregando rotas…' }));
    const corpo = h('div', { class: 'api-corpo' },
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'Endereço da API' }), bloco(urlApi, 'Endereço da API')),
      h('p', {}, 'Envie a chave no cabeçalho ', h('code', { text: 'Authorization: Bearer SUA_CHAVE' }), '.'),
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'Testar a chave' }), bloco(`curl -H "Authorization: Bearer SUA_CHAVE" ${origem}/api/v1/`, 'Exemplo de teste')),
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'Retirar contatos' }),
        bloco(`curl -X POST "${origem}/api/v1/listas/ID_DA_LISTA/retirar" \\\n  -H "Authorization: Bearer SUA_CHAVE" -H "Content-Type: application/json" \\\n  -d '{"pasta": "Disparo semana 47", "quantidade": 100}'`, 'Exemplo de retirada'),
        h('p', { class: 'dica', text: 'Retirar entrega os próximos contatos que ainda não estão em nenhuma pasta e já os guarda na pasta indicada; chamar de novo traz os seguintes.' })),
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'Rotas' }), rotas),
      h('p', { class: 'dica', text: 'Limite: 300 chamadas por minuto por chave. A chave só abre a API: não entra na página e não apaga nada.' }));
    det.append(corpo);
    let pedido = false;
    det.addEventListener('toggle', () => {
      if (!det.open || pedido) return; pedido = true;
      api('/api/v1/').then((r) => {
        const ls = (r && r.rotas) || [];
        rotas.replaceChildren(...(ls.length ? ls.map((x) => h('div', { class: 'api-rota' },
          h('span', { class: 'api-metodo', text: x.metodo }), h('code', { class: 'api-caminho', text: String(x.caminho || '').replace(/^\/api\/v1(?=\/)/, '').replace(/\{(id_|pasta_id)\}/g, '{id}') }),
          h('span', { class: 'api-desc', text: x.descricao || '' }))) : [h('p', { class: 'dica', text: 'Nenhuma rota listada.' })]));
      }).catch((e) => { pedido = false; rotas.replaceChildren(h('p', { class: 'dica', text: e.message })); });
    });
    return det;
  }

  function comoUsarMcp() {
    const det = h('details', { class: 'api-como' }, h('summary', { text: 'Conectar por MCP (Claude e outros agentes de IA)' }));
    const urlMcp = origem + '/mcp';
    const json = JSON.stringify({ mcpServers: { garimpo: { type: 'http', url: urlMcp, headers: { Authorization: 'Bearer SUA_CHAVE' } } } }, null, 2);
    det.append(h('div', { class: 'api-corpo' },
      h('p', { text: 'Agentes de IA usam o Garimpo por ferramentas: montar a busca com IA, gerar listas (cidade, estado ou Brasil), ler e retirar contatos, avaliar com IA. Nenhuma ferramenta apaga nada nem gasta o saldo de WhatsApp.' }),
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'Endereço do MCP' }), bloco(urlMcp, 'Endereço do MCP')),
      h('p', {}, h('strong', { text: 'Ferramentas com login (Claude, ChatGPT e outras): ' }), 'cole só o endereço. Ela abre uma página do Garimpo; você digita a senha e clica em Permitir. A ferramenta aparece aqui em cima, e dá para revogar quando quiser.'),
      h('p', {}, h('strong', { text: 'Ferramentas com chave fixa: ' }), 'use uma chave desta página, no cabeçalho ', h('code', { text: 'Authorization: Bearer SUA_CHAVE' }), '.'),
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'No Claude Code' }),
        bloco(`claude mcp add --transport http garimpo ${urlMcp} \\\n  --header "Authorization: Bearer SUA_CHAVE"`, 'Comando do Claude Code')),
      h('div', { class: 'campo' }, h('span', { class: 'api-rot', text: 'Em outros clientes (arquivo de configuração)' }), bloco(json, 'Configuração do MCP')),
      h('p', { class: 'dica', text: 'As ferramentas de IA gastam créditos da Anthropic dentro do teto do mês. Mesmo limite da API: 300 chamadas por minuto por chave.' })));
    return det;
  }

  async function revogarConexao(c) {
    const ok = await confirmar({ titulo: 'Revogar acesso?', texto: `“${c.nome}” perde o acesso ao Garimpo na hora. Para voltar, ela terá de pedir login de novo.`, ok: 'Revogar', perigo: true });
    if (!ok) return;
    try { await api(`/api/mcp/conexoes/${encodeURIComponent(c.id)}`, { method: 'DELETE' }); avisar('Acesso revogado.'); }
    catch (e) { avisar(e.message, 'erro'); }
    carregar();
  }
  function linhasConexoes() {
    if (!S.conexoes.length) return [];
    return [h('div', { class: 'ajuste' }, h('p', { class: 'ajuste-t', text: 'Ferramentas conectadas pelo login do MCP' })),
      ...S.conexoes.map((c) => h('div', { class: 'ajuste chave-linha' },
        h('div', { class: 'ajuste-tx' }, h('div', { class: 'ajuste-t', text: c.nome }),
          h('div', { class: 'ajuste-d', text: [`autorizada ${dataRelativa(c.criada_em)}`, c.ultimo_uso ? `último uso ${dataRelativa(c.ultimo_uso)}` : 'nunca usada'].join(' · ') })),
        h('button', { class: 'btn btn-texto btn-pequeno', type: 'button', 'aria-label': `Revogar o acesso de ${c.nome}`, onclick: () => revogarConexao(c) }, 'Revogar')))];
  }

  function desenhar() {
    let linhas;
    if (S.erro) linhas = [h('div', { class: 'ajuste' }, h('div', { class: 'ajuste-tx' }, h('div', { class: 'ajuste-d', text: S.erro }), h('button', { class: 'btn btn-sec btn-pequeno', type: 'button', onclick: carregar }, 'Tentar de novo')))];
    else if (!S.chaves) linhas = [h('div', { class: 'ajuste' }, esqueleto('60%', '18px'))];
    else {
      const ativas = S.chaves.filter((c) => !c.revogada_em), rev = S.chaves.filter((c) => c.revogada_em);
      linhas = ativas.length ? ativas.map((c) => linhaChave(c, false)) : [h('div', { class: 'ajuste' }, h('div', { class: 'ajuste-d', text: 'Nenhuma chave ainda.' }))];
      if (rev.length) {
        const b = h('button', { class: 'chave-rev-bt', type: 'button', 'aria-expanded': String(S.mostrarRevogadas), onclick: () => { S.mostrarRevogadas = !S.mostrarRevogadas; desenhar(); } },
          `${rev.length} ${rev.length === 1 ? 'revogada' : 'revogadas'}`);
        linhas.push(h('div', { class: 'ajuste' }, b));
        if (S.mostrarRevogadas) linhas.push(...rev.map((c) => linhaChave(c, true)));
      }
    }
    zona.replaceChildren(h('section', { class: 'ajustes' }, h('h2', { class: 'ajustes-tit', text: 'Chaves de API' }),
      h('div', { class: 'cartao cartao-ajustes' },
        h('div', { class: 'ajuste' }, h('p', { class: 'ajuste-d', text: 'Para suas automações (n8n, GHL) e agentes de IA (MCP) lerem listas e retirarem contatos sem usar a senha do sistema.' })),
        linhas,
        h('div', { class: 'ajuste' }, h('button', { class: 'btn btn-prim', type: 'button', disabled: !S.chaves, onclick: dialogoCriar }, 'Criar chave')),
        ...linhasConexoes(),
        h('div', { class: 'ajuste ajuste-bloco' }, comoUsar()),
        h('div', { class: 'ajuste ajuste-bloco' }, comoUsarMcp()))));
  }

  async function carregar() {
    try { S.chaves = await api('/api/chaves'); S.erro = ''; S.conexoes = await api('/api/mcp/conexoes').catch(() => []); }
    catch (e) { S.erro = e.status === 404 ? 'As chaves de API ainda não estão disponíveis neste servidor.' : e.message; }
    const abertos = [...zona.querySelectorAll('details.api-como')].map((d) => d.open);
    desenhar();
    zona.querySelectorAll('details.api-como').forEach((d, i) => { if (abertos[i]) d.open = true; });
  }
  desenhar();
  carregar();
  return zona;
}
