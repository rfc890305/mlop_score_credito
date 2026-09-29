# Imagem para o Google Cloud Run.
# A imagem leva só o código da API. O modelo NÃO é treinado aqui: ao iniciar, a API
# baixa o registry do MLflow do Cloud Storage (QF_REGISTRY_URI) e carrega o modelo
# com alias champion. Assim, a versão do modelo segue o registry persistente, e um
# rollback ou nova promoção não exige rebuild da imagem.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MLFLOW_DISABLE_AGENT_HINT=1
# Versão da release (v1.0.<n>), informada pelo CI e devolvida em GET /v1/modelo
ARG VERSAO_RELEASE=dev
ENV QF_VERSAO_RELEASE=${VERSAO_RELEASE}
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config/ config/
COPY src/ src/
COPY api/ api/
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
# O Cloud Run informa a porta na variável PORT
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
