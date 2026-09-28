"""Limpeza dos dados brutos e definição das features do modelo.

Este módulo é a ÚNICA fonte de verdade das transformações: o script de treino,
o script de inferência e a API importam daqui, evitando divergência
treino/produção (training-serving skew).

Modelo simplificado (requisito de negócio): usamos apenas 20 variáveis
financeiras/comportamentais e descartamos identificadores pessoais
(Name, SSN, Customer_ID, ID) e colunas de alta cardinalidade (Type_of_Loan).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

MESES = ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"]

CLASSES = ["Poor", "Standard", "Good"]

# Faixas plausíveis. Valores fora delas são erros de digitação/sentinelas no
# dataset original (ex.: idade -500, 1798 contas bancárias) e viram NaN,
# sendo depois imputados pela mediana.
FAIXAS_VALIDAS: dict[str, tuple[float, float]] = {
    "Age": (14, 100),
    "Annual_Income": (0, 250_000),
    "Monthly_Inhand_Salary": (0, 25_000),
    "Num_Bank_Accounts": (0, 20),
    "Num_Credit_Card": (0, 15),
    "Interest_Rate": (0, 40),
    "Num_of_Loan": (0, 15),
    "Delay_from_due_date": (-10, 100),
    "Num_of_Delayed_Payment": (0, 40),
    "Changed_Credit_Limit": (-50, 50),
    "Num_Credit_Inquiries": (0, 30),
    "Outstanding_Debt": (0, 10_000),
    "Credit_Utilization_Ratio": (0, 100),
    "Credit_History_Age": (0, 600),        # em meses
    "Total_EMI_per_month": (0, 5_000),
    "Amount_invested_monthly": (0, 9_999),  # 10000 é sentinela no dataset
    "Monthly_Balance": (-5_000, 5_000),
}

FEATURES_NUMERICAS = list(FAIXAS_VALIDAS.keys())
FEATURES_CATEGORICAS = ["Credit_Mix", "Payment_of_Min_Amount", "Payment_Behaviour"]
FEATURES = FEATURES_NUMERICAS + FEATURES_CATEGORICAS

CATEGORIAS_VALIDAS = {
    "Credit_Mix": ["Bad", "Standard", "Good"],
    "Payment_of_Min_Amount": ["Yes", "No", "NM"],
    "Payment_Behaviour": [
        "Low_spent_Small_value_payments", "Low_spent_Medium_value_payments",
        "Low_spent_Large_value_payments", "High_spent_Small_value_payments",
        "High_spent_Medium_value_payments", "High_spent_Large_value_payments",
    ],
}


def _para_numero(serie: pd.Series) -> pd.Series:
    """Converte textos como '28_' ou '_' em número (ou NaN)."""
    if pd.api.types.is_numeric_dtype(serie):
        return serie.astype(float)
    convertido = pd.to_numeric(serie.astype("string").str.strip().str.strip("_"), errors="coerce")
    return convertido.astype("float64")


def _historico_em_meses(serie: pd.Series) -> pd.Series:
    """'22 Years and 3 Months' -> 267. Aceita também valores já numéricos."""
    if pd.api.types.is_numeric_dtype(serie):
        return serie.astype(float)
    extraido = serie.astype("string").str.extract(r"(\d+)\s*Years?\s*and\s*(\d+)\s*Months?")
    meses = extraido[0].astype("float64") * 12 + extraido[1].astype("float64")
    return meses.fillna(_para_numero(serie))


def limpar_dados(df: pd.DataFrame) -> pd.DataFrame:
    """Recebe dados no formato bruto (CSV original ou payload da API) e devolve
    apenas as colunas FEATURES, com tipos corretos e valores inválidos como NaN."""
    saida = pd.DataFrame(index=df.index)
    for col in FEATURES_NUMERICAS:
        if col not in df:
            saida[col] = np.nan
            continue
        valores = _historico_em_meses(df[col]) if col == "Credit_History_Age" else _para_numero(df[col])
        minimo, maximo = FAIXAS_VALIDAS[col]
        saida[col] = valores.where(valores.between(minimo, maximo))
    for col in FEATURES_CATEGORICAS:
        valores = df[col].astype("string") if col in df else pd.Series(pd.NA, index=df.index, dtype="string")
        saida[col] = valores.where(valores.isin(CATEGORIAS_VALIDAS[col])).astype(object)
        saida[col] = saida[col].where(saida[col].notna(), "Desconhecido")
    return saida


def filtrar_meses_recentes(df: pd.DataFrame, coluna_mes: str, n_meses: int) -> pd.DataFrame:
    """Mantém apenas os N meses mais recentes presentes no dataset."""
    ordem = df[coluna_mes].map({m: i for i, m in enumerate(MESES)})
    recentes = sorted(ordem.dropna().unique())[-n_meses:]
    return df[ordem.isin(recentes)].copy()


def criar_preprocessador() -> ColumnTransformer:
    numerico = Pipeline([
        ("imputacao", SimpleImputer(strategy="median")),
        ("escala", StandardScaler()),
    ])
    categorico = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    return ColumnTransformer([
        ("num", numerico, FEATURES_NUMERICAS),
        ("cat", categorico, FEATURES_CATEGORICAS),
    ])
