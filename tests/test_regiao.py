"""Lista por atividade num estado inteiro ou no Brasil todo (perfil sem cidade).

A base real não entra aqui: usa a mesma base em memória dos testes da lista por nome.
"""

import pytest

from solar_leads import enriquecer, perfis
from solar_leads.enriquecer import _da_base, carregar_regiao, data_limite
from solar_leads.web import servidor
from test_perfis import RECEITA
from test_por_nome import base, cnpjs, logado  # noqa: F401 - fixtures


def test_brasil_todo_traz_a_atividade_em_todos_os_estados(base):  # noqa: F811
    n = carregar_regiao(base, ["4321500"])
    assert cnpjs(base) == {"00000001", "00000002", "00000003", "00000004", "00000005", "00000006"}  # sem o MEI
    assert n == 6
    achadas = _da_base(base, ["4321500"], incluir_mei=False, so_locais=True, extras=("uf", "cidade"))
    assert {e["uf"] for e in achadas} == {"MG", "RJ"}
    assert all(e["cidade"] for e in achadas)


def test_estado_e_filtros_entram_na_carga(base):  # noqa: F811
    carregar_regiao(base, ["4321500"], "RJ")
    assert cnpjs(base) == {"00000004"}
    carregar_regiao(base, ["4321500"], "mg", so_ativas=True, incluir_mei=True)
    assert cnpjs(base) == {"00000001", "00000002", "00000003", "00000006", "00000007"}
    carregar_regiao(base, ["4321500"], idade_min=200)
    assert cnpjs(base) == set()
    carregar_regiao(base, ["4321500"], portes=["05"])
    assert cnpjs(base) == set()


def test_atividade_de_outro_ramo_nao_entra(base):  # noqa: F811
    assert carregar_regiao(base, ["9602501"]) == 0


def test_sem_atividade_recusa(base):  # noqa: F811
    with pytest.raises(ValueError, match="atividade"):
        carregar_regiao(base, ["", "x"])


def test_grande_demais_recusa_e_sugere_estado(base, monkeypatch):  # noqa: F811
    monkeypatch.setattr(enriquecer, "MAX_EMPRESAS_POR_NOME", 2)
    with pytest.raises(ValueError, match="Escolha um estado"):
        carregar_regiao(base, ["4321500"])


def test_data_limite_aguenta_29_de_fevereiro(monkeypatch):
    import datetime as dt

    class Hoje(dt.date):
        @classmethod
        def today(cls):
            return cls(2028, 2, 29)

    monkeypatch.setattr(enriquecer, "date", Hoje)
    assert data_limite(2) == "20260228"
    assert data_limite(4) == "20240229"


@pytest.mark.parametrize(
    "meta,esperado",
    [({"cidade": "Brasil", "uf": "", "regiao": True}, "Brasil todo"), ({"cidade": "MG", "uf": "MG", "regiao": True}, "MG (estado todo)")],
)
def test_rotulo_da_lista_de_regiao(meta, esperado):
    assert servidor._rotulo(meta) == esperado


def test_lista_sem_cidade_e_sem_atividade_da_400(logado):  # noqa: F811
    r = logado.post("/api/listas", json={"uf": "", "perfil": "geral"})
    assert r.status_code == 400
    assert "atividades" in r.json()["detail"]


def test_lista_de_regiao_grande_demais_da_400_na_hora(logado, monkeypatch):  # noqa: F811
    monkeypatch.setattr(servidor.busca_ia, "contar_empresas", lambda con, **k: {"total": 999_999})
    monkeypatch.setattr(servidor.cnpj_db, "conectar", lambda **k: type("C", (), {"close": lambda self: None})())
    r = logado.post("/api/listas", json={"uf": "", "perfil": "geral", "cnaes": ["9602501"]})
    assert r.status_code == 400
    assert "999.999" in r.json()["detail"] and "estado" in r.json()["detail"]


def test_lista_de_regiao_vai_para_a_fila(logado, monkeypatch):  # noqa: F811
    monkeypatch.setattr(servidor, "_cabe_na_lista", lambda *a: None)
    monkeypatch.setattr(servidor, "ufs", lambda: ["MG", "RJ"])
    postos = []
    monkeypatch.setattr(servidor._fila, "put", postos.append)
    perfil = perfis.criar("Agenda no estado", RECEITA)
    r = logado.post("/api/listas", json={"uf": "mg", "perfil": perfil["id"]})
    perfis.excluir(perfil["id"])
    assert r.status_code == 200, r.text
    meta, codigo = postos[0]
    assert meta["regiao"] is True and meta["uf"] == "MG" and meta["palavras"] == [] and codigo == ""
    assert servidor._amplo(meta) and servidor._codigo_do_municipio(meta) == ""
    assert [a["codigo"] for a in meta["atividades"]] == ["9602501", "9602502"]
    assert meta["parametros"]["idade_min"] == 2 and meta["parametros"]["portes"] == ["03", "05"]
    assert logado.delete(f"/api/listas/{meta['id']}").status_code in (200, 204)
