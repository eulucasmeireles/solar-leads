"""Login OAuth do servidor MCP: a ferramenta abre uma página do Garimpo, o dono entra com a senha e autoriza.

É o jeito padrão de um cliente MCP (Claude, ChatGPT, agentes) ganhar acesso sem chave fixa:
cadastro automático do cliente (RFC 7591), autorização com PKCE e troca do código por um acesso.
Cada autorização é uma "conexão": um par de acesso (7 dias) e renovação (90 dias, trocada a cada uso),
que o dono vê e revoga na tela de chaves. Como nas chaves de API, só o SHA-256 fica guardado.
O acesso abre só o /mcp: nem a página, nem a /api/v1.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from contextlib import closing
from datetime import datetime

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import pastas

PREFIXO_ACESSO = "gma_"
PREFIXO_RENOVACAO = "gmr_"
VALIDADE_ACESSO_S = 7 * 24 * 3600
VALIDADE_RENOVACAO_S = 90 * 24 * 3600
VALIDADE_CODIGO_S = 5 * 60
VALIDADE_PEDIDO_S = 15 * 60  # tempo para a pessoa entrar com a senha e autorizar
MAX_CLIENTES = 200

_trava = threading.Lock()
_pedidos: dict[str, dict] = {}  # pedido de autorização esperando a pessoa -> cliente e parâmetros
_codigos: dict[str, AuthorizationCode] = {}  # código (uso único) -> autorização dada


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _limpar(guardados: dict, campo) -> None:
    agora = time.time()
    for k in [k for k, v in guardados.items() if campo(v) < agora]:
        guardados.pop(k, None)


# ---------- pedidos de autorização (a página do Garimpo) ----------

def novo_pedido(client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
    with _trava:
        _limpar(_pedidos, lambda p: p["expira"])
        pedido = secrets.token_urlsafe(24)
        _pedidos[pedido] = {"client": client, "params": params, "expira": time.time() + VALIDADE_PEDIDO_S}
    return pedido


def ver_pedido(pedido: str) -> dict | None:
    with _trava:
        _limpar(_pedidos, lambda p: p["expira"])
        return _pedidos.get(pedido)


def responder_pedido(pedido: str, permitir: bool) -> str:
    """Fecha o pedido e devolve o endereço de volta para a ferramenta: com o código, ou com a recusa."""
    with _trava:
        p = _pedidos.pop(pedido, None)
        if not p or p["expira"] < time.time():
            raise KeyError(pedido)
        params: AuthorizationParams = p["params"]
        if not permitir:
            return construct_redirect_uri(str(params.redirect_uri), error="access_denied",
                                          error_description="A pessoa recusou o acesso.", state=params.state)
        _limpar(_codigos, lambda c: c.expires_at)
        codigo = secrets.token_urlsafe(32)
        _codigos[codigo] = AuthorizationCode(
            code=codigo, scopes=params.scopes or [], expires_at=time.time() + VALIDADE_CODIGO_S,
            client_id=p["client"].client_id, code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=params.resource, subject="dono",
        )
    return construct_redirect_uri(str(params.redirect_uri), code=codigo, state=params.state)


def nome_do_cliente(client: OAuthClientInformationFull) -> str:
    return " ".join((client.client_name or "").split())[:60] or "Ferramenta sem nome"


# ---------- acessos ----------

def _emitir(client_id: str, escopos: list[str], recurso: str | None, conexao: str | None = None) -> OAuthToken:
    acesso, renovacao = PREFIXO_ACESSO + secrets.token_urlsafe(32), PREFIXO_RENOVACAO + secrets.token_urlsafe(32)
    conexao = conexao or secrets.token_hex(8)
    agora = int(time.time())
    with closing(pastas._con()) as con, con:
        for token, tipo, validade in ((acesso, "acesso", VALIDADE_ACESSO_S), (renovacao, "renovacao", VALIDADE_RENOVACAO_S)):
            con.execute(
                "INSERT INTO oauth_tokens (hash, tipo, client_id, conexao, escopos, recurso, expira, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_hash(token), tipo, client_id, conexao, " ".join(escopos), recurso or "", agora + validade, _agora()),
            )
    return OAuthToken(access_token=acesso, token_type="Bearer", expires_in=VALIDADE_ACESSO_S,
                      refresh_token=renovacao, scope=" ".join(escopos) or None)


def _ler(token: str, tipo: str) -> dict | None:
    if not token or len(token) > 200:
        return None
    with closing(pastas._con()) as con:
        linha = con.execute(
            "SELECT * FROM oauth_tokens WHERE hash = ? AND tipo = ? AND revogado_em = '' AND expira > ?",
            (_hash(token), tipo, int(time.time())),
        ).fetchone()
    return dict(linha) if linha else None


_usos: dict[str, float] = {}


def conferir(token: str) -> dict | None:
    """O acesso, se valer: para o middleware do /mcp. Anota o último uso no máximo uma vez por minuto."""
    if not token.startswith(PREFIXO_ACESSO):
        return None
    linha = _ler(token, "acesso")
    if not linha:
        return None
    if time.monotonic() - _usos.get(linha["conexao"], 0) > 60:
        _usos[linha["conexao"]] = time.monotonic()
        with closing(pastas._con()) as con, con:
            con.execute("UPDATE oauth_tokens SET ultimo_uso = ? WHERE conexao = ?", (_agora(), linha["conexao"]))
    with closing(pastas._con()) as con:
        nome = con.execute("SELECT nome FROM oauth_clientes WHERE client_id = ?", (linha["client_id"],)).fetchone()
    return {"id": f"oauth:{linha['conexao']}", "nome": nome[0] if nome else "MCP"}


def conexoes() -> list[dict]:
    """As ferramentas autorizadas pelo login do MCP, com a última vez que usaram (para a tela)."""
    with closing(pastas._con()) as con:
        linhas = con.execute(
            """SELECT t.conexao, c.nome, min(t.criado_em) criada_em, max(t.ultimo_uso) ultimo_uso, max(t.expira) expira
               FROM oauth_tokens t LEFT JOIN oauth_clientes c USING (client_id)
               WHERE t.revogado_em = '' AND t.expira > ? GROUP BY t.conexao, c.nome ORDER BY criada_em DESC""",
            (int(time.time()),),
        ).fetchall()
    return [{"id": l["conexao"], "nome": l["nome"] or "MCP", "criada_em": l["criada_em"], "ultimo_uso": l["ultimo_uso"]} for l in linhas]


def revogar_conexao(conexao: str) -> bool:
    with closing(pastas._con()) as con, con:
        n = con.execute("UPDATE oauth_tokens SET revogado_em = ? WHERE conexao = ? AND revogado_em = ''", (_agora(), conexao)).rowcount
    return n > 0


# ---------- o provedor que o SDK do MCP chama ----------

class Provedor:
    """Implementa `OAuthAuthorizationServerProvider` do SDK `mcp`. `url_da_pagina` monta o endereço da autorização."""

    def __init__(self, url_da_pagina) -> None:
        self.url_da_pagina = url_da_pagina

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with closing(pastas._con()) as con:
            linha = con.execute("SELECT dados FROM oauth_clientes WHERE client_id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(linha[0]) if linha else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with closing(pastas._con()) as con, con:
            # Cadastro é aberto (é o padrão do MCP); o limite só evita que alguém encha o arquivo.
            if con.execute("SELECT count(*) FROM oauth_clientes").fetchone()[0] >= MAX_CLIENTES:
                con.execute(
                    "DELETE FROM oauth_clientes WHERE client_id NOT IN (SELECT DISTINCT client_id FROM oauth_tokens WHERE revogado_em = '') "
                    "AND client_id IN (SELECT client_id FROM oauth_clientes ORDER BY criado_em LIMIT 50)"
                )
            con.execute(
                "INSERT OR REPLACE INTO oauth_clientes (client_id, nome, dados, criado_em) VALUES (?, ?, ?, ?)",
                (client_info.client_id, nome_do_cliente(client_info), client_info.model_dump_json(), _agora()),
            )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not client.client_id:
            raise AuthorizeError(error="unauthorized_client", error_description="Cliente sem identificação.")
        return self.url_da_pagina(novo_pedido(client, params))

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with _trava:
            codigo = _codigos.get(authorization_code)
        if not codigo or codigo.client_id != client.client_id or codigo.expires_at < time.time():
            return None
        return codigo

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        with _trava:
            if _codigos.pop(authorization_code.code, None) is None:  # uso único
                raise TokenError(error="invalid_grant", error_description="Código já usado ou vencido.")
        return _emitir(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        linha = _ler(refresh_token, "renovacao") if refresh_token.startswith(PREFIXO_RENOVACAO) else None
        if not linha or linha["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=linha["client_id"], scopes=linha["escopos"].split(),
                            expires_at=linha["expira"], resource=linha["recurso"] or None, subject="dono")

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        linha = _ler(refresh_token.token, "renovacao")
        if not linha:
            raise TokenError(error="invalid_grant", error_description="Renovação vencida ou revogada.")
        with closing(pastas._con()) as con, con:  # troca o par: o antigo deixa de valer
            con.execute("UPDATE oauth_tokens SET revogado_em = ? WHERE conexao = ? AND revogado_em = ''", (_agora(), linha["conexao"]))
        return _emitir(client.client_id, scopes or refresh_token.scopes, refresh_token.resource, conexao=linha["conexao"])

    async def load_access_token(self, token: str) -> AccessToken | None:
        linha = _ler(token, "acesso") if token.startswith(PREFIXO_ACESSO) else None
        if not linha:
            return None
        return AccessToken(token=token, client_id=linha["client_id"], scopes=linha["escopos"].split(),
                           expires_at=linha["expira"], resource=linha["recurso"] or None, subject="dono")

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        tipo = "acesso" if token.token.startswith(PREFIXO_ACESSO) else "renovacao"
        linha = _ler(token.token, tipo)
        if linha:
            revogar_conexao(linha["conexao"])
