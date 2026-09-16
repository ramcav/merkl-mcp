"""``GET /v1/escalations/{challenge}`` — has a human decided yet?

The same read ``merkl_trader.trader.EscalationQueue`` makes, for the same
reason: this process has no standing to approve anything and never asks to.
Approvals are signed by people through the dashboard and relayed to the
co-signer by merkl-api; what ``pending_approval`` wants back is the
co-signer's own decision, handed to whichever caller's approval completed the
quorum. When that caller was the notary rather than this process, the
settlement material never reaches here, and the tool says so in words instead
of inventing a decision nobody made.
"""

from __future__ import annotations

import httpx
from merkl.core.canonical import JSONObject


class EscalationQueue:
    def __init__(self, url: str, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, challenge: str) -> JSONObject | None:
        """The escalation, or ``None`` when the notary cannot say."""
        try:
            response = await self._client.get(
                f"{self._url}/v1/escalations/{challenge}",
                headers={"X-Merkl-API-Key": self._api_key} if self._api_key else {},
            )
        except httpx.HTTPError:
            return None
        if not response.is_success:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None


__all__ = ["EscalationQueue"]
