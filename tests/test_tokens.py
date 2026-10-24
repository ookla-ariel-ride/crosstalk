import hmac
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import TempRepo

from crosstalk import tokens as tokens_module
from crosstalk.tokens import Tokens, load_tokens, token_hash

REPO = Path(__file__).resolve().parent.parent


def cli_setup(r):
    """A temp repo with the scripts and a roster, ready for `relay.py`."""
    r.write_roster()
    shutil.copytree(str(REPO / "scripts"), str(r.root / "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    return r.root / "scripts" / "relay.py"


def run_cli(script, *args):
    return subprocess.run([sys.executable, str(script)] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, cwd="/")


class TokenTests(unittest.TestCase):
    def test_mint_then_verify(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            token = store.mint("codex")
            self.assertGreaterEqual(len(token), 32)
            result = store.verify("Bearer " + token)
            self.assertTrue(result.ok, result)
            self.assertEqual(result.pid, "codex")

    def test_only_the_hash_is_stored(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            token = store.mint("codex")
            text = (r.root / "tokens.json").read_text(encoding="utf-8")
            self.assertNotIn(token, text)
            self.assertEqual(json.loads(text), {"codex": token_hash(token)})

    def test_the_store_is_private_to_its_owner(self):
        with TempRepo() as r:
            Tokens(r.root / "tokens.json").mint("codex")
            mode = (r.root / "tokens.json").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_an_unknown_token_is_refused(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            store.mint("codex")
            result = store.verify("Bearer not-the-token")
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "unknown")

    def test_a_missing_header_is_refused(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            store.mint("codex")
            for header in (None, "", "Bearer", "Basic abc", "tok-codex"):
                result = store.verify(header)
                self.assertFalse(result.ok, header)
                self.assertIn(result.code, ("missing", "malformed"), header)

    def test_a_token_of_another_participant_does_not_match(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            token = store.mint("codex")
            result = store.verify("Bearer " + token, "vectorcraft")
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "mismatch")
            self.assertEqual(result.pid, "codex")
            self.assertTrue(store.verify("Bearer " + token, "codex").ok)

    def test_the_comparison_is_constant_time_and_hashed(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            token = store.mint("codex")
            seen = []
            real = hmac.compare_digest

            def spy(a, b):
                seen.append((a, b))
                return real(a, b)

            with mock.patch.object(tokens_module.hmac, "compare_digest", spy):
                result = store.verify("Bearer " + token)
            self.assertTrue(result.ok)
            self.assertTrue(seen)
            # The comparison is on hashes: the token itself is never compared.
            self.assertNotIn(token, str(seen))

    def test_a_missing_file_is_no_tokens(self):
        with TempRepo() as r:
            self.assertIsNone(load_tokens(r.root / "tokens.json"))
            store = Tokens.load(r.root / "tokens.json")
            self.assertEqual(store.entries, {})
            self.assertFalse(store.verify("Bearer whatever").ok)

    def test_the_store_survives_a_reload(self):
        with TempRepo() as r:
            token = Tokens(r.root / "tokens.json").mint("codex")
            again = Tokens.load(r.root / "tokens.json")
            self.assertTrue(again.verify("Bearer " + token).ok)

    def test_minting_twice_replaces_the_old_token(self):
        with TempRepo() as r:
            store = Tokens(r.root / "tokens.json")
            first = store.mint("codex")
            second = store.mint("codex")
            self.assertNotEqual(first, second)
            self.assertFalse(store.verify("Bearer " + first).ok)
            self.assertTrue(store.verify("Bearer " + second).ok)


class TokenCliTests(unittest.TestCase):
    def test_relay_token_prints_the_token_once_and_keeps_its_hash(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "token", "codex")
            self.assertEqual(p.returncode, 0, p.stderr)
            token = p.stdout.strip()
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(len(token.split()), 1)
            stored = json.loads((r.root / "tokens.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, {"codex": token_hash(token)})
            self.assertNotIn(token, (r.root / "tokens.json").read_text(encoding="utf-8"))

    def test_relay_token_can_mint_for_any_participant(self):
        with TempRepo() as r:
            script = cli_setup(r)
            for pid in ("codex", "owner"):
                p = run_cli(script, "token", pid)
                self.assertEqual(p.returncode, 0, p.stderr)
            stored = json.loads((r.root / "tokens.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(stored), ["codex", "owner"])

    def test_relay_token_refuses_an_unknown_participant(self):
        with TempRepo() as r:
            script = cli_setup(r)
            p = run_cli(script, "token", "nobody")
            self.assertEqual(p.returncode, 1)
            self.assertIn("nobody", p.stderr)
            self.assertEqual(p.stdout, "")
            self.assertFalse((r.root / "tokens.json").exists())


if __name__ == "__main__":
    unittest.main()
