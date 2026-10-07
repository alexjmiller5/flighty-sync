# nix-darwin module: the job as a packaged launchd USER agent on the mac mini.
# Pattern proven by the notion-finance-sync and screentime-backup deployments.
#
# `darwin-rebuild switch` builds the app from uv.lock (uv2nix), wraps it in a
# signed .app at a stable path, and installs the launchd agent - no repo
# checkout on the mini, no `uv sync`, no config file to place. Deploying a code
# change = push here, `nix flake update <input>` + switch in nix-config.
#
# Why a .app: TCC (Full Disk Access - Messages, Screen Time, etc.) keys grants
# on code identity. Activation maintains a stable self-signed cert (created
# once, idempotent) and re-signs the .app with it every rebuild, so ONE manual
# FDA grant survives every update. The bundle executable must be a real Mach-O:
# a shebang script as CFBundleExecutable runs as /bin/zsh and fails TCC's
# designated-requirement check (grant recorded, access still denied - hit on
# macOS 26). So the executable is a tiny signed stub that execs the runner;
# FDA inherits across the exec.
#
# Runs as a USER agent (login session) so it has the login Keychain, Messages
# DB, Chrome profiles, etc. when the job needs them.
#
# Irreducibly manual, document in the consuming repo's README:
#   - the FDA grant itself (System Settings → Privacy & Security → Full Disk
#     Access → the .app) - TCC is SIP-protected, GUI-only
#   - first interactive login for scraper jobs (device-trust cookies)
#
# Scraper jobs needing real Chrome: copy the `installChrome` homebrew-cask
# option and the Xcode CLT + Rosetta 2 activation preflights from
# notion-finance-sync's nix/darwin.nix.
self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.flighty-sync; 
  venv = self.packages.${pkgs.stdenv.hostPlatform.system}.default;

  runner = import ./runner.nix { inherit pkgs lib cfg venv; };

  # The .app bundle: a tiny Mach-O exec that hands off to the runner. Built
  # unsigned in the store; activation copies it to a stable path and codesigns it.
  appBundle = pkgs.runCommandCC "flighty-sync-app" { } ''
    mkdir -p "$out/Contents/MacOS"
    cat > "$out/Contents/Info.plist" <<'PLIST'
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>CFBundleIdentifier</key><string>${cfg.bundleId}</string>
      <key>CFBundleName</key><string>${cfg.appName}</string>
      <key>CFBundleExecutable</key><string>${cfg.appName}</string>
      <key>CFBundlePackageType</key><string>APPL</string>
      <key>LSBackgroundOnly</key><true/>
    </dict></plist>
    PLIST
    cat > stub.c <<EOF
    #include <unistd.h>
    int main(int argc, char **argv) {
      argv[0] = (char *)"${runner}";
      execv("${runner}", argv);
      return 127;
    }
    EOF
    $CC -O2 -o "$out/Contents/MacOS/${cfg.appName}" stub.c
  '';

  appExe = "${cfg.appInstallPath}/Contents/MacOS/${cfg.appName}";
in
{
  options.services.flighty-sync = {
    enable = lib.mkEnableOption "the Flighty Sync scheduled job";

    user = lib.mkOption {
      type = lib.types.str;
      description = "Login user the job runs as.";
      example = "local-user";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/Users/${cfg.user}/Library/Application Support/FlightySync"; 
      defaultText = lib.literalExpression ''"/Users/''${user}/Library/Application Support/FlightySync"'';
      description = "Writable dir for state, logs, sessions - exported to the job as JOB_STATE_DIR.";
    };

    hour = lib.mkOption {
      type = lib.types.ints.between 0 23;
      default = 3;
      description = "Hour (0-23, local time) the job fires.";
    };

    minute = lib.mkOption {
      type = lib.types.ints.between 0 59;
      default = 30;
      description = "Minute the job fires.";
    };

    credentialCommands = lib.mkOption {
      type = lib.types.attrsOf (lib.types.listOf lib.types.str);
      default = { };
      description = "Environment variable names mapped to commands returning caller credentials. Commands must exit successfully. No provider or vault knowledge belongs in the job.";
    };

    bundleId = lib.mkOption {
      type = lib.types.str;
      default = "org.flighty-sync"; 
      description = "CFBundleIdentifier of the generated .app.";
    };

    appName = lib.mkOption {
      type = lib.types.str;
      default = "FlightySync"; 
      description = "Name of the generated .app and its executable.";
    };

    appInstallPath = lib.mkOption {
      type = lib.types.str;
      default = "/Applications/FlightySync.app"; 
      description = "Stable path the signed .app is installed to (the thing you grant Full Disk Access).";
    };

    signingIdentity = lib.mkOption {
      type = lib.types.str;
      default = "flighty-sync-signing"; 
      description = ''
        Common name of the self-signed code-signing cert (System keychain, so root
        can sign at activation). Created automatically at activation if absent.
        A stable cert => stable designated requirement => FDA grant persists.
      '';
    };

  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ venv ];
    assertions = lib.mapAttrsToList
      (name: command: {
        assertion = builtins.match "[A-Za-z_][A-Za-z0-9_]*" name != null && command != [ ];
        message = "Job credentialCommands require valid environment names and nonempty commands.";
      })
      cfg.credentialCommands;

    system.activationScripts.postActivation.text = lib.mkAfter ''
      # 1. Ensure a stable self-signed code-signing cert in the System keychain.
      #    Created ONCE (idempotent) and reused every rebuild, so the .app's
      #    signature - and thus the one-time Full Disk Access grant - stays stable.
      if ! /usr/bin/security find-certificate -c ${lib.escapeShellArg cfg.signingIdentity} /Library/Keychains/System.keychain >/dev/null 2>&1; then
        echo "creating code-signing identity ${cfg.signingIdentity} (one-time)..."
        _t="$(/usr/bin/mktemp -d)"
        /usr/bin/printf '[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=%s\n[v3]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n' ${lib.escapeShellArg cfg.signingIdentity} > "$_t/req.cnf"
        /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout "$_t/key.pem" -out "$_t/cert.pem" -config "$_t/req.cnf"
        # non-empty p12 password: macOS `security` rejects empty-password PKCS12 (MAC verification fails)
        /usr/bin/openssl pkcs12 -export -inkey "$_t/key.pem" -in "$_t/cert.pem" -out "$_t/id.p12" -passout pass:flighty-sync-p12
        /usr/bin/security import "$_t/id.p12" -k /Library/Keychains/System.keychain -P flighty-sync-p12 -T /usr/bin/codesign -A
        # NB: no add-trusted-cert - it needs a GUI auth prompt (fails over SSH), and
        # it's unnecessary: codesign signs fine with an untrusted self-signed cert
        # and TCC matches FDA by the designated requirement, not trust.
        /bin/rm -rf "$_t"
      fi

      # 2. State dir must exist before launchd opens StandardOutPath (owned by the user).
      /bin/mkdir -p ${lib.escapeShellArg cfg.stateDir}
      /usr/sbin/chown ${lib.escapeShellArg cfg.user} ${lib.escapeShellArg cfg.stateDir}

      # 3. Install the .app to its stable path and sign it with the stable cert.
      echo "installing ${cfg.appInstallPath}..."
      /bin/rm -rf ${lib.escapeShellArg cfg.appInstallPath}
      /bin/cp -R ${appBundle} ${lib.escapeShellArg cfg.appInstallPath}
      /bin/chmod -R u+w ${lib.escapeShellArg cfg.appInstallPath}
      /usr/bin/codesign --force --sign ${lib.escapeShellArg cfg.signingIdentity} ${lib.escapeShellArg cfg.appInstallPath}
    '';

    launchd.user.agents.flighty-sync = {
      
      serviceConfig = {
        Label = "org.flighty-sync"; 
        ProgramArguments = [ appExe "run" ];
        # launchd's default CWD is "/" (read-only); anything writing CWD-relative
        # paths (e.g. seleniumbase downloads) needs a writable working dir.
        WorkingDirectory = cfg.stateDir;
        StartCalendarInterval = [
          { Hour = cfg.hour; Minute = cfg.minute; }
        ];
        RunAtLoad = false;
        StandardOutPath = "${cfg.stateDir}/launchd.log";
        StandardErrorPath = "${cfg.stateDir}/launchd.err.log";
      };
    };
  };
}
