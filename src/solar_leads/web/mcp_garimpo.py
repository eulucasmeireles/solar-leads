"""Servidor MCP do Garimpo: outros sistemas (Claude, agentes de IA, n8n) usam o Garimpo por ferramentas.

Fica em `/mcp`, no mesmo endereço do sistema, pelo transporte HTTP do MCP, e aceita as mesmas chaves
de API de `/api/v1` (cabeçalho `Authorization: Bearer <chave>`). As ferramentas chamam as mesmas
funções das rotas da v1, com as mesmas regras e mensagens.

O que fica de fora de propósito: apagar qualquer coisa, verificar WhatsApp (gasta o saldo pago da
CheckLeaked) e baixar PDF ou CSV (arquivos não cabem bem numa resposta de ferramenta).
As ferramentas de IA gastam créditos da Anthropic, dentro do teto mensal do sistema.
"""

from __future__ import annotations

import contextlib
import time
from types import ModuleType
from typing import Any

from fastapi import FastAPI, HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.routing import Route

from .. import perfis

CAMINHO = "/mcp"
ESPERA_DA_BUSCA_S = 100  # a busca com IA leva cerca de um minuto; depois disso a ferramenta devolve o id

INSTRUCOES = """Garimpo acha clientes B2B no Brasil a partir da base de empresas da Receita Federal (CNPJ):
monta listas com os donos das empresas e os telefones deles, pontuadas pelo encaixe num perfil de busca.

Caminho típico:
1. `montar_busca_com_ia` com a descrição do que se vende e de quem se quer encontrar, ou
   `buscar_atividades` + `contar_empresas` para montar a busca à mão.
2. `salvar_perfil_da_busca` (ou use um perfil de `listar_perfis`).
3. `criar_lista` com o perfil e o lugar: uma cidade (cidade + uf), um estado (só uf) ou o Brasil (nenhum).
   A lista leva de segundos a alguns minutos; acompanhe com `ver_lista` até o status "pronta".
4. `contatos_da_lista` para ler, ou `retirar_contatos` para pegar os próximos contatos ainda não usados
   e já guardá-los numa pasta (quem saiu não volta nas próximas retiradas).
Opcional: `avaliar_lista_com_ia` dá uma nota de 0 a 10 a cada empresa (gasta créditos de IA).
Os dados são pessoais (nome e telefone de donos de empresa): use só para prospecção legítima."""

LEITURA = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ESCRITA = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
PAGA = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

_s: ModuleType  # o módulo do servidor web, ligado em `montar`
mcp = MCPServer(
    name="garimpo",
    title="Garimpo",
    description="Busca de clientes B2B na base de empresas do Brasil, com perfis, listas, pastas e IA.",
    instructions=INSTRUCOES,
    version="1",
)


def _chamar(funcao, *args, **kwargs):
    """Chama a função da rota; qualquer erro vira uma mensagem que o agente lê (a v1 daria 400, 404 ou 500)."""
    try:
        return funcao(*args, **kwargs)
    except HTTPException as e:
        raise ToolError(str(e.detail)) from e
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001 - sem isso o agente só leria "Error executing tool"
        raise ToolError(f"Falha no Garimpo: {type(e).__name__}: {e}") from e


def _resumo_da_lista(m: dict) -> dict[str, Any]:
    resumo = m.get("resumo") or {}
    return {
        "id": m["id"], "nome": _s._rotulo(m), "perfil": (m.get("perfil") or {}).get("nome"), "status": m.get("status"),
        "etapa": m.get("etapa"), "erro": m.get("erro"), "aviso": m.get("aviso"), "criada_em": m.get("criada_em"),
        "contatos": resumo.get("contatos_para_disparo"), "contatos_fortes": resumo.get("faixa_A"),
        "atividades": [a["descricao"] for a in m.get("atividades") or []][:20],
    }


# ---------- base e busca ----------

@mcp.tool(annotations=LEITURA)
def situacao_da_base() -> dict[str, Any]:
    """Situação da base de empresas: mês da Receita usado, ANEEL e quantos WhatsApp já foram verificados."""
    return _chamar(_s.base)


@mcp.tool(annotations=LEITURA)
def buscar_cidades(texto: str) -> list[dict[str, Any]]:
    """Acha municípios brasileiros pelo nome (mínimo 2 letras). Devolve nome, UF e número de empresas."""
    return _chamar(_s.cidades, texto)


@mcp.tool(annotations=LEITURA)
def buscar_atividades(texto: str) -> list[dict[str, Any]]:
    """Acha atividades econômicas (CNAE) por palavra ou código (mínimo 3 letras). Devolve código e descrição."""
    return _chamar(_s.atividades, texto)


@mcp.tool(annotations=LEITURA)
def contar_empresas(
    atividades: list[str], cidade: str = "", uf: str = "", so_ativas: bool = True, incluir_mei: bool = False,
    idade_min: int = 0, portes: list[str] | None = None,
) -> dict[str, Any]:
    """Quantas empresas (sedes) cada atividade tem numa cidade (cidade + uf), num estado (só uf) ou no Brasil.

    `atividades`: códigos CNAE de 7 dígitos. `idade_min`: anos mínimos de empresa.
    `portes`: "01" micro, "03" pequeno, "05" médio ou grande (vazio = todos).
    """
    pedido = _s.Contagem(cidade=cidade, uf=uf, codigos=atividades, so_ativas=so_ativas, incluir_mei=incluir_mei,
                         idade_min=idade_min, portes=portes or [])
    return _chamar(_s.contar_para_o_perfil, pedido)


# ---------- busca com IA e perfis ----------

@mcp.tool(annotations=PAGA)
def montar_busca_com_ia(descricao: str) -> dict[str, Any]:
    """A IA lê a descrição (o que se vende e quem se quer encontrar, e onde) e monta uma proposta de busca:
    atividades com peso, filtros, critério de avaliação e o lugar. Leva cerca de um minuto e custa uns US$ 0,05.

    Se demorar mais que o normal, devolve o `busca_id` com status "pensando": consulte com `ver_busca_com_ia`.
    Com a proposta pronta, use `salvar_perfil_da_busca` e depois `criar_lista`.
    """
    busca_id = _chamar(_s.iniciar_busca, _s.PedidoBusca(texto=descricao))["id"]
    fim = time.monotonic() + ESPERA_DA_BUSCA_S
    while time.monotonic() < fim:
        estado = ver_busca_com_ia(busca_id)
        if estado["status"] != "pensando":
            return estado
        time.sleep(2)
    return ver_busca_com_ia(busca_id)


@mcp.tool(annotations=LEITURA)
def ver_busca_com_ia(busca_id: str) -> dict[str, Any]:
    """Andamento de uma busca com IA: status ("pensando", "pronta" ou "erro"), etapa, proposta e custo."""
    estado = _chamar(_s.andamento_da_busca, busca_id)
    return {k: estado.get(k) for k in ("id", "status", "etapa", "erro", "proposta", "custo_usd")}


@mcp.tool(annotations=ESCRITA)
def salvar_perfil_da_busca(busca_id: str, nome: str = "") -> dict[str, Any]:
    """Guarda a proposta de uma busca com IA pronta como perfil de busca. Devolve o perfil e o lugar sugerido,
    para usar em `criar_lista` (perfil = id; cidade e uf conforme `lugar`)."""
    estado = _chamar(_s.andamento_da_busca, busca_id)
    p = estado.get("proposta")
    if estado.get("status") != "pronta" or not p:
        raise ToolError("Esta busca ainda não tem proposta pronta. Consulte com ver_busca_com_ia.")
    receita = {
        "oferta": p["oferta"], "cliente_ideal": p["cliente_ideal"], "criterio_ia": p["criterio_ia"], "explicacao": p["explicacao"],
        "atividades": [{"codigo": a["codigo"], "descricao": a["descricao"], "peso": a["peso"]} for a in p["atividades"]],
        "palavras": p.get("palavras") or [], "filtros": p["filtros"],
    }
    try:
        perfil = perfis.criar(" ".join((nome or p["nome"]).split())[:80], receita)
    except perfis.PerfilInvalido as e:
        raise ToolError(str(e)) from e
    cidade = p.get("cidade")
    lugar = {"cidade": cidade["nome"], "uf": cidade["uf"]} if cidade else {"cidade": "", "uf": p.get("uf") or ""}
    return {"perfil": {"id": perfil["id"], "nome": perfil["nome"]}, "lugar": lugar}


@mcp.tool(annotations=LEITURA)
def listar_perfis() -> list[dict[str, Any]]:
    """Perfis de busca: os prontos ("solar" = energia solar, "geral" = sem perfil) e os salvos."""
    return [
        {"id": p["id"], "nome": p["nome"], "pronto": p.get("pronto", False), "oferta": (p.get("receita") or {}).get("oferta", ""),
         "atividades": len((p.get("receita") or {}).get("atividades") or [])}
        for p in _chamar(_s.listar_perfis)
    ]


@mcp.tool(annotations=LEITURA)
def ver_perfil(perfil_id: str) -> dict[str, Any]:
    """Um perfil de busca completo: atividades com peso, filtros e critério da IA."""
    return _chamar(_s.um_perfil, perfil_id)


# ---------- listas ----------

@mcp.tool(annotations=ESCRITA)
def criar_lista(
    perfil: str = "", cidade: str = "", uf: str = "", atividades: list[str] | None = None, palavras: list[str] | None = None,
) -> dict[str, Any]:
    """Gera uma lista de contatos. O lugar define o recorte:
    - cidade + uf: uma cidade (ache o nome exato com `buscar_cidades`);
    - só uf: o estado todo; nem cidade nem uf: o Brasil todo (estado e Brasil exigem atividades, do perfil ou aqui);
    - `palavras`: empresas com essas palavras no nome, no estado `uf` ou no Brasil.
    `perfil`: id de `listar_perfis` (atividades, pesos e filtros vêm dele). `atividades` substitui as do perfil.
    Volta o id na hora; a lista fica pronta em segundos ou minutos: acompanhe com `ver_lista`.
    """
    campos: dict[str, Any] = {"perfil": perfil, "cidade": cidade, "uf": uf}
    if atividades is not None:
        campos["cnaes"] = atividades
    if palavras:
        campos["palavras"] = palavras
    lista_id = _chamar(_s.criar_lista, _s.NovaLista(**campos))["id"]
    return ver_lista(lista_id)


@mcp.tool(annotations=LEITURA)
def ver_lista(lista_id: str) -> dict[str, Any]:
    """Uma lista: status ("na_fila", "gerando", "pronta" ou "erro"), etapa atual e número de contatos."""
    return _resumo_da_lista(_chamar(_s.uma_lista, lista_id))


@mcp.tool(annotations=LEITURA)
def listar_listas() -> list[dict[str, Any]]:
    """Todas as listas, das mais novas para as mais antigas."""
    return [_resumo_da_lista(m) for m in _chamar(_s.listar)]


@mcp.tool(annotations=LEITURA)
def contatos_da_lista(
    lista_id: str, pagina: int = 1, por_pagina: int = 50, ordem: str = "relevancia", busca: str = "",
    uf: list[str] | None = None, cidade: list[str] | None = None, encaixe_min: int | None = None, nota_ia_min: int | None = None,
    so_com_email: bool = False, fora_de_pastas: bool = False,
) -> dict[str, Any]:
    """Contatos de uma lista pronta, já sem repetidos, na ordem pedida.

    `ordem`: "relevancia", "encaixe", "ia" (nota da IA), "chance" (chance de ser o dono) ou "nome".
    `fora_de_pastas`: só quem ainda não está em nenhuma pasta. `por_pagina`: até 100.
    """
    f = _s.Filtros(uf=uf, cidade=cidade, encaixe_min=encaixe_min, ia_min=nota_ia_min, com_email=int(so_com_email),
                   busca=busca, ordem=ordem, pasta="fora" if fora_de_pastas else None)
    return _chamar(_s.contatos, lista_id, f, pagina=pagina, por_pagina=max(1, min(100, por_pagina)))


@mcp.tool(annotations=ESCRITA)
def retirar_contatos(
    lista_id: str, pasta: str, quantidade: int = 100, ordem: str = "relevancia",
    uf: list[str] | None = None, cidade: list[str] | None = None, encaixe_min: int | None = None, nota_ia_min: int | None = None,
) -> dict[str, Any]:
    """Entrega os próximos contatos da lista que ainda não estão em nenhuma pasta e já os guarda na pasta indicada
    (criada se não existir). Chamar de novo traz os seguintes: quem já saiu não volta. Até 1.000 por vez."""
    f = _s.Filtros(uf=uf, cidade=cidade, encaixe_min=encaixe_min, ia_min=nota_ia_min, ordem=ordem)
    return _chamar(_s.retirar, lista_id, _s.Retirada(pasta=pasta, quantidade=quantidade), f)


# ---------- avaliação com IA ----------

@mcp.tool(annotations=PAGA)
def avaliar_lista_com_ia(lista_id: str, quantidade: int = 500, criterio: str = "", so_estimar: bool = False) -> dict[str, Any]:
    """A IA dá uma nota de 0 a 10, com o motivo, às primeiras empresas da lista (só dados públicos da empresa).
    Custa cerca de US$ 0,06 por 1.000 empresas; empresas já avaliadas com o mesmo critério não pagam de novo.

    `criterio`: vazio usa o do perfil da lista. `so_estimar`: só devolve quantas entram e o custo, sem avaliar.
    Roda em segundo plano: acompanhe com `ver_avaliacao`; depois ordene os contatos por "ia".
    """
    estimativa = _chamar(_s.estado_da_avaliacao, lista_id, quantidade=quantidade, criterio=criterio)
    if so_estimar:
        return estimativa
    _chamar(_s.avaliar_lista, lista_id, _s.PedidoAvaliacao(quantidade=quantidade, criterio=criterio, amostra=False))
    return {"iniciada": True, "estimativa": {k: estimativa.get(k) for k in ("empresas", "ja_avaliadas", "custo_usd", "em_lote")}}


@mcp.tool(annotations=LEITURA)
def ver_avaliacao(lista_id: str) -> dict[str, Any]:
    """Andamento da avaliação com IA (só uma roda por vez no sistema)."""
    return _chamar(_s.estado_da_avaliacao, lista_id, so_andamento=1)


@mcp.tool(annotations=LEITURA)
def uso_da_ia() -> dict[str, Any]:
    """Gasto de IA do mês, o teto mensal e os modelos usados."""
    return _chamar(_s.uso_da_ia)


# ---------- pastas ----------

@mcp.tool(annotations=LEITURA)
def listar_pastas() -> list[dict[str, Any]]:
    """Todas as pastas (onde os contatos já usados ficam guardados)."""
    return _chamar(_s.listar_pastas)


@mcp.tool(annotations=LEITURA)
def contatos_da_pasta(pasta_id: int, pagina: int = 1, por_pagina: int = 50, busca: str = "") -> dict[str, Any]:
    """Contatos de uma pasta, com paginação (até 100 por página)."""
    return _chamar(_s.contatos_da_pasta, pasta_id, busca=busca, pagina=pagina, por_pagina=max(1, min(100, por_pagina)))


# ---------- ligação com o servidor web ----------

class _Transporte:
    """A rota /mcp. O gerenciador de sessões é refeito a cada vez que o servidor liga (ele só liga uma vez)."""

    async def __call__(self, scope, receive, send) -> None:
        await mcp.session_manager.handle_request(scope, receive, send)


def montar(app: FastAPI, servidor: ModuleType) -> None:
    """Liga o MCP ao servidor web. A chave de API é conferida antes, no middleware do servidor."""
    global _s
    _s = servidor
    vida_anterior = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def vida(aplicacao):
        # Sem estado (cada chamada é independente) e com respostas JSON: funciona atrás do Caddy e com
        # qualquer cliente. A proteção contra DNS rebinding fica desligada porque toda chamada exige chave.
        mcp.streamable_http_app(
            streamable_http_path=CAMINHO, stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with mcp.session_manager.run(), vida_anterior(aplicacao) as estado:
            yield estado

    app.router.lifespan_context = vida
    app.router.routes.insert(0, Route(CAMINHO, endpoint=_Transporte(), methods=["GET", "POST", "DELETE"]))
