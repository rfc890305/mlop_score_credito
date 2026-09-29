"""Contratos (schemas) de entrada e saída da API - validados pelo Pydantic."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CreditMix = Literal["Bad", "Standard", "Good"]
PagamentoMinimo = Literal["Yes", "No", "NM"]
ComportamentoPagamento = Literal[
    "Low_spent_Small_value_payments", "Low_spent_Medium_value_payments",
    "Low_spent_Large_value_payments", "High_spent_Small_value_payments",
    "High_spent_Medium_value_payments", "High_spent_Large_value_payments",
]


class DadosCliente(BaseModel):
    """Dados financeiros mais recentes (mês de referência) de um cliente."""
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "cliente_id": "CUS_0xd40",
        "Age": 23, "Annual_Income": 19114.12, "Monthly_Inhand_Salary": 1824.84,
        "Num_Bank_Accounts": 3, "Num_Credit_Card": 4, "Interest_Rate": 3, "Num_of_Loan": 4,
        "Delay_from_due_date": 3, "Num_of_Delayed_Payment": 7, "Changed_Credit_Limit": 11.27,
        "Num_Credit_Inquiries": 4, "Outstanding_Debt": 809.98, "Credit_Utilization_Ratio": 26.82,
        "Credit_History_Age": 265, "Total_EMI_per_month": 49.57, "Amount_invested_monthly": 80.42,
        "Monthly_Balance": 312.49, "Credit_Mix": "Good", "Payment_of_Min_Amount": "No",
        "Payment_Behaviour": "High_spent_Small_value_payments",
    }})

    cliente_id: str | None = Field(None, max_length=64,
                                   description="Identificador do cliente no parceiro (apenas devolvido na resposta)")
    Age: int = Field(..., ge=14, le=100, description="Idade em anos")
    Annual_Income: float = Field(..., ge=0, le=250_000, description="Renda anual")
    Monthly_Inhand_Salary: float = Field(..., ge=0, le=25_000, description="Salário líquido mensal")
    Num_Bank_Accounts: int = Field(..., ge=0, le=20)
    Num_Credit_Card: int = Field(..., ge=0, le=15)
    Interest_Rate: float = Field(..., ge=0, le=40, description="Taxa de juros média (%)")
    Num_of_Loan: int = Field(..., ge=0, le=15)
    Delay_from_due_date: int = Field(..., ge=-10, le=100, description="Dias médios de atraso")
    Num_of_Delayed_Payment: int = Field(..., ge=0, le=40)
    Changed_Credit_Limit: float = Field(..., ge=-50, le=50, description="Variação do limite (%)")
    Num_Credit_Inquiries: int = Field(..., ge=0, le=30)
    Outstanding_Debt: float = Field(..., ge=0, le=10_000)
    Credit_Utilization_Ratio: float = Field(..., ge=0, le=100, description="Utilização do crédito (%)")
    Credit_History_Age: int = Field(..., ge=0, le=600, description="Idade do histórico de crédito em MESES")
    Total_EMI_per_month: float = Field(..., ge=0, le=5_000)
    Amount_invested_monthly: float = Field(..., ge=0, le=9_999)
    Monthly_Balance: float = Field(..., ge=-5_000, le=5_000)
    Credit_Mix: CreditMix
    Payment_of_Min_Amount: PagamentoMinimo
    Payment_Behaviour: ComportamentoPagamento


class RequisicaoLote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientes: list[DadosCliente] = Field(..., min_length=1)


class InfoModelo(BaseModel):
    nome: str
    versao: str
    alias: str
    algoritmo: str | None = None
    run_id: str | None = None
    carregado_em: str | None = None
    f1_macro_validacao: float | None = Field(None, description="F1 macro do modelo na validação out-of-time")
    versao_release: str | None = Field(None, description="Versão da imagem publicada (tag v1.0.<n> do CI/CD)")


class ResultadoScore(BaseModel):
    cliente_id: str | None
    score: Literal["Poor", "Standard", "Good"]
    probabilidades: dict[str, float]


class RespostaScore(ResultadoScore):
    id_requisicao: str
    modelo: InfoModelo
    data_processamento: str


class RespostaLote(BaseModel):
    id_requisicao: str
    total: int
    resultados: list[ResultadoScore]
    modelo: InfoModelo
    data_processamento: str


class DetalheErro(BaseModel):
    codigo: str
    mensagem: str
    detalhes: list | dict | None = None


class RespostaErro(BaseModel):
    erro: DetalheErro
    id_requisicao: str | None = None


class RespostaSaude(BaseModel):
    status: Literal["ok", "degradado"]
    modelo_carregado: bool
    versao_modelo: str | None
