import hashlib
import logging
import pathlib
import subprocess
import shlex

from tqdm import tqdm
import requests

import appdirs

from portal_tool.models import (
    GitDetails,
    Configuration,
    LocalStatus,
    GitStatus,
)
from portal_tool.singleton import Singleton


def get_ref(repo_path: pathlib.Path, branch: str) -> str:
    branch_ref = repo_path / ".git/refs/heads" / branch
    if branch_ref.is_file():
        with open(branch_ref, "r") as main_ref_file:
            return main_ref_file.read().strip()
    logging.warning(f"Could not find branch ref at {branch_ref}")
    return "Invalid"


def initialize_local_status(status_path: pathlib.Path) -> LocalStatus:
    if not status_path.exists():
        status_path.parent.mkdir(parents=True, exist_ok=True)
        return LocalStatus()
    return LocalStatus.model_validate_json(status_path.read_text())


class GitManager(metaclass=Singleton):
    def __init__(self):
        self.data_folder = pathlib.Path(appdirs.user_data_dir("portal-tool"))
        self.status_path = self.data_folder / "status.json"
        self.status = initialize_local_status(self.status_path)

        self.framework_repo = (
            self.status.framework.repo if self.status.framework.repo else "Invalid"
        )
        self.branch = (
            self.status.framework.branch if self.status.framework.branch else "main"
        )
        self.framework_commit = (
            self.status.framework.commit if self.status.framework.commit else "Invalid"
        )
        self.repo_sha = self.status.framework.sha if self.status.framework.sha else None

        self.registry_repo = (
            self.status.registry.repo if self.status.registry.repo else "Invalid"
        )
        self.registry_commit = (
            self.status.registry.commit if self.status.registry.commit else "Invalid"
        )
        self.dirty = False

    def dump_status(self) -> None:
        self.dirty = False
        status = LocalStatus(
            framework=GitStatus(
                repo=self.framework_repo,
                commit=self.framework_commit,
                branch=self.branch,
                sha=self.repo_sha,
            ),
            registry=GitStatus(
                repo=self.registry_repo,
                commit=self.registry_commit,
            ),
        )
        self.status_path.write_text(
            status.model_dump_json(indent=4, exclude_none=True, exclude_defaults=True)
        )

    def init_repo(self, configuration: Configuration) -> None:
        if (
            configuration.repo != self.framework_repo
            or configuration.repo_branch != self.branch
        ):
            self.framework_repo = configuration.repo
            if configuration.repo_branch:
                self.branch = configuration.repo_branch
            self.dirty = True

        if configuration.vcpkg_registry_repo != self.registry_repo:
            self.registry_repo = configuration.vcpkg_registry_repo
            self.dirty = True

        framework_repo_url = f"https://github.com/{self.framework_repo}.git"
        registry_repo_url = f"https://github.com/{self.registry_repo}.git"

        self.framework_commit = self.check_repo_validity(
            framework_repo_url,
            self.data_folder / "repo" / "framework",
            self.framework_commit,
            self.branch,
        )
        self.registry_commit = self.check_repo_validity(
            registry_repo_url,
            self.data_folder / "repo" / "registry",
            self.registry_commit,
        )

        logging.debug(f"Got ref for {self.framework_repo}: {self.framework_commit}")
        logging.debug(f"Got ref for {self.registry_repo}: {self.registry_commit}")

        if self.dirty:
            self.repo_sha = None
            self.dump_status()

    def check_repo_validity(
        self,
        repo_url: str,
        path: pathlib.Path,
        expected_commit: str,
        head_ref: str = "main",
    ) -> str:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.check_output(
                shlex.split(f'git clone --branch {head_ref} {repo_url} "{path}"')
            )
            self.dirty = True
        else:
            current_ref = subprocess.check_output(
                shlex.split(f'git -C "{path}" branch --show-current')
            )
            if current_ref.decode().strip() != head_ref:
                subprocess.check_output(shlex.split(f'git -C "{path}" fetch'))
                subprocess.check_output(
                    shlex.split(f'git -C "{path}" checkout {head_ref}')
                )
            subprocess.check_output(shlex.split(f'git -C "{path}" pull --force'))

        commit = get_ref(path, head_ref)
        if commit != expected_commit:
            self.dirty = True
        return commit

    def calculate_sha(self) -> str:
        sha512 = hashlib.sha512()

        response = requests.get(
            f"https://github.com/{self.framework_repo}/archive/{self.framework_commit}.tar.gz",
            stream=True,
        )
        total_size = int(response.headers.get("content-length", 0))
        block_size = 2048

        with tqdm(
            desc="Downloading ref for sha calculation",
            total=total_size,
            unit="B",
            unit_scale=True,
        ) as progress_bar:
            for data in response.iter_content(block_size):
                progress_bar.update(len(data))
                sha512.update(data)

        sha = sha512.hexdigest()
        logging.debug(f"Got sha for {self.framework_repo}: {sha}")
        return sha

    def to_details(
        self,
        subdirectory: str = "",
    ) -> GitDetails:
        if self.dirty or self.repo_sha is None:
            logging.info("Unknown commit hash, calculating sha512 hash of repo")
            self.repo_sha = self.calculate_sha()
            self.dump_status()

        return GitDetails(
            repo=self.framework_repo,
            ref=self.framework_commit,
            head_ref=self.branch,
            sha=self.repo_sha,
            subdirectory=subdirectory,  # This will be filled out for each port
        )

    def get_version(self, subdirectory: str) -> str:
        version_file_path = (
            pathlib.Path(self.data_folder)
            / "repo"
            / "framework"
            / subdirectory
            / "version.txt"
        )
        if version_file_path.is_file():
            with open(version_file_path, "r") as version_file:
                return version_file.read().strip()
        logging.warning(f"Could not find version file at {version_file_path}")
        return "0.0.0"
