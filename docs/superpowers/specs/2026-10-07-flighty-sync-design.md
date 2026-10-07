# Flighty Sync

An opt-in one-way mirror from the installed Flighty macOS application to a user-configured Life Data service. Flighty remains authoritative for imported fields; no source writes occur. The consumer owns no Life Data infrastructure.

Read the local SQLite database in read-only/query-only mode inside one transaction. Require the known source schema and select live, non-random personal flights excluding connected friends. Match tickets to the same user; ambiguous tickets never duplicate flight rows. Preserve missing airline codes, cancellation status and local-date/time-zone semantics. Never turn flight tracking or actual aircraft times into a claim the user boarded. Retain the complete selected source payload with source identifiers.

Before table writes, retain and verify a content-addressed dated JSON snapshot through the Life Data file API. Pull all existing mirror rows, insert new source IDs through the insert-only API, and revision-patch changed source fields. Never overwrite destination-owned trip or transaction references, resurrect tombstones, or delete records missing from a read. Reconcile disappearance only as an explicit source state after a complete nonempty read. Any schema/read/archive/auth/revision failure fails the run visibly; repeat runs converge.

Credentials are independently provisioned for this consumer with narrowly scoped table and archive access, stored in the user's native Keychain through the app's enrollment command. No provider tokens, machine credentials, plaintext token files or broad operator fallback. Table definitions are operator-managed state through the Life Data catalog, not a migration in the generic Life Data product.

Install the packaged CLI and signed launchd wrapper through Nix. Run nightly only after configured source and destination access pass checks. The app reports local observation time separately from source modification time; neither is proof of current iCloud hydration. First-run Flighty/iCloud enrollment and an official export comparison are manual documented checks. Missing prerequisites leave a clear nonzero status rather than a successful empty sync.

No analytics. All tests use synthetic records; personal snapshots never enter source control.
