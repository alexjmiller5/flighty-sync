{ pkgs, lib, cfg, venv }:
pkgs.writeShellScript "flighty-sync-run" ''
  set -euo pipefail
  export JOB_STATE_DIR=${lib.escapeShellArg cfg.stateDir}
  mkdir -p "$JOB_STATE_DIR"
  ${lib.concatStringsSep "\n" (lib.mapAttrsToList (name: command: ''
    credential_value="$(${lib.escapeShellArgs command})"
    export ${lib.escapeShellArg name}="$credential_value"
    unset credential_value
  '') cfg.credentialCommands)}
  exec ${venv}/bin/flighty-sync "$@"
''
