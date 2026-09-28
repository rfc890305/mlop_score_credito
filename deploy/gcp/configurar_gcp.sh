#!/usr/bin/env bash
# Configuração única do Google Cloud para o pipeline de CI/CD.
# Execute no Cloud Shell (ou com gcloud autenticado) e ajuste as 3 variáveis abaixo.
set -euo pipefail

PROJECT_ID="seu-projeto-gcp"            # ID do projeto no Google Cloud
REGION="southamerica-east1"             # São Paulo
GITHUB_REPO="rfc890305/mlop_score_credito"

SA_NAME="github-deploy"
SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
POOL="github-pool"
PROVIDER="github-provider"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

echo "1) Ativando APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

echo "2) Repositório de imagens (Artifact Registry)"
gcloud artifacts repositories create credit-score --repository-format=docker \
  --location="$REGION" --description="Imagens da API de score" || true

echo "3) Conta de serviço usada pelo GitHub Actions"
gcloud iam service-accounts create "$SA_NAME" --display-name="GitHub Actions deploy" || true
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$ROLE" >/dev/null
done

echo "4) Workload Identity Federation (GitHub -> GCP sem chave JSON)"
gcloud iam workload-identity-pools create "$POOL" --location=global \
  --display-name="GitHub" || true
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
  --workload-identity-pool="$POOL" --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$GITHUB_REPO'" || true
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO"

echo "5) Chaves da API no Secret Manager (geradas aqui, nunca vão para o GitHub)"
python3 -c "import secrets;print(secrets.token_urlsafe(32))" | tr -d '\n' | \
  gcloud secrets create qf-api-keys --data-file=- || true
python3 -c "import secrets;print(secrets.token_urlsafe(32))" | tr -d '\n' | \
  gcloud secrets create qf-admin-keys --data-file=- || true
RUNTIME_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"   # conta padrão do Cloud Run
for S in qf-api-keys qf-admin-keys; do
  gcloud secrets add-iam-policy-binding "$S" --member="serviceAccount:$RUNTIME_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo
echo "Pronto. Cadastre estas VARIÁVEIS no GitHub (Settings > Secrets and variables > Actions > Variables):"
echo "  GCP_PROJECT_ID      = $PROJECT_ID"
echo "  GCP_REGION          = $REGION"
echo "  GCP_SERVICE_ACCOUNT = $SA"
echo "  GCP_WIF_PROVIDER    = projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
echo
echo "Para ver a chave de parceiro: gcloud secrets versions access latest --secret=qf-api-keys"
