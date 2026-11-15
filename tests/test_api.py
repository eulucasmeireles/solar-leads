"""API das automações (/api/v1): chaves, limites e a retirada de contatos."""

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from conftest import SENHA_TESTE
from solar_leads import chaves, pastas, pipeline
from solar_leads.web import servidor

_ips = itertools.count(1)


def cliente() -> TestClient:
    c = TestClient(servidor.app, base_url="https://testserver", follow_redirects=False)
    c.headers["X-Forwarded-For"] = f"198.51.100.{next(_ips)}"
    return c


@pytest.fixture(autouse=True)
def limpo():
    servidor._api_erros.clear()
    servidor._api_chamadas.clear()
    chaves._cache.clear()
    chaves._ultimo_uso_gravado.clear()
    yield


@pytest.fixture
def logado():
    with cliente() as c:
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


@pytest.fixture
def chave(logado) -> str:
    r = logado.post("/api/chaves", json={"nome": "n8n"})
    assert r.status_code == 200
    return r.json()["chave"]


def com_chave(chave: str) -> TestClient:
    c = cliente()
    c.headers["Authorization"] = f"Bearer {chave}"
    return c


# ---------- criação e listagem ----------

def test_sem_sessao_ninguem_cria_lista_ou_revoga_chave():
    with cliente() as c:
        assert c.post("/api/chaves", json={"nome": "x"}).status_code == 401
        assert c.get("/api/chaves").status_code == 401
        assert c.delete("/api/chaves/1").status_code == 401


def test_a_chave_completa_so_aparece_na_criacao(logado):
    criada = logado.post("/api/chaves", json={"nome": "  GHL   producao "}).json()
    assert criada["chave"].startswith("sl_") and len(criada["chave"]) > 40
    assert criada["nome"] == "GHL producao" and criada["comeco"] == criada["chave"][:7]
    listadas = logado.get("/api/chaves").json()
    assert [c["nome"] for c in listadas] == ["GHL producao"]
    assert "chave" not in listadas[0] and "hash" not in listadas[0]
    assert criada["chave"] not in json.dumps(listadas)


def test_o_arquivo_nao_guarda_a_chave_em_claro(chave):
    bruto = pastas.ARQUIVO.read_bytes() + b"".join(p.read_bytes() for p in pastas.ARQUIVO.parent.glob("pastas.sqlite-*"))
    assert chave.encode() not in bruto


@pytest.mark.parametrize("nome", ["", "   ", "x" * 61])
def test_nome_vazio_ou_longo_demais_da_400(logado, nome):
    assert logado.post("/api/chaves", json={"nome": nome}).status_code == 400


def test_limite_de_chaves_ativas(logado, monkeypatch):
    monkeypatch.setattr(chaves, "MAX_ATIVAS", 2)
    assert logado.post("/api/chaves", json={"nome": "a"}).status_code == 200
    segunda = logado.post("/api/chaves", json={"nome": "b"}).json()
    assert logado.post("/api/chaves", json={"nome": "c"}).status_code == 400
    assert logado.delete(f"/api/chaves/{segunda['id']}").status_code == 200
    assert logado.post("/api/chaves", json={"nome": "c"}).status_code == 200  # revogada não conta


# ---------- o que a chave abre e o que não abre ----------

def test_sem_chave_a_api_responde_401_e_diz_como_enviar():
    with cliente() as c:
        r = c.get("/api/v1/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "Authorization: Bearer" in r.json()["detail"]


def test_com_a_chave_a_api_responde_e_diz_o_nome_dela(chave):
    with com_chave(chave) as c:
        r = c.get("/api/v1/")
    assert r.status_code == 200
    dados = r.json()
    assert dados["ok"] is True and dados["chave"] == "n8n" and dados["versao"] == "v1"
    assert {"metodo": "POST", "caminho": "/api/v1/listas/{id_}/retirar"} == {k: v for k, v in next(
        x for x in dados["rotas"] if x["caminho"].endswith("/retirar")).items() if k != "descricao"}
    assert r.headers["cache-control"] == "no-store"


def test_a_chave_tambem_vale_no_cabecalho_x_api_key(chave):
    with cliente() as c:
        assert c.get("/api/v1/", headers={"X-API-Key": chave}).status_code == 200


def test_chave_na_url_nao_vale(chave):
    with cliente() as c:
        assert c.get("/api/v1/", params={"chave": chave, "api_key": chave, "key": chave}).status_code == 401


@pytest.mark.parametrize(
    "metodo,caminho",
    [("GET", "/api/pastas"), ("GET", "/api/listas"), ("GET", "/api/chaves"), ("POST", "/api/chaves"),
     ("GET", "/api/acessos"), ("POST", "/api/logout"), ("GET", "/api/whatsapp"), ("GET", "/static/app.js")],
)
def test_a_chave_nao_abre_nada_fora_da_api_v1(chave, metodo, caminho):
    with com_chave(chave) as c:
        assert c.request(metodo, caminho, json={"nome": "x"} if metodo == "POST" else None).status_code in (401, 303)


def test_a_chave_nao_abre_a_pagina(chave):
    with com_chave(chave) as c:
        assert c.get("/").status_code == 303


def test_nada_na_api_v1_apaga_dados():
    metodos = {m for m, *_ in servidor._ROTAS_V1}
    assert metodos == {"GET", "POST"}
    rotas = [r for r in servidor.app.routes if getattr(r, "path", "").startswith("/api/v1")]
    assert rotas and all(r.methods <= {"GET", "POST"} for r in rotas)


def test_a_sessao_da_pagina_tambem_chama_a_api(logado):
    r = logado.get("/api/v1/")
    assert r.status_code == 200 and r.json()["chave"] is None


def test_chave_revogada_para_de_valer_na_hora(logado, chave):
    with com_chave(chave) as c:
        assert c.get("/api/v1/pastas").status_code == 200
        id_ = logado.get("/api/chaves").json()[0]["id"]
        assert logado.delete(f"/api/chaves/{id_}").status_code == 200
        assert c.get("/api/v1/pastas").status_code == 401
    assert logado.get("/api/chaves").json()[0]["revogada_em"] != ""
    assert logado.delete(f"/api/chaves/{id_}").status_code == 404


def test_o_ultimo_uso_fica_registrado(logado, chave):
    assert logado.get("/api/chaves").json()[0]["ultimo_uso"] == ""
    with com_chave(chave) as c:
        c.get("/api/v1/")
    assert logado.get("/api/chaves").json()[0]["ultimo_uso"] != ""


def test_criar_e_revogar_chave_aparecem_nos_acessos(logado):
    id_ = logado.post("/api/chaves", json={"nome": "x"}).json()["id"]
    logado.delete(f"/api/chaves/{id_}")
    tipos = [a["tipo"] for a in logado.get("/api/acessos").json()]
    assert "chave criada" in tipos and "chave revogada" in tipos


# ---------- limites ----------

def test_muitas_chaves_erradas_bloqueiam_o_endereco_mas_nao_os_outros(chave, monkeypatch):
    monkeypatch.setattr(servidor, "API_MAX_ERROS", 3)
    with cliente() as c:
        for _ in range(3):
            assert c.get("/api/v1/", headers={"Authorization": "Bearer sl_errada"}).status_code == 401
        r = c.get("/api/v1/", headers={"Authorization": f"Bearer {chave}"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    with com_chave(chave) as outro:
        assert outro.get("/api/v1/").status_code == 200


def test_limite_de_chamadas_por_minuto(chave, monkeypatch):
    monkeypatch.setattr(servidor, "API_POR_MINUTO", 3)
    with com_chave(chave) as c:
        assert [c.get("/api/v1/").status_code for _ in range(4)] == [200, 200, 200, 429]
        assert 0 < int(c.get("/api/v1/").headers["retry-after"]) <= 61


# ---------- pastas e retirada ----------

def test_cria_e_lista_pastas_pela_api(chave):
    with com_chave(chave) as c:
        id_ = c.post("/api/v1/pastas", json={"nome": "Automacao"}).json()["id"]
        assert [p["nome"] for p in c.get("/api/v1/pastas").json()] == ["Automacao"]
        assert c.get(f"/api/v1/pastas/{id_}/contatos").json()["total"] == 0


@pytest.fixture
def lista():
    """Uma lista pronta com 5 contatos fortes e 1 fraco, gravada na pasta temporária."""
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    linhas = [
        {**base, "telefone": f"+55119999900{i:02d}", "primeiro_nome": f"Pessoa{i}", "faixa": "A" if i < 5 else "C",
         "chance_dono": 90 - i, "aptidao_solar": 5, "potencial_casa": 5, "potencial_empresa": 5, "whatsapp": "não verificado",
         "situacao": "ativa", "fonte": "Receita", "cidade": "Cidade Teste", "uf": "SP", "solar": "sem registro", "cnpj": f"{i:014d}"}
        for i in range(6)
    ]
    pipeline.ordenar(linhas, "Cidade Teste")
    id_ = "20260101-000000-cidade-teste-sp"
    pipeline.salvar_parquet(linhas, servidor.LISTAS / f"{id_}.parquet")
    (servidor.LISTAS / f"{id_}.json").write_text(json.dumps({
        "id": id_, "criada_em": "2026-01-01T00:00:00", "cidade": "CIDADE TESTE", "uf": "SP", "atividades": [],
        "parametros": {}, "status": "pronta", "etapa": None, "erro": None, "aviso": None, "resumo": {},
    }))
    yield id_
    for sufixo in ("parquet", "json"):
        (servidor.LISTAS / f"{id_}.{sufixo}").unlink(missing_ok=True)


def test_contatos_da_lista_pela_api(chave, lista):
    with com_chave(chave) as c:
        r = c.get(f"/api/v1/listas/{lista}/contatos", params={"por_pagina": 2})
    assert r.status_code == 200
    dados = r.json()
    assert dados["total"] == 5 and len(dados["linhas"]) == 2  # o contato fraco não entra no disparo
    assert dados["linhas"][0]["telefone"] == "+5511999990000"


def test_retirar_entrega_os_proximos_e_nunca_repete(chave, lista):
    with com_chave(chave) as c:
        um = c.post(f"/api/v1/listas/{lista}/retirar", json={"pasta": "Semana 1", "quantidade": 2}).json()
        dois = c.post(f"/api/v1/listas/{lista}/retirar", json={"pasta": "semana 1", "quantidade": 2}).json()
        tres = c.post(f"/api/v1/listas/{lista}/retirar", json={"pasta": "Semana 2", "quantidade": 10}).json()
        quatro = c.post(f"/api/v1/listas/{lista}/retirar", json={"pasta": "Semana 2", "quantidade": 10}).json()
        pastas_agora = {p["nome"]: p["contatos"] for p in c.get("/api/v1/pastas").json()}
    tels = [[l["telefone"] for l in r["linhas"]] for r in (um, dois, tres, quatro)]
    assert [len(t) for t in tels] == [2, 2, 1, 0]
    assert len({t for grupo in tels for t in grupo}) == 5  # ninguém saiu duas vezes
    assert (um["retirados"], um["restantes"], um["pasta"]["criada_agora"]) == (2, 3, True)
    assert (dois["restantes"], dois["pasta"]["criada_agora"], dois["pasta"]["id"]) == (1, False, um["pasta"]["id"])
    assert (tres["restantes"], quatro["retirados"]) == (0, 0)
    assert pastas_agora == {"Semana 1": 4, "Semana 2": 1}
    assert um["linhas"][0]["publico"] == "dono"


def test_retirar_respeita_os_filtros(chave, lista):
    with com_chave(chave) as c:
        r = c.post(f"/api/v1/listas/{lista}/retirar", params={"busca": "Pessoa3"}, json={"pasta": "Filtro"}).json()
    assert [l["primeiro_nome"] for l in r["linhas"]] == ["Pessoa3"] and r["restantes"] == 0


def test_retirar_de_lista_inexistente_da_404_e_sem_pasta_da_422(chave, lista):
    with com_chave(chave) as c:
        assert c.post("/api/v1/listas/nao-existe/retirar", json={"pasta": "X"}).status_code == 404
        assert c.post(f"/api/v1/listas/{lista}/retirar", json={}).status_code == 422
        assert c.post(f"/api/v1/listas/{lista}/retirar", json={"pasta": "   "}).status_code == 400
