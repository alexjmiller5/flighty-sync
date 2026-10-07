"""Installed user interface for source checks, enrollment and one-way sync."""

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path

from job.config import Settings, load_settings, save_json, state_dir
from job.credentials import account, read_token, store_token
from job.source import read_flights
from job.sync import Hub, SyncError, sync_flights, utc_now


def required_scopes(settings):
    return {
        f"tables:read:{settings.table}",
        f"tables:write:{settings.table}",
        f"files:read:{settings.archive_prefix}",
        f"files:write:{settings.archive_prefix}",
    }


def check_session(hub, settings):
    session = hub.session()
    if set(session.get("scopes", [])) != required_scopes(settings):
        raise SyncError(
            "Credential must have exactly the four table/file grants printed by scopes; broad or unrelated credentials are refused."
        )
    capabilities = session.get("capabilities", {})
    if (
        capabilities.get("conditional_patch") != "revision-v1"
        or capabilities.get("files") != "opaque-key-v1"
    ):
        raise SyncError(
            "The service does not advertise the required file and conditional-patch APIs."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only Flighty source mirror with independent retained backups"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    conf = commands.add_parser("configure")
    conf.add_argument("--hub-url", required=True)
    conf.add_argument("--source", type=Path)
    conf.add_argument("--table", default="flights")
    conf.add_argument("--archive-prefix", default="raw/flighty/")
    commands.add_parser(
        "inspect", help="Read local source only; no credentials or destination writes"
    )
    verify = commands.add_parser("verify-export")
    verify.add_argument("csv", type=Path)
    commands.add_parser("scopes", help="Print the exact grants required by this consumer")
    login = commands.add_parser("login")
    login.add_argument("--token-stdin", action="store_true", required=True)
    commands.add_parser(
        "doctor", help="Check source baseline and destination credential without writes"
    )
    commands.add_parser("run", help="Run one complete snapshot, archive and mirror cycle")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        state = state_dir()
        if args.command == "configure":
            settings = Settings(
                hub_url=args.hub_url,
                source=args.source or settings.source,
                table=args.table,
                archive_prefix=args.archive_prefix,
            )
            hub = Hub(settings.hub_url, "")
            hub.close()
            save_json(state / "config.json", settings.model_dump(mode="json"))
            print(json.dumps({"configured": True, "state_dir": str(state)}))
            return 0
        if args.command == "scopes":
            print(",".join(sorted(required_scopes(settings))))
            return 0
        if args.command == "status":
            print(
                (state / "status.json").read_text()
                if (state / "status.json").exists()
                else json.dumps({"state": "not_run"})
            )
            return 0
        if args.command == "login":
            token = sys.stdin.read().strip()
            if not token:
                raise ValueError("Expected token on stdin")
            hub = Hub(settings.hub_url, token)
            try:
                check_session(hub, settings)
            finally:
                hub.close()
            store_token(account(settings.hub_url), token)
            print(json.dumps({"enrolled": True}))
            return 0
        rows = read_flights(settings.source)
        if not rows:
            raise SyncError("Source is empty; complete Flighty enrollment and hydration.")
        if args.command == "inspect":
            print(
                json.dumps(
                    {
                        "source_rows": len(rows),
                        "source_warnings": sum(bool(r["source_warning"]) for r in rows),
                        "observed_at": utc_now(),
                        "freshness": "local_cache_only",
                    }
                )
            )
            return 0
        if args.command == "verify-export":
            exported = list(csv.DictReader(args.csv.open(encoding="utf-8-sig")))
            identifiers = [r.get("Flight Flighty ID") for r in exported]
            source_ids = {r["id"] for r in rows}
            if not all(identifiers) or set(identifiers) != source_ids:
                raise SyncError(
                    "Export IDs do not match the mini source; finish Flighty sync and retry with a fresh export."
                )
            save_json(
                state / "verification.json",
                {
                    "verified_at": utc_now(),
                    "hub_url": settings.hub_url,
                    "source": str(settings.source),
                    "source_rows": len(rows),
                    "export_sha256": hashlib.sha256(args.csv.read_bytes()).hexdigest(),
                },
            )
            print(json.dumps({"verified_rows": len(rows)}))
            return 0
        proof = state / "verification.json"
        if not proof.exists():
            raise SyncError("Run verify-export with a fresh official Flighty CSV before syncing.")
        baseline = json.loads(proof.read_text())
        if baseline["source"] != str(settings.source) or baseline["hub_url"] != settings.hub_url:
            raise SyncError("Source or destination changed; repeat verify-export.")
        if len(rows) < baseline["source_rows"] * (1 - settings.max_missing_fraction):
            raise SyncError(
                "Large source shrink; verify a fresh official export before proceeding."
            )
        # Explicit caller overrides are allowed, but the grant check never accepts operator authority.
        token = os.environ.get("FLIGHTY_SYNC_TOKEN") or read_token(account(settings.hub_url))
        hub = Hub(settings.hub_url, token)
        try:
            check_session(hub, settings)
            if args.command == "doctor":
                print(
                    json.dumps(
                        {"ready": True, "source_rows": len(rows), "freshness": "local_cache_only"}
                    )
                )
                return 0
            result = sync_flights(hub, rows, table=settings.table, prefix=settings.archive_prefix)
        finally:
            hub.close()
        result.update(state="success", freshness="local_cache_only")
        save_json(state / "status.json", result)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # Neither raw source payloads nor credentials are logged on failure.
        message = (
            str(exc)
            if isinstance(exc, (SyncError, RuntimeError, ValueError))
            else type(exc).__name__
        )
        print(message, file=sys.stderr)
        if args.command == "run":
            save_json(
                state_dir() / "status.json", {"state": "failed", "at": utc_now(), "error": message}
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
