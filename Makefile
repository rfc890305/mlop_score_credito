# Atalhos do ciclo de vida. Uso: make <alvo>
PY ?= python

instalar:          ## cria o ambiente e instala dependências
	$(PY) -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt

treinar:           ## treina, rastreia no MLflow e promove se passar nos critérios
	$(PY) src/treinamento.py

inferir:           ## inferência em lote com o modelo em produção (alias champion)
	$(PY) src/inferencia.py

versoes:           ## lista as versões do Model Registry
	$(PY) src/promover_modelo.py --listar

mlflow-ui:         ## interface do MLflow em http://localhost:5000
	mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000

api:               ## sobe a API local em http://localhost:8000 (lê chaves do .env)
	set -a && . ./.env && set +a && uvicorn api.main:app --host 0.0.0.0 --port 8000

testar:            ## testes automatizados (exige um modelo champion)
	$(PY) -m pytest -q tests

.PHONY: instalar treinar inferir versoes mlflow-ui api testar
