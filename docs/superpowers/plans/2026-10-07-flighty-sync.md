# Flighty Sync implementation plan

1. Declare Flighty installation and native enrollment in nix-config, proof-build and switch the mini.
2. Test the read-only source adapter using synthetic SQLite fixtures: personal/friend/deleted filtering, per-user tickets, duplicate protection, null airline codes, cancellations and timezone dates. Implement the adapter and inspect the installed source without mutations.
3. Test the HTTP mirror with a fake service: verified archive precedes writes, full pagination, insert-only creation, revision-checked patches, user reference preservation, failed archives/rejections/tombstones and idempotency. Implement the consumer.
4. Add user configuration, native Keychain token import/status, source/export verification and dry-run/run commands. Package the CLI and signed nightly wrapper with configuration owned by the product. Test and mutation-check the important safety branches.
5. Register the consumer project and configure the cataloged flights table and optional references through supported Life Data operations. Resolve enrollment through supported interfaces; document human-only steps rather than substituting infrastructure credentials.
6. Publish the public repository, wire its pinned Nix input, render machine manuals, proof-build and switch. Verify installed commands, source coverage, byte-verified archives, destination readback and scheduled execution. Leave unfinished login/access prerequisites explicitly pending.
