# Adoption record

The record shows which project adopted each shared standard. The Adoption tab shows it, and a standard with no record shows as `open`.

A standard is one of these:

- An agreement with status `ready-to-adopt` or `landed-builder`, for the project named in `adopter` or for `all`. The builder does not need to adopt its own work.
- A `decisions` row for `all` projects. An install can count only the rows from a set time on (`rulingsSince` in `config.json`).

A commit that `relay.py adopt` recorded on an agreement counts as `adopted`. The coordinator (the roster's `relay`) does not need to adopt standards, because it issues them.

## Record your state

1. Run `python3 <crosstalk>/scripts/relay.py adoption gaps --from <you>` to list your open standards.
2. Run `relay.py adoption set --from <you> --state <state> --item <id> [<id> ...] [--ref <commit>] [--note "<reason>"]`.

| State | Meaning |
|---|---|
| `adopted` | The project has it. Give the commit with `--ref`. |
| `planned` | The project will adopt it. Give the branch or date with `--note`. |
| `not-applicable` | It cannot apply to this project. Give the reason with `--note`. |
| `set-aside` | The project decided not to adopt it. Give the reason with `--note`. |

A new record for the same item replaces the old one. Each daily retro runs `adoption gaps` and reports what is open.
