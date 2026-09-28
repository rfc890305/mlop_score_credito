# Imagem da API de score. O modelo é lido do MLflow (volume ou servidor remoto).
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config/ config/
COPY src/ src/
COPY api/ api/
# usuário sem privilégios
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
