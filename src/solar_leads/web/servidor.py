"""Sistema web: gera listas por cidade, filtra e exporta para o GHL.

Sobe só em localhost. As listas ficam em dados/listas/<id>.parquet com um <id>.json
de metadados; as consultas usam conexões DuckDB em memória, sem travar a base.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import aneel, avaliacao_ia, busca_ia, chaves, cnpj_db, config, consulta, enriquecer, ia, pastas, perfis, pipeline, relatorio, whatsapp
from ..telefone import e164, normalizar, so_digitos
from . import mcp_garimpo

AQUI = Path(__file__).parent
LISTAS = config.DADOS / "listas"
MUNICIPIOS = config.DADOS / "municipios_uf.parquet"
LISTAS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Garimpo", docs_url=None, redoc_url=None)


class Estaticos(StaticFiles):
    """Serve só os arquivos da interface. O código Python mora na mesma pasta e nunca sai por aqui."""

    PERMITIDAS = {".js", ".css", ".woff2", ".png", ".svg", ".ico"}

    async def get_response(self, path: str, scope):
        if Path(path).suffix.lower() not in self.PERMITIDAS:
            raise HTTPException(404)
        resposta = await super().get_response(path, scope)
        # O navegador confere a cada visita se o arquivo mudou; evita misturar versões após uma atualização.
        resposta.headers["Cache-Control"] = "no-cache"
        return resposta


app.mount("/static", Estaticos(directory=AQUI), name="static")

# Acima disso a cidade inteira não cabe bem na memória do servidor; pede-se um recorte por atividade.
# Medido: Uberlândia (365 mil estabelecimentos) leva 55 s e chega a 6 GB.
LIMITE_SEM_ATIVIDADE = 700_000
_fila: queue.Queue = queue.Queue()  # pedidos de lista, atendidos um por vez
_cache: dict[str, tuple[float, object]] = {}
_verificacao = {
    "thread": None, "parar": threading.Event(), "lista_id": None, "feitos": 0, "total": 0, "mensagem": "", "resumo": None,
}
_meta_trava = threading.Lock()


def _em_cache(chave: str, validade_s: float, calcular):
    quando, valor = _cache.get(chave, (0.0, None))
    if valor is None or time.monotonic() - quando > validade_s:
        valor = calcular()
        _cache[chave] = (time.monotonic(), valor)
    return valor


# ---------- acesso com senha ----------
# Sem LEADS_SENHA_HASH o sistema fica aberto (uso só por túnel). Com ele, tudo exige sessão,
# menos a tela de login e as fontes. A sessão é um cookie assinado, válido por 30 dias.
SENHA_HASH = os.getenv("LEADS_SENHA_HASH", "")
SEGREDO = os.getenv("LEADS_SEGREDO", "")
if (not SENHA_HASH or len(SEGREDO) < 32) and os.getenv("LEADS_SEM_SENHA") != "1":
    # Sem senha ou sem segredo o sistema ficaria aberto (ou com sessão forjável). Só sobe assim
    # quando pedido de propósito, para uso local por túnel.
    raise RuntimeError(
        "Falta LEADS_SENHA_HASH ou LEADS_SEGREDO no .env. Rode `leads senha`, ou defina LEADS_SEM_SENHA=1 para uso só local."
    )
COOKIE = "sl_sessao"
VALIDADE_S = 30 * 24 * 3600
MAX_ERROS, BLOQUEIO_S = 5, 15 * 60
_tentativas: dict[str, list[float]] = {}  # ip -> [erros seguidos, bloqueado até]


def gerar_hash(senha: str) -> str:
    sal = os.urandom(16)
    return sal.hex() + ":" + hashlib.scrypt(senha.encode(), salt=sal, n=2**14, r=8, p=1, dklen=32).hex()


def _senha_confere(senha: str) -> bool:
    try:
        sal, esperado = SENHA_HASH.split(":")
        calculado = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1, dklen=32).hex()
    except ValueError:
        return False
    return hmac.compare_digest(calculado, esperado)


_EPOCA_ARQ = config.DADOS / "sessao-epoca.txt"
_epoca_atual = ""


def _epoca(nova: bool = False) -> str:
    """Marca que entra na assinatura das sessões. Trocá-la (ao sair) invalida todas as sessões abertas."""
    global _epoca_atual
    if nova or not (_epoca_atual or _EPOCA_ARQ.exists()):
        _EPOCA_ARQ.write_text(os.urandom(16).hex())
        _EPOCA_ARQ.chmod(0o600)
        _epoca_atual = ""
    if not _epoca_atual:
        _epoca_atual = _EPOCA_ARQ.read_text().strip()
    return _epoca_atual


def _assinar(validade: int, marca: str) -> str:
    return hmac.new(SEGREDO.encode(), f"{validade}.{marca}.{_epoca()}".encode(), "sha256").hexdigest()


def _sessao_valida(cookie: str | None) -> bool:
    try:
        validade, marca, assinatura = (cookie or "").split(".")
        return int(validade) > time.time() and hmac.compare_digest(assinatura, _assinar(int(validade), marca))
    except ValueError:
        return False


def _ip(request: Request) -> str:
    # O Caddy acrescenta o endereço real no fim do X-Forwarded-For.
    encaminhado = request.headers.get("x-forwarded-for", "")
    return encaminhado.split(",")[-1].strip() or (request.client.host if request.client else "?")


API_MAX_ERROS, API_POR_MINUTO = 20, 300
_api_erros: dict[str, list[float]] = {}  # ip -> [chaves erradas seguidas, bloqueado até]
_api_chamadas: dict[int, list[float]] = {}  # id da chave -> horários das chamadas do último minuto


def _conferir_chave(request: Request) -> JSONResponse | None:
    """None se a requisição traz uma chave de API válida e dentro do limite; senão, a resposta de recusa."""
    ip, agora = _ip(request), time.time()
    erros, ate = _api_erros.get(ip, [0, 0.0])
    if agora < ate:
        return JSONResponse(
            {"detail": "Muitas tentativas com chave inválida. Tente de novo mais tarde."},
            status_code=429, headers={"Retry-After": str(int(ate - agora) + 1)},
        )
    cabecalho = request.headers.get("authorization", "")
    chave = cabecalho[7:].strip() if cabecalho[:7].lower() == "bearer " else request.headers.get("x-api-key", "").strip()
    registro = chaves.conferir(chave)
    if not registro:
        erros += 1
        _api_erros[ip] = [0, agora + BLOQUEIO_S] if erros >= API_MAX_ERROS else [erros, 0.0]
        if len(_api_erros) > 5000:
            _api_erros.clear()
        return JSONResponse(
            {"detail": "Chave de API ausente ou inválida. Envie no cabeçalho: Authorization: Bearer <sua chave>."},
            status_code=401, headers={"WWW-Authenticate": "Bearer"},
        )
    _api_erros.pop(ip, None)
    recentes = [t for t in _api_chamadas.get(registro["id"], []) if agora - t < 60]
    if len(recentes) >= API_POR_MINUTO:
        return JSONResponse(
            {"detail": f"Limite de {API_POR_MINUTO} chamadas por minuto atingido. Espere um pouco."},
            status_code=429, headers={"Retry-After": str(int(60 - (agora - recentes[0])) + 1)},
        )
    recentes.append(agora)
    _api_chamadas[registro["id"]] = recentes
    request.state.chave = registro
    return None


@app.middleware("http")
async def exigir_sessao(request: Request, call_next):
    caminho = request.scope.get("path", "")
    # Caminho com ".." ou barra invertida não é de ninguém legítimo: sem isso, /static/fonts/../x
    # passava como "fonte" e era servido sem sessão.
    if ".." in caminho.split("/") or "\\" in caminho or "%" in caminho:
        return JSONResponse({"detail": "Endereço inválido."}, status_code=404)
    fonte = caminho.startswith("/static/fonts/") and caminho.endswith(".woff2") and caminho.count("/") == 3
    livre = caminho in ("/login", "/api/login", "/api/sessao") or fonte
    # A API das automações (/api/v1) e o servidor MCP (/mcp) aceitam uma chave no lugar da sessão.
    # A chave não abre mais nada: nem a página, nem as rotas internas, nem a criação de outras chaves.
    automacao = caminho.startswith("/api/v1/") or caminho == mcp_garimpo.CAMINHO
    if SENHA_HASH and automacao and not _sessao_valida(request.cookies.get(COOKIE)):
        recusa = _conferir_chave(request)
        if recusa is not None:
            return recusa
        resposta = await call_next(request)
        resposta.headers["Cache-Control"] = "no-store"
        return resposta
    if not SENHA_HASH or livre or _sessao_valida(request.cookies.get(COOKIE)):
        resposta = await call_next(request)
        if caminho.startswith("/api/"):
            resposta.headers["Cache-Control"] = "no-store"  # respostas com dados pessoais não ficam em cache
        return resposta
    if caminho.startswith("/api/"):
        return JSONResponse({"detail": "Sessão expirada. Entre de novo."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


class Entrada(BaseModel):
    senha: str


@app.get("/login")
def tela_de_login() -> FileResponse:
    return FileResponse(AQUI / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/api/sessao")
def sessao(request: Request) -> dict:
    return {"protegido": bool(SENHA_HASH), "dentro": not SENHA_HASH or _sessao_valida(request.cookies.get(COOKIE))}


@app.post("/api/login")
def entrar(entrada: Entrada, request: Request) -> JSONResponse:
    ip = _ip(request)
    erros, ate = _tentativas.get(ip, [0, 0.0])
    if time.time() < ate:
        minutos = int((ate - time.time()) // 60) + 1
        raise HTTPException(429, f"Muitas tentativas. Tente de novo em {minutos} min.")
    if not SENHA_HASH or not _senha_confere(entrada.senha):
        time.sleep(0.8)  # atrasa quem tenta adivinhar
        erros += 1
        bloqueou = erros >= MAX_ERROS
        _tentativas[ip] = [0, time.time() + BLOQUEIO_S] if bloqueou else [erros, 0.0]
        if len(_tentativas) > 5000:  # não deixa a tabela de tentativas crescer sem fim
            for velho in [k for k, v in _tentativas.items() if v[1] < time.time() and v[0] == 0]:
                _tentativas.pop(velho, None)
        pastas.registrar_acesso("bloqueado" if bloqueou else "senha errada", ip)
        raise HTTPException(401, "Senha incorreta.")
    _tentativas.pop(ip, None)
    pastas.registrar_acesso("entrou", ip)
    validade, marca = int(time.time()) + VALIDADE_S, os.urandom(8).hex()
    resposta = JSONResponse({"ok": True})
    resposta.set_cookie(
        COOKIE, f"{validade}.{marca}.{_assinar(validade, marca)}", max_age=VALIDADE_S,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return resposta


@app.get("/api/acessos")
def acessos() -> list[dict]:
    return pastas.acessos_recentes()


@app.post("/api/logout")
def sair(request: Request) -> JSONResponse:
    pastas.registrar_acesso("saiu", _ip(request))
    _epoca(nova=True)  # a senha é única: sair encerra a sessão em todos os aparelhos
    resposta = JSONResponse({"ok": True})
    resposta.delete_cookie(COOKIE, path="/")
    return resposta


# ---------- metadados das listas ----------

def _meta_caminho(id_: str) -> Path:
    if not id_.replace("-", "").isalnum():
        raise HTTPException(404, "Lista não encontrada.")
    return LISTAS / f"{id_}.json"


def _ler_meta(id_: str) -> dict:
    caminho = _meta_caminho(id_)
    if not caminho.exists():
        raise HTTPException(404, "Lista não encontrada.")
    return json.loads(caminho.read_text())


def _gravar_meta(meta: dict) -> None:
    caminho = _meta_caminho(meta["id"])
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    temporario.replace(caminho)


_migrando = threading.Lock()


def _mexer_meta(id_: str, **campos) -> dict:
    """Muda só alguns campos da lista, relendo do disco: não desfaz o que outra tarefa gravou no meio."""
    with _meta_trava:
        meta = _ler_meta(id_)
        meta.update(campos)
        _gravar_meta(meta)
        return meta


def _parquet(id_: str) -> Path:
    meta = _ler_meta(id_)
    if meta["status"] != "pronta":
        raise HTTPException(409, "A lista ainda não está pronta.")
    arquivo = LISTAS / f"{id_}.parquet"
    if not arquivo.exists():
        raise HTTPException(404, "Lista não encontrada.")
    if meta.get("versao", 1) < pipeline.VERSAO:
        _migrar(meta, arquivo)
    return arquivo


def _migrar(meta: dict, arquivo: Path) -> None:
    """Lista gravada numa versão antiga: leva ao formato atual uma vez, sem mudar a ordem (ver `pipeline.migrar`)."""
    with _migrando:
        meta = _ler_meta(meta["id"])
        if meta.get("versao", 1) >= pipeline.VERSAO:
            return
        perfil = perfis.do_meta(meta)
        if pipeline.migrar(arquivo, _publico(meta), perfis.pesos(perfil, meta.get("atividades"))) and meta.get("resumo"):
            _contar_para_o_cartao(meta)  # "com e-mail" muda quando o e-mail repetido sai
        meta.update(versao=pipeline.VERSAO, perfil=perfil)
        _gravar_meta(meta)


@app.exception_handler(duckdb.IOException)
async def _dados_indisponiveis(request: Request, erro: duckdb.IOException) -> JSONResponse:
    # Lista apagada no meio de uma consulta, ou base ocupada: responde com clareza em vez de erro 500.
    return JSONResponse({"detail": "Os dados não estão disponíveis agora. Atualize a página e tente de novo."}, status_code=503)


# Uma geração interrompida por reinício do servidor não volta sozinha.
for _arq in LISTAS.glob("*.json"):
    _m = json.loads(_arq.read_text())
    if _m.get("status") in ("gerando", "na_fila"):
        _m.update(status="erro", erro="A geração foi interrompida. Gere a lista de novo.")
        _gravar_meta(_m)
    elif _m.get("atualizando"):  # atualização interrompida: a lista de antes continua valendo
        _m.update(atualizando=None, atualizando_etapa=None)
        _gravar_meta(_m)
for _arq in LISTAS.glob("*.novo.parquet"):
    _arq.unlink(missing_ok=True)


# ---------- dados de apoio ----------

def _municipios() -> Path:
    """Tabela pequena (UF, município, nº de empresas), montada uma vez a partir da base."""
    if not MUNICIPIOS.exists():
        con = duckdb.connect()
        # A Receita grava os nomes sem acento; a ANEEL traz a grafia correta ("Uberlândia").
        acentos = "SELECT NULL AS uf, NULL AS chave, NULL AS acentuado WHERE false"
        if aneel.ARQUIVO.exists():
            acentos = (
                "SELECT SigUF AS uf, regexp_replace(upper(strip_accents(NomMunicipio)), '[^A-Z0-9]', '', 'g') AS chave, "
                f"upper(any_value(NomMunicipio)) AS acentuado FROM read_parquet('{aneel.ARQUIVO}') GROUP BY 1, 2"
            )
        con.execute(
            f"""
            COPY (
              SELECT r.*, coalesce(a.acentuado, r.nome) AS exibicao
              FROM (
                SELECT e.uf, e.municipio AS codigo, m.descricao AS nome, count(*) AS empresas
                FROM read_parquet('{config.CNPJ_PARQUET}/estabelecimentos_*.parquet') e
                JOIN read_parquet('{config.CNPJ_PARQUET}/municipios_*.parquet') m ON m.codigo = e.municipio
                WHERE length(e.uf) = 2 AND e.uf <> 'EX'
                GROUP BY ALL
              ) r
              LEFT JOIN ({acentos}) a
                ON a.uf = r.uf AND a.chave = regexp_replace(strip_accents(r.nome), '[^A-Z0-9]', '', 'g')
            ) TO '{MUNICIPIOS}' (FORMAT parquet)
            """
        )
        con.close()
    return MUNICIPIOS


@app.get("/api/ufs")
def ufs() -> list[str]:
    con = duckdb.connect()
    return [l[0] for l in con.execute(f"SELECT DISTINCT uf FROM read_parquet('{_municipios()}') ORDER BY 1").fetchall()]


@app.get("/api/municipios")
def municipios(uf: str) -> list[dict]:
    con = duckdb.connect()
    linhas = con.execute(
        f"SELECT codigo, exibicao, empresas FROM read_parquet('{_municipios()}') WHERE uf = ? ORDER BY nome", [uf.upper()]
    ).fetchall()
    return [{"codigo": c, "nome": n, "empresas": e} for c, n, e in linhas]


@app.get("/api/cidades")
def cidades(q: str = "") -> list[dict]:
    """Busca nacional de município pelo nome (começo do nome primeiro, depois qualquer palavra)."""
    q = q.strip()
    if len(q) < 2:
        return []
    con = duckdb.connect()
    linhas = con.execute(
        f"""
        SELECT codigo, exibicao, uf, empresas FROM read_parquet('{_municipios()}')
        WHERE strip_accents(nome) LIKE strip_accents(upper(?)) || '%'
           OR strip_accents(nome) LIKE '% ' || strip_accents(upper(?)) || '%'
        ORDER BY strip_accents(nome) LIKE strip_accents(upper(?)) || '%' DESC, empresas DESC LIMIT 20
        """,
        [q, q, q],
    ).fetchall()
    return [{"codigo": c, "nome": n, "uf": u, "empresas": e, "grande": e > LIMITE_SEM_ATIVIDADE} for c, n, u, e in linhas]


def _totais_whatsapp() -> dict:
    try:
        con = cnpj_db.conectar(tentativas=2)  # se a base estiver ocupada, não segura a tela
    except duckdb.IOException:
        return _cache.get("totais-whatsapp", (0, {"verificados": 0, "com_whatsapp": 0, "pessoais": 0, "comerciais": 0}))[1]
    try:
        whatsapp._garantir_cache(con)
        v, c, pss, com = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE tem), count(*) FILTER (WHERE tem AND NOT business), "
            "count(*) FILTER (WHERE tem AND business) FROM whatsapp_tipo"
        ).fetchone()
    finally:
        con.close()
    return {"verificados": v, "com_whatsapp": c, "pessoais": pss, "comerciais": com}


def _resumo_aneel() -> dict | None:
    if not aneel.ARQUIVO.exists():
        return None
    con = duckdb.connect()
    d, n, e = con.execute(
        f"SELECT max(DatGeracaoConjuntoDados), count(*), count(DISTINCT NumCPFCNPJ) FILTER (WHERE SigTipoConsumidor = 'PJ') "
        f"FROM read_parquet('{aneel.ARQUIVO}')"
    ).fetchone()
    return {"atualizado_em": str(d), "sistemas": n, "empresas_com_solar": e}


@app.get("/api/base")
def base() -> dict:
    carga = {}
    arquivo = config.CNPJ_PARQUET / "_carga.json"
    if arquivo.exists():
        carga = json.loads(arquivo.read_text())
    linhas = carga.get("linhas", {})
    n_mun = duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{_municipios()}')").fetchone()[0]
    return {
        "receita": {
            "mes": carga.get("mes"), "estabelecimentos": linhas.get("estabelecimentos"),
            "empresas": linhas.get("empresas"), "socios": linhas.get("socios"),
        },
        "aneel": _em_cache(f"aneel-{aneel.ARQUIVO.stat().st_mtime if aneel.ARQUIVO.exists() else 0}", 86400, _resumo_aneel),
        "whatsapp": _em_cache("totais-whatsapp", 20, _totais_whatsapp),
        "municipios": n_mun,
        "listas": len(list(LISTAS.glob("*.json"))),
    }


@app.get("/api/atividades")
def atividades(q: str = "") -> list[dict]:
    q = q.strip()
    if len(q) < 3:
        return []
    con = duckdb.connect()
    linhas = con.execute(
        f"""
        SELECT trim(codigo), descricao FROM read_parquet('{config.CNPJ_PARQUET}/cnaes_*.parquet')
        WHERE strip_accents(lower(descricao)) LIKE '%' || strip_accents(lower(?)) || '%' OR trim(codigo) LIKE ? || '%'
        ORDER BY descricao LIMIT 50
        """,
        [q, q],
    ).fetchall()
    return [{"codigo": c, "descricao": d} for c, d in linhas]


@app.get("/api/campos")
def campos() -> list[dict]:
    # so_dono: campo de energia solar, que a tela esconde quando o público da lista é "empresa".
    return [
        {"chave": c, "rotulo": r, "grupo": g, "tipo": t, "so_dono": c in pipeline.SO_DO_DONO}
        for c, _, r, g, t in pipeline.CAMPOS
    ]


def _estado_whatsapp() -> dict:
    try:
        st = {k.lower(): v for k, v in whatsapp.status().items()}
        ok = bool(st.get("connected")) and bool(st.get("loggedin"))
        return {"disponivel": ok, "motivo": "" if ok else "Nenhum WhatsApp conectado ao validador.", "conta": str(st.get("name") or "")}
    except whatsapp.WhatsAppIndisponivel as e:
        return {"disponivel": False, "motivo": str(e), "conta": ""}


def _servico_whatsapp() -> dict:
    """O serviço pago que verifica os números (CheckLeaked): se tem chave e quanto saldo resta."""
    configurado = bool(os.getenv("CHECKLEAKED_KEY", "").strip())
    return {"nome": "CheckLeaked", "configurado": configurado, "saldo": consulta.saldo_checkleaked() if configurado else None}


def _verificando() -> bool:
    t = _verificacao["thread"]
    return bool(t and t.is_alive())


@app.get("/api/whatsapp")
def estado_whatsapp() -> dict:
    return {
        "servico": _em_cache("servico-whatsapp", 60 if not _verificando() else 15, _servico_whatsapp),
        "totais": _em_cache("totais-whatsapp", 20, _totais_whatsapp),
        "verificacao": {
            "rodando": _verificando(),
            "lista_id": _verificacao["lista_id"],
            "feitos": _verificacao["feitos"],
            "total": _verificacao["total"],
            "mensagem": _verificacao["mensagem"],
            "resumo": _verificacao["resumo"],
        },
    }


@app.get("/api/whatsapp/conexao")
def conexao_whatsapp() -> dict:
    """O número próprio ligado ao Evolution GO (verificação antiga, só pela linha de comando)."""
    return _em_cache("estado-whatsapp", 3, _estado_whatsapp)


@app.post("/api/whatsapp/qr")
def qr_whatsapp() -> dict:
    try:
        return {"qr": whatsapp.qr_code()}
    except whatsapp.WhatsAppIndisponivel as e:
        raise HTTPException(409, str(e)) from e


MAX_VERIFICAR = 5000  # números por pedido de verificação


def _numeros_da_lista(meta: dict, quantidade: int) -> list[str]:
    """Os próximos números da lista ainda sem WhatsApp verificado, na ordem da tela (nota da IA primeiro, se houver).

    Devolve números nacionais (DDD + número), um por contato de disparo.
    """
    cte, params = _consulta(_parquet(meta["id"]), {}, {}, 0, 1, "")
    ordem = "nota_ia DESC NULLS LAST, ordem" if (meta.get("avaliacao") or {}).get("avaliadas") else "ordem"
    linhas = duckdb.connect().execute(
        f"{cte} SELECT telefone FROM r WHERE whatsapp <> 'sim' AND telefone LIKE '+55%' ORDER BY {ordem} LIMIT ?",
        params + [quantidade],
    ).fetchall()
    return [t[0].removeprefix("+55") for t in linhas]


@app.get("/api/listas/{id_}/whatsapp")
def estimativa_whatsapp(id_: str, quantidade: int = 500) -> dict:
    """Antes de verificar: quantos números entram, quantos já estão no cadastro e quanto saldo isso gasta."""
    meta = _ler_meta(id_)
    quantidade = max(1, min(MAX_VERIFICAR, quantidade))
    numeros = _numeros_da_lista(meta, quantidade)
    con = cnpj_db.conectar(tentativas=2)
    try:
        ja = len(whatsapp.do_cache(con, numeros))
    finally:
        con.close()
    servico = _em_cache("servico-whatsapp", 60, _servico_whatsapp)
    return {"numeros": len(numeros), "ja_verificados": ja, "consultas": len(numeros) - ja, "servico": servico}


class PedidoVerificacao(BaseModel):
    lista_id: str
    quantidade: int = 500


@app.post("/api/whatsapp/verificacao")
def iniciar_verificacao(pedido: PedidoVerificacao) -> dict:
    """Verifica pela CheckLeaked os próximos números da lista e, no fim, refaz a lista com os resultados."""
    if _verificando():
        raise HTTPException(409, "Já existe uma verificação em andamento.")
    if not os.getenv("CHECKLEAKED_KEY", "").strip():
        raise HTTPException(409, "Falta a chave da CheckLeaked. Rode `leads segredo CHECKLEAKED_KEY` no servidor.")
    meta = _ler_meta(pedido.lista_id)
    numeros = _numeros_da_lista(meta, max(1, min(MAX_VERIFICAR, pedido.quantidade)))
    if not numeros:
        raise HTTPException(400, "Todos os contatos desta lista já têm o WhatsApp verificado.")

    def avisar(texto: str, feitos: int, total: int) -> None:
        _verificacao.update(mensagem=texto, feitos=feitos, total=total)

    def rodar() -> None:
        try:
            r = consulta.verificar(numeros, parar=_verificacao["parar"], avisar=avisar)
            _verificacao["resumo"] = r
            _verificacao["mensagem"] = _frase_da_verificacao(r)
            if r["com_whatsapp"] or r["sem_whatsapp"]:
                _refazer(meta["id"], "Atualizando a lista com os WhatsApp verificados")
                _verificacao["mensagem"] += (
                    " A lista está sendo atualizada com esses resultados"
                    + (": numa lista grande como esta, leva alguns minutos." if _amplo(meta) else ".")
                )
        except Exception as e:  # noqa: BLE001 - o motivo aparece na tela
            _verificacao["mensagem"] = f"Parou com erro: {type(e).__name__}: {e}"
        _cache.pop("totais-whatsapp", None)
        _cache.pop("servico-whatsapp", None)

    _verificacao["parar"] = threading.Event()
    _verificacao.update(lista_id=meta["id"], feitos=0, total=len(numeros), mensagem="Começando.", resumo=None)
    _verificacao["thread"] = threading.Thread(target=rodar, daemon=True)
    _verificacao["thread"].start()
    return {"ok": True, "numeros": len(numeros)}


def _frase_da_verificacao(r: dict) -> str:
    feitos = f"{r['consultas']} consultados"
    achados = f"{r['com_whatsapp']} com WhatsApp ({r['pessoais']} pessoais, {r['comerciais']} comerciais), {r['sem_whatsapp']} sem"
    return f"{r['motivo'].capitalize()}: {feitos}, {achados}." if r["consultas"] else r["motivo"].capitalize() + "."


@app.delete("/api/whatsapp/verificacao")
def parar_verificacao() -> dict:
    _verificacao["parar"].set()
    _verificacao["mensagem"] = "Parando ao fim desta rodada."
    return {"ok": True}


# ---------- geração ----------

Lista = Annotated[list[str] | None, Query()]

class NovaLista(BaseModel):
    """Lista de uma cidade (`uf` + `cidade`); com `palavras`, das empresas de um estado (ou do país,
    sem `uf`) que têm uma dessas palavras no nome; sem cidade e sem palavras, das empresas com as
    atividades (`cnaes` ou as do perfil) no estado `uf` ou, sem `uf`, no Brasil todo."""

    uf: str = ""
    cidade: str = ""
    palavras: list[str] = []
    publico: str = ""  # "dono" ou "empresa"; vazio: dono na lista de cidade, empresa na lista por nome
    cnaes: list[str] = []
    so_ativas: bool = False
    incluir_mei: bool = False
    so_locais: bool = True
    validar_whatsapp: bool = False
    # Perfil de busca ("solar", "geral" ou o número de um perfil salvo). Sem perfil, vale o público:
    # "dono" é o perfil de energia solar, "empresa" é lista sem perfil.
    perfil: str = ""
    idade_min: int = 0  # anos mínimos de empresa (0 = qualquer)
    portes: list[str] = []  # "01" micro, "03" pequeno, "05" médio ou grande (vazio = todos)


def _publico(meta: dict) -> str:
    """Listas antigas não têm o campo: valem como público "dono"."""
    return meta.get("publico") or "dono"


def _com_perfil(meta: dict) -> dict:
    """A lista como a tela e a API veem: com o público e o perfil sempre preenchidos."""
    return {**meta, "publico": _publico(meta), "perfil": perfis.do_meta(meta)}


def _amplo(meta: dict) -> bool:
    """Lista de vários municípios: por nome da empresa, ou por atividade num estado ou no Brasil."""
    return bool(meta.get("palavras")) or bool(meta.get("regiao"))


def _contar_para_o_cartao(meta: dict) -> None:
    """Os números do cartão saem da mesma consulta que a tela usa, para os dois baterem."""
    cte, params = _consulta(LISTAS / f"{meta['id']}.parquet", {}, {}, 0, 1, "")
    n = duckdb.connect().execute(
        f"""{cte} SELECT count(*), count(*) FILTER (WHERE faixa = 'A'), count(*) FILTER (WHERE faixa = 'B'),
            count(*) FILTER (WHERE primeiro_nome <> ''), count(*) FILTER (WHERE email <> '') FROM r""",
        params,
    ).fetchone()
    meta["resumo"].update(contatos_para_disparo=n[0], faixa_A=n[1], faixa_B=n[2], com_nome=n[3], com_email=n[4])


VERIFICAR_AO_GERAR = 500  # "Verificar WhatsApp ao gerar" confere só os primeiros contatos


def _codigo_do_municipio(meta: dict) -> str:
    """Código do município da lista (as listas antigas não o guardaram); vazio nas listas de vários municípios."""
    if _amplo(meta):
        return ""
    if meta.get("codigo"):
        return meta["codigo"]
    achado = duckdb.connect().execute(
        f"SELECT codigo FROM read_parquet('{_municipios()}') WHERE uf = ? AND nome = strip_accents(upper(?))",
        [meta["uf"], meta["cidade"]],
    ).fetchone()
    if not achado:
        raise HTTPException(404, "Cidade da lista não encontrada na base.")
    return achado[0]


def _refazer(id_: str, motivo: str) -> None:
    """Refaz uma lista pronta com os mesmos parâmetros (WhatsApp novos, notas da IA). Ela segue aberta enquanto isso."""
    meta = _mexer_meta(id_, atualizando=motivo, atualizando_etapa="Começando")
    _fila.put((meta, _codigo_do_municipio(meta), True))


def _gerar(meta: dict, codigo: str, refazer: bool = False) -> None:
    """Gera a lista. Com `refazer`, a lista pronta continua aberta e só é trocada no fim, sem verificar números."""
    if refazer:
        _gerar_de_novo(meta, codigo)
        return

    def etapa(texto: str) -> None:
        meta["etapa"] = texto
        _gravar_meta(meta)

    try:
        meta["status"] = "gerando"
        p = meta["parametros"]
        amplo = _amplo(meta)  # lista por nome da empresa ou por atividade, num estado ou no país
        perfil = perfis.do_meta(meta)
        con = cnpj_db.conectar()
        try:
            leads = enriquecer.enriquecer(
                con, [], codigo, meta["uf"], cidade="" if amplo else meta["cidade"].title(),
                cnaes=[a["codigo"] for a in meta["atividades"]] or [""],
                so_ativas=p["so_ativas"], incluir_mei=p["incluir_mei"], so_locais=p.get("so_locais", True),
                palavras=meta.get("palavras") or None, progresso=etapa, solar=_publico(meta) == "dono",
                idade_min=p.get("idade_min", 0), portes=p.get("portes", []),
            )
            etapa("Aproveitando os WhatsApp já verificados")
            # Sempre aproveita o que já foi verificado; consulta o resto só quando pedido.
            celulares = [t["tel"] for l in leads for t in l["telefones"] if t["faixa"] != "C"]
            tem_whatsapp = whatsapp.do_cache(con, celulares)
        finally:
            con.close()
        etapa("Montando a lista")
        montar = lambda whats: pipeline.montar(  # noqa: E731
            leads, "" if amplo else meta["cidade"], whats, publico=_publico(meta),
            pesos=perfis.pesos(perfil, meta.get("atividades")),
            filtros={"idade_min": p.get("idade_min", 0), "portes": p.get("portes", [])},
            notas=_notas_guardadas(meta),
        )
        linhas, disparo = montar(tem_whatsapp)
        if p["validar_whatsapp"]:
            # Confere pela CheckLeaked os primeiros contatos da lista e monta de novo com as respostas.
            numeros = [l["telefone"].removeprefix("+55") for l in disparo if l["whatsapp"] != "sim"][:VERIFICAR_AO_GERAR]
            r = consulta.verificar(numeros, avisar=lambda texto, feitos, total: etapa(texto))
            if r["consultas"] or r["do_cadastro"]:
                con = cnpj_db.conectar()
                try:
                    tem_whatsapp = whatsapp.do_cache(con, celulares)
                finally:
                    con.close()
                etapa("Montando a lista com os WhatsApp verificados")
                linhas, disparo = montar(tem_whatsapp)
            if r["motivo"] not in ("concluída", "todos os números já estavam verificados"):
                meta["aviso"] = f"WhatsApp verificado só em parte: {r['motivo']}."
        pipeline.salvar_parquet(linhas, LISTAS / f"{meta['id']}.parquet")
        meta["resumo"] = pipeline.resumir(linhas, disparo, whatsapp_verificado=bool(tem_whatsapp))
        _contar_para_o_cartao(meta)
        meta.update(status="pronta", etapa=None)
    except Exception as e:  # noqa: BLE001 - o erro vai para a tela em vez de sumir na thread
        meta.update(status="erro", etapa=None, erro=f"{type(e).__name__}: {e}")
    finally:
        _gravar_meta(meta)


def _gerar_de_novo(meta: dict, codigo: str) -> None:
    """Refaz a lista com os mesmos parâmetros, sem tirá-la do ar: grava ao lado e troca no fim."""
    id_ = meta["id"]
    try:
        meta = _ler_meta(id_)
        p = meta["parametros"]
        amplo = _amplo(meta)
        perfil = perfis.do_meta(meta)
        con = cnpj_db.conectar()
        try:
            leads = enriquecer.enriquecer(
                con, [], codigo, meta["uf"], cidade="" if amplo else meta["cidade"].title(),
                cnaes=[a["codigo"] for a in meta["atividades"]] or [""],
                so_ativas=p["so_ativas"], incluir_mei=p["incluir_mei"], so_locais=p.get("so_locais", True),
                palavras=meta.get("palavras") or None, solar=_publico(meta) == "dono",
                progresso=lambda texto: _mexer_meta(id_, atualizando_etapa=texto),
                idade_min=p.get("idade_min", 0), portes=p.get("portes", []),
            )
            celulares = [t["tel"] for l in leads for t in l["telefones"] if t["faixa"] != "C"]
            tem_whatsapp = whatsapp.do_cache(con, celulares)
        finally:
            con.close()
        _mexer_meta(id_, atualizando_etapa="Montando a lista")
        meta = _ler_meta(id_)  # as notas da IA podem ter mudado enquanto isso
        linhas, disparo = pipeline.montar(
            leads, "" if amplo else meta["cidade"], tem_whatsapp, publico=_publico(meta),
            pesos=perfis.pesos(perfil, meta.get("atividades")),
            filtros={"idade_min": p.get("idade_min", 0), "portes": p.get("portes", [])},
            notas=_notas_guardadas(meta),
        )
        provisorio = LISTAS / f"{id_}.novo.parquet"
        pipeline.salvar_parquet(linhas, provisorio)
        with _meta_trava:
            provisorio.replace(LISTAS / f"{id_}.parquet")
            meta = _ler_meta(id_)
            meta["resumo"] = pipeline.resumir(linhas, disparo, whatsapp_verificado=bool(tem_whatsapp))
            _contar_para_o_cartao(meta)
            meta.update(atualizando=None, atualizando_etapa=None, versao=pipeline.VERSAO, codigo=codigo)
            _gravar_meta(meta)
    except Exception as e:  # noqa: BLE001 - a lista antiga continua valendo; o motivo vai para a tela
        try:
            _mexer_meta(id_, atualizando=None, atualizando_etapa=None, aviso=f"Não deu para atualizar a lista: {type(e).__name__}: {e}")
        except HTTPException:
            pass  # a lista foi excluída no meio


def _notas_guardadas(meta: dict) -> dict[str, tuple[int, str]]:
    """Notas da IA já dadas às empresas desta lista (refazer a lista não as perde nem paga de novo)."""
    chave = (meta.get("avaliacao") or {}).get("chave")
    return pastas.notas_da_ia(chave) if chave else {}


def _migrar_todas() -> None:
    """Ao ligar, leva as listas de antes dos perfis ao formato novo, para ninguém esperar ao abri-las."""
    for arq in sorted(LISTAS.glob("*.json")):
        try:
            meta = json.loads(arq.read_text())
            parquet = LISTAS / f"{meta['id']}.parquet"
            if meta.get("status") == "pronta" and meta.get("versao", 1) < pipeline.VERSAO and parquet.exists():
                _migrar(meta, parquet)
        except Exception as e:  # noqa: BLE001 - uma lista com problema não impede as outras
            print(f"Lista {arq.stem} não migrada: {type(e).__name__}: {e}", flush=True)


def _atender_fila() -> None:
    _migrar_todas()
    while True:
        meta, codigo, *refazer = _fila.get()
        if _meta_caminho(meta["id"]).exists():  # pode ter sido excluída enquanto esperava
            _gerar(meta, codigo, bool(refazer and refazer[0]))


threading.Thread(target=_atender_fila, daemon=True).start()


def _palavras(brutas: list[str]) -> list[str]:
    """Palavras de busca no nome da empresa: sem repetição, sem acento, de 3 a 40 letras, no máximo 10."""
    limpas = sorted({" ".join(p.split()).upper() for p in brutas} - {""})
    if any(len(p) < 3 or len(p) > 40 for p in limpas):
        raise HTTPException(400, "Cada palavra precisa ter de 3 a 40 letras.")
    if len(limpas) > 10:
        raise HTTPException(400, "Use no máximo 10 palavras.")
    return limpas


def _uf_valida(uf: str) -> str:
    uf = uf.strip().upper()
    if uf and uf not in ufs():
        raise HTTPException(404, "Estado não encontrado na base.")
    return uf


def _rotulo(meta: dict) -> str:
    """Nome curto da lista: "Montes Claros/MG"; na lista por nome, "Solar · Fotovolt (Brasil)";
    na lista por atividade, "MG (estado todo)" ou "Brasil todo"."""
    if meta.get("palavras"):
        return f"{' · '.join(p.title() for p in meta['palavras'])} ({meta['uf'] or 'Brasil'})"
    if meta.get("regiao"):
        return f"{meta['uf']} (estado todo)" if meta["uf"] else "Brasil todo"
    return f"{meta['cidade'].title()}/{meta['uf']}"


@app.get("/api/publico")
def previa_do_publico(
    palavra: Lista = None, uf: str = "", so_ativas: int = 1, incluir_mei: int = 0,
) -> dict:
    """Quantas empresas (sedes) têm as palavras no nome, por atividade e por estado.

    Serve para escolher as atividades antes de gerar: "solar" também está no nome de condomínios e usinas.
    """
    palavras, uf = _palavras(palavra or []), _uf_valida(uf)
    if not palavras:
        raise HTTPException(400, "Informe ao menos uma palavra.")

    def calcular() -> dict:
        nome = "strip_accents(upper(coalesce(razao_social, '') || ' ' || coalesce(nome_fantasia, '')))"
        cond = " OR ".join(f"contains({nome}, strip_accents(?))" for _ in palavras)
        extra, params = "", list(palavras)
        if uf:
            extra += " AND uf = ?"
            params.append(uf)
        if so_ativas:
            extra += " AND situacao = '02'"
        if not incluir_mei:
            extra += " AND NOT mei"
        con = cnpj_db.conectar(tentativas=2)
        try:
            linhas = con.execute(
                f"""
                SELECT cnae_fiscal, any_value(cnae_descricao), uf, count(*) FROM estab_cidade
                WHERE ({cond}) {extra} AND matriz_filial = '1' AND coalesce(natureza_juridica, '') NOT LIKE '1%'
                GROUP BY cnae_fiscal, uf
                """,
                params,
            ).fetchall()
        finally:
            con.close()
        atividades: dict[str, list] = {}
        estados: dict[str, int] = {}
        for cnae, descricao, estado, n in linhas:
            atividades.setdefault(cnae or "", [descricao or "", 0])[1] += n
            estados[estado] = estados.get(estado, 0) + n
        total = sum(estados.values())
        return {
            "total": total,
            "demais": total > enriquecer.MAX_EMPRESAS_POR_NOME,
            "atividades": [
                {"codigo": c, "descricao": d, "n": n}
                for c, (d, n) in sorted(atividades.items(), key=lambda a: (-a[1][1], a[0]))[:60]
            ],
            "estados": [{"uf": e, "n": n} for e, n in sorted(estados.items(), key=lambda a: (-a[1], a[0]))],
        }

    return _em_cache(f"publico:{'|'.join(palavras)}:{uf}:{so_ativas}:{incluir_mei}", 600, calcular)


def _cabe_na_lista(uf: str, cnaes: list[str], so_ativas: bool, incluir_mei: bool, idade_min: int, portes: list[str]) -> None:
    """Recusa na hora, e não depois de minutos na fila, a lista de estado ou do Brasil grande demais."""
    con = cnpj_db.conectar(tentativas=2)
    try:
        n = busca_ia.contar_empresas(
            con, uf=uf, codigos=cnaes, so_ativas=so_ativas, incluir_mei=incluir_mei, idade_min=idade_min, portes=portes
        )["total"]
    finally:
        con.close()
    if n > enriquecer.MAX_EMPRESAS_POR_NOME:
        onde = f"em {uf}" if uf else "no Brasil"
        raise HTTPException(
            400,
            f"São {n:,} empresas {onde} com essas atividades e filtros, mais que as {enriquecer.MAX_EMPRESAS_POR_NOME:,} de uma lista."
            .replace(",", ".") + " Escolha um estado, tire o MEI ou use menos atividades.",
        )


@app.post("/api/listas")
def criar_lista(pedido: NovaLista) -> dict:
    con = duckdb.connect()
    cnaes = sorted({"".join(c for c in x if c.isdigit()) for x in pedido.cnaes} - {""})
    palavras = _palavras(pedido.palavras)
    if pedido.perfil.strip():
        perfil = perfis.um(pedido.perfil)
        if not perfil:
            raise HTTPException(404, "Perfil não encontrado.")
        publico = "dono" if perfil["solar"] else "empresa"
    else:
        publico = pedido.publico.strip() or ("empresa" if palavras else "dono")
        if publico not in pipeline.PUBLICOS:
            raise HTTPException(400, "Público desconhecido.")
        perfil = perfis.PRONTOS[perfis.SOLAR if publico == "dono" else perfis.GERAL]
    # O que o pedido não disse vem do perfil: atividades, palavras e filtros.
    dito = pedido.model_fields_set
    receita = perfil["receita"]
    conhecidas = {a["codigo"]: a["descricao"] for a in receita["atividades"]}
    if "cnaes" not in dito and not palavras:
        cnaes = sorted(conhecidas)
    if "palavras" not in dito and not cnaes and receita["palavras"]:
        palavras = _palavras(receita["palavras"])
    f = receita["filtros"]
    so_ativas = pedido.so_ativas if "so_ativas" in dito else f["so_ativas"]
    incluir_mei = pedido.incluir_mei if "incluir_mei" in dito else f["incluir_mei"]
    so_locais = pedido.so_locais if "so_locais" in dito else f["so_locais"]
    portes = sorted({str(x).zfill(2) for x in (pedido.portes if "portes" in dito else f["portes"])} & set(perfis.PORTES))
    idade_min = max(0, min(50, pedido.idade_min if "idade_min" in dito else f["idade_min"]))
    regiao = not palavras and not pedido.cidade.strip()
    if palavras:
        uf = _uf_valida(pedido.uf)
        # achado: (código do município, nome da lista, nº de empresas) — aqui não há município.
        achado = ("", " · ".join(palavras), 0)
    elif regiao:
        uf = _uf_valida(pedido.uf)
        if not cnaes:
            raise HTTPException(400, "Para gerar a lista de um estado ou do Brasil, escolha as atividades.")
        _cabe_na_lista(uf, cnaes, so_ativas, incluir_mei, idade_min, portes)
        achado = ("", uf or "Brasil", 0)
    else:
        uf = pedido.uf.upper()
        achado = con.execute(
            f"SELECT codigo, exibicao, empresas FROM read_parquet('{_municipios()}') "
            "WHERE uf = ? AND nome = strip_accents(upper(?))",
            [uf, pedido.cidade.strip()],
        ).fetchone()
        if not achado:
            raise HTTPException(404, "Cidade não encontrada na base.")
    if achado[2] > LIMITE_SEM_ATIVIDADE and not cnaes:
        raise HTTPException(
            400,
            f"{achado[1].title()} tem {achado[2]:,} empresas".replace(",", ".")
            + ", muito para uma lista só. Escolha as atividades que interessam e gere por partes.",
        )
    descricoes = {c: conhecidas[c] for c in cnaes if conhecidas.get(c)}
    faltam = [c for c in cnaes if c not in descricoes]
    if faltam:
        descricoes |= dict(
            con.execute(
                f"SELECT trim(codigo), descricao FROM read_parquet('{config.CNPJ_PARQUET}/cnaes_*.parquet') "
                "WHERE trim(codigo) IN (SELECT unnest(?))",
                [faltam],
            ).fetchall()
        )
    agora = datetime.now()
    id_ = f"{agora:%Y%m%d-%H%M%S}-{pipeline.slug(achado[1])[:40]}-{uf.lower() or 'br'}"
    while (LISTAS / f"{id_}.json").exists():
        id_ += "b"
    meta = {
        "id": id_,
        "criada_em": agora.isoformat(timespec="seconds"),
        "cidade": achado[1],
        "uf": uf,
        "palavras": palavras,
        "regiao": regiao,
        "publico": publico,
        "perfil": perfis.retrato(perfil),
        "versao": pipeline.VERSAO,
        "codigo": achado[0],
        "atividades": [{"codigo": c, "descricao": descricoes.get(c, c)} for c in cnaes],
        "parametros": {
            "so_ativas": so_ativas, "incluir_mei": incluir_mei, "so_locais": so_locais,
            "validar_whatsapp": pedido.validar_whatsapp, "idade_min": idade_min, "portes": portes,
        },
        "status": "na_fila",
        "etapa": "Aguardando a vez na fila",
        "erro": None,
        "aviso": None,
        "resumo": None,
    }
    _gravar_meta(meta)
    _fila.put((meta, achado[0]))
    return {"id": meta["id"]}


@app.get("/api/listas")
def listar() -> list[dict]:
    metas = [json.loads(a.read_text()) for a in LISTAS.glob("*.json")]
    return sorted((_com_perfil(m) for m in metas), key=lambda m: m["criada_em"], reverse=True)


@app.get("/api/listas/{id_}")
def uma_lista(id_: str) -> dict:
    return _com_perfil(_ler_meta(id_))


class Publico(BaseModel):
    publico: str


@app.patch("/api/listas/{id_}")
def mudar_publico(id_: str, pedido: Publico) -> dict:
    """Troca o público de uma lista pronta: muda a ordem, as etiquetas e o que a tela e o CSV mostram.

    Os contatos e os telefones continuam os mesmos; não é preciso gerar de novo.
    """
    meta = _ler_meta(id_)
    if pedido.publico not in pipeline.PUBLICOS:
        raise HTTPException(400, "Público desconhecido.")
    if meta["status"] != "pronta":
        raise HTTPException(409, "Espere a lista ficar pronta para mudar o público.")
    if pedido.publico != _publico(meta):
        arquivo = _parquet(id_)
        meta = _ler_meta(id_)
        # Trocar o público troca o perfil pelo pronto correspondente; um perfil salvo vira "sem perfil".
        perfil = perfis.retrato(perfis.PRONTOS[perfis.SOLAR if pedido.publico == "dono" else perfis.GERAL])
        if pedido.publico == "empresa" and not meta.get("perfil", {}).get("solar", True):
            perfil = meta["perfil"]
        pipeline.trocar_publico(
            arquivo, "" if _amplo(meta) else meta["cidade"], pedido.publico, perfis.pesos(perfil, meta.get("atividades"))
        )
        meta.update(publico=pedido.publico, perfil=perfil)
        _contar_para_o_cartao(meta)
        _gravar_meta(meta)
    return _com_perfil(meta)


# ---------- IA: busca por descrição ----------

_buscas: dict[str, dict] = {}


class PedidoBusca(BaseModel):
    texto: str


def _custo_da_ref(ref: str) -> float:
    from contextlib import closing

    with closing(pastas._con()) as con:
        return round(float(con.execute("SELECT coalesce(sum(custo_usd), 0) FROM ia_uso WHERE ref = ?", [ref]).fetchone()[0]), 4)


@app.get("/api/ia")
def uso_da_ia() -> dict:
    return ia.uso_do_mes()


@app.post("/api/ia/busca")
def iniciar_busca(pedido: PedidoBusca) -> dict:
    """A IA monta um perfil a partir da descrição. Leva cerca de um minuto; acompanhe em /api/ia/busca/{id}."""
    try:
        if not ia.configurada():
            raise ia.IaIndisponivel("Falta a chave da Anthropic. Rode `uv run leads segredo ANTHROPIC_API_KEY` no servidor.")
        ia.conferir_teto(0.30)
    except ia.IaIndisponivel as e:
        raise HTTPException(409, str(e)) from e
    texto = pedido.texto.strip()
    if not 10 <= len(texto) <= 2000:
        raise HTTPException(400, "Descreva em 10 a 2.000 letras o que você vende e quem quer encontrar.")
    if any(b["status"] == "pensando" for b in _buscas.values()):
        raise HTTPException(409, "Já existe uma busca com IA em andamento. Espere ela terminar.")
    id_ = os.urandom(6).hex()
    estado = {"id": id_, "status": "pensando", "etapa": "Começando", "texto": texto, "proposta": None, "erro": None,
              "criada_em": datetime.now().isoformat(timespec="seconds")}
    _buscas[id_] = estado
    for velho in sorted(_buscas, key=lambda k: _buscas[k]["criada_em"])[:-20]:
        _buscas.pop(velho, None)

    def rodar() -> None:
        try:
            estado["proposta"] = busca_ia.montar_perfil(texto, etapa=lambda t: estado.update(etapa=t), ref=f"busca:{id_}")
            estado["status"] = "pronta"
        except ia.IaIndisponivel as e:
            estado.update(status="erro", erro=str(e))
        except Exception as e:  # noqa: BLE001 - o motivo vai para a tela
            estado.update(status="erro", erro=f"{type(e).__name__}: {e}")

    threading.Thread(target=rodar, daemon=True).start()
    return {"id": id_}


@app.get("/api/ia/busca/{busca_id}")
def andamento_da_busca(busca_id: str) -> dict:
    estado = _buscas.get(busca_id)
    if not estado:
        raise HTTPException(404, "Busca não encontrada (o servidor pode ter reiniciado). Descreva de novo.")
    return {**estado, "custo_usd": _custo_da_ref(f"busca:{busca_id}")}


class Contagem(BaseModel):
    cidade: str = ""
    uf: str = ""
    codigos: list[str] = []
    so_ativas: bool = True
    incluir_mei: bool = False
    idade_min: int = 0
    portes: list[str] = []


@app.post("/api/perfis/contar")
def contar_para_o_perfil(c: Contagem) -> dict:
    """Quantas empresas cada atividade tem na cidade com os filtros: para ajustar a proposta antes de gerar."""
    con = cnpj_db.conectar(tentativas=5)
    try:
        return busca_ia.contar_empresas(
            con, cidade=c.cidade, uf=c.uf, codigos=c.codigos, so_ativas=c.so_ativas, incluir_mei=c.incluir_mei,
            idade_min=max(0, min(50, c.idade_min)), portes=c.portes,
        )
    finally:
        con.close()


# ---------- IA: avaliação dos contatos ----------

_avaliacao = {"thread": None, "parar": threading.Event(), "lista_id": None, "feitos": 0, "total": 0, "mensagem": "", "resumo": None, "amostra": None}
_CAMPOS_PUBLICOS = (
    "cnpj, empresa, razao_social, atividade, porte, capital_social, idade_empresa, natureza, simples, situacao, "
    "unidades_ativas, numero_socios, atividades_secundarias, bairro, cidade, uf"
)


def _avaliando() -> bool:
    t = _avaliacao["thread"]
    return bool(t and t.is_alive())


def _empresas_para_avaliar(meta: dict, quantidade: int) -> list[dict]:
    """As primeiras empresas da lista (uma linha por CNPJ, na ordem da tela), só com os campos públicos."""
    cte, params = _consulta(_parquet(meta["id"]), {}, {}, 0, 1, "")
    cur = duckdb.connect().execute(
        f"""{cte} SELECT {_CAMPOS_PUBLICOS} FROM (
              SELECT * FROM r WHERE cnpj <> '' QUALIFY row_number() OVER (PARTITION BY cnpj ORDER BY ordem) = 1
            ) ORDER BY ordem LIMIT ?""",
        params + [quantidade],
    )
    return _linhas(cur)


def _criterio_da_lista(meta: dict, escrito: str = "") -> str:
    return (escrito or "").strip() or (meta.get("avaliacao") or {}).get("criterio") or perfis.criterio(perfis.do_meta(meta))


def _andamento_da_avaliacao() -> dict:
    return {k: _avaliacao[k] for k in ("lista_id", "feitos", "total", "mensagem", "resumo", "amostra")} | {"rodando": _avaliando()}


@app.get("/api/listas/{id_}/avaliacao")
def estado_da_avaliacao(id_: str, quantidade: int = 500, criterio: str = "", so_andamento: int = 0) -> dict:
    """Antes de avaliar: o critério, quantas empresas entram, quantas já têm nota e o custo estimado.

    Com `so_andamento=1` (a tela consultando a cada segundo), só o andamento, sem refazer as contas.
    """
    meta = _ler_meta(id_)
    if so_andamento:
        return {"avaliacao": meta.get("avaliacao"), "andamento": _andamento_da_avaliacao()}
    quantidade = max(1, min(avaliacao_ia.LIMITE_DIRETO * 20, quantidade))
    texto = _criterio_da_lista(meta, criterio)
    empresas = _empresas_para_avaliar(meta, quantidade) if meta["status"] == "pronta" else []
    ja = len(pastas.notas_da_ia(avaliacao_ia.chave(texto), [e["cnpj"] for e in empresas])) if texto else 0
    falta = len(empresas) - ja
    uso = ia.uso_do_mes()
    return {
        "criterio": texto, "empresas": len(empresas), "ja_avaliadas": ja, "a_avaliar": falta,
        "custo_usd": avaliacao_ia.estimar_usd(falta), "em_lote": falta > avaliacao_ia.LIMITE_DIRETO,
        "gasto_usd": uso["gasto_usd"], "teto_usd": uso["teto_usd"], "configurada": uso["configurada"],
        "avaliacao": meta.get("avaliacao"),
        "andamento": _andamento_da_avaliacao(),
    }


class PedidoAvaliacao(BaseModel):
    quantidade: int = 500
    criterio: str = ""
    amostra: bool = False


TAMANHO_DA_AMOSTRA = 30


@app.post("/api/listas/{id_}/avaliar")
def avaliar_lista(id_: str, pedido: PedidoAvaliacao) -> dict:
    """A IA dá nota de 0 a 10 às primeiras empresas da lista. Com `amostra`, só as 30 primeiras, para conferir."""
    meta = _ler_meta(id_)
    _parquet(id_)
    if _avaliando():
        raise HTTPException(409, "Já existe uma avaliação em andamento.")
    criterio = _criterio_da_lista(meta, pedido.criterio)
    quantidade = TAMANHO_DA_AMOSTRA if pedido.amostra else max(1, min(avaliacao_ia.LIMITE_DIRETO * 20, pedido.quantidade))
    empresas = _empresas_para_avaliar(meta, quantidade)
    try:
        if not ia.configurada():
            raise ia.IaIndisponivel("Falta a chave da Anthropic. Rode `uv run leads segredo ANTHROPIC_API_KEY` no servidor.")
        if len(criterio) < 15:
            raise ia.IaIndisponivel("Escreva o critério: o que você vende e quem é o cliente ideal.")
        k = avaliacao_ia.chave(criterio)
        falta = len(empresas) - len(pastas.notas_da_ia(k, [e["cnpj"] for e in empresas]))
        ia.conferir_teto(avaliacao_ia.estimar_usd(falta))
    except ia.IaIndisponivel as e:
        raise HTTPException(409, str(e)) from e

    def avisar(texto: str, feitos: int, total: int) -> None:
        _avaliacao.update(mensagem=texto, feitos=feitos, total=total)

    def rodar() -> None:
        try:
            r = avaliacao_ia.avaliar(empresas, criterio, parar=_avaliacao["parar"], avisar=avisar, ref=f"lista:{id_}")
            _avaliacao["resumo"] = r
            _aplicar_notas(id_, criterio)
            if pedido.amostra:
                notas = pastas.notas_da_ia(k, [e["cnpj"] for e in empresas])
                _avaliacao["amostra"] = sorted(
                    ({"empresa": e["empresa"], "atividade": e["atividade"], "nota": notas[e["cnpj"]][0], "motivo": notas[e["cnpj"]][1]}
                     for e in empresas if e["cnpj"] in notas),
                    key=lambda a: -a["nota"],
                )
            feitas = r["avaliadas"] + r["ja_tinham"]
            _avaliacao["mensagem"] = (
                f"{r['motivo'].capitalize()}: {feitas} empresas com nota"
                + (f", {r['recusadas']} sem nota" if r["recusadas"] else "")
                + (f" · US$ {r['custo_usd']:.4f}" if r["custo_usd"] else "") + "."
            )
        except ia.IaIndisponivel as e:
            _avaliacao["mensagem"] = str(e)
        except Exception as e:  # noqa: BLE001 - o motivo aparece na tela
            _avaliacao["mensagem"] = f"Parou com erro: {type(e).__name__}: {e}"

    _avaliacao["parar"] = threading.Event()
    _avaliacao.update(lista_id=id_, feitos=0, total=len(empresas), mensagem="Começando.", resumo=None, amostra=None)
    _avaliacao["thread"] = threading.Thread(target=rodar, daemon=True)
    _avaliacao["thread"].start()
    return {"ok": True, "empresas": len(empresas)}


@app.delete("/api/listas/{id_}/avaliar")
def parar_avaliacao(id_: str) -> dict:
    _avaliacao["parar"].set()
    _avaliacao["mensagem"] = "Parando."
    return {"ok": True}


def _aplicar_notas(id_: str, criterio: str) -> None:
    """Grava na lista as notas da IA com este critério e anota na lista o que foi avaliado."""
    k = avaliacao_ia.chave(criterio)
    notas = pastas.notas_da_ia(k)
    arquivo = _parquet(id_)
    with _meta_trava:
        pipeline.trocar_notas_da_ia(arquivo, notas)
        avaliadas = duckdb.connect().execute(
            f"SELECT count(DISTINCT cnpj) FROM read_parquet('{arquivo}') WHERE nota_ia IS NOT NULL"
        ).fetchone()[0]
        meta = _ler_meta(id_)
        meta["avaliacao"] = {
            "chave": k, "criterio": criterio, "modelo": ia.MODELO_AVALIACAO, "avaliadas": avaliadas,
            "custo_usd": _custo_da_ref(f"lista:{id_}"), "em": datetime.now().isoformat(timespec="seconds"),
        }
        _gravar_meta(meta)


# ---------- perfis de busca ----------

class NovoPerfil(BaseModel):
    nome: str
    receita: dict = {}


def _perfil_ou_404(perfil_id: str) -> dict:
    perfil = perfis.um(perfil_id)
    if not perfil:
        raise HTTPException(404, "Perfil não encontrado.")
    return perfil


@app.get("/api/perfis")
def listar_perfis() -> list[dict]:
    return perfis.listar()


@app.get("/api/perfis/{perfil_id}")
def um_perfil(perfil_id: str) -> dict:
    return _perfil_ou_404(perfil_id)


@app.post("/api/perfis")
def criar_perfil(novo: NovoPerfil) -> dict:
    try:
        return perfis.criar(novo.nome, novo.receita)
    except perfis.PerfilInvalido as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/perfis/{perfil_id}")
def mudar_perfil(perfil_id: str, novo: NovoPerfil) -> dict:
    _perfil_ou_404(perfil_id)
    try:
        return perfis.atualizar(perfil_id, novo.nome, novo.receita)
    except perfis.PerfilInvalido as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/perfis/{perfil_id}")
def excluir_perfil(perfil_id: str) -> dict:
    _perfil_ou_404(perfil_id)
    try:
        perfis.excluir(perfil_id)
    except perfis.PerfilInvalido as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.delete("/api/listas/{id_}")
def excluir(id_: str) -> dict:
    meta = _ler_meta(id_)
    if meta["status"] == "gerando":
        raise HTTPException(409, "Espere a geração terminar para excluir.")
    _meta_caminho(id_).unlink()
    (LISTAS / f"{id_}.parquet").unlink(missing_ok=True)
    return {"ok": True}


# ---------- consulta ----------

_FILTROS_IN = ("faixa", "situacao", "papel", "bairro", "atividade", "tipo_whatsapp", "solar", "oportunidade", "uf", "cidade")
_ORDENS = {
    "relevancia": "ordem",
    # "aptidao" é o nome antigo: o encaixe no perfil, que nas listas de energia solar é a própria nota solar.
    "aptidao": "encaixe DESC, ordem",
    "encaixe": "encaixe DESC, ordem",
    "ia": "nota_ia DESC NULLS LAST, ordem",
    "chance": "chance_dono DESC, ordem",
    "nome": "primeiro_nome, sobrenome, ordem",
    "empresa": "empresa, ordem",
}


def _consulta(
    arquivo: Path, filtros: dict, minimos: dict | None, com_email: int, so_disparo: int, busca: str, pasta: str | None = None
):
    """Monta o SQL (CTE `r` com as linhas do filtro) e seus parâmetros.

    `minimos`: nota mínima por coluna (`encaixe`, `nota_ia`).
    `pasta`: "fora" deixa só quem não está em nenhuma pasta; um id deixa só quem está naquela.
    """
    cond, params = ["true"], []
    for campo in _FILTROS_IN:
        valores = filtros.get(campo)
        if valores:
            cond.append(f"{campo} IN (SELECT unnest(?))")
            params.append(valores)
    for coluna, minimo in (minimos or {}).items():
        if minimo:
            cond.append(f"{coluna} >= ?")
            params.append(minimo)
    if com_email:
        cond.append("email <> ''")
    if busca.strip():
        cond.append(
            "strip_accents(lower(concat_ws(' ', primeiro_nome, sobrenome, empresa, razao_social, cnpj, telefone, bairro, cidade))) "
            "LIKE '%' || strip_accents(lower(?)) || '%'"
        )
        params.append(busca.strip())
    bloqueados = pastas.bloqueados()
    if bloqueados:  # quem pediu para sair não aparece em nenhuma consulta nem exportação
        cond.append("telefone NOT IN (SELECT unnest(?))")
        params.append(bloqueados)
    if pasta == "fora":
        cond.append("telefone NOT IN (SELECT unnest(?))")
        params.append(pastas.telefones())
    elif pasta:
        cond.append("telefone IN (SELECT unnest(?))")
        params.append(pastas.telefones(int(pasta)) if pasta.isascii() and pasta.isdigit() else [])
    if so_disparo:
        cond.append("faixa IN ('A', 'B') AND telefone <> ''")
    base = f"SELECT * FROM read_parquet('{arquivo}') WHERE {' AND '.join(cond)}"
    if so_disparo:
        # Uma linha por telefone e por dono, ficando a de melhor colocação dentro do filtro.
        base = f"""
            SELECT * FROM (
              SELECT * FROM ({base}) QUALIFY row_number() OVER (PARTITION BY telefone ORDER BY ordem) = 1
            ) QUALIFY row_number() OVER (
              PARTITION BY CASE WHEN dono_chave <> '' THEN 'd:' || dono_chave ELSE 't:' || telefone END ORDER BY ordem) = 1
        """
    return f"WITH r AS ({base})", params


class Filtros:
    """Os filtros da tela, iguais em contatos, painel, exportar e adicionar a uma pasta."""

    def __init__(
        self,
        faixa: Lista = None, situacao: Lista = None, papel: Lista = None, bairro: Lista = None,
        atividade: Lista = None, tipo_whatsapp: Lista = None, solar: Lista = None, oportunidade: Lista = None,
        uf: Lista = None, cidade: Lista = None,
        aptidao_min: int | None = None, com_email: int = 0, so_disparo: int = 1, busca: str = "",
        ordem: str = "relevancia", pasta: str | None = None, encaixe_min: int | None = None, ia_min: int | None = None,
    ) -> None:
        self.em = {
            "faixa": faixa, "situacao": situacao, "papel": papel, "bairro": bairro, "atividade": atividade,
            "tipo_whatsapp": tipo_whatsapp, "solar": solar, "oportunidade": oportunidade, "uf": uf, "cidade": cidade,
        }
        self.aptidao_min, self.com_email, self.so_disparo, self.busca = aptidao_min, com_email, so_disparo, busca
        self.encaixe_min, self.ia_min = encaixe_min, ia_min
        # `aptidao_min` (nome antigo) e `encaixe_min` filtram a mesma nota: nas listas solares, encaixe = nota solar.
        self.minimos = {"encaixe": max(aptidao_min or 0, encaixe_min or 0) or None, "nota_ia": ia_min}
        self.ordem = _ORDENS.get(ordem, "ordem")
        self.pasta = pasta

    def consulta(self, arquivo: Path, pasta: str | None = None):
        return _consulta(arquivo, self.em, self.minimos, self.com_email, self.so_disparo, self.busca, pasta or self.pasta)


@app.get("/api/listas/{id_}/filtros")
def opcoes_de_filtro(id_: str, so_disparo: int = 1) -> dict:
    arquivo = _parquet(id_)
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE fora (telefone VARCHAR)")
    bloqueados = pastas.bloqueados()
    if bloqueados:  # o DuckDB recusa inserção em lote com lista vazia
        con.executemany("INSERT INTO fora VALUES (?)", [(t,) for t in bloqueados])
    saida = {}
    for campo in _FILTROS_IN:
        limite = 400 if campo in ("bairro", "atividade", "cidade") else 50
        if so_disparo:
            # Mesma regra da tela: filtra pela opção e só depois tira os repetidos,
            # para o número ao lado de cada opção bater com o que aparece ao marcá-la.
            sql = f"""
                SELECT {campo}, count(*) n FROM (
                  SELECT * FROM (
                    SELECT * FROM read_parquet('{arquivo}') WHERE faixa IN ('A', 'B') AND telefone <> ''
                      AND telefone NOT IN (SELECT telefone FROM fora)
                    QUALIFY row_number() OVER (PARTITION BY {campo}, telefone ORDER BY ordem) = 1
                  ) QUALIFY row_number() OVER (
                    PARTITION BY {campo}, CASE WHEN dono_chave <> '' THEN 'd:' || dono_chave ELSE 't:' || telefone END
                    ORDER BY ordem) = 1
                ) GROUP BY 1 ORDER BY n DESC, 1 LIMIT {limite}"""
        else:
            sql = (
                f"SELECT {campo}, count(*) n FROM read_parquet('{arquivo}') WHERE telefone NOT IN (SELECT telefone FROM fora) "
                f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {limite}"
            )
        saida[campo] = [{"valor": v, "n": n} for v, n in con.execute(sql).fetchall()]
    return saida


def _linhas(cur) -> list[dict]:
    nomes = [d[0] for d in cur.description]
    return [dict(zip(nomes, linha)) for linha in cur.fetchall()]


@app.get("/api/listas/{id_}/contatos")
def contatos(id_: str, f: Annotated[Filtros, Depends()], pagina: int = 1, por_pagina: int = 50) -> dict:
    cte, params = f.consulta(_parquet(id_))
    pagina, por_pagina = max(1, pagina), max(1, min(500, por_pagina))
    con = duckdb.connect()
    r = con.execute(
        f"""{cte} SELECT count(*), count(*) FILTER (WHERE faixa = 'A'), count(*) FILTER (WHERE faixa = 'B'),
            count(*) FILTER (WHERE faixa = 'C'), count(*) FILTER (WHERE email <> ''),
            count(*) FILTER (WHERE primeiro_nome <> '') FROM r""",
        params,
    ).fetchone()
    linhas = _linhas(
        con.execute(
            f"{cte} SELECT * EXCLUDE (dono_chave, ordem) FROM r ORDER BY {f.ordem} LIMIT ? OFFSET ?",
            params + [por_pagina, (pagina - 1) * por_pagina],
        )
    )
    onde = pastas.pastas_dos([l["telefone"] for l in linhas if l["telefone"]])
    for l in linhas:
        l["pastas"] = onde.get(l["telefone"], [])
    return {
        "total": r[0],
        "pagina": pagina,
        "por_pagina": por_pagina,
        "resumo": {"contatos": r[0], "faixa_A": r[1], "faixa_B": r[2], "faixa_C": r[3], "com_email": r[4], "com_nome": r[5]},
        "linhas": linhas,
    }


_IDADES = ["21 a 30", "31 a 40", "41 a 50", "51 a 60", "61 a 70", "71 a 80", "mais de 80"]


@app.get("/api/listas/{id_}/painel")
def painel(id_: str, f: Annotated[Filtros, Depends()]) -> dict:
    """Números para os gráficos, com os mesmos filtros da tabela."""
    cte, params = f.consulta(_parquet(id_))
    con = duckdb.connect()
    con.execute(f"CREATE TEMP TABLE r AS {cte} SELECT * FROM r", params)

    def grupo(expr: str, onde: str = "true", ordem: str = "n DESC, 1", limite: int = 50) -> list[dict]:
        linhas = con.execute(f"SELECT {expr} v, count(*) n FROM r WHERE {onde} GROUP BY 1 ORDER BY {ordem} LIMIT {limite}").fetchall()
        return [{"valor": v, "n": n} for v, n in linhas]

    idades = {g["valor"]: g["n"] for g in grupo("idade_dono", "idade_dono <> ''")}
    return {
        "total": con.execute("SELECT count(*) FROM r").fetchone()[0],
        "oportunidade": grupo("oportunidade"),
        "faixa": grupo("faixa", ordem="1"),
        "situacao": grupo("situacao"),
        "solar": grupo("solar"),
        "whatsapp": grupo("CASE WHEN tipo_whatsapp <> '' THEN tipo_whatsapp ELSE 'não verificado' END"),
        "idade": [{"valor": i, "n": idades[i]} for i in _IDADES if i in idades],
        "aptidao": grupo("encaixe", ordem="1"),
        "nota_ia": grupo("nota_ia", "nota_ia IS NOT NULL", ordem="1"),
        "atividades": grupo("atividade", "atividade <> ''", limite=10),
        "bairros": grupo("bairro", "bairro <> ''", limite=10),
        "solar_por_ano": grupo(
            "substr(solar_desde, 1, 4)", "solar IN ('empresa já tem', 'outra unidade da empresa tem') AND solar_desde <> ''", ordem="1"
        ),
    }


def _csv(linhas: list[list], nome: str, publico: str = "dono") -> StreamingResponse:
    saida = io.StringIO()
    w = csv.writer(saida)
    w.writerow(pipeline.cabecalhos_do_publico(publico))
    w.writerows([[pipeline.celula_segura(v) for v in linha] for linha in linhas])
    return StreamingResponse(
        iter(["﻿" + saida.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/api/listas/{id_}/exportar")
def exportar(id_: str, f: Annotated[Filtros, Depends()], request: Request) -> StreamingResponse:
    cte, params = f.consulta(_parquet(id_))
    meta = _ler_meta(id_)
    sel = ", ".join(pipeline.chaves_do_publico(_publico(meta)))
    linhas = duckdb.connect().execute(f"{cte} SELECT {sel} FROM r ORDER BY {f.ordem}", params).fetchall()
    pastas.registrar_exportacao("lista", id_, f"Lista de {_rotulo(meta)}", len(linhas), _ip(request))
    return _csv(
        linhas,
        f"garimpo-{pipeline.slug(meta['cidade'])[:40]}-{meta['uf'].lower() or 'br'}-{datetime.now():%Y%m%d-%H%M}.csv",
        _publico(meta),
    )


# ---------- PDF ----------

_FAIXAS = {"A": "contatos fortes", "B": "contatos médios"}
_TIPOS_WHATSAPP = {"pessoal": "WhatsApp pessoal", "business": "WhatsApp comercial"}


def _descricao_do_filtro(f: Filtros, publico: str = "dono") -> str:
    """O filtro em palavras, para a capa do PDF: "Com WhatsApp confirmado · MG · busca “solar”"."""
    partes = []
    tipos = [t for t in (f.em.get("tipo_whatsapp") or []) if t in _TIPOS_WHATSAPP]
    if len(tipos) == 2:
        partes.append("Com WhatsApp confirmado")
    elif tipos:
        partes.append(_TIPOS_WHATSAPP[tipos[0]])
    if f.em.get("faixa") and len(f.em["faixa"]) == 1 and f.em["faixa"][0] in _FAIXAS:
        partes.append("Só " + _FAIXAS[f.em["faixa"][0]])
    for campo in ("situacao", "papel", "uf", "cidade", "bairro", "atividade", "oportunidade", "solar"):
        valores = f.em.get(campo) or []
        if valores:
            partes.append(", ".join(v.title() if campo in ("cidade", "bairro") else v for v in valores[:3]) + (" e outros" if len(valores) > 3 else ""))
    nota = max(f.aptidao_min or 0, f.encaixe_min or 0)
    if nota:
        partes.append(f"{'Potencial' if publico == 'dono' else 'Encaixe'} a partir de {nota}")
    if f.ia_min:
        partes.append(f"Nota da IA a partir de {f.ia_min}")
    if f.com_email:
        partes.append("Com e-mail")
    if f.pasta == "fora":
        partes.append("Fora de pastas")
    if f.busca.strip():
        partes.append(f"Busca “{f.busca.strip()}”")
    return "  ·  ".join(partes)


def _resposta_pdf(pdf: bytes, nome: str) -> Response:
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/api/listas/{id_}/pdf")
def pdf_da_lista(id_: str, f: Annotated[Filtros, Depends()], request: Request) -> Response:
    """Os contatos do filtro num PDF organizado: capa com o resumo e um cartão por contato."""
    cte, params = f.consulta(_parquet(id_))
    meta = _ler_meta(id_)
    con = duckdb.connect()
    total = con.execute(f"{cte} SELECT count(*) FROM r", params).fetchone()[0]
    if total > relatorio.MAX_CONTATOS:
        raise HTTPException(
            400,
            f"O PDF vai até {relatorio.MAX_CONTATOS:,} contatos e este filtro tem {total:,}. Use os filtros ou divida em pastas.".replace(",", "."),
        )
    if not total:
        raise HTTPException(400, "Nenhum contato neste filtro.")
    linhas = _linhas(con.execute(f"{cte} SELECT * EXCLUDE (dono_chave, ordem) FROM r ORDER BY {f.ordem}", params))
    if meta.get("palavras"):
        nome, regiao = " · ".join(p.title() for p in meta["palavras"]), meta["uf"] or "Brasil"
    elif meta.get("regiao"):
        nome, regiao = perfis.do_meta(meta)["nome"], meta["uf"] or "Brasil"
    else:
        nome, regiao = relatorio.titulo(meta["cidade"]), meta["uf"]
    pdf = relatorio.gerar_pdf(
        linhas, nome=nome, regiao=regiao, publico=_publico(meta), filtro=_descricao_do_filtro(f, _publico(meta)),
        perfil=perfis.do_meta(meta)["nome"],
    )
    pastas.registrar_exportacao("lista", id_, f"PDF da lista de {_rotulo(meta)}", len(linhas), _ip(request))
    return _resposta_pdf(pdf, f"garimpo-{pipeline.slug(meta['cidade'])[:40]}-{meta['uf'].lower() or 'br'}-{datetime.now():%Y%m%d-%H%M}.pdf")


@app.get("/api/pastas/{pasta_id}/pdf")
def pdf_da_pasta(pasta_id: int, request: Request) -> Response:
    pasta = pastas.uma(pasta_id)
    if not pasta:
        raise HTTPException(404, "Pasta não encontrada.")
    _, linhas = pastas.contatos(pasta_id, por_pagina=10**9)
    if not linhas:
        raise HTTPException(400, "A pasta está vazia.")
    if len(linhas) > relatorio.MAX_CONTATOS:
        raise HTTPException(
            400,
            f"O PDF vai até {relatorio.MAX_CONTATOS:,} contatos e esta pasta tem {len(linhas):,}. Divida em pastas menores.".replace(",", "."),
        )
    publico = "empresa" if all(l.get("publico") == "empresa" for l in linhas) else "dono"
    pdf = relatorio.gerar_pdf(linhas, nome=pasta["nome"], publico=publico, origem="Pasta de contatos")
    pastas.registrar_exportacao("pasta", str(pasta_id), f"PDF da pasta {pasta['nome']}", len(linhas), _ip(request))
    return _resposta_pdf(pdf, f"garimpo-pasta-{pipeline.slug(pasta['nome'])}-{datetime.now():%Y%m%d-%H%M}.pdf")


# ---------- pastas ----------

class NomeDaPasta(BaseModel):
    nome: str


def _pasta(pasta_id: int) -> dict:
    achada = pastas.uma(pasta_id)
    if not achada:
        raise HTTPException(404, "Pasta não encontrada.")
    return achada


@app.get("/api/pastas")
def listar_pastas() -> list[dict]:
    return pastas.listar()


@app.post("/api/pastas")
def criar_pasta(pedido: NomeDaPasta) -> dict:
    try:
        return {"id": pastas.criar(pedido.nome)}
    except pastas.PastaInvalida as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/pastas/{pasta_id}")
def uma_pasta(pasta_id: int) -> dict:
    return _pasta(pasta_id)


@app.patch("/api/pastas/{pasta_id}")
def renomear_pasta(pasta_id: int, pedido: NomeDaPasta) -> dict:
    _pasta(pasta_id)
    try:
        pastas.renomear(pasta_id, pedido.nome)
    except pastas.PastaInvalida as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.delete("/api/pastas/{pasta_id}")
def excluir_pasta(pasta_id: int) -> dict:
    _pasta(pasta_id)
    pastas.excluir(pasta_id)
    return {"ok": True}


@app.post("/api/pastas/{pasta_id}/adicionar")
def adicionar_a_pasta(
    pasta_id: int, lista_id: str, f: Annotated[Filtros, Depends()],
    quantidade: int = 100, pular_em_pastas: int = 1, telefone: Lista = None,
) -> dict:
    """Põe na pasta os N primeiros contatos do filtro atual (ou os telefones indicados)."""
    _pasta(pasta_id)
    meta = _ler_meta(lista_id)
    arquivo = _parquet(lista_id)
    con = duckdb.connect()
    if telefone:
        linhas = _linhas(
            con.execute(
                f"SELECT * EXCLUDE (dono_chave, ordem) FROM read_parquet('{arquivo}') WHERE telefone IN (SELECT unnest(?)) "
                "QUALIFY row_number() OVER (PARTITION BY telefone ORDER BY ordem) = 1",
                [telefone],
            )
        )
    else:
        cte, params = f.consulta(arquivo, "fora" if pular_em_pastas and not f.pasta else None)
        limite = f"LIMIT {int(quantidade)}" if quantidade > 0 else ""
        linhas = _linhas(con.execute(f"{cte} SELECT * EXCLUDE (dono_chave, ordem) FROM r ORDER BY {f.ordem} {limite}", params))
    for l in linhas:
        l["publico"] = _publico(meta)  # a pasta guarda de que tipo de lista o contato veio
    novos, ja = pastas.adicionar(pasta_id, linhas, lista_id, _rotulo(meta))
    return {"adicionados": novos, "ja_estavam": ja, "total_na_pasta": _pasta(pasta_id)["contatos"]}


@app.post("/api/pastas/dividir")
def dividir_em_pastas(
    pedido: NomeDaPasta, lista_id: str, f: Annotated[Filtros, Depends()],
    tamanho: int = 100, pastas_n: Annotated[int, Query(alias="pastas")] = 3, pular_em_pastas: int = 1,
) -> dict:
    """Cria várias pastas de uma vez ("Nome 1", "Nome 2"...), cada uma com `tamanho` contatos do filtro atual."""
    if not 1 <= tamanho <= 5000 or not 1 <= pastas_n <= 20:
        raise HTTPException(400, "Escolha de 1 a 20 pastas, com até 5.000 contatos cada.")
    base = " ".join(pedido.nome.split())
    if not base:
        raise HTTPException(400, "Dê um nome para as pastas.")
    nomes = [f"{base} {i}" for i in range(1, pastas_n + 1)]
    existentes = {p["nome"].lower() for p in pastas.listar()}
    repetidos = [n for n in nomes if n.lower() in existentes]
    if repetidos:
        raise HTTPException(400, f"Já existe uma pasta chamada {repetidos[0]}. Escolha outro nome.")
    meta = _ler_meta(lista_id)
    cte, params = f.consulta(_parquet(lista_id), "fora" if pular_em_pastas and not f.pasta else None)
    linhas = _linhas(
        duckdb.connect().execute(
            f"{cte} SELECT * EXCLUDE (dono_chave, ordem) FROM r ORDER BY {f.ordem} LIMIT {tamanho * pastas_n}", params
        )
    )
    for l in linhas:
        l["publico"] = _publico(meta)  # a pasta guarda de que tipo de lista o contato veio
    criadas, total = [], 0
    for i, nome in enumerate(nomes):
        parte = linhas[i * tamanho : (i + 1) * tamanho]
        if not parte:
            break  # acabaram os contatos: não cria pasta vazia
        pasta_id = pastas.criar(nome)
        novos, _ = pastas.adicionar(pasta_id, parte, lista_id, _rotulo(meta))
        criadas.append({"id": pasta_id, "nome": nome, "contatos": novos})
        total += novos
    return {"pastas": criadas, "adicionados": total}


@app.get("/api/pastas/{pasta_id}/contatos")
def contatos_da_pasta(pasta_id: int, busca: str = "", pagina: int = 1, por_pagina: int = 50) -> dict:
    _pasta(pasta_id)
    pagina, por_pagina = max(1, pagina), max(1, min(500, por_pagina))
    total, linhas = pastas.contatos(pasta_id, busca, pagina, por_pagina)
    onde = pastas.pastas_dos([l["telefone"] for l in linhas])
    for l in linhas:
        l["pastas"] = onde.get(l["telefone"], [])
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina, "linhas": linhas}


@app.delete("/api/pastas/{pasta_id}/contatos")
def remover_da_pasta(pasta_id: int, telefone: Lista = None) -> dict:
    _pasta(pasta_id)
    return {"removidos": pastas.remover(pasta_id, telefone or [])}


# ---------- resultados dos disparos ----------

class Resultado(BaseModel):
    resultado: str = ""
    telefones: list[str] = []
    texto: str = ""
    todos_sem_resultado: bool = False


def _refletir_no_whatsapp(resultado: str, telefones_e164: list[str]) -> None:
    """O resultado do disparo também diz se o número tem WhatsApp: não entregue = não tem; quem respondeu = tem."""
    nacionais = [t.removeprefix("+55") for t in telefones_e164]
    if not nacionais or resultado not in pastas.RESPONDEU | {"nao_entregue"}:
        return
    try:
        con = cnpj_db.conectar(tentativas=5)
    except duckdb.IOException:
        return  # base ocupada gerando lista; o resultado em si já ficou gravado na pasta
    try:
        whatsapp._garantir_cache(con)
        if resultado == "nao_entregue":
            # Quem já estava verificado só muda de "tem" para "não tem" (o tipo de conta fica guardado);
            # quem nunca foi verificado entra como "não tem".
            con.execute("UPDATE whatsapp_tipo SET tem = false, verificado_em = now() WHERE tel IN (SELECT unnest(?))", [nacionais])
            con.execute(
                "INSERT INTO whatsapp_tipo SELECT t, false, false, '', now() FROM (SELECT unnest(?) AS t) "
                "WHERE t NOT IN (SELECT tel FROM whatsapp_tipo)",
                [nacionais],
            )
        else:
            con.execute("UPDATE whatsapp_tipo SET tem = true, verificado_em = now() WHERE tel IN (SELECT unnest(?))", [nacionais])
    finally:
        con.close()
    _cache.pop("totais-whatsapp", None)


@app.get("/api/resultados/opcoes")
def opcoes_de_resultado() -> list[dict]:
    return [{"valor": v, "rotulo": r} for v, r in pastas.RESULTADOS]


@app.get("/api/resultados")
def resultados_gerais() -> dict:
    return pastas.resumo_resultados()


@app.get("/api/pastas/{pasta_id}/resultados")
def resultados_da_pasta(pasta_id: int) -> dict:
    _pasta(pasta_id)
    return pastas.resumo_resultados(pasta_id)


@app.post("/api/pastas/{pasta_id}/resultados")
def registrar_resultados(pasta_id: int, pedido: Resultado) -> dict:
    _pasta(pasta_id)
    brutos = pedido.telefones + [l for l in pedido.texto.splitlines() if l.strip()]
    if len(brutos) > 20_000:
        raise HTTPException(400, "Envie no máximo 20 mil números por vez.")
    validos = [t for t in (_e164(b) for b in brutos) if t]
    try:
        marcados, fora, alvo = pastas.marcar_resultado(pasta_id, pedido.resultado, validos, pedido.todos_sem_resultado)
    except pastas.PastaInvalida as e:
        raise HTTPException(400, str(e)) from e
    _refletir_no_whatsapp(pedido.resultado, alvo)
    return {"marcados": marcados, "nao_encontrados": fora + (len(brutos) - len(validos))}


@app.get("/api/exportacoes")
def exportacoes() -> list[dict]:
    return pastas.exportacoes_recentes()


@app.get("/api/pastas/{pasta_id}/exportar")
def exportar_pasta(pasta_id: int, request: Request) -> StreamingResponse:
    pasta = _pasta(pasta_id)
    _, linhas = pastas.contatos(pasta_id, por_pagina=10**9)
    pastas.registrar_exportacao("pasta", str(pasta_id), f"Pasta {pasta['nome']}", len(linhas), _ip(request))
    marca = f"pasta-{pipeline.slug(pasta['nome'])}"
    saida = []
    # Pasta só com contatos de listas de público "empresa" sai sem as colunas de energia solar.
    publico = "empresa" if linhas and all(l.get("publico") == "empresa" for l in linhas) else "dono"
    chaves = pipeline.chaves_do_publico(publico)
    for l in linhas:
        l["tags"] = ",".join(t for t in (l.get("tags") or "", marca) if t)
        saida.append([l.get(c, "") for c in chaves])
    return _csv(saida, f"garimpo-pasta-{pipeline.slug(pasta['nome'])}-{datetime.now():%Y%m%d-%H%M}.csv", publico)


# ---------- não contatar ----------

class NaoContatar(BaseModel):
    telefones: list[str] = []
    texto: str = ""
    motivo: str = ""


def _e164(valor: str) -> str | None:
    """Aceita +55..., 55..., (38) 9..., com ou sem o nono dígito; devolve +55DDDNÚMERO ou None."""
    digitos = so_digitos(valor)
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    nacional = normalizar(digitos)
    return e164(nacional) if nacional else None


@app.get("/api/nao-contatar")
def listar_nao_contatar() -> dict:
    total, itens = pastas.bloqueados_recentes()
    return {"total": total, "itens": itens}


@app.post("/api/nao-contatar")
def marcar_nao_contatar(pedido: NaoContatar) -> dict:
    brutos = pedido.telefones + [l for l in pedido.texto.splitlines() if l.strip()]
    if len(brutos) > 20_000:
        raise HTTPException(400, "Envie no máximo 20 mil números por vez.")
    validos = {t for t in (_e164(b) for b in brutos) if t}
    novos = pastas.bloquear(sorted(validos), pedido.motivo)
    return {"adicionados": novos, "invalidos": len(brutos) - len(validos), "total": pastas.bloqueados_recentes(0)[0]}


@app.delete("/api/nao-contatar")
def desmarcar_nao_contatar(telefone: str) -> dict:
    return {"removidos": pastas.desbloquear(_e164(telefone) or telefone)}


def _copias_diarias() -> None:
    """Uma cópia por dia do arquivo das pastas; é o único dado do sistema que não dá para gerar de novo."""
    while True:
        try:
            pastas.copia_de_seguranca(config.DADOS / "backups")
        except Exception:  # noqa: BLE001 - a cópia nunca pode derrubar o sistema
            pass
        time.sleep(24 * 3600)


threading.Thread(target=_copias_diarias, daemon=True).start()


# ---------- chaves de API (só com sessão: uma chave não cria nem revoga chaves) ----------

class NovaChave(BaseModel):
    nome: str


@app.get("/api/chaves")
def listar_chaves() -> list[dict]:
    return chaves.listar()


@app.post("/api/chaves")
def criar_chave(pedido: NovaChave, request: Request) -> dict:
    try:
        registro, chave = chaves.criar(pedido.nome)
    except chaves.ChaveInvalida as e:
        raise HTTPException(400, str(e)) from e
    pastas.registrar_acesso("chave criada", _ip(request))
    return {**registro, "chave": chave}  # a chave completa só aparece aqui, uma vez


@app.delete("/api/chaves/{chave_id}")
def revogar_chave(chave_id: int, request: Request) -> dict:
    if not chaves.revogar(chave_id):
        raise HTTPException(404, "Chave não encontrada ou já revogada.")
    pastas.registrar_acesso("chave revogada", _ip(request))
    return {"ok": True}


# ---------- API das automações (/api/v1) ----------
# Mesmas respostas das rotas que a página usa, num endereço estável e que aceita chave de API.
# Só leitura, criação de lista, criação de pasta e retirada de contatos: nada aqui apaga dados.

class Retirada(BaseModel):
    pasta: str  # nome da pasta onde os contatos ficam marcados; é criada se não existir
    quantidade: int = 100


_trava_retirada = threading.Lock()


def retirar(id_: str, pedido: Retirada, f: Annotated[Filtros, Depends()]) -> dict:
    """Entrega os próximos contatos da lista que ainda não estão em nenhuma pasta e já os guarda na pasta indicada.

    Chamar de novo traz os seguintes: quem já saiu não volta. Aceita os mesmos filtros de /contatos.
    """
    meta = _ler_meta(id_)
    if meta["status"] != "pronta":
        raise HTTPException(409, "A lista ainda não está pronta.")
    arquivo = _parquet(id_)
    quantidade = max(1, min(1000, pedido.quantidade))
    with _trava_retirada:  # duas retiradas ao mesmo tempo não podem entregar o mesmo contato
        nome = " ".join(pedido.pasta.split())
        achada = next((p for p in pastas.listar() if p["nome"].casefold() == nome.casefold()), None)
        try:
            pasta_id = achada["id"] if achada else pastas.criar(nome)
        except pastas.PastaInvalida as e:
            raise HTTPException(400, str(e)) from e
        cte, params = f.consulta(arquivo, "fora")
        con = duckdb.connect()
        linhas = _linhas(
            con.execute(f"{cte} SELECT * EXCLUDE (dono_chave, ordem) FROM r ORDER BY {f.ordem} LIMIT {quantidade}", params)
        )
        for l in linhas:
            l["publico"] = _publico(meta)
        pastas.adicionar(pasta_id, linhas, id_, _rotulo(meta))
        cte, params = f.consulta(arquivo, "fora")
        restantes = con.execute(f"{cte} SELECT count(*) FROM r", params).fetchone()[0]
    return {
        "pasta": {"id": pasta_id, "nome": achada["nome"] if achada else nome, "criada_agora": not achada},
        "retirados": len(linhas),
        "restantes": restantes,
        "linhas": linhas,
    }


def inicio_da_api(request: Request) -> dict:
    """Serve para testar a chave e ver o que a API oferece."""
    registro = getattr(request.state, "chave", None)
    return {
        "ok": True,
        "sistema": "Garimpo",
        "versao": "v1",
        "chave": registro["nome"] if registro else None,
        "limite_por_minuto": API_POR_MINUTO,
        "rotas": [{"metodo": m, "caminho": f"/api/v1{c}", "descricao": d} for m, c, _, d in _ROTAS_V1 if c != "/"],
    }


_ROTAS_V1 = [
    ("GET", "/", inicio_da_api, "Testa a chave e lista as rotas"),
    ("GET", "/base", base, "Situação da base: mês da Receita, ANEEL, WhatsApp verificados"),
    ("GET", "/cidades", cidades, "Busca de município pelo nome (q=)"),
    ("GET", "/atividades", atividades, "Busca de atividade econômica (q=)"),
    ("GET", "/campos", campos, "Campos que cada contato traz"),
    ("GET", "/publico", previa_do_publico, "Quantas empresas têm certas palavras no nome (palavra=, uf=)"),
    ("GET", "/perfis", listar_perfis, "Perfis de busca: os prontos (energia solar, sem perfil) e os salvos"),
    ("GET", "/ia", uso_da_ia, "Gasto de IA do mês, teto e modelos"),
    ("GET", "/perfis/{perfil_id}", um_perfil, "Um perfil de busca, com atividades, filtros e critério"),
    ("GET", "/listas", listar, "Todas as listas"),
    ("POST", "/listas", criar_lista, "Gera uma lista (cidade + uf, ou palavras; perfil= opcional); acompanhe o status em /listas/{id}"),
    ("GET", "/listas/{id_}", uma_lista, "Uma lista: status, etapa e resumo"),
    ("GET", "/listas/{id_}/filtros", opcoes_de_filtro, "Opções de cada filtro da lista, com contagem"),
    ("GET", "/listas/{id_}/contatos", contatos, "Contatos da lista, com filtros e paginação (até 500 por página)"),
    ("GET", "/listas/{id_}/exportar", exportar, "CSV da lista para o GHL, com os mesmos filtros"),
    ("GET", "/listas/{id_}/pdf", pdf_da_lista, "PDF organizado da lista, com os mesmos filtros (até 3.000 contatos)"),
    ("POST", "/listas/{id_}/retirar", retirar, "Entrega os próximos contatos fora de pastas e já os guarda numa pasta"),
    ("GET", "/pastas", listar_pastas, "Todas as pastas"),
    ("POST", "/pastas", criar_pasta, "Cria uma pasta"),
    ("GET", "/pastas/{pasta_id}", uma_pasta, "Uma pasta"),
    ("GET", "/pastas/{pasta_id}/contatos", contatos_da_pasta, "Contatos da pasta, com paginação"),
    ("POST", "/pastas/{pasta_id}/adicionar", adicionar_a_pasta, "Guarda na pasta os N primeiros contatos de uma lista (lista_id=, quantidade=)"),
    ("GET", "/pastas/{pasta_id}/exportar", exportar_pasta, "CSV da pasta para o GHL"),
    ("GET", "/pastas/{pasta_id}/pdf", pdf_da_pasta, "PDF organizado da pasta (até 3.000 contatos)"),
]
for _metodo, _caminho, _funcao, _descricao in _ROTAS_V1:
    app.add_api_route(f"/api/v1{_caminho}", _funcao, methods=[_metodo], summary=_descricao)

# O servidor MCP (/mcp): as mesmas funções da v1, como ferramentas para agentes de IA.
mcp_garimpo.montar(app, sys.modules[__name__])


@app.get("/")
def pagina() -> FileResponse:
    return FileResponse(AQUI / "index.html", headers={"Cache-Control": "no-store"})
