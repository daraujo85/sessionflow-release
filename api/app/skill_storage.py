"""Armazenamento dos pacotes de skill (``docs/skills-distribution-contract.md``).

Interface ``SkillStorage``: ``put(name, stream, size) -> file_id`` e
``open(file_id) -> async iter bytes`` (+ ``delete`` best-effort, usado só pra
limpar órfão de publicação concorrente). Duas implementações:

- ``GoogleDriveStorage`` — produção. OAuth via refresh token (envs
  ``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET``/``GOOGLE_REFRESH_TOKEN``), pasta
  ``SKILLS_DRIVE_FOLDER_ID`` (ou acha/cria "sessionflow-skills" e loga o id).
  Upload resumable e download ``alt=media`` em streaming via httpx — o pacote
  não fica salvo no backend.
- ``LocalStorage`` — dev/testes, quando faltam as envs do Google.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

CHUNK_BYTES = 64 * 1024

class SkillStorageError(Exception):
    """Falha do provedor de storage (rede, auth, cota...)."""

class SkillStorageNotFound(SkillStorageError):
    """``file_id`` não existe no storage."""

class SkillStorage(Protocol):
    async def put(self, name: str, stream: AsyncIterator[bytes], size: int) -> str: ...

    def open(self, file_id: str) -> AsyncIterator[bytes]: ...

    async def delete(self, file_id: str) -> None: ...

class LocalStorage:
    """Pacotes num diretório local (fallback sem Google)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, file_id: str) -> Path:
        # file_id é só um nome de arquivo nosso — nunca um caminho.
        if not file_id or Path(file_id).name != file_id or file_id.startswith("."):
            raise SkillStorageNotFound(file_id)
        return self._root / file_id

    async def put(self, name: str, stream: AsyncIterator[bytes], size: int) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        file_id = f"{uuid4().hex}-{Path(name).name}"
        final = self._root / file_id
        tmp = self._root / f".{file_id}.part"
        try:
            with tmp.open("wb") as fh:
                async for chunk in stream:
                    await asyncio.to_thread(fh.write, chunk)
            tmp.replace(final)
        finally:
            tmp.unlink(missing_ok=True)
        return file_id

    async def open(self, file_id: str) -> AsyncIterator[bytes]:
        path = self._path(file_id)
        if not path.is_file():
            raise SkillStorageNotFound(file_id)
        with path.open("rb") as fh:
            while chunk := await asyncio.to_thread(fh.read, CHUNK_BYTES):
                yield chunk

    async def delete(self, file_id: str) -> None:
        try:
            self._path(file_id).unlink(missing_ok=True)
        except SkillStorageNotFound:
            pass

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_DEFAULT_FOLDER_NAME = "sessionflow-skills"
_RETRY_STATUS = {429, 500, 502, 503, 504}

class GoogleDriveStorage:
    """Pacotes no Google Drive (API v3) com OAuth refresh token."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        folder_id: str = "",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._folder_id = folder_id
        self._transport = transport
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=httpx.Timeout(60.0, connect=10.0)
        )

    async def _token(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._access_token and time.monotonic() < self._token_expires_at:
                return self._access_token
            resp = await self._send(
                client,
                "POST",
                _TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                auth=False,
            )
            if resp.status_code != 200:
                raise SkillStorageError(f"refresh do token Google falhou ({resp.status_code})")
            body = resp.json()
            self._access_token = body["access_token"]
            self._token_expires_at = time.monotonic() + int(body.get("expires_in", 3600)) - 60
            return self._access_token

    async def _send(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        auth: bool = True,
        **kwargs,
    ) -> httpx.Response:
        """Request com retry limitado (429/5xx, backoff exponencial) e um
        refresh de token em 401. Só pra corpos re-enviáveis."""
        refreshed = False
        attempt = 0
        while True:
            headers = dict(kwargs.pop("headers", None) or {})
            if auth:
                headers["Authorization"] = f"Bearer {await self._token(client)}"
            try:
                resp = await client.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                resp = None
                error: Exception | None = exc
            else:
                error = None
            kwargs["headers"] = headers
            if resp is not None and resp.status_code == 401 and auth and not refreshed:
                refreshed = True
                await self._token(client, force=True)
                continue
            attempt += 1
            retryable = resp is None or resp.status_code in _RETRY_STATUS
            if retryable and attempt < self._max_attempts:
                await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
                continue
            if resp is None:
                raise SkillStorageError(f"Google Drive inacessível: {error}") from error
            return resp

    async def _folder(self, client: httpx.AsyncClient) -> str:
        if self._folder_id:
            return self._folder_id
        query = (
            f"name = '{_DEFAULT_FOLDER_NAME}' and mimeType = '{_FOLDER_MIME}'"
            " and trashed = false"
        )
        resp = await self._send(
            client, "GET", _FILES_URL, params={"q": query, "fields": "files(id)"}
        )
        if resp.status_code != 200:
            raise SkillStorageError(f"busca da pasta no Drive falhou ({resp.status_code})")
        files = resp.json().get("files") or []
        if files:
            self._folder_id = files[0]["id"]
        else:
            resp = await self._send(
                client,
                "POST",
                _FILES_URL,
                params={"fields": "id"},
                json={"name": _DEFAULT_FOLDER_NAME, "mimeType": _FOLDER_MIME},
            )
            if resp.status_code not in (200, 201):
                raise SkillStorageError(f"criação da pasta no Drive falhou ({resp.status_code})")
            self._folder_id = resp.json()["id"]
        logger.warning(
            "skills: pasta do Drive '%s' id=%s — fixe SKILLS_DRIVE_FOLDER_ID no .env",
            _DEFAULT_FOLDER_NAME,
            self._folder_id,
        )
        return self._folder_id

    async def put(self, name: str, stream: AsyncIterator[bytes], size: int) -> str:
        async with self._client() as client:
            folder = await self._folder(client)
            init = await self._send(
                client,
                "POST",
                _UPLOAD_URL,
                params={"uploadType": "resumable", "fields": "id"},
                headers={
                    "X-Upload-Content-Type": "application/gzip",
                    "X-Upload-Content-Length": str(size),
                },
                json={"name": name, "parents": [folder], "mimeType": "application/gzip"},
            )
            session_url = init.headers.get("location")
            if init.status_code != 200 or not session_url:
                raise SkillStorageError(f"início do upload no Drive falhou ({init.status_code})")
            # Corpo é um stream de uso único: sem retry aqui. Falhou → erro e
            # o publish não registra versão nenhuma.
            token = await self._token(client)
            try:
                resp = await client.put(
                    session_url,
                    content=stream,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Length": str(size),
                        "Content-Type": "application/gzip",
                    },
                )
            except httpx.TransportError as exc:
                raise SkillStorageError(f"upload no Drive falhou: {exc}") from exc
            if resp.status_code not in (200, 201):
                raise SkillStorageError(f"upload no Drive falhou ({resp.status_code})")
            return resp.json()["id"]

    async def open(self, file_id: str) -> AsyncIterator[bytes]:
        async with self._client() as client:
            for attempt in range(1, self._max_attempts + 1):
                token = await self._token(client, force=attempt > 1)
                request = client.build_request(
                    "GET",
                    f"{_FILES_URL}/{file_id}",
                    params={"alt": "media"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                try:
                    resp = await client.send(request, stream=True)
                except httpx.TransportError as exc:
                    if attempt == self._max_attempts:
                        raise SkillStorageError(f"download do Drive falhou: {exc}") from exc
                    await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
                    continue
                if resp.status_code == 200:
                    try:
                        async for chunk in resp.aiter_bytes(CHUNK_BYTES):
                            yield chunk
                    finally:
                        await resp.aclose()
                    return
                await resp.aclose()
                if resp.status_code == 404:
                    raise SkillStorageNotFound(file_id)
                if (
                    resp.status_code in _RETRY_STATUS or resp.status_code == 401
                ) and attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
                    continue
                raise SkillStorageError(f"download do Drive falhou ({resp.status_code})")

    async def delete(self, file_id: str) -> None:
        async with self._client() as client:
            resp = await self._send(client, "DELETE", f"{_FILES_URL}/{file_id}")
            if resp.status_code not in (200, 204, 404):
                raise SkillStorageError(f"remoção no Drive falhou ({resp.status_code})")

def build_skill_storage(settings: Settings) -> SkillStorage:
    """Drive quando as 3 credenciais existem; senão LocalStorage."""
    if settings.google_drive_configured:
        return GoogleDriveStorage(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_refresh_token,
            settings.skills_drive_folder_id,
        )
    root = settings.skills_local_dir or str(Path(settings.uploads_dir) / "skills")
    return LocalStorage(root)
