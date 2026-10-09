"""Native Keychain storage through the stable Apple security executable."""

import hashlib
import subprocess

SERVICE = "flighty-sync.hub"


def account(hub_url):
    return hashlib.sha256(hub_url.rstrip("/").encode()).hexdigest()


def read_token(key):
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", key, "-w"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(
            "Keychain credential unavailable. Enroll from the mini desktop; see the manual."
        )
    return result.stdout.rstrip("\n")


def store_token(key, token):
    if not token or any(c in token + key for c in '\r\n\x00"\\'):
        raise ValueError("Invalid credential input")
    # The credential travels only through stdin, never process arguments or disk.
    command = (
        f'add-generic-password -U -s "{SERVICE}" -a "{key}" -w "{token}" -T /usr/bin/security\n'
    )
    result = subprocess.run(
        ["/usr/bin/security", "-i"], input=command, capture_output=True, text=True, timeout=30
    )
    if result.returncode or read_token(key) != token:
        raise RuntimeError("Keychain enrollment failed; use the logged-in mini desktop.")


def delete_token(key):
    subprocess.run(
        ["/usr/bin/security", "delete-generic-password", "-s", SERVICE, "-a", key],
        capture_output=True,
        timeout=10,
    )
