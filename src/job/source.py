"""Read Flighty's private schema without modifying its database or app state."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class SourceError(RuntimeError):
    pass


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
        "scheduledarrivalAirportId",
        "departureScheduleGateOriginal",
        "isCancelled",
        "deleted",
    },
    "Airline": {"id", "iata", "icao", "name"},
    "Airport": {"id", "iata", "timezoneIdentifier"},
    "Ticket": {"accountId", "flightId", "userId", "deleted", "seatNumber", "pnr"},
}


def stamp(value):
    return (
        datetime.fromtimestamp(value, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
        if value is not None
        else None
    )


def read_flights(path: Path) -> list[dict]:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SourceError("Flighty source is missing; open Flighty and finish native sync first.")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN")
            for table, required in REQUIRED.items():
                actual = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if not required <= actual:
                    raise SourceError(
                        f"Unsupported Flighty schema: {table} missing {sorted(required - actual)}"
                    )
            users = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM UserFlight WHERE deleted IS NULL AND isMyFlight=1 AND isRandom=0 AND importSource IS NOT 'CONNECTED_FRIEND'"
                )
            ]
            grouped = {}
            for row in users:
                grouped.setdefault(row["flightId"], []).append(row)
            result = []

            def one(table, identifier):
                row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
                if row is None:
                    raise SourceError(f"Incomplete Flighty reference in {table}")
                return dict(row)

            for identifier, memberships in sorted(grouped.items()):
                owners = {(r["userId"], r["accountId"]) for r in memberships}
                if len(owners) != 1:
                    raise SourceError("Ambiguous ownership in personal flight list")
                f = one("Flight", identifier)
                if f["deleted"] is not None:
                    continue
                airline = one("Airline", f["airlineId"])
                dep = one("Airport", f["departureAirportId"])
                arr = one("Airport", f["scheduledarrivalAirportId"])
                tickets = [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM Ticket WHERE flightId=? AND userId=? AND accountId=? AND deleted IS NULL",
                        (identifier, *next(iter(owners))),
                    )
                ]
                ticket = tickets[0] if len(tickets) == 1 else {}
                original = f["departureScheduleGateOriginal"]
                if not isinstance(original, (int, float)) or original <= 0:
                    raise SourceError("Missing or invalid original departure timestamp")
                if f["isCancelled"] not in (0, 1):
                    raise SourceError("Unknown cancellation state")
                payload = {
                    "flight": f,
                    "user_flights": sorted(
                        memberships, key=lambda r: json.dumps(r, sort_keys=True)
                    ),
                    "tickets": sorted(tickets, key=lambda r: json.dumps(r, sort_keys=True)),
                    "airline": airline,
                    "departure_airport": dep,
                    "arrival_airport": arr,
                }
                result.append(
                    {
                        "id": identifier,
                        "source_id": identifier,
                        "flight_number": str(f["number"]),
                        "airline_iata": airline["iata"],
                        "airline_icao": airline["icao"],
                        "airline_name": airline["name"],
                        "departure_airport": dep["iata"],
                        "arrival_airport": arr["iata"],
                        "departure_date": datetime.fromtimestamp(
                            original, ZoneInfo(dep["timezoneIdentifier"])
                        )
                        .date()
                        .isoformat(),
                        "departure_timezone": dep["timezoneIdentifier"],
                        "arrival_timezone": arr["timezoneIdentifier"],
                        "departure_scheduled_at": stamp(original),
                        "arrival_scheduled_at": stamp(f.get("arrivalScheduleGateOriginal")),
                        "departure_actual_at": stamp(f.get("departureScheduleGateActual")),
                        "arrival_actual_at": stamp(f.get("arrivalScheduleGateActual")),
                        "cancelled": int(f["isCancelled"]),
                        "seat": ticket.get("seatNumber"),
                        "booking_reference": ticket.get("pnr"),
                        "source_warning": "Multiple own tickets; see retained source."
                        if len(tickets) > 1
                        else None,
                        "source_payload": json.dumps(
                            payload, sort_keys=True, separators=(",", ":")
                        ),
                        "source_state": "present",
                        "travel_status": "unverified",
                    }
                )
            return result
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"Cannot read a complete Flighty snapshot: {type(exc).__name__}") from exc
