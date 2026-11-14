"""Verificação de WhatsApp pelo Evolution GO (API não oficial, em localhost).

Use um número separado só para validar: a consulta em massa pode levar ao
bloqueio do número conectado, então nunca conecte aqui o número da API oficial.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import duckdb
import httpx

from . import config
from .telefone import so_digitos

LOTE = 40
PAUSA_S = 3.0


class WhatsAppIndisponivel(RuntimeError):
    pass


def _token() -> str:
    return os.getenv("EVOGO_TOKEN", "")


def _req(metodo: str, caminho: str, *, admin: bool = False, **kw) -> dict:
    chave = config.EVOGO_API_KEY if admin else _token()
    try:
        r = httpx.request(metodo, f"{config.EVOGO_URL}{caminho}", headers={"apikey": chave}, timeout=60, **kw)
    except httpx.HTTPError as e:
        raise WhatsAppIndisponivel(f"Evolution GO não respondeu: {e}") from e
    if r.status_code == 503 and "LICENSE_REQUIRED" in r.text:
        raise WhatsAppIndisponivel(
            "Evolution GO ainda não foi ativado. Abra http://127.0.0.1:4000/manager/login e conclua o registro da licença."
        )
    if r.status_code >= 400:
        raise WhatsAppIndisponivel(f"Evolution GO respondeu {r.status_code}: {r.text[:300]}")
    return r.json()


def criar_instancia() -> dict:
    return _req("POST", "/instance/create", admin=True, json={"name": config.EVOGO_INSTANCIA, "token": _token()})


def conectar() -> dict:
    return _req("POST", "/instance/connect", json={"webhookUrl": "", "subscribe": []})


def codigo_pareamento(numero_com_ddi: str) -> str:
    dados = _req("POST", "/instance/pair", json={"phone": so_digitos(numero_com_ddi)})
    return str((dados.get("data") or {}).get("code") or dados)


def salvar_qr(destino: Path) -> Path:
    dados = _req("GET", "/instance/qr").get("data") or {}
    # A imagem vem em "qrcode"; "code" é o texto bruto do QR.
    imagem = next((str(dados.get(k)) for k in ("qrcode", "code") if "base64," in str(dados.get(k) or "")), "")
    if "base64," not in imagem:
        raise WhatsAppIndisponivel(f"QR code indisponível: {dados}")
    destino.write_bytes(base64.b64decode(imagem.split("base64,", 1)[1]))
    return destino


def status() -> dict:
    return _req("GET", "/instance/status").get("data") or {}


def conectado() -> bool:
    st = {k.lower(): v for k, v in status().items()}
    return bool(st.get("connected")) and bool(st.get("loggedin"))


def _garantir_cache(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS whatsapp_tipo (tel VARCHAR PRIMARY KEY, tem BOOLEAN, business BOOLEAN, "
        "nome_business VARCHAR, verificado_em TIMESTAMP)"
    )


def do_cache(con: duckdb.DuckDBPyConnection, nacionais: list[str], validade_dias: int = 30) -> dict[str, dict]:
    """Só o que já foi verificado dentro da validade; não consulta o WhatsApp."""
    _garantir_cache(con)
    nacionais = sorted(set(nacionais))
    if not nacionais:
        return {}
    linhas = con.execute(
        f"SELECT tel, tem, business, nome_business FROM whatsapp_tipo "
        f"WHERE verificado_em > now() - INTERVAL {int(validade_dias)} DAY AND tel IN (SELECT unnest(?))",
        [nacionais],
    ).fetchall()
    return {tel: {"tem": tem, "business": business, "nome_business": nome or ""} for tel, tem, business, nome in linhas}


def _consultar(nacionais: list[str]) -> dict[str, dict]:
    """Uma chamada ao /user/check; devolve {número nacional consultado: resposta}."""
    dados = _req("POST", "/user/check", json={"number": ["55" + n for n in nacionais], "formatJid": False})
    usuarios = (dados.get("data") or {}).get("Users") or []
    return {so_digitos(str(u.get("Query")))[2:]: u for u in usuarios}


def checar(
    con: duckdb.DuckDBPyConnection,
    nacionais: list[str],
    validade_dias: int = 30,
    lote: int = LOTE,
    pausa_s: float = PAUSA_S,
    progresso=None,
) -> dict[str, dict]:
    """Para cada número nacional (DDD+número): {tem, business, nome_business}. Usa cache.

    Conta comercial é a que devolve nome verificado; conta pessoal não expõe nome
    nenhum antes de a pessoa responder. Contas antigas podem estar registradas sem
    o nono dígito, então quem não aparece é consultado de novo sem ele.
    """
    resultado = do_cache(con, nacionais, validade_dias)
    faltam = [n for n in sorted(set(nacionais)) if n not in resultado]
    if faltam and not conectado():
        raise WhatsAppIndisponivel("A instância do Evolution GO não está conectada a um WhatsApp.")

    for i in range(0, len(faltam), lote):
        grupo = faltam[i : i + lote]
        respostas = _consultar(grupo)
        sem_nove = {n[:2] + n[3:]: n for n in grupo if len(n) == 11 and not (respostas.get(n) or {}).get("IsInWhatsapp")}
        if sem_nove:
            time.sleep(pausa_s)
            for curto, u in _consultar(list(sem_nove)).items():
                if u.get("IsInWhatsapp") and curto in sem_nove:
                    respostas[sem_nove[curto]] = u
        for n in grupo:
            u = respostas.get(n)
            if u is None:
                continue
            nome = str(u.get("VerifiedName") or "").strip()
            info = {"tem": bool(u.get("IsInWhatsapp")), "business": bool(nome), "nome_business": nome}
            resultado[n] = info
            con.execute(
                "INSERT OR REPLACE INTO whatsapp_tipo VALUES (?, ?, ?, ?, now())",
                [n, info["tem"], info["business"], info["nome_business"]],
            )
        if progresso:
            progresso(min(i + lote, len(faltam)), len(faltam))
        if i + lote < len(faltam):
            time.sleep(pausa_s)
    return resultado


def verificar_devagar(
    fila: list[str],
    *,
    parar=None,
    avisar=print,
    lote_min: int = 3,
    lote_max: int = 6,
    pausa_min: float = 30,
    pausa_max: float = 60,
    limite_dia: int = 3000,
    das: int = 8,
    ate: int = 20,
) -> str:
    """Verifica a fila aos poucos, em ritmo de uso humano. Devolve o motivo de ter parado.

    Abre a base só pelo instante de cada consulta, para não travar quem gera listas.
    `parar` é um threading.Event opcional; `avisar(texto)` recebe o andamento.
    """
    import random
    from datetime import datetime

    from . import cnpj_db

    def dormir(segundos: float) -> bool:
        """True se pediram para parar durante a espera."""
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            if parar is not None and parar.is_set():
                return True
            time.sleep(min(1.0, max(0.0, fim - time.monotonic())))
        return False

    con = cnpj_db.conectar()
    feitos = set(do_cache(con, fila))
    con.close()
    fila = [n for n in dict.fromkeys(fila) if n not in feitos]
    avisar(f"Fila com {len(fila)} números ({len(feitos)} já conferidos).", len(fila), None)
    while fila:
        if parar is not None and parar.is_set():
            return "Verificação interrompida."
        if not das <= datetime.now().hour < ate:
            avisar(f"Fora do horário ({das}h às {ate}h); aguardando.", len(fila), None)
            if dormir(600):
                return "Verificação interrompida."
            continue
        con = cnpj_db.conectar()
        try:
            hoje = con.execute("SELECT count(*) FROM whatsapp_tipo WHERE verificado_em::DATE = current_date").fetchone()[0]
            if hoje >= limite_dia:
                con.close()
                avisar(f"Limite de {limite_dia} verificações do dia atingido; continua amanhã.", len(fila), hoje)
                if dormir(3600):
                    return "Verificação interrompida."
                continue
            grupo = fila[: random.randint(lote_min, lote_max)]
            fila = fila[len(grupo) :]
            checar(con, grupo, lote=len(grupo), pausa_s=random.uniform(4, 9))
        except WhatsAppIndisponivel as e:
            return f"Parou: {e}"
        finally:
            con.close()
        avisar("Verificando.", len(fila), hoje + len(grupo))
        if dormir(random.uniform(pausa_min, pausa_max)):
            return "Verificação interrompida."
    return "Fila concluída."


def qr_code() -> str:
    """Imagem do QR code (data URL). Abre a conexão uma única vez: chamadas a mais
    criam ciclos extras de QR no Evolution GO, e o ciclo que sobra derruba a sessão."""
    estado = {k.lower(): v for k, v in status().items()}
    if estado.get("loggedin"):
        raise WhatsAppIndisponivel("Já existe um WhatsApp conectado.")
    if not estado.get("connected"):
        conectar()
        time.sleep(3)
    dados = _req("GET", "/instance/qr").get("data") or {}
    imagem = next((str(dados.get(k)) for k in ("qrcode", "code") if "base64," in str(dados.get(k) or "")), "")
    if not imagem:
        raise WhatsAppIndisponivel("O QR code ainda não ficou pronto. Tente de novo em alguns segundos.")
    return imagem
