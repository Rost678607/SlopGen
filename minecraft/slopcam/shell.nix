# Build environment for the slopcam mod: a JDK the mod targets and Gradle.
# Kept apart from the repo's shell.nix so the Python shell does not carry a JDK.
# Usage: nix-shell minecraft/slopcam/shell.nix --run 'gradle build'
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = with pkgs; [ jdk21 gradle ];
  shellHook = ''
    export JAVA_HOME=${pkgs.jdk21}
  '';
}
