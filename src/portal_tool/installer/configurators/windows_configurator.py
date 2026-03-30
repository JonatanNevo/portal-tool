import logging
import os
import re
import subprocess

import pathlib

import typer

from portal_tool.installer.repo.build_models import ConfigurePreset
from portal_tool.installer.configurators.configurator import (
    Configurator,
    CompilerDetails,
)


class WindowsConfigurator(Configurator):
    def __init__(self, yes: bool):
        logging.info("Running Windows 11 configurator")

    def _try_install_vcpkg_dependencies(self) -> None:
        pass

    def _install_package(self, packages: list[str]) -> None:
        raise NotImplementedError

    def validate_compilers(self) -> list[CompilerDetails]:
        typer.echo("Validating compilers...")

        clang_valid = False
        msvc_valid = False

        found_compilers = []

        try:
            result = subprocess.run(
                ["clang", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                match = re.search(r"clang version (\d+)\.(\d+)", result.stdout)
                installation_path = re.search("InstalledDir: (.*)", result.stdout)
                if match:
                    major = int(match.group(1))
                    if major >= 19:
                        path_info = (
                            f" ({installation_path.group(1)})"
                            if installation_path
                            else ""
                        )
                        typer.echo(f"Clang {major}.{match.group(2)} found{path_info}")
                        clang_valid = True

                        clang_result = subprocess.run(
                            ["where.exe", "clang"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if clang_result.returncode != 0:
                            typer.echo(
                                "clang executable exists in PATH but failed to fetch exe location"
                            )
                            raise FileNotFoundError
                        clang_location = clang_result.stdout.strip()

                        clangpp_result = subprocess.run(
                            ["where.exe", "clang++"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if clangpp_result.returncode != 0:
                            typer.echo(
                                "clang++ executable exists in PATH but failed to fetch exe location"
                            )
                            raise FileNotFoundError
                        clangpp_location = clangpp_result.stdout.strip()

                        found_compilers.append(
                            CompilerDetails(
                                name="clang",
                                c_compiler=clang_location,
                                cpp_compiler=clangpp_location,
                            )
                        )
                    else:
                        typer.echo(
                            f"Clang {major}.{match.group(2)} found, but version 19+ is required"
                        )
        except (subprocess.SubprocessError, FileNotFoundError):
            typer.echo("Clang not found")

        try:
            program_files_x86_path = os.environ.get("ProgramFiles(x86)", "")
            vs_where_path = (
                pathlib.Path(program_files_x86_path)
                / "Microsoft Visual Studio"
                / "Installer"
                / "vswhere.exe"
            )

            result = subprocess.run(
                [vs_where_path], capture_output=True, text=True, timeout=5
            )

            output = result.stdout + result.stderr
            match = re.search(r"installationVersion: (\d+)\.(\d+).(\d+)\.(\d+)", output)
            installation_path = re.search("installationPath: (.*)", output)
            if match:
                major = int(match.group(1))
                minor = int(match.group(2))
                version_str = f"{major}.{minor}"
                if major >= 17:
                    path_info = (
                        f" ({installation_path.group(1)})" if installation_path else ""
                    )
                    typer.echo(f"MSVC {version_str} found{path_info}")
                    msvc_valid = True

                    vs_install_path = pathlib.Path(installation_path.group(1).strip())
                    msvc_tools_dir = vs_install_path / "VC" / "Tools" / "MSVC"
                    if not msvc_tools_dir.exists():
                        typer.echo("MSVC tools directory not found")
                        raise FileNotFoundError
                    toolset_versions = os.listdir(msvc_tools_dir)
                    if not toolset_versions:
                        typer.echo("No MSVC toolset versions found")
                        raise FileNotFoundError
                    toolset_version = sorted(toolset_versions)[-1]
                    cl_path = (
                        msvc_tools_dir
                        / toolset_version
                        / "bin"
                        / "Hostx64"
                        / "x64"
                        / "cl.exe"
                    )
                    if not cl_path.exists():
                        typer.echo(f"cl.exe not found at {cl_path}")
                        raise FileNotFoundError
                    cl_location = str(cl_path)

                    found_compilers.append(
                        CompilerDetails(
                            name="msvc",
                            c_compiler=cl_location,
                            cpp_compiler=cl_location,
                        )
                    )
                else:
                    typer.echo(f"MSVC {version_str} found, but version 17+ is required")
        except (subprocess.SubprocessError, FileNotFoundError):
            typer.echo("MSVC not found")

        if not clang_valid and not msvc_valid:
            typer.echo("\nNo valid compiler found!")
            typer.echo("Please install at least one of the following:")
            typer.echo("  - Clang 19 or later")
            typer.echo("        can be installed from here https://releases.llvm.org/")
            typer.echo("  - MSVC 17 or later")
            typer.echo(
                "        can be installed from here https://visualstudio.microsoft.com/downloads/"
            )
            raise typer.Abort("Compiler validation failed")

        typer.echo("Compiler validation successful!")
        return found_compilers

    def generate_configuration_preset(
        self, compiler: CompilerDetails
    ) -> ConfigurePreset:
        if compiler.name == "msvc":
            return ConfigurePreset(
                name="msvc-toolchain",
                inherits=["base"],
                generator="Visual Studio 18 2026",
                # TODO choose correct visual studio based on whats installed
            )
        else:
            return ConfigurePreset(
                name="ninja-multi", inherits=["base"], generator="Ninja Multi-Config"
            )

    def get_script_extension(self) -> str:
        return "bat"

    def get_executable_extension(self) -> str:
        return ".exe"

    def _validate_dependencies(self) -> None:
        # Windows does not have any dependencies that need validating
        typer.echo("No dependencies to validate!")
