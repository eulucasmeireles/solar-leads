"""Pastas de contatos: o usuário separa leads em grupos nomeados antes de disparar.

Ficam num SQLite próprio (dados/pastas.sqlite), fora da base de CNPJ, para que
gerar uma lista de novo não apague nada. Cada contato guardado leva uma cópia
dos dados dele naquele momento; a identidade do contato é o telefone.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

from . import config

ARQUIVO = config.DADOS / "pastas.sqlite"


class PastaInvalida(ValueError):
    pass


def _con() -> sqlite3.Connection:
    config.DADOS.mkdir(parents=True, exist_ok=True)
    novo = not ARQUIVO.exists()
    con = sqlite3.connect(ARQUIVO, timeout=30)
    if novo:
        ARQUIVO.chmod(0o600)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS pastas (
          id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE COLLATE NOCASE, criada_em TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pasta_contatos (
          pasta_id INTEGER NOT NULL REFERENCES pastas(id) ON DELETE CASCADE,
          telefone TEXT NOT NULL, lista_id TEXT NOT NULL, cidade TEXT NOT NULL, adicionado_em TEXT NOT NULL,
          resultado TEXT NOT NULL DEFAULT '', dados TEXT NOT NULL,
          PRIMARY KEY (pasta_id, telefone));
        CREATE INDEX IF NOT EXISTS pasta_contatos_tel ON pasta_contatos (telefone);
        CREATE TABLE IF NOT EXISTS nao_contatar (
          telefone TEXT PRIMARY KEY, motivo TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL);
        """
    )
    return con


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _nome_valido(nome: str) -> str:
    nome = " ".join((nome or "").split())
    if not nome:
        raise PastaInvalida("Dê um nome para a pasta.")
    if len(nome) > 80:
        raise PastaInvalida("O nome da pasta pode ter até 80 letras.")
    return nome


def listar() -> list[dict]:
    with closing(_con()) as con:
        linhas = con.execute(
            """
            SELECT p.id, p.nome, p.criada_em, count(c.telefone) AS contatos,
                   (SELECT group_concat(DISTINCT cidade) FROM pasta_contatos WHERE pasta_id = p.id) AS cidades
            FROM pastas p LEFT JOIN pasta_contatos c ON c.pasta_id = p.id
            GROUP BY p.id ORDER BY p.criada_em DESC, p.id DESC
            """
        ).fetchall()
    return [{**dict(l), "cidades": sorted((l["cidades"] or "").split(",")) if l["cidades"] else []} for l in linhas]


def uma(pasta_id: int) -> dict | None:
    return next((p for p in listar() if p["id"] == pasta_id), None)


def criar(nome: str) -> int:
    nome = _nome_valido(nome)
    with closing(_con()) as con, con:
        try:
            return con.execute("INSERT INTO pastas (nome, criada_em) VALUES (?, ?)", [nome, _agora()]).lastrowid
        except sqlite3.IntegrityError as e:
            raise PastaInvalida("Já existe uma pasta com esse nome.") from e


def renomear(pasta_id: int, nome: str) -> None:
    nome = _nome_valido(nome)
    with closing(_con()) as con, con:
        try:
            con.execute("UPDATE pastas SET nome = ? WHERE id = ?", [nome, pasta_id])
        except sqlite3.IntegrityError as e:
            raise PastaInvalida("Já existe uma pasta com esse nome.") from e


def excluir(pasta_id: int) -> None:
    with closing(_con()) as con, con:
        con.execute("DELETE FROM pastas WHERE id = ?", [pasta_id])


def adicionar(pasta_id: int, linhas: list[dict], lista_id: str, cidade: str) -> tuple[int, int]:
    """Guarda os contatos na pasta. Devolve (adicionados, já estavam)."""
    agora = _agora()
    with closing(_con()) as con, con:
        antes = con.execute("SELECT count(*) FROM pasta_contatos WHERE pasta_id = ?", [pasta_id]).fetchone()[0]
        con.executemany(
            "INSERT OR IGNORE INTO pasta_contatos (pasta_id, telefone, lista_id, cidade, adicionado_em, dados) VALUES (?, ?, ?, ?, ?, ?)",
            [(pasta_id, l["telefone"], lista_id, cidade, agora, json.dumps(l, ensure_ascii=False)) for l in linhas if l.get("telefone")],
        )
        depois = con.execute("SELECT count(*) FROM pasta_contatos WHERE pasta_id = ?", [pasta_id]).fetchone()[0]
    novos = depois - antes
    return novos, len(linhas) - novos


def remover(pasta_id: int, telefones: list[str]) -> int:
    with closing(_con()) as con, con:
        cur = con.executemany("DELETE FROM pasta_contatos WHERE pasta_id = ? AND telefone = ?", [(pasta_id, t) for t in telefones])
        return cur.rowcount


def contatos(pasta_id: int, busca: str = "", pagina: int = 1, por_pagina: int = 50) -> tuple[int, list[dict]]:
    """(total, linhas da página), na ordem em que entraram na pasta."""
    with closing(_con()) as con:
        todas = con.execute(
            "SELECT lista_id, adicionado_em, resultado, dados FROM pasta_contatos WHERE pasta_id = ? ORDER BY rowid", [pasta_id]
        ).fetchall()
    linhas = [{**json.loads(l["dados"]), "lista_id": l["lista_id"], "adicionado_em": l["adicionado_em"], "resultado": l["resultado"]} for l in todas]
    termo = busca.strip().lower()
    if termo:
        campos = ("primeiro_nome", "sobrenome", "empresa", "razao_social", "cnpj", "telefone", "bairro")
        linhas = [l for l in linhas if termo in " ".join(str(l.get(c, "")) for c in campos).lower()]
    inicio = (max(1, pagina) - 1) * por_pagina
    return len(linhas), linhas[inicio : inicio + por_pagina]


def telefones(pasta_id: int | None = None) -> list[str]:
    """Telefones de uma pasta, ou de todas quando `pasta_id` é None."""
    with closing(_con()) as con:
        if pasta_id is None:
            return [l[0] for l in con.execute("SELECT DISTINCT telefone FROM pasta_contatos").fetchall()]
        return [l[0] for l in con.execute("SELECT telefone FROM pasta_contatos WHERE pasta_id = ?", [pasta_id]).fetchall()]


def pastas_dos(lista_de_telefones: list[str]) -> dict[str, list[dict]]:
    """{telefone: [{id, nome}, ...]} só para quem está em alguma pasta."""
    if not lista_de_telefones:
        return {}
    saida: dict[str, list[dict]] = {}
    with closing(_con()) as con:
        for i in range(0, len(lista_de_telefones), 500):
            parte = lista_de_telefones[i : i + 500]
            marcas = ",".join("?" * len(parte))
            for l in con.execute(
                f"SELECT c.telefone, p.id, p.nome FROM pasta_contatos c JOIN pastas p ON p.id = c.pasta_id WHERE c.telefone IN ({marcas})",
                parte,
            ).fetchall():
                saida.setdefault(l["telefone"], []).append({"id": l["id"], "nome": l["nome"]})
    return saida


# ---- Não contatar ----
# Quem pede para sair nunca mais aparece em lista, pasta ou exportação. Guardamos só o
# telefone, que é o mínimo necessário para reconhecer a pessoa nas próximas listas.

def bloquear(telefones_e164: list[str], motivo: str = "") -> int:
    """Marca os telefones como "não contatar" e tira essas pessoas de todas as pastas. Devolve quantos são novos."""
    agora = _agora()
    with closing(_con()) as con, con:
        antes = con.execute("SELECT count(*) FROM nao_contatar").fetchone()[0]
        con.executemany(
            "INSERT OR IGNORE INTO nao_contatar (telefone, motivo, criado_em) VALUES (?, ?, ?)",
            [(t, motivo[:120], agora) for t in telefones_e164],
        )
        con.executemany("DELETE FROM pasta_contatos WHERE telefone = ?", [(t,) for t in telefones_e164])
        return con.execute("SELECT count(*) FROM nao_contatar").fetchone()[0] - antes


def desbloquear(telefone: str) -> int:
    with closing(_con()) as con, con:
        return con.execute("DELETE FROM nao_contatar WHERE telefone = ?", [telefone]).rowcount


def bloqueados() -> list[str]:
    with closing(_con()) as con:
        return [l[0] for l in con.execute("SELECT telefone FROM nao_contatar").fetchall()]


def bloqueados_recentes(limite: int = 20) -> tuple[int, list[dict]]:
    with closing(_con()) as con:
        total = con.execute("SELECT count(*) FROM nao_contatar").fetchone()[0]
        itens = con.execute(
            "SELECT telefone, motivo, criado_em FROM nao_contatar ORDER BY criado_em DESC, rowid DESC LIMIT ?", [limite]
        ).fetchall()
    return total, [dict(i) for i in itens]


def copia_de_seguranca(pasta_destino, manter: int = 14) -> str:
    """Copia o arquivo das pastas (único dado que não se refaz) e guarda as últimas `manter` cópias."""
    from pathlib import Path

    destino = Path(pasta_destino)
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / f"pastas-{datetime.now():%Y%m%d}.sqlite"
    with closing(_con()) as origem, closing(sqlite3.connect(arquivo)) as copia:
        origem.backup(copia)
    arquivo.chmod(0o600)
    for velho in sorted(destino.glob("pastas-*.sqlite"))[:-manter]:
        velho.unlink()
    return str(arquivo)
