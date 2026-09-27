"""Linux keyring integration via secretstorage.

Stores API keys and, since Phase 3 (F6), the secret broker's named secrets.
Every item is keyed by attributes {application, service}; a broker secret is
service "secret:<name>".

Two read paths on purpose. `get_api_key` is forgiving and returns None on any
failure, because an API key falls back to config.json. `lookup` raises
`KeyringUnavailable`, because the broker must tell "no keyring" from "no such
secret" and refuse on the first rather than run with nothing.
"""
from __future__ import annotations

KEYRING_APP = "agentic-shell"

# secretstorage raises its own SecretStorageException family, which is not an
# OSError or RuntimeError. With no D-Bus session (a normal SSH login on a
# headless server) it raises SecretServiceNotAvailableException, and before
# this was caught `/secret add` took the whole login shell down. Found by the
# Phase 3 gate run, not by any unit test.
try:
    from secretstorage.exceptions import SecretStorageException as _SecretStorageError  # type: ignore
except ImportError:
    class _SecretStorageError(Exception):  # type: ignore[no-redef]
        """Stand-in so the tuple below is valid without secretstorage."""

_ERRORS = (ImportError, OSError, RuntimeError, AttributeError, _SecretStorageError)


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
    except _ERRORS as exc:
        # No secretstorage, no D-Bus session, or a locked collection that will
        # not unlock. The caller falls back to config.json.
        raise RuntimeError(
            f"Keyring unavailable: cannot store key for {service!r}"
        ) from exc


class KeyringUnavailable(RuntimeError):
    """secretstorage or the D-Bus session it needs is not there."""



def _collection():
    try:
        import secretstorage  # type: ignore

        collection = secretstorage.get_default_collection(secretstorage.dbus_init())
        if collection.is_locked():
            collection.unlock()
        return collection
    except _ERRORS as exc:
        raise KeyringUnavailable(str(exc) or type(exc).__name__) from exc


def lookup(service: str) -> str | None:
    """Value for `service`, None if absent. Raises KeyringUnavailable."""
    try:
        items = list(_collection().search_items(
            {"application": KEYRING_APP, "service": service}
        ))
        return items[0].get_secret().decode() if items else None
    except (*_ERRORS, UnicodeDecodeError) as exc:
        if isinstance(exc, KeyringUnavailable):
            raise
        raise KeyringUnavailable(str(exc) or type(exc).__name__) from exc


def delete(service: str) -> bool:
    """Remove `service`. True if something was removed. Raises KeyringUnavailable."""
    try:
        items = list(_collection().search_items(
            {"application": KEYRING_APP, "service": service}
        ))
        for item in items:
            item.delete()
        return bool(items)
    except KeyringUnavailable:
        raise
    except _ERRORS as exc:
        raise KeyringUnavailable(str(exc) or type(exc).__name__) from exc


def services(prefix: str = "") -> list[str]:
    """Sorted service names starting with `prefix`. Raises KeyringUnavailable."""
    try:
        items = _collection().search_items({"application": KEYRING_APP})
        names = {i.get_attributes().get("service", "") for i in items}
    except KeyringUnavailable:
        raise
    except _ERRORS as exc:
        raise KeyringUnavailable(str(exc) or type(exc).__name__) from exc
    return sorted(n for n in names if n.startswith(prefix))


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
    except (*_ERRORS, UnicodeDecodeError):
        return None
