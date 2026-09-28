# Imagem para o Google Cloud Run.
# O modelo é treinado DURANTE o build: a imagem já sai com o registry do MLflow
# (mlflow.db + mlartifacts) e com o alias champion apontando para o modelo aprovado.
# Assim cada imagem é imutável: versão do código + versão do modelo + dados usados.
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MLFLOW_DISABLE_AGENT_HINT=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config/ config/
COPY src/ src/
COPY api/ api/
COPY data/raw/train.csv data/raw/train.csv
# Treina, rastreia e promove. O build falha se não houver modelo champion ao final.
RUN python src/treinamento.py && python src/promover_modelo.py --listar | grep -q champion \
    && rm -f data/raw/train.csv
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
# O Cloud Run informa a porta na variável PORT
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
