import pytest

from solar_leads.telefone import e164, e_celular, normalizar


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("+55 11 99999-9999", "11999999999"),
        ("(11) 3333-4444", "1133334444"),
        ("5511999999999", "11999999999"),
        ("011 99999-9999", "11999999999"),
        ("(11) 9999-9999", "11999999999"),  # 8 dígitos antigos ganham o 9
        ("(21) 8888-7777", "21988887777"),
    ],
)
def test_normalizar_com_ddd_no_proprio_numero(entrada, esperado):
    assert normalizar(entrada) == esperado


def test_normalizar_ddd_separado_do_numero():
    assert normalizar("999999999", "11") == "11999999999"
    assert normalizar("33334444", "011") == "1133334444"


def test_normalizar_ddd_separado_com_oito_digitos_de_celular_ganha_nove():
    assert normalizar("99999999", "11") == "11999999999"


@pytest.mark.parametrize("lixo", [None, "", "abc", "123", "00000000000", "99999", "1111111111111111"])
def test_normalizar_lixo_devolve_none(lixo):
    assert normalizar(lixo) is None


def test_normalizar_ddd_inexistente_devolve_none():
    assert normalizar("999999999", "00") is None


def test_e_celular():
    assert e_celular("11999999999") is True
    assert e_celular("1133334444") is False


@pytest.mark.parametrize("v", [None, "", "abc"])
def test_e_celular_vazio_ou_invalido_e_falso(v):
    assert e_celular(v) is False


def test_e164_acrescenta_codigo_do_pais():
    assert e164("11999999999") == "+5511999999999"
