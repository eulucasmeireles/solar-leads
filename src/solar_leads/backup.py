"""Cópia de segurança, criptografada, do que não dá para refazer.

Entram o arquivo das pastas (contatos guardados, resultados, exportações, acessos), os
WhatsApp já verificados, a descrição de cada lista e o `.env`. Ficam fora a base da
Receita e os arquivos das listas: esses se baixam ou se geram de novo.

O arquivo sai criptografado (AES-256, pelo gpg) com uma chave que fica fora do projeto.
Sem a chave ninguém lê a cópia; por isso ela precisa estar guardada também fora do servidor.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb

from . import cnpj_db, config, pastas

CHAVE = Path(os.getenv("LEADS_CHAVE_BACKUP") or Path.home() / ".config" / "solar-leads" / "chave-backup.txt")
LOCAL = config.DADOS / "backups"
REMOTO = config.DADOS / "backup-remoto"  # cópia local do repositório privado que recebe os arquivos
MANTER = 14
PREFIXO = "solar-leads-"
_RAIZ_NO_ARQUIVO = "solar-leads-backup"

LEIA_ME = """# Cópias de segurança do Solar Leads

Cada arquivo `solar-leads-AAAAMMDD-HHMMSS.tar.gz.gpg` é uma cópia criptografada dos dados do
sistema (pastas, WhatsApp verificados, descrição das listas e configurações). Ficam as 14 mais recentes.

Para abrir é preciso o arquivo da chave (`chave-backup.txt`), que NÃO está aqui.

## Restaurar

No servidor novo, com o código do sistema instalado e a chave em `~/.config/solar-leads/chave-backup.txt`:

    uv run leads backup restaurar solar-leads-AAAAMMDD-HHMMSS.tar.gz.gpg restaurado/

Dentro de `restaurado/` vêm `pastas.sqlite` (vai para `dados/`), `whatsapp_tipo.parquet`
(entra na base com `leads backup repor-whatsapp restaurado/`), `listas/` e `env` (vira o `.env`).
"""


def chave() -> Path:
    """Caminho da chave; cria uma nova, aleatória, se ainda não houver."""
    if not CHAVE.exists():
        CHAVE.parent.mkdir(parents=True, exist_ok=True)
        CHAVE.parent.chmod(0o700)
        CHAVE.write_text(secrets.token_urlsafe(48) + "\n")
        CHAVE.chmod(0o600)
    return CHAVE


def _gpg(*args: str) -> None:
    r = subprocess.run(
        ["gpg", "--batch", "--yes", "--quiet", "--no-tty", "--pinentry-mode", "loopback", "--passphrase-file", str(chave()), *args],
        capture_output=True, text=True,
    )
    if r.returncode:
        raise RuntimeError(f"O gpg recusou a operação: {r.stderr.strip()[-300:] or 'sem mensagem'}")


def criar(destino: Path | str | None = None) -> tuple[Path, dict]:
    """Monta a cópia criptografada em `destino` (por padrão `dados/backups`). Devolve (arquivo, resumo)."""
    destino = Path(destino or LOCAL)
    destino.mkdir(parents=True, exist_ok=True)
    agora = datetime.now()
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp) / _RAIZ_NO_ARQUIVO
        raiz.mkdir()
        pastas.copiar_para(raiz / "pastas.sqlite")
        resumo = {"criado_em": agora.isoformat(timespec="seconds"), **pastas.totais()}

        resumo["whatsapp_verificados"] = None
        if config.BANCO.exists():
            con = cnpj_db.conectar(somente_leitura=True)
            try:
                if cnpj_db.tem_tabela(con, "whatsapp_tipo"):
                    con.execute(f"COPY whatsapp_tipo TO '{raiz / 'whatsapp_tipo.parquet'}' (FORMAT parquet)")
                    resumo["whatsapp_verificados"] = con.execute("SELECT count(*) FROM whatsapp_tipo").fetchone()[0]
            finally:
                con.close()

        metas = sorted((config.DADOS / "listas").glob("*.json"))
        if metas:
            (raiz / "listas").mkdir()
            for meta in metas:
                shutil.copy2(meta, raiz / "listas" / meta.name)
        resumo["listas"] = len(metas)

        env = config.RAIZ / ".env"
        resumo["configuracoes"] = env.exists()
        if env.exists():
            shutil.copy2(env, raiz / "env")
        (raiz / "manifesto.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1))

        pacote = Path(tmp) / "pacote.tar.gz"
        with tarfile.open(pacote, "w:gz") as tar:
            tar.add(raiz, arcname=_RAIZ_NO_ARQUIVO)
        arquivo = destino / f"{PREFIXO}{agora:%Y%m%d-%H%M%S}.tar.gz.gpg"
        _gpg("--symmetric", "--cipher-algo", "AES256", "--output", str(arquivo), str(pacote))
    arquivo.chmod(0o600)
    for velho in sorted(destino.glob(f"{PREFIXO}*.tar.gz.gpg"))[:-MANTER]:
        velho.unlink()
    return arquivo, resumo


def restaurar(arquivo: Path | str, destino: Path | str) -> dict:
    """Abre uma cópia numa pasta nova (nunca por cima dos dados em uso). Devolve o resumo gravado nela."""
    arquivo, destino = Path(arquivo), Path(destino)
    if not arquivo.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {arquivo}")
    if destino.exists() and any(destino.iterdir()):
        raise FileExistsError(f"A pasta {destino} já tem arquivos. Escolha uma pasta nova.")
    destino.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        pacote = Path(tmp) / "pacote.tar.gz"
        _gpg("--decrypt", "--output", str(pacote), str(arquivo))
        with tarfile.open(pacote) as tar:
            tar.extractall(tmp, filter="data")
        for item in (Path(tmp) / _RAIZ_NO_ARQUIVO).iterdir():
            shutil.move(str(item), destino / item.name)
    for item in destino.rglob("*"):
        item.chmod(0o700 if item.is_dir() else 0o600)
    return json.loads((destino / "manifesto.json").read_text())


def repor_whatsapp(pasta: Path | str) -> int:
    """Devolve à base os WhatsApp verificados de uma cópia restaurada, sem apagar os que já estão lá."""
    parquet = Path(pasta) / "whatsapp_tipo.parquet"
    if not parquet.exists():
        raise FileNotFoundError(f"Não há whatsapp_tipo.parquet em {pasta}")
    con = cnpj_db.conectar()
    try:
        con.execute(f"CREATE TABLE IF NOT EXISTS whatsapp_tipo AS SELECT * FROM read_parquet('{parquet}') LIMIT 0")
        antes = con.execute("SELECT count(*) FROM whatsapp_tipo").fetchone()[0]
        con.execute(
            f"INSERT INTO whatsapp_tipo SELECT * FROM read_parquet('{parquet}') p "
            "WHERE NOT EXISTS (SELECT 1 FROM whatsapp_tipo w WHERE w.tel = p.tel)"
        )
        return con.execute("SELECT count(*) FROM whatsapp_tipo").fetchone()[0] - antes
    finally:
        con.close()


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=REMOTO, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {args[0]} falhou: {r.stderr.strip()[-300:]}")
    return r.stdout.strip()


def conectar(url: str, autor: str = "", email: str = "") -> None:
    """Liga a pasta de envio a um repositório git privado (uma vez só)."""
    REMOTO.mkdir(parents=True, exist_ok=True)
    if not (REMOTO / ".git").exists():
        _git("init", "--quiet", "--initial-branch", "main")
    if "origin" in _git("remote").split():
        _git("remote", "set-url", "origin", url)
    else:
        _git("remote", "add", "origin", url)
    if autor:
        _git("config", "user.name", autor)
    if email:
        _git("config", "user.email", email)
    (REMOTO / "LEIA-ME.md").write_text(LEIA_ME)


def conectado() -> bool:
    return (REMOTO / ".git").exists()


def enviar(arquivo: Path | str) -> int:
    """Manda a cópia para o repositório privado. Devolve quantas cópias ficaram lá.

    O repositório fica sempre com um commit só (as últimas cópias), para não crescer sem fim
    com arquivos antigos guardados no histórico.
    """
    if not conectado():
        raise RuntimeError("O envio ainda não foi configurado: rode `leads backup conectar <endereço do repositório>`.")
    arquivo = Path(arquivo)
    shutil.copy2(arquivo, REMOTO / arquivo.name)
    for velho in sorted(REMOTO.glob(f"{PREFIXO}*.tar.gz.gpg"))[:-MANTER]:
        velho.unlink()
    _git("checkout", "--quiet", "--orphan", "envio")
    _git("add", "--all")
    _git("commit", "--quiet", "--message", f"Cópia de segurança de {datetime.now():%d/%m/%Y %H:%M}")
    _git("branch", "--move", "--force", "main")
    _git("push", "--quiet", "--force", "origin", "main")
    _git("reflog", "expire", "--expire=now", "--all")
    _git("gc", "--quiet", "--prune=now")
    return len(list(REMOTO.glob(f"{PREFIXO}*.tar.gz.gpg")))
