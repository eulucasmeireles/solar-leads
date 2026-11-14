"""Cliente da API do gosom/google-maps-scraper (modo web, em localhost)."""

from __future__ import annotations

import csv
import io
import json
import time

import httpx

from . import config
from .telefone import normalizar


def criar_job(nome: str, buscas: list[str], profundidade: int = 10, tempo_max: int = 1800, emails: bool = False) -> str:
    corpo = {
        "name": nome,
        "keywords": buscas,
        "lang": "pt",  # a API só aceita o código de duas letras
        "zoom": 15,
        "depth": profundidade,
        "email": emails,
        "max_time": tempo_max,
    }
    r = httpx.post(f"{config.GMAPS_URL}/api/v1/jobs", json=corpo, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def status(job_id: str) -> str:
    r = httpx.get(f"{config.GMAPS_URL}/api/v1/jobs/{job_id}", timeout=30)
    r.raise_for_status()
    return str(r.json().get("status") or r.json().get("Status") or "")


def aguardar(job_id: str, limite_s: int = 3600, intervalo_s: int = 15) -> str:
    inicio = time.monotonic()
    while True:
        st = status(job_id).lower()
        if st in ("ok", "failed"):
            return st
        if time.monotonic() - inicio > limite_s:
            raise TimeoutError(f"Job {job_id} não terminou em {limite_s}s (status: {st})")
        time.sleep(intervalo_s)


def baixar(job_id: str) -> list[dict]:
    """Baixa o CSV do job e devolve os lugares já normalizados e sem repetição."""
    r = httpx.get(f"{config.GMAPS_URL}/api/v1/jobs/{job_id}/download", timeout=120)
    r.raise_for_status()
    return ler_csv(r.text)


def ler_csv(texto: str) -> list[dict]:
    vistos: set[str] = set()
    lugares: list[dict] = []
    for linha in csv.DictReader(io.StringIO(texto)):
        chave = linha.get("place_id") or linha.get("cid") or linha.get("link") or ""
        if not linha.get("title") or chave in vistos:
            continue
        vistos.add(chave)
        end = {}
        try:
            end = json.loads(linha.get("complete_address") or "{}") or {}
        except json.JSONDecodeError:
            pass
        lugares.append(
            {
                "maps_id": chave,
                "nome": linha["title"].strip(),
                "categoria": (linha.get("category") or "").strip(),
                "endereco": (linha.get("address") or "").strip(),
                "rua": (end.get("street") or "").strip(),
                "bairro": (end.get("borough") or "").strip(),
                "cidade": (end.get("city") or "").strip(),
                "uf": (end.get("state") or "").strip(),
                "cep": "".join(c for c in (end.get("postal_code") or "") if c.isdigit()),
                "telefone": normalizar(linha.get("phone")),
                "site": (linha.get("website") or "").strip(),
                "avaliacoes": int(float(linha.get("review_count") or 0)),
                "nota": float(linha.get("review_rating") or 0),
                "latitude": linha.get("latitude") or "",
                "longitude": linha.get("longitude") or "",
                "link": linha.get("link") or "",
                "emails": (linha.get("emails") or "").strip(),
            }
        )
    return lugares
