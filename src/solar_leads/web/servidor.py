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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import aneel, cnpj_db, config, enriquecer, pastas, pipeline, whatsapp
from ..telefone import e164, normalizar, so_digitos

AQUI = Path(__file__).parent
LISTAS = config.DADOS / "listas"
MUNICIPIOS = config.DADOS / "municipios_uf.parquet"
LISTAS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Solar Leads", docs_url=None, redoc_url=None)


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
_verificacao = {"thread": None, "parar": threading.Event(), "lista_id": None, "feitos_hoje": 0, "restantes": None, "mensagem": ""}


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


@app.middleware("http")
async def exigir_sessao(request: Request, call_next):
    caminho = request.scope.get("path", "")
    # Caminho com ".." ou barra invertida não é de ninguém legítimo: sem isso, /static/fonts/../x
    # passava como "fonte" e era servido sem sessão.
    if ".." in caminho.split("/") or "\\" in caminho or "%" in caminho:
        return JSONResponse({"detail": "Endereço inválido."}, status_code=404)
    fonte = caminho.startswith("/static/fonts/") and caminho.endswith(".woff2") and caminho.count("/") == 3
    livre = caminho in ("/login", "/api/login", "/api/sessao") or fonte
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
        _tentativas[ip] = [0, time.time() + BLOQUEIO_S] if erros >= MAX_ERROS else [erros, 0.0]
        raise HTTPException(401, "Senha incorreta.")
    _tentativas.pop(ip, None)
    validade, marca = int(time.time()) + VALIDADE_S, os.urandom(8).hex()
    resposta = JSONResponse({"ok": True})
    resposta.set_cookie(
        COOKIE, f"{validade}.{marca}.{_assinar(validade, marca)}", max_age=VALIDADE_S,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return resposta


@app.post("/api/logout")
def sair() -> JSONResponse:
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


def _parquet(id_: str) -> Path:
    meta = _ler_meta(id_)
    if meta["status"] != "pronta":
        raise HTTPException(409, "A lista ainda não está pronta.")
    return LISTAS / f"{id_}.parquet"


# Uma geração interrompida por reinício do servidor não volta sozinha.
for _arq in LISTAS.glob("*.json"):
    _m = json.loads(_arq.read_text())
    if _m.get("status") in ("gerando", "na_fila"):
        _m.update(status="erro", erro="A geração foi interrompida. Gere a lista de novo.")
        _gravar_meta(_m)


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
    return [{"chave": c, "rotulo": r, "grupo": g, "tipo": t} for c, _, r, g, t in pipeline.CAMPOS]


def _estado_whatsapp() -> dict:
    try:
        st = {k.lower(): v for k, v in whatsapp.status().items()}
        ok = bool(st.get("connected")) and bool(st.get("loggedin"))
        return {"disponivel": ok, "motivo": "" if ok else "Nenhum WhatsApp conectado ao validador.", "conta": str(st.get("name") or "")}
    except whatsapp.WhatsAppIndisponivel as e:
        return {"disponivel": False, "motivo": str(e), "conta": ""}


@app.get("/api/whatsapp")
def estado_whatsapp() -> dict:
    t = _verificacao["thread"]
    return {
        **_em_cache("estado-whatsapp", 3, _estado_whatsapp),
        "totais": _em_cache("totais-whatsapp", 20, _totais_whatsapp),
        "verificacao": {
            "rodando": bool(t and t.is_alive()),
            "lista_id": _verificacao["lista_id"],
            "feitos_hoje": _verificacao["feitos_hoje"],
            "restantes": _verificacao["restantes"],
            "mensagem": _verificacao["mensagem"],
        },
    }


@app.post("/api/whatsapp/qr")
def qr_whatsapp() -> dict:
    try:
        return {"qr": whatsapp.qr_code()}
    except whatsapp.WhatsAppIndisponivel as e:
        raise HTTPException(409, str(e)) from e


class PedidoVerificacao(BaseModel):
    lista_id: str


@app.post("/api/whatsapp/verificacao")
def iniciar_verificacao(pedido: PedidoVerificacao) -> dict:
    t = _verificacao["thread"]
    if t and t.is_alive():
        raise HTTPException(409, "Já existe uma verificação em andamento.")
    if not _estado_whatsapp()["disponivel"]:
        raise HTTPException(409, "Conecte um WhatsApp antes de iniciar a verificação.")
    arquivo = _parquet(pedido.lista_id)
    fila = [
        tel[0].removeprefix("+55")
        for tel in duckdb.connect().execute(
            f"SELECT telefone FROM read_parquet('{arquivo}') WHERE faixa IN ('A', 'B') AND telefone <> '' ORDER BY ordem"
        ).fetchall()
    ]

    def avisar(texto: str, restantes: int | None, hoje: int | None) -> None:
        _verificacao["mensagem"] = texto
        _verificacao["restantes"] = restantes
        if hoje is not None:
            _verificacao["feitos_hoje"] = hoje

    def rodar() -> None:
        try:
            _verificacao["mensagem"] = whatsapp.verificar_devagar(fila, parar=_verificacao["parar"], avisar=avisar)
        except Exception as e:  # noqa: BLE001 - o motivo aparece na tela
            _verificacao["mensagem"] = f"Parou com erro: {type(e).__name__}: {e}"
        _cache.pop("totais-whatsapp", None)

    _verificacao["parar"] = threading.Event()
    _verificacao.update(lista_id=pedido.lista_id, restantes=len(fila), mensagem="Iniciando.")
    _verificacao["thread"] = threading.Thread(target=rodar, daemon=True)
    _verificacao["thread"].start()
    return {"ok": True}


@app.delete("/api/whatsapp/verificacao")
def parar_verificacao() -> dict:
    _verificacao["parar"].set()
    _verificacao["mensagem"] = "Parando."
    return {"ok": True}


# ---------- geração ----------

class NovaLista(BaseModel):
    uf: str
    cidade: str
    cnaes: list[str] = []
    so_ativas: bool = False
    incluir_mei: bool = False
    so_locais: bool = True
    validar_whatsapp: bool = False


def _gerar(meta: dict, codigo: str) -> None:
    def etapa(texto: str) -> None:
        meta["etapa"] = texto
        _gravar_meta(meta)

    try:
        meta["status"] = "gerando"
        p = meta["parametros"]
        con = cnpj_db.conectar()
        try:
            leads = enriquecer.enriquecer(
                con, [], codigo, meta["uf"], cidade=meta["cidade"].title(),
                cnaes=[a["codigo"] for a in meta["atividades"]] or [""],
                so_ativas=p["so_ativas"], incluir_mei=p["incluir_mei"], so_locais=p.get("so_locais", True),
                progresso=etapa,
            )
            etapa("Aproveitando os WhatsApp já verificados")
            # Sempre aproveita o que já foi verificado; consulta o resto só quando pedido.
            celulares = [t["tel"] for l in leads for t in l["telefones"] if t["faixa"] != "C"]
            tem_whatsapp = whatsapp.do_cache(con, celulares)
            if p["validar_whatsapp"]:
                try:
                    tem_whatsapp = whatsapp.checar(con, celulares)
                except whatsapp.WhatsAppIndisponivel as e:
                    meta["aviso"] = f"WhatsApp não verificado por completo. {e}"
        finally:
            con.close()
        etapa("Montando a lista")
        linhas, disparo = pipeline.montar(leads, meta["cidade"], tem_whatsapp)
        pipeline.salvar_parquet(linhas, LISTAS / f"{meta['id']}.parquet")
        meta["resumo"] = pipeline.resumir(linhas, disparo, whatsapp_verificado=bool(tem_whatsapp))
        # Os números do cartão saem da mesma consulta que a tela usa, para os dois baterem.
        cte, params = _consulta(LISTAS / f"{meta['id']}.parquet", {}, None, 0, 1, "")
        n = duckdb.connect().execute(
            f"""{cte} SELECT count(*), count(*) FILTER (WHERE faixa = 'A'), count(*) FILTER (WHERE faixa = 'B'),
                count(*) FILTER (WHERE primeiro_nome <> ''), count(*) FILTER (WHERE email <> '') FROM r""",
            params,
        ).fetchone()
        meta["resumo"].update(contatos_para_disparo=n[0], faixa_A=n[1], faixa_B=n[2], com_nome=n[3], com_email=n[4])
        meta.update(status="pronta", etapa=None)
    except Exception as e:  # noqa: BLE001 - o erro vai para a tela em vez de sumir na thread
        meta.update(status="erro", etapa=None, erro=f"{type(e).__name__}: {e}")
    finally:
        _gravar_meta(meta)


def _atender_fila() -> None:
    while True:
        meta, codigo = _fila.get()
        if _meta_caminho(meta["id"]).exists():  # pode ter sido excluída enquanto esperava
            _gerar(meta, codigo)


threading.Thread(target=_atender_fila, daemon=True).start()


@app.post("/api/listas")
def criar_lista(pedido: NovaLista) -> dict:
    con = duckdb.connect()
    achado = con.execute(
        f"SELECT codigo, exibicao, empresas FROM read_parquet('{_municipios()}') "
        "WHERE uf = ? AND nome = strip_accents(upper(?))",
        [pedido.uf.upper(), pedido.cidade.strip()],
    ).fetchone()
    if not achado:
        raise HTTPException(404, "Cidade não encontrada na base.")
    cnaes = sorted({"".join(c for c in x if c.isdigit()) for x in pedido.cnaes} - {""})
    if achado[2] > LIMITE_SEM_ATIVIDADE and not cnaes:
        raise HTTPException(
            400,
            f"{achado[1].title()} tem {achado[2]:,} empresas".replace(",", ".")
            + ", muito para uma lista só. Escolha as atividades que interessam e gere por partes.",
        )
    descricoes = dict(
        con.execute(
            f"SELECT trim(codigo), descricao FROM read_parquet('{config.CNPJ_PARQUET}/cnaes_*.parquet') "
            "WHERE trim(codigo) IN (SELECT unnest(?))",
            [cnaes],
        ).fetchall()
    )
    agora = datetime.now()
    id_ = f"{agora:%Y%m%d-%H%M%S}-{pipeline.slug(achado[1])}-{pedido.uf.lower()}"
    while (LISTAS / f"{id_}.json").exists():
        id_ += "b"
    meta = {
        "id": id_,
        "criada_em": agora.isoformat(timespec="seconds"),
        "cidade": achado[1],
        "uf": pedido.uf.upper(),
        "atividades": [{"codigo": c, "descricao": descricoes.get(c, c)} for c in cnaes],
        "parametros": {
            "so_ativas": pedido.so_ativas, "incluir_mei": pedido.incluir_mei, "so_locais": pedido.so_locais,
            "validar_whatsapp": pedido.validar_whatsapp,
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
    return sorted(metas, key=lambda m: m["criada_em"], reverse=True)


@app.get("/api/listas/{id_}")
def uma_lista(id_: str) -> dict:
    return _ler_meta(id_)


@app.delete("/api/listas/{id_}")
def excluir(id_: str) -> dict:
    meta = _ler_meta(id_)
    if meta["status"] == "gerando":
        raise HTTPException(409, "Espere a geração terminar para excluir.")
    _meta_caminho(id_).unlink()
    (LISTAS / f"{id_}.parquet").unlink(missing_ok=True)
    return {"ok": True}


# ---------- consulta ----------

Lista = Annotated[list[str] | None, Query()]
_FILTROS_IN = ("faixa", "situacao", "papel", "bairro", "atividade", "tipo_whatsapp", "solar", "oportunidade")
_ORDENS = {
    "relevancia": "ordem",
    "aptidao": "aptidao_solar DESC, ordem",
    "chance": "chance_dono DESC, ordem",
    "nome": "primeiro_nome, sobrenome, ordem",
    "empresa": "empresa, ordem",
}


def _consulta(
    arquivo: Path, filtros: dict, aptidao_min: int | None, com_email: int, so_disparo: int, busca: str, pasta: str | None = None
):
    """Monta o SQL (CTE `r` com as linhas do filtro) e seus parâmetros.

    `pasta`: "fora" deixa só quem não está em nenhuma pasta; um id deixa só quem está naquela.
    """
    cond, params = ["true"], []
    for campo in _FILTROS_IN:
        valores = filtros.get(campo)
        if valores:
            cond.append(f"{campo} IN (SELECT unnest(?))")
            params.append(valores)
    if aptidao_min:
        cond.append("aptidao_solar >= ?")
        params.append(aptidao_min)
    if com_email:
        cond.append("email <> ''")
    if busca.strip():
        cond.append(
            "strip_accents(lower(concat_ws(' ', primeiro_nome, sobrenome, empresa, razao_social, cnpj, telefone, bairro))) "
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
        aptidao_min: int | None = None, com_email: int = 0, so_disparo: int = 1, busca: str = "",
        ordem: str = "relevancia", pasta: str | None = None,
    ) -> None:
        self.em = {
            "faixa": faixa, "situacao": situacao, "papel": papel, "bairro": bairro, "atividade": atividade,
            "tipo_whatsapp": tipo_whatsapp, "solar": solar, "oportunidade": oportunidade,
        }
        self.aptidao_min, self.com_email, self.so_disparo, self.busca = aptidao_min, com_email, so_disparo, busca
        self.ordem = _ORDENS.get(ordem, "ordem")
        self.pasta = pasta

    def consulta(self, arquivo: Path, pasta: str | None = None):
        return _consulta(arquivo, self.em, self.aptidao_min, self.com_email, self.so_disparo, self.busca, pasta or self.pasta)


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
        limite = 400 if campo in ("bairro", "atividade") else 50
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
        "aptidao": grupo("aptidao_solar", ordem="1"),
        "atividades": grupo("atividade", "atividade <> ''", limite=10),
        "bairros": grupo("bairro", "bairro <> ''", limite=10),
        "solar_por_ano": grupo(
            "substr(solar_desde, 1, 4)", "solar IN ('empresa já tem', 'outra unidade da empresa tem') AND solar_desde <> ''", ordem="1"
        ),
    }


def _csv(linhas: list[list], nome: str) -> StreamingResponse:
    saida = io.StringIO()
    w = csv.writer(saida)
    w.writerow(pipeline.CABECALHOS)
    w.writerows([[pipeline.celula_segura(v) for v in linha] for linha in linhas])
    return StreamingResponse(
        iter(["﻿" + saida.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/api/listas/{id_}/exportar")
def exportar(id_: str, f: Annotated[Filtros, Depends()], request: Request) -> StreamingResponse:
    cte, params = f.consulta(_parquet(id_))
    sel = ", ".join(pipeline.CHAVES)
    linhas = duckdb.connect().execute(f"{cte} SELECT {sel} FROM r ORDER BY {f.ordem}", params).fetchall()
    meta = _ler_meta(id_)
    pastas.registrar_exportacao("lista", id_, f"Lista de {meta['cidade'].title()}/{meta['uf']}", len(linhas), _ip(request))
    return _csv(linhas, f"solar-leads-{pipeline.slug(meta['cidade'])}-{meta['uf'].lower()}-{datetime.now():%Y%m%d-%H%M}.csv")


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
    novos, ja = pastas.adicionar(pasta_id, linhas, lista_id, f"{meta['cidade'].title()}/{meta['uf']}")
    return {"adicionados": novos, "ja_estavam": ja, "total_na_pasta": _pasta(pasta_id)["contatos"]}


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
    for l in linhas:
        l["tags"] = ",".join(t for t in (l.get("tags") or "", marca) if t)
        saida.append([l.get(c, "") for c in pipeline.CHAVES])
    return _csv(saida, f"solar-leads-pasta-{pipeline.slug(pasta['nome'])}-{datetime.now():%Y%m%d-%H%M}.csv")


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


@app.get("/")
def pagina() -> FileResponse:
    return FileResponse(AQUI / "index.html", headers={"Cache-Control": "no-store"})
