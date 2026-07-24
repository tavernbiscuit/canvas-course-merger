from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class TokenCipher:
    def __init__(self, settings: Settings):
        self._fernet = Fernet(settings.token_encryption_key.encode())

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Unable to decrypt stored Canvas credential") from exc
