"""Autocadastro de chaves (POST /v1/chaves) com código de convite."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QF_API_KEYS", "chave-teste")
os.environ.setdefault("QF_ADMIN_KEYS", "admin-teste")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import chaves_emitidas  # noqa: E402
from api.main import app  # noqa: E402

PEDIDO = {"nome": "Prof. Avaliador", "email": "Avaliador@Exemplo.com", "codigo_convite": "convite-123"}


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("QF_CHAVES_URI", str(tmp_path))
    monkeypatch.setenv("QF_CODIGO_CONVITE", "convite-123")
    app.state.limiter.reset()
    chaves_emitidas.limpar_cache()
    yield tmp_path


def test_emite_chave_que_funciona_no_score(cliente, ambiente):
    r = cliente.post("/v1/chaves", json=PEDIDO)
    assert r.status_code == 201
    corpo = r.json()
    assert len(corpo["chave"]) == 43 and corpo["chave"].startswith(corpo["prefixo"])
    assert corpo["email"] == "avaliador@exemplo.com"
    # a chave não é gravada, só o hash
    registros = list((ambiente / "chaves").glob("*.json"))
    assert len(registros) == 1 and corpo["chave"] not in registros[0].read_text()
    assert cliente.get("/v1/modelo", headers={"X-API-Key": corpo["chave"]}).status_code == 200


def test_convite_invalido_403(cliente):
    r = cliente.post("/v1/chaves", json={**PEDIDO, "codigo_convite": "errado"})
    assert r.status_code == 403 and r.json()["erro"]["codigo"] == "convite_invalido"


def test_sem_convite_configurado_503(cliente, monkeypatch):
    monkeypatch.delenv("QF_CODIGO_CONVITE")
    r = cliente.post("/v1/chaves", json=PEDIDO)
    assert r.status_code == 503 and r.json()["erro"]["codigo"] == "cadastro_indisponivel"


def test_chave_revogada_ou_expirada_401(cliente, ambiente):
    chave = cliente.post("/v1/chaves", json=PEDIDO).json()["chave"]
    registro = next((ambiente / "chaves").glob("*.json"))
    dados = json.loads(registro.read_text())
    dados["expira_em"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    registro.write_text(json.dumps(dados))
    chaves_emitidas.limpar_cache()
    assert cliente.get("/v1/modelo", headers={"X-API-Key": chave}).status_code == 401
    registro.unlink()  # revogação = apagar o registro
    chaves_emitidas.limpar_cache()
    assert cliente.get("/v1/modelo", headers={"X-API-Key": chave}).status_code == 401


def test_limite_de_cadastros_por_ip(cliente):
    codigos = [cliente.post("/v1/chaves", json={**PEDIDO, "codigo_convite": "errado"},
                            headers={"X-Forwarded-For": "1.2.3.4"}).status_code for _ in range(6)]
    assert codigos[:5] == [403] * 5 and codigos[5] == 429
    # forjar o início do X-Forwarded-For não escapa do limite (vale o último IP)
    r = cliente.post("/v1/chaves", json=PEDIDO, headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"})
    assert r.status_code == 429
