"""Teste dos serviços pagos de consulta de WhatsApp (sem chamar serviço nenhum de verdade)."""

import httpx
import pytest

from solar_leads import consulta

CONHECIDOS = {
    "38999990001": {"tem": True, "comercial": False},
    "38999990002": {"tem": True, "comercial": True},
    "38999990003": {"tem": False, "comercial": False},
    "38999990004": {"tem": False, "comercial": False},
}


def test_comparar_conta_acertos_erros_e_silencios():
    r = consulta.comparar(CONHECIDOS, {"38999990001": True, "38999990002": False, "38999990003": True, "38999990004": None})
    assert (r["total"], r["acertos"], r["sem_resposta"]) == (4, 1, 1)
    assert (r["disse_nao_tem_e_tem"], r["disse_tem_e_nao_tem"]) == (1, 1)
    assert r["acerto_pct"] == 33.3


def test_comparar_sem_nenhuma_resposta_nao_divide_por_zero():
    assert consulta.comparar(CONHECIDOS, {})["acerto_pct"] == 0.0


def test_leitura_das_respostas_de_cada_servico():
    assert consulta._vtrdev([{"phone": "+5538999990001", "hasWhatsapp": True}, {"phone": "5538999990003", "hasWhatsapp": False}]) == {
        "5538999990001": True, "5538999990003": False}
    lido = consulta._apivault([
        {"e164": "+5538999990001", "whatsapp": True, "whatsappCapable": True},
        {"e164": "+5538999990003", "whatsapp": None, "whatsappCapable": True},  # celular, mas a consulta não respondeu
    ])
    assert lido == {"5538999990001": True, "5538999990003": None}


def test_sem_chave_avisa_o_que_fazer(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(consulta.ServicoIndisponivel, match="leads segredo APIFY_TOKEN"):
        consulta.consultar(consulta.SERVICOS["vtrdev"], ["38999990001"])


def test_servico_desconhecido():
    with pytest.raises(consulta.ServicoIndisponivel, match="vtrdev"):
        consulta.testar("inexistente", CONHECIDOS)


def test_teste_completo_manda_os_numeros_com_55_e_a_chave_no_cabecalho(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    visto = {}

    def falso_post(url, **kw):
        visto.update(url=url, kw=kw)
        itens = [{"phone": n, "hasWhatsapp": n.endswith(("1", "2"))} for n in kw["json"]["phone_numbers"]]
        return httpx.Response(201, json=itens, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", falso_post)
    r = consulta.testar("vtrdev", CONHECIDOS)
    assert visto["url"].endswith("/acts/vtrdev~whatsapp-number-validator/run-sync-get-dataset-items")
    assert visto["kw"]["json"]["phone_numbers"][0] == "+5538999990001"
    assert visto["kw"]["headers"]["Authorization"].startswith("Bearer chave-de-teste-")
    assert "token" not in str(visto["kw"].get("params"))  # a chave nunca vai na URL
    assert (r["acertos"], r["acerto_pct"], r["custo_usd"]) == (4, 100.0, 0.004)
    assert r["campos_da_resposta"] == ["hasWhatsapp", "phone"]


def test_recusa_da_apify_vira_mensagem_clara(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    resposta = httpx.Response(402, json={"error": {"message": "Actor requires a paid plan"}}, request=httpx.Request("POST", "https://x"))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resposta)
    with pytest.raises(consulta.ServicoIndisponivel, match="402.*paid plan"):
        consulta.consultar(consulta.SERVICOS["vtrdev"], ["38999990001"])


def test_lista_maior_que_o_lote_e_dividida_em_varias_execucoes(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    chamadas = []

    def falso_post(url, **kw):
        chamadas.append(len(kw["json"]["phone_numbers"]))
        return httpx.Response(201, json=[{"phone": n, "hasWhatsapp": True} for n in kw["json"]["phone_numbers"]], request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", falso_post)
    numeros = [f"389999{i:05d}" for i in range(250)]
    respostas, bruto = consulta.consultar(consulta.SERVICOS["vtrdev"], numeros)
    assert chamadas == [100, 100, 50]
    assert len(bruto) == 250 and all(respostas[n] is True for n in numeros)


def test_cota_esgotada_vira_erro_em_vez_de_duzentos_sem_resposta(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    corpo = [{"error": "Trial limit reached. Upgrade to continue beyond 100."}, {"summary": {"processed": 0}}]
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(201, json=corpo, request=httpx.Request("POST", url)))
    with pytest.raises(consulta.ServicoIndisponivel, match="Trial limit reached"):
        consulta.consultar(consulta.SERVICOS["vtrdev"], ["38999990001"])


def test_aviso_junto_de_respostas_validas_nao_derruba_a_consulta(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    corpo = [{"phone": "+5538999990001", "hasWhatsapp": True}, {"error": "Trial limit reached."}]
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(201, json=corpo, request=httpx.Request("POST", url)))
    respostas, _ = consulta.consultar(consulta.SERVICOS["vtrdev"], ["38999990001", "38999990002"])
    assert respostas == {"38999990001": True, "38999990002": None}


def test_vero_le_tem_whatsapp_e_tipo_de_conta():
    itens = [
        {"number": "5538999990001", "hasWhatsApp": True, "accountType": "business"},
        {"number": "5538999990002", "hasWhatsApp": True, "accountType": "personal"},
        {"number": "5538999990003", "hasWhatsApp": False},
        {"number": "5538999990004", "hasWhatsApp": True},  # sem tipo: não vira "pessoal" por omissão
    ]
    servico = consulta.SERVICOS["vero"]
    assert servico.ler(itens) == {"5538999990001": True, "5538999990002": True, "5538999990003": False, "5538999990004": True}
    assert servico.comercial(itens) == {"5538999990001": True, "5538999990002": False}
    assert servico.entrada(["+5538999990001"]) == {"numbers": ["+5538999990001"], "pictures": False, "carrierInfo": False}


def test_comparar_comercial_so_olha_quem_tem_whatsapp():
    conhecidos = {
        "38999990001": {"tem": True, "comercial": True},
        "38999990002": {"tem": True, "comercial": False},
        "38999990003": {"tem": False, "comercial": False},
        "38999990004": {"tem": True, "comercial": True},
        "38999990005": {"tem": True, "comercial": False},
    }
    tipos = {"5538999990001": True, "5538999990002": True, "5538999990004": False}
    r = consulta.comparar_comercial(conhecidos, tipos)
    assert r == {
        "com_whatsapp": 4, "sem_tipo": 1, "acertos": 1, "disse_comercial_e_e_pessoal": 1,
        "disse_pessoal_e_e_comercial": 1, "acerto_pct": 33.3,
    }


def test_teto_de_gasto_vai_para_a_apify(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    visto = {}

    def falso_post(url, **kw):
        visto.update(kw)
        return httpx.Response(201, json=[{"number": "5538999990001", "hasWhatsApp": True, "accountType": "personal"}], request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", falso_post)
    respostas, _ = consulta.consultar(consulta.SERVICOS["vero"], ["38999990001"], teto_usd=0.4)
    assert respostas == {"38999990001": True}
    assert visto["params"]["maxTotalChargeUsd"] == 0.4


def test_execucao_sem_nenhuma_linha_vira_erro(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "chave-de-teste-" + "x" * 20)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(201, json=[], request=httpx.Request("POST", url)))
    with pytest.raises(consulta.ServicoIndisponivel, match="nenhuma resposta"):
        consulta.consultar(consulta.SERVICOS["vero"], ["38999990001"])


def test_romy_le_tem_whatsapp_e_perfil_comercial():
    itens = [
        {"phone": "5538999990001", "is_on_whatsapp": True, "business": {"address": "Rua A", "categories": ["Loja"]}},
        {"phone": "5538999990002", "is_on_whatsapp": True, "business": None},
        {"phone": "5538999990003", "is_on_whatsapp": False},
    ]
    servico = consulta.SERVICOS["romy"]
    assert servico.ler(itens) == {"5538999990001": True, "5538999990002": True, "5538999990003": False}
    assert servico.comercial(itens) == {"5538999990001": True, "5538999990002": False}


def _zip_com(nome: str, texto: str) -> bytes:
    import io, zipfile

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as z:
        z.writestr(nome, texto)
    return saida.getvalue()


def _checknumber_falsa(monkeypatch, resultado: bytes, visto: dict, estados=("processing", "exported")):
    monkeypatch.setenv("CHECKNUMBER_KEY", "chave-de-teste-" + "x" * 20)
    monkeypatch.setattr(consulta.time, "sleep", lambda s: None)
    fila = list(estados)

    def falso_post(url, **kw):
        visto.setdefault("posts", []).append((url, kw))
        pedido = httpx.Request("POST", url)
        if url.endswith("/tasks"):
            return httpx.Response(202, json={"task_id": "t1", "status": "pending", "total": 3, "estimated_amount": {"amount": "0.000360", "currency": "USD"}}, request=pedido)
        estado = fila.pop(0)
        corpo = {"task_id": "t1", "status": estado, "total": 3, "success": 3, "failure": 0}
        if estado == "exported":
            corpo |= {"result_url": "https://arquivos.exemplo.test/r.zip", "actual_amount": {"amount": "0.000360", "currency": "USD"}}
        return httpx.Response(200, json=corpo, request=pedido)

    def falso_get(url, **kw):
        visto["get"] = (url, kw)
        return httpx.Response(200, content=resultado, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "post", falso_post)
    monkeypatch.setattr(httpx, "get", falso_get)


def test_checknumber_envia_a_lista_espera_e_le_o_resultado(monkeypatch):
    visto: dict = {}
    resultado = _zip_com("resultado.csv", "number,whatsapp,business\n+5538999990001,yes,yes\n+5538999990002,yes,no\n+5538999990003,no,no\n")
    _checknumber_falsa(monkeypatch, resultado, visto)
    conhecidos = {
        "38999990001": {"tem": True, "comercial": True},
        "38999990002": {"tem": True, "comercial": False},
        "38999990003": {"tem": False, "comercial": False},
    }
    r = consulta.testar("checknumber", conhecidos)
    assert r["acertos"] == 3 and r["comercial"]["acertos"] == 2 and r["comercial"]["sem_tipo"] == 0
    assert r["tarefa"]["actual_amount"]["amount"] == "0.000360"
    url, kw = visto["posts"][0]
    assert url.endswith("/v1/tasks") and kw["data"] == {"task_type": "ws_business"}
    assert kw["files"]["file"][1] == b"+5538999990001\n+5538999990002\n+5538999990003"
    assert kw["headers"] == {"X-API-Key": "chave-de-teste-" + "x" * 20}
    assert "headers" not in visto["get"][1]  # a chave não vai para o servidor do arquivo


def test_checknumber_le_resultado_solto_com_outro_separador_e_coluna_activated():
    linhas = consulta._linhas_do_resultado("Number\tActivated\tBusiness\n+5538999990001\tyes\tno\n".encode())
    assert consulta._checknumber(linhas) == {"5538999990001": True}
    assert consulta._checknumber_comercial(linhas) == {"5538999990001": False}


def test_checknumber_formato_desconhecido_ou_tarefa_falha_vira_erro(monkeypatch):
    with pytest.raises(consulta.ServicoIndisponivel, match="formato"):
        consulta._linhas_do_resultado(_zip_com("resultado.xlsx", "x"))
    _checknumber_falsa(monkeypatch, b"", {}, estados=("failed",))
    with pytest.raises(consulta.ServicoIndisponivel, match="falhou"):
        consulta.consultar(consulta.SERVICOS["checknumber"], ["38999990001"])


def test_checknumber_sem_chave_avisa_o_que_fazer(monkeypatch):
    monkeypatch.delenv("CHECKNUMBER_KEY", raising=False)
    with pytest.raises(consulta.ServicoIndisponivel, match="leads segredo CHECKNUMBER_KEY"):
        consulta.consultar(consulta.SERVICOS["checknumber"], ["38999990001"])


def _checkleaked_falsa(monkeypatch, respostas: dict, visto: list):
    monkeypatch.setenv("CHECKLEAKED_KEY", "wapi_" + "x" * 40)

    def atende(pedido: httpx.Request) -> httpx.Response:
        visto.append(pedido)
        estado, corpo = respostas[pedido.url.path.rsplit("/", 1)[-1]]
        return httpx.Response(estado, json=corpo)

    monkeypatch.setattr(
        consulta, "_cliente_checkleaked",
        lambda chave: httpx.Client(base_url=consulta.CHECKLEAKED, headers={"x-rapidapi-key": chave}, transport=httpx.MockTransport(atende)),
    )


def test_checkleaked_le_tem_whatsapp_tipo_e_descarta_o_resto(monkeypatch):
    visto: list = []
    _checkleaked_falsa(monkeypatch, {
        "5538999990001": (200, {"isWAContact": True, "exists": True, "isBusiness": True, "about": "recado", "businessProfile": {"x": 1}, "date": "2026-11-15T10:00:00Z"}),
        "5538999990002": (200, {"isWAContact": True, "exists": True, "isBusiness": False, "profilePic": {"success": True}}),
        "5538999990003": (200, {"exists": False, "error": "Whatsapp number doesn't exist", "cached": False}),
        "5538999990004": (500, {"error": "falhou"}),
    }, visto)
    conhecidos = {
        "38999990001": {"tem": True, "comercial": True},
        "38999990002": {"tem": True, "comercial": False},
        "38999990003": {"tem": False, "comercial": False},
        "38999990004": {"tem": True, "comercial": False},
    }
    respostas, bruto = consulta.consultar(consulta.SERVICOS["checkleaked"], sorted(conhecidos))
    assert respostas == {"38999990001": True, "38999990002": True, "38999990003": False, "38999990004": None}
    assert consulta.comparar_comercial(conhecidos, consulta.SERVICOS["checkleaked"].comercial(bruto))["acertos"] == 2
    assert all(not ({"about", "businessProfile", "profilePic"} & set(linha)) for linha in bruto)
    assert all(p.url.path.startswith("/number/no_picture/") and p.headers["x-rapidapi-key"].startswith("wapi_") for p in visto)
    assert "wapi_" not in str(visto[0].url)  # a chave nunca vai no endereço


def test_checkleaked_chave_recusada_vira_erro(monkeypatch):
    _checkleaked_falsa(monkeypatch, {"5538999990001": (401, {"error": "Invalid API key"})}, [])
    with pytest.raises(consulta.ServicoIndisponivel, match="recusou a chave"):
        consulta.consultar(consulta.SERVICOS["checkleaked"], ["38999990001"])


def test_checkleaked_espaca_os_pedidos_mesmo_em_paralelo(monkeypatch):
    monkeypatch.setattr(consulta, "CHECKLEAKED_INTERVALO", 0.05)
    momentos: list = []
    _checkleaked_falsa(monkeypatch, {f"553899999000{i}": (200, {"exists": False}) for i in range(8)}, [])
    original = consulta._esperar_a_vez
    monkeypatch.setattr(consulta, "_esperar_a_vez", lambda dormir=consulta.time.sleep: (original(dormir), momentos.append(consulta.time.monotonic()))[0])
    consulta.consultar(consulta.SERVICOS["checkleaked"], [f"3899999000{i}" for i in range(8)])
    momentos.sort()
    assert len(momentos) == 8 and min(b - a for a, b in zip(momentos, momentos[1:])) >= 0.045
