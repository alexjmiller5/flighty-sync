# Flighty Sync

Scheduled Python jobs for residential networking, Apple data or local hardware.
Cloud-safe jobs belong in the cloud-service template.

## Runtime and ownership

The Nix package contains the locked application and dependencies. The app's module
owns launchd setup, installed paths and runtime behavior. Deploy by pushing the
project, updating its input in the consumer's machine configuration, then rebuilding
and verifying the job. Never run a daemon from a working tree.

Consumer credentials are independently enrolled and revocable. Runtime accepts
caller-prepared environment variables; the module's optional credentialCommands
maps variable names to commands that return their values. A failed command stops
the job. There is no required provider CLI, service-account token or machine-vault
fallback. Never put credential values in Nix settings or source control.

The signed app wrapper supports jobs requiring TCC-protected Apple data. Its stable
identity retains an explicit Full Disk Access grant across updates. Document that
grant and any native account enrollment in the consuming project's README. A job
that only needs networking can use a plain packaged home-manager service.

State and logs belong in the configured standard application-support directory,
exported as JOB_STATE_DIR. No current-directory-relative state.

## Development

uv, pydantic-settings, httpx, structlog, pytest and Ruff. Construct settings inside
main, never on import. Use just run, test, check, fmt and logs. Write behavior tests
before implementation. The credential runner has a Nix integration check in
tests/runner.nix; it executes credential-free, successful and failed-command cases.


## Data contract

The source database is private Flighty state and is read-only. Never migrate or
write its tables. The archive and flights table belong to the user-configured
Soma service, accessed through its file and row APIs with exact grants.
This project owns its package, launchd wrapper, native Keychain credential and
local status/configuration. It does not own Soma infrastructure or schema.

Do not log source payloads or tokens. Fixtures are synthetic. Preserve human-owned
trip, purchase and travel-status fields. Archive readback precedes row writes;
creation is insert-only and field edits use revisions. Flighty is the source of
truth: a flight absent from a complete read is soft-deleted, one that reappears is
restored, both through sparse pushes checked by readback, never a hard delete.
An incomplete read (no manual tables) or a shrink over max_missing_fraction of live
rows deletes nothing. Local observation does not prove iCloud freshness.

Public repository, no analytics. Version 0.1.0 is pre-release.

Read both Flight/UserFlight and ManualFlight/UserManualFlight when available.
Manual ownership must match accountId, and missing manual carrier/number/time
values stay null. Date-only midnight markers retain their raw timestamp but do
not become a claimed scheduled departure time.

A run requests a native background launch of Flighty before reading its local
cache, so CloudKit can resume after reboot. Inspect and doctor do not launch
apps. Hydration completion is not inferred from launch or a populated cache.
