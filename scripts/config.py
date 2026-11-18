"""Configure provider credentials and optional tunnel credentials in the OS keyring."""

from __future__ import annotations

from getpass import getpass
import sys

SERVICE = "social-supplement-mcp"
KEYS = (("threads-token", "Threads API user access token", True),)
TUNNEL_KEYS = (
    ("tunnel-runtime-key", "OpenAI tunnel runtime API key", True),
    ("tunnel-id", "OpenAI tunnel ID (tunnel_...)", False),
)


def get_store():
    """Refuse missing or explicitly insecure keyring backends."""
    import keyring
    from keyring.backends import fail, null

    backend = keyring.get_keyring()
    if isinstance(backend, (fail.Keyring, null.Keyring)):
        raise RuntimeError("No secure OS keyring available; configure your system keyring")
    return keyring


def read_settings(store=None, include_tunnel: bool = False) -> dict[str, str]:
    """Read secrets without echoing them. Tunnel credentials are optional."""
    if store is None:
        store = get_store()
    keys = KEYS + (TUNNEL_KEYS if include_tunnel else ())
    values = {}
    for key, _, _ in keys:
        try:
            value = store.get_password(SERVICE, key)
        except Exception as exc:
            raise RuntimeError(f"Unable to read {key} from the OS keyring") from exc
        if not value:
            command = "make config-tunnel" if key.startswith("tunnel-") else "make config"
            raise RuntimeError(f"Missing {key}; run '{command}'")
        values[key] = value
    if include_tunnel and not values["tunnel-id"].startswith("tunnel_"):
        raise RuntimeError("Tunnel ID must start with tunnel_; run 'make config-tunnel'")
    return values


def configure(store=None, prompt=getpass, output=print, include_tunnel: bool = False) -> None:
    """Prompt for provider or tunnel credentials; Enter preserves existing values."""
    if store is None:
        store = get_store()
    output("Configure optional tunnel credentials:" if include_tunnel else "Configure social-media credentials:")
    keys = TUNNEL_KEYS if include_tunnel else KEYS
    for key, label, _secret in keys:
        current = store.get_password(SERVICE, key)
        value = prompt(f"{label} [{'keep existing' if current else 'required'}]: ").strip()
        if not value:
            if current:
                continue
            raise ValueError(f"{label} is required")
        if key == "tunnel-id" and not value.startswith("tunnel_"):
            raise ValueError("Tunnel ID must start with tunnel_")
        store.set_password(SERVICE, key, value)
    output("Credentials stored in the OS keyring. No .env file created.")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv not in ([], ["--tunnel"]):
        print("Usage: python -m scripts.config [--tunnel]", file=sys.stderr)
        return 2
    try:
        configure(include_tunnel=(argv == ["--tunnel"]))
    except (RuntimeError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
