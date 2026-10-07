"""Life Data public file/row APIs; no replica or infrastructure access."""

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx


class SyncError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Hub:
    def __init__(self, url, token, *, transport=None):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTPS service URL without credentials, query or fragment.")
        self.client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": "Bearer " + token, "User-Agent": "flighty-sync/0.1.0"},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def post(self, path, data):
        response = self.client.post(path, json=data)
        response.raise_for_status()
        return response.json()

    def session(self):
        response = self.client.get("/v1/session")
        response.raise_for_status()
        return response.json()

    def retain(self, key, data, content_type="application/json"):
        path = "/v1/files/" + "/".join(quote(p, safe="") for p in key.split("/"))
        response = self.client.get(path)
        if response.status_code == 404:
            response = self.client.put(path, content=data, headers={"Content-Type": content_type})
            response.raise_for_status()
            response = self.client.get(path)
        response.raise_for_status()
        if response.content != data:
            raise SyncError("Retained file readback did not match; no row writes allowed.")

    def pull(self, table, columns):
        body = {"table": table, "columns": columns, "limit": 200}
        rows = {}
        while True:
            page = self.post("/v1/rows/pull", body)
            for row in page["rows"]:
                if not isinstance(row.get("id"), str) or row["id"] in rows:
                    raise SyncError("Invalid or duplicate destination row identity")
                rows[row["id"]] = row
            cursor = page.get("next_cursor")
            if cursor is None:
                return rows
            if not isinstance(cursor, str) or cursor <= body.get("after", "") or not page["rows"]:
                raise SyncError("Incomplete destination pagination")
            body["after"] = cursor

    def patch(self, table, existing, values):
        result = self.post(
            "/v1/rows/patch",
            {
                "table": table,
                "id": existing["id"],
                "values": values,
                "expected_revision": {
                    "updated_at": existing["updated_at"],
                    "hub_at": existing.get("hub_at"),
                },
            },
        )
        if result.get("id") != existing["id"] or not isinstance(result.get("revision"), dict):
            raise SyncError("Invalid patch receipt")


def sync_flights(hub, rows, *, table, prefix):
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise SyncError("Empty or duplicate source snapshot; refusing reconciliation.")
    if not prefix.endswith("/") or any(p in ("", ".", "..") for p in prefix.rstrip("/").split("/")):
        raise ValueError("Archive prefix must be a safe slash-terminated namespace.")
    observed = utc_now()
    payload = json.dumps(
        {"format": "flighty-source-v1", "flights": sorted(rows, key=lambda r: r["id"])},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    key = f"{prefix}{observed[:10]}/{digest}.json"
    # Raw bytes must be durable and verified before touching the materialized view.
    hub.retain(key, payload)
    fields = set().union(*(r.keys() for r in rows))
    columns = sorted(fields | {"updated_at", "hub_at", "deleted_at", "source_archive_key"})
    existing = hub.pull(table, columns)
    counts = {
        "source_rows": len(rows),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "tombstones": 0,
        "missing": 0,
        "archive_key": key,
        "observed_at": observed,
    }
    expected = {}
    for row in rows:
        old = existing.get(row["id"])
        if old and old.get("deleted_at"):
            counts["tombstones"] += 1
            continue
        if old is None:
            new = {**row, "source_archive_key": key, "updated_at": observed}
            receipt = hub.post(
                "/v1/rows/insert", {"table": table, "columns": sorted(new), "rows": [new]}
            )
            if (
                receipt.get("rejected")
                or receipt.get("inserted") != [row["id"]]
                or receipt.get("existing")
            ):
                raise SyncError("Creation rejected or concurrently created; rerun to reconcile.")
            counts["inserted"] += 1
            expected[row["id"]] = row
            continue
        if old.get("source_id") != row["source_id"]:
            raise SyncError("Existing row has a conflicting source identity.")
        # This is a user-owned assessment, initialized only when creating a row.
        changes = {
            k: v
            for k, v in row.items()
            if k not in ("id", "source_id", "travel_status") and old.get(k) != v
        }
        if changes:
            changes["source_archive_key"] = key
            hub.patch(table, old, changes)
            counts["updated"] += 1
            expected[row["id"]] = changes
        else:
            counts["unchanged"] += 1
    seen = {r["id"] for r in rows}
    for identifier, old in existing.items():
        if (
            identifier not in seen
            and not old.get("deleted_at")
            and old.get("source_state") != "missing"
        ):
            values = {"source_state": "missing", "source_archive_key": key}
            hub.patch(table, old, values)
            counts["missing"] += 1
            expected[identifier] = values
    after = hub.pull(table, columns)
    for identifier, values in expected.items():
        if identifier not in after or any(after[identifier].get(k) != v for k, v in values.items()):
            raise SyncError("Destination readback mismatch; rerun required.")
    return counts
