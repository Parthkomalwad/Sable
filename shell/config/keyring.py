"""Linux keyring integration via secretstorage.

Phase 2 only. Stores and retrieves API keys from the Linux keyring.
Falls back to config.json if keyring is unavailable.
"""
from __future__ import annotations

KEYRING_APP = "agentic-shell"


def store_api_key(service: str, key: str) -> None:
    """Store an API key in the Linux keyring.

    Args:
        service: Service name, e.g. 'openai', 'anthropic'.
        key: API key string.
    """
    try:
        import secretstorage  # type: ignore

        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        collection.create_item(
            label=f"{KEYRING_APP}:{service}",
            attributes={"application": KEYRING_APP, "service": service},
            secret=key.encode(),
            replace=True,
        )
    except Exception:
        # Keyring unavailable caller should fall back to config.json
        raise RuntimeError(f"Keyring unavailable: cannot store key for {service!r}")


def get_api_key(service: str) -> str | None:
    """Retrieve an API key from the Linux keyring.

    Args:
        service: Service name, e.g. 'openai', 'anthropic'.

    Returns:
        API key string, or None if not found.
    """
    try:
        import secretstorage  # type: ignore

        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        items = list(collection.search_items(
            {"application": KEYRING_APP, "service": service}
        ))
        if not items:
            return None
        return items[0].get_secret().decode()
    except Exception:
        return None
