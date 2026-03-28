import logging
import enum
import platform
import re
import shlex
import subprocess

import typer

from portal_tool.installer.configurators.configurator import (
    Configurator,
    CompilerDetails,
)


class LinuxDistro(enum.Enum):
    Debian = enum.auto()
    Fedora = enum.auto()
    Alpine = enum.auto()


class LinuxConfigurator(Configurator):
    def __init__(self, yes: bool):
        self.yes = yes

        logging.info("Running Ubuntu configurator")
        self.distro = self._detect_distro()

    @staticmethod
    def _detect_distro() -> LinuxDistro:
        try:
            with open("/etc/os-release") as f:
                content = f.read()

            if (
                "ID=ubuntu" in content
                or "ID=debian" in content
                or "ID_LIKE=debian" in content
            ):
                return LinuxDistro.Debian
            elif "ID=fedora" in content or "ID_LIKE=fedora" in content:
                return LinuxDistro.Fedora
            elif "ID=alpine" in content:
                return LinuxDistro.Alpine
        except FileNotFoundError:
            pass

        # Fallback to platform if os-release is missing
        uname_version = platform.version().lower()
        if "ubuntu" in uname_version or "debian" in uname_version:
            return LinuxDistro.Debian
        elif "fedora" in uname_version:
            return LinuxDistro.Fedora
        elif "alpine" in uname_version:
            return LinuxDistro.Alpine

        raise typer.Abort(
            "Unsupported Linux distribution. Could not detect from /etc/os-release or uname."
        )

    def _try_install_vcpkg_dependencies(self) -> None:
        typer.echo("Installing vcpkg dependencies... (curl, zip, unzip, tar, git)")
        self._install_package(["curl", "zip", "unzip", "tar", "git"])

    def _install_package(self, packages: list[str]) -> None:
        if self.distro == LinuxDistro.Debian:
            subprocess.run(
                shlex.split(f"sudo apt-get install -y {' '.join(packages)}"),
                check=True,
            )
        elif self.distro == LinuxDistro.Alpine:
            subprocess.run(
                shlex.split(f"sudo apk add {' '.join(packages)}"), check=True
            )
        elif self.distro == LinuxDistro.Fedora:
            subprocess.run(
                shlex.split(f"sudo dnf install {' '.join(packages)}"), check=True
            )
        else:
            raise typer.Abort(f"Unsupported Linux distribution: {self.distro}")

    def validate_compilers(self) -> list[CompilerDetails]:
        typer.echo("Validating compilers...")

        clang_valid = False
        gcc_valid = False

        found_compilers = []

        # Check for Clang 12+
        try:
            result = subprocess.run(
                ["clang", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                match = re.search(r"clang version (\d+)\.(\d+)", result.stdout)
                if match:
                    major = int(match.group(1))
                    if major >= 20:
                        # Try to get installation path
                        path_result = subprocess.run(
                            ["which", "clang"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        install_path = (
                            path_result.stdout.strip()
                            if path_result.returncode == 0
                            else "unknown"
                        )
                        typer.echo(
                            f"Clang {major}.{match.group(2)} found ({install_path})"
                        )
                        clang_valid = True
                    else:
                        typer.echo(
                            f"Clang {major}.{match.group(2)} found, but version 19+ is required"
                        )

            found_compilers.append(
                CompilerDetails(
                    name="clang", c_compiler="clang", cpp_compiler="clang++"
                )
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            typer.echo("Clang not found")

        # # Check for gcc 14+
        try:
            result = subprocess.run(
                ["gcc", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Parse version from output like "gcc (GCC) X.Y.Z" or "gcc version X.Y.Z"
                match = re.search(r"gcc.*?(\d+)\.(\d+)", result.stdout, re.IGNORECASE)
                if match:
                    major = int(match.group(1))
                    if major >= 14:
                        # Try to get installation path
                        path_result = subprocess.run(
                            ["which", "gcc"], capture_output=True, text=True, timeout=5
                        )
                        install_path = (
                            path_result.stdout.strip()
                            if path_result.returncode == 0
                            else "unknown"
                        )
                        typer.echo(
                            f"gcc {major}.{match.group(2)} found ({install_path})"
                        )
                        gcc_valid = True
                        found_compilers.append(
                            CompilerDetails(
                                name="gcc",
                                c_compiler="gcc",
                                cpp_compiler="g++",
                                default_compiler=True,
                            )
                        )
                    else:
                        typer.echo(
                            f"gcc {major}.{match.group(2)} found, but version 15+ is required"
                        )
        except (subprocess.SubprocessError, FileNotFoundError):
            typer.echo("gcc not found")

        # Require at least one valid compiler
        if not clang_valid and not gcc_valid:
            typer.echo("\nNo valid compiler found!")
            typer.echo("Please install at least one of the following:")
            typer.echo("  - Clang 19 or later")
            typer.echo("  - gcc 14 or later")
            raise typer.Abort("Compiler validation failed")

        typer.echo("Compiler validation successful!")
        return found_compilers

    def _validate_dependencies(self) -> None:
        dependency_map = {
            "All": [
                "pkg-config",
                "extra-cmake-modules",
                "autoconf-archive",
                "automake",
                "libtool",
                "ninja-build",
                "build-essential",
            ],
            "Debian": [
                "linux-libc-dev",
                "libwayland-dev",
                "libxkbcommon-dev",
                "wayland-protocols",
                "python3-venv",
                "xorg-dev",
            ],
            "Fedora": [
                "wayland-devel",
                "libxkbcommon-devel",
                "wayland-protocols-devel",
                "libXcursor-devel",
                "libXi-devel",
                "libXinerama-devel",
                "libXrandr-devel",
            ],
            "Alpine": ["linux-headers"],
        }

        dependency_list = dependency_map["All"] + dependency_map.get(
            self.distro.name, []
        )
        typer.echo(f"The following dependencies are required: {dependency_list}")

        if self.yes:
            proceed = True
        else:
            proceed = typer.confirm("Would you like to install them?")

        if proceed:
            self._install_package(dependency_list)
        else:
            typer.echo(
                "Please install them manually before building, exit now if some of them are missing"
            )

    def get_script_extension(self) -> str:
        return "sh"

    def get_executable_extension(self) -> str:
        return ""
