"""Linha de comando: `leads base preparar`, `leads gerar ...`, `leads whatsapp ...`."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import aneel, cnpj_db, config, enriquecer, gmaps, pipeline, whatsapp

app = typer.Typer(no_args_is_help=True, help="Listas de leads de energia solar por região.")
base_app = typer.Typer(no_args_is_help=True, help="Base de CNPJ da Receita.")
aneel_app = typer.Typer(no_args_is_help=True, help="Registro da ANEEL de quem já tem energia solar.")
app.add_typer(aneel_app, name="aneel")
wa_app = typer.Typer(no_args_is_help=True, help="Validação de WhatsApp pelo Evolution GO.")
app.add_typer(base_app, name="base")
app.add_typer(wa_app, name="whatsapp")

@base_app.command("cidades")
def base_cidades() -> None:
    """Refaz só as tabelas nacionais por cidade (deixa a geração de listas rápida)."""
    con = cnpj_db.conectar()
    cnpj_db.preparar_cidades(con, avisar=lambda t: typer.echo(f"{datetime.now():%H:%M:%S} {t}"))
    for t in ("empresa_resumo", "estab_cidade"):
        typer.echo(f"{t}: {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:,} linhas")


@base_app.command("preparar")
def base_preparar() -> None:
    """Cria as tabelas derivadas (telefones, frequências, sócios) após a carga."""
    con = cnpj_db.conectar()
    cnpj_db.preparar(con)
    cnpj_db.preparar_cidades(con, avisar=typer.echo)
    for t in ("tel_estab", "tel_freq", "socio_pf", "empresa_resumo", "estab_cidade"):
        typer.echo(f"{t}: {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:,} linhas")


@aneel_app.command("baixar")
def aneel_baixar(
    proxy: Annotated[Optional[str], typer.Option(help="Túnel SOCKS para sair pelo Brasil, ex.: 127.0.0.1:1080")] = None,
) -> None:
    """Baixa o arquivo de geração distribuída e refaz as tabelas de quem já tem solar."""
    typer.echo(f"Arquivo: {aneel.baixar(proxy)}")
    aneel_preparar()


@aneel_app.command("preparar")
def aneel_preparar() -> None:
    """Refaz as tabelas a partir do arquivo já baixado."""
    for tabela, n in aneel.preparar(cnpj_db.conectar()).items():
        typer.echo(f"{tabela}: {n:,} linhas")


@app.command()
def gerar(
    cidade: Annotated[str, typer.Option(help="Município, como na Receita (ex.: Valinhos)")],
    uf: Annotated[str, typer.Option(help="Sigla do estado (ex.: SP)")],
    segmento: Annotated[Optional[list[str]], typer.Option(help="Tipo de negócio (ex.: padaria); repita para vários. Sem segmento e com --fonte receita, pega todas as atividades")] = None,
    fonte: Annotated[str, typer.Option(help="maps, receita ou ambas")] = "ambas",
    cnae: Annotated[Optional[list[str]], typer.Option(help="CNAE (ou início dele) para a busca na Receita; sem isso, procura o segmento na descrição dos CNAEs")] = None,
    so_ativas: Annotated[bool, typer.Option(help="Ignora empresas baixadas, inaptas e suspensas")] = False,
    incluir_mei: Annotated[bool, typer.Option(help="Mantém os MEI (por padrão ficam fora, inclusive quem era MEI ao fechar)")] = False,
    incluir_de_fora: Annotated[bool, typer.Option(help="Mantém filiais de empresas de outra cidade e telefones de outro DDD")] = False,
    bairro: Annotated[Optional[list[str]], typer.Option(help="Restringe a busca a bairros; repita para vários")] = None,
    profundidade: Annotated[int, typer.Option(help="Quantas rolagens por busca no Maps")] = 10,
    min_aptidao: Annotated[int, typer.Option(help="Descarta leads com aptidão solar abaixo disso")] = 0,
    validar_whatsapp: Annotated[bool, typer.Option(help="Confere os números no Evolution GO")] = True,
    so_pessoal: Annotated[bool, typer.Option(help="Mantém só números com conta pessoal de WhatsApp (exige validação)")] = False,
    csv_maps: Annotated[Optional[Path], typer.Option(help="Usa um CSV do coletor já baixado, sem nova busca")] = None,
) -> None:
    """Junta Google Maps e base da Receita, acha o dono, pontua e exporta o CSV para o GHL."""
    if fonte not in ("maps", "receita", "ambas"):
        raise typer.Exit("--fonte deve ser maps, receita ou ambas.")
    segmento = segmento or []
    if fonte != "receita" and not segmento:
        raise typer.Exit("Informe --segmento, ou use --fonte receita para todas as atividades.")
    con = cnpj_db.conectar()
    codigo, nome_oficial = cnpj_db.codigo_municipio(con, cidade, uf)
    typer.echo(f"Município: {nome_oficial}/{uf.upper()} (código {codigo})")

    cnaes: list[str] = []
    if fonte != "maps":
        cnaes = [re.sub(r"\D", "", c) for c in cnae] if cnae else []
        if not cnaes and not segmento:
            cnaes = [""]  # todas as atividades
            typer.echo("Atividades: todas")
        if not cnaes:
            achados = cnpj_db.cnaes_do_segmento(con, segmento)
            cnaes = [c for c, _ in achados]
            for c, d in achados:
                typer.echo(f"CNAE {c}: {d}")
        if not cnaes:
            raise typer.Exit("Nenhum CNAE corresponde ao segmento; informe com --cnae.")

    if fonte == "receita":
        lugares = []
    elif csv_maps:
        lugares = gmaps.ler_csv(csv_maps.read_text())
    else:
        locais = [f"{b}, {cidade} {uf}" for b in bairro] if bairro else [f"{cidade} {uf}"]
        buscas = [f"{s} em {l}" for s in segmento for l in locais]
        job = gmaps.criar_job(f"{cidade}-{uf}", buscas, profundidade, tempo_max=max(600, 240 * len(buscas)))
        typer.echo(f"Buscando no Google Maps ({len(buscas)} buscas, job {job})...")
        if gmaps.aguardar(job, limite_s=max(1200, 400 * len(buscas))) != "ok":
            raise typer.Exit(f"A busca no Google Maps falhou (job {job}).")
        lugares = gmaps.baixar(job)
    typer.echo(f"Lugares encontrados no Maps: {len(lugares)}")

    leads = enriquecer.enriquecer(
        con, lugares, codigo, uf, cidade=nome_oficial.title(), cnaes=cnaes, so_ativas=so_ativas,
        incluir_mei=incluir_mei, so_locais=not incluir_de_fora,
    )
    if not incluir_mei:
        antes = len(leads)
        leads = [l for l in leads if not l["mei"]]
        typer.echo(f"MEI excluídos entre os lugares do Maps: {antes - len(leads)}")

    tem_whatsapp = None
    if validar_whatsapp:
        celulares = [t["tel"] for l in leads for t in l["telefones"] if t["faixa"] != "C"]
        try:
            tem_whatsapp = whatsapp.checar(con, celulares)
            ativos = [i for i in tem_whatsapp.values() if i["tem"]]
            typer.echo(
                f"WhatsApp: {len(ativos)} de {len(tem_whatsapp)} números ativos, "
                f"{sum(1 for i in ativos if not i['business'])} em conta pessoal"
            )
        except whatsapp.WhatsAppIndisponivel as e:
            typer.echo(f"AVISO: WhatsApp não verificado. {e}")
    if so_pessoal and tem_whatsapp is None:
        raise typer.Exit("--so-pessoal exige a validação de WhatsApp, que não está disponível.")

    linhas, disparo = pipeline.montar(leads, cidade, tem_whatsapp, so_pessoal, min_aptidao)

    config.SAIDA.mkdir(parents=True, exist_ok=True)
    escopo = pipeline.slug("-".join(segmento)) or ("cnae-" + "-".join(cnae) if cnae else "todas")
    base = config.SAIDA / f"{pipeline.slug(cidade)}-{uf.lower()}-{escopo}-{datetime.now():%Y%m%d-%H%M}"
    pipeline.salvar_csv(disparo, f"{base}-ghl.csv")
    pipeline.salvar_csv(linhas, f"{base}-completo.csv")
    resumo = pipeline.resumir(linhas, disparo, lugares=len(lugares), whatsapp_verificado=tem_whatsapp is not None)
    typer.echo(json.dumps(resumo, ensure_ascii=False, indent=2))
    typer.echo(f"Para importar no GHL: {base}-ghl.csv\nLista completa: {base}-completo.csv")


@wa_app.command("criar")
def wa_criar() -> None:
    """Cria a instância de validação no Evolution GO."""
    typer.echo(whatsapp.criar_instancia())


@wa_app.command("parear")
def wa_parear(numero: Annotated[str, typer.Argument(help="Número do chip de validação com DDI, ex.: 5511999999999")]) -> None:
    """Gera o código de 8 dígitos para digitar em Aparelhos conectados no celular."""
    whatsapp.conectar()
    typer.echo(f"Código de pareamento: {whatsapp.codigo_pareamento(numero)}")


@wa_app.command("qr")
def wa_qr(destino: Path = Path("saida/qr-whatsapp.png")) -> None:
    """Salva o QR code de conexão em um PNG."""
    # Só abre a conexão se ainda não houver uma: cada chamada a mais cria outro ciclo
    # de QR code, e o ciclo sobrando derruba a sessão quando expira.
    estado = {k.lower(): v for k, v in whatsapp.status().items()}
    if estado.get("loggedin"):
        # Pedir QR com a sessão ativa abre um cliente extra no Evolution GO, que derruba a sessão ao expirar.
        typer.echo("Já existe um WhatsApp conectado; não é preciso QR code.")
        return
    if not estado.get("connected"):
        whatsapp.conectar()
        time.sleep(3)
    destino.parent.mkdir(parents=True, exist_ok=True)
    typer.echo(f"QR code salvo em {whatsapp.salvar_qr(destino)}")


@wa_app.command("verificar")
def wa_verificar(
    lista: Annotated[Optional[str], typer.Option(help="Id da lista do sistema web; sem isso, usa a mais recente")] = None,
    arquivo: Annotated[Optional[Path], typer.Option(help="Em vez da lista: JSON com números nacionais (DDD + número)")] = None,
    lote_min: int = 3,
    lote_max: int = 6,
    pausa_min: Annotated[float, typer.Option(help="Segundos mínimos entre consultas")] = 30,
    pausa_max: Annotated[float, typer.Option(help="Segundos máximos entre consultas")] = 60,
    limite_dia: Annotated[int, typer.Option(help="Para ao chegar a esse número de verificações no dia")] = 3000,
    das: Annotated[int, typer.Option(help="Hora em que começa a consultar")] = 8,
    ate: Annotated[int, typer.Option(help="Hora em que para de consultar")] = 20,
) -> None:
    """Verifica devagar os números ainda não conferidos, do contato mais forte para o mais fraco.

    Pode ser interrompido e retomado: cada resultado é gravado na hora. Para sozinho
    se o WhatsApp desconectar, para não insistir numa sessão derrubada.
    """
    import duckdb

    if arquivo:
        fila = json.loads(arquivo.read_text())
    else:
        pasta = config.DADOS / "listas"
        alvo = pasta / f"{lista}.parquet" if lista else max(pasta.glob("*.parquet"), key=lambda a: a.stat().st_mtime)
        fila = [
            t[0].removeprefix("+55")
            for t in duckdb.connect().execute(
                f"SELECT telefone FROM read_parquet('{alvo}') WHERE faixa IN ('A', 'B') AND telefone <> '' ORDER BY ordem"
            ).fetchall()
        ]
    motivo = whatsapp.verificar_devagar(
        fila, lote_min=lote_min, lote_max=lote_max, pausa_min=pausa_min, pausa_max=pausa_max,
        limite_dia=limite_dia, das=das, ate=ate,
        avisar=lambda texto, restantes, hoje: typer.echo(f"{datetime.now():%d/%m %H:%M} {texto} faltam {restantes}"
                                                         + (f" | hoje {hoje}" if hoje is not None else ""))
        if hoje is None or hoje % 50 < 6 else None,
    )
    typer.echo(f"{datetime.now():%d/%m %H:%M} {motivo}")


@wa_app.command("status")
def wa_status() -> None:
    typer.echo(whatsapp.status())


@app.command()
def senha() -> None:
    """Define a senha de acesso do sistema web (grava só o código embaralhado dela no .env)."""
    import secrets

    from .web.servidor import gerar_hash

    nova = typer.prompt("Nova senha (12 caracteres ou mais)", hide_input=True, confirmation_prompt=True)
    if len(nova) < 12:
        raise typer.Exit("Senha curta demais.")
    env = config.RAIZ / ".env"
    # Trocar a senha também troca o segredo: todas as sessões abertas deixam de valer.
    linhas = [l for l in env.read_text().splitlines() if not l.startswith(("LEADS_SENHA_HASH=", "LEADS_SEGREDO="))] if env.exists() else []
    linhas.append(f"LEADS_SEGREDO={secrets.token_hex(32)}")
    linhas.append(f"LEADS_SENHA_HASH={gerar_hash(nova)}")
    env.write_text("\n".join(linhas) + "\n")
    env.chmod(0o600)
    typer.echo("Senha trocada. Reinicie o serviço: systemctl --user restart solar-leads-web")


@app.command()
def web(porta: int = 8787, host: str = "127.0.0.1") -> None:
    """Sobe o sistema web (por padrão só em localhost; use um túnel SSH para abrir no navegador)."""
    import uvicorn

    uvicorn.run("solar_leads.web.servidor:app", host=host, port=porta)


if __name__ == "__main__":
    app()
