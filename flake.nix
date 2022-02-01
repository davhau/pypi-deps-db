{
  inputs = {
    mach-nix.url = "mach-nix";
    mach-nix.inputs.nixpkgs.follows = "nixpkgs";
    mach-nix.inputs.pypi-deps-db.follows = "";
    nixpkgs.url = "nixpkgs/nixos-21.11";
    pypiIndex.url = "github:davhau/nix-pypi-fetcher";
    pypiIndex.flake = false;
  };

  outputs = inp:
    with builtins;
    with inp.nixpkgs.lib;
    let
      systems = ["x86_64-linux"];
      self = {
        lib.supportedPythonVersions = [ "37" "38" "39" ];
        lib.formatVersion = toInt (readFile ./FORMAT_VERSION);
      } 
      // foldl' (a: b: recursiveUpdate a b) {} ( map ( system:
        let
          pkgs = inp.nixpkgs.legacyPackages."${system}";
          pyEnv = inp.mach-nix.lib."${system}".mkPython {
            requirements = ''
              packaging
              requests
              pkginfo
              bounded-pool-executor
            '';
          };
          deps = [
            pyEnv
            pkgs.git
            pkgs.nixFlakes
          ];
          pyOverlay = pkgs.writeText "py36-overlay.nix" ''
            [(curr: prev: 
              rec {
                useInterpreters = [
                  prev.python37
                  prev.python38
                  prev.python39
                ];
              }
            )]
          '';
          # NIX_PATH has to be set, since the crawler is a python program calling
          #   nix with a legacy nix expression.
          # The overlay is passed via `nixpkgs-overlays`.
          defaultVars = {
            PYTHONPATH = "${./updater}";
            PYTHON_VERSIONS = concatStringsSep "," self.lib.supportedPythonVersions;
            PYPI_FETCHER = "${inp.pypiIndex}";
            EXTRACTOR_SRC = "${inp.mach-nix}/lib/extractor";
          };
          fixedVars = {
            NIX_PATH = "nixpkgs=${inp.nixpkgs}:nixpkgs-overlays=${pyOverlay}";
          };
          # defaultVars are only set if they are not already set
          # fixedVars are always set
          exports = ''
            ${concatStringsSep "\n" (mapAttrsToList (n: v: "export ${n}=\"${v}\"") fixedVars)}
            ${concatStringsSep "\n" (mapAttrsToList (n: v: "export ${n}=\"\${${n}:-${v}}\"") defaultVars)}
          '';
        in {
          
          # devShell to load all dependencies and environment variables
          devShell."${system}" = pkgs.mkShell {
            buildInputs = deps;
            shellHook = exports;
          };

          # apps to update the database
          # All apps assume that the current directory is a git checkout of this project
          apps."${system}" = rec {

            # update sdist dataset by crawling packages found in inp.pypiIndex
            update-wheel.type = "app";
            update-wheel.program = toString (pkgs.writeScript "update-wheel" ''
              #!/usr/bin/env bash
              set -e
              set -x
              ${exports}
              ${pyEnv}/bin/python ${./updater}/crawl_wheel_deps.py
            '');

            # update wheel dataset by crawling packages found in inp.pypiIndex
            update-sdist.type = "app";
            update-sdist.program = toString (pkgs.writeScript "update-sdist" ''
              #!/usr/bin/env bash
              set -e
              set -x
              ${exports}
              ${pyEnv}/bin/python ${./updater}/crawl_sdist_deps.py
            '');

            # update pypiIndex flake input + update data + commit to git.
            job-sdist-wheel.type = "app";
            job-sdist-wheel.program = toString (pkgs.writeScript "job-sdist" ''
              #!/usr/bin/env bash
              set -e
              set -x

              # update the index to get the newest packages
              indexRevPrev=$(${pkgs.nixFlakes}/bin/nix flake metadata --json | ${pkgs.jq}/bin/jq -e --raw-output '.locks .nodes .pypiIndex .locked .rev')
              #nix flake lock --update-input pypiIndex
              indexRev=$(${pkgs.nixFlakes}/bin/nix flake metadata --json | ${pkgs.jq}/bin/jq -e --raw-output '.locks .nodes .pypiIndex .locked .rev')

              # commit to git
              echo $(date +%s) > UNIX_TIMESTAMP
              indexHash=$(${pkgs.nixFlakes}/bin/nix flake metadata --json | ${pkgs.jq}/bin/jq -e --raw-output '.locks .nodes .pypiIndex .locked .narHash')
              echo $indexRev > PYPI_FETCHER_COMMIT
              echo $indexHash > PYPI_FETCHER_SHA256

              # crawl wheel and sdist packages
              # If CI system has a run time limit, make sure to set MAX_MINUTES_WHEEL and MAX_MINUTES_SDIST
              # time ratio for wheel/sdist should be around 1/10
              MAX_MINUTES=''${MAX_MINUTES_WHEEL:-0} ${update-wheel.program}
              MAX_MINUTES=''${MAX_MINUTES_SDIST:-0} ${update-sdist.program}

              git add sdist sdist-errors wheel flake.lock UNIX_TIMESTAMP PYPI_FETCHER_COMMIT PYPI_FETCHER_SHA256
              git status
              git pull origin $(git rev-parse --abbrev-ref HEAD)
              git commit -m "$(date) - update sdist + wheel"
            '');
          };

          # This python interpreter can be used for debugging in IDEs
          # It will set all env variables during startup
          packages."${system}".pythonWithVariables = pkgs.writeScriptBin "python3" ''
            #!/usr/bin/env bash
            ${exports}
            ${pyEnv}/bin/python $@
          '';

        }) systems);
    in
      self;
}
