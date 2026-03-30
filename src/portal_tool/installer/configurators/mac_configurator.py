import logging

import typer

from portal_tool.installer.repo.build_models import ConfigurePreset
from portal_tool.installer.configurators.configurator import (
    Configurator,
    CompilerDetails,
)


class MacConfigurator(Configurator):
    def __init__(self, yes: bool):
        logging.info("Running MacOs configurator")

    def _try_install_vcpkg_dependencies(self) -> None:
        pass

    def _install_package(self, packages: list[str]) -> None:
        raise NotImplementedError

    def validate_compilers(self) -> list[CompilerDetails]:
        typer.echo("Missing compiler validation, skipping...")
        return [
            CompilerDetails(name="clang", c_compiler="clang", cpp_compiler="clang++")
        ]

    def generate_configuration_preset(
        self, compiler: CompilerDetails
    ) -> ConfigurePreset:
        return ConfigurePreset(
            name="ninja-multi", inherits=["base"], generator="Ninja Multi-Config"
        )

    def get_script_extension(self) -> str:
        return "sh"

    def get_executable_extension(self) -> str:
        return ""

    def _validate_dependencies(self) -> None:
        typer.echo("No dependencies to validate!")
