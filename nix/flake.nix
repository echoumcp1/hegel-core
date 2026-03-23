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

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./..; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      mkPythonSet = pkgs:
        let
          python = pkgs.python312;
          baseSet =
            pkgs.callPackage pyproject-nix.build.packages { inherit python; };
        in baseSet.overrideScope (lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          overlay
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
    };
}
