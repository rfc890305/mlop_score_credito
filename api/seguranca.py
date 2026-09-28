"""Autenticação por chave de API (header X-API-Key) e throttling (slowapi).

As chaves NUNCA ficam no código: são lidas das variáveis de ambiente
  QF_API_KEYS    -> chaves dos parceiros, separadas por vírgula
  QF_ADMIN_KEYS  -> chaves administrativas (recarregar modelo)
"""
from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

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
    if not _chave_valida(chave, _chaves("QF_API_KEYS") + _chaves("QF_ADMIN_KEYS")):
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
    return "ip:" + get_remote_address(request)


limiter = Limiter(key_func=identificar_cliente, headers_enabled=True,
                  storage_uri=os.getenv("QF_RATE_LIMIT_STORAGE", "memory://"))
