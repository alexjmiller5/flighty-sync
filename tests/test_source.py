import sqlite3

import pytest

from job.source import SourceError, read_flights


def source(tmp_path):
    path = tmp_path / "source.db"
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE UserFlight(accountId TEXT,userId TEXT,flightId TEXT,deleted REAL,isMyFlight INT,isRandom INT,importSource TEXT);
    CREATE TABLE Flight(id TEXT,number TEXT,airlineId TEXT,departureAirportId TEXT,scheduledarrivalAirportId TEXT,departureScheduleGateOriginal INT,departureScheduleGateActual INT,arrivalScheduleGateOriginal INT,arrivalScheduleGateActual INT,isCancelled INT,deleted REAL,lastUpdated INT);
    CREATE TABLE Airline(id TEXT,iata TEXT,icao TEXT,name TEXT);
    CREATE TABLE Airport(id TEXT,iata TEXT,timezoneIdentifier TEXT);
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
