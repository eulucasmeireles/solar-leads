"""PDF da lista: uma capa com o resumo e, depois, um cartão por contato, no visual da página.

Desenhado direto na folha (ReportLab), sem navegador: sai rápido mesmo com milhares de contatos.
"""

from __future__ import annotations

import io
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

MAX_CONTATOS = 3000  # acima disso o arquivo passa de 400 páginas; melhor filtrar ou dividir em pastas
POR_PAGINA = 7

_FONTES = Path(__file__).parent / "fontes"
REGULAR, MEDIO, FORTE = "Inter", "Inter-Medio", "Inter-Forte"
_registradas = False

# As mesmas cores da página (tema claro).
TXT, TXT2, TXT3 = HexColor("#1D1D1F"), HexColor("#6E6E73"), HexColor("#A1A1A6")
LINHA, APOIO, APOIO2 = HexColor("#E8E8ED"), HexColor("#F5F5F7"), HexColor("#EBEBF0")
VERDE, VERDE_BG = HexColor("#1E8E3E"), HexColor("#E6F4EA")
AZUL, AZUL_BG, AZUL_FG = HexColor("#0071E3"), HexColor("#E5F0FF"), HexColor("#0A5FCC")
LARANJA, LARANJA_BG, LARANJA_FG = HexColor("#E8870E"), HexColor("#FFF1DC"), HexColor("#B25E00")
CINZA_BG, BARRA, SOL = HexColor("#F0F0F2"), HexColor("#9DBBE0"), HexColor("#F5A623")
# Fundo e letra das iniciais, como os círculos da página.
AVATARES = [
    ("#E5F0FF", "#0A5FCC"), ("#FCE4EC", "#B0224F"), ("#E0F2F1", "#0B7A70"),
    ("#FFE9D6", "#B25E00"), ("#EDE7F6", "#5B3FB5"), ("#E6F4EA", "#1E7B36"),
]

L, A = A4
MARGEM = 42
MESES = "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro".split()
JA_TEM_SOLAR = ("empresa já tem", "outra unidade da empresa tem", "outra empresa do dono tem")


def _fontes() -> None:
    global _registradas
    if not _registradas:
        for nome, arquivo in ((REGULAR, "Inter-Regular.ttf"), (MEDIO, "Inter-Medium.ttf"), (FORTE, "Inter-SemiBold.ttf")):
            pdfmetrics.registerFont(TTFont(nome, str(_FONTES / arquivo)))
        _registradas = True


# ---------- textos ----------

def num(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def titulo(texto: str | None) -> str:
    """"MONTES CLAROS DE GOIAS" -> "Montes Claros de Goias"."""
    palavras = (texto or "").strip().title().split()
    return " ".join(p.lower() if i and p in ("De", "Da", "Do", "Das", "Dos", "E") else p for i, p in enumerate(palavras))


def telefone(e164: str | None) -> str:
    d = re.sub(r"\D", "", e164 or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return e164 or ""


def _cabe(texto: str, fonte: str, tamanho: float, largura: float) -> str:
    """O texto inteiro, ou cortado com reticências para caber na largura."""
    texto = " ".join((texto or "").split())
    if pdfmetrics.stringWidth(texto, fonte, tamanho) <= largura:
        return texto
    while texto and pdfmetrics.stringWidth(texto + "…", fonte, tamanho) > largura:
        texto = texto[:-1]
    return texto.rstrip(" ,.;·-") + "…"


def _inteiro(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _nome(l: dict) -> str:
    return titulo(f"{l.get('primeiro_nome') or ''} {l.get('sobrenome') or ''}")


def _iniciais(l: dict) -> str:
    palavras = (_nome(l) or titulo(l.get("empresa"))).split()
    palavras = [p for p in palavras if p[0].isalnum()] or ["?"]
    return (palavras[0][0] + (palavras[-1][0] if len(palavras) > 1 else "")).upper()


# ---------- peças de desenho ----------

def _texto(c: Canvas, x: float, y: float, texto: str, fonte: str = REGULAR, tamanho: float = 9, cor=TXT, aperto: float = 0.0) -> float:
    """Escreve e devolve a largura. `aperto` aproxima as letras, como os títulos da página."""
    t = c.beginText(x, y)
    t.setFont(fonte, tamanho)
    t.setFillColor(cor)
    if aperto:
        t.setCharSpace(-aperto)
    t.textOut(texto)
    if aperto:
        t.setCharSpace(0)  # o aperto vale além deste texto se não for desfeito
    c.drawText(t)
    return pdfmetrics.stringWidth(texto, fonte, tamanho) - aperto * max(0, len(texto) - 1)


def _texto_dir(c: Canvas, x: float, y: float, texto: str, fonte: str = REGULAR, tamanho: float = 9, cor=TXT) -> None:
    c.setFont(fonte, tamanho)
    c.setFillColor(cor)
    c.drawRightString(x, y, texto)


def _texto_centro(c: Canvas, x: float, y: float, texto: str, fonte: str = REGULAR, tamanho: float = 9, cor=TXT) -> None:
    c.setFont(fonte, tamanho)
    c.setFillColor(cor)
    c.drawCentredString(x, y, texto)


def _selo(c: Canvas, x: float, y: float, texto: str, fundo, cor) -> float:
    """Etiqueta arredondada; devolve onde começa a próxima."""
    largura = pdfmetrics.stringWidth(texto, MEDIO, 7.4) + 14
    c.setFillColor(fundo)
    c.roundRect(x, y, largura, 15, 7.5, stroke=0, fill=1)
    _texto(c, x + 7, y + 4.6, texto, MEDIO, 7.4, cor)
    return x + largura + 5


def _anel(c: Canvas, cx: float, cy: float, fracao: float, rotulo: str, cor, legenda: tuple[str, str]) -> None:
    """Anel de progresso com o número no meio, como os do perfil na página."""
    r = 14.5
    c.setLineWidth(2.6)
    c.setStrokeColor(APOIO2)
    c.circle(cx, cy, r, stroke=1, fill=0)
    fracao = max(0.0, min(1.0, fracao))
    if fracao > 0:
        c.setStrokeColor(cor)
        c.setLineCap(1)
        c.arc(cx - r, cy - r, cx + r, cy + r, 90, -360 * min(fracao, 0.9999))
        c.setLineCap(0)
    _texto_centro(c, cx, cy - (2.9 if len(rotulo) < 4 else 2.5), rotulo, MEDIO, 8.4 if len(rotulo) < 4 else 7, TXT)
    _texto_centro(c, cx, cy - r - 11, legenda[0], REGULAR, 6.6, TXT2)
    _texto_centro(c, cx, cy - r - 19, legenda[1], REGULAR, 6.6, TXT2)


def _quadro(c: Canvas, cx: float, cy: float, rotulo: str, legenda: tuple[str, str]) -> None:
    """Número num quadrado arredondado: os fatos da empresa no público "empresa"."""
    c.setFillColor(APOIO)
    c.roundRect(cx - 15.5, cy - 15.5, 31, 31, 9, stroke=0, fill=1)
    _texto_centro(c, cx, cy - 3.2, rotulo, MEDIO, 9.5 if len(rotulo) < 3 else 8, TXT)
    _texto_centro(c, cx, cy - 14.5 - 11, legenda[0], REGULAR, 6.6, TXT2)
    _texto_centro(c, cx, cy - 14.5 - 19, legenda[1], REGULAR, 6.6, TXT2)


def _sol(c: Canvas, cx: float, cy: float, r: float = 3.4) -> None:
    c.setStrokeColor(SOL)
    c.setLineWidth(1.1)
    c.setLineCap(1)
    c.circle(cx, cy, r, stroke=1, fill=0)
    for i in range(8):
        a = math.pi * i / 4
        c.line(cx + math.cos(a) * (r + 2), cy + math.sin(a) * (r + 2), cx + math.cos(a) * (r + 3.8), cy + math.sin(a) * (r + 3.8))
    c.setLineCap(0)


def _rodape(c: Canvas, nome: str, pagina: int, paginas: int) -> None:
    c.setStrokeColor(LINHA)
    c.setLineWidth(0.6)
    c.line(MARGEM, 36, L - MARGEM, 36)
    _sol(c, MARGEM + 5, 23.5, 2.6)
    _texto(c, MARGEM + 15, 21, _cabe(f"Solar Leads  ·  {nome}", REGULAR, 7.5, 380), REGULAR, 7.5, TXT3)
    _texto_dir(c, L - MARGEM, 21, f"Página {pagina} de {paginas}", REGULAR, 7.5, TXT3)


# ---------- capa ----------

def _barras(c: Canvas, x: float, y: float, largura: float, nome: str, itens: list[tuple[str, int]]) -> None:
    _texto(c, x, y, nome, MEDIO, 9.5, TXT)
    maior = max((n for _, n in itens), default=1) or 1
    trilho_x, trilho_l = x + largura * 0.6, largura * 0.26
    for i, (rotulo, n) in enumerate(itens):
        ly = y - 22 - i * 19
        _texto(c, x, ly, _cabe(rotulo, REGULAR, 8.6, largura * 0.6 - 10), REGULAR, 8.6, TXT2)
        c.setFillColor(APOIO)
        c.roundRect(trilho_x, ly - 1, trilho_l, 7, 3.5, stroke=0, fill=1)
        c.setFillColor(BARRA)
        c.roundRect(trilho_x, ly - 1, max(7, trilho_l * n / maior), 7, 3.5, stroke=0, fill=1)
        _texto_dir(c, x + largura, ly, num(n), MEDIO, 8.6, TXT)


def _faixa(c: Canvas, x: float, y: float, largura: float, nome: str, partes: list[tuple[str, int, object]]) -> None:
    """Uma barra só, dividida em partes, com a legenda embaixo."""
    _texto(c, x, y, nome, MEDIO, 9.5, TXT)
    total = sum(n for _, n, _ in partes) or 1
    partes = [p for p in partes if p[1]]
    c.saveState()
    recorte = c.beginPath()
    recorte.roundRect(x, y - 22, largura, 9, 4.5)
    c.clipPath(recorte, stroke=0, fill=0)
    cx = x
    for _, n, cor in partes:
        w = largura * n / total
        c.setFillColor(cor)
        c.rect(cx, y - 22, w + 0.5, 9, stroke=0, fill=1)
        cx += w
    c.restoreState()
    lx = x
    for rotulo, n, cor in partes:
        c.setFillColor(cor)
        c.circle(lx + 3, y - 36.5, 3, stroke=0, fill=1)
        lx += 10
        lx += _texto(c, lx, y - 39.5, f"{rotulo}  ", REGULAR, 8.2, TXT2)
        lx += _texto(c, lx, y - 39.5, num(n), MEDIO, 8.2, TXT) + 16


def _capa(c: Canvas, linhas: list[dict], nome: str, regiao: str, publico: str, filtro: str, origem: str, paginas: int) -> None:
    # Brilho suave no topo, como o da tela inicial.
    c.saveState()
    for cx, cy, raio, cor in ((150, A + 40, 330, HexColor("#FFE9B8")), (500, A + 70, 260, HexColor("#E2DBFF"))):
        c.setFillColor(cor)
        c.setFillAlpha(0.022)
        for i in range(36):
            c.circle(cx, cy, raio * (1 - i / 36), stroke=0, fill=1)
    c.restoreState()

    agora = datetime.now()
    _sol(c, MARGEM + 6, A - 49)
    _texto(c, MARGEM + 19, A - 52.5, "Solar Leads", FORTE, 10.5, TXT)
    _texto_dir(c, L - MARGEM, A - 52.5, f"{agora.day} de {MESES[agora.month - 1]} de {agora.year}", REGULAR, 9, TXT2)

    y = A - 150
    _texto(c, MARGEM, y + 34, origem, MEDIO, 9, TXT2)
    tamanho = 30
    while tamanho > 18 and pdfmetrics.stringWidth(nome, FORTE, tamanho) > L - 2 * MARGEM - (60 if regiao else 0):
        tamanho -= 1
    nome_cabe = _cabe(nome, FORTE, tamanho, L - 2 * MARGEM - (60 if regiao else 0))
    fim = MARGEM + _texto(c, MARGEM, y, nome_cabe, FORTE, tamanho, TXT, aperto=tamanho * 0.022)
    if regiao:
        _texto(c, fim + 9, y, regiao, MEDIO, tamanho * 0.55, TXT3)
    publico_txt = "Público: empresas" if publico == "empresa" else "Público: donos (energia solar)"
    _texto(c, MARGEM, y - 22, publico_txt + (f"  ·  {filtro}" if filtro else ""), REGULAR, 10, TXT2)

    # Os números grandes.
    n = len(linhas)
    fortes = sum(1 for l in linhas if l.get("faixa") == "A")
    confirmados = sum(1 for l in linhas if l.get("whatsapp") == "sim")
    com_email = sum(1 for l in linhas if l.get("email"))
    y -= 92
    passo = (L - 2 * MARGEM) / 4
    for i, (valor, rotulo) in enumerate(
        ((n, "Contatos" if n != 1 else "Contato"), (fortes, "Contatos fortes"), (confirmados, "Com WhatsApp confirmado"), (com_email, "Com e-mail"))
    ):
        _texto(c, MARGEM + i * passo, y, num(valor), FORTE, 27, TXT, aperto=0.7)
        _texto(c, MARGEM + i * passo, y - 17, rotulo, REGULAR, 9, TXT2)
    c.setStrokeColor(LINHA)
    c.setLineWidth(0.6)
    c.line(MARGEM, y - 40, L - MARGEM, y - 40)

    # Onde estão e o que fazem.
    y -= 72
    coluna = (L - 2 * MARGEM - 34) / 2
    ufs = Counter(l.get("uf") or "" for l in linhas)
    cidades = Counter(f"{titulo(l.get('cidade'))}" + (f" · {l.get('uf')}" if len(ufs) > 1 else "") for l in linhas if l.get("cidade"))
    if len(ufs) > 1:
        onde, itens = "Estados", ufs.most_common(6)
    elif len(cidades) > 1:
        onde, itens = "Cidades", cidades.most_common(6)
    else:
        onde, itens = "Bairros", Counter(titulo(l.get("bairro")) or "Não informado" for l in linhas).most_common(6)
    _barras(c, MARGEM, y, coluna, onde, [(r or "Não informado", q) for r, q in itens])
    atividades = Counter((l.get("atividade") or "Não informada").strip() for l in linhas).most_common(6)
    _barras(c, MARGEM + coluna + 34, y, coluna, "Atividades", atividades)

    y -= 22 + 6 * 19 + 26
    _faixa(c, MARGEM, y, coluna, "Força do contato", [("Fortes", fortes, VERDE), ("Médios", n - fortes, HexColor("#D1D1D6"))])
    pessoais = sum(1 for l in linhas if l.get("whatsapp") == "sim" and l.get("tipo_whatsapp") == "pessoal")
    _faixa(
        c, MARGEM + coluna + 34, y, coluna, "WhatsApp",
        [("Pessoal", pessoais, VERDE), ("Comercial", confirmados - pessoais, AZUL), ("Não verificado", n - confirmados, HexColor("#D1D1D6"))],
    )
    if publico != "empresa":
        y -= 72
        ordem = ["Casa e empresa", "Casa", "Empresa", "Ampliação", "Baixo potencial"]
        cores = [AZUL, HexColor("#5AA9F5"), HexColor("#A8D0FA"), LARANJA, HexColor("#D1D1D6")]
        contagem = Counter(l.get("oportunidade") or "" for l in linhas)
        _faixa(c, MARGEM, y, L - 2 * MARGEM, "Onde está a oportunidade de energia solar", [(o, contagem.get(o, 0), cor) for o, cor in zip(ordem, cores)])

    # Como ler.
    itens_ler = [
        ("Contato forte ou médio", "a chance de o número ser do próprio dono, e não da empresa ou do contador."),
        ("WhatsApp confirmado", "o número foi conferido e tem conta ativa; os demais ainda não foram verificados."),
    ]
    if publico != "empresa":
        itens_ler.append(("Potencial da casa e da empresa", "nota de 0 a 10 para instalar energia solar em cada lugar."))
    itens_ler.append(("Telefone", "toque no número para abrir a conversa no WhatsApp."))
    altura = 34 + len(itens_ler) * 16
    base = 58
    c.setFillColor(APOIO)
    c.roundRect(MARGEM, base, L - 2 * MARGEM, altura, 14, stroke=0, fill=1)
    _texto(c, MARGEM + 18, base + altura - 21, "Como ler esta lista", MEDIO, 9.5, TXT)
    for i, (termo, explicacao) in enumerate(itens_ler):
        ly = base + altura - 40 - i * 16
        fim = MARGEM + 18 + _texto(c, MARGEM + 18, ly, termo, MEDIO, 8.4, TXT)
        _texto(c, fim + 5, ly, _cabe("— " + explicacao, REGULAR, 8.4, L - MARGEM - 18 - fim - 5), REGULAR, 8.4, TXT2)
    _rodape(c, f"{nome}" + (f" — {regiao}" if regiao else ""), 1, paginas)


# ---------- cartão do contato ----------

ALTURA_CARTAO, VAO = 97.0, 9.0


def _cartao(c: Canvas, x: float, topo: float, l: dict, indice: int, publico: str) -> None:
    largura, base = L - 2 * MARGEM, topo - ALTURA_CARTAO
    c.setFillColor(white)
    c.setStrokeColor(LINHA)
    c.setLineWidth(0.7)
    c.roundRect(x, base, largura, ALTURA_CARTAO, 14, stroke=1, fill=1)

    # Quem é.
    nome = _nome(l)
    fundo, letra = AVATARES[sum(map(ord, nome or l.get("empresa") or "")) % len(AVATARES)]
    c.setFillColor(HexColor(fundo))
    c.circle(x + 35, topo - 35, 17, stroke=0, fill=1)
    _texto_centro(c, x + 35, topo - 39, _iniciais(l), FORTE, 10.5, HexColor(letra))
    tx, tl = x + 63, 170
    empresa = titulo(l.get("empresa") or l.get("razao_social"))
    _texto(c, tx, topo - 27, _cabe(nome or empresa or "Sem nome", FORTE, 11, tl), FORTE, 11, TXT, aperto=0.1)
    cargo = (l.get("qualificacao_dono") or "").strip()
    sub = " · ".join(p for p in (cargo, empresa if nome else "Dono não identificado") if p)
    _texto(c, tx, topo - 41, _cabe(sub, REGULAR, 8.6, tl), REGULAR, 8.6, TXT2)
    _texto(c, tx, topo - 53.5, _cabe(l.get("atividade") or "", REGULAR, 7.8, tl), REGULAR, 7.8, TXT3)
    sx = tx
    forte = l.get("faixa") == "A"
    sx = _selo(c, sx, base + 13, "Contato forte" if forte else "Contato médio", VERDE_BG if forte else CINZA_BG, VERDE if forte else TXT2)
    if publico != "empresa":
        if l.get("solar") in JA_TEM_SOLAR:
            sx = _selo(c, sx, base + 13, "Já tem energia solar", LARANJA_BG, LARANJA_FG)
        elif l.get("oportunidade") and l["oportunidade"] != "Baixo potencial":
            sx = _selo(c, sx, base + 13, l["oportunidade"], AZUL_BG, AZUL_FG)

    # Como falar com ele.
    mx, ml = x + 246, 132
    numero = telefone(l.get("telefone"))
    w = _texto(c, mx, topo - 27, numero, MEDIO, 11.5, TXT)
    digitos = re.sub(r"\D", "", l.get("telefone") or "")
    if digitos:
        c.linkURL(f"https://wa.me/{digitos}", (mx - 2, topo - 31, mx + w + 2, topo - 16), relative=0)
    confirmado = l.get("whatsapp") == "sim"
    c.setFillColor(VERDE if confirmado else HexColor("#D1D1D6"))
    c.circle(mx + 3, topo - 38.2, 2.6, stroke=0, fill=1)
    if confirmado:
        tipo = {"pessoal": "pessoal", "business": "comercial"}.get(l.get("tipo_whatsapp") or "", "")
        _texto(c, mx + 10, topo - 41, "WhatsApp confirmado" + (f" · {tipo}" if tipo else ""), MEDIO, 7.5, VERDE)
    else:
        _texto(c, mx + 10, topo - 41, "WhatsApp não verificado", REGULAR, 7.8, TXT2)
    ly = topo - 55
    if l.get("telefone_alternativo"):
        _texto(c, mx, ly, f"Outro  {telefone(l['telefone_alternativo'])}", REGULAR, 8, TXT2)
        ly -= 12
    if l.get("email"):
        _texto(c, mx, ly, _cabe(l["email"], REGULAR, 8, ml), REGULAR, 8, TXT2)
        ly -= 12
    local = " · ".join(p for p in (titulo(l.get("bairro")), titulo(l.get("cidade")), l.get("uf") or "") if p)
    _texto(c, mx, base + 16.5, _cabe(local, REGULAR, 7.8, ml), REGULAR, 7.8, TXT3)

    # Os números.
    centros = [x + largura - 110, x + largura - 69, x + largura - 28]
    chance = _inteiro(l.get("chance_dono")) or 0
    cy = topo - 33
    _anel(c, centros[0], cy, chance / 100, f"{chance}%", VERDE, ("chance de", "ser o dono"))
    if publico == "empresa":
        anos, socios = _inteiro(l.get("idade_empresa")), _inteiro(l.get("numero_socios"))
        if anos is not None:
            _quadro(c, centros[1], cy, num(anos), ("ano de" if anos == 1 else "anos de", "empresa"))
        if socios:
            _quadro(c, centros[2], cy, num(socios), ("sócio" if socios == 1 else "sócios", ""))
    else:
        casa, emp = _inteiro(l.get("potencial_casa")), _inteiro(l.get("potencial_empresa"))
        if casa is not None:
            _anel(c, centros[1], cy, casa / 10, str(casa), AZUL, ("potencial", "da casa"))
        if emp is not None:
            _anel(c, centros[2], cy, emp / 10, str(emp), LARANJA, ("potencial", "da empresa"))
    _texto_dir(c, x + largura - 14, base + 9, f"{indice:,}".replace(",", "."), REGULAR, 6.3, TXT3)


# ---------- o arquivo ----------

def gerar_pdf(linhas: list[dict], *, nome: str, regiao: str = "", publico: str = "dono", filtro: str = "", origem: str = "Lista de contatos") -> bytes:
    """O PDF completo, em bytes. `nome` é o título da capa ("Montes Claros", "Fotovolt · Solar", nome da pasta)."""
    if len(linhas) > MAX_CONTATOS:
        raise ValueError(
            f"São {num(len(linhas))} contatos; o PDF vai até {num(MAX_CONTATOS)}. Use os filtros ou divida em pastas."
        )
    _fontes()
    saida = io.BytesIO()
    c = Canvas(saida, pagesize=A4, pageCompression=1)
    completo = nome + (f" — {regiao}" if regiao else "")
    c.setTitle(f"{completo} · Solar Leads")
    c.setAuthor("Solar Leads")
    c.setSubject(origem)
    paginas = 1 + math.ceil(len(linhas) / POR_PAGINA)
    _capa(c, linhas, nome, regiao, publico, filtro, origem, paginas)
    c.showPage()
    for inicio in range(0, len(linhas), POR_PAGINA):
        topo = A - 44
        for i, l in enumerate(linhas[inicio : inicio + POR_PAGINA]):
            _cartao(c, MARGEM, topo - i * (ALTURA_CARTAO + VAO), l, inicio + i + 1, publico)
        _rodape(c, completo, inicio // POR_PAGINA + 2, paginas)
        c.showPage()
    c.save()
    return saida.getvalue()
