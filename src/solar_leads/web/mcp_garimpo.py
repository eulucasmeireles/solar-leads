"""Servidor MCP do Garimpo: outros sistemas (Claude, agentes de IA, n8n) usam o Garimpo por ferramentas.

Fica em `/mcp`, no mesmo endereço do sistema, pelo transporte HTTP do MCP. Aceita dois jeitos de entrar:
as chaves de API de `/api/v1` (cabeçalho `Authorization: Bearer <chave>`) e o login OAuth do MCP
(`oauth.py`: a ferramenta abre a página do Garimpo e o dono autoriza com a senha). As ferramentas chamam
as mesmas funções das rotas da v1, com as mesmas regras e mensagens.

O que fica de fora de propósito: apagar listas, pastas ou perfis, e baixar PDF ou CSV (arquivos não cabem
bem numa resposta de ferramenta). As ferramentas de IA gastam créditos da Anthropic, dentro do teto mensal
do sistema; `verificar_whatsapp` gasta o saldo pago da CheckLeaked, com teto por pedido (`MAX_VERIFICAR_MCP`).
"""

from __future__ import annotations

import contextlib
import time
from types import ModuleType
from typing import Any

from fastapi import FastAPI, HTTPException
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.routing import Route

from .. import oauth, perfis

CAMINHO = "/mcp"
PAGINA_DE_AUTORIZACAO = "/mcp/autorizar"
# Abertas sem sessão: o cliente MCP as chama antes de ter acesso (a página pede a senha ela mesma).
ROTAS_DO_LOGIN = frozenset({"/authorize", "/token", "/register", "/revoke", PAGINA_DE_AUTORIZACAO})
ESPERA_DA_BUSCA_S = 100  # a busca com IA leva cerca de um minuto; depois disso a ferramenta devolve o id
MAX_VERIFICAR_MCP = 1000  # números por pedido de verificação de WhatsApp (cada um gasta uma consulta do saldo)

INSTRUCOES = """Garimpo acha clientes B2B no Brasil a partir da base de empresas da Receita Federal (CNPJ):
monta listas com os donos das empresas e os telefones deles, pontuadas pelo encaixe num perfil de busca.

Caminho típico:
1. `montar_busca_com_ia` com a descrição do que se vende e de quem se quer encontrar, ou
   `buscar_atividades` + `contar_empresas` para montar a busca à mão.
2. `salvar_perfil_da_busca` (ou use um perfil de `listar_perfis`).
3. `criar_lista` com o perfil e o lugar: uma cidade (cidade + uf), um estado (só uf) ou o Brasil (nenhum).
   A lista leva de segundos a alguns minutos; acompanhe com `ver_lista` até o status "pronta".
4. `verificar_whatsapp` confere quais números têm WhatsApp e se a conta é pessoal ou business (gasta o saldo
   pago de consultas; veja o custo antes com `so_estimar`). Contato não verificado não tem WhatsApp garantido.
5. `contatos_da_lista` para ler, ou `retirar_contatos` para pegar os próximos contatos ainda não usados
   e já guardá-los numa pasta (quem saiu não volta nas próximas retiradas). Os dois filtram por
   `whatsapp` (ex.: ["pessoal"]). Para retirar contatos escolhidos, passe os `contato_id` em `ids`.
6. `marcar_nao_usar` para quem pediu para sair ou bloqueou: some de todas as listas e pastas.
Opcional: `avaliar_lista_com_ia` dá uma nota de 0 a 10 a cada empresa (gasta créditos de IA).
Cada contato traz um `contato_id`, igual em todas as listas e pastas, para marcar sem usar o telefone.
Os dados são pessoais (nome e telefone de donos de empresa): use só para prospecção legítima."""

LEITURA = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ESCRITA = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
PAGA = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
DEFINITIVA = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

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


def _situacoes(whatsapp: list[str] | None) -> list[str] | None:
    """O filtro `whatsapp` das ferramentas: "com_whatsapp" vale para qualquer conta, pessoal ou business."""
    if not whatsapp:
        return None
    validos = set(_s.SITUACOES_WHATSAPP)
    errados = [w for w in whatsapp if w not in validos]
    if errados:
        raise ToolError(f"whatsapp aceita {', '.join(_s.SITUACOES_WHATSAPP)}; veio {', '.join(errados)}.")
    saida = set(whatsapp)
    if "com_whatsapp" in saida:
        saida |= {"pessoal", "business"}
    return sorted(saida)


def _filtros(uf, cidade, encaixe_min, nota_ia_min, ordem, whatsapp=None, **mais):
    return _s.Filtros(uf=uf, cidade=cidade, encaixe_min=encaixe_min, ia_min=nota_ia_min, ordem=ordem,
                      situacao_whatsapp=_situacoes(whatsapp), **mais)


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
    so_com_email: bool = False, fora_de_pastas: bool = False, whatsapp: list[str] | None = None, ids: list[str] | None = None,
) -> dict[str, Any]:
    """Contatos de uma lista pronta, já sem repetidos, na ordem pedida. Cada um traz `contato_id`.

    `ordem`: "relevancia", "encaixe", "ia" (nota da IA), "chance" (chance de ser o dono) ou "nome".
    `whatsapp`: "pessoal", "business", "com_whatsapp" (qualquer um dos dois), "sem_whatsapp" ou "nao_verificado".
    `fora_de_pastas`: só quem ainda não está em nenhuma pasta. `ids`: só esses contatos. `por_pagina`: até 100.
    """
    f = _filtros(uf, cidade, encaixe_min, nota_ia_min, ordem, whatsapp, com_email=int(so_com_email), busca=busca,
                 pasta="fora" if fora_de_pastas else None, ids=[i.strip().lower() for i in ids] if ids else None)
    return _chamar(_s.contatos, lista_id, f, pagina=pagina, por_pagina=max(1, min(100, por_pagina)))


@mcp.tool(annotations=ESCRITA)
def retirar_contatos(
    lista_id: str, pasta: str, quantidade: int = 100, ordem: str = "relevancia",
    uf: list[str] | None = None, cidade: list[str] | None = None, encaixe_min: int | None = None, nota_ia_min: int | None = None,
    whatsapp: list[str] | None = None, ids: list[str] | None = None,
) -> dict[str, Any]:
    """Entrega os próximos contatos da lista que ainda não estão em nenhuma pasta e já os guarda na pasta indicada
    (criada se não existir). Chamar de novo traz os seguintes: quem já saiu não volta. Até 1.000 por vez.

    `whatsapp`: mesmo filtro de `contatos_da_lista` (ex.: ["pessoal"] para só WhatsApp pessoal confirmado).
    `ids`: retira só esses `contato_id` (até 1.000; `quantidade` é ignorada); os que não saírem voltam em
    `nao_retirados`, com o motivo (já numa pasta, marcado para não usar, fora da lista ou do filtro).
    """
    f = _filtros(uf, cidade, encaixe_min, nota_ia_min, ordem, whatsapp)
    return _chamar(_s.retirar, lista_id, _s.Retirada(pasta=pasta, quantidade=quantidade, ids=ids), f)


# ---------- WhatsApp e não usar ----------

@mcp.tool(annotations=PAGA)
def verificar_whatsapp(
    lista_id: str, quantidade: int = 200, so_estimar: bool = False, ordem: str = "relevancia",
    uf: list[str] | None = None, cidade: list[str] | None = None, encaixe_min: int | None = None, nota_ia_min: int | None = None,
) -> dict[str, Any]:
    """Confere pela CheckLeaked se os números têm WhatsApp e se a conta é pessoal ou business.

    Pega os próximos contatos da lista ainda não verificados e fora de pastas, na `ordem` e nos filtros pedidos.
    Cada número novo gasta 1 consulta do saldo pago (mesmo sem WhatsApp); quem já foi verificado nos últimos
    30 dias não gasta. Até 1.000 números por pedido, um pedido por vez no sistema.
    `so_estimar`: só diz quantos números entram, quantas consultas gastariam e o saldo, sem verificar.
    Roda em segundo plano (~3 s por número, 4 ao mesmo tempo): acompanhe com `ver_verificacao_whatsapp`.
    No fim a lista é atualizada sozinha; depois disso filtre por `whatsapp` em `contatos_da_lista` ou `retirar_contatos`.
    """
    meta = _chamar(_s.uma_lista, lista_id)
    f = _filtros(uf, cidade, encaixe_min, nota_ia_min, ordem)
    n = max(1, min(MAX_VERIFICAR_MCP, quantidade))
    escolher = lambda: _s._numeros_da_lista(meta, n, f)  # noqa: E731
    if so_estimar:
        return _chamar(lambda: _s._estimar_verificacao(escolher()))
    r = _chamar(_s.comecar_verificacao, meta, escolher)
    return {"iniciada": True, "numeros": r["numeros"], "acompanhe_com": "ver_verificacao_whatsapp"}


@mcp.tool(annotations=LEITURA)
def ver_verificacao_whatsapp() -> dict[str, Any]:
    """Andamento da verificação de WhatsApp, o resumo quando termina, o saldo de consultas e se a lista
    já foi atualizada com os resultados (`lista_atualizando` falso = pode filtrar por `whatsapp`)."""
    estado = _chamar(_s.estado_whatsapp)
    v = dict(estado["verificacao"])
    if v.get("lista_id"):
        with contextlib.suppress(Exception):
            m = _s.uma_lista(v["lista_id"])
            v["lista_atualizando"] = bool(m.get("atualizando")) or m.get("status") != "pronta"
    v["saldo"] = (estado.get("servico") or {}).get("saldo")
    v["totais"] = estado.get("totais")
    return v


@mcp.tool(annotations=DEFINITIVA)
def marcar_nao_usar(contato_ids: list[str], motivo: str = "", lista_id: str = "") -> dict[str, Any]:
    """Marca contatos para nunca mais serem usados: quem pediu para sair, bloqueou ou reclamou.

    O contato some de todas as listas, consultas, retiradas e exportações, e sai de todas as pastas em que estiver.
    `contato_ids`: os `contato_id` (até 1.000). `lista_id`: a lista de onde vieram, se não estiverem em pasta.
    `motivo`: curto, ex. "bloqueou", "pediu para sair". Desfazer só pela pessoa dona do Garimpo.
    """
    if not contato_ids or len(contato_ids) > 1000:
        raise ToolError("Mande de 1 a 1.000 contato_id por vez.")
    achados = _chamar(_s.telefones_dos_ids, contato_ids, lista_id)
    novos = _chamar(_s.pastas.bloquear, sorted(set(achados.values())), " ".join(motivo.split())[:120] or "MCP")
    pedidos = {i.strip().lower() for i in contato_ids if i and i.strip()}
    return {"marcados": len(achados), "novos": novos, "nao_achados": sorted(pedidos - achados.keys())}


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

    # Login OAuth do MCP (descoberta, cadastro do cliente, autorização, troca do código, revogação).
    base = servidor.URL_PUBLICA
    provedor = oauth.Provedor(lambda pedido: f"{base}{PAGINA_DE_AUTORIZACAO}?pedido={pedido}")
    rotas = create_auth_routes(
        provedor, issuer_url=AnyHttpUrl(base),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )
    recurso = create_protected_resource_routes(
        resource_url=AnyHttpUrl(f"{base}{CAMINHO}"), authorization_servers=[AnyHttpUrl(base)], resource_name="Garimpo",
    )
    # Há clientes que procuram os dados do recurso na raiz, sem o "/mcp" no fim.
    raiz = Route("/.well-known/oauth-protected-resource", endpoint=recurso[0].endpoint, methods=["GET", "OPTIONS"])
    for rota in [Route(CAMINHO, endpoint=_Transporte(), methods=["GET", "POST", "DELETE"]), *rotas, *recurso, raiz]:
        app.router.routes.insert(0, rota)
