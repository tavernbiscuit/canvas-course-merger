from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import OAuthCredential, utcnow
from app.security import TokenCipher


class CanvasError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        ambiguous: bool = False,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.ambiguous = ambiguous
        self.retryable = retryable


@dataclass(frozen=True)
class CanvasResponse:
    data: Any
    status_code: int
    request_id: str | None


class CanvasClient:
    def __init__(
        self,
        settings: Settings,
        access_token: str,
        *,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.access_token = access_token
        self._client = httpx.Client(
            base_url=settings.canvas_base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "CanvasCourseMerger/0.1",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CanvasClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        ambiguous_on_transport_error: bool = False,
        **kwargs: Any,
    ) -> CanvasResponse:
        for attempt in range(4):
            try:
                response = self._client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise CanvasError(
                    "Canvas request did not return a definitive response",
                    ambiguous=ambiguous_on_transport_error,
                    retryable=not ambiguous_on_transport_error,
                ) from exc

            request_id = response.headers.get("X-Request-Context-Id") or response.headers.get(
                "X-Canvas-Meta"
            )
            if response.status_code == 429 or (
                response.status_code >= 500 and not ambiguous_on_transport_error
            ):
                if attempt < 3:
                    retry_after = response.headers.get("Retry-After")
                    delay = min(float(retry_after), 8.0) if retry_after else 0.5 * (2**attempt)
                    # Keep backoff bounded; the worker will persist longer failures for retry.
                    import time

                    time.sleep(delay)
                    continue
            if response.is_error:
                detail = "Canvas API request failed"
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        detail = str(payload.get("message") or payload.get("errors") or detail)
                except ValueError:
                    pass
                raise CanvasError(
                    detail,
                    status_code=response.status_code,
                    request_id=request_id,
                    ambiguous=ambiguous_on_transport_error and response.status_code >= 500,
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
            try:
                data = response.json() if response.content else None
            except ValueError as exc:
                raise CanvasError(
                    "Canvas returned an invalid JSON response",
                    status_code=response.status_code,
                    request_id=request_id,
                ) from exc
            return CanvasResponse(data, response.status_code, request_id)
        raise AssertionError("unreachable")

    def _get_all(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url: str | None = path
        params = kwargs.pop("params", None)
        while url:
            response = self._client.get(url, params=params, **kwargs)
            params = None
            if response.is_error:
                raise CanvasError(
                    "Canvas list request failed",
                    status_code=response.status_code,
                    request_id=response.headers.get("X-Request-Context-Id"),
                )
            results.extend(response.json())
            url = response.links.get("next", {}).get("url")
        return results

    def current_user(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/users/self/profile").data

    def manageable_accounts(self) -> list[dict[str, Any]]:
        return self._get_all("/api/v1/manageable_accounts", params={"per_page": 100})

    def account_permissions(
        self,
        account_id: int,
        permissions: Sequence[str],
    ) -> dict[str, bool]:
        params = [("permissions[]", permission) for permission in permissions]
        return self._request(
            "GET", f"/api/v1/accounts/{account_id}/permissions", params=params
        ).data

    def get_account(self, account_id: int) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/accounts/{account_id}").data

    def get_section_by_sis_id(self, sis_id: str) -> dict[str, Any]:
        identifier = quote(f"sis_section_id:{sis_id}", safe=":")
        return self._request("GET", f"/api/v1/sections/{identifier}").data

    def get_course(self, course_id: int) -> dict[str, Any]:
        params = [("include[]", "term"), ("include[]", "account")]
        return self._request("GET", f"/api/v1/courses/{course_id}", params=params).data

    def create_course(
        self,
        account_id: int,
        *,
        name: str,
        course_code: str,
        term_id: int,
    ) -> CanvasResponse:
        data = {
            "course[name]": name,
            "course[course_code]": course_code,
            "course[enrollment_term_id]": str(term_id),
            "offer": "false",
            "skip_course_template": "true",
        }
        return self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/courses",
            data=data,
            ambiguous_on_transport_error=True,
        )

    def crosslist_section(self, sis_id: str, destination_course_id: int) -> CanvasResponse:
        identifier = quote(f"sis_section_id:{sis_id}", safe=":")
        return self._request(
            "POST",
            f"/api/v1/sections/{identifier}/crosslist/{destination_course_id}",
            data={"override_sis_stickiness": "true"},
            ambiguous_on_transport_error=True,
        )


class OAuthService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cipher = TokenCipher(settings)

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.settings.canvas_client_id,
            "response_type": "code",
            "redirect_uri": self.settings.oauth_callback_url,
            "state": state,
            "scope": self.settings.canvas_oauth_scopes,
        }
        return str(
            httpx.URL(f"{self.settings.canvas_base_url}/login/oauth2/auth").copy_merge_params(
                params
            )
        )

    def exchange_code(self, code: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.settings.canvas_base_url}/login/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.settings.canvas_client_id,
                "client_secret": self.settings.canvas_client_secret,
                "redirect_uri": self.settings.oauth_callback_url,
                "code": code,
            },
            timeout=30,
        )
        if response.is_error:
            raise CanvasError("Canvas OAuth code exchange failed", status_code=response.status_code)
        return response.json()

    def refresh(self, credential: OAuthCredential, db: Session) -> str:
        refresh_token = self.cipher.decrypt(credential.encrypted_refresh_token)
        if not refresh_token:
            raise CanvasError("Canvas authorization has expired; sign in again")
        response = httpx.post(
            f"{self.settings.canvas_base_url}/login/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.settings.canvas_client_id,
                "client_secret": self.settings.canvas_client_secret,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
        if response.is_error:
            raise CanvasError(
                "Canvas authorization refresh failed",
                status_code=response.status_code,
            )
        payload = response.json()
        credential.encrypted_access_token = self.cipher.encrypt(payload["access_token"]) or ""
        if payload.get("refresh_token"):
            credential.encrypted_refresh_token = self.cipher.encrypt(payload["refresh_token"])
        credential.expires_at = utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600)))
        credential.updated_at = utcnow()
        db.flush()
        return payload["access_token"]

    def access_token(self, credential: OAuthCredential, db: Session) -> str:
        expires_at = credential.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at and expires_at <= utcnow() + timedelta(minutes=2):
            return self.refresh(credential, db)
        token = self.cipher.decrypt(credential.encrypted_access_token)
        if not token:
            raise CanvasError("Canvas authorization is unavailable; sign in again")
        return token

    def revoke(self, credential: OAuthCredential) -> None:
        token = self.cipher.decrypt(credential.encrypted_access_token)
        if token:
            try:
                httpx.delete(
                    f"{self.settings.canvas_base_url}/login/oauth2/token",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
            except httpx.HTTPError:
                pass
