# Glossary

Terms used in code, docs and tool output.

| term | meaning |
| --- | --- |
| **BLID** | The robot id. It is the MQTT client id and username on the local link, and part of every cloud topic. |
| **LSS** | Local Smart Service, the robot's own MQTT broker on TCP 8883. |
| **Shadow** | A JSON document with `state.reported` and `state.desired`. The classic shadow is unnamed; named shadows hold specific domains. |
| **Classic shadow** | The unnamed shadow. Locally it carries live state. In the cloud it carries identity and capability fields only. |
| **Named shadow** | A shadow addressed as `$aws/things/<blid>/shadow/name/<name>`. Relevant names: `ro-currentstate`, `rw-settings`, `rw-schedule`. |
| **Reported state** | What the robot says is true now. Tools read this, not the desired state. |
| **Desired state** | What a client asked for. Settings writes use the desired document in the cloud. |
| **Transport** | The path to the robot: `local` (LSS) or `cloud` (AWS IoT). |
| **Failover** | Retrying a failed read over the other transport. Writes never fail over. |
| **pmap** | A persistent smart map. `get_map` returns its id, name and regions. |
| **pmapv** | A map version. A region clean references the active version indirectly through the map id. |
| **Region** | A named area inside a smart map, with an id used by `start_clean`. |
| **Mission** | One cleaning run, with a phase, cycle, counters and a timeline. |
| **Mission history** | Finished missions from the cloud REST API. |
| **Consumable** | A replaceable part with a counter, for example a filter or a pad. |
| **Capability tier** | `verified`, `derived-low-risk` or `unverified-physical`. |
| **Writes flag** | `ROOMBA_ALLOW_WRITES=1`. Required for every command. |
| **Raw flag** | `ROOMBA_ALLOW_RAW=1`. Required for `raw_publish`. |
| **Experimental flag** | `ROOMBA_EXPERIMENTAL=1`. Required for unverified parameters. |
| **Outcome** | The result of a write: `sent`, `rejected` or `unknown`. |
| **Stale** | A cached read older than the snapshot TTL. The payload says so instead of pretending it is live. |
