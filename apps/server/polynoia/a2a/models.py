"""Internal A2A discovery models and stable public error categories."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel


class A2AError(Exception):
    """Error safe to translate into an API response or PAP failure."""

    def __init__(self, category: str, message: str, status_code: int = 400):
        super().__init__(f"{category}: {message}")
        self.category = category
        self.message = message
        self.status_code = status_code

    def as_detail(self) -> dict[str, str]:
        return {"category": self.category, "message": self.message}


class DiscoveredAgent(BaseModel):
    """Validated Agent Card plus the selected callable interface."""

    locator: str
    card_url: str
    endpoint_url: str
    protocol_binding: Literal["JSONRPC", "HTTP+JSON"]
    protocol_version: str
    card: dict[str, Any]
    card_hash: str
    etag: str | None = None
    signature_status: Literal["signed_valid", "unsigned"]
    installable: bool
    auth_kind: Literal["none", "bearer", "unsupported"]
    unsupported_auth_reason: str | None = None


class DiscoveryProvider(Protocol):
    async def discover(self, locator: str) -> DiscoveredAgent:
        raise NotImplementedError
