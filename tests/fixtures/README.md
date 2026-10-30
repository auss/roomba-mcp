# Test fixtures

These files are sanitized captures from a Roomba Combo j9+ (`stingray`,
sku `c975840`) running firmware `stingray+24.29.5`. They are used to keep
payload mapping and field names honest without shipping account data.

Sanitization applied to every file:

- robot id replaced with `0123456789ABCDEF0123456789ABCDEF`
- smart map and map version ids replaced with `map-*` and `2026*` values
- mission ids replaced with `01TESTMISSION*`
- map and region names translated to English
- the backup hash zeroed

Timestamps, counters, capability flags and shadow keys are unchanged so the
mapping code is exercised against realistic documents.

## Files

| file | source |
| --- | --- |
| `shadows.json` | classic, `ro-currentstate`, `rw-settings` and `rw-schedule` shadow documents |
| `parts.json` | `GET /v1/robots/{blid}/parts` |
| `missionhistory.json` | `GET /v1/{blid}/missionhistory` |
| `pmaps.json` | `GET /v1/{blid}/pmaps?visible=true&activeDetails=2` |

The captures are read-only test data. Do not add credentials, tokens, email
addresses or account identifiers to this directory.
