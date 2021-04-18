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
    in foldl' (a: b: recursiveUpdate a b) {} ( map ( system:
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
        ];
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
        exports = ''
          export PYTHONPATH="${./updater}"
          export PYPI_FETCHER=${inp.pypiIndex}
          export EXTRACTOR_DIR=${inp.mach-nix}/lib/extractor
          export NIX_PATH=nixpkgs=${inp.nixpkgsPy36}:nixpkgs-overlays=${py36Overlay}
        '';
      in {

        devShell."${system}" = pkgs.mkShell {
          buildInputs = deps;
          shellHook = exports;
        };

        apps."${system}" = rec {
          update-wheel.type = "app";
          update-wheel.program = toString (pkgs.writeScript "update-wheel" ''
            #!/usr/bin/env bash
            ${exports}
            ${pyEnv}/bin/python ${./updater}/crawl_wheel_deps.py
          '');
          update-sdist.type = "app";
          update-sdist.program = toString (pkgs.writeScript "update-sdist" ''
            #!/usr/bin/env bash
            ${exports}
            ${pyEnv}/bin/python ${./updater}/crawl_sdist_deps.py
          '');
          pipeline-sdist.type = "app";
          pipeline-sdist.program = toString (pkgs.writeScript "pipeline-sdist" ''
            #!/usr/bin/env bash
            set -e
            nix flake lock --update-input pypiIndex
            JOBS=1 ${update-sdist.program}

            echo $(date +%s) > UNIX_TIMESTAMP
            indexRev=$(nix flake metadata --json | nix-shell -p jq --run "jq -e --raw-output '.locks .nodes .pypiIndex .locked .rev'")
            indexHash=$(nix flake metadata --json | nix-shell -p jq --run "jq -e --raw-output '.locks .nodes .pypiIndex .locked .narHash'")
            echo $indexRev > PYPI_FETCHER_COMMIT
            echo $indexHash > PYPI_FETCHER_SHA256

            git add ./sdist UNIX_TIMESTAMP PYPI_FETCHER_COMMIT PYPI_FETCHER_SHA256
            git pull
            git commit -m "$(date) - sdist_update"
          '');
        };

        packages."${system}".pythonWithVariables = pkgs.writeScriptBin "python3" ''
          #!/usr/bin/env bash
          ${exports}
          ${pyEnv}/bin/python $@
        '';

      }) systems);
}