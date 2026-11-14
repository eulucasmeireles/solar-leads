import asyncio
import itertools

import pytest
from fastapi.testclient import TestClient

from conftest import SENHA_TESTE
from solar_leads import pipeline
from solar_leads.web import servidor

_ips = itertools.count(1)


def novo_ip() -> str:
    return f"203.0.113.{next(_ips)}"


def cliente(ip: str | None = None) -> TestClient:
    c = TestClient(servidor.app, base_url="https://testserver", follow_redirects=False)
    c.headers["X-Forwarded-For"] = ip or novo_ip()
    return c


@pytest.fixture
def anonimo():
    with cliente() as c:
        yield c


@pytest.fixture
def logado():
    """Cliente com sessão válida. Um por teste: o logout de um teste invalida as sessões dos outros."""
    with cliente() as c:
        r = c.post("/api/login", json={"senha": SENHA_TESTE})
        assert r.status_code == 200
        yield c


def pedir_raw(path: str, cookie: str | None = None) -> int:
    """GET sem normalizar o caminho (o cliente HTTP removeria os '..' antes de enviar)."""
    headers = [(b"host", b"testserver"), (b"x-forwarded-for", novo_ip().encode())]
    if cookie:
        headers.append((b"cookie", f"sl_sessao={cookie}".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 1234), "server": ("testserver", 443), "root_path": "",
    }
    status = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            status["codigo"] = msg["status"]

    asyncio.run(servidor.app(scope, receive, send))
    return status["codigo"]


# ---------- sem sessão ----------

def test_sem_sessao_a_raiz_redireciona_para_o_login(anonimo):
    r = anonimo.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_sem_sessao_a_api_responde_401(anonimo):
    r = anonimo.get("/api/listas")
    assert r.status_code == 401
    assert "Sessão expirada" in r.json()["detail"]


@pytest.mark.parametrize("caminho", ["/api/pastas", "/api/campos", "/api/exportacoes", "/api/nao-contatar", "/api/resultados"])
def test_sem_sessao_nenhuma_rota_da_api_abre(anonimo, caminho):
    assert anonimo.get(caminho).status_code == 401


def test_sem_sessao_o_codigo_da_interface_nao_e_servido(anonimo):
    r = anonimo.get("/static/app.js")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_a_tela_de_login_e_a_sessao_sao_livres(anonimo):
    assert anonimo.get("/login").status_code == 200
    r = anonimo.get("/api/sessao")
    assert r.json() == {"protegido": True, "dentro": False}


def test_fontes_woff2_sao_livres_sem_sessao(anonimo):
    r = anonimo.get("/static/fonts/inter-var.woff2")
    assert r.status_code == 200 and len(r.content) > 1000


def test_fonte_inexistente_da_404(anonimo):
    assert anonimo.get("/static/fonts/nao-existe.woff2").status_code == 404


def test_licenca_da_fonte_nao_e_livre_sem_sessao(anonimo):
    assert anonimo.get("/static/fonts/LICENCA-inter.txt").status_code == 303


@pytest.mark.parametrize(
    "caminho",
    [
        "/static/fonts/../servidor.py",
        "/static/fonts/../../servidor.py",
        "/static/fonts/../servidor.woff2",
        "/static/fonts/..\\servidor.py",
        "/static/fonts/%2e%2e/servidor.py",
        "/static/fonts/..%2fservidor.py",
        "/static/../web/servidor.py",
    ],
)
def test_caminhos_com_ponto_ponto_sao_404_sem_sessao(caminho):
    assert pedir_raw(caminho) == 404


def test_caminho_com_ponto_ponto_tambem_e_404_com_sessao(logado):
    cookie = logado.cookies.get("sl_sessao")
    assert pedir_raw("/static/fonts/../servidor.py", cookie) == 404


def test_codigo_python_nunca_e_servido_nem_com_sessao(logado):
    for caminho in ("/static/servidor.py", "/static/__init__.py", "/static/__pycache__/x.pyc", "/static/index.html"):
        assert logado.get(caminho).status_code == 404, caminho


def test_com_sessao_os_arquivos_da_interface_sao_servidos(logado):
    r = logado.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert logado.get("/static/style.css").status_code == 200


def test_com_sessao_a_raiz_serve_o_index(logado):
    r = logado.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


# ---------- login ----------

def test_login_com_senha_errada_da_401():
    with cliente() as c:
        r = c.post("/api/login", json={"senha": "errada"})
        assert r.status_code == 401 and "sl_sessao" not in c.cookies


def test_login_sem_corpo_ou_sem_senha_da_422(anonimo):
    assert anonimo.post("/api/login", json={}).status_code == 422


def test_cinco_erros_seguidos_bloqueiam_o_ip_com_429():
    with cliente() as c:
        codigos = [c.post("/api/login", json={"senha": "errada"}).status_code for _ in range(5)]
        assert codigos == [401] * 4 + [401]
        r = c.post("/api/login", json={"senha": SENHA_TESTE})  # nem a senha certa entra bloqueado
        assert r.status_code == 429 and "Muitas tentativas" in r.json()["detail"]


def test_bloqueio_de_um_ip_nao_vaza_para_outro():
    ip = novo_ip()
    with cliente(ip) as c:
        for _ in range(5):
            c.post("/api/login", json={"senha": "errada"})
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 429
    with cliente() as outro:
        assert outro.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200


def test_login_certo_zera_o_contador_de_erros():
    with cliente() as c:
        for _ in range(4):
            assert c.post("/api/login", json={"senha": "errada"}).status_code == 401
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        assert c.post("/api/login", json={"senha": "errada"}).status_code == 401  # recomeça do zero


def test_login_certo_entrega_cookie_httponly_e_seguro():
    with cliente() as c:
        r = c.post("/api/login", json={"senha": SENHA_TESTE})
        assert r.status_code == 200 and r.json() == {"ok": True}
        set_cookie = r.headers["set-cookie"]
        assert set_cookie.startswith("sl_sessao=")
        assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=lax" in set_cookie
        assert c.get("/api/sessao").json() == {"protegido": True, "dentro": True}


def test_a_chave_do_bloqueio_e_o_ultimo_ip_do_x_forwarded_for():
    """O proxy acrescenta o IP real no fim; um IP forjado no começo não escapa do bloqueio."""
    real = novo_ip()
    with cliente() as c:
        for i in range(5):
            c.post("/api/login", json={"senha": "errada"}, headers={"X-Forwarded-For": f"9.9.9.{i}, {real}"})
        r = c.post("/api/login", json={"senha": SENHA_TESTE}, headers={"X-Forwarded-For": f"8.8.8.8, {real}"})
        assert r.status_code == 429


# ---------- sessão ----------

def test_com_sessao_pastas_responde_200(logado):
    r = logado.get("/api/pastas")
    assert r.status_code == 200 and r.json() == []
    assert r.headers["cache-control"] == "no-store"


def test_depois_do_logout_o_cookie_antigo_deixa_de_valer():
    with cliente() as c:
        c.post("/api/login", json={"senha": SENHA_TESTE})
        antigo = c.cookies.get("sl_sessao")
        assert c.post("/api/logout").status_code == 200
        with cliente() as outro:
            outro.cookies.set("sl_sessao", antigo, domain="testserver.local")
            outro.headers["Cookie"] = f"sl_sessao={antigo}"
            assert outro.get("/api/pastas").status_code == 401
            assert pedir_raw("/api/pastas", antigo) == 401


def test_logout_encerra_a_sessao_de_todos_os_aparelhos():
    with cliente() as a, cliente() as b:
        a.post("/api/login", json={"senha": SENHA_TESTE})
        b.post("/api/login", json={"senha": SENHA_TESTE})
        assert b.get("/api/pastas").status_code == 200
        a.post("/api/logout")
        assert b.get("/api/pastas").status_code == 401


def test_nova_sessao_depois_do_logout_volta_a_funcionar():
    with cliente() as c:
        c.post("/api/login", json={"senha": SENHA_TESTE})
        c.post("/api/logout")
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        assert c.get("/api/pastas").status_code == 200


@pytest.mark.parametrize(
    "mudar",
    [
        lambda v, m, a: f"{v}.{m}.{'0' * 64}",           # assinatura trocada
        lambda v, m, a: f"{int(v) + 1}.{m}.{a}",         # validade alterada
        lambda v, m, a: f"{v}.outra.{a}",                # marca alterada
        lambda v, m, a: a,                               # formato errado
        lambda v, m, a: f"abc.{m}.{a}",                  # validade não numérica
        lambda v, m, a: "",
        lambda v, m, a: f"{v}.{m}.{a}.extra",
    ],
)
def test_cookie_adulterado_da_401(mudar):
    with cliente() as c:
        c.post("/api/login", json={"senha": SENHA_TESTE})
        validade, marca, assinatura = c.cookies.get("sl_sessao").split(".")
    forjado = mudar(validade, marca, assinatura)
    with cliente() as outro:
        outro.headers["Cookie"] = f"sl_sessao={forjado}"
        assert outro.get("/api/pastas").status_code == 401


def test_cookie_expirado_da_401(monkeypatch):
    with cliente() as c:
        c.post("/api/login", json={"senha": SENHA_TESTE})
        cookie = c.cookies.get("sl_sessao")
        validade = int(cookie.split(".")[0])
        monkeypatch.setattr(servidor.time, "time", lambda: validade + 1)
        assert c.get("/api/pastas").status_code == 401


def test_assinatura_depende_do_segredo(monkeypatch):
    with cliente() as c:
        c.post("/api/login", json={"senha": SENHA_TESTE})
        monkeypatch.setattr(servidor, "SEGREDO", "x" * 64)
        assert c.get("/api/pastas").status_code == 401


# ---------- rotas simples ----------

def test_campos_devolve_todos_os_campos_do_pipeline(logado):
    r = logado.get("/api/campos")
    assert r.status_code == 200
    dados = r.json()
    assert [d["chave"] for d in dados] == pipeline.CHAVES
    assert {"chave", "rotulo", "grupo", "tipo"} == set(dados[0])


@pytest.mark.parametrize("q", ["", "a", " a ", "  "])
def test_cidades_com_menos_de_duas_letras_devolve_lista_vazia(logado, q):
    r = logado.get("/api/cidades", params={"q": q})
    assert r.status_code == 200 and r.json() == []


def test_opcoes_de_resultado_listam_os_seis_resultados(logado):
    r = logado.get("/api/resultados/opcoes")
    assert r.status_code == 200 and len(r.json()) == 6


# ---------- pastas pela API ----------

def test_ciclo_de_pastas_pela_api(logado):
    r = logado.post("/api/pastas", json={"nome": "Padarias"})
    assert r.status_code == 200
    pid = r.json()["id"]

    assert logado.post("/api/pastas", json={"nome": "padarias"}).status_code == 400
    assert logado.post("/api/pastas", json={"nome": "  "}).status_code == 400

    assert logado.get(f"/api/pastas/{pid}").json()["nome"] == "Padarias"
    assert logado.patch(f"/api/pastas/{pid}", json={"nome": "Mercados"}).json() == {"ok": True}
    assert [p["nome"] for p in logado.get("/api/pastas").json()] == ["Mercados"]

    outra = logado.post("/api/pastas", json={"nome": "Outra"}).json()["id"]
    assert logado.patch(f"/api/pastas/{outra}", json={"nome": "MERCADOS"}).status_code == 400

    assert logado.delete(f"/api/pastas/{pid}").json() == {"ok": True}
    assert logado.get(f"/api/pastas/{pid}").status_code == 404
    assert [p["nome"] for p in logado.get("/api/pastas").json()] == ["Outra"]


def test_pasta_inexistente_da_404_em_renomear_e_excluir(logado):
    assert logado.patch("/api/pastas/9999", json={"nome": "X"}).status_code == 404
    assert logado.delete("/api/pastas/9999").status_code == 404


def test_pasta_com_id_nao_numerico_da_422(logado):
    assert logado.get("/api/pastas/abc").status_code == 422


def test_nome_de_pasta_longo_demais_da_400(logado):
    assert logado.post("/api/pastas", json={"nome": "x" * 81}).status_code == 400


def test_contatos_de_pasta_vazia(logado):
    pid = logado.post("/api/pastas", json={"nome": "A"}).json()["id"]
    r = logado.get(f"/api/pastas/{pid}/contatos")
    assert r.status_code == 200
