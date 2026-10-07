import sqlite3

import pytest

from job.source import SourceError, read_flights


def source(tmp_path):
    path = tmp_path / "source.db"
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE UserFlight(accountId TEXT,userId TEXT,flightId TEXT,deleted REAL,isMyFlight INT,isRandom INT,importSource TEXT);
    CREATE TABLE Flight(id TEXT,number TEXT,airlineId TEXT,departureAirportId TEXT,scheduledArrivalAirportId TEXT,departureScheduleGateOriginal INT,departureScheduleGateActual INT,arrivalScheduleGateOriginal INT,arrivalScheduleGateActual INT,isCancelled INT,deleted REAL,lastUpdated INT);
    CREATE TABLE Airline(id TEXT,iata TEXT,icao TEXT,name TEXT);
    CREATE TABLE Airport(id TEXT,iata TEXT,timeZoneIdentifier TEXT);
    CREATE TABLE Ticket(accountId TEXT,flightId TEXT,userId TEXT,deleted REAL,seatNumber TEXT,pnr TEXT);
    INSERT INTO Airline VALUES ('airline','ZZ',NULL,'Fixture Air');
    INSERT INTO Airport VALUES ('origin','AAA','America/New_York'),('destination','BBB','Europe/London');
    INSERT INTO Flight VALUES ('flight','12','airline','origin','destination',1704155400,NULL,1704180600,NULL,1,NULL,1704000000);
    INSERT INTO UserFlight VALUES ('account','owner','flight',NULL,1,0,NULL);
    INSERT INTO Ticket VALUES ('account','flight','owner',NULL,'1A','fixture'),('account','flight','someone-else',NULL,'2B','other');
    """)
    c.commit()
    c.close()
    return path


def test_read_only_null_codes_cancelled_and_local_date(tmp_path):
    path = source(tmp_path)
    before = path.read_bytes()
    result = read_flights(path)
    assert len(result) == 1
    row = result[0]
    assert row["source_id"] == "flight"
    assert row["airline_icao"] is None
    assert row["cancelled"] == 1
    assert row["departure_date"] == "2024-01-01"
    assert row["seat"] == "1A"
    assert row["travel_status"] == "unverified"
    assert path.read_bytes() == before


def test_exclude_friend_deleted_random_and_nonpersonal(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE UserFlight SET importSource='CONNECTED_FRIEND'")
    assert read_flights(path) == []
    for change in ["deleted=1", "isMyFlight=0", "isRandom=1"]:
        with sqlite3.connect(path) as c:
            c.execute(
                "UPDATE UserFlight SET importSource=NULL,deleted=NULL,isMyFlight=1,isRandom=0"
            )
            c.execute("UPDATE UserFlight SET " + change)
        assert read_flights(path) == []


def test_multiple_own_tickets_are_explicit_not_duplicate_flights(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO Ticket VALUES ('account','flight','owner',NULL,'3C','fixture')")
    rows = read_flights(path)
    assert len(rows) == 1
    assert rows[0]["seat"] is None
    assert "ticket" in rows[0]["source_warning"]


def test_fail_on_missing_schema_and_do_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(SourceError):
        read_flights(path)
    assert not path.exists()
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE unrelated(id TEXT)")
    with pytest.raises(SourceError, match="schema"):
        read_flights(path)


def test_binary_source_fields_are_retained_losslessly(tmp_path):
    import base64
    import json

    path = source(tmp_path)
    binary = bytes([0, 255, 17, 128])
    with sqlite3.connect(path) as c:
        c.execute("ALTER TABLE Flight ADD COLUMN arrivalWeatherWarnings BLOB")
        c.execute("UPDATE Flight SET arrivalWeatherWarnings=?", (binary,))
    payload = json.loads(read_flights(path)[0]["source_payload"])
    retained = payload["flight"]["arrivalWeatherWarnings"]
    assert retained["encoding"] == "base64"
    assert base64.b64decode(retained["data"]) == binary


def manual_source(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE ManualFlight(accountId TEXT,id TEXT,number TEXT,airlineId TEXT,departureAirportId TEXT,scheduledArrivalAirportId TEXT,departureScheduleGateOriginal INT,lastKnownDepartureDate INT,isCancelled INT,deleted REAL);
        CREATE TABLE UserManualFlight(accountId TEXT,userId TEXT,flightId TEXT,deleted REAL,isMyFlight INT,importSource TEXT);
        INSERT INTO ManualFlight VALUES ('account','manual',NULL,NULL,'origin','destination',1704085200,1704085200,0,NULL);
        INSERT INTO UserManualFlight VALUES ('account','owner','manual',NULL,1,'MANUAL');
        """)
    return path


def test_manual_date_only_flight_is_retained_without_invented_details(tmp_path):
    import json

    path = manual_source(tmp_path)
    before = path.read_bytes()
    rows = read_flights(path)
    assert len(rows) == 2
    row = next(r for r in rows if r["source_id"] == "manual")
    assert row["flight_number"] is None
    assert row["airline_name"] is None
    assert row["departure_date"] == "2024-01-01"
    assert row["departure_scheduled_at"] is None
    assert row["arrival_scheduled_at"] is None
    assert row["departure_airport"] == "AAA"
    assert row["arrival_airport"] == "BBB"
    assert "Manual" in row["source_warning"]
    assert json.loads(row["source_payload"])["flight"]["lastKnownDepartureDate"] == 1704085200
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["deleted=1", "isMyFlight=0", "importSource='CONNECTED_FRIEND'"])
def test_manual_friend_deleted_nonpersonal_are_excluded(tmp_path, change):
    path = manual_source(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE UserManualFlight SET " + change)
    assert [r["source_id"] for r in read_flights(path)] == ["flight"]


def test_manual_owner_reference_must_match_account(tmp_path):
    path = manual_source(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE ManualFlight SET accountId='other'")
    with pytest.raises(SourceError, match="reference"):
        read_flights(path)


def test_manual_known_carrier_number_and_time_are_preserved(tmp_path):
    path = manual_source(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute(
            "UPDATE ManualFlight SET number='ZZ 42',airlineId='airline',departureScheduleGateOriginal=1704155400"
        )
    row = next(r for r in read_flights(path) if r["source_id"] == "manual")
    assert row["flight_number"] == "ZZ 42"
    assert row["airline_iata"] == "ZZ"
    assert row["departure_scheduled_at"] == "2024-01-02T00:30:00.000Z"
