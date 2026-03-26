{
  description = "Hegel - universal property-based testing";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-compat.url = "https://flakehub.com/f/edolstra/flake-compat/1.tar.gz";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems, ... }:
    let
      inherit (nixpkgs) lib;

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ../.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      mkPythonSet = pkgs:
        let
          python = pkgs.python312;
          baseSet =
            pkgs.callPackage pyproject-nix.build.packages { inherit python; };
        in baseSet.overrideScope (lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          overlay
          (final: prev: {
            hegel-core = prev.hegel-core.overrideAttrs {
              src = lib.fileset.toSource {
                root = ../.;
                fileset = lib.fileset.intersection (lib.fileset.gitTracked ../.) (lib.fileset.fileFilter ({ name, hasExt, ... }: name == "pyproject.toml" || hasExt "py") ../.);
              };
            };
          })
        ]);

    in {
      packages = lib.genAttrs lib.systems.flakeExposed (system: let
        pkgs = nixpkgs.legacyPackages.${system};
        pythonSet = mkPythonSet pkgs;
        inherit (pkgs.callPackages pyproject-nix.build.util { }) mkApplication;
      in {
        default = mkApplication {
          venv = pythonSet.mkVirtualEnv "hegel-core-env" workspace.deps.default;
          package = pythonSet.hegel-core;
        };
      });

      devShells = lib.genAttrs lib.systems.flakeExposed (system: let
        pkgs = nixpkgs.legacyPackages.${system};
        # NOTE(winter): If we don't use editable here, this will cause... coverage to fail?
        # (Mainly just noting because of how weird it is, we do actually want editable.)
        pythonSet = (mkPythonSet pkgs).overrideScope (workspace.mkEditablePyprojectOverlay {
          root = "$REPO_ROOT";
        });
      in {
        default = pkgs.mkShell {
          packages = [
            (pythonSet.mkVirtualEnv "hegel-core-dev-env" workspace.deps.all)
            pkgs.uv
            pkgs.just
          ];
          env = {
            UV_NO_SYNC = "1";
            UV_PYTHON = pythonSet.python.interpreter;
            UV_PYTHON_DOWNLOADS = "never";
          };
          shellHook = ''
            unset PYTHONPATH
            export REPO_ROOT=$(git rev-parse --show-toplevel)
          '';
        };
      });
    };
}
