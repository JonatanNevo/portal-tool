import enum
import importlib
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import zipimport
from typing import cast

import typer
from cookiecutter.main import cookiecutter

from portal_tool.installer.repo.build_models import (
    PackagePreset,
    WorkflowPreset,
    WorkflowStep,
)

from portal_tool.framework.framework_manager import FrameworkManager
from portal_tool.installer.configurators.configurator import CompilerDetails
from portal_tool.installer.configurator_factory import ConfiguratorFactory
from portal_tool.installer.repo.build_models import (
    CMakePresets,
    ConfigurePreset,
    BuildPreset,
)


class RepoMaker:
    """
    A repo consists of:
    - vcpkg (either submodule or globally installed)
    - src
    - resources
    - vcpkg configuration
    - cmake configuration
    """

    def __init__(self, path: pathlib.Path, framework_manager: FrameworkManager):
        self.configurator = ConfiguratorFactory().create(False)
        self.presets = CMakePresets()
        self.framework_manager = framework_manager

        self.name = typer.prompt("Project Name")
        self.base_path = pathlib.Path(
            typer.prompt(
                f"Base Location (will create a folder named {self.name.lower().replace(' ', '-')})",
                default=path,
            )
        )
        self.project_path = self.base_path / self.name.lower().replace(" ", "-")

        self.vcpkg_toolchain_location = "{}/scripts/buildsystems/vcpkg.cmake"
        self.use_global = False
        self.vcpkg_exec_location = pathlib.Path("")

        self._create_repo_from_template()

        use_example = typer.confirm(
            "Would you like to use an example project?", default=True
        )
        if use_example:
            available_examples = self.framework_manager.list_examples()

            example_choices = enum.Enum(
                "Examples", {ex: ex for ex in available_examples}
            )
            default_example = (
                "engine_test"
                if "engine_test" in available_examples
                else available_examples[0]
            )

            chosen_example = typer.prompt(
                f"Please choose an example to use ({', '.join(available_examples)})",
                type=example_choices,
                default=default_example,
            )

            self.framework_manager.configure_example(
                chosen_example.value, self.project_path
            )

        self._configure_git()
        self._setup_vcpkg()
        self._configure_build_system()
        self._update_vcpkg_baseline()

    def _find_pacakge_path(self) -> pathlib.Path:
        source_path = "templates/repo/bootstrap"
        spec = importlib.util.find_spec("portal_tool")
        assert spec is not None, "An import spec was not found for the package."
        loader = spec.loader
        assert loader is not None, "A loader was not found for the package."

        if isinstance(loader, zipimport.zipimporter):
            self._archive = loader.archive
            pkgdir = next(iter(spec.submodule_search_locations))  # type: ignore
            template_root = os.path.join(pkgdir, source_path).rstrip(os.path.sep)
        else:
            roots: list[str] = []

            # One element for regular packages, multiple for namespace
            # packages, or None for single module file.
            if spec.submodule_search_locations:
                roots.extend(spec.submodule_search_locations)
            # A single module file, use the parent directory instead.
            elif spec.origin is not None:
                roots.append(os.path.dirname(spec.origin))

            if not roots:
                raise ValueError(
                    "The portal_tool package was not installed in a"
                    " way that PackageLoader understands."
                )

            for root in roots:
                root = os.path.join(root, source_path)

                if os.path.isdir(root):
                    template_root = root
                    break
            else:
                raise ValueError(
                    f"PackageLoader could not find a {source_path!r} directory"
                    f" in the portal_tool package."
                )
        return pathlib.Path(template_root)

    def _create_repo_from_template(self) -> None:
        if self.project_path.exists():
            proceed = typer.confirm(
                "Found existing project, would you like to continue? (delete folder)"
            )
            if not proceed:
                raise typer.Abort("Aborting repo creation.")
            return

        cookiecutter(
            self._find_pacakge_path().as_posix(),
            no_input=True,
            extra_context={
                "project_name": self.name,
                "engine_version": self.framework_manager.get_engine_version(),
                "vcpkg_baseline": self.framework_manager.get_vcpkg_baseline_hash(),
            },
            output_dir=self.base_path.as_posix(),
        )

    def _configure_git(self) -> None:
        typer.echo(f"Initializing git repo in: {self.project_path}")
        subprocess.check_output(shlex.split(f'git -C "{self.project_path}" init'))

    def _setup_vcpkg(self) -> None:
        vcpkg_root, found_using_env = self.configurator.find_vcpkg_root()

        self.use_global = False
        if vcpkg_root:
            self.use_global = typer.confirm(
                f"Found global vcpkg, [{vcpkg_root}] would you like to use? (if not, a local submodule will be created)",
                default=True,
            )

        if not self.use_global:
            typer.echo(f"Creating vcpkg submodule in: {self.project_path / 'vcpkg'}")
            try:
                subprocess.check_output(
                    shlex.split(
                        f'git -C "{self.project_path}" submodule add https://github.com/microsoft/vcpkg "{"vcpkg"}"'
                    )
                )
                subprocess.check_output(
                    f"{self.project_path.as_posix()}/vcpkg/bootstrap-vcpkg.{self.configurator.get_script_extension()}",
                    shell=True,
                )
                self.vcpkg_exec_location = (
                    self.project_path
                    / "vcpkg"
                    / f"vcpkg{self.configurator.get_executable_extension()}"
                )
            except subprocess.CalledProcessError:
                typer.echo("Failed to add submodule, please add it manually.")
        else:
            self.vcpkg_exec_location = (
                cast(pathlib.Path, vcpkg_root)
                / f"vcpkg{self.configurator.get_executable_extension()}"
            )

        self.vcpkg_toolchain_location = self.vcpkg_toolchain_location.format(
            (
                "$env{VCPKG_ROOT}"
                if found_using_env
                else (pathlib.Path(os.path.expanduser("~")) / ".vcpkg").as_posix()
            )
            if self.use_global
            else "${sourceDir}/vcpkg"
        )

    def _configure_build_system(self) -> None:
        project_compiler: CompilerDetails
        compilers = self.configurator.validate_compilers()
        if len(compilers) == 0:
            raise typer.Abort(
                "No valid compilers found, please run `portal-tool install` and follow the instructions."
            )

        if len(compilers) == 1:
            project_compiler = compilers[0]

        else:
            choices_data = {comp.name: comp.name for comp in compilers}
            compilers_choices = enum.Enum("Compilers", choices_data)

            default_name = "clang" if "clang" in choices_data else compilers[0].name

            choice_member = typer.prompt(
                f"Multiple compilers found, please choose one ({', '.join(choices_data.keys())})",
                type=compilers_choices,
                default=default_name,
            )

            comp_lookup = {comp.name: comp for comp in compilers}
            project_compiler = comp_lookup[choice_member.value]

        base = ConfigurePreset(
            name="base",
            hidden=True,
            binary_dir="${sourceDir}/build/${presetName}",
            cache_variables={
                "CMAKE_TOOLCHAIN_FILE": self.vcpkg_toolchain_location,
                "CMAKE_CONFIGURATION_TYPES": "Debug;RelWithDebInfo;Release",
                "CMAKE_C_COMPILER": project_compiler.c_compiler,
                "CMAKE_CXX_COMPILER": project_compiler.cpp_compiler,
            },
        )
        base.environment = {
            "PORTAL_C_COMPILER": project_compiler.c_compiler,
            "PORTAL_CPP_COMPILER": project_compiler.cpp_compiler,
            "VCPKG_KEEP_ENV_VARS": "PORTAL_C_COMPILER;PORTAL_CPP_COMPILER;PATH",
        }

        generator = self.configurator.generate_configuration_preset(project_compiler)

        self.presets.configure_presets = [base, generator]

        self.presets.build_presets = [
            BuildPreset(
                name="debug", configure_preset=generator.name, configuration="Debug"
            ),
            BuildPreset(
                name="development",
                configure_preset=generator.name,
                configuration="RelWithDebInfo",
            ),
            BuildPreset(
                name="dist",
                configure_preset=generator.name,
                configuration="Release",
            ),
        ]

        self.presets.package_presets = [
            PackagePreset(
                name="pack-zip",
                configure_preset=generator.name,
                package_directory="${sourceDir}/dist",
                generators=["ZIP"],
            ),
            PackagePreset(
                name="pack-installer",
                configure_preset=generator.name,
                package_directory="${sourceDir}/dist",
                generators=["IFW"],
            ),
        ]

        self.presets.workflow_presets = [
            WorkflowPreset(
                name="package-zip",
                steps=[
                    WorkflowStep(name=generator.name, type="configure"),
                    WorkflowStep(name="dist", type="build"),
                    WorkflowStep(name="pack-zip", type="package"),
                ],
            ),
            WorkflowPreset(
                name="package-installer",
                steps=[
                    WorkflowStep(name=generator.name, type="configure"),
                    WorkflowStep(name="dist", type="build"),
                    WorkflowStep(name="pack-installer", type="package"),
                ],
            ),
        ]

        (self.project_path / "CMakePresets.json").write_text(
            self.presets.model_dump_json(indent=4, exclude_none=True, by_alias=True)
        )

        (self.project_path / "vcpkg-configuration.json").write_text(
            self.framework_manager.get_vcpkg_configuration()
        )

    def _update_vcpkg_baseline(self) -> None:
        output = subprocess.check_output(
            shlex.split(
                f"{self.vcpkg_exec_location.as_posix()} x-update-baseline --dry-run"
            ),
            cwd=self.project_path.as_posix(),
        )

        portal_vcpkg_baseline = None
        output_lines = output.decode().splitlines()
        for line in output_lines:
            if "https://github.com/JonatanNevo/portal-vcpkg-registry" in line:
                portal_vcpkg_baseline = line.split(" ")[-1].strip("'")

        if portal_vcpkg_baseline is None:
            typer.echo(
                "Failed to update portal-vcpkg-registry baseline, please make sure it points the correct commit."
            )
            return

        vcpkg_configuration = json.loads(
            (self.project_path / "vcpkg-configuration.json").read_text()
        )
        for registry in vcpkg_configuration["registries"]:
            if (
                registry["repository"]
                == "https://github.com/JonatanNevo/portal-vcpkg-registry"
            ):
                registry["baseline"] = portal_vcpkg_baseline
        json.dump(
            vcpkg_configuration,
            (self.project_path / "vcpkg-configuration.json").open("w"),
            indent=2,
        )
