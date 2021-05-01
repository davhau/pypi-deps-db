{
  inputs = {
    mach-nix.url = "mach-nix";
    nixpkgs.url = "nixpkgs/nixos-unstable";
    nixpkgsPy36.url = "nixpkgs/b4db68ff563895eea6aab4ff24fa04ef403dfe14";
    pypiIndex.url = "github:davhau/nix-pypi-fetcher";
    pypiIndex.flake = false;
  };

  outputs = inp:
    with builtins;
    with inp.nixpkgs.lib;
    let
      systems = ["x86_64-linux"];
      self = {
        lib.supportedPythonVersions = [ "27" "36" "37" "38" "39" "310" ];
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
          # py27 and p36 crash when taken from current nixpkgs
          # this overlay mixes python interpreters from old and new nixpkgs
          py36Overlay = pkgs.writeText "py36-overlay.nix" ''
            [(curr: prev: 
              let
                pkgsNew = import ${inp.nixpkgs} {};
              in rec {
                useInterpreters = [
                  prev.python27
                  prev.python36
                  pkgsNew.python37
                  pkgsNew.python38
                  pkgsNew.python39
                  pkgsNew.python310
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
            NIX_PATH = "nixpkgs=${inp.nixpkgsPy36}:nixpkgs-overlays=${py36Overlay}";
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
              ${exports}
              ${pyEnv}/bin/python ${./updater}/crawl_wheel_deps.py
            '');

            # update wheel dataset by crawling packages found in inp.pypiIndex
            update-sdist.type = "app";
            update-sdist.program = toString (pkgs.writeScript "update-sdist" ''
              #!/usr/bin/env bash
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
              nix flake lock --update-input pypiIndex
              indexRev=$(${pkgs.nixFlakes}/bin/nix flake metadata --json | ${pkgs.jq}/bin/jq -e --raw-output '.locks .nodes .pypiIndex .locked .rev')
              if [ "$indexRevPrev" == "$indexRev" ]; then
                echo "Index unchanged. Nothing to do. Exiting..."
              fi

              # crawl wheel and sdist packages
              # If CI system has a run time limit, make sure to set MAX_MINUTES_WHEEL and MAX_MINUTES_SDIST
              # time ratio for wheel/sdist should be around 1/10
              MAX_MINUTES=''${MAX_MINUTES_WHEEL:-0} ${pkgs.nixFlakes}/bin/nix run .#update-wheel
              MAX_MINUTES=''${MAX_MINUTES_SDIST:-0} ${pkgs.nixFlakes}/bin/nix run .#update-sdist

              # commit to git
              echo $(date +%s) > UNIX_TIMESTAMP
              indexHash=$(${pkgs.nixFlakes}/bin/nix flake metadata --json | ${pkgs.jq}/bin/jq -e --raw-output '.locks .nodes .pypiIndex .locked .narHash')
              echo $indexRev > PYPI_FETCHER_COMMIT
              echo $indexHash > PYPI_FETCHER_SHA256

              git add sdist sdist-errors wheel flake.lock UNIX_TIMESTAMP PYPI_FETCHER_COMMIT PYPI_FETCHER_SHA256
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