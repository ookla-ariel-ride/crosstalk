# Crosstalk plugin for Hermes Agent

This plugin delivers Crosstalk inbox items to a Hermes Agent session. It
registers each session, injects new items and the check-in prompt only while
the session is idle, and adds a one-line note to a tool result when an item
arrives during a turn.

## Install

Use one Hermes profile per participant. `<pid>` is the participant id and
`<root>` is the Crosstalk checkout.

1. Run `hermes profile create <pid>`, then link the plugin into the profile:
   `ln -s <root>/scripts/hermes-plugin/crosstalk ~/.hermes/profiles/<pid>/plugins/crosstalk`
2. Add the `plugins` block to `~/.hermes/profiles/<pid>/config.yaml`. This
   command prints the block and all three steps for a participant:
   `python3 scripts/relay.py live hooks <pid> --harness hermes`.
   With a data root (`relay.py --data-root <dir>`), the block also sets
   `data_root`: the plugin loads its code from `root` and reads the logs in
   `data_root`.
3. Run `hermes -p <pid> plugins enable crosstalk`.

## Where things are

- `__init__.py` is a thin adapter, and `plugin.yaml` lists the hooks.
- The logic is `Pump` in `scripts/crosstalk/hermes.py`.
- The tests are in `tests/test_hermes.py`. They run the plugin against a fake
  Hermes context.

The full text is in
[docs/live-sessions.md, "Hermes sessions"](../../../docs/live-sessions.md#hermes-sessions):
the delivery rules, the shell hook for a profile that cannot load the plugin,
and the upstream bugs that the plugin works around.
