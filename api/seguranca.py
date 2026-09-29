"""Autenticação por chave de API (header X-API-Key) e throttling (slowapi).

As chaves NUNCA ficam no código: são lidas das variáveis de ambiente
  QF_API_KEYS    -> chaves dos parceiros, separadas por vírgula
  QF_ADMIN_KEYS  -> chaves administrativas (recarregar modelo)
Além delas, valem as chaves geradas pelo autocadastro (POST /v1/chaves), guardadas
só como hash em QF_CHAVES_URI (ver api/chaves_emitidas.py).
"""
from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

from . import chaves_emitidas

NOME_HEADER = "X-API-Key"
esquema_chave = APIKeyHeader(name=NOME_HEADER, auto_error=False,
                             description="Chave de API fornecida pela QuantumFinance ao parceiro")


def _chaves(variavel: str) -> list[str]:
    return [c.strip() for c in os.getenv(variavel, "").split(",") if c.strip()]


def _chave_valida(chave: str, permitidas: list[str]) -> bool:
    # compare_digest evita ataques de tempo (timing attacks)
    return any(secrets.compare_digest(chave.encode(), p.encode()) for p in permitidas)


def exigir_chave_api(chave: str | None = Security(esquema_chave)) -> str:
    if not chave:
        raise HTTPException(401, detail={"codigo": "chave_ausente",
                                         "mensagem": f"Header {NOME_HEADER} não informado."},
                            headers={"WWW-Authenticate": "ApiKey"})
    if not (_chave_valida(chave, _chaves("QF_API_KEYS") + _chaves("QF_ADMIN_KEYS"))
            or chaves_emitidas.chave_emitida_valida(chave)):
        raise HTTPException(401, detail={"codigo": "chave_invalida",
                                         "mensagem": "Chave de API inválida ou revogada."},
                            headers={"WWW-Authenticate": "ApiKey"})
    return chave


def exigir_chave_admin(chave: str = Security(exigir_chave_api)) -> str:
    if not _chave_valida(chave, _chaves("QF_ADMIN_KEYS")):
        raise HTTPException(403, detail={"codigo": "acesso_negado",
                                         "mensagem": "Esta operação exige chave administrativa."})
    return chave


def identificar_cliente(request: Request) -> str:
    """Chave do throttling: cada chave de API tem sua própria cota.
    Sem chave, a cota é contada pelo IP de origem."""
    chave = request.headers.get(NOME_HEADER)
    if chave:
        # usa o hash da chave para não manter o segredo em memória do limitador
        return "chave:" + hashlib.sha256(chave.encode()).hexdigest()[:16]
    return "ip:" + ip_origem(request)


def ip_origem(request: Request) -> str:
    """IP de quem chamou. No Cloud Run o balanceador acrescenta o IP real ao FINAL do
    X-Forwarded-For; usar o último item impede que o cliente forje um IP no header."""
    encaminhado = request.headers.get("X-Forwarded-For", "")
    if encaminhado.strip():
        return encaminhado.split(",")[-1].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=identificar_cliente, headers_enabled=True,
                  storage_uri=os.getenv("QF_RATE_LIMIT_STORAGE", "memory://"))
