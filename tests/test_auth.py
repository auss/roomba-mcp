import datetime
import json
from pathlib import Path

import pytest

from roomba_mcp.auth import (
    AwsSession,
    IotSession,
    LoginCache,
    load_login,
    pick_deployment,
    save_login,
)
from roomba_mcp.errors import LoginFailed

SAMPLE = {
    "version": 1,
    "created_at": "2026-01-01T00:00:00+00:00",
    "email": "user@example.com",
    "blid": "0123456789ABCDEF0123456789ABCDEF",
    "robot_name": "Test Robot",
    "robot_password": ":1:1234567890:secret",
    "iot": {
        "host": "example.iot.us-east-1.amazonaws.com",
        "token": "token",
        "signature": "signature",
        "authorizer_name": "authorizer",
        "client_id": "client",
        "topic_prefix": "v005-test",
    },
    "aws": {
        "access_key_id": "AKIA",
        "secret_key": "secret",
        "session_token": "session",
        "expiration": "2099-01-01T00:00:00Z",
        "region": "us-east-1",
        "rest_base": "https://auth1.example.com",
    },
}


def test_login_cache_round_trip() -> None:
    cache = LoginCache.from_dict(SAMPLE)
    assert cache.to_dict() == SAMPLE
    assert cache.iot.topic_prefix == "v005-test"


def test_login_cache_rejects_incomplete_data() -> None:
    with pytest.raises(LoginFailed):
        LoginCache.from_dict({"blid": "x"})


def test_expiry_parsing() -> None:
    cache = LoginCache.from_dict(SAMPLE)
    moment = cache.expires_at()
    assert moment is not None
    assert moment.tzinfo is not None
    assert not cache.is_expired()


def test_expired_session() -> None:
    data = json.loads(json.dumps(SAMPLE))
    data["aws"]["expiration"] = "2020-01-01T00:00:00Z"
    cache = LoginCache.from_dict(data)
    assert cache.is_expired()
    assert (cache.expires_in_s() or 0) < 0


def test_missing_expiration_is_not_expired() -> None:
    data = json.loads(json.dumps(SAMPLE))
    data["aws"]["expiration"] = ""
    cache = LoginCache.from_dict(data)
    assert cache.expires_at() is None
    assert not cache.is_expired()


def test_save_and_load_login(tmp_path: Path) -> None:
    path = tmp_path / "login.json"
    cache = LoginCache.from_dict(SAMPLE)
    save_login(path, cache)
    assert load_login(path) == cache
    assert load_login(tmp_path / "missing.json") is None


def test_pick_deployment_prefers_current() -> None:
    discovery = {
        "current_deployment": "v005",
        "deployments": {
            "v011": {"httpBase": "https://old", "mqtt": "old", "irbtTopics": "v011-x"},
            "v005": {
                "httpBase": "https://new",
                "httpBaseAuth": "https://auth-new",
                "mqtt": "new",
                "irbtTopics": "v005-x",
                "awsRegion": "eu-west-1",
            },
        },
    }
    deployment = pick_deployment(discovery)
    assert deployment.http_base == "https://new"
    assert deployment.http_base_auth == "https://auth-new"
    assert deployment.topic_prefix == "v005-x"
    assert deployment.region == "eu-west-1"


def test_pick_deployment_uses_highest_version() -> None:
    discovery = {
        "deployments": {
            "v005": {"httpBase": "https://a", "mqtt": "a"},
            "v011": {"httpBase": "https://b", "mqtt": "b"},
        }
    }
    assert pick_deployment(discovery).http_base == "https://b"


def test_pick_deployment_rejects_empty() -> None:
    with pytest.raises(LoginFailed):
        pick_deployment({})


def test_iot_and_aws_sessions_are_plain_data() -> None:
    iot = IotSession("h", "t", "s", "a", "c", "p")
    aws = AwsSession("k", "s", "t", "e", "r", "b")
    cache = LoginCache(
        email="e",
        blid="b",
        robot_name="n",
        robot_password="p",
        iot=iot,
        aws=aws,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    assert cache.to_dict()["iot"]["topic_prefix"] == "p"
