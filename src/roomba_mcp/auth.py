"""iRobot account login and the local login cache.

``roomba-mcp login`` is the only writer of ``login.json``. The server reads
the cache and never performs a login on its own; when the cloud session is
missing or expired it reports ``login_required`` or ``login_expired`` and the
operator re-runs the command.
"""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import write_private_json
from .errors import LoginFailed

APP_ID = "ANDROID-C7FB240E-DF34-42D7-AE4E-A8C17079A294"
DISCOVERY_URL = "https://disc-prod.iot.irobotapi.com/v1/discover/endpoints"
FALLBACK_GIGYA_BASE = "https://accounts.us1.gigya.com"
FALLBACK_HTTP_BASE = "https://unauth1.prod.iot.irobotapi.com"
CACHE_VERSION = 1
EXPIRY_MARGIN_S = 60


@dataclass(frozen=True)
class Deployment:
    http_base: str
    http_base_auth: str
    mqtt: str
    topic_prefix: str
    region: str


@dataclass(frozen=True)
class IotSession:
    host: str
    token: str
    signature: str
    authorizer_name: str
    client_id: str
    topic_prefix: str


@dataclass(frozen=True)
class AwsSession:
    access_key_id: str
    secret_key: str
    session_token: str
    expiration: str
    region: str
    rest_base: str


@dataclass(frozen=True)
class LoginCache:
    email: str
    blid: str
    robot_name: str
    robot_password: str
    iot: IotSession
    aws: AwsSession
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )
    version: int = CACHE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "email": self.email,
            "blid": self.blid,
            "robot_name": self.robot_name,
            "robot_password": self.robot_password,
            "iot": {
                "host": self.iot.host,
                "token": self.iot.token,
                "signature": self.iot.signature,
                "authorizer_name": self.iot.authorizer_name,
                "client_id": self.iot.client_id,
                "topic_prefix": self.iot.topic_prefix,
            },
            "aws": {
                "access_key_id": self.aws.access_key_id,
                "secret_key": self.aws.secret_key,
                "session_token": self.aws.session_token,
                "expiration": self.aws.expiration,
                "region": self.aws.region,
                "rest_base": self.aws.rest_base,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoginCache:
        try:
            iot = data["iot"]
            aws = data["aws"]
            return cls(
                email=str(data.get("email", "")),
                blid=str(data["blid"]),
                robot_name=str(data.get("robot_name", "")),
                robot_password=str(data["robot_password"]),
                iot=IotSession(
                    host=str(iot["host"]),
                    token=str(iot["token"]),
                    signature=str(iot["signature"]),
                    authorizer_name=str(iot["authorizer_name"]),
                    client_id=str(iot["client_id"]),
                    topic_prefix=str(iot.get("topic_prefix", "")),
                ),
                aws=AwsSession(
                    access_key_id=str(aws["access_key_id"]),
                    secret_key=str(aws["secret_key"]),
                    session_token=str(aws["session_token"]),
                    expiration=str(aws.get("expiration", "")),
                    region=str(aws.get("region", "us-east-1")),
                    rest_base=str(aws["rest_base"]),
                ),
                created_at=str(data.get("created_at", "")),
                version=int(data.get("version", CACHE_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LoginFailed(
                "login.json is incomplete; run 'roomba-mcp login' again",
                detail=str(exc),
            ) from exc

    def expires_at(self) -> datetime.datetime | None:
        if not self.aws.expiration:
            return None
        raw = self.aws.expiration.replace("Z", "+00:00")
        try:
            moment = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.UTC)
        return moment

    def expires_in_s(self, now: datetime.datetime | None = None) -> float | None:
        moment = self.expires_at()
        if moment is None:
            return None
        current = now or datetime.datetime.now(datetime.UTC)
        return (moment - current).total_seconds()

    def is_expired(self, now: datetime.datetime | None = None) -> bool:
        remaining = self.expires_in_s(now)
        return remaining is not None and remaining <= EXPIRY_MARGIN_S


def load_login(path: Path) -> LoginCache | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise LoginFailed(
            f"cannot read {path}; run 'roomba-mcp login' again",
            detail=str(exc),
        ) from exc
    if not isinstance(data, dict):
        raise LoginFailed(f"{path} is not a login cache; run 'roomba-mcp login' again")
    return LoginCache.from_dict(data)


def save_login(path: Path, cache: LoginCache) -> None:
    write_private_json(path, cache.to_dict())


def _version_key(key: str) -> int:
    try:
        return int(str(key).lstrip("v") or 0)
    except ValueError:
        return 0


def pick_deployment(discovery: dict[str, Any]) -> Deployment:
    deployments = discovery.get("deployments")
    if not isinstance(deployments, dict) or not deployments:
        raise LoginFailed("discovery response has no deployments")
    current = discovery.get("current_deployment")
    chosen: dict[str, Any] | None = None
    if isinstance(current, str) and isinstance(deployments.get(current), dict):
        chosen = deployments[current]
    else:
        for key in sorted(deployments, key=_version_key, reverse=True):
            candidate = deployments.get(key)
            if isinstance(candidate, dict):
                chosen = candidate
                break
    if chosen is None:
        raise LoginFailed("discovery response has no usable deployment")
    try:
        return Deployment(
            http_base=str(chosen.get("httpBase") or FALLBACK_HTTP_BASE),
            http_base_auth=str(chosen.get("httpBaseAuth") or chosen.get("httpBase") or ""),
            mqtt=str(chosen.get("mqtt") or ""),
            topic_prefix=str(chosen.get("irbtTopics") or ""),
            region=str(chosen.get("awsRegion") or "us-east-1"),
        )
    except (TypeError, ValueError) as exc:
        raise LoginFailed("discovery deployment is malformed", detail=str(exc)) from exc


def _request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[int, Any]:
    body: bytes | None = None
    headers = {"Accept": "application/json", "Connection": "close"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"_raw": raw}
    except urllib.error.URLError as exc:
        raise LoginFailed(f"network error while contacting iRobot: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LoginFailed("timed out while contacting iRobot") from exc


def discover(country: str = "US", timeout: float = 20.0) -> tuple[dict[str, Any], Deployment]:
    url = f"{DISCOVERY_URL}?country_code={urllib.parse.quote(country)}"
    status, payload = _request(url, timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        raise LoginFailed("iRobot service discovery failed", detail={"status": status})
    return payload, pick_deployment(payload)


def login(
    email: str,
    password: str,
    *,
    blid: str | None = None,
    country: str = "US",
    timeout: float = 20.0,
) -> LoginCache:
    """Exchange account credentials for a robot password and cloud session."""
    discovery, deployment = discover(country, timeout)
    gigya = discovery.get("gigya")
    if not isinstance(gigya, dict) or not gigya.get("api_key"):
        raise LoginFailed("discovery response has no Gigya API key")
    datacenter = gigya.get("datacenter_domain")
    gigya_base = f"https://accounts.{datacenter}" if datacenter else FALLBACK_GIGYA_BASE

    status, account = _request(
        gigya_base + "/accounts.login",
        method="POST",
        form={
            "apiKey": str(gigya["api_key"]),
            "targetenv": "mobile",
            "targetEnv": "mobile",
            "loginID": email,
            "password": password,
            "format": "json",
        },
        timeout=timeout,
    )
    if status != 200 or account.get("errorCode", 0) != 0:
        raise LoginFailed(
            "Gigya login failed; check the account email and password",
            detail={"status": status, "errorCode": account.get("errorCode")},
        )
    uid = account.get("UID")
    signature = account.get("UIDSignature")
    timestamp = account.get("signatureTimestamp")
    if not (uid and signature and timestamp):
        raise LoginFailed("Gigya login response is missing UID, signature or timestamp")

    status, session = _request(
        deployment.http_base + "/v2/login",
        method="POST",
        data={
            "app_id": APP_ID,
            "assume_robot_ownership": 0,
            "gigya": {"signature": signature, "timestamp": timestamp, "uid": uid},
        },
        timeout=timeout,
    )
    if status != 200 or not isinstance(session, dict):
        raise LoginFailed("iRobot login failed", detail={"status": status})

    robots = session.get("robots")
    if not isinstance(robots, dict) or not robots:
        raise LoginFailed("the iRobot account has no robots")
    if blid:
        if blid not in robots:
            raise LoginFailed(
                "the requested robot is not on this account",
                detail={"blid": blid, "robots": sorted(robots)},
            )
        chosen = blid
    elif len(robots) == 1:
        chosen = next(iter(robots))
    else:
        raise LoginFailed(
            "the account has several robots; pass --blid",
            detail={"robots": sorted(robots)},
        )

    robot = robots[chosen]
    if not isinstance(robot, dict) or not robot.get("password"):
        raise LoginFailed("the login response has no local password for the robot")

    credentials = session.get("credentials")
    if not isinstance(credentials, dict):
        raise LoginFailed("the login response has no AWS credentials")
    iot_token = session.get("iot_token")
    iot_signature = session.get("iot_signature")
    iot_authorizer = session.get("iot_authorizer_name")
    iot_client_id = session.get("iot_clientid")
    if not (iot_token and iot_signature and iot_authorizer and iot_client_id):
        raise LoginFailed("the login response is missing IoT authorizer fields")
    if not deployment.mqtt:
        raise LoginFailed("the deployment has no MQTT host")

    return LoginCache(
        email=email,
        blid=chosen,
        robot_name=str(robot.get("name", "")),
        robot_password=str(robot["password"]),
        iot=IotSession(
            host=deployment.mqtt,
            token=str(iot_token),
            signature=str(iot_signature),
            authorizer_name=str(iot_authorizer),
            client_id=str(iot_client_id),
            topic_prefix=deployment.topic_prefix,
        ),
        aws=AwsSession(
            access_key_id=str(credentials.get("AccessKeyId", "")),
            secret_key=str(credentials.get("SecretKey", "")),
            session_token=str(credentials.get("SessionToken", "")),
            expiration=str(credentials.get("Expiration", "")),
            region=deployment.region,
            rest_base=deployment.http_base_auth or deployment.http_base,
        ),
    )
