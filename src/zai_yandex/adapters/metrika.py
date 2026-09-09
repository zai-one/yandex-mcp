from __future__ import annotations

from datetime import date
from typing import Any

from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.transport import JsonHttpClient, ProviderError, request_hash

ALLOWED_STAT_PARAMS = frozenset(
    {"ids", "date1", "date2", "metrics", "dimensions", "accuracy", "limit", "offset", "filters", "sort"}
)

# Guarded Metrika Management API write allowlist for goals. A new writable
# operation stays inert until it is added here on review. Deletes are
# irreversible and carry the high tier; create/update carry medium.
METRIKA_WRITE_ALLOWLIST: dict[str, str] = {
    "create_goal": "medium",
    "update_goal": "medium",
    "delete_goal": "high",
}
REQUIRED_GOAL_FIELDS = ("name", "type", "conditions")
_GOAL_BODY_OPERATIONS = frozenset({"create_goal", "update_goal"})
_GOAL_ID_OPERATIONS = frozenset({"update_goal", "delete_goal"})


class YandexMetrikaAdapter:
    """Bounded read-only Yandex Metrika Management and Reporting adapter."""

    def __init__(
        self,
        base_url: str,
        token: str,
        http: JsonHttpClient | None = None,
        coalescer: AsyncSingleFlight | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or JsonHttpClient(timeout=60)
        self.coalescer = coalescer or AsyncSingleFlight()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ProviderError("Yandex Metrika token is not configured")
        return {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}

    async def counters(self) -> dict[str, Any]:
        return await self.coalescer.run(
            request_hash({"provider": "yandex_metrika", "operation": "counters"}),
            lambda: self.http.request(
                "GET", f"{self.base_url}/management/v1/counters", headers=self._headers()
            ),
        )

    async def goals(self, counter_id: str) -> dict[str, Any]:
        normalized = self.validate_counter_id(counter_id)
        return await self.coalescer.run(
            request_hash({"provider": "yandex_metrika", "operation": "goals", "counter_id": normalized}),
            lambda: self.http.request(
                "GET",
                f"{self.base_url}/management/v1/counter/{normalized}/goals",
                headers=self._headers(),
            ),
        )

    async def statistics(self, params: dict[str, Any]) -> dict[str, Any]:
        values = self.validate_statistics(params)
        return await self.coalescer.run(
            request_hash({"provider": "yandex_metrika", "operation": "statistics", "params": values}),
            lambda: self.http.request(
                "GET",
                f"{self.base_url}/stat/v1/data",
                headers=self._headers(),
                params=values,
            ),
        )

    @staticmethod
    def validate_counter_id(counter_id: str) -> str:
        normalized = counter_id.strip()
        if not normalized.isdigit() or int(normalized) <= 0:
            raise ValueError("counter_id must be a positive integer")
        return normalized

    @staticmethod
    def validate_statistics(params: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(params) - ALLOWED_STAT_PARAMS)
        if unknown:
            raise ValueError("unsupported Metrika parameters: " + ", ".join(unknown))
        required = {"ids", "date1", "date2", "metrics"}
        missing = sorted(key for key in required if not str(params.get(key, "")).strip())
        if missing:
            raise ValueError("missing Metrika parameters: " + ", ".join(missing))
        start = date.fromisoformat(str(params["date1"]))
        end = date.fromisoformat(str(params["date2"]))
        if end < start:
            raise ValueError("date2 must not precede date1")
        for key, maximum in [("filters", 10000), ("sort", 2000)]:
            if key in params:
                value = params[key]
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > maximum
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)
                ):
                    raise ValueError("invalid Metrika " + key)
        return dict(params)

    @staticmethod
    def risk_tier(operation: str) -> str:
        """Return the write risk tier for an operation (``unknown`` if unlisted)."""
        return METRIKA_WRITE_ALLOWLIST.get(str(operation).strip(), "unknown")

    @staticmethod
    def validate_write(
        operation: str, counter_id: Any, params: dict[str, Any] | None
    ) -> tuple[str, int, dict[str, Any]]:
        """Fail-closed validation for a Metrika goal write before any admission.

        Only an allowlisted operation, a positive integer ``counter_id`` and a
        goal body carrying the required fields (``name``, ``type``,
        ``conditions``) for create/update pass; delete carries no body. Params
        may be the goal object directly or wrapped as ``{"goal": {...}}``. Runs
        as the pre-admission validator, so a bad write never opens the provider
        circuit.
        """
        normalized = str(operation).strip()
        if normalized not in METRIKA_WRITE_ALLOWLIST:
            raise ProviderError("Metrika operation is not in the write allowlist")
        if isinstance(counter_id, bool) or not isinstance(counter_id, int) or counter_id <= 0:
            raise ProviderError("Metrika write requires a positive integer counter_id")
        values = dict(params or {})
        if normalized not in _GOAL_BODY_OPERATIONS:
            return normalized, counter_id, {}
        wrapped = values.get("goal")
        goal_body = wrapped if isinstance(wrapped, dict) else values
        missing = [field for field in REQUIRED_GOAL_FIELDS if field not in goal_body]
        if missing:
            raise ProviderError("Metrika goal requires fields: " + ", ".join(missing))
        name = goal_body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProviderError("Metrika goal name must be a non-empty string")
        goal_type = goal_body.get("type")
        if not isinstance(goal_type, str) or not goal_type.strip():
            raise ProviderError("Metrika goal type must be a non-empty string")
        conditions = goal_body.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise ProviderError("Metrika goal conditions must be a non-empty list")
        return normalized, counter_id, dict(goal_body)

    @staticmethod
    def _validate_goal_id(operation: str, goal_id: int | None) -> int | None:
        if operation in _GOAL_ID_OPERATIONS:
            if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id <= 0:
                raise ProviderError(f"Metrika {operation} requires a positive integer goal_id")
            return goal_id
        if goal_id is not None:
            raise ProviderError("Metrika create_goal must not carry a goal_id")
        return None

    async def apply_write(
        self,
        operation: str,
        counter_id: int,
        params: dict[str, Any] | None = None,
        goal_id: int | None = None,
    ) -> dict[str, Any]:
        """Execute one allowlisted Metrika goal write (create/update/delete).

        Selects the corresponding HTTP verb (POST/PUT/DELETE). A production
        mutation bypasses the read coalescer and is issued exactly once: it is
        never coalesced or retried blindly. Returns a JSON-safe envelope
        carrying the goal id for idempotency and readback.
        """
        normalized, normalized_counter, goal_body = self.validate_write(operation, counter_id, params)
        normalized_goal_id = self._validate_goal_id(normalized, goal_id)
        headers = self._headers()
        base = f"{self.base_url}/management/v1/counter/{normalized_counter}"
        if normalized == "create_goal":
            method, url, payload = "POST", f"{base}/goals", {"goal": goal_body}
        elif normalized == "update_goal":
            method, url, payload = "PUT", f"{base}/goal/{normalized_goal_id}", {"goal": goal_body}
        else:  # delete_goal
            method, url, payload = "DELETE", f"{base}/goal/{normalized_goal_id}", None
        response = await self.http.request(method, url, headers=headers, payload=payload)
        if isinstance(response.get("errors"), list) and response["errors"]:
            raise ProviderError("Yandex Metrika write returned an error envelope")
        applied_goal_id = normalized_goal_id
        if applied_goal_id is None:
            goal = response.get("goal")
            candidate = goal.get("id") if isinstance(goal, dict) else None
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                applied_goal_id = candidate
        return {
            "provider": "yandex_metrika",
            "operation": normalized,
            "counter_id": normalized_counter,
            "goal_id": applied_goal_id,
            "risk_tier": METRIKA_WRITE_ALLOWLIST[normalized],
            "applied": True,
            "response": response,
        }

    async def readback(self, counter_id: int, goal_id: int | None = None) -> dict[str, Any]:
        """Re-read a goal (or the goals list) after a write to confirm state.

        With ``goal_id`` it re-reads that single goal; without it, the full
        goals list. After a delete, read the list to confirm the goal is gone.
        """
        if isinstance(counter_id, bool) or not isinstance(counter_id, int) or counter_id <= 0:
            raise ProviderError("Metrika readback requires a positive integer counter_id")
        if goal_id is None:
            return await self.goals(str(counter_id))
        if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id <= 0:
            raise ProviderError("Metrika readback goal_id must be a positive integer")
        return await self.coalescer.run(
            request_hash(
                {
                    "provider": "yandex_metrika",
                    "operation": "goal",
                    "counter_id": counter_id,
                    "goal_id": goal_id,
                }
            ),
            lambda: self.http.request(
                "GET",
                f"{self.base_url}/management/v1/counter/{counter_id}/goal/{goal_id}",
                headers=self._headers(),
            ),
        )
