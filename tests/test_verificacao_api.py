"""Verificação de WhatsApp pela CheckLeaked (sem falar com serviço nenhum): motor, estimativa e rota."""

import json
import threading
import time

import httpx
import pytest

from conftest import SENHA_TESTE
from solar_leads import cnpj_db, config, consulta, pipeline, whatsapp
from solar_leads.web import servidor
from test_servidor import cliente


@pytest.fixture(autouse=True)
def servico_falso(monkeypatch):
    """Cadastro zerado e uma CheckLeaked de mentira: número terminado em 0 não tem conta, em 5 é comercial, em 7 não responde."""
    config.BANCO.unlink(missing_ok=True)
    estado = {"consultados": [], "saldo": 5000, "recusar": False}

    def atende(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path == "/api-key-stats":
            return httpx.Response(200, json={"requestRemaining": estado["saldo"], "requestLimit": 5000, "expiresAt": "2027-01-14T10:00:00Z"})
        if estado["recusar"]:
            return httpx.Response(401, json={"error": "Invalid API key"})
        numero = pedido.url.path.rsplit("/", 1)[-1]
        estado["consultados"].append(numero[2:])
        estado["saldo"] -= 1
        if numero.endswith("7"):
            return httpx.Response(500, json={"error": "falhou"})
        if numero.endswith("0"):
            return httpx.Response(200, json={"exists": False, "error": "Whatsapp number doesn't exist"})
        return httpx.Response(200, json={"isWAContact": True, "exists": True, "isBusiness": numero.endswith("5"), "about": "recado"})

    monkeypatch.setenv("CHECKLEAKED_KEY", "wapi_" + "x" * 40)
    monkeypatch.setattr(consulta, "CHECKLEAKED_INTERVALO", 0.0)
    monkeypatch.setattr(
        consulta, "_cliente_checkleaked",
        lambda chave: httpx.Client(base_url=consulta.CHECKLEAKED, headers={"x-rapidapi-key": chave}, transport=httpx.MockTransport(atende)),
    )
    yield estado
    config.BANCO.unlink(missing_ok=True)


def numeros(n: int, inicio: int = 1) -> list[str]:
    return [f"389{i:08d}" for i in range(inicio, inicio + n)]


def cadastro(lista: list[str]) -> dict:
    con = cnpj_db.conectar()
    try:
        return whatsapp.do_cache(con, lista)
    finally:
        con.close()


# ---------- motor ----------

def test_verifica_conta_tipos_e_grava_no_cadastro(servico_falso):
    andamento = []
    r = consulta.verificar(numeros(20), avisar=lambda texto, feitos, total: andamento.append((feitos, total)))
    assert r["motivo"] == "concluída" and r["consultas"] == 20
    assert (r["com_whatsapp"], r["pessoais"], r["comerciais"], r["sem_whatsapp"], r["sem_resposta"]) == (16, 14, 2, 2, 2)
    assert andamento[0] == (0, 20) and andamento[-1] == (20, 20)
    gravados = cadastro(numeros(20))
    assert gravados["38900000005"] == {"tem": True, "business": True, "nome_business": ""}
    assert gravados["38900000010"]["tem"] is False
    assert "38900000007" not in gravados  # sem resposta não vira "sem WhatsApp"
    assert r["saldo"]["restam"] == 4980


def test_quem_ja_esta_no_cadastro_nao_gasta_saldo(servico_falso):
    consulta.verificar(numeros(10))
    servico_falso["consultados"].clear()
    r = consulta.verificar(numeros(12))
    assert sorted(servico_falso["consultados"]) == ["38900000007", "38900000011", "38900000012"]  # o 7 não tinha respondido
    assert r["do_cadastro"] == 9 and r["consultas"] == 3


def test_saldo_curto_consulta_so_o_que_cabe(servico_falso):
    servico_falso["saldo"] = 5
    r = consulta.verificar(numeros(20))
    assert r["consultas"] == 5 and r["motivo"] == "o saldo da CheckLeaked acabou no meio"
    servico_falso["saldo"] = 0
    assert consulta.verificar(numeros(5, 100))["motivo"] == "o saldo da CheckLeaked acabou"


def test_parar_interrompe_entre_rodadas(servico_falso, monkeypatch):
    monkeypatch.setattr(consulta, "GRUPO", 5)
    parar = threading.Event()
    r = consulta.verificar(numeros(20), parar=parar, avisar=lambda texto, feitos, total: parar.set() if feitos >= 5 else None)
    assert r["motivo"] == "parado a pedido" and r["consultas"] == 5


def test_chave_recusada_para_com_motivo(servico_falso):
    servico_falso["recusar"] = True
    r = consulta.verificar(numeros(3))
    assert r["motivo"].startswith("serviço indisponível") and r["consultas"] == 0


def test_sem_chave_nao_consulta(servico_falso, monkeypatch):
    monkeypatch.setenv("CHECKLEAKED_KEY", "")
    assert consulta.verificar(numeros(3))["motivo"] == "falta a chave da CheckLeaked"
    assert servico_falso["consultados"] == []


# ---------- pela tela ----------

@pytest.fixture
def lista():
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    linhas = [
        {**base, "telefone": f"+55{n}", "primeiro_nome": f"Pessoa{i}", "faixa": "A", "chance_dono": 90, "whatsapp": "não verificado",
         "situacao": "ativa", "fonte": "Receita", "cidade": "Montes Claros", "uf": "MG", "solar": "sem registro", "cnpj": f"{i:014d}"}
        for i, n in enumerate(numeros(8))
    ]
    pipeline.ordenar(linhas, "Montes Claros")
    id_ = "20260101-000000-montes-claros-mg"
    pipeline.salvar_parquet(linhas, servidor.LISTAS / f"{id_}.parquet")
    (servidor.LISTAS / f"{id_}.json").write_text(json.dumps({
        "id": id_, "criada_em": "2026-01-01T00:00:00", "cidade": "MONTES CLAROS", "uf": "MG", "atividades": [], "codigo": "0001",
        "parametros": {"so_ativas": False, "incluir_mei": False, "so_locais": True, "validar_whatsapp": False},
        "status": "pronta", "etapa": None, "erro": None, "aviso": None, "resumo": {}, "versao": pipeline.VERSAO,
    }))
    yield id_
    for sufixo in ("parquet", "json"):
        (servidor.LISTAS / f"{id_}.{sufixo}").unlink(missing_ok=True)


@pytest.fixture
def logado():
    with cliente() as c:
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


def test_estimativa_diz_quantos_gastam_saldo(logado, lista):
    consulta.verificar(numeros(3))
    r = logado.get(f"/api/listas/{lista}/whatsapp", params={"quantidade": 5}).json()
    assert (r["numeros"], r["ja_verificados"], r["consultas"]) == (5, 3, 2)
    assert r["servico"]["configurado"] is True and r["servico"]["saldo"]["restam"] == 4997


def test_verificacao_pela_tela_consulta_e_pede_para_refazer_a_lista(logado, lista, monkeypatch):
    refeitas = []
    monkeypatch.setattr(servidor, "_refazer", lambda id_, motivo: refeitas.append(id_))
    servidor._cache.pop("servico-whatsapp", None)
    r = logado.post("/api/whatsapp/verificacao", json={"lista_id": lista, "quantidade": 6})
    assert r.status_code == 200 and r.json()["numeros"] == 6
    for _ in range(100):
        if not servidor._verificando():
            break
        time.sleep(0.05)
    estado = logado.get("/api/whatsapp").json()["verificacao"]
    assert estado["rodando"] is False and estado["resumo"]["consultas"] == 6
    assert "6 com WhatsApp (5 pessoais, 1 comerciais)" in estado["mensagem"]
    assert refeitas == [lista]


def test_verificacao_sem_chave_recusa(logado, lista, monkeypatch):
    monkeypatch.setenv("CHECKLEAKED_KEY", "")
    assert logado.post("/api/whatsapp/verificacao", json={"lista_id": lista}).status_code == 409
