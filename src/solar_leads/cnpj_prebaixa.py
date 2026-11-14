"""Baixa os zips da Receita em paralelo, por faixas, para dados/cnpj_zip/.

O servidor limita cada conexão a ~0,7 MB/s mas aceita várias ao mesmo tempo.
Rode junto com (ou antes de) `cnpj_carga`, que pula o download dos zips prontos.
Uso: python -m solar_leads.cnpj_prebaixa [--mes AAAA-MM] [--conexoes 8]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from .cnpj_carga import BASE, COLUNAS, OUT, TENTATIVAS, ZIPS, cliente, listar, log

FAIXA = 64 * 1024 * 1024


def _baixar_faixa(mes: str, nome: str, inicio: int, fim: int, destino) -> None:
    esperado = fim - inicio + 1
    if destino.exists() and destino.stat().st_size == esperado:
        return
    for tent in range(1, TENTATIVAS + 1):
        try:
            with cliente() as cli, cli.stream("GET", f"{BASE}/{mes}/{nome}", headers={"Range": f"bytes={inicio}-{fim}"}) as r:
                if r.status_code != 206:
                    raise OSError(f"HTTP {r.status_code}")
                with open(destino, "wb") as f:
                    for bloco in r.iter_bytes(1 << 20):
                        f.write(bloco)
            if destino.stat().st_size == esperado:
                return
            raise OSError(f"faixa incompleta: {destino.stat().st_size} de {esperado}")
        except Exception as e:  # noqa: BLE001 - qualquer falha de rede leva a nova tentativa
            if tent == TENTATIVAS:
                raise
            log(f"  {nome} faixa {inicio}: tentativa {tent} falhou ({e!r})")
            time.sleep(min(5 * tent, 60))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes")
    ap.add_argument("--conexoes", type=int, default=8)
    args = ap.parse_args()
    ZIPS.mkdir(parents=True, exist_ok=True)

    with cliente() as cli:
        mes = args.mes or max(n for n, _ in listar(cli, "") if re.fullmatch(r"\d{4}-\d{2}", n))
        arquivos = {n: t for n, t in listar(cli, f"{mes}/") if n.endswith(".zip")}

    def pendente(nome: str) -> bool:
        tabela = re.sub(r"\d*\.zip$", "", nome).lower()
        num = re.search(r"(\d*)\.zip", nome).group(1) or "0"
        pronto = (ZIPS / nome).exists() and (ZIPS / nome).stat().st_size == arquivos[nome]
        em_curso = (ZIPS / f"{nome}.part").exists()  # o cnpj_carga já está baixando este
        return tabela in COLUNAS and not (OUT / f"{tabela}_{num}.parquet").exists() and not pronto and not em_curso

    # Maiores primeiro: são os que o cnpj_carga deixa para o fim.
    alvo = sorted((n for n in arquivos if pendente(n)), key=lambda n: -arquivos[n])
    total = sum(arquivos[n] for n in alvo)
    log(f"mês {mes}: {len(alvo)} arquivos, {total / 1e9:.1f} GB, {args.conexoes} conexões")
    t0 = time.time()

    with ThreadPoolExecutor(args.conexoes) as pool:
        for nome in alvo:
            if not pendente(nome):
                continue
            tam = arquivos[nome]
            pasta = ZIPS / f"{nome}.faixas"
            pasta.mkdir(exist_ok=True)
            faixas = [(i, min(i + FAIXA, tam) - 1) for i in range(0, tam, FAIXA)]
            partes = [pasta / f"{i:012d}" for i, _ in faixas]
            list(pool.map(lambda a: _baixar_faixa(mes, nome, a[0][0], a[0][1], a[1]), zip(faixas, partes)))
            montado = ZIPS / f"{nome}.montando"
            with open(montado, "wb") as saida:
                for p in partes:
                    with open(p, "rb") as f:
                        shutil.copyfileobj(f, saida, 1 << 22)
            if montado.stat().st_size != tam:
                log(f"{nome}: tamanho final incorreto")
                return 1
            montado.rename(ZIPS / nome)
            shutil.rmtree(pasta)
            log(f"{nome}: {tam / 1e6:.0f} MB prontos ({(time.time() - t0) / 60:.1f} min desde o início)")
    log(f"concluído em {(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
