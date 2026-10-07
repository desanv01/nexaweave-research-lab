# Browser origin and request-log boundary (U02d)

The Flask factory validates an explicit browser Origin allowlist at startup.
`NEXAWEAVE_ALLOWED_ORIGINS` is a comma-separated environment value; a config
class can override it. Defaults are `http://localhost:3000` and
`http://127.0.0.1:3000` for the inherited Vite development flow. An explicitly
empty value disables cross-origin browser access. No-Origin clients and Vite
proxy calls continue through the API normally.

Entries must be an exact HTTP or HTTPS scheme, ASCII lowercase DNS hostname
or bracketed IPv6 address, and optional numeric port. The parser rejects
whitespace, wildcard or regex syntax, userinfo, paths, queries, fragments,
malformed ports, and noncanonical host casing. Spaces around CSV commas are
invalid. Future trusted deployment origins must be added explicitly; Host,
X-Forwarded-Host, and model provider URLs are never used as allowlist sources.
The startup error does not echo the rejected value.

For `/api/` requests with an Origin header, a nonmatching origin receives
`{"success": false, "error": "Origin not allowed"}` with HTTP 403 before an
API handler runs. Every `/api/` response includes `Vary: Origin`; matching
origins also receive their exact origin value. Allowed preflights terminate
before business handlers and
grant only route-supported methods from GET, POST, PUT, PATCH, DELETE, OPTIONS,
plus the fixed `Content-Type` and `Authorization` request headers. Credential
sharing is disabled. Health remains a minimal no-secret endpoint.

Request logs contain method, registered endpoint identifier (or `unmatched`),
and response status only. The logging hook does not parse JSON or record raw
paths, queries, headers, or bodies. Other route and service exception logs
remain separate review work.

This check enforces browser-origin behavior at the server boundary. A
non-browser caller can omit or spoof Origin. It is not authentication, tenant
ownership, general cookie CSRF protection, or public deployment readiness.
