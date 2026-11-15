'use strict';
/* Solar Leads - telas WhatsApp e Base de dados (cartões no estilo Ajustes) */

function grupoAjustes(titulo, linhas, rodape) {
  return h('section', { class: 'ajustes' }, titulo ? h('h2', { class: 'ajustes-tit', text: titulo }) : null,
    h('div', { class: 'cartao cartao-ajustes' }, linhas), rodape ? h('p', { class: 'ajustes-rodape', text: rodape }) : null);
}

/* ---------- WhatsApp ---------- */
async function telaWhatsapp(raiz) {
  document.title = 'WhatsApp · Solar Leads';
  const rota = app.rota;
  const S = { w: null, qr: null, gerandoQr: false, erroQr: '', listas: [], timer: null, timerQr: null, vivo: true };
  const zonaEstado = h('div', {}, h('div', { class: 'cartao' }, h('div', { class: 'sk-pilha' }, esqueleto('40%', '22px'), esqueleto('70%', '14px'), esqueleto('100%', '56px'))));
  const zonaConexao = h('div', { hidden: true });
  const zonaVerif = h('div', {});
  const live = h('div', { class: 'so-leitor', 'aria-live': 'polite' });
  aoSair(() => { S.vivo = false; clearTimeout(S.timer); clearInterval(S.timerQr); });

  const aviso = h('div', { class: 'nota nota-laranja', role: 'note' }, icone('alerta', 20),
    h('div', {}, h('strong', { text: 'Use um chip que você aceite perder.' }), h('p', { text: 'Consultas em massa podem levar o WhatsApp a bloquear o número. Nunca conecte o número da API oficial.' })));

  function desenharEstado() {
    const w = S.w, on = Boolean(w && w.disponivel), t = (w && w.totais) || {};
    const dado = (k, r) => h('div', { class: 'mini-num' }, h('div', { class: 'mini-num-v', text: t[k] == null ? '—' : num(t[k]) }), h('div', { class: 'mini-num-r', text: r }));
    zonaEstado.replaceChildren(h('section', { class: 'cartao' },
      ajuste({ icone: 'whatsapp', tom: on ? 'verde' : 'cinza', titulo: on ? 'Conectado' : 'Não conectado',
        descricao: on ? (w.conta ? `Conta ${w.conta}` : 'Pronto para verificar números') : (w && w.motivo) || 'Nenhum WhatsApp conectado.',
        valor: h('span', { class: `status-ponto ${on ? 'on' : ''}`, 'aria-hidden': 'true' }) }),
      h('div', { class: 'mini-nums' }, dado('verificados', 'verificados'), dado('com_whatsapp', 'com WhatsApp'), dado('pessoais', 'pessoais'), dado('comerciais', 'comerciais'))));
  }

  function desenharConexao() {
    const on = Boolean(S.w && S.w.disponivel);
    zonaConexao.hidden = on;
    if (on) { clearInterval(S.timerQr); S.qr = null; zonaConexao.replaceChildren(); return; }
    const corpo = [h('div', {}, h('h2', { text: 'Conectar o WhatsApp' }), h('p', { class: 'apoio', text: 'Leia o código com o celular do chip que será usado nas consultas.' }))];
    if (S.qr) {
      corpo.push(h('div', { class: 'qr-area' },
        h('img', { class: 'qr', src: S.qr, alt: 'QR code para conectar o WhatsApp', width: 232, height: 232 }),
        h('ol', { class: 'passos' },
          h('li', {}, 'Abra o WhatsApp no celular.'),
          h('li', {}, 'Vá em ', h('strong', { text: 'WhatsApp > Aparelhos conectados > Conectar um aparelho' }), '.'),
          h('li', {}, 'Aponte a câmera para este código.'),
          h('li', { class: 'mudo', text: 'O código se renova a cada 20 segundos. Avisamos aqui quando conectar.' }))));
    } else {
      corpo.push(h('div', {}, h('button', { class: 'btn btn-prim', type: 'button', disabled: S.gerandoQr, onclick: gerarQr }, S.gerandoQr ? h('i', { class: 'giro', 'aria-hidden': 'true' }) : icone('sparkle', 18), S.gerandoQr ? 'Gerando…' : 'Gerar QR code')));
      if (S.erroQr) corpo.push(h('p', { class: 'erro-inline', role: 'alert', text: S.erroQr }));
    }
    zonaConexao.replaceChildren(h('section', { class: 'cartao cartao-qr' }, corpo));
  }

  async function gerarQr() {
    S.gerandoQr = true; S.erroQr = ''; desenharConexao();
    try {
      const r = await api('/api/whatsapp/qr', { method: 'POST' });
      if (!S.vivo) return;
      S.qr = r.qr; S.gerandoQr = false; desenharConexao(); agendar(true);
      clearInterval(S.timerQr);
      S.timerQr = setInterval(async () => {
        if (S.w && S.w.disponivel) { clearInterval(S.timerQr); return; }
        try { const n = await api('/api/whatsapp/qr', { method: 'POST' }); if (S.vivo) { S.qr = n.qr; desenharConexao(); } } catch (e) { if (e.status === 409) atualizar(); }
      }, 20000);
    } catch (e) {
      S.gerandoQr = false;
      if (e.status === 409) { await atualizar(); return; }
      S.erroQr = e.message; desenharConexao();
    }
  }

  function desenharVerificacao() {
    const w = S.w, v = (w && w.verificacao) || {}, on = Boolean(w && w.disponivel);
    const ritmo = 'Ritmo lento de propósito, para proteger o chip: cerca de 360 números por hora, das 8h às 20h, até 3.000 por dia.';
    if (v.rodando) {
      const lista = S.listas.find((m) => m.id === v.lista_id);
      const quanto = Math.min(100, (v.feitos_hoje || 0) / 3000 * 100);
      zonaVerif.replaceChildren(h('section', { class: 'cartao' },
        ajuste({ icone: 'sparkle', tom: 'laranja', titulo: 'Verificando números', descricao: lista ? `${rotuloLista(lista, true)} · ${regiaoDaLista(lista)}` : 'Em andamento', valor: selo('Rodando', 'laranja', { ponto: true }) }),
        h('div', { class: 'verif-roda' },
          h('div', { class: 'mini-nums' },
            h('div', { class: 'mini-num' }, h('div', { class: 'mini-num-v', text: num(v.feitos_hoje) }), h('div', { class: 'mini-num-r', text: 'feitos hoje' })),
            h('div', { class: 'mini-num' }, h('div', { class: 'mini-num-v', text: v.restantes == null ? '—' : num(v.restantes) }), h('div', { class: 'mini-num-r', text: 'restantes' }))),
          progresso(quanto, 'Verificações feitas hoje'),
          h('p', { class: 'apoio-peq', text: `${num(v.feitos_hoje)} de 3.000 permitidos hoje` }),
          v.mensagem ? h('p', { class: 'verif-msg', text: v.mensagem }) : null,
          h('div', {}, h('button', { class: 'btn btn-perigo', type: 'button', onclick: parar }, icone('parar', 18), 'Parar')),
          h('p', { class: 'dica', text: ritmo }))));
      return;
    }
    const prontas = S.listas.filter((m) => m.status === 'pronta');
    const sel = h('select', { id: 'sel-lista', disabled: !on || !prontas.length }, prontas.map((m) => h('option', { value: m.id, text: `${cidadeUF(m)} · ${num(m.resumo ? m.resumo.contatos_para_disparo : 0)} contatos` })));
    const botao = h('button', { class: 'btn btn-prim', type: 'button', disabled: !on || !prontas.length, onclick: () => iniciar(sel.value, botao) }, 'Iniciar verificação lenta');
    const motivo = !on ? 'Conecte um WhatsApp para poder verificar.' : !prontas.length ? 'Gere uma lista primeiro.' : '';
    zonaVerif.replaceChildren(h('section', { class: 'cartao' },
      ajuste({ icone: 'sparkle', tom: 'azul', titulo: 'Verificar números', descricao: 'Descobre quais números têm WhatsApp e se são pessoais ou comerciais.' }),
      v.mensagem ? h('p', { class: 'verif-msg', text: v.mensagem }) : null,
      h('div', { class: 'campo' }, h('label', { for: 'sel-lista', text: 'Lista para verificar' }), sel),
      h('div', { class: 'linha-botoes' }, botao, motivo ? h('span', { class: 'dica', text: motivo }) : null),
      h('p', { class: 'dica' }, ritmo)));
  }

  async function iniciar(id, botao) {
    botao.disabled = true;
    try { await api('/api/whatsapp/verificacao', { method: 'POST', corpo: { lista_id: id } }); avisar('Verificação iniciada.'); await atualizar(); }
    catch (e) { avisar(e.message, 'erro'); botao.disabled = false; }
  }
  async function parar() {
    try { await api('/api/whatsapp/verificacao', { method: 'DELETE' }); avisar('Verificação parada.'); await atualizar(); } catch (e) { avisar(e.message, 'erro'); }
  }

  async function atualizar() {
    try {
      const antes = S.w && S.w.disponivel;
      S.w = await api('/api/whatsapp');
      if (!S.vivo) return;
      if (S.w.disponivel && !antes && S.qr !== null) live.textContent = 'WhatsApp conectado.';
      desenharEstado(); desenharConexao(); desenharVerificacao();
      if (S.w.verificacao && S.w.verificacao.mensagem && S.w.verificacao.rodando) live.textContent = S.w.verificacao.mensagem;
    } catch (e) {
      if (!S.w) zonaEstado.replaceChildren(estadoErro(e.message, atualizar));
    }
    agendar();
  }
  function agendar(forcar) {
    clearTimeout(S.timer);
    const preciso = forcar || (S.qr && !(S.w && S.w.disponivel)) || (S.w && S.w.verificacao && S.w.verificacao.rodando);
    if (S.vivo && preciso) S.timer = setTimeout(atualizar, 3000);
  }

  raiz.append(cabecalhoPagina('WhatsApp', 'Descubra quais números têm WhatsApp antes de disparar.'), live, zonaEstado, zonaConexao, aviso, zonaVerif);
  try { S.listas = await api('/api/listas'); } catch (e) { S.listas = []; }
  if (rota !== app.rota) return;
  await atualizar();
}

/* ---------- Base de dados ---------- */
async function telaBase(raiz) {
  document.title = 'Base de dados · Solar Leads';
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
      blocoExportacoes(), blocoChaves(), blocoAcessos());
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
    'chave criada': ['check', 'verde', 'Chave de API criada'], 'chave revogada': ['alerta', 'laranja', 'Chave de API revogada'] };
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
  const S = { chaves: null, erro: '', mostrarRevogadas: false };
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
        h('div', { class: 'ajuste' }, h('p', { class: 'ajuste-d', text: 'Para suas automações (n8n, GHL) lerem listas e retirarem contatos sem usar a senha do sistema.' })),
        linhas,
        h('div', { class: 'ajuste' }, h('button', { class: 'btn btn-prim', type: 'button', disabled: !S.chaves, onclick: dialogoCriar }, 'Criar chave')),
        h('div', { class: 'ajuste ajuste-bloco' }, comoUsar()))));
  }

  async function carregar() {
    try { S.chaves = await api('/api/chaves'); S.erro = ''; }
    catch (e) { S.erro = e.status === 404 ? 'As chaves de API ainda não estão disponíveis neste servidor.' : e.message; }
    const aberto = zona.querySelector('details.api-como[open]');
    desenhar();
    if (aberto) { const d = zona.querySelector('details.api-como'); if (d) d.open = true; }
  }
  desenhar();
  carregar();
  return zona;
}
