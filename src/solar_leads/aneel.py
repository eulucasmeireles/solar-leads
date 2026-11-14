"""Registro de geração distribuída da ANEEL: quem já tem energia solar.

O portal da ANEEL só aceita acesso do Brasil. Se o servidor estiver fora, baixe
por um túnel: no computador local, `ssh -N -R 1080 <servidor>`, e aqui
`leads aneel baixar --proxy 127.0.0.1:1080`.

Empresas vêm com CNPJ completo. Pessoas físicas vêm com CPF mascarado (seis
dígitos do meio) e CEP só com cinco dígitos. O cruzamento com o dono, dentro do
mesmo município, acerta perto de 45% (medido com placebo em outras cidades):
é indício fraco. Juntar o CEP não melhora, porque a cidade tem poucos prefixos.
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path

import duckdb

from . import config

URL = (
    "https://dadosabertos.aneel.gov.br/dataset/5e0fafd2-21b9-4d5b-b622-40438d40aba2/resource/"
    "cd29f6eb-e08d-4db7-b6fb-ed6e3b682d27/download/empreendimento-geracao-distribuida.parquet"
)
ARQUIVO = config.DADOS / "aneel" / "gd.parquet"
_SO_ALFANUM = "[^A-Z0-9]"


def chave_municipio(nome: str) -> str:
    """Nome do município sem acento, espaço ou pontuação, para casar ANEEL e Receita."""
    t = unicodedata.normalize("NFKD", (nome or "").upper())
    return re.sub(_SO_ALFANUM, "", "".join(c for c in t if not unicodedata.combining(c)))


def baixar(proxy: str | None = None) -> Path:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    parcial = ARQUIVO.with_suffix(".part")
    cmd = ["curl", "-sS", "--fail", "-m", "1800", "-C", "-", "-A", "Mozilla/5.0", "-o", str(parcial), URL]
    if proxy:
        cmd[1:1] = ["--socks5-hostname", proxy]
    subprocess.run(cmd, check=True)
    # Só troca o arquivo em uso depois de conferir que o novo abre.
    duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{parcial}')").fetchone()
    parcial.replace(ARQUIVO)
    return ARQUIVO


def preparar(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Resume o arquivo em duas tabelas: sistemas por CNPJ e por (município, CPF mascarado)."""
    base = f"read_parquet('{ARQUIVO}')"
    solar = "DscFonteGeracao = 'Radiação solar'"
    desde = "min(CASE WHEN DthAtualizaCadastralEmpreend >= DATE '2008-01-01' THEN DthAtualizaCadastralEmpreend END)"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE gd_pj AS
        SELECT NumCPFCNPJ AS cnpj, substr(NumCPFCNPJ, 1, 8) AS cnpj_basico, {desde} AS desde,
               round(sum(MdaPotenciaInstaladaKW), 1) AS kw, count(*) AS sistemas,
               array_to_string(list_distinct(list(NomMunicipio || '/' || SigUF))[:4], ', ') AS onde
        FROM {base} WHERE SigTipoConsumidor = 'PJ' AND {solar} AND length(NumCPFCNPJ) = 14
        GROUP BY 1
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE gd_pf AS
        SELECT SigUF AS uf, regexp_replace(upper(strip_accents(NomMunicipio)), '{_SO_ALFANUM}', '', 'g') AS municipio,
               regexp_replace(NumCPFCNPJ, '\\D', '', 'g') AS cpf6, {desde} AS desde,
               round(sum(MdaPotenciaInstaladaKW), 1) AS kw, count(*) AS sistemas
        FROM {base} WHERE SigTipoConsumidor = 'PF' AND {solar}
        GROUP BY 1, 2, 3
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE gd_cep AS
        SELECT SigUF AS uf, regexp_replace(upper(strip_accents(NomMunicipio)), '{_SO_ALFANUM}', '', 'g') AS municipio,
               substr(regexp_replace(CodCEP, '\\D', '', 'g'), 1, 5) AS cep5, count(*) AS residenciais
        FROM {base} WHERE SigTipoConsumidor = 'PF' AND {solar} AND DscClasseConsumo = 'Residencial'
        GROUP BY 1, 2, 3
        """
    )
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("gd_pj", "gd_pf", "gd_cep")}


def regioes_solares(con: duckdb.DuckDBPyConnection, cidade: str, uf: str, empresas_por_cep: dict[str, int]) -> set[str]:
    """Prefixos de CEP (5 dígitos) onde a energia solar em casas é mais comum.

    Contar sistemas só mostraria onde mora mais gente, então a medida é sistemas residenciais
    por empresa ativa no mesmo prefixo (o que temos de mais parecido com o tamanho da região).
    Vale o terço de cima, e só quando a cidade tem prefixos suficientes para comparar.
    """
    try:
        linhas = con.execute(
            "SELECT cep5, residenciais FROM gd_cep WHERE uf = ? AND municipio = ? AND length(cep5) = 5",
            [uf.upper(), chave_municipio(cidade)],
        ).fetchall()
    except duckdb.CatalogException:
        return set()
    taxas = sorted(
        ((res / empresas_por_cep[cep], cep) for cep, res in linhas if empresas_por_cep.get(cep, 0) >= 30), reverse=True
    )
    if len(taxas) < 9:
        return set()
    return {cep for _, cep in taxas[: len(taxas) // 3]}


def consultar(
    con: duckdb.DuckDBPyConnection, cnpjs: list[str], cpf6s: list[str], cidade: str, uf: str
) -> tuple[dict[str, tuple], dict[str, tuple], dict[str, tuple]]:
    """({cnpj: (desde, kw, sistemas, onde)}, {cnpj_basico: ...}, {cpf6: ...}).

    Devolve (None, None, None) se a base da ANEEL não foi carregada: quem chama deve dizer
    "não consultado", e não "sem registro".
    """
    try:
        pj = {
            c: (d, k, n, o)
            for c, d, k, n, o in con.execute(
                "SELECT cnpj, desde, kw, sistemas, onde FROM gd_pj WHERE cnpj IN (SELECT unnest(?))", [cnpjs]
            ).fetchall()
        }
        grupo = {
            c: (d, k, n, o)
            for c, d, k, n, o in con.execute(
                "SELECT cnpj_basico, min(desde), round(sum(kw), 1), sum(sistemas), "
                "array_to_string(list_distinct(list(onde))[:3], ', ') FROM gd_pj "
                "WHERE cnpj_basico IN (SELECT unnest(?)) GROUP BY 1",
                [sorted({c[:8] for c in cnpjs})],
            ).fetchall()
        }
        pf = {
            c: (d, k, n)
            for c, d, k, n in con.execute(
                "SELECT cpf6, desde, kw, sistemas FROM gd_pf WHERE uf = ? AND municipio = ? AND cpf6 IN (SELECT unnest(?))",
                [uf.upper(), chave_municipio(cidade), cpf6s],
            ).fetchall()
        }
    except duckdb.CatalogException:
        return None, None, None
    return pj, grupo, pf
