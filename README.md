# Flighty Sync

A read-only mirror of your local Flighty flights into your own Soma service,
with independently retained, byte-verified source snapshots. No analytics.

Flighty Sync uses Flighty's private macOS SQLite schema. It is not affiliated
with Flighty, and a Flighty update can require an adapter update. It never writes
to the Flighty database. A local cache read does not prove current iCloud sync.

## Install with Nix

Add this flake as an input, following your `nixpkgs`, and import
`inputs.flighty-sync.darwinModules.default`. Enable `services.flighty-sync` with
`enable = true; user = <login user>;`. It installs `flighty-sync`, a signed
`FlightySync.app`, and a nightly user LaunchAgent at 03:30 local time. `hour`,
`minute`, `stateDir`, `appInstallPath`, and `credentialCommands` are configurable.
The package runs from the Nix store, never a working checkout.

Declare Flighty separately through nix-darwin `homebrew.masApps` using its App
Store ID `1358823008`. App Store administrator authentication, native iCloud
sign-in and Full Disk Access grants cannot be supplied by Nix.

## First run and replacement machines

1. Open Flighty in the Mac's logged-in desktop. Finish its native onboarding and
   iCloud sync using your existing library. Confirm it matches a fresh official
   Flighty CSV export. If source access is denied, grant `FlightySync.app` Full
   Disk Access in System Settings and retry from its actual launch context.
2. Configure this consumer through its installed interface:

   ```sh
   flighty-sync configure --hub-url https://your-life-service.example
   flighty-sync inspect
   flighty-sync verify-export /path/to/FlightyExport.csv
   flighty-sync scopes
   ```

3. Have the Soma operator provision the destination catalog and mint a
   dedicated consumer token with **exactly** the grants printed by `scopes`.
   Current defaults are `tables:read:flights`, `tables:write:flights`, and
   `files:read:raw/flighty/`, `files:write:raw/flighty/`. Configure table/file
   names using `configure` if your installation differs. Do not give this app
   an administrator, full-replica, provider or another consumer's token.
4. Pipe that dedicated token to `flighty-sync login --token-stdin` from the
   logged-in desktop. The app verifies the scopes and service capabilities and
   stores the token in native Keychain, never configuration or a plaintext file.
   SSH sessions cannot necessarily access the login Keychain. The scheduled
   user agent must pass its own `doctor`/run check; shell success is insufficient.
5. Run `flighty-sync doctor`, then `flighty-sync run`, and verify `status` and
   destination records. Test another source change made through Flighty's UI
   and check that the mini receives it before relying on unattended freshness.

Repeat native enrollment after replacing a Mac or resetting its Keychain. The
source/export verification is bound to the configured source path and service
URL. Changing either requires another export comparison. A source shrink over
25% stops for review. Empty sources are always rejected.

## Operation

- `inspect`: source-only summary; no destination access or writes.
- `verify-export <csv>`: compare stable Flighty IDs against an official export.
- `doctor`: verify source baseline, narrowly scoped credential and API support.
- `run`: read one coherent snapshot, retain and read it back, then reconcile
  the destination and verify row readback. Repeat runs do not duplicate flights.
- `status`: last run outcome and local observation time.

Raw snapshots are content-addressed beneath dated archive keys. The `flights`
projection includes source IDs, carrier/route/time/seat details, cancellation,
source payload and archive reference. Source fields refresh; user-maintained
`travel_status`, trip references and purchase links do not. Initial
`travel_status=unverified` means presence in Flighty does not establish boarding.
Missing source rows are retained with `source_state=missing`; tombstones are
never resurrected. Multiple same-owner tickets are retained in the raw payload,
with an explicit warning and no guessed seat. Friends' tickets are excluded.
Both searched flights and manual flight-log entries are included. Manual entries
retain unknown carrier, number and times as null. A manual midnight departure
with no arrival or actual departure time is treated as a date marker, with an
explicit warning and its original timestamp retained in the raw snapshot.

Soma operators create/catalog the destination through supported user
interfaces, not by changing the Soma repository. `txns` can be a union
view with composite identity; purchase links must retain both source and source
record ID rather than assuming a universal transaction ID. Keep transaction
links separate so purchases, fees and refunds can all be represented.

The required service APIs are documented by Soma: `/v1/session`, retained
files, complete paginated row pulls, insert-only creation, and revision-checked
patches. Failed archives, rejections, schema drift, missing credentials and
revision conflicts fail visibly. A rerun reconciles partial completed work.

The default settings/status directory is `~/Library/Application Support/FlightySync`.
`JOB_STATE_DIR` can override it. No secret is stored there. An explicit
`FLIGHTY_SYNC_TOKEN` can be supplied by a caller, but the same narrow-grant check
applies. Credentials are never accepted in command-line arguments.

## Development

`just test`, `just check`, `just fmt`, and `nix build`. Tests use synthetic
SQLite sources and an in-memory HTTP service; no personal data belongs in Git.

Each scheduled run opens Flighty in the background using macOS `open -g` before
reading its cache. Finish native Flighty enrollment first. CloudKit hydration is
owned by Flighty and may finish after that snapshot; the next run picks up later
changes. A successful mirror verifies local data and retained storage, not iCloud
freshness. Confirm propagation after a known change on another device.
