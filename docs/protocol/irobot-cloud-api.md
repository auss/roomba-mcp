<!--
iRobot cloud REST and MQTT reference

Vendored from the roomba-mcp research notes captured on 2026-10-29
against a Roomba Combo j9+ (stingray, sku c975840). Sanitized: no
account ids, tokens, passwords or private hostnames are included.
Treat this file as reference material, not as a specification the
robot vendor supports. Script names mentioned in the text belong to
the original research project and are not shipped here.
-->

# iRobot Home cloud API - reverse-engineering notes

How to access live information about an iRobot Roomba Combo j9+ without the
official app. Findings are derived from static reverse-engineering of the
`iRobot Home (Classic)` app (`com.irobot.home`, version 7.18.0,
`versionCode 1607180002`, APKMirror bundle).

- Target: `com.irobot.home_7.18.0-1607180002_..._apkmirror.com.apkm`
- Code lives in `base.apk` (6 `classes*.dex`) and the native libs
  `libcore_base.so` + `libcore_jni.so` (arm64 split). The app is Kotlin
  Multiplatform: orchestration/state machines are compiled to C++, the AWS and
  Gigya transport layer is Java/Kotlin in the DEX.

Confidence tags used throughout: **observed** (literal string/symbol in the
artifact), **derived** (read from decompiled control flow), **inferred**
(consistent but not directly proven), **unknown** (not determined statically).

---

## 1. Architecture at a glance

Live robot state is **not** a simple REST poll. It is pushed over **AWS IoT
Core (MQTT over WebSocket)**. REST (AWS API Gateway) is used for history, maps,
schedules, and configuration. Authentication is **Gigya** (SAP Customer Data
Cloud), whose session is exchanged for AWS temporary credentials.

```
                 ┌──────────────────────────────────────────────┐
                 │            Service discovery (bootstrap)     │
                 │  disc-prod.iot.irobotapi.com/v1/discover/... │
                 │  returns: Gigya API key + domain, API URLs,  │
                 │           IoT host, topic prefixes           │
                 └──────────────────────┬───────────────────────┘
                                        │
      ┌─────────────────────────────────┼─────────────────────────────┐
      │ Gigya login (accounts.login /   │                             │
      │  hosted screen gigya-login-     │                             │
      │  screen) -> UID, UIDSignature,  │                             │
      │  signatureTimestamp             │                             │
      └─────────────────┬───────────────┘                             │
                        │                                             │
      ┌─────────────────▼──────────────────────────────┐              │
      │ /v2/login  (exchange Gigya session)            │              │
      │  -> AWS temp credentials (AccessKeyId/Secret/  │              │
      │     SessionToken/CognitoId) + IoT connection   │              │
      │     data (host, tokens, authorizer, prefixes)  │              │
      └───────┬──────────────────────┬────────────────┘              │
              │                      │                               │
   ┌──────────▼──────────┐  ┌────────▼───────────────────────────────┐
   │ REST (API Gateway)  │  │ AWS IoT MQTT over WebSocket (live)     │
   │ SigV4 (execute-api) │  │ custom authorizer headers              │
   │ history/maps/config │  │ device shadow + mission topics         │
   └─────────────────────┘  └────────────────────────────────────────┘
```

Key classes (decompiled sources under `assets/extracted/jadx-out/sources/`):

- `com.irobot.awsservices.iot.mqttclient.AwsMqttWebsocketClient` - MQTT/WS client
- `com.irobot.awsservices.iot.AwsIotBrokerConnectionProxy` - broker connection proxy
- `com.irobot.awsservices.apigateway.ApiGatewayRestClient` - REST client
- `com.irobot.awsservices.apigateway.utils.RequestHeaderSigner` - AWS SigV4 signer
- `com.irobot.awsservices.auth.IrobotSessionCredentialsProvider` - session creds interface
- `com.irobot.core.platform.authentication.AwsCredentialsService` - creds implementation
- `com.irobot.core.platform.account.GigyaHandler` - Gigya auth
- `com.irobot.core.ServiceDiscoveryData` - parsed discovery response
- `com.irobot.core.AwsIoTSessionAttributes` - IoT session (host/authorizer/token/…)

Native symbols of interest (in `libcore_base.so`):
`core::ServiceDiscoveryImpl::k*FieldName`, `core::WebClientConstants::k*Key`.

---

## 2. Service discovery (bootstrap)

**Observed** endpoint templates (from `libcore_base.so`):

```
https://disc-{env}.iot.irobotapi.com/v1/discover/endpoints?country_code={CC}
https://disc-{env}.iot.irobotapi.com/v1/robot/discover?robot_id={blid}&country_code={CC}&sku={sku}
https://disc-{env}.iot.irobotapi.com/v1/robot/discover/{blid}
```

`{env}` is `prod` or `int-test` (**observed**). China hosts use
`.iot.irobot.cn` instead of `.iot.irobotapi.com` (**observed**).

The app-level `/discover/endpoints` call is unauthenticated and returns the
bootstrap config. Fields (decoded from `ServiceDiscoveryImpl` member variables
and `ServiceDiscoveryData.java`):

| JSON field | Member / symbol | Meaning |
|---|---|---|
| `awsRegion` / `region_id` | `mAwsRegion` | AWS region for IoT + SigV4 |
| `datacenter_domain` | `mAccountApiDomain` | Gigya data center (`us1.gigya.com`) |
| `account_api_key` (inferred) | `mAccountApiKey` | **Gigya API key** (not hardcoded) |
| `userServicesBase` | `mUserServicesBase` | REST base URL |
| `apiGatewayAuthenticatedUrl` | `mApiGatewayAuthenticatedUrl` | authed API Gateway URL |
| `apiGatewayUnauthenticatedUrl` | `mApiGatewayUnauthenticatedUrl` | unauthed API Gateway URL |
| `iot_host` (inferred) | `mIotHostName` | AWS IoT MQTT broker host |
| `iot_authorizer_name` | `mIotAuthorizerName` | IoT custom authorizer name |
| `iot_token` | `mIotToken` | IoT auth token (`x-irobot-auth`) |
| `iot_signature` | `mIotSignature` | IoT auth signature |
| `client_id` | `mIotClientId` | MQTT client id |
| `iotTopics` / `irbtTopics` | `mIotTopicPrefix` / `mIrbtTopicPrefix` | MQTT topic prefixes |
| `iotServiceUrl` | `mIotServiceUrl` | IoT data-plane URL |
| `prod_sec_base_auth` / `httpProdSecBaseAuth` | `mProdSecBaseAuthUrl` | legacy auth URL |
| `srvc_deploy_id` | `mSrvcDeployId` | service deployment id |
| `discoveryTTL` | `mDiscoveryTTL` | cache TTL (seconds) |

**Observed** hardcoded default IoT broker (fallback, may differ from your
account's assigned host): `a28me8ncai1wg2.iot.us-east-1.amazonaws.com`.

> The Gigya API key and per-account IoT host are returned at runtime, so call
> discovery yourself instead of trying to extract them from the binary.

Live confirmation of the app-discovery response (verified 2026-10): the
top-level keys are `current_deployment`, `deployments`, and `gigya`.
`gigya` = `{ "api_key", "datacenter_domain" }` (e.g. `us1.gigya.com`).
Each `deployments.{v}` (e.g. `v005`, `v007`, `v011`) carries:

```
awsRegion            us-east-1
discoveryTTL         86400
httpBase             https://unauth3.prod.iot.irobotapi.com      (v011)
httpBaseAuth         https://auth3.prod.iot.irobotapi.com        (v011)
httpProdSecBaseAuth  https://certificatefactory.prod.security.irobotapi.com
iotTopics            $aws
irbtTopics           v011-irbthbu
mqtt / mqttApp       a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com
userServicesBase     prod.user-services.irobotapi.com
svcDeplId            v011
```

(`httpBase`/`httpBaseAuth` version with the deployment: v007 -> `unauth2`/`auth2`.)

The robot-discovery response shape is visible in `res/raw/sapphire_discovery_config.json`
(demo record): `robotname`, `robotid` (the BLID / robot id), `sw`, `mac`, `sku`,
`ver`, `hostname`, `nc`, `proto` (`mqtt`), `ip`, and a `cap` capabilities map.
`res/raw/base_roomba_config.json` lists the known SKUs, which include `j9`
(your Combo j9+) alongside `j7`, `j8`, `c3`, `c7`, `i7`/`i8`, `s9`, `m6`, etc.

---

## 3. Authentication (Gigya)

**Observed** (from `GigyaHandler.java` / `GigyaConst.java`):

- Init: `Gigya.init(apiKey, apiDomain)` with key + domain from discovery.
- Login UI is the hosted screen set `gigya-login-screen` (via `accounts.ScreenSet`).
  Related screen-set ids: `gigya-register-screen`, `gigya-forgot-password-screen`,
  `gigya-change-password-screen`, `gigya-update-profile-screen`,
  `gigya-verify-new-email`.
- Underlying Gigya REST calls: `accounts.login`, `accounts.getAccountInfo`,
  `accounts.getSchema`, `accounts.setAccountInfo`, `accounts.logout`,
  `accounts.otp.sendCode`, `accounts.otp.update`, `socialize.getUserInfo`,
  `accounts.session.verify`, `accounts.tfa.getProviders`.
- Gigya data center: `us1.gigya.com` (**observed**).

The Gigya session yields `UID`, `UIDSignature`, `signatureTimestamp`
(**observed**, `GigyaConst`), which are used downstream.

---

## 4. Login and token exchange

**Observed** endpoints (from `libcore_base.so`):

```
/v2/login
{base}/v1/token/authorization
```

### `/v2/login` - Gigya session -> robots + AWS credentials

Confirmed request (**observed** via the `dorita980` reference implementation,
and consistent with the app's `performAwsLoginRequest`):

```
POST {httpBase}/v2/login        (httpBase = unauth gateway, see section 2)
{
  "app_id": "ANDROID-C7FB240E-DF34-42D7-AE4E-A8C17079A294",
  "assume_robot_ownership": 0,
  "gigya": {
    "signature": "<UIDSignature>",
    "timestamp": "<signatureTimestamp>",
    "uid":        "<UID>"
  }
}
```

The response includes the owned robots and their **local MQTT passwords**:

```json
{ "robots": { "<BLID>": { "name": "...", "sku": "...",
                          "softwareVer": "...", "password": ":1:<epoch>:<secret>" } } }
```

The AWS temporary credentials used for SigV4 REST calls are returned separately
by the AWS-login/refresh path (`LoginResponse.java` + `LoginResponseCredentials.java`):

```json
{
  "credentials": {
    "AccessKeyId":  "...",
    "SecretKey":    "...",
    "SessionToken": "...",
    "CognitoId":    "...",
    "Expiration":   "yyyy-MM-dd'T'HH:mm:ssZ"
  }
}
```

These are STS-style temporary credentials. `AwsCredentialsService` wraps them
as `AWSSessionCredentials` and refreshes them via the account service
(`RefreshAWSCredentials` command) before expiry (600 s offset, **derived**).

---

## 5. Live state - AWS IoT MQTT over WebSocket

**Observed** connect flow (`AwsMqttWebsocketClient.java`):

```
wss://{iotHostName}:443/mqtt        (MQTT 3.1.1, clean session, keepalive 300s)

Custom WebSocket headers:
  x-amz-customauthorizer-name:      {iot_authorizer_name}
  x-amz-customauthorizer-signature: {iot_signature}
  x-irobot-auth:                    {iot_token}
```

This is the AWS IoT *custom authorizer* flow over WebSocket. The
`iot_token`/`iot_signature` come from the discovery/login response.

**Observed** topic templates (from `libcore_base.so`; `%s` = thing name; the
`iotTopicPrefix` / `irbtTopicPrefix` from discovery is prepended):

```
$aws/things/%s/shadow/update                      classic device shadow
$aws/things/%s/shadow/get
$aws/things/%s/shadow/update/accepted
$aws/things/%s/shadow/get/accepted
/things/%s/shadow/name/{name}/update              named shadows
/things/%s/shadow/name/{name}/get
/things/%s/shadow/name/+/update/accepted
/things/%s/shadow/name/+/get/accepted
/things/%s/cmd                                    commands -> robot
/things/%s/mission/timeline/report                live mission timeline
/things/%s/mission/timeline/request
/things/%s/mission/rrtp/report/update             live position/telemetry
/things/%s/mission/rrtp/request
/things/%s/rejected/report                        rejected commands
```

The **device shadow** is the canonical current-state source. Shadow/status
fields observed in `libcore_base.so` strings: `battery_level`, `battery_charge`,
`battery_voltage`, `robot_state`, `robot_status`, `in_mission`,
`mission_status`, `mission_number`, `number_of_cleaning_jobs`,
`dirt_detect_count`, `average_time_per_job`, `paused_robots`,
`paused_timestamp`, `software_version`, `serial_number`, `robot_name`.

### Command/event -> topic mapping (**observed**, `res/raw/base_roomba_config.json`)

The app drives the robot through three networks, named in every command:
`lss` (local secure socket / local MQTT - a full control path, see
`roomba-local-protocol.md`), `awsIot` (cloud MQTT), `awsApiGateway` (cloud REST).

Read (live) topics:

| Command | topic | network |
|---|---|---|
| `GetThingShadow` | `shadow` (classic shadow) | awsIot |
| `GetMissionTimelineCommand` | `timeline` | awsIot |
| `Control` | `cmd` | lss / awsIot |
| `GetRobotPositionCommand` | (shadow) | lss / awsIot |

Write (settings) go to named shadows via the `delta` topic:

| Named shadow | Purpose |
|---|---|
| `rw-settings` | name, bin pause, child lock, edge clean, carpet boost, suction level, pad wetness, nav strategy, multi-pass, language, gentle mode, etc. |
| `rw-schedule` | schedule |
| `rw-software` | OTA |
| `rw-constatus` | echo / connection status |

So a live feed means subscribing to the classic shadow (`shadow`) and the
mission timeline (`timeline`) topics; changing settings means publishing to
`delta` with the target `namedShadow`.

The Combo j9+ maps to product string `combo_roomba_combo` (**observed**).
Other robot codenames seen: `sapphire`, `stingray`, `topaz`, `atlantis`,
`lewis`, `ruby`, `san_marino`, `soho`, `bin_full`.

---

## 6. REST API paths

**Observed** (from `libcore_base.so` / `libcore_jni.so`). Base is
`userServicesBase` or `apiGatewayAuthenticatedUrl` from discovery; requests are
AWS-SigV4 signed with service name `execute-api`
(`RequestHeaderSigner.java`, `x-amz-content-sha256: required`).

```
/v2/login
/v1/token/authorization

/v1/robots/owned                                     list your robots (blids)
/v1/robots?robot_id={blid}
/v1/robots/{blid}/echo                               live ping/echo (POST)
/v1/robots/{blid}/gps
/v1/robots/{blid}/parts                              parts/consumables + reset
/v1/robots/{blid}/sec_message
/v1/robots/{blid}/setupinfo?sku={sku}
/v1/robots/{blid}/time-estimates
/v1/robots/{blid}/timeline?event_type=HKC&...         notification timeline
/v1/robots/sensordata?robotIds=...&aqiScale=...      air-quality/sensor data
/v1/{blid}/missionhistory                            past missions
/v1/{blid}/missionhistory?filterType=omit_quickly_canceled_not_scheduled&supportedDoneCodes=dndEnd,returnHomeEnd
/v1/evachistory                                      "Eva" obstacle/photo history
/v1/{blid}/request-ota?app_id=...                    request OTA
/v1/{blid}/reset?app_id=...
/v1/{blid}/imageupload/approval?mssnN=...&approvals=...
/v1/{blid}/team/config                               teaming / coordinated robots
/v1/{blid}/team/status
/v1/{blid}/team/report
/v1/{blid}/team/cancel
/v1/insights/surveys/{id}?event_id=...

/v1/households                                       households
/v1/households/{id}
/v1/households/{id}/robots
/v1/households/{id}/settings
/v1/households/{id}/settings/schedule
/v1/households/{id}/environmental/data
/v1/households/transfer-robot

/v1/{blid}/pmaps                                     smart maps
/v1/{blid}/pmaps/{id}/settings
/v1/{blid}/pmaps?visible=true&activeDetails=2
/v1/map/{id}/spatial/rendered?...                    rendered map image
/v1/map/{id}/umf?app_id=...
/v1/v4maps/{id}/umf
/v1/pmaps/clean-score

/v1/user/associations/robots
/v1/user/associations/robots/{blid}?change_ownership=0
/v1/user/provisional_associations/robots/{blid}
/v1/user/settings
/v1/user/devices
/v1/user/automations
/v1/user/favorites
/v1/user/households
/v1/user/logout?app_id=
/v1/user/forget-me
/v1/user/request-data
/v1/notifications/register?app_id=...
/v1/notifications/settings?app_id=...
/v1/ifttt/connections
/v1/app-link/amazon
```

Legacy paths (older robots / provisioning): `/services/v1/rest/Scripto/execute/`
and `/ProductInstance?blid=%s` (**observed**).

---

## 7. Practical recipe

1. `GET https://disc-prod.iot.irobotapi.com/v1/discover/endpoints?country_code={CC}`
   -> capture `datacenter_domain` and the account API key (Gigya key).
2. Authenticate against Gigya (`{datacenter_domain}`, e.g. `us1.gigya.com`)
   using that API key - either drive the hosted `gigya-login-screen`, or call
   `accounts.login` directly and keep `UID`/`UIDSignature`/`signatureTimestamp`.
3. Call `/v2/login` (and/or `{base}/v1/token/authorization`) with the Gigya
   session -> AWS temp credentials + per-account IoT connection data
   (`iot_host`, `iot_token`, `iot_signature`, `iot_authorizer_name`,
   `client_id`, topic prefixes).
4. Open `wss://{iot_host}:443/mqtt` with the three custom-authorizer headers,
   then subscribe to the shadow + mission topics. This is the live feed.
5. For history/maps/config, AWS-SigV4-sign REST calls to `userServicesBase`
   using the temporary credentials (service name `execute-api`).

---

## 8. TLS / interception notes

**Observed** `res/xml/network_security_config.xml` (decoded with apktool):

```xml
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">global.irobot.com</domain>
    </domain-config>
</network-security-config>
```

- No `<pin-set>` anywhere; no 44-char `sha256/` pin constants in the DEX or
  native libs. The `CertificatePinner` strings present are OkHttp internals.
  Conclusion: **no certificate pinning**.
- No `<trust-anchors><certificates src="user"/>`. On Android 7+ this means a
  user-installed CA is **not** trusted, so a stock MITM proxy CA is rejected.
- Cleartext is permitted only for `global.irobot.com`; everything else is HTTPS.

Interception therefore needs either a system-trusted CA (rooted device /
Magisk / emulator), an APK patch that adds a user trust-anchor, or runtime
hooking (Frida) across both the JVM (OkHttp for Gigya, `HttpsURLConnection` for
API Gateway) and native (`libcore_jni.so`) HTTP stacks. See
`traffic-capture-guide.md`.

## 9. Unknowns / remaining gaps

- Exact `/v2/login` request body - **confirmed** (see section 4).
- Whether `/v1/token/authorization` is a separate refresh step or an alias.
  **unknown**
- Token lifetimes and refresh cadence for both the Gigya session and the IoT
  authorizer token. **derived** (600 s offset for AWS creds only).
- Any device attestation / certificate pinning / anti-abuse checks. **unknown**
- Whether the hardcoded `a28me8ncai1wg2.iot.us-east-1.amazonaws.com` is used by
  your account or overridden by discovery. **resolved**: discovery returns the
  live `mqtt` host (`a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com`), which
  overrides the hardcoded default.
- Exact default/factory local password for an un-provisioned j9+. **unknown**

---

## 10. Direct / local communication (LSS)

The j9+ **also** exposes a local WiFi control interface - the same MQTT-over-TLS
protocol as older Roombas (`dorita980`), not cloud-only. Inside the app it is
"LSS" (Local Secure Socket). Full detail: see `roomba-local-protocol.md`.

- Discovery: mDNS `_mqtt._tcp`, hostname `iRobot-<BLID>`.
- Transport: TLS to port **8883** (plain 1883 during setup), trust store
  `irobotkeystore.bks` (password `st0r3pA$$`), server cert CN = BLID.
- Auth: MQTT `username=BLID`, `password=<robot password>`
  (format `:1:<epoch>:Password…`).
- Topics: `cmd`, `connect`, `pwd`, `shadow`, `delta`, `wifictl`, `timeline`,
  `testcmd`.
- The password is returned by the cloud (`/v1/user/associations/robots` ->
  `password`), stored by the app, and used for local auth.

Prior art that implements this same protocol: `dorita980`, `rest980`, `pyroomba`
/ `roombapy`, and the Home Assistant Roomba integration.
