"""IA: custo e teto, busca por descrição e avaliação dos contatos, com um cliente de mentira.

Nenhum teste fala com a Anthropic nem gasta crédito: o cliente da API é trocado por um roteiro.
"""

import json
import time
from contextlib import closing
from types import SimpleNamespace

import duckdb
import pytest

from conftest import SENHA_TESTE
from solar_leads import avaliacao_ia, busca_ia, cnpj_db, config, ia, pastas, pipeline
from solar_leads.web import servidor
from test_servidor import cliente


def uso(entrada=1000, saida=200):
    return SimpleNamespace(input_tokens=entrada, output_tokens=saida, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def bloco(**kw):
    return SimpleNamespace(**kw)


def resposta(conteudo, stop="end_turn", modelo="claude-opus-5-5"):
    return SimpleNamespace(content=conteudo, stop_reason=stop, usage=uso(), model=modelo)


class ClienteFalso:
    """Responde, em ordem, com as funções do roteiro (cada uma recebe os parâmetros do pedido)."""

    def __init__(self, roteiro):
        self.roteiro, self.pedidos = list(roteiro), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._criar))
        self.messages = SimpleNamespace(create=self._criar)

    def _criar(self, **kw):
        self.pedidos.append(kw)
        return self.roteiro.pop(0)(kw)


@pytest.fixture(autouse=True)
def uso_zerado(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste-" + "x" * 30)
    monkeypatch.setenv("GARIMPO_IA_TETO_USD", "20")
    with closing(pastas._con()) as con, con:
        con.execute("DELETE FROM ia_uso")
        con.execute("DELETE FROM ia_avaliacoes")
    yield


# ---------- custo e teto ----------

def test_custo_por_modelo_cache_e_lote():
    assert ia.custo("claude-opus-5-5", 1_000_000, 100_000) == pytest.approx(6.0)
    assert ia.custo("claude-haiku-5-5", 1_000_000, 1_000_000, lote=True) == pytest.approx(0.30)
    assert ia.custo("claude-opus-5-5", 0, 0, cache_lido=1_000_000, cache_gravado=1_000_000) == pytest.approx(0.20 + 5.0)


def test_gasto_do_mes_e_teto():
    ia.registrar("busca", "claude-opus-5-5", uso(1_000_000, 0), "x")
    assert ia.gasto_do_mes() == pytest.approx(4.0)
    ia.conferir_teto(15.0)
    with pytest.raises(ia.IaIndisponivel, match="teto"):
        ia.conferir_teto(16.5)
    assert ia.uso_do_mes()["por_funcao"] == [{"funcao": "busca", "chamadas": 1, "gasto_usd": 4.0}]


def test_sem_chave_avisa_o_comando(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(ia.IaIndisponivel, match="leads segredo ANTHROPIC_API_KEY"):
        ia.cliente()


# ---------- busca por descrição ----------

@pytest.fixture
def base():
    """Base mínima: catálogo de atividades, municípios e estab_cidade com 4 empresas em Montes Claros."""
    config.CNPJ_PARQUET.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * FROM (VALUES ('9602502', 'Atividades de estética e outros serviços de cuidados com a beleza'), "
        f"('9602501', 'Cabeleireiros, manicure e pedicure'), ('4711302', 'Supermercados')) t(codigo, descricao)) "
        f"TO '{config.CNPJ_PARQUET}/cnaes_0.parquet' (FORMAT parquet)"
    )
    con.execute(
        f"COPY (SELECT 'MG' uf, '4865' codigo, 'MONTES CLAROS' nome, 4 empresas, 'MONTES CLAROS' exibicao) TO '{busca_ia.MUNICIPIOS}' (FORMAT parquet)"
    )
    con.close()
    config.BANCO.unlink(missing_ok=True)
    banco = cnpj_db.conectar()
    banco.execute(
        "CREATE TABLE estab_cidade (uf VARCHAR, municipio VARCHAR, cnpj VARCHAR, matriz_filial VARCHAR, nome_fantasia VARCHAR, "
        "natureza_juridica VARCHAR, porte VARCHAR, cnae_fiscal VARCHAR, cnae_descricao VARCHAR, data_inicio_atividade VARCHAR, situacao VARCHAR, mei BOOLEAN)"
    )
    banco.executemany("INSERT INTO estab_cidade VALUES (?, ?, ?, '1', ?, '2062', ?, ?, ?, ?, '02', false)", [
        ("MG", "4865", "1", "Clínica Bela", "03", "9602502", "Estética", "20150101"),
        ("MG", "4865", "2", "Studio Pele", "01", "9602502", "Estética", "20250101"),
        ("MG", "4865", "3", "Salão Ana", "01", "9602501", "Cabeleireiros", "20100101"),
        ("MG", "4865", "4", "Mercado Bom", "05", "4711302", "Supermercados", "20000101"),
    ])
    banco.close()
    yield
    config.BANCO.unlink(missing_ok=True)


def chamada(nome, entrada, ident):
    return bloco(type="tool_use", name=nome, input=entrada, id=ident)


def test_busca_usa_as_ferramentas_e_entrega_perfil_conferido(base):
    final = {
        "nome_sugerido": "Agenda para estética", "oferta": "Sistema de agendamento", "cliente_ideal": "Clínicas de estética.",
        "criterio_ia": "Vendo agenda online para clínicas de estética.", "explicacao": "Estética é o centro.",
        "cidade": "Montes Claros", "uf": "MG",
        "atividades": [{"codigo": "9602502", "peso": 3, "motivo": "núcleo"}, {"codigo": "9602501", "peso": 1, "motivo": "vizinho"},
                       {"codigo": "0000000", "peso": 3, "motivo": "inventado"}],
        "palavras": [], "so_ativas": True, "incluir_mei": False, "idade_min": 2, "portes": [],
    }
    falso = ClienteFalso([
        lambda kw: resposta([bloco(type="thinking", thinking=""), chamada("achar_cidade", {"texto": "montes claros"}, "t1"),
                             chamada("buscar_atividades", {"termo": "estética"}, "t2")], "tool_use"),
        lambda kw: resposta([chamada("entregar_perfil", final, "t3")], "tool_use"),
    ])
    etapas = []
    p = busca_ia.montar_perfil("Vendo agenda para clínicas de estética em Montes Claros", etapa=etapas.append, ref="busca:x", cliente=falso)
    assert p["cidade"] == {"nome": "Montes Claros", "uf": "MG", "empresas": 4}
    assert [(a["codigo"], a["peso"], a["empresas"]) for a in p["atividades"]] == [("9602502", 3, 1), ("9602501", 1, 1)]  # a de 2025 tem menos de 2 anos
    assert p["filtros"]["idade_min"] == 2 and p["total"] == 2
    # o resultado das ferramentas voltou para a IA, junto com o que ela tinha dito
    volta = falso.pedidos[1]["messages"]
    assert volta[1]["role"] == "assistant" and volta[2]["content"][1]["tool_use_id"] == "t2"
    assert "9602502" in volta[2]["content"][1]["content"] and json.loads(volta[2]["content"][0]["content"])[0]["uf"] == "MG"
    assert falso.pedidos[0]["fallbacks"] == "default" and falso.pedidos[0]["model"] == ia.MODELO_BUSCA
    assert "Procurando atividades: “estética”" in etapas
    assert ia.uso_do_mes()["chamadas"] == 2


def test_busca_recusada_vira_frase_para_a_tela(base):
    falso = ClienteFalso([lambda kw: resposta([], "refusal")])
    with pytest.raises(ia.IaIndisponivel, match="não quis atender"):
        busca_ia.montar_perfil("Quero uma lista de clientes para vender agenda", cliente=falso)


def test_busca_que_so_conversa_recebe_um_lembrete_e_depois_desiste(base):
    falso = ClienteFalso([lambda kw: resposta([bloco(type="text", text="Pronto!")])] * 2)
    with pytest.raises(ia.IaIndisponivel, match="não conseguiu"):
        busca_ia.montar_perfil("Quero uma lista de clientes para vender agenda", cliente=falso)
    lembretes = [m["content"] for m in falso.pedidos[1]["messages"] if m["role"] == "user" and isinstance(m["content"], str)]
    assert lembretes[-1].startswith("Entregue a proposta")


def test_contagem_respeita_filtros(base):
    con = cnpj_db.conectar(somente_leitura=True)
    try:
        r = busca_ia.contar_empresas(con, cidade="Montes Claros", uf="MG", codigos=["9602502", "4711302"], portes=["05"])
        assert r["total"] == 1 and r["por_atividade"][0]["codigo"] == "4711302"
        assert busca_ia.exemplos_de_empresas(con, codigo="9602502", cidade="Montes Claros", uf="MG")[0]["nome"] in ("Clínica Bela", "Studio Pele")
        assert busca_ia.contar_empresas(con, cidade="Atlantida", uf="MG")["erro"].startswith("Cidade não encontrada")
    finally:
        con.close()


# ---------- avaliação ----------

def linha(i, **kw):
    return {"cnpj": f"{i:014d}", "empresa": f"Empresa {i}", "razao_social": f"EMPRESA {i} LTDA", "atividade": "Estética",
            "porte": "Pequeno porte", "idade_empresa": 5, "natureza": "Sociedade Empresária Limitada", "cidade": "Montes Claros",
            "uf": "MG", "telefone": "+5538999990000", "email": "dono@exemplo.com", "primeiro_nome": "João", **kw}


def test_so_dados_publicos_vao_para_a_ia():
    d = avaliacao_ia.dados_publicos(linha(1), 0)
    assert not {"telefone", "email", "primeiro_nome", "cnpj"} & set(d)
    pessoa = avaliacao_ia.dados_publicos(linha(2, empresa="Maria Da Silva", razao_social="MARIA DA SILVA 12345678901",
                                               natureza="Empresário (Individual)"), 1)
    assert "nome" not in pessoa and "razao_social" not in pessoa
    com_fantasia = avaliacao_ia.dados_publicos(linha(3, empresa="Studio Bela", razao_social="MARIA DA SILVA", natureza="Empresário (Individual)"), 2)
    assert com_fantasia["nome"] == "Studio Bela" and "razao_social" not in com_fantasia


def notas_json(kw, notas):
    lista = json.loads(kw["messages"][0]["content"])
    return resposta([bloco(type="text", text=json.dumps({"notas": [{"id": e["id"], "nota": notas(e), "motivo": "bom encaixe"} for e in lista]}))],
                    modelo="claude-haiku-5-5")


def test_avalia_em_grupos_guarda_e_nao_paga_de_novo(monkeypatch):
    monkeypatch.setattr(avaliacao_ia, "GRUPO", 2)
    empresas = [linha(i) for i in range(5)]
    falso = ClienteFalso([lambda kw: notas_json(kw, lambda e: 7)] * 3)
    r = avaliacao_ia.avaliar(empresas, "Vendo agenda online para clínicas de estética.", cliente=falso)
    assert (r["avaliadas"], r["recusadas"], r["motivo"]) == (5, 0, "concluída") and len(falso.pedidos) == 3
    assert falso.pedidos[0]["output_config"]["effort"] == "low" and falso.pedidos[0]["model"] == ia.MODELO_AVALIACAO
    guardadas = pastas.notas_da_ia(avaliacao_ia.chave("Vendo agenda online para clínicas de estética."))
    assert guardadas[f"{3:014d}"] == (7, "bom encaixe")
    de_novo = avaliacao_ia.avaliar(empresas, "Vendo agenda online para clínicas de estética.", cliente=ClienteFalso([]))
    assert de_novo["motivo"] == "todas já tinham nota" and de_novo["ja_tinham"] == 5


def test_grupo_recusado_fica_sem_nota_e_nota_fora_da_escala_e_cortada(monkeypatch):
    monkeypatch.setattr(avaliacao_ia, "GRUPO", 2)
    falso = ClienteFalso([lambda kw: resposta([], "refusal", "claude-haiku-5-5"), lambda kw: notas_json(kw, lambda e: 42)])
    monkeypatch.setattr(avaliacao_ia, "EM_PARALELO", 1)
    r = avaliacao_ia.avaliar([linha(i) for i in range(4)], "Vendo agenda online para clínicas.", cliente=falso)
    assert r["recusadas"] == 2 and r["avaliadas"] == 2
    assert set(n for n, _ in pastas.notas_da_ia(avaliacao_ia.chave("Vendo agenda online para clínicas.")).values()) == {10}


def test_avaliacao_respeita_o_teto(monkeypatch):
    monkeypatch.setenv("GARIMPO_IA_TETO_USD", "0.0001")
    with pytest.raises(ia.IaIndisponivel, match="teto"):
        avaliacao_ia.avaliar([linha(i) for i in range(100)], "Vendo agenda online para clínicas.", cliente=ClienteFalso([]))


# ---------- pela tela ----------

@pytest.fixture
def logado():
    with cliente() as c:
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


@pytest.fixture
def lista():
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    linhas = [{**base, **{k: v for k, v in linha(i).items() if k in base}, "telefone": f"+5538999990{i:03d}", "faixa": "A",
               "chance_dono": 90 - i, "whatsapp": "não verificado", "situacao": "ativa", "fonte": "Receita", "solar": "não consultado"}
              for i in range(6)]
    pipeline.ordenar(linhas, "Montes Claros", "empresa")
    id_ = "20260101-000000-montes-claros-mg"
    pipeline.salvar_parquet(linhas, servidor.LISTAS / f"{id_}.parquet")
    (servidor.LISTAS / f"{id_}.json").write_text(json.dumps({
        "id": id_, "criada_em": "2026-01-01T00:00:00", "cidade": "MONTES CLAROS", "uf": "MG", "atividades": [], "publico": "empresa",
        "perfil": {"id": "1", "nome": "Agenda", "solar": False, "oferta": "Agenda online", "cliente_ideal": "Clínicas de estética.",
                   "criterio_ia": "", "atividades": [], "filtros": {}},
        "parametros": {}, "status": "pronta", "etapa": None, "erro": None, "aviso": None, "resumo": {}, "versao": pipeline.VERSAO,
    }))
    yield id_
    for sufixo in ("parquet", "json"):
        (servidor.LISTAS / f"{id_}.{sufixo}").unlink(missing_ok=True)


def esperar_avaliacao():
    for _ in range(200):
        if not servidor._avaliando():
            return
        time.sleep(0.02)


def test_avaliar_lista_pela_tela_grava_notas_e_ordena(logado, lista, monkeypatch):
    falso = ClienteFalso([lambda kw: notas_json(kw, lambda e: e["id"] % 10)] * 2)
    monkeypatch.setattr(ia, "cliente", lambda: falso)
    antes = logado.get(f"/api/listas/{lista}/avaliacao", params={"quantidade": 6}).json()
    assert antes["criterio"] == "Vendo: Agenda online. Cliente ideal: Clínicas de estética." and antes["a_avaliar"] == 6
    assert logado.post(f"/api/listas/{lista}/avaliar", json={"amostra": True}).json()["empresas"] == 6
    esperar_avaliacao()
    estado = logado.get(f"/api/listas/{lista}/avaliacao").json()
    assert [a["nota"] for a in estado["andamento"]["amostra"]] == [5, 4, 3, 2, 1, 0]
    assert estado["avaliacao"]["avaliadas"] == 6 and estado["a_avaliar"] == 0
    r = logado.get(f"/api/listas/{lista}/contatos", params={"ordem": "ia", "ia_min": 4}).json()
    assert [l["nota_ia"] for l in r["linhas"]] == [5, 4] and r["linhas"][0]["motivo_ia"] == "bom encaixe"


def test_avaliar_sem_chave_recusa(logado, lista, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert logado.post(f"/api/listas/{lista}/avaliar", json={}).status_code == 409
