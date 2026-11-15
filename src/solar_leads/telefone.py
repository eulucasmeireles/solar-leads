"""Normalização e classificação de telefones brasileiros."""

from __future__ import annotations

import re
from functools import lru_cache

import phonenumbers
from phonenumbers import PhoneNumberType

_DIGITOS = re.compile(r"\D+")


def so_digitos(valor: str | None) -> str:
    return _DIGITOS.sub("", valor or "")


def normalizar(valor: str | None, ddd: str | None = None) -> str | None:
    """Devolve o número nacional com DDD (10 ou 11 dígitos) ou None se inválido.

    Aceita "+55 11 99999-9999", "(11) 3333-4444", ou ddd e número separados
    (formato do cadastro da Receita). Celulares antigos de 8 dígitos ganham o 9.
    """
    num = so_digitos(valor)
    if ddd is not None:
        num = so_digitos(ddd).lstrip("0")[-2:] + num
    if num.startswith("55") and len(num) in (12, 13):
        num = num[2:]
    num = num.lstrip("0")
    if len(num) == 10 and num[2] in "6789":
        num = num[:2] + "9" + num[2:]
    if len(num) not in (10, 11):
        return None
    try:
        parsed = phonenumbers.parse("+55" + num, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return num


@lru_cache(maxsize=None)
def _ddd_tem_celular(ddd: str) -> bool:
    """Se o DDD existe para celular, pela mesma biblioteca; calculado uma vez por DDD."""
    return _e_celular_pela_biblioteca(ddd + "991234567")


def e_celular(nacional: str | None) -> bool:
    if not nacional:
        return False
    # Caso de quase todo celular: 11 dígitos com 9 depois do DDD. Resolver aqui evita uma
    # consulta lenta à biblioteca para cada um dos milhares de números de uma lista.
    if len(nacional) == 11 and nacional[2] == "9" and nacional.isdigit():
        return _ddd_tem_celular(nacional[:2])
    return _e_celular_pela_biblioteca(nacional)


def suspeito(nacional: str | None) -> bool:
    """Número de enfeite no cadastro: os oito últimos dígitos usam um ou dois algarismos só, ou são uma sequência."""
    fim = (nacional or "")[-8:]
    return len(fim) == 8 and (len(set(fim)) <= 2 or fim in "0123456789" or fim in "9876543210")


@lru_cache(maxsize=200_000)
def _e_celular_pela_biblioteca(nacional: str) -> bool:
    try:
        parsed = phonenumbers.parse("+55" + nacional, None)
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.number_type(parsed) in (
        PhoneNumberType.MOBILE,
        PhoneNumberType.FIXED_LINE_OR_MOBILE,
    )


def e164(nacional: str) -> str:
    return "+55" + nacional


# DDDs de cada estado (plano de numeração da Anatel). Brasília (61) também atende o entorno, em Goiás.
DDDS_POR_UF: dict[str, set[str]] = {
    uf: set(ddds.split())
    for uf, ddds in {
        "SP": "11 12 13 14 15 16 17 18 19", "RJ": "21 22 24", "ES": "27 28", "MG": "31 32 33 34 35 37 38",
        "PR": "41 42 43 44 45 46", "SC": "47 48 49", "RS": "51 53 54 55", "DF": "61", "GO": "61 62 64",
        "TO": "63", "MT": "65 66", "MS": "67", "AC": "68", "RO": "69", "BA": "71 73 74 75 77", "SE": "79",
        "PE": "81 87", "AL": "82", "PB": "83", "RN": "84", "CE": "85 88", "PI": "86 89", "PA": "91 93 94",
        "AM": "92 97", "RR": "95", "AP": "96", "MA": "98 99",
    }.items()
}
