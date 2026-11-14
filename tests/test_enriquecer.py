from datetime import date

import pytest

from solar_leads import enriquecer
from solar_leads.enriquecer import (
    _capital, _chave_identifica, _cpf6, _data, _nome_pessoa, _numero_porta, escolher_telefone, norm,
)


def tel(numero="11999990001", pontos=70, faixa="A", motivos=("celular",)):
    return {"tel": numero, "pontos": pontos, "faixa": faixa, "motivos": list(motivos)}


def lead(telefones, primeiro_nome="Joao", **extra):
    return {"primeiro_nome": primeiro_nome, "telefones": telefones, **extra}


# ---------- norm ----------

@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Padaria do João LTDA", "PADARIA JOAO"),
        ("  café   São  José ", "CAFE SAO JOSE"),
        ("Mercado & Cia ME", "MERCADO"),
        ("Bar-do-Zé", "BAR ZE"),
        ("", ""),
        (None, ""),
    ],
)
def test_norm_tira_acentos_pontuacao_e_sufixos_societarios(texto, esperado):
    assert norm(texto) == esperado


# ---------- _numero_porta ----------

@pytest.mark.parametrize(
    "lugar,esperado",
    [
        ({"rua": "Rua A, 123"}, "123"),
        ({"rua": "Av. B, nº 45 - Centro"}, "45"),
        ({"rua": "Av. B, n° 45"}, "45"),
        ({"rua": "Rua A, s/n"}, ""),
        ({"rua": "Rua A", "endereco": "Rua A, 55 - Centro"}, "55"),
        ({"rua": "Rua A, 7", "endereco": "Rua A, 99"}, "7"),
        ({"rua": None, "endereco": None}, ""),
        ({}, ""),
    ],
)
def test_numero_porta(lugar, esperado):
    assert _numero_porta(lugar) == esperado


# ---------- _data / _capital ----------

def test_data_valida_com_espacos():
    assert _data(" 20200131 ") == date(2020, 1, 31)


@pytest.mark.parametrize("v", [None, "", "00000000", "2020-01-31", "20201340", "abc"])
def test_data_invalida_devolve_none(v):
    assert _data(v) is None


@pytest.mark.parametrize(
    "v,esperado",
    [("1.000,50", 1000.5), ("0,00", 0.0), ("150000", 150000.0), ("2.500.000,00", 2_500_000.0)],
)
def test_capital_no_formato_da_receita(v, esperado):
    assert _capital(v) == esperado


@pytest.mark.parametrize("v", [None, "", "abc", "R$ 10"])
def test_capital_invalido_devolve_none(v):
    assert _capital(v) is None


# ---------- _nome_pessoa / _cpf6 / _chave_identifica ----------

@pytest.mark.parametrize(
    "nome,esperado",
    [
        ("JOAO DA SILVA", ("Joao", "Da Silva")),
        ("MARIA", ("Maria", "")),
        ("MARIA 12345678901", ("Maria", "")),
        ("", ("", "")),
        ("   ", ("", "")),
        ("123.456", ("", "")),
    ],
)
def test_nome_pessoa(nome, esperado):
    assert _nome_pessoa(nome) == esperado


def test_cpf6_do_meio_do_cpf_mascarado():
    assert _cpf6({"chave": "JOAO SILVA|***123456**"}, None) == "123456"


def test_cpf6_zeros_nao_identificam():
    assert _cpf6({"chave": "JOAO|***000000**"}, None) == ""


def test_cpf6_sem_dono_e_vazio():
    assert _cpf6(None, None) == ""


def test_cpf6_empresario_individual_com_cpf_inteiro_na_razao_social():
    assert _cpf6({"chave": ""}, {"razao_social": "JOAO 12345678901"}) == "456789"


def test_cpf6_cpf_na_razao_social_so_vale_sem_chave():
    assert _cpf6({"chave": "JOAO"}, {"razao_social": "JOAO 12345678901"}) == ""


def test_cpf6_numero_maior_que_11_digitos_na_razao_nao_e_cpf():
    assert _cpf6({"chave": ""}, {"razao_social": "EMPRESA 123456789012"}) == ""


@pytest.mark.parametrize(
    "chave,esperado",
    [("JOAO|***123456**", True), ("***123456**", True), ("JOAO|***000000**", False), ("JOAO", False), ("", False), (None, False)],
)
def test_chave_identifica(chave, esperado):
    assert _chave_identifica(chave) is esperado


# ---------- escolher_telefone ----------

def test_sem_whatsapp_informado_fica_com_o_melhor_telefone_nao_verificado():
    r = escolher_telefone(lead([tel("11999990001", 70), tel("11999990002", 50, "B")]))
    assert r["telefone"] == "+5511999990001"
    assert r["telefone_alternativo"] == "+5511999990002"
    assert r["whatsapp"] == "não verificado"
    assert r["faixa"] == "A" and r["chance_dono"] == 70
    assert "telefones" not in r


def test_telefones_de_faixa_c_nunca_sao_escolhidos():
    r = escolher_telefone(lead([tel(pontos=30, faixa="C")]))
    assert r["telefone"] == "" and r["faixa"] == "C" and r["chance_dono"] == 0
    assert r["motivos_contato"] == "sem celular válido"


def test_faixa_a_sem_nome_do_dono_vira_b():
    assert escolher_telefone(lead([tel()], primeiro_nome=""))["faixa"] == "B"


def test_numero_sem_conta_de_whatsapp_sai_e_marca_whatsapp_nao():
    r = escolher_telefone(lead([tel()]), {"11999990001": {"tem": False, "business": False, "nome_business": ""}})
    assert r["telefone"] == "" and r["whatsapp"] == "não" and r["faixa"] == "C"


def test_conta_pessoal_ganha_10_pontos_e_pode_subir_de_faixa():
    wa = {"11999990001": {"tem": True, "business": False, "nome_business": ""}}
    r = escolher_telefone(lead([tel(pontos=60, faixa="B")]), wa)
    assert r["chance_dono"] == 70 and r["faixa"] == "A"
    assert r["whatsapp"] == "sim" and r["tipo_whatsapp"] == "pessoal"
    assert "conta pessoal de WhatsApp" in r["motivos_contato"]


def test_pontos_da_conta_pessoal_ficam_limitados_a_100():
    wa = {"11999990001": {"tem": True, "business": False, "nome_business": ""}}
    assert escolher_telefone(lead([tel(pontos=98)]), wa)["chance_dono"] == 100


def test_conta_business_nao_ganha_pontos_e_guarda_o_nome():
    wa = {"11999990001": {"tem": True, "business": True, "nome_business": "Padaria X"}}
    r = escolher_telefone(lead([tel(pontos=60, faixa="B")]), wa)
    assert r["chance_dono"] == 60 and r["faixa"] == "B"
    assert r["tipo_whatsapp"] == "business" and r["nome_whatsapp_business"] == "Padaria X"


def test_conta_pessoal_passa_na_frente_de_business_de_pontos_proximos():
    wa = {
        "11999990001": {"tem": True, "business": True, "nome_business": "X"},
        "11999990002": {"tem": True, "business": False, "nome_business": ""},
    }
    r = escolher_telefone(lead([tel("11999990001", 70), tel("11999990002", 65)]), wa)
    assert r["telefone"] == "+5511999990002"
    assert r["telefone_alternativo"] == "+5511999990001"


def test_numero_ainda_nao_verificado_fica_e_o_lead_continua_nao_verificado():
    r = escolher_telefone(lead([tel()]), {"11999990009": {"tem": True, "business": False, "nome_business": ""}})
    assert r["telefone"] == "+5511999990001" and r["whatsapp"] == "não verificado"


def test_so_pessoal_descarta_contas_business():
    wa = {"11999990001": {"tem": True, "business": True, "nome_business": "X"}}
    r = escolher_telefone(lead([tel()]), wa, so_pessoal=True)
    assert r["telefone"] == ""


def test_so_pessoal_mantem_contas_pessoais():
    wa = {"11999990001": {"tem": True, "business": False, "nome_business": ""}}
    assert escolher_telefone(lead([tel()]), wa, so_pessoal=True)["telefone"] == "+5511999990001"


def test_escolher_telefone_nao_altera_o_lead_original():
    original = lead([tel(pontos=60, faixa="B")])
    wa = {"11999990001": {"tem": True, "business": False, "nome_business": ""}}
    escolher_telefone(original, wa)
    assert original["telefones"][0]["pontos"] == 60 and "tipo" not in original["telefones"][0]
