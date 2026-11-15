"""Cópia de segurança: cria, criptografa, restaura e envia para um repositório git."""

import shutil
import sqlite3
import subprocess

import pytest

from solar_leads import backup, config, pastas

pytestmark = pytest.mark.skipif(shutil.which("gpg") is None, reason="precisa do gpg")


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    """Chave, pasta de cópias e pasta de envio só deste teste."""
    monkeypatch.setattr(backup, "CHAVE", tmp_path / "chave" / "chave-backup.txt")
    monkeypatch.setattr(backup, "LOCAL", tmp_path / "copias")
    monkeypatch.setattr(backup, "REMOTO", tmp_path / "envio")
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "gnupg"))
    (tmp_path / "gnupg").mkdir(mode=0o700)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    yield tmp_path


def uma_pasta_com_contato() -> None:
    pasta = pastas.criar("Disparo de teste")
    pastas.adicionar(pasta, [{"telefone": "+5511999990001", "primeiro_nome": "Fulano"}], "lista-x", "Cidade/UF")


def test_a_chave_nasce_sozinha_e_so_o_dono_le():
    arquivo = backup.chave()
    assert arquivo.stat().st_mode & 0o777 == 0o600
    assert len(arquivo.read_text().strip()) >= 48
    assert backup.chave().read_text() == arquivo.read_text()  # não troca a chave que já existe


def test_cria_e_restaura_o_arquivo_das_pastas(ambiente):
    uma_pasta_com_contato()
    arquivo, resumo = backup.criar()
    assert arquivo.name.startswith("solar-leads-") and arquivo.name.endswith(".tar.gz.gpg")
    assert arquivo.stat().st_mode & 0o777 == 0o600
    assert (resumo["pastas"], resumo["contatos_em_pastas"]) == (1, 1)

    volta = backup.restaurar(arquivo, ambiente / "restaurado")
    assert volta["contatos_em_pastas"] == 1
    con = sqlite3.connect(ambiente / "restaurado" / "pastas.sqlite")
    assert con.execute("SELECT telefone FROM pasta_contatos").fetchall() == [("+5511999990001",)]
    con.close()


def test_o_arquivo_criptografado_nao_mostra_os_contatos():
    uma_pasta_com_contato()
    arquivo, _ = backup.criar()
    bruto = arquivo.read_bytes()
    assert b"5511999990001" not in bruto and b"Fulano" not in bruto and b"SQLite" not in bruto


def test_com_outra_chave_nao_abre(ambiente):
    uma_pasta_com_contato()
    arquivo, _ = backup.criar()
    backup.CHAVE.write_text("outra-chave-qualquer\n")
    with pytest.raises(RuntimeError):
        backup.restaurar(arquivo, ambiente / "restaurado")


def test_nao_restaura_por_cima_de_pasta_com_arquivos(ambiente):
    arquivo, _ = backup.criar()
    (ambiente / "ocupada").mkdir()
    (ambiente / "ocupada" / "algo.txt").write_text("x")
    with pytest.raises(FileExistsError):
        backup.restaurar(arquivo, ambiente / "ocupada")


def test_leva_a_descricao_das_listas_e_as_configuracoes(ambiente, monkeypatch):
    (config.DADOS / "listas").mkdir(parents=True, exist_ok=True)
    meta = config.DADOS / "listas" / "20260101-000000-teste-xx.json"
    meta.write_text("{}")
    monkeypatch.setattr(config, "RAIZ", ambiente / "projeto")
    (ambiente / "projeto").mkdir()
    (ambiente / "projeto" / ".env").write_text("LEADS_SEGREDO=abc\n")
    try:
        arquivo, resumo = backup.criar()
    finally:
        meta.unlink()
    assert resumo["listas"] == 1 and resumo["configuracoes"] is True
    backup.restaurar(arquivo, ambiente / "restaurado")
    assert (ambiente / "restaurado" / "listas" / meta.name).exists()
    assert (ambiente / "restaurado" / "env").read_text() == "LEADS_SEGREDO=abc\n"
    assert (ambiente / "restaurado" / "env").stat().st_mode & 0o777 == 0o600


def test_guarda_so_as_ultimas_copias_no_servidor(monkeypatch):
    monkeypatch.setattr(backup, "MANTER", 2)
    backup.LOCAL.mkdir(parents=True)
    for i in range(3):
        (backup.LOCAL / f"solar-leads-2026010{i}-000000.tar.gz.gpg").write_text("velha")
    novo, _ = backup.criar()
    restantes = sorted(a.name for a in backup.LOCAL.iterdir())
    assert restantes == ["solar-leads-20260102-000000.tar.gz.gpg", novo.name]


def test_envio_sem_configurar_avisa_o_que_fazer():
    arquivo, _ = backup.criar()
    with pytest.raises(RuntimeError, match="backup conectar"):
        backup.enviar(arquivo)


def test_envia_para_o_repositorio_e_fica_sempre_com_um_commit_so(ambiente, monkeypatch):
    monkeypatch.setattr(backup, "MANTER", 2)
    destino = ambiente / "remoto.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch", "main", str(destino)], check=True)
    backup.conectar(str(destino), "Teste", "teste@example.invalid")
    nomes = []
    for i in range(3):
        copia = ambiente / f"solar-leads-2026010{i + 1}-000000.tar.gz.gpg"
        copia.write_bytes(b"cifrado %d" % i)
        nomes.append(copia.name)
        assert backup.enviar(copia) == min(i + 1, 2)

    def no_remoto(*args):
        return subprocess.run(["git", "--git-dir", str(destino), *args], capture_output=True, text=True, check=True).stdout.split()

    assert no_remoto("rev-list", "--count", "main") == ["1"]
    assert sorted(no_remoto("ls-tree", "--name-only", "main")) == sorted(["LEIA-ME.md", *nomes[1:]])
