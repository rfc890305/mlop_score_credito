#!/usr/bin/env bash
# Gestão das chaves de parceiro da API (segredo qf-api-keys no Secret Manager).
# Execute no Cloud Shell. Cada parceiro (ou avaliador) recebe uma chave própria,
# que pode ser revogada sem afetar as demais.
#
#   bash deploy/gcp/gerenciar_chaves.sh listar
#   bash deploy/gcp/gerenciar_chaves.sh criar
#   bash deploy/gcp/gerenciar_chaves.sh revogar <primeiros caracteres da chave>
#
# Depois de criar ou revogar, o script atualiza o Cloud Run para ler a nova versão
# do segredo (o Cloud Run só lê segredos ao criar uma revisão).
set -euo pipefail

PROJECT_ID="consultorfinanceiroai"
REGION="southamerica-east1"
SERVICO="api-score-credito"
SEGREDO="qf-api-keys"

chaves_atuais() {  # uma chave por linha (o segredo é gravado sem quebra de linha no final)
  { gcloud secrets versions access latest --secret="$SEGREDO" --project="$PROJECT_ID"; echo; } \
    | tr ',' '\n' | sed 's/[[:space:]]//g; /^$/d'
}

gravar() {  # recebe as chaves (uma por linha) e grava como nova versão do segredo
  paste -sd, - | tr -d '\n' | gcloud secrets versions add "$SEGREDO" --data-file=- --project="$PROJECT_ID" >/dev/null
  echo "Atualizando o Cloud Run para usar a nova versão do segredo..."
  gcloud run services update "$SERVICO" --region="$REGION" --project="$PROJECT_ID" \
    --update-secrets=QF_API_KEYS="$SEGREDO":latest --quiet >/dev/null
  echo "Pronto: a nova revisão já está atendendo."
}

case "${1:-}" in
  listar)
    echo "Chaves ativas (mostradas só as pontas):"
    chaves_atuais | awk '{ printf "  %d) %s...%s (%d caracteres)\n", NR, substr($0,1,6), substr($0,length($0)-3), length($0) }'
    ;;
  criar)
    NOVA=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    TODAS=$(chaves_atuais; echo "$NOVA")
    echo "$TODAS" | gravar
    echo
    echo "Nova chave (copie apenas a linha entre os marcadores e envie por um canal privado):"
    echo "----------------------------------------------------------------"
    echo "$NOVA"
    echo "----------------------------------------------------------------"
    echo "Tamanho: ${#NOVA} caracteres. Para revogar depois: bash $0 revogar ${NOVA:0:6}"
    ;;
  revogar)
    PREFIXO="${2:?informe os primeiros caracteres da chave a revogar}"
    ENCONTRADAS=$(chaves_atuais | grep -c "^$PREFIXO" || true)
    [ "$ENCONTRADAS" -eq 0 ] && { echo "Nenhuma chave começa com '$PREFIXO'."; exit 1; }
    RESTANTES=$(chaves_atuais | grep -v "^$PREFIXO" || true)
    [ -z "$RESTANTES" ] && { echo "Não é possível revogar a última chave ativa. Crie outra antes."; exit 1; }
    echo "$RESTANTES" | gravar
    echo "Chave(s) iniciada(s) por '$PREFIXO' revogada(s)."
    ;;
  *)
    sed -n '2,10p' "$0"; exit 1 ;;
esac
