"""Pastas de contatos: o usuário separa leads em grupos nomeados antes de disparar.

Ficam num SQLite próprio (dados/pastas.sqlite), fora da base de CNPJ, para que
gerar uma lista de novo não apague nada. Cada contato guardado leva uma cópia
dos dados dele naquele momento; a identidade do contato é o telefone.
"""

from __future__ import annotations

import os
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
        CREATE TABLE IF NOT EXISTS exportacoes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL, origem TEXT NOT NULL, ref TEXT NOT NULL,
          descricao TEXT NOT NULL, contatos INTEGER NOT NULL, ip TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS acessos (
          id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL, tipo TEXT NOT NULL, ip TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS nao_contatar (
          telefone TEXT PRIMARY KEY, motivo TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chaves_api (
          id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, comeco TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
          criada_em TEXT NOT NULL, ultimo_uso TEXT NOT NULL DEFAULT '', revogada_em TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS oauth_clientes (
          client_id TEXT PRIMARY KEY, nome TEXT NOT NULL, dados TEXT NOT NULL, criado_em TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS oauth_tokens (
          hash TEXT PRIMARY KEY, tipo TEXT NOT NULL, client_id TEXT NOT NULL, conexao TEXT NOT NULL,
          escopos TEXT NOT NULL DEFAULT '', recurso TEXT NOT NULL DEFAULT '', expira INTEGER NOT NULL,
          criado_em TEXT NOT NULL, ultimo_uso TEXT NOT NULL DEFAULT '', revogado_em TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS oauth_tokens_conexao ON oauth_tokens (conexao);
        CREATE TABLE IF NOT EXISTS perfis (
          id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE COLLATE NOCASE, receita TEXT NOT NULL,
          criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ia_uso (
          id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL, funcao TEXT NOT NULL, modelo TEXT NOT NULL,
          entrada INTEGER NOT NULL DEFAULT 0, saida INTEGER NOT NULL DEFAULT 0,
          cache_lido INTEGER NOT NULL DEFAULT 0, cache_gravado INTEGER NOT NULL DEFAULT 0,
          custo_usd REAL NOT NULL DEFAULT 0, ref TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS ia_uso_quando ON ia_uso (quando);
        CREATE TABLE IF NOT EXISTS ia_avaliacoes (
          criterio TEXT NOT NULL, cnpj TEXT NOT NULL, nota INTEGER NOT NULL, motivo TEXT NOT NULL,
          modelo TEXT NOT NULL, avaliado_em TEXT NOT NULL,
          PRIMARY KEY (criterio, cnpj));
        """
    )
    try:  # coluna acrescentada depois da primeira versão
        con.execute("ALTER TABLE pasta_contatos ADD COLUMN resultado_em TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
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
                   (SELECT group_concat(DISTINCT cidade) FROM pasta_contatos WHERE pasta_id = p.id) AS cidades,
                   (SELECT count(*) FROM exportacoes WHERE origem = 'pasta' AND ref = CAST(p.id AS TEXT)) AS exportacoes,
                   (SELECT max(quando) FROM exportacoes WHERE origem = 'pasta' AND ref = CAST(p.id AS TEXT)) AS ultima_exportacao
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
    return novos, sum(1 for l in linhas if l.get("telefone")) - novos  # linha sem telefone não conta como "já estava"


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


def copiar_para(arquivo) -> None:
    """Cópia íntegra do arquivo das pastas, mesmo com o sistema em uso."""
    with closing(_con()) as origem, closing(sqlite3.connect(arquivo)) as copia:
        origem.backup(copia)
    os.chmod(arquivo, 0o600)


def totais() -> dict:
    """Quanto há guardado, para o resumo da cópia de segurança."""
    with closing(_con()) as con:
        n_pastas, n_contatos = con.execute("SELECT (SELECT count(*) FROM pastas), (SELECT count(*) FROM pasta_contatos)").fetchone()
    return {"pastas": n_pastas, "contatos_em_pastas": n_contatos}


def copia_de_seguranca(pasta_destino, manter: int = 14) -> str:
    """Copia o arquivo das pastas (único dado que não se refaz) e guarda as últimas `manter` cópias."""
    from pathlib import Path

    destino = Path(pasta_destino)
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / f"pastas-{datetime.now():%Y%m%d}.sqlite"
    copiar_para(arquivo)
    for velho in sorted(destino.glob("pastas-*.sqlite"))[:-manter]:
        velho.unlink()
    return str(arquivo)


# ---- Resultados dos disparos ----
# O que aconteceu com cada contato depois do envio. É o retorno que permite medir
# se a pontuação acerta: quem respondeu mais, os contatos fortes ou os médios?

RESULTADOS = [
    ("interessado", "Interessado"),
    ("respondeu", "Respondeu"),
    ("nao_quer", "Não tem interesse"),
    ("nao_e_o_dono", "Não é o dono"),
    ("nao_entregue", "Não entregue"),
    ("sem_resposta", "Sem resposta"),
]
_ROTULO = dict(RESULTADOS)
RESPONDEU = {"interessado", "respondeu", "nao_quer", "nao_e_o_dono"}
_FORCA = {"A": "Contato forte", "B": "Contato médio", "C": "Fraco"}
_WHATS = {"pessoal": "WhatsApp pessoal", "business": "WhatsApp comercial", "": "Não verificado"}


def marcar_resultado(
    pasta_id: int, resultado: str, telefones_e164: list[str] | None = None, todos_sem_resultado: bool = False
) -> tuple[int, int, list[str]]:
    """Grava o resultado. Devolve (marcados, números que não estão na pasta, telefones marcados)."""
    if resultado and resultado not in _ROTULO:
        raise PastaInvalida("Resultado desconhecido.")
    agora = _agora() if resultado else ""
    with closing(_con()) as con, con:
        na_pasta = {l[0] for l in con.execute("SELECT telefone FROM pasta_contatos WHERE pasta_id = ?", [pasta_id]).fetchall()}
        if todos_sem_resultado:
            alvo = [l[0] for l in con.execute(
                "SELECT telefone FROM pasta_contatos WHERE pasta_id = ? AND resultado = ''", [pasta_id]).fetchall()]
            fora = 0
        else:
            pedidos = list(dict.fromkeys(telefones_e164 or []))
            alvo = [t for t in pedidos if t in na_pasta]
            fora = len(pedidos) - len(alvo)
        con.executemany(
            "UPDATE pasta_contatos SET resultado = ?, resultado_em = ? WHERE pasta_id = ? AND telefone = ?",
            [(resultado, agora, pasta_id, t) for t in alvo],
        )
    return len(alvo), fora, alvo


def resumo_resultados(pasta_id: int | None = None) -> dict:
    """Totais e taxas de resposta, de uma pasta ou de todas, quebrados por força, oportunidade e WhatsApp."""
    with closing(_con()) as con:
        if pasta_id is None:
            linhas = con.execute("SELECT resultado, dados FROM pasta_contatos").fetchall()
        else:
            linhas = con.execute("SELECT resultado, dados FROM pasta_contatos WHERE pasta_id = ?", [pasta_id]).fetchall()
    por_resultado: dict[str, int] = {}
    grupos: dict[str, dict[str, list[int]]] = {"forca": {}, "oportunidade": {}, "whatsapp": {}}
    for l in linhas:
        if not l["resultado"]:
            continue
        por_resultado[l["resultado"]] = por_resultado.get(l["resultado"], 0) + 1
        d = json.loads(l["dados"])
        chaves = {
            "forca": d.get("faixa") or "",
            "oportunidade": d.get("oportunidade") or "",
            "whatsapp": d.get("tipo_whatsapp") or "",
        }
        for grupo, valor in chaves.items():
            g = grupos[grupo].setdefault(valor, [0, 0, 0])  # enviados, responderam, interessados
            g[0] += 1
            g[1] += l["resultado"] in RESPONDEU
            g[2] += l["resultado"] == "interessado"
    com_resultado = sum(por_resultado.values())
    responderam = sum(n for r, n in por_resultado.items() if r in RESPONDEU)

    def tabela(grupo: str, rotulos: dict[str, str] | None = None) -> list[dict]:
        return [
            {
                "valor": v, "rotulo": (rotulos or {}).get(v, v or "Não informado"), "enviados": e, "responderam": r,
                "interessados": i, "taxa": round(100 * r / e, 1) if e else None,
            }
            for v, (e, r, i) in sorted(grupos[grupo].items(), key=lambda x: -x[1][0])
        ]

    return {
        "total": len(linhas),
        "com_resultado": com_resultado,
        "responderam": responderam,
        "interessados": por_resultado.get("interessado", 0),
        "taxa_resposta": round(100 * responderam / com_resultado, 1) if com_resultado else None,
        "por_resultado": [{"valor": v, "rotulo": r, "n": por_resultado[v]} for v, r in RESULTADOS if v in por_resultado],
        "grupos": {"forca": tabela("forca", _FORCA), "oportunidade": tabela("oportunidade"), "whatsapp": tabela("whatsapp", _WHATS)},
    }


# ---- Histórico de exportações ----

def registrar_exportacao(origem: str, ref: str, descricao: str, contatos: int, ip: str = "") -> None:
    with closing(_con()) as con, con:
        con.execute(
            "INSERT INTO exportacoes (quando, origem, ref, descricao, contatos, ip) VALUES (?, ?, ?, ?, ?, ?)",
            [_agora(), origem, ref, descricao[:200], contatos, ip[:60]],
        )


def exportacoes_recentes(limite: int = 20) -> list[dict]:
    with closing(_con()) as con:
        return [
            dict(l)
            for l in con.execute(
                "SELECT quando, origem, descricao, contatos FROM exportacoes ORDER BY id DESC LIMIT ?", [limite]
            ).fetchall()
        ]


# ---- Registro de acessos ----

def registrar_acesso(tipo: str, ip: str = "") -> None:
    """tipo: "entrou", "senha errada", "bloqueado" ou "saiu". Guarda os últimos 2 mil registros."""
    with closing(_con()) as con, con:
        con.execute("INSERT INTO acessos (quando, tipo, ip) VALUES (?, ?, ?)", [_agora(), tipo, ip[:60]])
        con.execute("DELETE FROM acessos WHERE id <= (SELECT max(id) FROM acessos) - 2000")


def acessos_recentes(limite: int = 30) -> list[dict]:
    with closing(_con()) as con:
        return [dict(l) for l in con.execute("SELECT quando, tipo, ip FROM acessos ORDER BY id DESC LIMIT ?", [limite]).fetchall()]


# ---------- notas da IA (por critério e CNPJ) ----------

def notas_da_ia(chave: str, cnpjs: list[str] | None = None) -> dict[str, tuple[int, str]]:
    """Notas já dadas pela IA com este critério: CNPJ -> (nota, motivo)."""
    with closing(_con()) as con:
        if cnpjs is None:
            linhas = con.execute("SELECT cnpj, nota, motivo FROM ia_avaliacoes WHERE criterio = ?", [chave]).fetchall()
        else:
            linhas = []
            for i in range(0, len(cnpjs), 900):  # o SQLite limita o número de parâmetros
                parte = cnpjs[i : i + 900]
                linhas += con.execute(
                    f"SELECT cnpj, nota, motivo FROM ia_avaliacoes WHERE criterio = ? AND cnpj IN ({','.join('?' * len(parte))})",
                    [chave, *parte],
                ).fetchall()
    return {l["cnpj"]: (int(l["nota"]), l["motivo"]) for l in linhas}


def gravar_notas_da_ia(chave: str, notas: dict[str, tuple[int, str]], modelo: str) -> None:
    agora = _agora()
    with closing(_con()) as con, con:
        con.executemany(
            "INSERT OR REPLACE INTO ia_avaliacoes (criterio, cnpj, nota, motivo, modelo, avaliado_em) VALUES (?, ?, ?, ?, ?, ?)",
            [(chave, cnpj, int(n), m, modelo, agora) for cnpj, (n, m) in notas.items()],
        )
