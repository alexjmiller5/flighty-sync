{ pkgs ? import <nixpkgs> { } }:
let
  makeRunner = import ../nix/runner.nix;
  job = pkgs.writeShellScriptBin "flighty-sync" ''
    test "''${CONSUMER_TOKEN-}" = "$EXPECTED_TOKEN"
    test "$1" = "run"
    test -d "$JOB_STATE_DIR"
  '';
  runner = commands: makeRunner {
    inherit pkgs;
    lib = pkgs.lib;
    venv = job;
    cfg = { stateDir = "/tmp/flighty-sync-runner-fixture"; credentialCommands = commands; };
  };
in
pkgs.runCommand "flighty-sync-credential-tests" { } ''
  export EXPECTED_TOKEN=""
  ${runner { }} run
  export EXPECTED_TOKEN="fixture with spaces"
  ${runner { CONSUMER_TOKEN = [ "${pkgs.coreutils}/bin/printf" "%s" "fixture with spaces" ]; }} run
  if ${runner { CONSUMER_TOKEN = [ "${pkgs.coreutils}/bin/false" ]; }} run; then
    echo "Failed credential command must prevent job execution" >&2
    exit 1
  fi
  rmdir /tmp/flighty-sync-runner-fixture
  touch $out
''
