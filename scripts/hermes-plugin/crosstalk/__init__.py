"""Crosstalk plugin for Hermes Agent (read against the v0.22.7 source, 2026-11-06).

A thin adapter: the logic is crosstalk.hermes.Pump in this checkout's
scripts/, so it shares Crosstalk's own inbox code and is tested there.

Settings, in the participant profile's config.yaml:

    plugins:
      enabled: [crosstalk]
      entries:
        crosstalk:
          allow_gateway_injection: true   # gateway, TUI, desktop; the CLI doesn't check it
          settings:
            participant: <pid>
            root: /path/to/crosstalk      # default: the checkout this file is in

Hooks: pre_llm_call registers a session on its first turn, marks it busy and
adds new items to the turn as context, and ignores subagents and cron jobs.
on_session_start is not used: Hermes fires it for delegated children too,
without parent_session_id, and again on a prompt rebuild.
on_session_end (after every turn) marks it idle; transform_tool_result
appends a one-line note when items arrive mid-turn; post_compaction follows a
session's new id; on_session_finalize unregisters. A background thread polls
the log and calls ctx.inject_message only while a session is idle.
post_tool_call is not used: Hermes skips it in injected turns
(NousResearch/hermes-agent#141867).
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# scripts/hermes-plugin/crosstalk/__init__.py -> the checkout (through a symlink too)
DEFAULT_ROOT = Path(__file__).resolve().parents[3]


def _session_key() -> str:
    """The calling session's routing key; hooks don't receive it as a kwarg."""
    try:
        from hermes_cli.plugin_session import session_key
        key = session_key()
        if key:
            return key
    except Exception:
        pass
    try:
        from gateway.session_context import bound_session_key
        return bound_session_key() or ""
    except Exception:
        return ""


def _load_pump_module(root: Path):
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from crosstalk import hermes
    return hermes


def make_pump(ctx, root: Path, pid: str):
    """The Pump wired to ctx (separate so tests can build it with a fake ctx)."""
    ct = _load_pump_module(root)

    def inject(text, key):
        # While the classic CLI's agent runs, inject_message puts the text on the queue that
        # calls agent.interrupt(). Refuse here too, for a turn pre_llm_call hasn't shown yet.
        cli = getattr(getattr(ctx, "_manager", None), "_cli_ref", None)
        if cli is not None and getattr(cli, "_agent_running", False):
            return False
        # The session key it saw in that session's own hooks (#139847).
        return ctx.inject_message(text, role="user", session_key=key)

    return ct.Pump(root, pid, ct.state_path_from_env(), inject, log=logger.warning)


def register(ctx):
    pid = ctx.get_config("participant")
    root = Path(ctx.get_config("root") or DEFAULT_ROOT).expanduser()
    if not isinstance(pid, str) or not pid:
        logger.warning("crosstalk: set plugins.entries.crosstalk.settings.participant; "
                       "the plugin stays idle")
        return
    try:
        pump = make_pump(ctx, root, pid)
    except Exception as e:
        logger.warning("crosstalk: cannot load Crosstalk from %s: %s", root, e)
        return
    wire(ctx, pump)
    pump.start()


def wire(ctx, pump):
    def pre_llm_call(session_id="", parent_session_id="", platform="", **kwargs):
        text = pump.on_turn_start(session_id or "", _session_key(), parent_session_id or "",
                                  platform or "")
        return {"context": text} if text else None

    def on_session_end(session_id="", **kwargs):
        pump.on_turn_end(session_id or "")

    def transform_tool_result(result=None, session_id="", **kwargs):
        return pump.on_tool_result(session_id or "", result)

    def post_compaction(session_id="", old_session_id="", **kwargs):
        pump.on_compacted(old_session_id or "", session_id or "", _session_key())

    def on_session_finalize(session_id=None, **kwargs):
        pump.on_session_finalize(session_id or "")

    for name, fn in (("pre_llm_call", pre_llm_call),
                     ("on_session_end", on_session_end),
                     ("transform_tool_result", transform_tool_result),
                     ("post_compaction", post_compaction),
                     ("on_session_finalize", on_session_finalize)):
        ctx.register_hook(name, fn)

    def unload():
        pump.stop()
        pump.unregister_all()

    ctx.on_unload(unload)
