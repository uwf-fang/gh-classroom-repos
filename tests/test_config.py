from __future__ import annotations

from pathlib import Path

import pytest

from classroom_repos.config import load_config


def test_load_config_defaults_repo_roots_to_current_working_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "classroom-repos.yml"
    config_path.write_text(
        """
managed_files:
  - .gitignore
""",
        encoding="utf-8",
    )
    workdir = tmp_path / "repos"
    workdir.mkdir()

    config = load_config(config_path, default_repo_root=workdir)

    assert config.repo_roots == (workdir.resolve(),)


def test_load_config_allows_explicit_repo_roots(tmp_path: Path) -> None:
    config_path = tmp_path / "classroom-repos.yml"
    config_path.write_text(
        """
repo_roots:
  - repos
managed_files:
  - .gitignore
""",
        encoding="utf-8",
    )

    config = load_config(config_path, default_repo_root=tmp_path / "ignored")

    assert config.repo_roots == ((tmp_path / "repos").resolve(),)


def test_load_config_rejects_non_list_repo_roots(tmp_path: Path) -> None:
    config_path = tmp_path / "classroom-repos.yml"
    config_path.write_text(
        """
repo_roots: repos
managed_files:
  - .gitignore
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="repo_roots"):
        load_config(config_path)


def test_load_config_accepts_deprecated_same_and_similar_file_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "classroom-repos.yml"
    config_path.write_text(
        """
same_files:
  - .gitignore
similar_files:
  - README.md
""",
        encoding="utf-8",
    )

    config = load_config(config_path, default_repo_root=tmp_path)

    assert config.managed_files == (".gitignore",)
    assert [rule.path for rule in config.checked_files] == ["README.md"]
