<!--
Roomba local LSS protocol reference

Vendored from the roomba-mcp research notes captured on 2026-10-29
against a Roomba Combo j9+ (stingray, sku c975840). Sanitized: no
account ids, tokens, passwords or private hostnames are included.
Treat this file as reference material, not as a specification the
robot vendor supports. Script names mentioned in the text belong to
the original research project and are not shipped here.
-->

# Roomba j9+ - direct (local) communication protocol

Reverse-engineered from `com.irobot.home` 7.18.0. The key finding: **the
Roomba Combo j9+ exposes a local WiFi control interface** - the same family of
protocol as the older 900-series Roombas (`dorita980`). It is called "LSS"
(Local Secure Socket) inside the app, and it is a first-class control path, not
just onboarding.

Related docs: `rc-joystick.md` (manual drive / RC joystick deep-dive),
`irobot-cloud-api.md`, `traffic-capture-guide.md`.

Confidence tags: **observed** (literal string/symbol), **derived** (decompiled
control flow), **inferred** (consistent but unproven).

## 1. Discovery

The robot advertises itself with mDNS (**observed** `_mqtt._tcp` service type,
`MDNSServiceDiscovery.java`):

```
avahi-browse -rt _mqtt._tcp          # Linux
```

The robot hostname is `iRobot-<BLID>` (**observed**, `sapphire_discovery_config.json`),
so it also resolves as `irobot-<blid>.local`. `BLID` ("robot id") is the robot's
unique id - printed on a sticker under the bin, and also returned by the cloud.

During first-time setup the robot runs a SoftAP at `192.168.10.1` (**observed**).

## 2. Transport

| Property | Value | Source |
|---|---|---|
| Port (TLS) | **8883** | `LocalSecureSocketClient.SOCKET_SSL_PORT` |
| Port (plain, setup) | 1883 | `LocalSecureSocketClient.SOCKET_NOSSL_PORT` |
| Protocol | MQTT 3.1.1 (`protocolVersion: 4`) | `LocalMQTTMessageUtils` + `dorita980` |
| TLS cipher | **`AES128-SHA256`** | `dorita980` `ciphers` option |
| Legacy TLS | `SSL_OP_LEGACY_SERVER_CONNECT` | `dorita980` `secureOptions` |
| Client cert | none (trust-store only) | `SSLContext.init(null, trustManagerArr, null)` |

MQTT connect params (**derived**, cross-checked with `dorita980`):
`clientId = BLID`, `username = BLID`, `password = <robot password>`,
`rejectUnauthorized = false` (the robot cert is iRobot-signed, not publicly
trusted).

> If a TLS handshake fails with a cipher error, the robot's server is using the
> legacy `AES128-SHA256` CBC cipher; set that cipher explicitly (and, on
> Node.js, `SSL_OP_LEGACY_SERVER_CONNECT`). The maintained Python lib
> `roombapy` already handles this.

## 3. TLS

The app loads a bundled BKS trust store `res/raw/irobotkeystore.bks` with
password `st0r3pA$$` (**observed**, `NetworkSessionUtils.java` +
`strings.xml lss_cert_password`). It contains the iRobot CA certificate(s) that
sign the robot's server certificate.

On top of normal chain validation, the app checks that the server certificate's
**Common Name (CN) equals the BLID** (**observed**, `AssetCertificateVerifier.verify`).

For a hobby client you can accept any cert (the cert only proves you're talking
to a real iRobot robot; the CN check is just identity confirmation).

## 4. Authentication

The connection authenticates with MQTT username/password (**observed**):

```
username = BLID
password = <robot password>
```

Evidence: `authentication error, bad blid/password/username supplied` and
`Local conn ack - AUTH err (bad username or password)`.

There is also a higher-level `Authenticate` command (topic `connect`), and
`GetPassword`/`SetPassword` commands (topic `pwd`) for managing the password.

### The password format

The app contains these literal test credentials (**observed**):

```
:1:1537473457:Password
:1:1537473457:Password12345678
```

The real robot password uses the same `:1:<epoch>:Password…` shape (the leading
`1` is a version/scheme tag and `<epoch>` is a timestamp).

### Where the app gets the password

The cloud returns it. `AssetPoolAssociationInfo` maps the association response
(**observed**, `@SerializedName` fields):

```json
{ "robot_id": "<BLID>", "password": "<password>", "deleted": false }
```

This comes from `GET /v1/user/associations/robots` (see `irobot-cloud-api.md`).
The app stores it in SharedPreferences (`aws_preferences_app_asset_associations`)
keyed by BLID (**observed**, `AuthenticationUtils`).

> For a fully offline client you still need the password once. Either read the
> BLID off the sticker and fetch the password via the cloud (one-time), or use
> the factory default if the robot has never been re-provisioned.

## 5. Topics and commands

From `res/raw/base_roomba_config.json` (**observed**). Local network is `lss`:

| topic | commands | direction |
|---|---|---|
| `connect` | `Authenticate` | write |
| `pwd` | `GetPassword` / `SetPassword` | read / write |
| `cmd` | `Control` (start/stop/pause/dock/spot), `AssetControlCommand`, `ResetRobotCommand` | write |
| `shadow` | `GetThingShadow` | read |
| `delta` | settings (`rw-settings`, `rw-schedule`, `rw-software`, `rw-constatus`) | write |
| `wifictl` | wifi config / NTP / time / SSID / SoftAP control | read / write |
| `timeline` | `GetMissionTimelineCommand` | read |
| `testcmd` | `TestCommand` | write |

The same command set is used over `awsIot` (cloud MQTT) and `lss` (local), so
the local path can do the live reads (`shadow`, `timeline`) and every control
(`cmd`, `delta`) you'd otherwise do through the cloud.

The RC/manual-drive `drive` command (published to `cmd`) is documented in
`rc-joystick.md`.

## 6. Existing open-source libraries

This is the **same protocol** implemented by:

- `dorita980` (koalazak, Node.js) - local MQTT, `getPassword()` + control
- `rest980` (koalazak) - REST wrapper
- `pyroomba` / `roomba` Python packages
- Home Assistant "Roomba" integration (uses `roombapy`)

They all connect to `wss`/`ssl` MQTT on `:8883` with `username=BLID` and
`password=<robot password>`, and publish to the same topics. They should work
against the j9+ as-is, **provided you supply the correct BLID and password**.

## 7. Minimal client (Python + paho-mqtt)

A ready-to-run script is in `roomba-control.py` (connect, subscribe `#`, dump
live state, and send commands). The essentials:

```python
import ssl, paho.mqtt.client as mqtt

BLID = "…"                    # your BLID
PASSWORD = ":1:…:…"           # your robot password (the full string)
HOST = "irobot-%s.local" % BLID   # or the IP from avahi-browse

c = mqtt.Client(client_id=BLID, protocol=mqtt.MQTTv311)
c.username_pw_set(BLID, PASSWORD)
c.tls_set(cert_reqs=ssl.CERT_NONE, ciphers="AES128-SHA256")  # accept robot cert
c.tls_insecure_set(True)
c.on_message = lambda cl, u, m: print(m.topic, m.payload)
c.connect(HOST, 8883, 60)
c.subscribe("#")              # hear everything
c.loop_forever()
```

### Command JSON (confirmed, `dorita980` v2/local)

```
cmd topic:     {"command":"start","time":<epoch>,"initiator":"localApp"}
               start | pause | resume | stop | dock | find | clean | …
delta topic:   {"state": {"<setting>": <value>}}     (settings write)
```

### Live state

Incoming messages carry `{"state": {"reported": { … }}}`. Typical reported
fields (from `dorita980`): `cleanMissionStatus` (current mission: phase, cycle,
battery), `batPct`, `bin` (full?), `pose`, `sku`, `cap` (capabilities),
`cleanSchedule`, `name`, `wifistat`/`wlcfg`/`netinfo`, `softwareVer`,
`bbrstinfo`, `langs`, `vacHigh`, `utctime`, `signal`, `cloudEnv`.

## 8. Open questions

- Exact default/factory password for a j9+ out of the box. **unknown**
- Whether the `Authenticate` (topic `connect`) step is required in addition to
  MQTT username/password on current firmware. **derived** (both exist)
- Full JSON schema of `shadow` and `cmd` payloads on current firmware - best
  confirmed by connecting to a real robot (existing `dorita980` docs cover most
  of it).
