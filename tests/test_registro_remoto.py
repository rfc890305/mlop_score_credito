"""Registry persistente: a API baixa o mlflow.db da origem configurada (bucket ou diretório)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from credit_score.config import artifact_root  # noqa: E402
from credit_score.registro_remoto import baixar_registry, origem_registry  # noqa: E402


def test_baixar_registry_de_diretorio(tmp_path):
    origem = tmp_path / "bucket"
    origem.mkdir()
    (origem / "mlflow.db").write_bytes(b"conteudo-do-registry")
    destino = baixar_registry(str(origem), tmp_path / "local" / "mlflow.db")
    assert destino.read_bytes() == b"conteudo-do-registry"


def test_origem_registry_opcional(monkeypatch):
    monkeypatch.delenv("QF_REGISTRY_URI", raising=False)
    assert origem_registry() is None
    monkeypatch.setenv("QF_REGISTRY_URI", "gs://bucket-teste")
    assert origem_registry() == "gs://bucket-teste"


def test_artifact_root_aceita_bucket(monkeypatch):
    cfg = {"mlflow": {"artifact_root": "./mlartifacts"}}
    monkeypatch.delenv("QF_ARTIFACT_ROOT", raising=False)
    assert artifact_root(cfg).startswith("file://")
    monkeypatch.setenv("QF_ARTIFACT_ROOT", "gs://bucket-teste/mlartifacts")
    assert artifact_root(cfg) == "gs://bucket-teste/mlartifacts"
