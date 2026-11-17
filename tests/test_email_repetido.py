"""Cada e-mail num contato só: o e-mail da empresa não se repete entre os sócios nem entre empresas."""

import duckdb

from solar_leads import pipeline


def linha(i, email, *, faixa="A", telefone=None, dono=None, tipo="corporativo"):
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    return {**base, "telefone": telefone or f"+55389990000{i:02d}", "email": email, "tipo_email": tipo if email else "",
            "faixa": faixa, "ordem": i, "dono_chave": dono or f"d{i}", "primeiro_nome": f"Pessoa{i}"}


def test_o_email_fica_no_primeiro_contato_e_sai_dos_outros():
    linhas = [linha(0, "clinica@x.com"), linha(1, "CLINICA@x.com "), linha(2, "outra@x.com"), linha(3, "clinica@x.com")]
    disparo = pipeline.sem_repetidos(linhas)
    assert pipeline.email_uma_vez(linhas, disparo) == 2
    assert [l["email"] for l in linhas] == ["clinica@x.com", "", "outra@x.com", ""]
    assert [l["tipo_email"] for l in linhas] == ["corporativo", "repetido", "corporativo", "repetido"]
    assert all(l["telefone"] for l in linhas)  # o contato continua; só o e-mail sai


def test_contato_de_disparo_tem_preferencia_sobre_linha_fora_do_disparo():
    fora = linha(0, "clinica@x.com", faixa="C")  # mais bem colocada, mas fora do disparo
    dentro = linha(1, "clinica@x.com")
    linhas = [fora, dentro]
    disparo = pipeline.sem_repetidos([l for l in linhas if l["faixa"] in ("A", "B")])
    pipeline.email_uma_vez(linhas, disparo)
    assert dentro["email"] == "clinica@x.com" and fora["email"] == ""


def test_repetido_que_saiu_do_disparo_nao_leva_o_email():
    # a mesma pessoa repetida (sai do disparo) não pode ficar com o e-mail no lugar do sócio seguinte
    a = linha(0, "a@x.com", dono="ana")
    a_de_novo = linha(1, "grupo@x.com", dono="ana")
    b = linha(2, "grupo@x.com", dono="bruno")
    linhas = [a, a_de_novo, b]
    disparo = pipeline.sem_repetidos(linhas)
    pipeline.email_uma_vez(linhas, disparo)
    assert b["email"] == "grupo@x.com" and a_de_novo["email"] == ""


def test_migracao_tira_os_repetidos_de_lista_pronta_e_guarda_copia(tmp_path):
    arquivo = tmp_path / "lista.parquet"
    pipeline.salvar_parquet([linha(0, "c@x.com"), linha(1, "c@x.com"), linha(2, "")], arquivo)
    assert pipeline.migrar(arquivo, "empresa") is True
    emails = duckdb.connect().execute(f"SELECT email, tipo_email FROM '{arquivo}' ORDER BY ordem").fetchall()
    assert emails == [("c@x.com", "corporativo"), ("", "repetido"), ("", "")]
    assert (tmp_path / "antes-garimpo" / "lista.parquet").exists()
    assert pipeline.migrar(arquivo, "empresa") is False  # segunda vez não muda nada
