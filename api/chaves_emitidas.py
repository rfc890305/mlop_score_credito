"""Chaves emitidas pelo autocadastro (POST /v1/chaves).

Quem tem o código de convite (QF_CODIGO_CONVITE) gera a própria chave, sem que o
responsável precise rodar script. Cada chave emitida vira um registro em
QF_CHAVES_URI (bucket `gs://...` em produção ou diretório local em testes):

    chaves/<sha256 da chave>.json  ->  {nome, email, prefixo, criada_em, expira_em}

- A chave em si NUNCA é gravada: só o hash. Quem ler o bucket não consegue usá-la.
- Validade limitada (QF_VALIDADE_CHAVE_DIAS, padrão 30 dias).
- Revogar = apagar o registro (deploy/gcp/gerenciar_chaves.sh revogar-emitida).
  O cache em memória faz a revogação valer em até CACHE_SEGUNDOS.
- Não exige nova revisão do Cloud Run, ao contrário das chaves em QF_API_KEYS.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREFIXO_OBJETOS = "chaves"
CACHE_SEGUNDOS = 60
TAMANHO_CHAVE = 43  # secrets.token_urlsafe(32)


def _destino() -> str | None:
    return os.getenv("QF_CHAVES_URI") or None


def codigo_convite() -> str | None:
    return (os.getenv("QF_CODIGO_CONVITE") or "").strip() or None


def cadastro_habilitado() -> bool:
    return bool(_destino() and codigo_convite())


def convite_valido(codigo: str) -> bool:
    esperado = codigo_convite()
    return bool(esperado) and secrets.compare_digest(codigo.strip().encode(), esperado.encode())


def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


class _Armazenamento:
    """Lê/grava registros JSON num bucket do Cloud Storage ou num diretório local."""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        self._bucket = None
        if uri.startswith("gs://"):
            nome, _, prefixo = uri.removeprefix("gs://").partition("/")
            self._nome_bucket, self._prefixo = nome, prefixo.strip("/")

    def _caminho(self, nome: str) -> str:
        return f"{self._prefixo}/{nome}" if getattr(self, "_prefixo", "") else nome

    def _gcs(self):
        if self._bucket is None:
            from google.cloud import storage  # dependência só necessária com bucket
            self._bucket = storage.Client().bucket(self._nome_bucket)
        return self._bucket

    def ler(self, nome: str) -> dict | None:
        if self.uri.startswith("gs://"):
            blob = self._gcs().blob(self._caminho(nome))
            try:
                return json.loads(blob.download_as_bytes())
            except Exception as exc:  # NotFound -> chave inexistente/revogada
                if type(exc).__name__ == "NotFound":
                    return None
                raise
        arq = Path(self.uri) / nome
        return json.loads(arq.read_text()) if arq.exists() else None

    def gravar(self, nome: str, dados: dict) -> None:
        conteudo = json.dumps(dados, ensure_ascii=False)
        if self.uri.startswith("gs://"):
            # if_generation_match=0: nunca sobrescreve um registro existente
            self._gcs().blob(self._caminho(nome)).upload_from_string(
                conteudo, content_type="application/json", if_generation_match=0)
            return
        arq = Path(self.uri) / nome
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(conteudo)


_cache: dict[str, tuple[float, dict | None]] = {}
_trava = threading.Lock()


def _armazenamento() -> _Armazenamento | None:
    uri = _destino()
    return _Armazenamento(uri) if uri else None


def emitir(nome: str, email: str) -> dict:
    """Gera uma chave nova, grava só o hash e devolve a chave (única vez em que ela aparece)."""
    arm = _armazenamento()
    if arm is None:
        raise RuntimeError("QF_CHAVES_URI não configurado")
    dias = int(os.getenv("QF_VALIDADE_CHAVE_DIAS", "30"))
    chave = secrets.token_urlsafe(32)
    agora = datetime.now(timezone.utc)
    registro = {"nome": nome, "email": email, "prefixo": chave[:6],
                "criada_em": agora.isoformat(timespec="seconds"),
                "expira_em": (agora + timedelta(days=dias)).isoformat(timespec="seconds")}
    arm.gravar(f"{PREFIXO_OBJETOS}/{_hash(chave)}.json", registro)
    return {"chave": chave, **registro}


def chave_emitida_valida(chave: str) -> bool:
    arm = _armazenamento()
    if arm is None or len(chave) != TAMANHO_CHAVE:  # evita consultas ao bucket com lixo
        return False
    h = _hash(chave)
    agora = time.monotonic()
    with _trava:
        em_cache = _cache.get(h)
    if em_cache and agora - em_cache[0] < CACHE_SEGUNDOS:
        registro = em_cache[1]
    else:
        registro = arm.ler(f"{PREFIXO_OBJETOS}/{h}.json")
        with _trava:
            if len(_cache) > 10_000:
                _cache.clear()
            _cache[h] = (agora, registro)
    if not registro:
        return False
    return datetime.fromisoformat(registro["expira_em"]) > datetime.now(timezone.utc)


def limpar_cache() -> None:
    with _trava:
        _cache.clear()
