"""Servidor MCP (/mcp): exige a chave de API e expõe as ferramentas do Garimpo."""

import anyio
import pytest
from mcp import Client
from mcp_types.version import LATEST_PROTOCOL_VERSION

from solar_leads import perfis
from solar_leads.web import mcp_garimpo, servidor
from test_api import chave, cliente, com_chave, limpo, logado  # noqa: F401 - fixtures

JSONRPC = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _inicio(id_=1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": "initialize", "params": {
        "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "teste", "version": "1"}}}


def test_sem_chave_o_mcp_recusa():
    with cliente() as c:
        r = c.post("/mcp", json=_inicio(), headers=JSONRPC)
        assert r.status_code == 401
        assert "Bearer" in r.json()["detail"]


def test_chave_errada_o_mcp_recusa():
    with com_chave("garimpo_errada") as c:
        assert c.post("/mcp", json=_inicio(), headers=JSONRPC).status_code == 401


def test_com_chave_o_mcp_responde(chave):  # noqa: F811
    with com_chave(chave) as c:
        r = c.post("/mcp", json=_inicio(), headers=JSONRPC)
        assert r.status_code == 200, r.text
        resposta = r.json()["result"]
        assert resposta["serverInfo"]["name"] == "garimpo"
        assert "criar_lista" in resposta["instructions"]


def _chamar(nome: str, argumentos: dict | None = None):
    async def rodar():
        async with Client(mcp_garimpo.mcp) as c:
            return await c.call_tool(nome, argumentos or {})

    return anyio.run(rodar)


def test_ferramentas_cobrem_o_caminho_e_nada_apaga():
    async def rodar():
        async with Client(mcp_garimpo.mcp) as c:
            return (await c.list_tools()).tools

    ferramentas = {f.name: f for f in anyio.run(rodar)}
    for nome in ("montar_busca_com_ia", "salvar_perfil_da_busca", "criar_lista", "ver_lista", "contatos_da_lista",
                 "retirar_contatos", "avaliar_lista_com_ia", "buscar_cidades", "contar_empresas", "listar_pastas"):
        assert nome in ferramentas
    assert not any(p in nome for nome in ferramentas for p in ("excluir", "apagar", "revogar", "whatsapp"))
    assert all(f.annotations and f.annotations.destructive_hint is not True for f in ferramentas.values())
    assert ferramentas["listar_listas"].annotations.read_only_hint is True


def test_listar_perfis_traz_os_prontos():
    r = _chamar("listar_perfis")
    assert not r.is_error
    ids = [p["id"] for p in r.structured_content["result"]]
    assert ids[:2] == ["solar", "geral"]


def test_erro_do_sistema_vira_mensagem_para_o_agente(monkeypatch):
    monkeypatch.setattr(servidor, "ufs", lambda: ["MG"])
    r = _chamar("ver_lista", {"lista_id": "nao-existe"})
    assert r.is_error and "não encontrada" in r.content[0].text.lower()
    r = _chamar("criar_lista", {"perfil": "geral", "uf": "MG"})
    assert r.is_error and "atividades" in r.content[0].text
    monkeypatch.setattr(servidor, "base", lambda: 1 / 0)
    r = _chamar("situacao_da_base")
    assert r.is_error and "ZeroDivisionError" in r.content[0].text


def test_salvar_perfil_da_busca_guarda_a_proposta_e_devolve_o_lugar():
    proposta = {
        "nome": "Agenda para estética", "oferta": "Agenda online", "cliente_ideal": "Clínicas de estética",
        "criterio_ia": "Vendo agenda online para clínicas de estética com equipe.", "explicacao": "",
        "cidade": None, "uf": "MG", "palavras": [], "total": 10,
        "atividades": [{"codigo": "9602502", "descricao": "Estética", "peso": 3, "motivo": "", "empresas": 10}],
        "filtros": {"so_ativas": True, "incluir_mei": False, "so_locais": True, "idade_min": 2, "portes": []},
    }
    servidor._buscas["b1"] = {"id": "b1", "status": "pronta", "etapa": None, "texto": "x", "proposta": proposta,
                              "erro": None, "criada_em": "2026-11-17T10:00:00"}
    try:
        r = _chamar("salvar_perfil_da_busca", {"busca_id": "b1"})
        assert not r.is_error, r.content
        saida = r.structured_content
        assert saida["lugar"] == {"cidade": "", "uf": "MG"}
        guardado = perfis.um(saida["perfil"]["id"])
        assert guardado["receita"]["atividades"][0]["peso"] == 3 and guardado["receita"]["filtros"]["idade_min"] == 2
        perfis.excluir(saida["perfil"]["id"])
    finally:
        servidor._buscas.pop("b1", None)


def test_busca_que_nao_terminou_nao_vira_perfil():
    servidor._buscas["b2"] = {"id": "b2", "status": "pensando", "etapa": "x", "texto": "x", "proposta": None,
                              "erro": None, "criada_em": "2026-11-17T10:00:00"}
    try:
        r = _chamar("salvar_perfil_da_busca", {"busca_id": "b2"})
        assert r.is_error and "ainda não" in r.content[0].text
    finally:
        servidor._buscas.pop("b2", None)
