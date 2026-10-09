"""Read Flighty's private schema without modifying its database or app state."""

import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class SourceError(RuntimeError):
    pass


class Snapshot(list):
    """Source rows; complete only when every personal flight table was read."""

    def __init__(self, rows, complete):
        super().__init__(rows)
        self.complete = complete


class SourceRow(dict):
    """Honor SQLite column-name casing while preserving original source keys."""

    def __getitem__(self, key):
        for actual in self:
            if actual.casefold() == key.casefold():
                return super().__getitem__(actual)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def encode_binary(value):
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    raise TypeError("Unsupported source value")


REQUIRED = {
    "UserFlight": {
        "accountId",
        "userId",
        "flightId",
        "deleted",
        "isMyFlight",
        "isRandom",
        "importSource",
    },
    "Flight": {
        "id",
        "number",
        "airlineId",
        "departureAirportId",
        "scheduledArrivalAirportId",
        "departureScheduleGateOriginal",
        "isCancelled",
        "deleted",
    },
    "Airline": {"id", "iata", "icao", "name"},
    "Airport": {"id", "iata", "timezoneIdentifier"},
    "Ticket": {"accountId", "flightId", "userId", "deleted", "seatNumber", "pnr"},
}

MANUAL_REQUIRED = {
    "ManualFlight": REQUIRED["Flight"] | {"accountId", "lastKnownDepartureDate"},
    "UserManualFlight": REQUIRED["UserFlight"] - {"isRandom"},
}


def stamp(value):
    return (
        datetime.fromtimestamp(value, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
        if value is not None
        else None
    )


def read_flights(path: Path) -> Snapshot:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SourceError("Flighty source is missing; open Flighty and finish native sync first.")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            manual = bool(tables & MANUAL_REQUIRED.keys())
            required_tables = REQUIRED | (MANUAL_REQUIRED if manual else {})
            for table, required in required_tables.items():
                actual = {r["name"].casefold() for r in c.execute(f"PRAGMA table_info({table})")}
                missing = {name for name in required if name.casefold() not in actual}
                if missing:
                    raise SourceError(
                        f"Unsupported Flighty schema: {table} missing {sorted(missing)}"
                    )
            users = [
                SourceRow(r)
                for r in c.execute(
                    "SELECT * FROM UserFlight WHERE deleted IS NULL AND isMyFlight=1 AND isRandom=0 AND importSource IS NOT 'CONNECTED_FRIEND'"
                )
            ]
            grouped = {}
            for row in users:
                grouped.setdefault(("Flight", row["flightId"]), []).append(row)
            if manual:
                for raw in c.execute(
                    "SELECT * FROM UserManualFlight WHERE deleted IS NULL AND isMyFlight=1 AND importSource IS NOT 'CONNECTED_FRIEND'"
                ):
                    row = SourceRow(raw)
                    grouped.setdefault(("ManualFlight", row["flightId"]), []).append(row)
            result = []

            def one(table, identifier):
                row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
                if row is None:
                    raise SourceError(f"Incomplete Flighty reference in {table}")
                return SourceRow(row)

            for (table, identifier), memberships in sorted(grouped.items()):
                owners = {(r["userId"], r["accountId"]) for r in memberships}
                if len(owners) != 1:
                    raise SourceError("Ambiguous ownership in personal flight list")
                f = one(table, identifier)
                if table == "ManualFlight" and f["accountId"] != next(iter(owners))[1]:
                    raise SourceError("Incomplete Flighty owner reference in ManualFlight")
                if any(r["id"] == identifier for r in result):
                    raise SourceError("Ambiguous source identity across Flighty tables")
                if f["deleted"] is not None:
                    continue
                airline = one("Airline", f["airlineId"]) if f["airlineId"] is not None else {}
                if table == "Flight" and not airline:
                    raise SourceError("Incomplete Flighty reference in Airline")
                dep = one("Airport", f["departureAirportId"])
                arr = one("Airport", f["scheduledArrivalAirportId"])
                tickets = [
                    SourceRow(r)
                    for r in c.execute(
                        "SELECT * FROM Ticket WHERE flightId=? AND userId=? AND accountId=? AND deleted IS NULL",
                        (identifier, *next(iter(owners))),
                    )
                ]
                ticket = tickets[0] if len(tickets) == 1 else {}
                original = f["departureScheduleGateOriginal"]
                if table == "ManualFlight" and original is None:
                    original = f["lastKnownDepartureDate"]
                if not isinstance(original, (int, float)) or original <= 0:
                    raise SourceError("Missing or invalid original departure timestamp")
                if f["isCancelled"] not in (0, 1):
                    raise SourceError("Unknown cancellation state")
                local_departure = datetime.fromtimestamp(
                    original, ZoneInfo(dep["timezoneIdentifier"])
                )
                date_only = (
                    table == "ManualFlight"
                    and local_departure.hour
                    == local_departure.minute
                    == local_departure.second
                    == 0
                    and f.get("arrivalScheduleGateOriginal") is None
                    and f.get("departureScheduleGateActual") is None
                )
                warnings = []
                if table == "ManualFlight":
                    warnings.append("Manual Flighty record; unknown details remain unset.")
                if date_only:
                    warnings.append(
                        "Midnight date marker retained in source; departure time unknown."
                    )
                if len(tickets) > 1:
                    warnings.append("Multiple own tickets; see retained source.")
                payload = {
                    "source_table": table,
                    "flight": f,
                    "user_flights": sorted(
                        memberships,
                        key=lambda r: json.dumps(r, sort_keys=True, default=encode_binary),
                    ),
                    "tickets": sorted(
                        tickets, key=lambda r: json.dumps(r, sort_keys=True, default=encode_binary)
                    ),
                    "airline": airline,
                    "departure_airport": dep,
                    "arrival_airport": arr,
                }
                result.append(
                    {
                        "id": identifier,
                        "source_id": identifier,
                        "flight_number": str(f["number"]) if f["number"] is not None else None,
                        "airline_iata": airline.get("iata"),
                        "airline_icao": airline.get("icao"),
                        "airline_name": airline.get("name"),
                        "departure_airport": dep["iata"],
                        "arrival_airport": arr["iata"],
                        "departure_date": local_departure.date().isoformat(),
                        "departure_timezone": dep["timezoneIdentifier"],
                        "arrival_timezone": arr["timezoneIdentifier"],
                        "departure_scheduled_at": None if date_only else stamp(original),
                        "arrival_scheduled_at": stamp(f.get("arrivalScheduleGateOriginal")),
                        "departure_actual_at": stamp(f.get("departureScheduleGateActual")),
                        "arrival_actual_at": stamp(f.get("arrivalScheduleGateActual")),
                        "cancelled": int(f["isCancelled"]),
                        "seat": ticket.get("seatNumber"),
                        "booking_reference": ticket.get("pnr"),
                        "source_warning": " ".join(warnings) or None,
                        "source_payload": json.dumps(
                            payload, sort_keys=True, separators=(",", ":"), default=encode_binary
                        ),
                        "source_state": "present",
                        "travel_status": "unverified",
                    }
                )
            # Without the manual tables, manual removals are indistinguishable from drift.
            return Snapshot(result, complete=manual)
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"Cannot read a complete Flighty snapshot: {type(exc).__name__}") from exc
