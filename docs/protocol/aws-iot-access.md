<!--
AWS IoT cloud access: MQTT headers, topic prefix, shadow reads

Vendored from the roomba-mcp research notes captured on 2026-10-29
against a Roomba Combo j9+ (stingray, sku c975840). Sanitized: no
account ids, tokens, passwords or private hostnames are included.
Treat this file as reference material, not as a specification the
robot vendor supports. Script names mentioned in the text belong to
the original research project and are not shipped here.
-->

# iRobot AWS IoT MQTT - cloud access (app impersonation)

How to connect to the **same MQTT broker the iRobot app uses**, read robot
shadows, and send commands - with your own account credentials. Everything
below is **observed** against a live account + Roomba Combo j9+ (stingray,
sku `c975840`) on 2025-10-29 unless marked otherwise.

Companion scripts: `iot-watch.py` (MQTT watch), `request-ota.py` (REST),
`fetch-robot-credentials.py` (login → BLID/password).

## 1. Credentials

`POST {httpBase}/v2/login` (see `irobot-cloud-api.md` §7 for the Gigya
preamble) returns everything needed:

| field | use |
|---|---|
| `credentials.{AccessKeyId,SecretKey,SessionToken}` | SigV4 REST calls (~1 h) |
| `iot_token` | MQTT header `x-irobot-auth` |
| `iot_signature` | MQTT header `x-amz-customauthorizer-signature` |
| `iot_authorizer_name` | MQTT header `x-amz-customauthorizer-name` |
| `iot_clientid` | MQTT client id |
| `robots.<blid>.password` | **local** MQTT password |

A working login response is cached at `/tmp/irobot_login.json`; regenerate
any time by re-running the login flow in `fetch-robot-credentials.py`.

## 2. MQTT connection (works, tested with paho-mqtt)

```
host:       a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com   (discovery: deployments[*].mqtt)
transport:  websockets, path /mqtt, TLS 443, MQTT 3.1.1, keepalive 120-300
client_id:  <iot_clientid>
headers:    x-amz-customauthorizer-name / -signature / x-irobot-auth
```

```python
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=login["iot_clientid"],
                protocol=mqtt.MQTTv311, transport="websockets")
c.ws_set_options(path="/mqtt", headers={
    "x-amz-customauthorizer-name": login["iot_authorizer_name"],
    "x-amz-customauthorizer-signature": login["iot_signature"],
    "x-irobot-auth": login["iot_token"]})
c.tls_set_context(ssl.create_default_context())
```

## 3. Topic permissions (the tricky part)

The deployment topic prefix from discovery (`irbtTopics`, currently
**`v005-irbthbu`**) is **required** on app/robot topics:

| topic | result |
|---|---|
| `v005-irbthbu/things/<blid>/cmd` | ✅ SUBACK - this is the cloud command topic |
| `v005-irbthbu/things/<blid>/shadow/#` | ✅ |
| `v005-irbthbu/things/<blid>/rejected/report` | ✅ |
| `$aws/things/<blid>/shadow/#` | ✅ (AWS shadow service namespace) |
| `/things/<blid>/…` (unprefixed) | ❌ connection dropped |
| `#` | ❌ connection dropped |

**Quirk (observed):** the authorizer accepts only **one SUBSCRIBE per
connection** - a second subscribe drops the connection (rc 7). Use one
connection per subscription, or a single wildcard that covers what you need.

## 4. Reading the device shadows (full robot state)

One connection: subscribe `$aws/things/<blid>/shadow/#`, then publish

```
$aws/things/<blid>/shadow/name/<name>/get   payload {}
```

and the response arrives on `…/shadow/name/<name>/get/accepted`.
Classic shadow: publish `$aws/things/<blid>/shadow/get`.

Named shadows that exist on a Combo j9+ (each a full JSON document):

| shadow | contents (highlights) |
|---|---|
| `ro-currentstate` | cleanMissionStatus, pose, tank… live-ish state |
| `rw-settings` | all settings ( suction, languages, cloudEnv, country…) |
| `rw-schedule` | cleanSchedule2 (with per-region cmdStr params!) |
| `rw-software` | softwareVer, subModSwVer, lastCommand, lastSwUpdate, deploymentState, requestedSoftwareVersion |
| `ro-configinfo` | passwordHash, battery info, sku |
| `ro-services` | optFeats, smartHome flags |
| `ro-stats` | bbchg/bbmssn/bbnav… black-box counters |
| `rw-constatus` | connected state, svcDeplId |

## 5. SigV4 REST (works)

Hand-rolled AWS SigV4 (see `request-ota.py`) - **must include
`x-amz-security-token` in the signed headers** (STS temp creds). Host:
`auth3.prod.iot.irobotapi.com`, service `execute-api`, region `us-east-1`.

| endpoint | status |
|---|---|
| `GET /v1/robots/{blid}/parts` | ✅ 200 |
| `POST /v1/{blid}/request-ota?app_id=…` | ✅ 200 `{"result":"success"}` (inert - no update available) |
| `GET /v1/{blid}/ota`, `/firmware`, `/v1/robots/{blid}/ota` | ❌ 403 role not authorized |
| `GET /v1/robots?robot_id=…` | ❌ 403 (different API) |

## 6. Firmware acquisition - status: **blocked** (as of 2025-10-29)

- Robot is on `stingray+24.29.5` = **latest for sku c975840** (24.29.6 is
  j5/j6/j7 only, per robofaults.com) → `request-ota` never yields a URL.
- `rw-software` shadow contains version history (`lastSwUpdate`) but no
  download URL.
- No public firmware mirrors; `firmware.irobot.com` & friends don't resolve;
  iRobot serves no manual gen-3 downloads.
- **Plan when the next OTA rolls out**: keep `iot-watch.py`-style shadow
  watch running, or capture the robot's ~100 MB download at the router to
  learn the CDN URL pattern, then pull the package and read the command
  schema (see `rc-joystick.md` §1.6 for why we want it).

## 7. Known-broken script notes

`iot-watch.py` / `iot-capture.py` predate the topic-prefix and
one-subscribe findings - they reconnect-loop. The recipes in §2-§4 are the
validated ones; rewrite the watcher from those when next needed.
