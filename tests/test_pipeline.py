import csv

import pytest

from solar_leads import pipeline
from solar_leads.pipeline import celula_segura, montar, salvar_csv, sem_repetidos, slug


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("=1+1", "'=1+1"),
        ("@SOMA(A1)", "'@SOMA(A1)"),
        ("+cmd|x", "'+cmd|x"),
        ("-cmd", "'-cmd"),
        ("+", "'+"),
        ("\tabc", "'\tabc"),
        ("\rabc", "'\rabc"),
        ("+5511999990001", "+5511999990001"),
        ("-5", "-5"),
        ("Padaria", "Padaria"),
        ("", ""),
        (42, 42),
        (3.5, 3.5),
        (None, None),
        (True, True),
    ],
)
def test_celula_segura(valor, esperado):
    assert celula_segura(valor) == esperado


@pytest.mark.parametrize("texto,esperado", [("Uberlândia", "uberlandia"), ("Google Maps", "google-maps"), ("  A--B  ", "a-b"), ("", "")])
def test_slug(texto, esperado):
    assert slug(texto) == esperado


def test_sem_repetidos_mantem_a_primeira_por_telefone_e_por_dono():
    linhas = [
        {"telefone": "+551", "dono_chave": "A"},
        {"telefone": "+551", "dono_chave": "B"},  # telefone repetido
        {"telefone": "+552", "dono_chave": "A"},  # dono repetido
        {"telefone": "+553", "dono_chave": ""},
        {"telefone": "+554", "dono_chave": ""},  # sem dono não conta como repetido
        {"telefone": "+555", "dono_chave": "C"},
    ]
    assert [l["telefone"] for l in sem_repetidos(linhas)] == ["+551", "+553", "+554", "+555"]


def test_sem_repetidos_lista_vazia():
    assert sem_repetidos([]) == []


# ---------- montar ----------

def t(numero, pontos=70, faixa="A"):
    return {"tel": numero, "pontos": pontos, "faixa": faixa, "motivos": ["celular"]}


def mk(cnpj, telefones, **extra):
    base = {
        "primeiro_nome": "Ana", "aptidao_solar": 5, "dono_chave": "", "solar": "sem registro", "porte": "",
        "fonte": "Google Maps", "situacao": "ativa", "cnpj": cnpj, "telefones": telefones, "email": "", "papel": "",
    }
    base.update(extra)
    return base


def test_montar_whatsapp_confirmado_vem_primeiro_dentro_da_faixa():
    sem_wa = mk("1", [t("11999990001", 80)])
    com_wa = mk("2", [t("11999990002", 66)])
    wa = {"11999990002": {"tem": True, "business": False, "nome_business": ""}}
    linhas, disparo = montar([sem_wa, com_wa], "Uberlândia", wa)
    assert [l["cnpj"] for l in linhas] == ["2", "1"]
    assert [l["ordem"] for l in linhas] == [0, 1]
    assert len(disparo) == 2


def test_montar_faixa_c_fica_no_fim_e_fora_do_disparo():
    fraco = mk("1", [t("11999990001", 30, "C")])
    forte = mk("2", [t("11999990002", 70)], aptidao_solar=0)
    linhas, disparo = montar([fraco, forte], "X")
    assert [l["cnpj"] for l in linhas] == ["2", "1"]
    assert [l["cnpj"] for l in disparo] == ["2"]


def test_montar_dentro_da_faixa_prefere_maps_depois_ativa_depois_aptidao():
    a = mk("1", [t("11999990001")], fonte="Base CNPJ", aptidao_solar=9)
    b = mk("2", [t("11999990002")], fonte="Google Maps", aptidao_solar=1)
    c = mk("3", [t("11999990003")], fonte="Google Maps", aptidao_solar=1, situacao="baixada")
    d = mk("4", [t("11999990004")], fonte="Google Maps", aptidao_solar=8)
    linhas, _ = montar([a, b, c, d], "X")
    assert [l["cnpj"] for l in linhas] == ["4", "2", "3", "1"]


def test_montar_outra_empresa_do_dono_tem_solar_propaga_para_as_demais_linhas():
    com = mk("1", [t("11999990001")], dono_chave="D|***111111**", solar="empresa já tem")
    sem = mk("2", [t("11999990002")], dono_chave="D|***111111**", solar="sem registro")
    fraco = mk("3", [t("11999990003")], dono_chave="D|***111111**", solar="dono: indício fraco")
    unidade = mk("4", [t("11999990004")], dono_chave="D|***111111**", solar="outra unidade da empresa tem")
    outro = mk("5", [t("11999990005")], dono_chave="E|***222222**", solar="sem registro")
    avulso = mk("6", [t("11999990006")], dono_chave="", solar="sem registro")
    linhas, _ = montar([com, sem, fraco, unidade, outro, avulso], "X")
    por = {l["cnpj"]: l["solar"] for l in linhas}
    assert por == {
        "1": "empresa já tem", "2": "outra empresa do dono tem", "3": "outra empresa do dono tem",
        "4": "outra unidade da empresa tem", "5": "sem registro", "6": "sem registro",
    }


def test_montar_nao_propaga_nao_consultado():
    com = mk("1", [t("11999990001")], dono_chave="D|***111111**", solar="empresa já tem")
    nc = mk("2", [t("11999990002")], dono_chave="D|***111111**", solar="não consultado")
    linhas, _ = montar([com, nc], "X")
    assert {l["cnpj"]: l["solar"] for l in linhas}["2"] == "não consultado"


def test_montar_tags():
    l1 = mk("1", [t("11999990001")], solar="empresa já tem")
    l2 = mk("2", [t("11999990002", 50, "B")], situacao="", fonte="Base CNPJ")
    linhas, _ = montar([l1, l2], "São José do Rio Preto")
    tags = {l["cnpj"]: l["tags"].split(",") for l in linhas}
    assert tags["1"] == ["solar", "faixa-a", "sao-jose-rio-preto", "fonte-google-maps", "cnpj-ativa", "ja-tem-solar"]
    assert tags["2"] == ["solar", "faixa-b", "sao-jose-rio-preto", "fonte-base-cnpj"]


def test_montar_indicio_fraco_nao_gera_tag_ja_tem_solar():
    linhas, _ = montar([mk("1", [t("11999990001")], solar="dono: indício fraco")], "X")
    assert "ja-tem-solar" not in linhas[0]["tags"]


@pytest.mark.parametrize("porte,esperado", [("01", "Microempresa"), ("03", "Pequeno porte"), ("5", "Médio ou grande"), ("", ""), (None, ""), ("99", "")])
def test_montar_traduz_o_porte(porte, esperado):
    linhas, _ = montar([mk("1", [t("11999990001")], porte=porte)], "X")
    assert linhas[0]["porte"] == esperado


def test_montar_filtra_pela_aptidao_minima():
    baixo = mk("1", [t("11999990001")], aptidao_solar=2)
    alto = mk("2", [t("11999990002")], aptidao_solar=6)
    linhas, _ = montar([baixo, alto], "X", min_aptidao=5)
    assert [l["cnpj"] for l in linhas] == ["2"]


def test_montar_disparo_nao_repete_telefone_nem_dono():
    a = mk("1", [t("11999990001", 80)], dono_chave="D|***111111**")
    b = mk("2", [t("11999990001", 70)], dono_chave="E|***222222**")  # mesmo telefone
    c = mk("3", [t("11999990003", 60)], dono_chave="D|***111111**")  # mesmo dono
    d = mk("4", [t("11999990004", 50, "B")], dono_chave="")
    linhas, disparo = montar([a, b, c, d], "X")
    assert len(linhas) == 4
    assert [l["cnpj"] for l in disparo] == ["1", "4"]


def test_montar_so_pessoal_deixa_quem_so_tem_business_sem_telefone():
    wa = {"11999990001": {"tem": True, "business": True, "nome_business": "X"}}
    linhas, disparo = montar([mk("1", [t("11999990001")])], "X", wa, so_pessoal=True)
    assert linhas[0]["telefone"] == "" and disparo == []


def test_resumir_conta_faixas_e_repetidos():
    a = mk("1", [t("11999990001", 80)], papel="dono principal")
    b = mk("2", [t("11999990001", 70)], papel="outro sócio")
    linhas, disparo = montar([a, b], "X")
    r = pipeline.resumir(linhas, disparo)
    assert r["contatos_para_disparo"] == 1 and r["repetidos_removidos"] == 1
    assert r["total_de_empresas"] == 1 and r["linhas_de_outros_socios"] == 1


def test_salvar_csv_neutraliza_formulas_e_preserva_telefone(tmp_path):
    linha = {c: "" for c in pipeline.CHAVES}
    linha.update(primeiro_nome="=HYPERLINK(1)", telefone="+5511999990001", chance_dono=70)
    destino = tmp_path / "saida.csv"
    salvar_csv([linha], destino)
    with open(destino, encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.reader(f))
    assert linhas[0] == pipeline.CABECALHOS
    registro = dict(zip(linhas[0], linhas[1]))
    assert registro["First Name"] == "'=HYPERLINK(1)"
    assert registro["Phone"] == "+5511999990001"
    assert registro["Chance de ser o dono"] == "70"
