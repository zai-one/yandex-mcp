from __future__ import annotations

import base64
import hashlib
from typing import Any

from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.transport import JsonHttpClient, ProviderError, request_hash


class YandexSearchAdapter:
    def __init__(
        self,
        base_url: str,
        operation_base_url: str,
        api_key: str,
        folder_id: str,
        http: JsonHttpClient | None = None,
        coalescer: AsyncSingleFlight | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.operation_base_url = operation_base_url.rstrip("/")
        self.api_key = api_key
        self.folder_id = folder_id
        self.http = http or JsonHttpClient()
        self.coalescer = coalescer or AsyncSingleFlight()

    def _headers(self) -> dict[str, str]:
        if not self.api_key or not self.folder_id:
            raise ProviderError("Yandex Search API credentials are not configured")
        return {"Authorization": f"Api-Key {self.api_key}", "Accept": "application/json"}

    async def wordstat_get_top(
        self, phrase: str, regions: list[str], num_phrases: int = 10
    ) -> dict[str, Any]:
        payload = {
            "phrase": phrase,
            "numPhrases": num_phrases,
            "regions": regions,
            "devices": ["DEVICE_ALL"],
            "folderId": self.folder_id,
        }
        raw = await self.coalescer.run(
            request_hash({"provider": "yandex_search", "operation": "wordstat_get_top", "payload": payload}),
            lambda: self.http.request(
                "POST",
                f"{self.base_url}/wordstat/topRequests",
                headers=self._headers(),
                payload=payload,
            ),
        )
        return {
            "raw": raw,
            "normalized": {
                "total_count": raw.get("totalCount"),
                "results": raw.get("results", []),
                "associations": raw.get("associations", []),
            },
        }

    async def submit_serp(
        self, query: str, region: str, response_format: str = "FORMAT_XML"
    ) -> dict[str, Any]:
        if response_format not in {"FORMAT_XML", "FORMAT_HTML"}:
            raise ValueError("response_format must be FORMAT_XML or FORMAT_HTML")
        payload = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query,
                "familyMode": "FAMILY_MODE_MODERATE",
                "page": "0",
                "fixTypoMode": "FIX_TYPO_MODE_ON",
            },
            "groupSpec": {"groupMode": "GROUP_MODE_FLAT", "groupsOnPage": "10", "docsInGroup": "1"},
            "maxPassages": "2",
            "region": region,
            "l10n": "LOCALIZATION_RU",
            "folderId": self.folder_id,
            "responseFormat": response_format,
        }
        return await self.http.request(
            "POST", f"{self.base_url}/web/searchAsync", headers=self._headers(), payload=payload
        )

    async def operation(self, operation_id: str) -> dict[str, Any]:
        return await self.coalescer.run(
            request_hash({"provider": "yandex_search", "operation": "operation", "id": operation_id}),
            lambda: self.http.request(
                "GET", f"{self.operation_base_url}/{operation_id}", headers=self._headers()
            ),
        )


def split_operation(operation: dict[str, Any]) -> dict[str, Any]:
    response = operation.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("rawData"), str):
        return {"raw_provenance": operation, "normalized": {"done": bool(operation.get("done"))}}
    raw_b64 = response["rawData"]
    raw_bytes = base64.b64decode(raw_b64)
    redacted = {**operation, "response": {**response}}
    redacted["response"].pop("rawData", None)
    redacted["response"]["rawDataRedacted"] = {
        "base64_length": len(raw_b64),
        "decoded_bytes": len(raw_bytes),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }
    return {
        "raw_provenance": redacted,
        "normalized": {
            "done": bool(operation.get("done")),
            "response_type": response.get("@type"),
            "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
    }
