from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from classroom_repos.cli import app

runner = CliRunner()


def test_init_creates_commented_starter_config(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["init"])
        config = Path("classroom-repos.yml")

        assert result.exit_code == 0
        assert config.exists()
        content = config.read_text(encoding="utf-8")
        assert "# Put this file in the directory that contains all student repository directories." in content
        assert "managed_files:" in content
        assert "checked_files:" in content
        assert "  - path: .github/classroom/autograding.json" in content
        assert "## Grading Information:" in content
        assert "^test-all:" in content


def test_init_refuses_to_overwrite_existing_config(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        config = Path("classroom-repos.yml")
        config.write_text("existing: true\n", encoding="utf-8")

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert config.read_text(encoding="utf-8") == "existing: true\n"


def test_init_force_overwrites_existing_config(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        config = Path("classroom-repos.yml")
        config.write_text("existing: true\n", encoding="utf-8")

        result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0
        assert "managed_files:" in config.read_text(encoding="utf-8")


def test_check_report_names_problem_repository_subdirectory(tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        write(Path("templates/.gitignore"), "template\n")
        write(
            Path("classroom-repos.yml"),
            """
template_root: ./templates
managed_files:
  - .gitignore
""",
        )
        repo = Path("student-assignment-1")
        init_repo(repo)
        write(repo / ".gitignore", "old\n")

        result = runner.invoke(app, ["check"])

        assert result.exit_code == 1
        assert "FAIL student-assignment-1" in result.output
        assert "student-assignment-1/.gitignore: content_mismatch" in result.output


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
