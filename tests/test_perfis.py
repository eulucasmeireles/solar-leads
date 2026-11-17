"""Perfis de busca, nota de encaixe e migração das listas gravadas antes dos perfis."""

import json

import duckdb
import pytest

from test_servidor import cliente  # noqa: F401 - mesmo cliente com sessão dos outros testes
from conftest import SENHA_TESTE
from solar_leads import perfis, pipeline, qualificacao
from solar_leads.web import servidor


@pytest.fixture
def logado():
    with cliente() as c:
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


RECEITA = {
    "oferta": "Sistema de agendamento",
    "cliente_ideal": "Clínicas de estética e salões com mais de 2 anos",
    "atividades": [
        {"codigo": "9602-5/02", "descricao": "Atividades de estética e outros serviços de cuidados com a beleza", "peso": 3},
        {"codigo": "9602501", "descricao": "Cabeleireiros, manicure e pedicure", "peso": 2},
        {"codigo": "123", "descricao": "código inválido", "peso": 3},
    ],
    "filtros": {"so_ativas": True, "idade_min": 2, "portes": ["3", "05", "99"]},
}


# ---------- nota de encaixe ----------

def test_encaixe_soma_atividade_idade_porte_capital_e_unidades():
    nota, motivos = qualificacao.encaixe(
        {"cnae": "9602502", "idade_empresa": 7, "porte": "Médio ou grande", "capital_social": 150000, "unidades_ativas": 3},
        {"9602502": 3},
    )
    assert nota == 10
    assert "atividade principal do perfil" in motivos and "mais de uma unidade ativa" in motivos


def test_encaixe_sem_atividade_do_perfil_conta_so_o_resto():
    nota, motivos = qualificacao.encaixe({"cnae": "4711302", "idade_empresa": 3, "porte": "Microempresa"}, {"9602502": 3})
    assert nota == 1 and motivos == ["mais de 2 anos de empresa"]


def test_encaixe_de_lista_antiga_acha_a_atividade_pela_descricao():
    pesos = perfis.pesos({"atividades": [{"codigo": "9602502", "descricao": "Estética", "peso": 2}]})
    assert qualificacao.encaixe({"cnae": "", "atividade": "Estética"}, pesos)[0] == 3


def test_encaixe_aceita_campos_vazios():
    assert qualificacao.encaixe({"idade_empresa": "", "capital_social": "", "porte": ""}, {}) == (0, [])


# ---------- receita e cadastro ----------

def test_receita_valida_limpa_codigos_portes_e_pesos():
    r = perfis.receita_valida(RECEITA)
    assert [a["codigo"] for a in r["atividades"]] == ["9602502", "9602501"]  # o código inválido sai
    assert r["atividades"][0]["peso"] == 3
    assert r["filtros"] == {"so_ativas": True, "incluir_mei": False, "so_locais": True, "idade_min": 2, "portes": ["03", "05"]}


def test_cria_lista_muda_e_exclui_perfil():
    criado = perfis.criar("Agenda para estética", RECEITA)
    assert criado["id"].isdigit() and not criado["solar"]
    assert [p["id"] for p in perfis.listar()][:2] == ["solar", "geral"]
    with pytest.raises(perfis.PerfilInvalido, match="Já existe"):
        perfis.criar("agenda PARA estética", RECEITA)
    mudado = perfis.atualizar(criado["id"], "Agenda", {**RECEITA, "oferta": "Agenda online"})
    assert mudado["nome"] == "Agenda" and mudado["receita"]["oferta"] == "Agenda online"
    perfis.excluir(criado["id"])
    assert perfis.um(criado["id"]) is None


@pytest.mark.parametrize("pronto", ["solar", "geral"])
def test_perfis_prontos_nao_mudam(pronto):
    with pytest.raises(perfis.PerfilInvalido):
        perfis.atualizar(pronto, "Outro", {})
    with pytest.raises(perfis.PerfilInvalido):
        perfis.excluir(pronto)


def test_lista_antiga_vale_como_solar_ou_sem_perfil():
    assert perfis.do_meta({})["solar"] is True
    assert perfis.do_meta({"publico": "empresa"})["id"] == "geral"


def test_sem_atividades_no_perfil_as_da_lista_valem_peso_dois():
    assert perfis.pesos({"atividades": []}, [{"codigo": "4711302", "descricao": "Supermercados"}]) == {
        "4711302": 2, "supermercados": 2,
    }


def test_criterio_da_ia_sai_da_oferta_quando_nao_foi_escrito():
    assert perfis.criterio({"oferta": "Agenda online", "cliente_ideal": "Salões."}) == "Vendo: Agenda online. Cliente ideal: Salões."
    assert "energia solar" in perfis.criterio(perfis.retrato(perfis.PRONTOS["solar"]))


# ---------- montagem e ordem ----------

def test_filtros_do_perfil_tiram_empresa_nova_e_porte_fora():
    filtros = {"idade_min": 2, "portes": ["03"]}
    assert pipeline.passa_nos_filtros({"idade_empresa": 5, "porte": "Pequeno porte"}, filtros)
    assert not pipeline.passa_nos_filtros({"idade_empresa": 1, "porte": "Pequeno porte"}, filtros)
    assert not pipeline.passa_nos_filtros({"idade_empresa": 5, "porte": "Microempresa"}, filtros)
    assert not pipeline.passa_nos_filtros({"idade_empresa": "", "porte": "Pequeno porte"}, filtros)
    assert pipeline.passa_nos_filtros({"idade_empresa": ""}, {})


def test_no_publico_dono_o_encaixe_e_a_nota_solar():
    linhas = [{"aptidao_solar": 7, "motivos_solar": "atividade de consumo diurno"}]
    pipeline.pontuar_encaixe(linhas, "dono")
    assert linhas[0]["encaixe"] == 7 and linhas[0]["motivos_encaixe"] == "atividade de consumo diurno"


def linha(cnpj, encaixe, chance):
    return {
        "cnpj": cnpj, "faixa": "A", "whatsapp": "não verificado", "aptidao_solar": 0, "chance_dono": chance, "encaixe": encaixe,
        "solar": "não consultado", "situacao": "ativa", "fonte": "Receita", "cidade": "Montes Claros", "dono_chave": "",
    }


def test_no_publico_empresa_o_encaixe_desempata_antes_da_chance():
    linhas = [linha("1", 2, 95), linha("2", 8, 60)]
    pipeline.ordenar(linhas, "Montes Claros", "empresa")
    assert [l["cnpj"] for l in linhas] == ["2", "1"]


def test_notas_da_ia_entram_por_cnpj():
    linhas = [{"cnpj": "1"}, {"cnpj": "2"}]
    pipeline.aplicar_notas_da_ia(linhas, {"1": (9, "cliente ideal")})
    assert linhas == [{"cnpj": "1", "nota_ia": 9, "motivo_ia": "cliente ideal"}, {"cnpj": "2", "nota_ia": None, "motivo_ia": ""}]


# ---------- migração ----------

def _parquet_antigo(arquivo, linhas):
    """Grava como as listas de antes dos perfis: sem encaixe, nota da IA e código da atividade."""
    novas = {"encaixe", "motivos_encaixe", "nota_ia", "motivo_ia", "cnae"}
    colunas = [c for c in pipeline.CHAVES + pipeline.INTERNAS if c not in novas]
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({', '.join(f'{c} VARCHAR' for c in colunas)})")
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(colunas))})", [[str(l.get(c, "")) for c in colunas] for l in linhas])
    con.execute(f"COPY (SELECT * REPLACE (CAST(ordem AS INTEGER) AS ordem) FROM t) TO '{arquivo}' (FORMAT parquet)")


def test_migrar_lista_antiga_guarda_copia_e_nao_muda_a_ordem(tmp_path):
    arquivo = tmp_path / "lista.parquet"
    _parquet_antigo(arquivo, [
        {"cnpj": "2", "ordem": 0, "aptidao_solar": 3, "motivos_solar": "x", "faixa": "A"},
        {"cnpj": "1", "ordem": 1, "aptidao_solar": 9, "motivos_solar": "y", "faixa": "A"},
    ])
    assert pipeline.migrar(arquivo, "dono") is True
    assert (tmp_path / "antes-garimpo" / "lista.parquet").exists()
    linhas = pipeline.ler_parquet(arquivo)
    assert [(l["cnpj"], l["encaixe"], l["nota_ia"]) for l in linhas] == [("2", 3, None), ("1", 9, None)]
    assert pipeline.migrar(arquivo, "dono") is False


# ---------- pela tela ----------

def _lista_antiga(id_):
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    linhas = [
        {**base, "telefone": f"+55389999900{i:02d}", "primeiro_nome": f"Pessoa{i}", "faixa": "A", "chance_dono": 90 - i,
         "aptidao_solar": i, "whatsapp": "não verificado", "situacao": "ativa", "fonte": "Receita", "cidade": "Montes Claros",
         "uf": "MG", "solar": "sem registro", "cnpj": f"{i:014d}", "ordem": i}
        for i in range(3)
    ]
    _parquet_antigo(servidor.LISTAS / f"{id_}.parquet", linhas)
    (servidor.LISTAS / f"{id_}.json").write_text(json.dumps({
        "id": id_, "criada_em": "2026-01-01T00:00:00", "cidade": "MONTES CLAROS", "uf": "MG", "atividades": [],
        "parametros": {}, "status": "pronta", "etapa": None, "erro": None, "aviso": None, "resumo": {},
    }))


def test_lista_antiga_abre_migrada_e_ordena_pela_nota_da_ia(logado):
    id_ = "20260101-000000-montes-claros-mg"
    _lista_antiga(id_)
    try:
        r = logado.get(f"/api/listas/{id_}/contatos", params={"ordem": "ia", "ia_min": 0})
        assert r.status_code == 200
        assert [l["encaixe"] for l in r.json()["linhas"]] == [0, 1, 2]
        meta = logado.get(f"/api/listas/{id_}").json()
        assert meta["versao"] == pipeline.VERSAO and meta["perfil"]["id"] == "solar"
        assert logado.get(f"/api/listas/{id_}/contatos", params={"encaixe_min": 2}).json()["total"] == 1
    finally:
        for sufixo in ("parquet", "json"):
            (servidor.LISTAS / f"{id_}.{sufixo}").unlink(missing_ok=True)


def test_perfis_pela_tela(logado):
    r = logado.post("/api/perfis", json={"nome": "Academias", "receita": RECEITA})
    assert r.status_code == 200
    novo = r.json()["id"]
    assert [p["nome"] for p in logado.get("/api/perfis").json()] == ["Energia solar", "Sem perfil", "Academias"]
    assert logado.put(f"/api/perfis/{novo}", json={"nome": "", "receita": RECEITA}).status_code == 400
    assert logado.put("/api/perfis/solar", json={"nome": "X", "receita": {}}).status_code == 400
    assert logado.delete(f"/api/perfis/{novo}").status_code == 200
    assert logado.get(f"/api/perfis/{novo}").status_code == 404


def test_lista_com_perfil_salvo_guarda_o_retrato_do_perfil(logado, monkeypatch):
    postos = []
    monkeypatch.setattr(servidor._fila, "put", postos.append)
    perfil = perfis.criar("Clínicas", RECEITA)
    con = duckdb.connect()
    monkeypatch.setattr(servidor, "_municipios", lambda: _municipios_falsos(con))
    try:
        r = logado.post("/api/listas", json={"uf": "MG", "cidade": "Montes Claros", "perfil": perfil["id"], "portes": ["03", "7"]})
        assert r.status_code == 200, r.text
        meta = postos[0][0]
        assert meta["publico"] == "empresa" and meta["perfil"]["nome"] == "Clínicas"
        # o que o pedido não disse veio do perfil
        assert [a["codigo"] for a in meta["atividades"]] == ["9602501", "9602502"]
        assert meta["parametros"]["idade_min"] == 2 and meta["parametros"]["so_ativas"] is True
        assert meta["parametros"]["portes"] == ["03"]
        assert logado.post("/api/listas", json={"uf": "MG", "cidade": "Montes Claros", "perfil": "999"}).status_code == 404
    finally:
        (servidor.LISTAS / f"{r.json()['id']}.json").unlink(missing_ok=True)
        perfis.excluir(perfil["id"])


_MUN = None


def _municipios_falsos(con):
    global _MUN
    if _MUN is None:
        import tempfile
        from pathlib import Path

        _MUN = Path(tempfile.mkdtemp()) / "municipios.parquet"
        con.execute(
            f"COPY (SELECT 'MG' AS uf, 'MONTES CLAROS' AS nome, '0001' AS codigo, 'MONTES CLAROS' AS exibicao, 1000 AS empresas) "
            f"TO '{_MUN}' (FORMAT parquet)"
        )
    return _MUN
