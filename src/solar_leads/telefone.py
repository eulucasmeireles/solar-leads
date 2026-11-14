"""Normalização e classificação de telefones brasileiros."""

from __future__ import annotations

import re

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


def e_celular(nacional: str | None) -> bool:
    if not nacional:
        return False
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
