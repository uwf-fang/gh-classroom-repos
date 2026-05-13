from __future__ import annotations

import subprocess
from pathlib import Path

from classroom_repos.config import CheckedFileRule, Config
from classroom_repos.core import check_repository, discover_repositories, update_repository


def test_discover_repositories_finds_git_repos(tmp_path: Path) -> None:
    repo = tmp_path / "repos" / "assignment-1"
    init_repo(repo)

    assert discover_repositories((tmp_path / "repos",)) == [repo.resolve()]


def test_discover_repositories_prefers_child_repos_when_root_is_git_repo(tmp_path: Path) -> None:
    root = init_repo(tmp_path / "repos")
    assignment_1 = init_repo(root / "assignment-1")
    assignment_2 = init_repo(root / "assignment-2")

    assert discover_repositories((root,)) == [assignment_1.resolve(), assignment_2.resolve()]


def test_discover_repositories_falls_back_to_root_repo_when_no_child_repos(tmp_path: Path) -> None:
    root = init_repo(tmp_path / "single-repo")

    assert discover_repositories((root,)) == [root.resolve()]


def test_check_reports_managed_file_mismatch_and_checked_pattern(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".gitignore", "old\n")
    write(repo / ".gitattributes", "* text=auto\n")
    write(repo / ".github/workflows/classroom.yml", "workflow\n")
    write(repo / ".github/classroom/autograding.json", "{}\n")
    write(repo / "README.md", "# Assignment\n")
    write(repo / "Makefile", "build:\n\ttrue\n")
    write(repo / "test/main.cpp", "int main() {}\n")

    result = check_repository(config, repo)

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"content_mismatch", "missing_required_pattern"}


def test_update_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".gitignore", "old\n")
    commit_all(repo)

    result = update_repository(config, repo, apply=False)

    assert result.actions[0].status == "would_update"
    assert (repo / ".gitignore").read_text() == "old\n"


def test_update_apply_copies_managed_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".gitignore", "old\n")
    commit_all(repo)

    result = update_repository(config, repo, apply=True)

    assert result.actions[0].status == "updated"
    assert (repo / ".gitignore").read_text() == "template ignore\n"


def test_update_skips_dirty_repo(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".gitignore", "old\n")
    write(repo / "untracked.txt", "dirty\n")

    result = update_repository(config, repo, apply=True)

    assert result.skipped_reason == "repository has uncommitted changes"
    assert (repo / ".gitignore").read_text() == "old\n"


def test_check_reports_missing_required_glob(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    copy_templates(config, repo)
    write(repo / "README.md", "## Build\n## Test\n")
    write(repo / "Makefile", "build:\n\ntest:\n")
    (repo / "test").mkdir()

    result = check_repository(config, repo)

    assert [issue.code for issue in result.issues] == ["missing_required_glob"]


def make_config(tmp_path: Path) -> Config:
    template_root = tmp_path / "templates"
    write(template_root / ".gitignore", "template ignore\n")
    write(template_root / ".gitattributes", "* text=auto\n")
    write(template_root / ".github/workflows/classroom.yml", "workflow\n")
    write(template_root / ".github/classroom/autograding.json", "{}\n")
    return Config(
        path=tmp_path / "classroom-repos.yml",
        repo_roots=(tmp_path,),
        template_root=template_root,
        managed_files=(
            ".gitignore",
            ".gitattributes",
            ".github/workflows/classroom.yml",
            ".github/classroom/autograding.json",
        ),
        checked_files=(
            CheckedFileRule("README.md", required_patterns=("## Build", "## Test")),
            CheckedFileRule("Makefile", required_patterns=("^build:", "^test:")),
            CheckedFileRule("test", kind="directory", required_globs=("*.cpp",)),
        ),
    )


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_templates(config: Config, repo: Path) -> None:
    for relative in config.managed_files:
        source = config.template_root / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
