"""Testes da API. Exigem um modelo com alias 'champion' (rode src/treinamento.py antes)."""
import os

os.environ.setdefault("QF_API_KEYS", "chave-teste")
os.environ.setdefault("QF_ADMIN_KEYS", "admin-teste")
os.environ["QF_RATE_LIMIT"] = "5/minute"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

CLIENTE = {
    "cliente_id": "CUS_0xd40", "Age": 23, "Annual_Income": 19114.12, "Monthly_Inhand_Salary": 1824.84,
    "Num_Bank_Accounts": 3, "Num_Credit_Card": 4, "Interest_Rate": 3, "Num_of_Loan": 4,
    "Delay_from_due_date": 3, "Num_of_Delayed_Payment": 7, "Changed_Credit_Limit": 11.27,
    "Num_Credit_Inquiries": 4, "Outstanding_Debt": 809.98, "Credit_Utilization_Ratio": 26.82,
    "Credit_History_Age": 265, "Total_EMI_per_month": 49.57, "Amount_invested_monthly": 80.42,
    "Monthly_Balance": 312.49, "Credit_Mix": "Good", "Payment_of_Min_Amount": "No",
    "Payment_Behaviour": "High_spent_Small_value_payments",
}
H = {"X-API-Key": "chave-teste"}


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def zerar_limites():
    app.state.limiter.reset()


def test_health_sem_autenticacao(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200 and r.json()["modelo_carregado"] is True


def test_score_ok(cliente):
    r = cliente.post("/v1/score", json=CLIENTE, headers=H)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["score"] in {"Poor", "Standard", "Good"}
    assert abs(sum(corpo["probabilidades"].values()) - 1) < 1e-3
    assert corpo["modelo"]["alias"] == "champion" and "X-Request-ID" in r.headers


def test_score_lote(cliente):
    r = cliente.post("/v1/score/lote", json={"clientes": [CLIENTE, CLIENTE]}, headers=H)
    assert r.status_code == 200 and r.json()["total"] == 2


def test_sem_chave_401(cliente):
    r = cliente.post("/v1/score", json=CLIENTE)
    assert r.status_code == 401 and r.json()["erro"]["codigo"] == "chave_ausente"


def test_chave_invalida_401(cliente):
    r = cliente.post("/v1/score", json=CLIENTE, headers={"X-API-Key": "errada"})
    assert r.status_code == 401 and r.json()["erro"]["codigo"] == "chave_invalida"


def test_payload_invalido_422(cliente):
    r = cliente.post("/v1/score", json={**CLIENTE, "Age": 500}, headers=H)
    assert r.status_code == 422 and r.json()["erro"]["detalhes"][0]["campo"] == "Age"


def test_campo_desconhecido_422(cliente):
    r = cliente.post("/v1/score", json={**CLIENTE, "SSN": "821-00-0265"}, headers=H)
    assert r.status_code == 422


def test_lote_muito_grande_413(cliente):
    r = cliente.post("/v1/score/lote", json={"clientes": [CLIENTE] * 101}, headers=H)
    assert r.status_code == 413


def test_recarregar_exige_admin(cliente):
    assert cliente.post("/v1/modelo/recarregar", headers=H).status_code == 403
    assert cliente.post("/v1/modelo/recarregar", headers={"X-API-Key": "admin-teste"}).status_code == 200


def test_throttling_429(cliente):
    codigos = [cliente.get("/v1/modelo", headers=H).status_code for _ in range(6)]
    assert codigos[:5] == [200] * 5 and codigos[5] == 429
    assert "Retry-After" in cliente.get("/v1/modelo", headers=H).headers
