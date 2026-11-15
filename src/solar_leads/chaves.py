"""Chaves de API: deixam as automações do dono chamar /api/v1 sem usar a senha do sistema.

A chave completa só aparece uma vez, na criação; o sistema guarda apenas o código
embaralhado dela (SHA-256). Revogar vale na hora.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from contextlib import closing

from . import pastas

PREFIXO = "sl_"
MAX_ATIVAS = 20
_CACHE_S = 30  # quanto tempo uma chave já conferida vale sem nova leitura do arquivo
_cache: dict[str, tuple[float, dict]] = {}
_ultimo_uso_gravado: dict[int, float] = {}


class ChaveInvalida(ValueError):
    pass


def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def criar(nome: str) -> tuple[dict, str]:
    """Devolve (registro, chave completa). A chave completa não fica guardada em lugar nenhum."""
    nome = " ".join((nome or "").split())
    if not 1 <= len(nome) <= 60:
        raise ChaveInvalida("Dê um nome de até 60 letras para a chave (ex.: n8n, GHL).")
    chave = PREFIXO + secrets.token_urlsafe(32)
    with closing(pastas._con()) as con, con:
        if con.execute("SELECT count(*) FROM chaves_api WHERE revogada_em = ''").fetchone()[0] >= MAX_ATIVAS:
            raise ChaveInvalida(f"Já existem {MAX_ATIVAS} chaves ativas. Revogue alguma antes de criar outra.")
        id_ = con.execute(
            "INSERT INTO chaves_api (nome, comeco, hash, criada_em) VALUES (?, ?, ?, ?)",
            [nome, chave[:7], _hash(chave), pastas._agora()],
        ).lastrowid
    return uma(id_), chave


def uma(id_: int) -> dict | None:
    with closing(pastas._con()) as con:
        l = con.execute("SELECT id, nome, comeco, criada_em, ultimo_uso, revogada_em FROM chaves_api WHERE id = ?", [id_]).fetchone()
    return dict(l) if l else None


def listar() -> list[dict]:
    """Ativas primeiro, depois as revogadas; nunca traz a chave nem o código embaralhado."""
    with closing(pastas._con()) as con:
        linhas = con.execute(
            "SELECT id, nome, comeco, criada_em, ultimo_uso, revogada_em FROM chaves_api ORDER BY revogada_em <> '', id DESC"
        ).fetchall()
    return [dict(l) for l in linhas]


def revogar(id_: int) -> bool:
    with closing(pastas._con()) as con, con:
        feito = con.execute(
            "UPDATE chaves_api SET revogada_em = ? WHERE id = ? AND revogada_em = ''", [pastas._agora(), id_]
        ).rowcount > 0
    _cache.clear()
    return feito


def conferir(chave: str) -> dict | None:
    """O registro da chave, se ela existir e não estiver revogada. Anota o último uso (no máximo uma vez por minuto)."""
    if not chave or not chave.startswith(PREFIXO) or len(chave) > 200:
        return None
    h, agora = _hash(chave), time.monotonic()
    guardado = _cache.get(h)
    if guardado and agora - guardado[0] < _CACHE_S:
        registro = guardado[1]
    else:
        with closing(pastas._con()) as con:
            l = con.execute("SELECT id, nome FROM chaves_api WHERE hash = ? AND revogada_em = ''", [h]).fetchone()
        if not l:
            return None
        registro = dict(l)
        if len(_cache) > 200:
            _cache.clear()
        _cache[h] = (agora, registro)
    if agora - _ultimo_uso_gravado.get(registro["id"], -1e9) > 60:
        _ultimo_uso_gravado[registro["id"]] = agora
        with closing(pastas._con()) as con, con:
            con.execute("UPDATE chaves_api SET ultimo_uso = ? WHERE id = ?", [pastas._agora(), registro["id"]])
    return registro
