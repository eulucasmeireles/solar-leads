'use strict';
/* Garimpo - avaliação dos contatos pela IA: critério, custo antes de começar, amostra de 30 e andamento */

const QUANTIDADES_AVALIAR = [{ valor: '200', rotulo: '200' }, { valor: '500', rotulo: '500' }, { valor: '1000', rotulo: '1.000' }, { valor: '100000', rotulo: 'Todas' }];

function dialogoAvaliar(meta) {
  const idT = uid('dlg');
  const idUrl = encodeURIComponent(meta.id);
  let quantidade = '500', pedido = 0, vivo = true, timer = null, estimativa = null;
  const criterio = h('textarea', { class: 'textarea', id: uid('crit'), rows: '4', maxlength: '2000', 'aria-label': 'Critério da IA',
    placeholder: 'Ex.: Vendo software de agendamento. O cliente ideal é clínica ou consultório com equipe, alguns anos de empresa e porte para pagar mensalidade.' });
  const conta = h('p', { class: 'verif-conta', 'aria-live': 'polite' }, esqueleto('80%', '14px'));
  const erro = h('p', { class: 'erro-inline', role: 'alert' });
  const cancelar = h('button', { class: 'btn btn-sec', type: 'button', onclick: () => m.fechar() }, 'Cancelar');
  const amostra = h('button', { class: 'btn btn-sec', type: 'button', disabled: true }, 'Amostra de 30');
  const avaliar = h('button', { class: 'btn btn-prim', type: 'button', disabled: true }, 'Avaliar');
  const corpo = h('div', { class: 'avaliar-corpo' });
  const acoes = h('div', { class: 'dialogo-acoes' });
  const painel = h('div', { class: 'dialogo dialogo-largo dialogo-avaliar', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idT, tabindex: '-1' },
    h('h2', { id: idT, text: 'Avaliar com IA' }), corpo, erro, acoes);
  const m = abrirModal(painel, { aoFechar: () => { vivo = false; clearTimeout(timer); } });

  async function estimar() {
    const meu = ++pedido;
    amostra.disabled = true; avaliar.disabled = true;
    try {
      const e = await api(`/api/listas/${idUrl}/avaliacao?quantidade=${quantidade}&criterio=${encodeURIComponent(criterio.value.trim())}`);
      if (meu !== pedido || !vivo) return;
      estimativa = e;
      if (!criterio.value.trim()) criterio.value = e.criterio || '';
      if (e.andamento && e.andamento.rodando) { mostrarAndamento(false); return; }
      const partes = [`${num(e.empresas)} ${e.empresas === 1 ? 'empresa' : 'empresas'}`];
      if (e.ja_avaliadas) partes.push(`${num(e.ja_avaliadas)} já com nota, sem custo`);
      partes.push(`custo estimado ${usd(e.custo_usd)}`);
      conta.replaceChildren(h('strong', { text: partes.join(' · ') }), h('br'),
        h('span', { text: !e.configurada ? 'Falta a chave da Anthropic no servidor.' : `Gasto de IA no mês: ${usd(e.gasto_usd)} de ${usd(e.teto_usd)}.${e.em_lote ? ' Muitas empresas: vai em lote, pela metade do preço, e pode levar até 1 hora.' : ''}` }));
      const ok = e.configurada && criterio.value.trim().length >= 15 && e.empresas > 0;
      avaliar.disabled = !ok; amostra.disabled = !ok;
      avaliar.textContent = e.a_avaliar ? `Avaliar ${num(e.empresas)}` : 'Aplicar as notas';
    } catch (err) { if (meu === pedido) conta.textContent = err.message; }
  }
  const estimarDepois = debounce(estimar, 400);
  criterio.addEventListener('input', estimarDepois);
  const seg = segmentado({ opcoes: QUANTIDADES_AVALIAR, valor: quantidade, rotulo: 'Quantas empresas avaliar', papel: 'radio', aoMudar: (v) => { quantidade = v; estimar(); } });

  async function comecar(soAmostra) {
    erro.textContent = '';
    avaliar.disabled = true; amostra.disabled = true;
    try {
      await api(`/api/listas/${idUrl}/avaliar`, { method: 'POST', corpo: { quantidade: Number(quantidade), criterio: criterio.value.trim(), amostra: soAmostra } });
      mostrarAndamento(soAmostra);
    } catch (e) { erro.textContent = e.message; avaliar.disabled = false; amostra.disabled = false; }
  }
  amostra.addEventListener('click', () => comecar(true));
  avaliar.addEventListener('click', () => comecar(false));

  function formulario() {
    corpo.replaceChildren(
      h('p', { text: 'A IA dá uma nota de 0 a 10 a cada empresa, na ordem da lista, com o motivo. Vão para a IA só dados públicos da empresa: nunca telefone, e-mail ou nome do dono.' }),
      h('div', { class: 'campo campo-esq' }, h('label', { for: criterio.id, text: 'Critério' }), criterio),
      seg.el, conta);
    acoes.replaceChildren(cancelar, amostra, avaliar);
    estimar();
  }

  function mostrarAndamento(eraAmostra) {
    const barra = h('div', {});
    const msg = h('p', { class: 'verif-msg', 'aria-live': 'polite' });
    const parar = h('button', { class: 'btn btn-perigo', type: 'button' }, icone('parar', 18), 'Parar');
    parar.addEventListener('click', async () => { try { await api(`/api/listas/${idUrl}/avaliar`, { method: 'DELETE' }); } catch (e) { erro.textContent = e.message; } });
    corpo.replaceChildren(h('div', { class: 'avaliar-andamento' }, h('span', { class: 'ia-selo' }, icone('sparkle', 22)), barra, msg));
    acoes.replaceChildren(h('button', { class: 'btn btn-sec', type: 'button', onclick: () => m.fechar() }, 'Fechar'), parar);
    async function vigiar() {
      try {
        const e = await api(`/api/listas/${idUrl}/avaliacao?so_andamento=1`);
        if (!vivo) return;
        const a = e.andamento || {};
        barra.replaceChildren(progresso(a.total ? a.feitos / a.total * 100 : null, 'Empresas avaliadas'));
        msg.textContent = a.mensagem || 'Avaliando…';
        if (!a.rodando) { terminou(e, eraAmostra || !!a.amostra); return; }
      } catch (err) { if (!vivo) return; msg.textContent = err.message; }
      timer = setTimeout(vigiar, 1500);
    }
    vigiar();
  }

  function terminou(e, eraAmostra) {
    const a = e.andamento || {};
    if (eraAmostra && Array.isArray(a.amostra) && a.amostra.length) {
      const linhas = a.amostra.map((x) => h('li', {}, h('span', { class: `nota-ia-n tom-${x.nota >= 7 ? 'verde' : x.nota >= 4 ? 'laranja' : 'cinza'}`, text: String(x.nota) }),
        h('div', {}, h('strong', { text: x.empresa || 'Empresa sem nome fantasia' }), h('span', { text: x.motivo }))));
      corpo.replaceChildren(h('p', { text: 'Confira as notas da amostra. Se o critério estiver bom, continue com as demais empresas; se não, ajuste o critério e teste de novo.' }),
        h('ul', { class: 'amostra-ia' }, linhas), h('p', { class: 'dica', text: a.mensagem || '' }));
      const continuar = h('button', { class: 'btn btn-prim', type: 'button' }, Number(quantidade) >= 100000 ? 'Continuar com todas' : `Continuar com ${num(Number(quantidade))}`);
      continuar.addEventListener('click', () => comecar(false));
      const ajustar = h('button', { class: 'btn btn-sec', type: 'button', onclick: formulario }, 'Ajustar critério');
      acoes.replaceChildren(ajustar, continuar);
      return;
    }
    corpo.replaceChildren(h('div', { class: 'avaliar-andamento' }, h('span', { class: 'ia-selo' }, icone('check', 22)), h('p', { class: 'verif-msg', text: a.mensagem || 'Avaliação concluída.' })));
    acoes.replaceChildren(h('button', { class: 'btn btn-prim', type: 'button', onclick: () => { m.fechar(); rotear(); } }, 'Ver a lista'));
  }

  formulario();
}
