"""Núcleo da IA (API da Anthropic): cliente, custo de cada chamada, registro do gasto e teto mensal.

Duas funções usam a IA: a busca por descrição (`busca_ia.py`, modelo forte, poucas chamadas) e a
avaliação dos contatos (`avaliacao_ia.py`, modelo barato, muitas chamadas). Toda chamada fica
registrada na tabela `ia_uso` do pastas.sqlite, com tokens e custo; é dela que sai o gasto do mês.
Para a Anthropic vão só dados públicos de empresas: nunca telefone, e-mail, nome do dono ou CPF.
"""

from __future__ import annotations

import os
from contextlib import closing
from datetime import datetime

from . import pastas

# Modelos: trocáveis no .env sem mexer no código.
MODELO_BUSCA = os.getenv("GARIMPO_MODELO_BUSCA", "claude-opus-5-5")
MODELO_AVALIACAO = os.getenv("GARIMPO_MODELO_AVALIACAO", "claude-haiku-5-5")
TETO_PADRAO_USD = 20.0

# Preço por milhão de tokens (entrada, saída, leitura do cache), tabela de 03/11/2026.
# Gravar no cache custa 1,25 vez a entrada. Lote (Batches) custa metade de tudo.
PRECOS = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
}


class IaIndisponivel(RuntimeError):
    """A IA não pode atender: sem chave, teto atingido, recusa ou falha da API. A mensagem vai para a tela."""


def configurada() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def teto_usd() -> float:
    try:
        return max(0.0, float(os.getenv("GARIMPO_IA_TETO_USD", TETO_PADRAO_USD)))
    except ValueError:
        return TETO_PADRAO_USD


def cliente():
    """Cliente da API. Cada chamada tenta de novo sozinha em falhas passageiras (429, 5xx, rede)."""
    import anthropic

    chave = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not chave:
        raise IaIndisponivel("Falta a chave da Anthropic. Rode `uv run leads segredo ANTHROPIC_API_KEY` no servidor.")
    return anthropic.Anthropic(api_key=chave, max_retries=4, timeout=300)


def custo(modelo: str, entrada: int, saida: int, cache_lido: int = 0, cache_gravado: int = 0, lote: bool = False) -> float:
    """Custo em dólares de uma chamada. Modelo desconhecido custa como o mais caro da tabela de busca."""
    pe, ps, pc = PRECOS.get(modelo, PRECOS[MODELO_BUSCA] if MODELO_BUSCA in PRECOS else PRECOS["claude-opus-5-5"])
    total = (entrada * pe + saida * ps + cache_lido * pc + cache_gravado * pe * 1.25) / 1_000_000
    return round(total * (0.5 if lote else 1.0), 6)


def registrar(funcao: str, modelo: str, uso, ref: str = "", lote: bool = False) -> float:
    """Grava o uso de uma resposta da API (objeto `usage`) e devolve o custo."""
    entrada = int(getattr(uso, "input_tokens", 0) or 0)
    saida = int(getattr(uso, "output_tokens", 0) or 0)
    lido = int(getattr(uso, "cache_read_input_tokens", 0) or 0)
    gravado = int(getattr(uso, "cache_creation_input_tokens", 0) or 0)
    valor = custo(modelo, entrada, saida, lido, gravado, lote)
    with closing(pastas._con()) as con, con:
        con.execute(
            "INSERT INTO ia_uso (quando, funcao, modelo, entrada, saida, cache_lido, cache_gravado, custo_usd, ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [pastas._agora(), funcao, modelo, entrada, saida, lido, gravado, valor, ref],
        )
    return valor


def gasto_do_mes(agora: datetime | None = None) -> float:
    inicio = (agora or datetime.now()).strftime("%Y-%m-01")
    with closing(pastas._con()) as con:
        return float(con.execute("SELECT coalesce(sum(custo_usd), 0) FROM ia_uso WHERE quando >= ?", [inicio]).fetchone()[0])


def uso_do_mes(agora: datetime | None = None) -> dict:
    """Para a tela Base: gasto do mês por função, teto e chamadas."""
    inicio = (agora or datetime.now()).strftime("%Y-%m-01")
    with closing(pastas._con()) as con:
        linhas = con.execute(
            "SELECT funcao, count(*), sum(custo_usd) FROM ia_uso WHERE quando >= ? GROUP BY funcao ORDER BY 3 DESC", [inicio]
        ).fetchall()
    gasto = sum(float(l[2] or 0) for l in linhas)
    return {
        "configurada": configurada(), "teto_usd": teto_usd(), "gasto_usd": round(gasto, 4),
        "chamadas": sum(int(l[1]) for l in linhas),
        "por_funcao": [{"funcao": l[0], "chamadas": int(l[1]), "gasto_usd": round(float(l[2] or 0), 4)} for l in linhas],
        "modelos": {"busca": MODELO_BUSCA, "avaliacao": MODELO_AVALIACAO},
    }


def conferir_teto(estimativa_usd: float = 0.0) -> None:
    """Recusa antes de começar quando o gasto do mês mais a estimativa passaria do teto."""
    gasto, teto = gasto_do_mes(), teto_usd()
    if gasto + estimativa_usd > teto:
        raise IaIndisponivel(
            f"O teto de IA do mês é US$ {teto:.2f} e já foram gastos US$ {gasto:.2f}"
            + (f"; esta tarefa custaria cerca de US$ {estimativa_usd:.2f}" if estimativa_usd else "")
            + ". Aumente GARIMPO_IA_TETO_USD no .env ou espere o próximo mês."
        )


def erro_da_api(e: Exception) -> IaIndisponivel:
    """Traduz os erros do SDK numa frase para a tela, do mais específico ao mais geral."""
    import anthropic

    if isinstance(e, anthropic.AuthenticationError):
        return IaIndisponivel("A Anthropic recusou a chave. Confira ANTHROPIC_API_KEY.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return IaIndisponivel("A chave da Anthropic não tem permissão para este modelo.")
    if isinstance(e, anthropic.NotFoundError):
        return IaIndisponivel("Modelo não encontrado na Anthropic. Confira GARIMPO_MODELO_BUSCA e GARIMPO_MODELO_AVALIACAO.")
    if isinstance(e, anthropic.RateLimitError):
        return IaIndisponivel("A Anthropic pediu para esperar (limite de uso). Tente de novo em alguns minutos.")
    if isinstance(e, anthropic.BadRequestError):
        texto = getattr(e, "message", str(e))
        if "credit" in texto.lower() or "billing" in texto.lower():
            return IaIndisponivel("Os créditos da API da Anthropic acabaram. Recarregue no console da Anthropic.")
        return IaIndisponivel(f"A Anthropic recusou o pedido: {texto[:300]}")
    if isinstance(e, anthropic.APIStatusError):
        return IaIndisponivel(f"A API da Anthropic falhou ({e.status_code}). Tente de novo mais tarde.")
    if isinstance(e, anthropic.APIConnectionError):
        return IaIndisponivel("Não consegui falar com a API da Anthropic (rede).")
    return IaIndisponivel(f"Falha na IA: {type(e).__name__}: {e}")


def texto_da_resposta(resposta) -> str:
    return "".join(b.text for b in resposta.content if getattr(b, "type", "") == "text")
