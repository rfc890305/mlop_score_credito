import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from credit_score.processamento import FEATURES, filtrar_meses_recentes, limpar_dados  # noqa: E402


def test_limpeza_trata_valores_sujos():
    bruto = pd.DataFrame({
        "Age": ["-500", "28_", "33"],
        "Annual_Income": ["19114.12_", "abc", "5000"],
        "Num_Bank_Accounts": [1798, 3, 2],
        "Credit_History_Age": ["22 Years and 3 Months", None, "1 Years and 0 Months"],
        "Credit_Mix": ["_", "Good", "Bad"],
        "Payment_Behaviour": ["!@9#%8", "Low_spent_Small_value_payments", None],
    })
    limpo = limpar_dados(bruto)
    assert list(limpo.columns) == FEATURES
    assert np.isnan(limpo.loc[0, "Age"]) and limpo.loc[1, "Age"] == 28
    assert limpo.loc[0, "Annual_Income"] == 19114.12 and np.isnan(limpo.loc[1, "Annual_Income"])
    assert np.isnan(limpo.loc[0, "Num_Bank_Accounts"])
    assert limpo.loc[0, "Credit_History_Age"] == 267 and limpo.loc[2, "Credit_History_Age"] == 12
    assert limpo.loc[0, "Credit_Mix"] == "Desconhecido"
    assert limpo.loc[2, "Payment_Behaviour"] == "Desconhecido"


def test_filtra_meses_mais_recentes():
    df = pd.DataFrame({"Month": ["January", "March", "August", "July", "May"]})
    assert set(filtrar_meses_recentes(df, "Month", 2)["Month"]) == {"July", "August"}
