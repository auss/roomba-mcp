"""AWS Signature Version 4 for the iRobot API Gateway.

Only the headers the iRobot gateway requires are signed: ``host``,
``x-amz-content-sha256``, ``x-amz-date`` and ``x-amz-security-token``. The
temporary credentials come from the login cache.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SERVICE = "execute-api"
ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_HEADERS = "host;x-amz-content-sha256;x-amz-date;x-amz-security-token"


@dataclass(frozen=True)
class AwsCredentials:
    access_key_id: str
    secret_key: str
    session_token: str
    region: str = "us-east-1"


def _quote(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def canonical_query(params: Mapping[str, Any] | None) -> str:
    """Sort and percent-encode exactly the way SigV4 expects.

    ``urllib.parse.urlencode`` is wrong here: it uses ``+`` for spaces and
    leaves commas alone, which produces a signature mismatch on
    ``supportedDoneCodes=dndEnd,returnHomeEnd``.
    """
    if not params:
        return ""
    items = sorted((_quote(key), _quote(value)) for key, value in params.items())
    return "&".join(f"{key}={value}" for key, value in items)


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sign(
    method: str,
    host: str,
    path: str,
    params: Mapping[str, Any] | None,
    body: bytes,
    credentials: AwsCredentials,
    *,
    service: str = SERVICE,
    now: datetime.datetime | None = None,
) -> dict[str, str]:
    moment = now or datetime.datetime.now(datetime.UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    datestamp = moment.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    token = credentials.session_token
    query = canonical_query(params)

    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{token}\n"
    )
    canonical_request = "\n".join(
        [method.upper(), path, query, canonical_headers, UNSIGNED_HEADERS, payload_hash]
    )
    scope = f"{datestamp}/{credentials.region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _hmac(("AWS4" + credentials.secret_key).encode("utf-8"), datestamp)
    key = _hmac(key, credentials.region)
    key = _hmac(key, service)
    key = _hmac(key, "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{ALGORITHM} Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={UNSIGNED_HEADERS}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "x-amz-security-token": token,
    }
