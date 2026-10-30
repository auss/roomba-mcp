# Security policy

## Supported versions

Security fixes target the latest commit on `main`. Use the latest revision and
keep dependencies updated. This project is alpha software.

## Report a vulnerability

Please report suspected vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/auss/roomba-mcp/security/advisories/new).
Do not open a public issue for an undisclosed vulnerability.

Include the affected revision, impact, and minimal reproduction with sanitized
data. Never attach account passwords, robot credentials, bearer tokens, login
caches, AWS sessions, or unredacted household maps and logs.

The maintainer will coordinate investigation, fixes, and disclosure in the
private advisory. There is no guaranteed response time or bug bounty.

## Safe deployment

Use stdio or a trusted private network. For remote HTTP access, use TLS through
a trusted reverse proxy or encrypted tunnel; bearer tokens do not encrypt
traffic. Do not expose the server directly to the public internet.

Keep write, raw-publish, and experimental flags disabled unless needed. Enabled
commands can move a physical robot. Treat the login cache and HTTP token as
secrets, restrict access to the config directory, and rotate exposed credentials.
The local robot protocol requires legacy TLS compatibility; isolate the robot
and server on a trusted network. See the README for the safety gates and TLS
limitations.
