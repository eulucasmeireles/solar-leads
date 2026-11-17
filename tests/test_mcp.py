"""Servidor MCP (/mcp): exige a chave de API e expõe as ferramentas do Garimpo."""

import json

import anyio
import pytest
from mcp import Client
from mcp_types.version import LATEST_PROTOCOL_VERSION

from solar_leads import perfis, pipeline
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
    assert not any(p in nome for nome in ferramentas for p in ("excluir", "apagar", "revogar"))
    # só "não usar" é definitiva (tira o contato de tudo); verificar WhatsApp gasta saldo
    assert {n for n, f in ferramentas.items() if f.annotations.destructive_hint is True} == {"marcar_nao_usar"}
    assert ferramentas["verificar_whatsapp"].annotations.open_world_hint is True
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


# ---------- login OAuth do MCP ----------

import base64  # noqa: E402
import hashlib  # noqa: E402
import secrets  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

from conftest import SENHA_TESTE  # noqa: E402
from solar_leads import oauth  # noqa: E402

VOLTA = "http://localhost:33418/callback"


def _pkce():
    verificador = secrets.token_urlsafe(48)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, desafio


def _cadastrar(c) -> str:
    r = c.post("/register", json={"redirect_uris": [VOLTA], "client_name": "Ferramenta de teste", "token_endpoint_auth_method": "none",
                                  "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _pedir(c, client_id, desafio) -> str:
    r = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": VOLTA,
                                    "code_challenge": desafio, "code_challenge_method": "S256", "state": "estado-1"})
    assert r.status_code == 302, r.text
    destino = urlparse(r.headers["location"])
    assert destino.path == mcp_garimpo.PAGINA_DE_AUTORIZACAO
    return parse_qs(destino.query)["pedido"][0]


def _autorizar(c, client_id, desafio, senha=SENHA_TESTE) -> str:
    pedido = _pedir(c, client_id, desafio)
    r = c.post(mcp_garimpo.PAGINA_DE_AUTORIZACAO, data={"pedido": pedido, "decisao": "permitir", "senha": senha})
    assert r.status_code == 303, r.text
    volta = urlparse(r.headers["location"])
    assert f"{volta.scheme}://{volta.netloc}{volta.path}" == VOLTA and parse_qs(volta.query)["state"] == ["estado-1"]
    return parse_qs(volta.query)["code"][0]


def _trocar(c, client_id, codigo, verificador) -> dict:
    r = c.post("/token", data={"grant_type": "authorization_code", "code": codigo, "redirect_uri": VOLTA,
                               "client_id": client_id, "code_verifier": verificador})
    assert r.status_code == 200, r.text
    return r.json()


def test_descoberta_aponta_para_o_login():
    with cliente() as c:
        recusa = c.post("/mcp", json=_inicio(), headers=JSONRPC)
        assert recusa.status_code == 401
        assert 'resource_metadata="https://garimpo.lucasmeireles.pro/.well-known/oauth-protected-resource/mcp"' in recusa.headers["www-authenticate"]
        for caminho in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
            r = c.get(caminho)
            assert r.status_code == 200 and r.json()["resource"] == "https://garimpo.lucasmeireles.pro/mcp"
        meta = c.get("/.well-known/oauth-authorization-server").json()
        assert meta["registration_endpoint"] == "https://garimpo.lucasmeireles.pro/register"
        assert "S256" in meta["code_challenge_methods_supported"]
        assert c.get("/.well-known/openid-configuration").status_code == 404  # e não a tela de senha


def test_login_completo_da_acesso_so_ao_mcp_renova_e_revoga(logado):  # noqa: F811
    verificador, desafio = _pkce()
    with cliente() as c:
        client_id = _cadastrar(c)
        pagina = c.get(mcp_garimpo.PAGINA_DE_AUTORIZACAO, params={"pedido": _pedir(c, client_id, desafio)})
        assert pagina.status_code == 200 and "Ferramenta de teste" in pagina.text and 'type="password"' in pagina.text
        tokens = _trocar(c, client_id, _autorizar(c, client_id, desafio), verificador)
    acesso = tokens["access_token"]
    assert acesso.startswith("gma_") and tokens["refresh_token"].startswith("gmr_")
    with com_chave(acesso) as c:
        assert c.post("/mcp", json=_inicio(), headers=JSONRPC).status_code == 200
        assert c.get("/api/v1/").status_code == 401  # o login do MCP não abre a API v1
        assert c.get("/api/listas").status_code == 401  # nem a página
    with cliente() as c:  # renovar troca o par: o acesso antigo deixa de valer
        r = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id})
        assert r.status_code == 200, r.text
        novo = r.json()["access_token"]
        assert c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}).status_code == 400
    with com_chave(acesso) as c:
        assert c.post("/mcp", json=_inicio(), headers=JSONRPC).status_code == 401
    with com_chave(novo) as c:
        assert c.post("/mcp", json=_inicio(), headers=JSONRPC).status_code == 200
    conexoes = logado.get("/api/mcp/conexoes").json()
    assert [x["nome"] for x in conexoes] == ["Ferramenta de teste"]
    assert logado.delete(f"/api/mcp/conexoes/{conexoes[0]['id']}").status_code == 200
    with com_chave(novo) as c:
        assert c.post("/mcp", json=_inicio(), headers=JSONRPC).status_code == 401
    assert logado.get("/api/mcp/conexoes").json() == []


def test_senha_errada_nao_autoriza_e_o_codigo_vale_uma_vez():
    verificador, desafio = _pkce()
    with cliente() as c:
        client_id = _cadastrar(c)
        pedido = _pedir(c, client_id, desafio)
        r = c.post(mcp_garimpo.PAGINA_DE_AUTORIZACAO, data={"pedido": pedido, "decisao": "permitir", "senha": "errada"})
        assert r.status_code == 401 and "Senha incorreta" in r.text
        servidor._tentativas.clear()
        codigo = _autorizar(c, client_id, desafio)
        _trocar(c, client_id, codigo, verificador)
        assert c.post("/token", data={"grant_type": "authorization_code", "code": codigo, "redirect_uri": VOLTA,
                                      "client_id": client_id, "code_verifier": verificador}).status_code == 400
    for conexao in oauth.conexoes():
        oauth.revogar_conexao(conexao["id"])


def test_recusar_volta_para_a_ferramenta_com_acesso_negado():
    _, desafio = _pkce()
    with cliente() as c:
        client_id = _cadastrar(c)
        r = c.post(mcp_garimpo.PAGINA_DE_AUTORIZACAO, data={"pedido": _pedir(c, client_id, desafio), "decisao": "recusar"})
        assert r.status_code == 303
        assert parse_qs(urlparse(r.headers["location"]).query)["error"] == ["access_denied"]


def test_quem_ja_esta_dentro_so_confirma(logado):  # noqa: F811
    verificador, desafio = _pkce()
    client_id = _cadastrar(logado)
    pedido = _pedir(logado, client_id, desafio)
    assert 'type="password"' not in logado.get(mcp_garimpo.PAGINA_DE_AUTORIZACAO, params={"pedido": pedido}).text
    r = logado.post(mcp_garimpo.PAGINA_DE_AUTORIZACAO, data={"pedido": pedido, "decisao": "permitir"})
    assert r.status_code == 303 and "code=" in r.headers["location"]
    for conexao in oauth.conexoes():
        oauth.revogar_conexao(conexao["id"])


def test_pedido_vencido_explica():
    with cliente() as c:
        r = c.get(mcp_garimpo.PAGINA_DE_AUTORIZACAO, params={"pedido": "nao-existe"})
        assert r.status_code == 410 and "venceu" in r.text


# ---------- WhatsApp, ids e não usar ----------

@pytest.fixture
def lista_wpp():
    """6 contatos de disparo: 0 e 1 pessoais, 2 business, 3 sem WhatsApp, 4 e 5 não verificados."""
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    situacoes = [("sim", "pessoal"), ("sim", "pessoal"), ("sim", "business"), ("não", ""), ("não verificado", ""), ("não verificado", "")]
    linhas = [
        {**base, "telefone": f"+55119999900{i:02d}", "primeiro_nome": f"Pessoa{i}", "faixa": "A", "chance_dono": 90 - i,
         "whatsapp": w, "tipo_whatsapp": t, "situacao": "ativa", "fonte": "Receita", "cidade": "Cidade Teste", "uf": "SP",
         "solar": "sem registro", "cnpj": f"{i:014d}"}
        for i, (w, t) in enumerate(situacoes)
    ]
    pipeline.ordenar(linhas, "Cidade Teste")
    id_ = "20260101-000000-cidade-wpp-sp"
    pipeline.salvar_parquet(linhas, servidor.LISTAS / f"{id_}.parquet")
    (servidor.LISTAS / f"{id_}.json").write_text(json.dumps({
        "id": id_, "criada_em": "2026-01-01T00:00:00", "cidade": "CIDADE TESTE", "uf": "SP", "atividades": [],
        "parametros": {}, "status": "pronta", "etapa": None, "erro": None, "aviso": None, "resumo": {}, "versao": pipeline.VERSAO,
    }))
    yield id_
    for sufixo in ("parquet", "json"):
        (servidor.LISTAS / f"{id_}.{sufixo}").unlink(missing_ok=True)


def _nomes(r) -> list[str]:
    assert not r.is_error, r.content[0].text
    return sorted(l["primeiro_nome"] for l in r.structured_content["linhas"])


def test_filtro_por_situacao_do_whatsapp(limpo, lista_wpp):  # noqa: F811
    def com(*w):
        return _nomes(_chamar("contatos_da_lista", {"lista_id": lista_wpp, "whatsapp": list(w)}))

    assert com("pessoal") == ["Pessoa0", "Pessoa1"]
    assert com("business") == ["Pessoa2"]
    assert com("com_whatsapp") == ["Pessoa0", "Pessoa1", "Pessoa2"]
    assert com("sem_whatsapp") == ["Pessoa3"]
    assert com("nao_verificado", "business") == ["Pessoa2", "Pessoa4", "Pessoa5"]
    r = _chamar("contatos_da_lista", {"lista_id": lista_wpp, "whatsapp": ["sim"]})
    assert r.is_error and "pessoal" in r.content[0].text


def test_cada_contato_tem_id_estavel_que_nao_e_o_telefone(limpo, lista_wpp):  # noqa: F811
    linhas = _chamar("contatos_da_lista", {"lista_id": lista_wpp}).structured_content["linhas"]
    ids = [l["contato_id"] for l in linhas]
    assert len(set(ids)) == 6 and all(len(i) == 16 and i not in l["telefone"] for i, l in zip(ids, linhas))
    assert ids[0] == servidor.id_do_contato(linhas[0]["telefone"])
    so_dois = _chamar("contatos_da_lista", {"lista_id": lista_wpp, "ids": [ids[3].upper(), ids[5]]})
    assert [l["contato_id"] for l in so_dois.structured_content["linhas"]] == [ids[3], ids[5]]


def test_retirar_so_pessoais(limpo, lista_wpp):  # noqa: F811
    r = _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Cliente X", "quantidade": 10, "whatsapp": ["pessoal"]})
    assert _nomes(r) == ["Pessoa0", "Pessoa1"] and r.structured_content["restantes"] == 0
    assert all(l["contato_id"] for l in r.structured_content["linhas"])


def test_retirar_por_ids_diz_por_que_os_outros_ficaram(limpo, lista_wpp):  # noqa: F811
    ids = {l["primeiro_nome"]: l["contato_id"] for l in _chamar("contatos_da_lista", {"lista_id": lista_wpp}).structured_content["linhas"]}
    um = _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Cliente X", "ids": [ids["Pessoa1"], ids["Pessoa4"]]})
    assert _nomes(um) == ["Pessoa1", "Pessoa4"] and "nao_retirados" in um.structured_content
    assert um.structured_content["nao_retirados"] == []
    dois = _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Cliente Y", "ids": [ids["Pessoa1"], ids["Pessoa2"], "0" * 16],
                                        "whatsapp": ["pessoal", "business"]})
    assert _nomes(dois) == ["Pessoa2"]
    motivos = {m["contato_id"]: m["motivo"] for m in dois.structured_content["nao_retirados"]}
    assert motivos == {ids["Pessoa1"]: "já está na pasta Cliente X", "0" * 16: "não está nesta lista"}
    tres = _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Cliente Y", "ids": [ids["Pessoa3"]], "whatsapp": ["pessoal"]})
    assert tres.structured_content["retirados"] == 0 and "filtros" in tres.structured_content["nao_retirados"][0]["motivo"]
    assert _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Y", "ids": []}).is_error


def test_marcar_nao_usar_tira_de_tudo(limpo, lista_wpp):  # noqa: F811
    ids = {l["primeiro_nome"]: l["contato_id"] for l in _chamar("contatos_da_lista", {"lista_id": lista_wpp}).structured_content["linhas"]}
    _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Cliente X", "ids": [ids["Pessoa0"]]})
    r = _chamar("marcar_nao_usar", {"contato_ids": [ids["Pessoa0"], ids["Pessoa5"], "f" * 16], "motivo": "bloqueou", "lista_id": lista_wpp})
    assert not r.is_error, r.content[0].text
    assert r.structured_content == {"marcados": 2, "novos": 2, "nao_achados": ["f" * 16]}
    assert "Pessoa0" not in _nomes(_chamar("contatos_da_lista", {"lista_id": lista_wpp}))
    assert "Pessoa5" not in _nomes(_chamar("contatos_da_lista", {"lista_id": lista_wpp}))
    assert [p["contatos"] for p in _chamar("listar_pastas").structured_content["result"]] == [0]
    volta = _chamar("retirar_contatos", {"lista_id": lista_wpp, "pasta": "Cliente Z", "ids": [ids["Pessoa0"]]})
    assert volta.structured_content["nao_retirados"][0]["motivo"] == "marcado para não usar"
    sem_lista = _chamar("marcar_nao_usar", {"contato_ids": [ids["Pessoa1"]]})  # fora de pasta e sem a lista: não acha
    assert sem_lista.structured_content["nao_achados"] == [ids["Pessoa1"]]
