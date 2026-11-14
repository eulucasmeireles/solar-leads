"""Banco DuckDB sobre os Parquet da base de CNPJ, com as tabelas derivadas.

Os Parquet ficam em dados/cnpj/<tabela>_*.parquet (gerados por cnpj_carga.py).
`preparar()` cria visões sobre eles e materializa o que o cruzamento precisa:
telefones normalizados por empresa, frequência de cada telefone e sócios PF.
"""

from __future__ import annotations

import time

import duckdb

from . import config

TABELAS = ("estabelecimentos", "empresas", "socios", "simples", "municipios", "cnaes", "qualificacoes", "naturezas")

# Normaliza ddd + telefone do cadastro para o número nacional (10 ou 11 dígitos).
_TEL_SQL = """
CASE
  WHEN length({n}) = 8 AND {n}[1] IN ('6','7','8','9') THEN {d} || '9' || {n}
  WHEN length({n}) IN (8, 9) THEN {d} || {n}
END
"""


def _tel(ddd: str, numero: str) -> str:
    d = f"right(ltrim(regexp_replace(coalesce({ddd}, ''), '\\D', '', 'g'), '0'), 2)"
    n = f"regexp_replace(coalesce({numero}, ''), '\\D', '', 'g')"
    return f"(CASE WHEN length({d}) = 2 THEN {_TEL_SQL.format(n=n, d=d)} END)"


def conectar(somente_leitura: bool = False, tentativas: int = 40) -> duckdb.DuckDBPyConnection:
    config.DADOS.mkdir(parents=True, exist_ok=True)
    # O verificador de WhatsApp abre a base por instantes; quem chegar junto espera a vez.
    for tentativa in range(tentativas):
        try:
            con = duckdb.connect(str(config.BANCO), read_only=somente_leitura)
            break
        except duckdb.IOException:
            if tentativa == tentativas - 1:
                raise
            time.sleep(1.5)
    con.execute("SET memory_limit = '12GB'")
    con.execute("SET threads = 4")
    con.execute(f"SET temp_directory = '{config.DADOS / 'tmp'}'")
    return con


def preparar(con: duckdb.DuckDBPyConnection) -> None:
    for t in TABELAS:
        padrao = config.CNPJ_PARQUET / f"{t}_*.parquet"
        con.execute(f"CREATE OR REPLACE VIEW {t} AS SELECT * FROM read_parquet('{padrao}')")

    con.execute(
        f"""
        CREATE OR REPLACE TABLE tel_estab AS
        SELECT DISTINCT tel, cnpj_basico, cnpj, situacao_cadastral
        FROM (
          SELECT {_tel('ddd_1', 'telefone_1')} AS tel, cnpj_basico,
                 cnpj_basico || cnpj_ordem || cnpj_dv AS cnpj, situacao_cadastral
          FROM estabelecimentos
          UNION ALL
          SELECT {_tel('ddd_2', 'telefone_2')}, cnpj_basico,
                 cnpj_basico || cnpj_ordem || cnpj_dv, situacao_cadastral
          FROM estabelecimentos
        )
        WHERE tel IS NOT NULL
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE tel_freq AS
        SELECT tel, count(DISTINCT cnpj_basico) AS n_empresas
        FROM tel_estab GROUP BY tel
        """
    )
    # Sócios pessoa física; a chave une a mesma pessoa em várias empresas
    # (nome + os seis dígitos visíveis do CPF mascarado).
    con.execute(
        """
        CREATE OR REPLACE TABLE socio_pf AS
        SELECT cnpj_basico,
               upper(trim(nome_socio)) || '|' || coalesce(cnpj_cpf_socio, '') AS chave,
               upper(trim(nome_socio)) AS nome,
               lpad(trim(qualificacao_socio), 2, '0') AS qualificacao,
               data_entrada_sociedade, trim(faixa_etaria) AS faixa_etaria
        FROM socios
        WHERE trim(identificador_socio) = '2' AND nome_socio IS NOT NULL
        """
    )
    preparar_emails(con)


def tem_tabela(con: duckdb.DuckDBPyConnection, nome: str) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [nome]).fetchone())


def _tirar_repetidos(con: duckdb.DuckDBPyConnection, tabela: str, chave: str) -> int:
    """Deixa uma linha por chave; devolve quantas saíram."""
    antes = con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
    con.execute(
        f"""
        DELETE FROM {tabela} WHERE rowid IN (
          SELECT rowid FROM (
            SELECT rowid, row_number() OVER (PARTITION BY {chave} ORDER BY rowid) AS n FROM {tabela}
            WHERE {chave} IN (SELECT {chave} FROM {tabela} GROUP BY 1 HAVING count(*) > 1)
          ) WHERE n > 1)
        """
    )
    return antes - con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


def preparar_cidades(con: duckdb.DuckDBPyConnection, avisar=print) -> None:
    """Tabelas nacionais já prontas para a geração por cidade.

    `empresa_resumo`: uma linha por empresa, com MEI/Simples e quantas unidades ativas tem.
    `estab_cidade`: todo estabelecimento com os dados da empresa, gravado por UF e município,
    para o filtro por cidade ler só o trecho dela em vez do país inteiro.
    """
    ativa = "lpad(trim(situacao_cadastral), 2, '0') = '02'"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE empresa_resumo AS
        SELECT em.cnpj_basico, em.razao_social, em.natureza_juridica, nat.descricao AS natureza, em.porte,
               em.capital_social, s.opcao_mei, s.data_exclusao_mei, s.opcao_simples,
               coalesce(u.ativas, 0) AS unidades_ativas, coalesce(u.matriz_ativa, false) AS matriz_ativa
        FROM empresas em
        LEFT JOIN naturezas nat ON nat.codigo = em.natureza_juridica
        LEFT JOIN (SELECT cnpj_basico, any_value(opcao_mei) AS opcao_mei, any_value(data_exclusao_mei) AS data_exclusao_mei,
                          any_value(opcao_simples) AS opcao_simples FROM simples GROUP BY 1) s USING (cnpj_basico)
        LEFT JOIN (SELECT cnpj_basico, count(*) FILTER (WHERE {ativa}) AS ativas,
                          bool_or(matriz_filial = '1' AND {ativa}) AS matriz_ativa
                   FROM estabelecimentos GROUP BY 1) u USING (cnpj_basico)
        """
    )
    _tirar_repetidos(con, "empresa_resumo", "cnpj_basico")  # a Receita repete algumas dezenas de empresas
    avisar("empresa_resumo pronta")
    tel1, tel2 = _tel("e.ddd_1", "e.telefone_1"), _tel("e.ddd_2", "e.telefone_2")
    selecao = f"""
        SELECT e.uf, e.municipio, e.cnpj_basico, e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj, e.matriz_filial,
               e.nome_fantasia, em.razao_social, em.natureza_juridica, em.porte, em.capital_social,
               e.cnae_fiscal, c.descricao AS cnae_descricao, e.data_inicio_atividade,
               trim(coalesce(e.tipo_logradouro, '') || ' ' || coalesce(e.logradouro, '')) AS logradouro,
               trim(e.numero) AS numero, e.complemento, e.bairro, e.cep,
               lower(e.correio_eletronico) AS email, {tel1} AS tel1, {tel2} AS tel2,
               (coalesce(em.opcao_mei, 'N') = 'S' OR (
                  lpad(trim(e.situacao_cadastral), 2, '0') <> '02'
                  AND trim(coalesce(em.data_exclusao_mei, '')) NOT IN ('', '00000000')
                  AND trim(em.data_exclusao_mei) >= trim(e.data_situacao_cadastral))) AS mei,
               lpad(trim(e.situacao_cadastral), 2, '0') AS situacao, e.data_situacao_cadastral AS data_situacao,
               em.natureza, coalesce(em.opcao_simples, 'N') = 'S' AS simples,
               CASE WHEN trim(coalesce(e.cnae_fiscal_secundaria, '')) = '' THEN 0
                    ELSE len(string_split(e.cnae_fiscal_secundaria, ',')) END AS secundarias
        FROM estabelecimentos e
        LEFT JOIN empresa_resumo em USING (cnpj_basico)
        LEFT JOIN cnaes c ON c.codigo = e.cnae_fiscal
    """
    con.execute(f"CREATE OR REPLACE TABLE estab_cidade_nova AS {selecao} LIMIT 0")
    ufs = [u[0] for u in con.execute("SELECT DISTINCT uf FROM estabelecimentos ORDER BY 1").fetchall()]
    for uf in ufs:
        con.execute(f"INSERT INTO estab_cidade_nova {selecao} WHERE e.uf = ? ORDER BY e.municipio", [uf])
        avisar(f"estab_cidade: {uf} pronta")
    con.execute("DROP TABLE IF EXISTS estab_cidade")
    con.execute("ALTER TABLE estab_cidade_nova RENAME TO estab_cidade")


def preparar_emails(con: duckdb.DuckDBPyConnection) -> None:
    """E-mails que aparecem em mais de uma empresa (os muito repetidos são de contador)."""
    con.execute(
        """
        CREATE OR REPLACE TABLE email_freq AS
        SELECT lower(trim(correio_eletronico)) AS email, count(DISTINCT cnpj_basico) AS n_empresas
        FROM estabelecimentos WHERE correio_eletronico LIKE '%@%'
        GROUP BY 1 HAVING count(DISTINCT cnpj_basico) >= 2
        """
    )


def codigo_municipio(con: duckdb.DuckDBPyConnection, nome: str, uf: str) -> tuple[str, str]:
    """Código da Receita e nome oficial do município (a UF desempata homônimos)."""
    linhas = con.execute(
        """
        SELECT m.codigo, m.descricao, count(*) AS n
        FROM municipios m
        JOIN estabelecimentos e ON e.municipio = m.codigo AND e.uf = ?
        WHERE strip_accents(upper(m.descricao)) = strip_accents(upper(?))
        GROUP BY ALL ORDER BY n DESC
        """,
        [uf.upper(), nome.strip()],
    ).fetchall()
    if not linhas:
        raise ValueError(f"Município não encontrado na base: {nome}/{uf}")
    return linhas[0][0], linhas[0][1]


def cnaes_do_segmento(con: duckdb.DuckDBPyConnection, termos: list[str]) -> list[tuple[str, str]]:
    """CNAEs cuja descrição contém algum dos termos (sem acento, sem diferenciar maiúsculas)."""
    achados: dict[str, str] = {}
    for termo in termos:
        for codigo, descricao in con.execute(
            "SELECT trim(codigo), descricao FROM cnaes "
            "WHERE strip_accents(lower(descricao)) LIKE '%' || strip_accents(lower(?)) || '%'",
            [termo.strip()],
        ).fetchall():
            achados[codigo] = descricao
    return sorted(achados.items())
