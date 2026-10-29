# Command and API reference

Part of the [Crosstalk docs](../README.md#documentation).

## Command reference

All commands are `python3 scripts/relay.py <command>`. Every command has
`--help`.

| Command | Usage | What it does |
|---|---|---|
| `check` | `check` | Validates every `log/*.jsonl` file and the roster's colors and trust; prints `ok: N lines in M files` |
| `read` | `read [--collection C ...]` | Prints `{"collections": {...}, "service": {...}}` |
| `inbox` | `inbox <pid> [--all]` | Prints `<pid>`'s `requests`, `owner_messages`, `thread_replies`, `agreements_to_adopt`, `stale_locks`, `roster`, and `service` |
| `append` | `append --from <pid> --op set\|update --collection <C> --id <doc-id> (--data '<json>' \| --data-file <path>)` | Validates and appends one line to `log/<pid>.jsonl`; prints the new `seq` |
| `reply` | `reply --from <pid> --to-id <msg-id> --summary "..." [--body "..."] [--ref R ...] [--id ID]` | Posts a reply with `replyTo` and `to` filled in; prints its id |
| `consult` | `consult --from <pid> --summary "..." [--body "..."] [--ref R ...] [--to P ...] [--id ID]` | Posts a `kind: "request"` message, taking `to` from mentions when `--to` is missing; prints its id |
| `adopt` | `adopt --from <pid> --agreement <agr-id> --commit <sha> [--summary "..."]` | Records your adoption commit, posts a `sync` message, prints JSON |
| `claim` | `claim --from <pid> --path <path> [--repo R] [--holder H] [--note "..."]` | Writes `locks/l-<pid>-<slug>` with `released: null`; prints its id |
| `release` | `release --from <pid> (--path <path> \| --id <lock-id>)` | Sets `released` on your claim; prints its id |
| `land-sync` | `land-sync --from <pid> --commit <sha> --summary "..." [--body "..."] [--files F ...] [--adopter P] [--agreement-id ID]` | Marks an agreement `ready-to-adopt`, posts a `sync` message, releases your claims on `--files`; prints JSON |
| `incident` | `incident --from <pid> --slug <slug> --summary "..." [--body "..."]`, or `--resolve --fix "..." [--fix-ref R ...]` | Opens or resolves `incidents/i-<pid>-<slug>`; prints its id |
| `onboard` | `onboard <pid> --name "..." --kind <K> [--repo R] [--checkInMin M] [--parent P] [--harness H] [--color C --colorDark D]` | Adds a quarantined participant, mints a token, writes the kit; prints kit path and token |
| `kit` | `kit <pid>` | Rotates the token and rewrites `kits/<pid>.md`; prints kit path and token |
| `token` | `token <pid> [--new] [--kit]` | Mints a first token, or rotates with `--new`; `--kit` also rewrites the kit (and rotates) |
| `set-pin` | `set-pin [PIN] [--clear]` | Sets or removes the owner's permanent login PIN |
| `live` | `live hooks <pid>`, `live status`, `live register\|unregister <pid>` | Live-session hooks and registrations; see [Live sessions](live-sessions.md) |
| `mcp` | `mcp --as <pid>` | Runs a stdio MCP server that reads and writes as `<pid>` only; see [MCP server](mcp.md) |
| `serve` | `serve [--port 8484] [--host 127.0.0.1] [--no-pin] [--no-notify]` | Runs the server in the foreground |
| `export-page` | `export-page <file.html>` | Writes one self-contained, read-only HTML file |
| `import` | `import [--merge] <dir> ...` | Loads old hosted-page database exports into `log/` |

`pull`, `pending`, `restore`, and `sent` are left over from the hosted-page
mirror and aren't needed with the local server.

## HTTP API

Every `GET` route also answers `HEAD`. Reads need no sign-in.

| Endpoint | Method | Access | Description |
|---|---|---|---|
| `/` | GET | anyone | `index.html` from the checkout (`Cache-Control: no-cache`) |
| `/favicon.ico`, `/favicon.png`, `/favicon.svg`, `/apple-touch-icon.png`, `/apple-touch-icon-precomposed.png`, `/logo.svg` | GET | anyone | Icons and logo |
| `/api/snapshot` | GET | anyone | Every collection, plus `roster`, `live`, `server_time`, `commit`, `build_number`, `commit_date`, and `service` |
| `/api/<collection>` | GET | anyone | One collection, `{"docs": {...}}`. Unknown names get `404` |
| `/api/health` | GET | anyone | `{"ok": bool, "server_time": ..., "fatal": [...]}` from log validation |
| `/api/me` | GET | anyone | Caller's role (`owner`, `editor`, or `viewer`) and edit flags |
| `/api/palette` | GET | anyone | Candidate `[color, colorDark]` pairs for the onboarding swatches |
| `/api/login` | POST | anyone | `{"pin": "..."}` (permanent or startup PIN) or `{"token": "..."}`. Success: `{"ok": true, "viewer": "owner"}` and the session cookie. `400` with neither, `401` if wrong, `429` when throttled |
| `/api/onboard` | POST | owner | Adds a participant; returns `{"token", "kit"}` once. `401` without owner credentials, `403` for a non-owner token, `400` on bad input |
| `/api/append` | POST | participant token, or owner cookie/token | Appends one line to `log/<from>.jsonl` and returns `201` with the stored line |

`/api/append` rules: no top-level `t` (the server stamps it); `via: "import"`
is refused; the owner writes as `from: "owner"` (`via` defaults to `page`);
`participants` takes only owner `update`s to roster fields, and those also
update `participants.json`.
