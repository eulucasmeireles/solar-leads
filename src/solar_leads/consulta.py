"""Serviços pagos que dizem se um número tem WhatsApp, e o teste que mede o acerto deles.

O teste usa números cujo resultado já conhecemos (verificados antes pela nossa própria conta)
e compara com a resposta do serviço. Os números saem do servidor para o serviço consultado.
"""

from __future__ import annotations

import csv
import io
import os
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import httpx

from . import cnpj_db

APIFY = "https://api.apify.com/v2/acts/{ator}/run-sync-get-dataset-items"
CHECKNUMBER = "https://api.checknumber.ai/v1"
CHECKLEAKED = "https://whatsapp-proxy.checkleaked.cc"
MAX_RESULTADO = 20 * 1024 * 1024  # o arquivo de resultado é lido só como texto, e nunca maior que isto


class ServicoIndisponivel(RuntimeError):
    pass


def _so_digitos(texto) -> str:
    return "".join(c for c in str(texto or "") if c.isdigit())


def _vtrdev(itens: list[dict]) -> dict[str, bool | None]:
    return {_so_digitos(i.get("phone")): i.get("hasWhatsapp") for i in itens if i.get("phone")}


def _apivault(itens: list[dict]) -> dict[str, bool | None]:
    # `whatsapp` é a consulta de verdade; `whatsappCapable` só diz que a linha é de celular.
    return {_so_digitos(i.get("e164")): i.get("whatsapp") if isinstance(i.get("whatsapp"), bool) else None for i in itens if i.get("e164")}


def _vero(itens: list[dict]) -> dict[str, bool | None]:
    return {_so_digitos(i.get("number")): i.get("hasWhatsApp") for i in itens if i.get("number") and isinstance(i.get("hasWhatsApp"), bool)}


def _vero_comercial(itens: list[dict]) -> dict[str, bool | None]:
    return {
        _so_digitos(i.get("number")): i.get("accountType") == "business"
        for i in itens if i.get("number") and i.get("hasWhatsApp") is True and i.get("accountType") in ("personal", "business")
    }


def _romy(itens: list[dict]) -> dict[str, bool | None]:
    return {_so_digitos(i.get("phone")): i.get("is_on_whatsapp") for i in itens if i.get("phone") and isinstance(i.get("is_on_whatsapp"), bool)}


def _romy_comercial(itens: list[dict]) -> dict[str, bool | None]:
    # O romy manda o perfil comercial em `business`; conta pessoal vem com esse campo vazio.
    return {_so_digitos(i.get("phone")): bool(i.get("business")) for i in itens if i.get("phone") and i.get("is_on_whatsapp") is True}


def _sim_ou_nao(valor) -> bool | None:
    return {"yes": True, "true": True, "1": True, "no": False, "false": False, "0": False}.get(str(valor or "").strip().lower())


def _checknumber(itens: list[dict]) -> dict[str, bool | None]:
    # A tarefa `ws_business` chama a coluna de `whatsapp`; a `ws_active`, de `activated`.
    return {_so_digitos(i.get("number")): _sim_ou_nao(i.get("whatsapp") or i.get("activated")) for i in itens if i.get("number")}


def _checknumber_comercial(itens: list[dict]) -> dict[str, bool | None]:
    return {
        _so_digitos(i.get("number")): _sim_ou_nao(i.get("business"))
        for i in itens if i.get("number") and _sim_ou_nao(i.get("whatsapp") or i.get("activated")) and _sim_ou_nao(i.get("business")) is not None
    }


def _linhas_do_resultado(dados: bytes) -> list[dict]:
    """Lê o arquivo de resultado da CheckNumber (texto com colunas, solto ou dentro de um zip). Nada é gravado nem executado."""
    if len(dados) > MAX_RESULTADO:
        raise ServicoIndisponivel("O arquivo de resultado veio grande demais para ser lido.")
    textos: list[str] = []
    if zipfile.is_zipfile(io.BytesIO(dados)):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for membro in z.infolist():
                if membro.is_dir():
                    continue
                if membro.file_size > MAX_RESULTADO or not membro.filename.lower().endswith((".csv", ".txt", ".tsv")):
                    raise ServicoIndisponivel(f"O resultado veio num formato que ainda não sei ler: {membro.filename[:80]}")
                textos.append(z.read(membro).decode("utf-8-sig", errors="replace"))
    else:
        textos.append(dados.decode("utf-8-sig", errors="replace"))
    linhas: list[dict] = []
    for texto in textos:
        cabecalho = texto.splitlines()[0] if texto.strip() else ""
        separador = max(",;\t|", key=cabecalho.count)
        for linha in csv.DictReader(io.StringIO(texto), delimiter=separador):
            linhas.append({(k or "").strip().lower(): (v or "").strip() for k, v in linha.items()})
    return linhas


ultima_tarefa: dict = {}  # resumo da última tarefa da CheckNumber (estado, totais e valor cobrado)


def _tarefa_checknumber(tipo: str) -> Callable[[list[str], int], list[dict]]:
    """Consulta por lote: envia a lista, espera a tarefa terminar e baixa o resultado."""

    def buscar(numeros: list[str], limite_s: int) -> list[dict]:
        chave = os.getenv("CHECKNUMBER_KEY", "").strip()
        if not chave:
            raise ServicoIndisponivel("Falta a chave da CheckNumber. Rode `leads segredo CHECKNUMBER_KEY` e cole a chave.")
        cabecalho = {"X-API-Key": chave}
        ultima_tarefa.clear()
        try:
            r = httpx.post(
                f"{CHECKNUMBER}/tasks", headers=cabecalho, timeout=60,
                data={"task_type": tipo}, files={"file": ("numeros.txt", "\n".join(numeros).encode(), "text/plain")},
            )
            if r.status_code >= 400:
                raise ServicoIndisponivel(f"A CheckNumber recusou a lista ({r.status_code}): {r.text[:200]}")
            tarefa = r.json()
            fim = time.time() + limite_s
            while tarefa.get("status") not in ("exported", "failed"):
                if time.time() > fim:
                    raise ServicoIndisponivel(f"A CheckNumber não terminou em {limite_s} s (tarefa {tarefa.get('task_id')}).")
                time.sleep(5)
                r = httpx.post(f"{CHECKNUMBER}/gettasks", headers=cabecalho, data={"task_id": tarefa["task_id"]}, timeout=60)
                if r.status_code >= 400:
                    raise ServicoIndisponivel(f"A CheckNumber não informou o andamento ({r.status_code}): {r.text[:200]}")
                tarefa = tarefa | r.json()
            ultima_tarefa.update({k: tarefa.get(k) for k in ("task_id", "status", "total", "success", "failure", "estimated_amount", "actual_amount")})
            if tarefa["status"] == "failed" or not tarefa.get("result_url"):
                raise ServicoIndisponivel(f"A tarefa da CheckNumber falhou (tarefa {tarefa.get('task_id')}).")
            # O endereço do resultado é de outro servidor: a chave não vai junto.
            arquivo = httpx.get(tarefa["result_url"], timeout=120, follow_redirects=True)
            if arquivo.status_code >= 400:
                raise ServicoIndisponivel(f"Não consegui baixar o resultado da CheckNumber ({arquivo.status_code}).")
        except httpx.HTTPError as e:
            raise ServicoIndisponivel(f"Não consegui falar com a CheckNumber: {type(e).__name__}") from e
        except (ValueError, KeyError) as e:
            raise ServicoIndisponivel("A CheckNumber respondeu num formato inesperado.") from e
        return _linhas_do_resultado(arquivo.content)

    return buscar


def _checkleaked_tem(item: dict) -> bool | None:
    # Número com WhatsApp vem com `isWAContact`; número sem WhatsApp vem só com `exists: false` e um aviso.
    for campo in ("isWAContact", "exists"):
        if isinstance(item.get(campo), bool):
            return item[campo]
    return None


def _checkleaked(itens: list[dict]) -> dict[str, bool | None]:
    return {i["number"]: _checkleaked_tem(i) for i in itens if _checkleaked_tem(i) is not None}


def _checkleaked_comercial(itens: list[dict]) -> dict[str, bool | None]:
    return {i["number"]: i.get("isBusiness") for i in itens if _checkleaked_tem(i) is True and isinstance(i.get("isBusiness"), bool)}


# Da resposta da CheckLeaked só fica isto; recado, foto e perfil comercial são descartados na hora.
_CAMPOS_CHECKLEAKED = ("isWAContact", "exists", "isBusiness", "isEnterprise", "cached", "date", "error")
CHECKLEAKED_EM_PARALELO = 4  # cada resposta ao vivo leva uns 3 segundos; em paralelo, 200 números saem em 2 minutos
CHECKLEAKED_INTERVALO = 0.35  # o serviço recusa pedidos com menos de 0,25 s entre um e outro (medido em 15/11/2026)
_vez = threading.Lock()
_ultimo_pedido = [0.0]


def _esperar_a_vez(dormir=time.sleep) -> None:
    """Segura cada pedido até passar o intervalo mínimo desde o anterior, mesmo com vários ao mesmo tempo."""
    with _vez:
        falta = _ultimo_pedido[0] + CHECKLEAKED_INTERVALO - time.monotonic()
        if falta > 0:
            dormir(falta)
        _ultimo_pedido[0] = time.monotonic()


def _cliente_checkleaked(chave: str) -> httpx.Client:
    return httpx.Client(base_url=CHECKLEAKED, headers={"x-rapidapi-key": chave}, timeout=45)


def _checkleaked_um(cliente: httpx.Client, digitos: str, dormir=time.sleep) -> dict:
    """Uma consulta (sem foto). Gasta uma unidade do saldo, tenha o número WhatsApp ou não."""
    r = None
    for tentativa in range(4):
        _esperar_a_vez(dormir)
        try:
            r = cliente.get(f"/number/no_picture/{digitos}")
        except httpx.HTTPError:
            r = None
            dormir(2.0)
            continue
        if r.status_code == 429:  # pedidos próximos demais: espera e repete
            dormir(1.0 + tentativa)
            continue
        break
    if r is None:
        return {"number": digitos, "http": 0}
    if r.status_code in (401, 402, 403):
        raise ServicoIndisponivel(f"A CheckLeaked recusou a chave ou o saldo acabou ({r.status_code}): {r.text[:160]}")
    try:
        corpo = r.json() if r.status_code < 500 else {}
    except ValueError:
        corpo = {}
    corpo = corpo if isinstance(corpo, dict) else {}
    return {"number": digitos, "http": r.status_code} | {k: corpo[k] for k in _CAMPOS_CHECKLEAKED if k in corpo}


def saldo_checkleaked(limite_s: float = 15) -> dict | None:
    """Consultas que restam na chave: {"restam", "total", "vence"}. None se o serviço não informar. Não gasta saldo."""
    chave = os.getenv("CHECKLEAKED_KEY", "").strip()
    if not chave:
        return None
    try:
        with _cliente_checkleaked(chave) as cliente:
            r = cliente.get("/api-key-stats", timeout=limite_s)
        d = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("requestRemaining"), int):
        return None
    return {"restam": d["requestRemaining"], "total": d.get("requestLimit"), "vence": str(d.get("expiresAt") or "")[:10]}


def _checkleaked_buscar(numeros: list[str], limite_s: int) -> list[dict]:
    """Consulta número por número, alguns ao mesmo tempo, e devolve as linhas na ordem da lista."""
    chave = os.getenv("CHECKLEAKED_KEY", "").strip()
    if not chave:
        raise ServicoIndisponivel("Falta a chave da CheckLeaked. Rode `leads segredo CHECKLEAKED_KEY` e cole a chave.")
    fim = time.time() + limite_s

    def um(numero: str) -> dict:
        if time.time() > fim:
            return {"number": _so_digitos(numero), "http": 0}
        return _checkleaked_um(cliente, _so_digitos(numero))

    with _cliente_checkleaked(chave) as cliente, ThreadPoolExecutor(CHECKLEAKED_EM_PARALELO) as fila:
        return list(fila.map(um, numeros))


@dataclass(frozen=True)
class Servico:
    nome: str
    ator: str
    por_mil_usd: float
    entrada: Callable[[list[str]], dict] | None
    ler: Callable[[list[dict]], dict[str, bool | None]]
    lote: int = 1000  # quantos números o serviço aceita por execução
    comercial: Callable[[list[dict]], dict[str, bool | None]] | None = None  # lê "é conta comercial?", se o serviço informar
    buscar: Callable[[list[str], int], list[dict]] | None = None  # serviço fora da Apify: recebe os números com +55 e devolve as linhas


SERVICOS = {
    # O vtrdev só responde os 100 primeiros números de cada execução (medido em 15/11/2026).
    "vtrdev": Servico("vtrdev", "vtrdev~whatsapp-number-validator", 1.0, lambda n: {"phone_numbers": n}, _vtrdev, lote=100),
    "apivault": Servico(
        "apivault", "apivault_labs~whatsapp-number-validator", 0.8,
        lambda n: {"phoneNumbers": n, "defaultCountry": "BR", "outputPreset": "full"}, _apivault,
    ),
    # O romy cobra US$ 0,05 por execução além do preço por número, e não deixa desligar recado e foto (não guardamos).
    # Em 15/11/2026 o servidor dele estava fora do ar (erro 530 nos 40 números do teste); a taxa de início foi cobrada assim mesmo.
    "romy": Servico("romy", "romy~whatsapp-contact-checker", 8.0, lambda n: {"phoneNumbers": n}, _romy, comercial=_romy_comercial),
    # Foto de perfil e operadora são extras pagos e ficam desligados de propósito.
    "vero": Servico(
        "vero", "vero-api~whatsapp-scraper", 6.0,
        lambda n: {"numbers": n, "pictures": False, "carrierInfo": False}, _vero, comercial=_vero_comercial,
    ),
    # CheckLeaked direta (o mesmo motor do eduair94 da Apify), número por número. Pacote de 5 mil consultas por US$ 19.
    "checkleaked": Servico("checkleaked", "", 3.8, None, _checkleaked, comercial=_checkleaked_comercial, buscar=_checkleaked_buscar),
    # CheckNumber.AI, fora da Apify, por lote. `ws_business` diz se tem WhatsApp e se é comercial (preço não publicado;
    # o valor cobrado de verdade sai em `ultima_tarefa`). `ws_active` custa US$ 1,20 por 10 mil e traz também a última vez online.
    "checknumber": Servico(
        "checknumber", "", 0.12, None, _checknumber, comercial=_checknumber_comercial, buscar=_tarefa_checknumber("ws_business"),
    ),
    "checknumber-dias": Servico(
        "checknumber-dias", "", 0.12, None, _checknumber, comercial=_checknumber_comercial, buscar=_tarefa_checknumber("ws_active"),
    ),
}


def consultar(
    servico: Servico, nacionais: list[str], limite_s: int = 290, teto_usd: float | None = None
) -> tuple[dict[str, bool | None], list[dict]]:
    """Pergunta ao serviço. Devolve ({número nacional: tem, não tem ou None se não respondeu}, resposta bruta).

    `teto_usd` é o máximo que a Apify pode cobrar por execução; passando disso ela para sozinha.
    """
    if servico.buscar:
        itens = servico.buscar([f"+55{n}" for n in nacionais], max(limite_s, 900))
        if not itens:
            raise ServicoIndisponivel(f"O serviço {servico.nome} não devolveu nenhuma resposta.")
        lidos = servico.ler(itens)
        return {n: lidos.get(f"55{n}") for n in nacionais}, itens
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise ServicoIndisponivel("Falta a chave da Apify. Rode `leads segredo APIFY_TOKEN` e cole a chave.")
    itens: list[dict] = []
    for inicio in range(0, len(nacionais), servico.lote):
        parte = nacionais[inicio : inicio + servico.lote]
        try:
            r = httpx.post(
                APIFY.format(ator=servico.ator), timeout=limite_s + 20,
                params={"timeout": limite_s} | ({"maxTotalChargeUsd": teto_usd} if teto_usd else {}),
                headers={"Authorization": f"Bearer {token}"}, json=servico.entrada([f"+55{n}" for n in parte]),
            )
        except httpx.HTTPError as e:
            raise ServicoIndisponivel(f"Não consegui falar com a Apify: {type(e).__name__}") from e
        if r.status_code >= 400:
            try:
                motivo = r.json().get("error", {}).get("message", "")
            except ValueError:
                motivo = r.text[:200]
            raise ServicoIndisponivel(f"A Apify recusou ({r.status_code}): {motivo}")
        resposta = r.json()
        if not resposta:
            # Execução "bem-sucedida" sem nenhuma linha: o serviço se recusou a consultar (ex.: só atende plano pago da Apify).
            raise ServicoIndisponivel(f"O serviço {servico.nome} não devolveu nenhuma resposta. Veja o aviso da execução no painel da Apify.")
        avisos = [str(i["error"]) for i in resposta if isinstance(i, dict) and i.get("error") and not servico.ler([i])]
        if avisos and not servico.ler(resposta):
            # O serviço "terminou bem", mas não consultou nada (ex.: cota de teste esgotada).
            raise ServicoIndisponivel(f"O serviço {servico.nome} não respondeu aos números: {avisos[0]}")
        itens += resposta
    lidos = servico.ler(itens)
    # O serviço devolve com 55 na frente; a nossa base guarda o número nacional.
    return {n: lidos.get(f"55{n}") for n in nacionais}, itens


def amostra_conhecida(com_pessoal: int = 110, com_comercial: int = 40, sem: int = 50) -> dict[str, dict]:
    """Números já verificados pela nossa conta: {nacional: {"tem": bool, "comercial": bool}}. Sempre os mesmos."""
    con = cnpj_db.conectar(somente_leitura=True)
    try:
        saida: dict[str, dict] = {}
        for filtro, quantos in (("tem AND NOT business", com_pessoal), ("tem AND business", com_comercial), ("NOT tem", sem)):
            for tel, tem, comercial in con.execute(
                f"SELECT tel, tem, business FROM whatsapp_tipo WHERE {filtro} ORDER BY md5(tel) LIMIT ?", [quantos]
            ).fetchall():
                saida[tel] = {"tem": bool(tem), "comercial": bool(comercial)}
        return saida
    finally:
        con.close()


def comparar(conhecidos: dict[str, dict], respostas: dict[str, bool | None]) -> dict:
    """Acerto do serviço contra o que já sabemos."""
    r = {"total": len(conhecidos), "sem_resposta": 0, "acertos": 0, "disse_tem_e_nao_tem": 0, "disse_nao_tem_e_tem": 0}
    for tel, certo in conhecidos.items():
        resposta = respostas.get(tel)
        if resposta is None:
            r["sem_resposta"] += 1
        elif resposta == certo["tem"]:
            r["acertos"] += 1
        elif resposta:
            r["disse_tem_e_nao_tem"] += 1
        else:
            r["disse_nao_tem_e_tem"] += 1
    respondidos = r["total"] - r["sem_resposta"]
    r["acerto_pct"] = round(100 * r["acertos"] / respondidos, 1) if respondidos else 0.0
    return r


def comparar_comercial(conhecidos: dict[str, dict], tipos: dict[str, bool | None]) -> dict:
    """Acerto do "pessoal ou comercial", só entre os números que sabemos ter WhatsApp."""
    r = {"com_whatsapp": 0, "sem_tipo": 0, "acertos": 0, "disse_comercial_e_e_pessoal": 0, "disse_pessoal_e_e_comercial": 0}
    for tel, certo in conhecidos.items():
        if not certo["tem"]:
            continue
        r["com_whatsapp"] += 1
        tipo = tipos.get(f"55{tel}")
        if tipo is None:
            r["sem_tipo"] += 1
        elif tipo == certo["comercial"]:
            r["acertos"] += 1
        elif tipo:
            r["disse_comercial_e_e_pessoal"] += 1
        else:
            r["disse_pessoal_e_e_comercial"] += 1
    respondidos = r["com_whatsapp"] - r["sem_tipo"]
    r["acerto_pct"] = round(100 * r["acertos"] / respondidos, 1) if respondidos else 0.0
    return r


def testar(nome: str, conhecidos: dict[str, dict] | None = None, teto_usd: float | None = None) -> dict:
    """Roda o teste completo num serviço e devolve o resultado, com tempo e custo estimado."""
    if nome not in SERVICOS:
        raise ServicoIndisponivel(f"Serviço desconhecido: {nome}. Opções: {', '.join(SERVICOS)}.")
    servico = SERVICOS[nome]
    conhecidos = conhecidos if conhecidos is not None else amostra_conhecida()
    inicio = time.time()
    respostas, bruto = consultar(servico, sorted(conhecidos), teto_usd=teto_usd)
    resultado = comparar(conhecidos, respostas)
    if servico.comercial:
        resultado["comercial"] = comparar_comercial(conhecidos, servico.comercial(bruto))
    if servico.buscar and ultima_tarefa:
        resultado["tarefa"] = dict(ultima_tarefa)
    resultado.update(
        servico=nome, segundos=round(time.time() - inicio, 1),
        custo_usd=round(servico.por_mil_usd * len(conhecidos) / 1000, 3), campos_da_resposta=sorted(bruto[0]) if bruto else [],
    )
    return resultado


# ---------- verificação das listas pela CheckLeaked ----------

GRUPO = 40  # números por rodada: o andamento aparece na tela a cada rodada e parar não espera muito


def verificar(numeros: list[str], *, parar=None, avisar=None) -> dict:
    """Confere pela CheckLeaked se cada número tem WhatsApp e se a conta é pessoal ou comercial.

    `numeros` são nacionais (DDD + número), na ordem de prioridade. Quem já está no cadastro de
    verificados (30 dias) não é consultado de novo. Cada resposta vai para o cadastro (`whatsapp_tipo`),
    o mesmo que a montagem das listas lê. Para quando acabam os números, se o saldo acabar, se o
    serviço falhar ou se pedirem para parar. `avisar(texto, feitos, total)` recebe o andamento.
    """
    from . import whatsapp

    numeros = list(dict.fromkeys(n for n in numeros if n))
    con = cnpj_db.conectar()
    try:
        sabidos = whatsapp.do_cache(con, numeros)
    finally:
        con.close()
    fila = [n for n in numeros if n not in sabidos]
    resumo = {
        "numeros": len(numeros), "do_cadastro": len(numeros) - len(fila), "consultas": 0, "com_whatsapp": 0,
        "pessoais": 0, "comerciais": 0, "sem_whatsapp": 0, "sem_resposta": 0,
    }
    inicio = time.monotonic()
    aviso = avisar or (lambda texto, feitos, total: None)

    def fim(motivo: str) -> dict:
        resumo.update(motivo=motivo, minutos=round((time.monotonic() - inicio) / 60, 1), saldo=saldo_checkleaked())
        return resumo

    if not fila:
        return fim("todos os números já estavam verificados")
    if not os.getenv("CHECKLEAKED_KEY", "").strip():
        return fim("falta a chave da CheckLeaked")
    saldo = saldo_checkleaked()
    cortado = False
    if saldo is not None and saldo["restam"] < len(fila):
        fila, cortado = fila[: max(0, saldo["restam"])], True
        if not fila:
            return fim("o saldo da CheckLeaked acabou")
    total = len(fila)
    servico = SERVICOS["checkleaked"]
    aviso(f"Verificando 0 de {total} números", 0, total)
    while fila:
        if parar is not None and parar.is_set():
            return fim("parado a pedido")
        grupo, fila = fila[:GRUPO], fila[GRUPO:]
        try:
            respostas, bruto = consultar(servico, grupo)
        except ServicoIndisponivel as e:
            return fim(f"serviço indisponível: {e}")
        tipos = servico.comercial(bruto)
        resumo["consultas"] += len(grupo)
        linhas = []
        for n in grupo:
            if respostas.get(n) is None:  # sem resposta para este número: não grava nada e fica para outra vez
                resumo["sem_resposta"] += 1
                continue
            tem, comercial = bool(respostas[n]), bool(tipos.get(f"55{n}"))
            linhas.append([n, tem, tem and comercial])
            if not tem:
                resumo["sem_whatsapp"] += 1
            else:
                resumo["com_whatsapp"] += 1
                resumo["comerciais" if comercial else "pessoais"] += 1
        if linhas:
            con = cnpj_db.conectar()
            try:
                whatsapp._garantir_cache(con)
                con.executemany("INSERT OR REPLACE INTO whatsapp_tipo VALUES (?, ?, ?, '', now())", linhas)
            finally:
                con.close()
        feitos = total - len(fila)
        aviso(f"Verificando {feitos} de {total} números · {resumo['com_whatsapp']} com WhatsApp", feitos, total)
    return fim("o saldo da CheckLeaked acabou no meio" if cortado else "concluída")
