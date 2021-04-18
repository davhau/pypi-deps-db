{
  inputs = {
    mach-nix.url = "/home/grmpf/projects/github/mach-nix";
    nixpkgs.url = "nixpkgs/nixos-unstable";
    nixpkgsPy36.url = "nixpkgs/b4db68ff563895eea6aab4ff24fa04ef403dfe14";
    pypi-index.url = "github:davhau/nix-pypi-fetcher";
    pypi-index.flake = false;
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
          export PYPI_FETCHER=${inp.pypi-index}
          export EXTRACTOR_DIR=${inp.mach-nix}/lib/extractor
          export NIX_PATH=nixpkgs=${inp.nixpkgsPy36}:nixpkgs-overlays=${py36Overlay}
        '';
      in {

        devShell."${system}" = pkgs.mkShell {
          buildInputs = deps;
          shellHook = exports;
        };

        apps."${system}" = {
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
        };

        packages."${system}".pythonWithVariables = pkgs.writeScriptBin "python3" ''
          #!/usr/bin/env bash
          ${exports}
          ${pyEnv}/bin/python $@
        '';

      }) systems);
}