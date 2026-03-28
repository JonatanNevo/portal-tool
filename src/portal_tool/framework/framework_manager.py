import json

import pathlib
import shutil

import typer

from portal_tool.framework.git_manager import GitManager
from portal_tool.models import Configuration, PortalModule, Dependency

IGNORED_FEATURES = ["dev"]


def get_portal_dependencies(depends_string: str) -> list[str]:
    if not depends_string.startswith("depends: "):
        typer.Abort(f"Invalid depends string: {depends_string}")

    cropped_string = depends_string[len("depends: ") :]
    return cropped_string.split(",")


class FrameworkManager:
    def __init__(self, config: Configuration):
        self.git_manager: GitManager = GitManager()
        self.git_manager.init_repo(config)

        self.framework_path = self.git_manager.data_folder / "repo" / "framework"

    def find_modules(self) -> list[PortalModule]:
        vcpkg_file = self.framework_path / "vcpkg.json"

        if not vcpkg_file.exists():
            typer.Abort("Could not find vcpkg.json in framework folder.")

        output = []
        vcpkg_data = json.loads(vcpkg_file.read_text())

        for name, feature in vcpkg_data.get("features", {}).items():
            if name in IGNORED_FEATURES:
                continue

            module_name = name if name.startswith("portal-") else f"portal-{name}"
            short_name = module_name.replace("portal-", "")
            description_tuple: tuple[str, str] | str = feature.get("description", "")
            dependencies: list[Dependency] = []
            has_portal_deps = False

            if isinstance(description_tuple, str):
                description = description_tuple
            else:
                description = description_tuple[0]
                for dep in get_portal_dependencies(description_tuple[1]):
                    dependencies.append(Dependency(name=dep))
                has_portal_deps = True

            for dependency in feature.get("dependencies", []):
                dependencies.append(
                    Dependency(
                        name=dependency.get("name"),
                        version=dependency.get("version>="),
                        features=dependency.get("features"),
                        platform=dependency.get("platform"),
                    )
                )

            options = ["PORTAL_BUILD_TESTS=OFF"]
            if has_portal_deps:
                options.append("PORTAL_FIND_PACKAGE=ON")

            output.append(
                PortalModule(
                    name=module_name,
                    short_name=short_name,
                    version=self.git_manager.get_version(short_name),
                    description=description,
                    options=options,
                    dependencies=dependencies,
                )
            )

        # TODO: validate portal dependencies version based on configures module versions
        return output

    def get_engine_version(self) -> str:
        return self.git_manager.get_version("engine")

    def get_vcpkg_baseline_hash(self) -> str:
        vcpkg_path = self.framework_path / "vcpkg.json"
        if not vcpkg_path.exists():
            typer.Abort("Could not find vcpkg.json in framework folder.")

        baseline = json.load(vcpkg_path.open())["builtin-baseline"]
        return baseline

    def list_examples(self) -> list[str]:
        examples_folder = self.framework_path / "examples"
        if not examples_folder.exists():
            return []

        return [
            example.name for example in examples_folder.iterdir() if example.is_dir()
        ]

    def configure_example(self, example_name: str, output_folder: pathlib.Path) -> None:
        example_folder = self.framework_path / "examples" / example_name
        if not example_folder.exists():
            typer.Abort(f"Example {example_name} not found.")

        output_folder.mkdir(parents=True, exist_ok=True)
        shutil.copytree(example_folder, output_folder, dirs_exist_ok=True)

        out_cmake = output_folder / "CMakeLists.txt"
        out_cmake_data = out_cmake.read_text()
        out_cmake_data = out_cmake_data.replace(
            "#find_package(portal-engine CONFIG REQUIRED)",
            "find_package(portal-engine CONFIG REQUIRED)",
        )
        out_cmake.write_text(out_cmake_data)

    def get_vcpkg_configuration(self) -> str:
        return (self.framework_path / "vcpkg-configuration.json").read_text()
