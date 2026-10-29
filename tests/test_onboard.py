import json
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path

import helpers
from helpers import SPEC_ROSTER, TempRepo

from crosstalk.onboard import kit_text, onboard, pick_colors, regenerate_kit, rotate
from crosstalk.participants import load_roster
from crosstalk.tokens import Tokens

REPO = Path(__file__).resolve().parent.parent


def cli_setup(r):
    """A temp repo with the scripts and a roster, ready for `relay.py`."""
    r.write_roster()
    shutil.copytree(str(REPO / "scripts"), str(r.root / "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    return r.root / "scripts" / "relay.py"


def run_cli(script, *args):
    return subprocess.run([sys.executable, str(script)] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, cwd="/")


def kit_entry(**kw):
    base = {"id": "smoke-agent", "name": "Smoke Agent", "kind": "agent",
            "color": "#2e7d32", "colorDark": "#81c784", "parent": "alpha",
            "trust": "quarantined"}
    base.update(kw)
    return base


class OnboardTests(unittest.TestCase):
    def test_concurrent_onboards_do_not_lose_entries(self):
        # Two onboards racing between their read and write used to lose one
        # entry (the last writer won). The flock serializes the whole
        # read-modify-write, so all N distinct ids must survive with working keys.
        with TempRepo() as r:
            r.write_roster()
            n = 5
            barrier = threading.Barrier(n)
            results = [None] * n
            errors = []

            def work(i):
                try:
                    barrier.wait()
                    results[i] = onboard(r.root, "c%d" % i, "C%d" % i, "tool")
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            roster = load_roster(r.root / "participants.json")
            for i in range(n):
                self.assertIn("c%d" % i, roster.participants)
            store = Tokens.load(r.root / "tokens.json")
            for i in range(n):
                self.assertIsNotNone(results[i])
                self.assertTrue(store.verify("Bearer " + results[i]["token"], "c%d" % i).ok)

    def test_duplicate_id_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError) as cm:
                onboard(r.root, "rev", "Rev Two", "reviewer")
            self.assertIn("rev", str(cm.exception))

    def test_repo_with_newline_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "tool", repo="owner/repo\nrm -rf")

    def test_repo_list_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "tool", repo=["owner/repo"])

    def test_repo_too_long_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "tool", repo="o" * 121 + "/r")

    def test_valid_repo_accepted(self):
        with TempRepo() as r:
            r.write_roster()
            onboard(r.root, "x", "X", "tool", repo="owner/repo")
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(roster.participants["x"].repo, "owner/repo")

    def test_onboard_keeps_aliases_and_item_names(self):
        with TempRepo() as r:
            data = json.loads(json.dumps(SPEC_ROSTER))
            data["participants"][1]["itemNames"] = {"bzone": "Battlezone"}
            r.write_roster(data)
            onboard(r.root, "x", "X", "tool")
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(roster.participants["alpha"].aliases, ["al"])
            self.assertEqual(roster.participants["beta"].itemNames, {"bzone": "Battlezone"})

    def test_load_roster_rejects_bad_repo(self):
        with TempRepo() as r:
            roster = json.loads(json.dumps(SPEC_ROSTER))
            roster["participants"][0]["repo"] = "bad\nrepo"
            r.write_roster(roster)
            with self.assertRaises(ValueError):
                load_roster(r.root / "participants.json")

    def test_duplicate_name_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError) as cm:
                onboard(r.root, "newbie", "Rev", "reviewer")
            self.assertIn("Rev", str(cm.exception))

    def test_bad_id_refused(self):
        with TempRepo() as r:
            r.write_roster()
            for bad in ("Bad Id", "Rev", "a b"):
                with self.assertRaises(ValueError):
                    onboard(r.root, bad, "X", "tool")

    def test_bad_kind_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "robot")

    def test_agent_without_parent_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "helper", "Helper", "agent")

    def test_check_in_min_bounded(self):
        with TempRepo() as r:
            r.write_roster()
            for bad in (0, -5, 1441, 9999):
                with self.assertRaises(ValueError):
                    onboard(r.root, "x", "X", "tool", check_in_min=bad)
            onboard(r.root, "ok1", "Ok1", "tool", check_in_min=1)
            onboard(r.root, "ok2", "Ok2", "tool", check_in_min=1440)

    def test_auto_colors_survive_load_roster(self):
        with TempRepo() as r:
            r.write_roster()
            result = onboard(r.root, "newbie", "Newbie", "project")
            roster = load_roster(r.root / "participants.json")
            self.assertIn("newbie", roster.participants)
            self.assertEqual(roster.participants["newbie"].trust, "quarantined")
            self.assertIn("token", result)
            self.assertIn("kit_path", result)

    def test_auto_colors_with_fullish_roster(self):
        with TempRepo() as r:
            r.write_roster()
            for i in range(5):
                onboard(r.root, "p%d" % i, "P%d" % i, "tool")
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(len(roster.participants), 4 + 5)

    def test_palette_exhaustion_raises(self):
        with TempRepo() as r:
            r.write_roster()
            for i in range(6):
                onboard(r.root, "p%d" % i, "P%d" % i, "tool")
            with self.assertRaises(ValueError):
                onboard(r.root, "p6", "P6", "tool")

    def test_invalid_override_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "tool", color="#f0f0f0", color_dark="#202020")

    def test_half_override_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.assertRaises(ValueError):
                onboard(r.root, "x", "X", "tool", color="#2e7d32")

    def test_valid_override_survives(self):
        with TempRepo() as r:
            r.write_roster()
            onboard(r.root, "x", "X", "tool", color="#2e7d32", color_dark="#81c784")
            roster = load_roster(r.root / "participants.json")
            self.assertEqual(roster.participants["x"].color, "#2e7d32")
            self.assertEqual(roster.participants["x"].colorDark, "#81c784")

    def test_pick_colors_returns_a_valid_pair(self):
        with TempRepo() as r:
            r.write_roster()
            roster = load_roster(r.root / "participants.json")
            color, color_dark = pick_colors(roster)
            onboard(r.root, "x", "X", "tool", color=color, color_dark=color_dark)
            load_roster(r.root / "participants.json")  # must not raise

    def test_onboard_writes_kit_with_token(self):
        with TempRepo() as r:
            r.write_roster()
            result = onboard(r.root, "newbie", "Newbie", "tool")
            kit = (r.root / "kits" / "newbie.md").read_text(encoding="utf-8")
            self.assertIn(result["token"], kit)
            self.assertIn('"from":"newbie"', kit)

    def test_kit_file_is_0600(self):
        with TempRepo() as r:
            r.write_roster()
            onboard(r.root, "newbie", "Newbie", "tool")
            mode = (r.root / "kits" / "newbie.md").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_kit_text_has_identity_and_key(self):
        with TempRepo() as r:
            r.write_roster()
            text = kit_text(r.root, "smoke-agent", "sekret-token", kit_entry())
            self.assertIn("smoke-agent", text)
            self.assertIn("Smoke Agent", text)
            self.assertIn("agent", text)
            self.assertIn("sekret-token", text)

    def test_kit_text_escapes_markdown_in_name(self):
        # A name like "[x](http://…)" would render as a live markdown link in
        # the kit the owner pastes; it must be backslash-escaped instead.
        with TempRepo() as r:
            r.write_roster()
            entry = kit_entry(name="[x](http://evil.example)")
            text = kit_text(r.root, "smoke-agent", "sekret-token", entry)
            self.assertNotIn("](http", text)
            self.assertIn("\\[x\\]\\(", text)

    def test_kit_text_escapes_backticks_in_name(self):
        with TempRepo() as r:
            r.write_roster()
            entry = kit_entry(name="`code`")
            text = kit_text(r.root, "smoke-agent", "sekret-token", entry)
            self.assertIn("\\`code\\`", text)

    def test_kit_text_from_in_both_examples(self):
        with TempRepo() as r:
            r.write_roster()
            text = kit_text(r.root, "smoke-agent", "sekret-token", kit_entry())
            self.assertIn('"from":"smoke-agent"', text)
            self.assertGreaterEqual(text.count('"from":"smoke-agent"'), 2)

    def test_kit_text_has_quarantine_and_warning(self):
        with TempRepo() as r:
            r.write_roster()
            text = kit_text(r.root, "smoke-agent", "sekret-token", kit_entry())
            self.assertIn("quarantined", text)
            self.assertIn("Do not paste", text)

    def test_kit_text_no_unreplaced_placeholders(self):
        with TempRepo() as r:
            r.write_roster()
            text = kit_text(r.root, "smoke-agent", "sekret-token", kit_entry())
            self.assertNotIn("__", text)
            self.assertNotIn("{pid}", text)
            self.assertNotIn("{token}", text)
            self.assertNotIn("@PID@", text)
            self.assertNotIn("@TOKEN@", text)

    def test_kit_text_uses_page_url_and_absolute_root(self):
        with TempRepo() as r:
            r.write_roster()
            text = kit_text(r.root, "smoke-agent", "sekret-token", kit_entry())
            self.assertIn(SPEC_ROSTER["page"], text)
            self.assertIn(str(r.root.resolve()), text)

    def test_rotation_old_key_stops_verifying(self):
        with TempRepo() as r:
            r.write_roster()
            old = onboard(r.root, "newbie", "Newbie", "tool")["token"]
            store = Tokens.load(r.root / "tokens.json")
            self.assertTrue(store.verify("Bearer " + old, "newbie").ok)
            new = rotate(r.root, "newbie")
            self.assertNotEqual(old, new)
            store = Tokens.load(r.root / "tokens.json")
            self.assertFalse(store.verify("Bearer " + old, "newbie").ok)
            self.assertTrue(store.verify("Bearer " + new, "newbie").ok)

    def test_rotate_sets_tokens_0600(self):
        with TempRepo() as r:
            r.write_roster()
            onboard(r.root, "newbie", "Newbie", "tool")
            rotate(r.root, "newbie")
            mode = (r.root / "tokens.json").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_gitignore_lists_kits(self):
        text = (REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("kits/", text)


TOKEN = "sekret-token-0123456789abcdef"


def mcp_section(text):
    """The kit's MCP block: from its heading to the next level-2 heading."""
    start = text.index("## MCP server")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def json_blocks(section):
    """Every ```json fenced block in a section, parsed."""
    out = []
    parts = section.split("```json\n")
    for part in parts[1:]:
        out.append(json.loads(part.split("```", 1)[0]))
    return out


# A marker that only the harness's own snippet contains.
HARNESS_MARKERS = {
    "claude-code": "claude mcp add --transport stdio --scope project crosstalk",
    "codex": "[mcp_servers.crosstalk]",
    "opencode": '"$schema": "https://opencode.ai/config.json"',
    "gemini-cli": ".gemini/settings.json",
    "hermes": "/reload-mcp",
    "pi": "pi install npm:pi-mcp-adapter",
}


class KitMcpTests(unittest.TestCase):
    def kit(self, root, harness=None, pid="smoke-agent"):
        entry = kit_entry(id=pid)
        if harness is not None:
            entry["harness"] = harness
        return kit_text(root, pid, TOKEN, entry)

    def test_each_covered_harness_gets_its_own_snippet(self):
        with TempRepo() as r:
            r.write_roster()
            for harness, marker in HARNESS_MARKERS.items():
                section = mcp_section(self.kit(r.root, harness))
                self.assertIn(marker, section, harness)
                for other, other_marker in HARNESS_MARKERS.items():
                    if other != harness:
                        self.assertNotIn(other_marker, section, (harness, other))
                self.assertNotIn("Generic", section, harness)

    def test_unknown_or_missing_harness_gets_generic_entry(self):
        with TempRepo() as r:
            r.write_roster()
            relay = str(r.root.resolve() / "scripts" / "relay.py")
            docs = str(r.root.resolve() / "docs" / "mcp.md")
            for harness in (None, "copilot", "amp"):
                section = mcp_section(self.kit(r.root, harness))
                for marker in HARNESS_MARKERS.values():
                    self.assertNotIn(marker, section, harness)
                self.assertIn("Generic", section)
                self.assertIn(docs, section)
                (entry,) = json_blocks(section)
                self.assertEqual(entry, {"command": "python3",
                                         "args": [relay, "mcp", "--as", "smoke-agent"]})

    def test_snippet_names_pid_and_absolute_relay_path(self):
        with TempRepo() as r:
            r.write_roster()
            relay = str(r.root.resolve() / "scripts" / "relay.py")
            for harness in list(HARNESS_MARKERS) + [None]:
                section = mcp_section(self.kit(r.root, harness, pid="beta.helper"))
                self.assertIn(relay, section, harness)
                self.assertTrue(relay.startswith("/"))
                self.assertIn("--as", section)
                self.assertIn("beta.helper", section, harness)
                self.assertNotIn("<id>", section, harness)
                self.assertNotIn("<you>", section, harness)

    def test_json_snippets_parse_with_exact_args(self):
        with TempRepo() as r:
            r.write_roster()
            relay = str(r.root.resolve() / "scripts" / "relay.py")
            args = [relay, "mcp", "--as", "smoke-agent"]
            for harness in ("claude-code", "gemini-cli", "pi"):
                (cfg,) = json_blocks(mcp_section(self.kit(r.root, harness)))
                self.assertEqual(cfg["mcpServers"]["crosstalk"],
                                 {"command": "python3", "args": args}, harness)
            (cfg,) = json_blocks(mcp_section(self.kit(r.root, "opencode")))
            server = cfg["mcp"]["crosstalk"]
            self.assertEqual(server["type"], "local")
            self.assertEqual(server["command"], ["python3"] + args)
            self.assertIs(server["enabled"], True)

    def test_codex_and_hermes_entries(self):
        with TempRepo() as r:
            r.write_roster()
            relay = str(r.root.resolve() / "scripts" / "relay.py")
            args = '["%s", "mcp", "--as", "smoke-agent"]' % relay
            codex = mcp_section(self.kit(r.root, "codex"))
            self.assertIn('[mcp_servers.crosstalk]\ncommand = "python3"\nargs = %s\n' % args, codex)
            self.assertIn("codex mcp add crosstalk -- python3 %s mcp --as smoke-agent" % relay, codex)
            hermes = mcp_section(self.kit(r.root, "hermes"))
            self.assertIn('mcp_servers:\n  crosstalk:\n    command: "python3"\n    args: %s\n' % args,
                          hermes)

    def test_shell_command_quotes_a_path_with_spaces(self):
        with TempRepo() as r:
            root = r.root / "my checkout"
            root.mkdir()
            section = mcp_section(self.kit(root, "claude-code"))
            relay = str(root.resolve() / "scripts" / "relay.py")
            self.assertIn("-- python3 '%s' mcp --as smoke-agent" % relay, section)
            (cfg,) = json_blocks(section)
            self.assertEqual(cfg["mcpServers"]["crosstalk"]["args"][0], relay)

    def test_no_token_in_mcp_block_and_token_not_duplicated(self):
        with TempRepo() as r:
            r.write_roster()
            plain = kit_text(r.root, "smoke-agent", TOKEN, kit_entry())
            for harness in list(HARNESS_MARKERS) + [None, "copilot"]:
                text = self.kit(r.root, harness)
                section = mcp_section(text)
                self.assertNotIn(TOKEN, section, harness)
                self.assertNotIn("Bearer", section, harness)
                self.assertEqual(text.count(TOKEN), plain.count(TOKEN), harness)
                self.assertEqual(text.count(TOKEN), 2, harness)
                # The key keeps its own section, after the MCP block.
                key = text.index("## Your key")
                self.assertIn(TOKEN, text[key:key + 200])
                self.assertLess(text.index("## Reading"), text.index("## MCP server"))
                self.assertLess(text.index("## MCP server"), key)

    def test_kit_keeps_existing_sections_in_order(self):
        with TempRepo() as r:
            r.write_roster()
            text = self.kit(r.root, "codex")
            heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
            self.assertEqual(heads, ["## The page", "## Writing", "## Reading", "## MCP server",
                                     "## Your key", "## The protocol", "## Trust", "## First action"])

    def test_onboard_kit_uses_harness(self):
        with TempRepo() as r:
            r.write_roster()
            result = onboard(r.root, "newbie", "Newbie", "tool", harness="codex")
            kit = (r.root / "kits" / "newbie.md").read_text(encoding="utf-8")
            section = mcp_section(kit)
            self.assertIn("[mcp_servers.crosstalk]", section)
            self.assertIn('"--as", "newbie"', section)
            self.assertNotIn(result["token"], section)

    def test_regenerated_kit_uses_roster_harness(self):
        with TempRepo() as r:
            data = json.loads(json.dumps(SPEC_ROSTER))
            data["participants"][2]["harness"] = "hermes"
            r.write_roster(data)
            result = regenerate_kit(r.root, "rev")
            kit = (r.root / "kits" / "rev.md").read_text(encoding="utf-8")
            section = mcp_section(kit)
            self.assertIn("/reload-mcp", section)
            self.assertIn('"--as", "rev"', section)
            self.assertNotIn(result["token"], section)


class OnboardCliTests(unittest.TestCase):
    def test_onboard_prints_kit_path_and_token(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "onboard", "newbie", "--name", "Newbie", "--kind", "tool")
            self.assertEqual(p.returncode, 0, p.stderr)
            lines = p.stdout.strip().splitlines()
            self.assertEqual(len(lines), 2)
            kit_path, token = lines
            self.assertTrue(kit_path.endswith("kits/newbie.md"), kit_path)
            self.assertGreaterEqual(len(token), 32)
            self.assertTrue((r.root / "kits" / "newbie.md").exists())

    def test_onboard_refuses_duplicate(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "onboard", "rev", "--name", "Rev", "--kind", "reviewer")
            self.assertEqual(p.returncode, 1)
            self.assertEqual(p.stdout, "")

    def test_kit_unknown_pid_exits_1(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "kit", "nobody")
            self.assertEqual(p.returncode, 1)
            self.assertIn("nobody", p.stderr)

    def test_kit_regenerates(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "onboard", "newbie", "--name", "Newbie", "--kind", "tool")
            self.assertEqual(p.returncode, 0, p.stderr)
            p = run_cli(script, "kit", "newbie")
            self.assertEqual(p.returncode, 0, p.stderr)
            lines = p.stdout.strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].endswith("kits/newbie.md"))
            self.assertTrue((r.root / "kits" / "newbie.md").exists())

    def test_token_new_rotates(self):
        with TempRepo() as r:
            script = cli_setup(r)
            old = run_cli(script, "token", "rev").stdout.strip()
            new = run_cli(script, "token", "rev", "--new").stdout.strip()
            self.assertNotEqual(old, new)
            store = Tokens.load(r.root / "tokens.json")
            self.assertFalse(store.verify("Bearer " + old, "rev").ok)
            self.assertTrue(store.verify("Bearer " + new, "rev").ok)

    def test_token_without_new_refuses_when_a_key_exists(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "token", "rev")
            self.assertEqual(p.returncode, 0, p.stderr)
            p = run_cli(script, "token", "rev")
            self.assertEqual(p.returncode, 1)
            self.assertIn("already has a key", p.stderr)
            self.assertEqual(p.stdout, "")

    def test_token_mints_when_no_key_exists(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "token", "rev")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertGreaterEqual(len(p.stdout.strip()), 32)

    def test_token_kit_regenerates_kit(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "token", "rev", "--kit")
            self.assertEqual(p.returncode, 0, p.stderr)
            lines = p.stdout.strip().splitlines()
            self.assertEqual(len(lines), 2)
            token = lines[0]
            self.assertTrue((r.root / "kits" / "rev.md").exists())
            kit = (r.root / "kits" / "rev.md").read_text(encoding="utf-8")
            self.assertIn(token, kit)


if __name__ == "__main__":
    unittest.main()
