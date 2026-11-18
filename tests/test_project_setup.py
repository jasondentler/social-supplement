"""Offline validation of Keychain configuration, startup and commit policy."""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.config import KEYS, SERVICE, TUNNEL_KEYS, configure, read_settings
from scripts.lint_commit import validate
from scripts.serve import PYTHON, SERVER, build_environment


class MemoryKeyring:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_password(self, service, name):
        assert service == SERVICE
        return self.values.get(name)

    def set_password(self, service, name, value):
        assert service == SERVICE
        self.values[name] = value


class ConfigTests(unittest.TestCase):
    def test_provider_configuration_does_not_require_tunnel(self):
        store = MemoryKeyring()
        output = []
        configure(store=store, prompt=lambda _: "threads-test-token", output=output.append)
        self.assertEqual(len(store.values), len(KEYS))
        self.assertEqual(read_settings(store), {"threads-token": "threads-test-token"})
        self.assertEqual(set(store.values), {"threads-token"})
        self.assertNotIn("threads-test-token", str(output))

    def test_separate_optional_tunnel_configuration(self):
        store = MemoryKeyring({"threads-token": "threads-test-token"})
        prompts = iter(["runtime-test-key", "tunnel_test123"])
        configure(
            store=store,
            prompt=lambda _: next(prompts),
            output=lambda _: None,
            include_tunnel=True,
        )
        settings = read_settings(store, include_tunnel=True)
        self.assertEqual(len(settings), len(KEYS) + len(TUNNEL_KEYS))
        self.assertEqual(settings["tunnel-id"], "tunnel_test123")

    def test_enter_preserves_existing(self):
        settings = {"threads-token": "existing"}
        store = MemoryKeyring(settings)
        configure(store=store, prompt=lambda _: "", output=lambda _: None)
        self.assertEqual(read_settings(store), settings)

    def test_missing_provider_token_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "make config"):
            read_settings(MemoryKeyring())

    def test_missing_tunnel_rejected_only_when_requested(self):
        store = MemoryKeyring({"threads-token": "present"})
        self.assertEqual(read_settings(store)["threads-token"], "present")
        with self.assertRaisesRegex(RuntimeError, "make config-tunnel"):
            read_settings(store, include_tunnel=True)

    def test_invalid_tunnel_id_rejected(self):
        store = MemoryKeyring()
        values = iter(["runtime-test-key", "not-a-tunnel"])
        with self.assertRaisesRegex(ValueError, "Tunnel ID"):
            configure(
                store=store,
                prompt=lambda _: next(values),
                output=lambda _: None,
                include_tunnel=True,
            )


class StartupTests(unittest.TestCase):
    def test_direct_stdio_does_not_require_openai(self):
        env = build_environment(
            {"threads-token": "super-secret-threads"},
            current={"PATH": "/usr/bin"},
        )
        self.assertEqual(env["THREADS_TOKEN"], "super-secret-threads")
        self.assertNotIn("CONTROL_PLANE_API_KEY", env)
        self.assertNotIn("CONTROL_PLANE_TUNNEL_ID", env)
        self.assertNotIn("MCP_COMMAND", env)

    def test_optional_tunnel_environment_contains_no_secrets_in_argv(self):
        settings = {
            "threads-token": "super-secret-threads",
            "tunnel-runtime-key": "super-secret-api",
            "tunnel-id": "tunnel_example",
        }
        env = build_environment(settings, current={"PATH": "/usr/bin"}, tunnel=True)
        self.assertEqual(env["THREADS_TOKEN"], "super-secret-threads")
        self.assertEqual(env["CONTROL_PLANE_API_KEY"], "super-secret-api")
        self.assertEqual(env["CONTROL_PLANE_TUNNEL_ID"], "tunnel_example")
        self.assertIn(str(PYTHON), env["MCP_COMMAND"])
        self.assertIn(str(SERVER), env["MCP_COMMAND"])
        self.assertNotIn("super-secret", env["MCP_COMMAND"])


class MakeTargetsTests(unittest.TestCase):
    def test_serve_starts_tunnel_and_stdio_remains_available(self):
        makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
        self.assertIn("serve: check\n\t@$(PY) -m scripts.serve --tunnel", makefile)
        self.assertIn("stdio:\n\t@$(MAKE) --no-print-directory check 1>&2\n\t@$(PY) -m scripts.serve", makefile)


class CommitLintTests(unittest.TestCase):
    def test_one_line_conventional_subject(self):
        validate("feat(mcp): return image content")

    def test_multiline_rejected(self):
        with self.assertRaisesRegex(ValueError, "single line"):
            validate("feat: message\nsecond line")

    def test_nonconventional_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conventional"):
            validate("updated stuff")


if __name__ == "__main__":
    unittest.main()
