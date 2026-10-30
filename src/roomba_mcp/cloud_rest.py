"""Cloud REST reads: consumables, mission history, smart maps.

These calls are AWS-SigV4 signed with the temporary credentials in the login
cache. The server never refreshes them; an expired session surfaces as
``login_expired`` and the operator re-runs ``roomba-mcp login``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .auth import LoginCache
from .errors import CloudHTTPError, LoginExpired
from .sigv4 import AwsCredentials, canonical_query, sign

MISSION_FILTER = "omit_quickly_canceled_not_scheduled"
MISSION_DONE_CODES = "dndEnd,returnHomeEnd"


@dataclass
class CloudRest:
    login: LoginCache
    timeout: float = 20.0

    def available(self) -> bool:
        return not self.login.is_expired()

    def _base(self) -> tuple[str, str]:
        raw = self.login.aws.rest_base
        if "://" not in raw:
            raw = "https://" + raw
        parts = urllib.parse.urlsplit(raw)
        if not parts.netloc:
            raise CloudHTTPError("login cache has no REST host", status=0)
        return f"{parts.scheme}://{parts.netloc}", parts.netloc

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if self.login.is_expired():
            raise LoginExpired(
                "the cloud session has expired; run 'roomba-mcp login' again",
                detail={"expiration": self.login.aws.expiration},
            )
        base, host = self._base()
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        query = canonical_query(params)
        url = base + path + (f"?{query}" if query else "")
        credentials = AwsCredentials(
            access_key_id=self.login.aws.access_key_id,
            secret_key=self.login.aws.secret_key,
            session_token=self.login.aws.session_token,
            region=self.login.aws.region,
        )
        headers = sign(method, host, path, params, payload, credentials)
        headers["Accept"] = "application/json"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload or None, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            if exc.code in (401, 403) and (
                "expired" in raw.lower() or "security token" in raw.lower()
            ):
                raise LoginExpired(
                    "the cloud session has expired; run 'roomba-mcp login' again",
                    detail={"status": exc.code},
                ) from exc
            raise CloudHTTPError(
                f"iRobot cloud returned HTTP {exc.code}",
                status=exc.code,
                body=raw,
            ) from exc
        except urllib.error.URLError as exc:
            raise CloudHTTPError(f"cannot reach the iRobot cloud: {exc.reason}", status=0) from exc
        except TimeoutError as exc:
            raise CloudHTTPError("timed out while contacting the iRobot cloud", status=0) from exc

    def parts(self) -> dict[str, Any]:
        data = self._call("GET", f"/v1/robots/{self.login.blid}/parts")
        return data if isinstance(data, dict) else {"parts": data}

    def mission_history(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self._call(
            "GET",
            f"/v1/{self.login.blid}/missionhistory",
            params={
                "filterType": MISSION_FILTER,
                "supportedDoneCodes": MISSION_DONE_CODES,
            },
        )
        missions = data if isinstance(data, list) else []
        return [item for item in missions if isinstance(item, dict)][:limit]

    def pmaps(self) -> list[dict[str, Any]]:
        data = self._call(
            "GET",
            f"/v1/{self.login.blid}/pmaps",
            params={"visible": "true", "activeDetails": "2"},
        )
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def reset_part(self, part_id: str) -> dict[str, Any]:
        """Reset one consumable counter.

        The path is observed; the request body key comes from the app's
        ``ResetAssetPartAwsSerializer`` string table (``partId``). Treat the
        outcome as derived-low-risk.
        """
        data = self._call(
            "POST",
            f"/v1/robots/{self.login.blid}/parts",
            body={"partId": str(part_id)},
        )
        return data if isinstance(data, dict) else {"result": data}
