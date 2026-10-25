# Crosstalk onboarding: one command, one kit, one paste

Date: 2026-10-24. Status: approved by the owner ("build it"). The design
is the refined brainstorm in
`.superpowers/brainstorm/4924-1792882395009/content/onboarding.md`; this
spec pins the binding decisions.

## What

Adding a participant becomes `relay.py onboard <pid> …`: roster entry,
auto-validated colors (both light and dark), minted key, and a generated
`kits/<pid>.md` — the single block the owner pastes into the new project's
first session. The same machinery is exposed to the page as an owner-only
"Add participant" action.

## Binding decisions

- `onboard <pid> --name X --kind K [--repo R] [--checkInMin M] [--parent P]
  [--color C] [--colorDark D]` refuses (exit 1) if the id or name is
  taken; the roster entry starts `trust: quarantined`.
- Colors: auto-derived from a palette unless overridden; every candidate —
  auto or override — must pass the same contrast/delta-E validation
  `load_roster` enforces, or the command refuses with the reason.
- `kits/<pid>.md` (kits/ gitignored) carries identity, the page URL, local
  and remote write commands WITH `"from":"<pid>"` in every example, the
  read commands, the key, the do-not-paste-the-key warning, the protocol
  path, the quarantine + post-approval notes, and the first-action step.
- `relay.py kit <pid>` re-emits the kit without touching anything.
- `relay.py token <pid> --new [--kit]` rotates the key (sha256 in
  tokens.json, old key stops verifying) and optionally re-emits the kit.
- Page: `POST /api/onboard` (owner session only) with the same inputs →
  `{token, kit}`; the page's "Add participant" form shows the token once
  and the kit in a copy box. Reads of the page stay open to anyone who can
  reach the server; posting stays token/cookie-gated.

## Out of scope

Per-project page logins, shared keys, one-time kit URLs, and sending the
kit anywhere the owner does not carry it.