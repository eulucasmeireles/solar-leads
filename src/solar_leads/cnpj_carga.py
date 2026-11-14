"""Baixa os dados abertos de CNPJ da Receita Federal e converte para Parquet.

Uso: python -m solar_leads.cnpj_carga [--mes AAAA-MM] [--so Arquivo.zip ...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET

import duckdb
import httpx

from solar_leads import config

TOKEN = os.getenv("RFB_SHARE_TOKEN", "gn672Ad4CF8N6TK")
BASE = "https://arquivos.receitafederal.gov.br/public.php/webdav/Dados/Cadastros/CNPJ"
ALT = f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{TOKEN}/Dados/Cadastros/CNPJ"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TENTATIVAS = 8

ZIPS = config.DADOS / "cnpj_zip"
TMP = config.DADOS / "tmp"
OUT = config.CNPJ_PARQUET

COLUNAS = {
    "estabelecimentos": "cnpj_basico cnpj_ordem cnpj_dv matriz_filial nome_fantasia situacao_cadastral "
    "data_situacao_cadastral motivo_situacao_cadastral nome_cidade_exterior pais data_inicio_atividade "
    "cnae_fiscal cnae_fiscal_secundaria tipo_logradouro logradouro numero complemento bairro cep uf "
    "municipio ddd_1 telefone_1 ddd_2 telefone_2 ddd_fax fax correio_eletronico situacao_especial "
    "data_situacao_especial".split(),
    "empresas": "cnpj_basico razao_social natureza_juridica qualificacao_responsavel capital_social "
    "porte ente_federativo".split(),
    "socios": "cnpj_basico identificador_socio nome_socio cnpj_cpf_socio qualificacao_socio "
    "data_entrada_sociedade pais representante_legal nome_representante qualificacao_representante "
    "faixa_etaria".split(),
    "simples": "cnpj_basico opcao_simples data_opcao_simples data_exclusao_simples opcao_mei "
    "data_opcao_mei data_exclusao_mei".split(),
}
for _t in ("cnaes", "municipios", "naturezas", "qualificacoes"):
    COLUNAS[_t] = ["codigo", "descricao"]

# ordem de processamento: pequenas, Socios, Empresas, Simples, Estabelecimentos
ORDEM = ["cnaes", "municipios", "naturezas", "qualificacoes", "socios", "empresas", "simples", "estabelecimentos"]


def log(*a):
    print(*a, flush=True)


def cliente() -> httpx.Client:
    return httpx.Client(
        auth=(TOKEN, ""),
        headers={"User-Agent": UA, "Accept-Encoding": "identity"},
        timeout=httpx.Timeout(60, connect=30),
        follow_redirects=True,
    )


def listar(cli: httpx.Client, caminho: str) -> list[tuple[str, int | None]]:
    r = cli.request("PROPFIND", f"{BASE}/{caminho}", headers={"Depth": "1"})
    r.raise_for_status()
    ns = {"d": "DAV:"}
    itens = []
    for resp in ET.fromstring(r.content).findall("d:response", ns):
        href = resp.find("d:href", ns).text.rstrip("/")
        tam = resp.find(".//d:getcontentlength", ns)
        itens.append((href.rsplit("/", 1)[-1], int(tam.text) if tam is not None else None))
    return itens


def baixar(cli: httpx.Client, mes: str, nome: str, tamanho: int, destino) -> None:
    """Baixa um arquivo com retomada por Range; confere o tamanho antes de renomear."""
    part = destino.with_name(destino.name + ".part")
    url = f"{BASE}/{mes}/{nome}"
    for tent in range(1, TENTATIVAS + 1):
        try:
            ja = part.stat().st_size if part.exists() else 0
            if ja > tamanho:
                part.unlink()
                ja = 0
            if ja < tamanho:
                h = {"Range": f"bytes={ja}-"} if ja else {}
                with cli.stream("GET", url, headers=h) as r:
                    if r.status_code in (404, 403) and url.startswith(BASE):
                        url = f"{ALT}/{mes}/{nome}"  # caminho alternativo
                        raise httpx.HTTPError(f"HTTP {r.status_code}, trocando para URL alternativa")
                    if r.status_code == 200 and ja:
                        ja = 0  # servidor ignorou o Range: recomeça
                    elif r.status_code not in (200, 206):
                        raise httpx.HTTPError(f"HTTP {r.status_code}")
                    with open(part, "ab" if ja else "wb") as f:
                        for bloco in r.iter_bytes(1 << 20):
                            f.write(bloco)
            if part.stat().st_size == tamanho:
                part.rename(destino)
                return
            raise httpx.HTTPError(f"tamanho {part.stat().st_size} != {tamanho}")
        except (httpx.HTTPError, OSError) as e:
            espera = min(5 * tent * tent, 120)
            log(f"  tentativa {tent}/{TENTATIVAS} falhou ({e!r}); aguardando {espera}s")
            if tent == TENTATIVAS:
                raise
            time.sleep(espera)


def converter(zip_path, tabela: str, parquet) -> tuple[int, int]:
    """Extrai o CSV, converte para Parquet e devolve (linhas_csv, linhas_parquet)."""
    pasta = TMP / zip_path.stem
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            membro = z.infolist()[0]
            csv = pasta / "dados.csv"
            # Converte para UTF-8 e tira bytes nulos na extração: o leitor latin-1 do
            # DuckDB rejeita alguns bytes que aparecem nos arquivos de Estabelecimentos.
            with z.open(membro) as src, open(csv, "wb") as dst:
                while bloco := src.read(1 << 22):
                    dst.write(bloco.replace(b"\x00", b"").decode("latin-1").encode("utf-8"))
        # wc -l conta quebras de linha; linhas com \n dentro de aspas poderiam divergir
        n_csv = int(subprocess.run(["wc", "-l", str(csv)], capture_output=True, text=True, check=True).stdout.split()[0])
        cols = COLUNAS[tabela]
        tipos = "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in cols) + "}"
        con = duckdb.connect()
        con.execute("SET memory_limit='10GB'")
        con.execute("SET threads=4")
        con.execute(f"SET temp_directory='{TMP}'")
        con.execute("SET preserve_insertion_order=false")
        tmp_pq = TMP / parquet.name  # fora de OUT para um parquet incompleto nunca ser visto como pronto
        # strict_mode=false tolera aspas soltas; null_padding completa linhas curtas;
        # escape='' evita que a barra invertida/aspas viram escape; ignore_errors fica desligado
        # para qualquer linha rejeitada gerar erro em vez de sumir em silêncio.
        con.execute(f"""
            COPY (SELECT * FROM read_csv('{csv}', delim=';', header=false, all_varchar=true,
                  quote='"', escape='"', strict_mode=false, null_padding=true, parallel=false,
                  columns={tipos}, ignore_errors=false))
            TO '{tmp_pq}' (FORMAT parquet, COMPRESSION zstd)""")
        n_pq = con.execute(f"SELECT count(*) FROM read_parquet('{tmp_pq}')").fetchone()[0]
        shutil.move(tmp_pq, parquet)
        con.close()
        return n_csv, n_pq
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes")
    ap.add_argument("--so", nargs="+", metavar="ARQUIVO")
    args = ap.parse_args()
    for d in (ZIPS, TMP, OUT):
        d.mkdir(parents=True, exist_ok=True)

    with cliente() as cli:
        mes = args.mes
        if not mes:
            meses = [n for n, _ in listar(cli, "") if re.fullmatch(r"\d{4}-\d{2}", n)]
            mes = max(meses)
        log(f"mês: {mes}")
        arquivos = {n: t for n, t in listar(cli, f"{mes}/") if n.endswith(".zip")}

        def tabela_de(n):
            return re.sub(r"\d*\.zip$", "", n).lower()

        alvo = [n for n in arquivos if tabela_de(n) in COLUNAS]
        if args.so:
            faltam = set(args.so) - set(alvo)
            if faltam:
                log(f"arquivos não encontrados: {sorted(faltam)}")
                return 1
            alvo = [n for n in alvo if n in args.so]
        alvo.sort(key=lambda n: (ORDEM.index(tabela_de(n)), int(re.search(r"(\d*)\.zip", n).group(1) or 0)))

        falhas = []
        for nome in alvo:
            tabela = tabela_de(nome)
            num = re.search(r"(\d*)\.zip", nome).group(1) or "0"
            parquet = OUT / f"{tabela}_{num}.parquet"
            if parquet.exists():
                log(f"{nome}: parquet já existe, pulando")
                continue
            try:
                destino = ZIPS / nome
                tam = arquivos[nome]
                t0 = time.time()
                if not (destino.exists() and destino.stat().st_size == tam):
                    log(f"{nome}: baixando {tam / 1e6:.1f} MB")
                    baixar(cli, mes, nome, tam, destino)
                    dt = time.time() - t0
                    log(f"  baixado em {dt:.0f}s ({tam / 1e6 / max(dt, 1e-3):.1f} MB/s)")
                t1 = time.time()
                n_csv, n_pq = converter(destino, tabela, parquet)
                aviso = "" if n_csv == n_pq else f"  ATENÇÃO: diferença csv-parquet = {n_csv - n_pq}"
                log(f"  convertido em {time.time() - t1:.0f}s: {n_pq} linhas (wc -l: {n_csv}){aviso}")
                destino.unlink()
            except Exception as e:  # noqa: BLE001 - registra e segue para o próximo arquivo
                log(f"{nome}: FALHOU: {e!r}")
                falhas.append(nome)

    # resumo: contagem por tabela a partir dos parquets existentes
    con = duckdb.connect()
    contagem = {}
    for t in COLUNAS:
        if list(OUT.glob(f"{t}_*.parquet")):
            contagem[t] = con.execute(f"SELECT count(*) FROM read_parquet('{OUT}/{t}_*.parquet')").fetchone()[0]
    (OUT / "_carga.json").write_text(json.dumps({"mes": mes, "linhas": contagem}, indent=2))
    log(f"resumo: {contagem}")
    if falhas:
        log(f"falharam: {falhas}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
