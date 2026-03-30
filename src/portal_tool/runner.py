import json
import shlex
import subprocess
from enum import Enum
from typing import Annotated
import re

import pathlib
import typer

from portal_tool.installer.configurator_factory import ConfiguratorFactory

global_working_directory = pathlib.Path.cwd()


class Configuration(str, Enum):
    debug = "debug"
    development = "development"
    dist = "dist"


CMAKE_CONFIGURATION: dict[Configuration, str] = {
    Configuration.debug: "Debug",
    Configuration.development: "RelWithDebInfo",
    Configuration.dist: "Release",
}

runner = typer.Typer()


def find_configure_preset_name(working_dir: pathlib.Path) -> str:
    presets_path = working_dir / "CMakePresets.json"
    if not presets_path.exists():
        raise typer.Abort("CMakePresets.json not found in the working directory.")

    presets = json.loads(presets_path.read_text())
    for preset in presets.get("configurePresets", []):
        if not preset.get("hidden", False):
            return preset["name"]

    raise typer.Abort("No visible configure preset found in CMakePresets.json.")


def first_cmake_call_arg(text: str, func_name: str) -> str | None:
    """
    Extract the first argument of: func_name(<arg> ...)
    Handles arbitrary whitespace/newlines and quoted first args.
    """
    # Match: func_name ( <spaces> ( "quoted" | bareword )
    pattern = rf"""
        ^\s*{re.escape(func_name)}\s*    # function name at line start (with indentation)
        \(\s*                           # opening paren, then whitespace/newlines
        (                               # capture group 1 = first arg
            "(?:\\.|[^"\\])*"           # "quoted string" with simple escapes
            |
            [^\s\)]+                    # or bare token up to whitespace or ')'
        )
    """
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL | re.VERBOSE)
    if not m:
        return None

    arg = m.group(1)
    if arg.startswith('"') and arg.endswith('"'):
        arg = arg[1:-1]  # unquote (optional; remove if you want raw token)
    return arg


def find_target_name(cmake_path: pathlib.Path) -> str:
    target_name = first_cmake_call_arg(cmake_path.read_text(), "portal_add_game")
    if target_name is None:
        return typer.prompt(
            "Unable to find target name in CMakeLists.txt. Please enter it manually:"
        )
    return target_name


class Runner:
    def __init__(
        self,
        working_dir: pathlib.Path,
        configuration: Configuration,
        build_path: pathlib.Path | None,
        cmake_configuration: str | None,
    ):
        self.working_dir = working_dir
        self.configuration = configuration
        self.configurator = ConfiguratorFactory().create(False)
        self.preset_name = find_configure_preset_name(working_dir)

        self.cmake_configuration = cmake_configuration
        if self.cmake_configuration is None:
            self.cmake_configuration = CMAKE_CONFIGURATION[configuration]

        if build_path is None:
            build_path = pathlib.Path("build") / self.preset_name

        self.target_name = find_target_name(working_dir / "CMakeLists.txt")
        self.executable_dir = working_dir / build_path / self.cmake_configuration

    def build_executable(self) -> None:
        subprocess.run(
            shlex.split(f"cmake --fresh --preset {self.preset_name}"),
            cwd=self.working_dir,
            check=True,
        )

        subprocess.run(
            shlex.split(f"cmake --build --preset {self.configuration.name}"),
            cwd=self.working_dir,
            check=True,
        )

    def validate_executable(self, executable: str, clean: bool) -> None:
        exe_path = pathlib.Path(executable)
        if clean or not exe_path.exists():
            typer.confirm(
                "No executable found. Would you like to build it now?", abort=True
            )

            self.build_executable()

    def run(self, *args: str) -> None:
        subprocess.Popen([*args], cwd=self.executable_dir)


@runner.command(name="editor")
def run_editor(
    working_dir: Annotated[
        pathlib.Path,
        typer.Option(
            "-d", "--working-dir", help="The working directory of the project"
        ),
    ] = pathlib.Path.cwd(),
    configuration: Annotated[
        Configuration,
        typer.Option("-c", "--configuration", help="Which configuration to use/build"),
    ] = Configuration.development,
    build_path: Annotated[
        pathlib.Path | None,
        typer.Option(
            "-b", "--build-path", help="The path to the build directory, (relative)"
        ),
    ] = None,
    cmake_configuration: Annotated[
        str | None,
        typer.Option(
            "--configuration-build-path",
            help="The path in the build directory for the configuration",
        ),
    ] = None,
    clean: Annotated[
        bool,
        typer.Option("--clean", help="Always rebuild the executable before running"),
    ] = False,
):
    engine_runner = Runner(working_dir, configuration, build_path, cmake_configuration)
    executable = f"{engine_runner.executable_dir / engine_runner.target_name}_editor{engine_runner.configurator.get_executable_extension()}"
    engine_runner.validate_executable(executable, clean=clean)

    typer.echo(f"Running editor from: {executable}")
    engine_runner.run(executable, "-p", f"{working_dir}")


@runner.command(name="runtime")
def run_runtime(
    working_dir: Annotated[
        pathlib.Path,
        typer.Option(
            "-d", "--working-dir", help="The working directory of the project"
        ),
    ] = pathlib.Path.cwd(),
    configuration: Annotated[
        Configuration,
        typer.Option("-c", "--configuration", help="Which configuration to use/build"),
    ] = Configuration.development,
    build_path: Annotated[
        pathlib.Path | None,
        typer.Option("-b", "--build-path", help="The path to the build directory"),
    ] = None,
    cmake_configuration: Annotated[
        str | None,
        typer.Option(
            "--configuration-build-path",
            help="The path in the build directory for the configuration",
        ),
    ] = None,
    clean: Annotated[
        bool,
        typer.Option("--clean", help="Always rebuild the executable before running"),
    ] = False,
):
    engine_runner = Runner(working_dir, configuration, build_path, cmake_configuration)
    executable = f"{engine_runner.executable_dir / engine_runner.target_name}{engine_runner.configurator.get_executable_extension()}"
    engine_runner.validate_executable(executable, clean=clean)

    typer.echo(f"Running game from: {executable}")
    engine_runner.run(executable)
