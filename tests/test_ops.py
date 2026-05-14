from __future__ import annotations

import subprocess
from pathlib import Path

from classroom_repos.config import Config, PairSyncConfig
from classroom_repos.ops import (
    clean_repositories,
    commit_repositories,
    git_status,
    pair_summaries,
    run_command,
    select_repositories,
)
from classroom_repos.pair_sync import discover_pairs, update_pair


def test_select_repositories_by_scope(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided = init_repo(tmp_path / "project")
    solution = init_repo(tmp_path / "project-solution")

    assert select_repositories(config) == [provided.resolve(), solution.resolve()]
    assert select_repositories(config, scope="provided") == [provided.resolve()]
    assert select_repositories(config, scope="solution") == [solution.resolve()]
    assert select_repositories(config, scope="pair", pair_name="project") == [provided.resolve(), solution.resolve()]


def test_run_command_dry_run_does_not_execute(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")

    results = run_command(config, ["touch", "created.txt"], scope="all", pair_name=None, repo=None, apply=False)

    assert results[0].status == "would_run"
    assert not (repo / "created.txt").exists()


def test_run_command_apply_executes_in_each_repo(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")

    results = run_command(config, ["git", "status", "--short"], scope="all", pair_name=None, repo=None, apply=True)

    assert results[0].repo == repo.resolve()
    assert results[0].returncode == 0


def test_git_status_reports_dirty_repo(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    write(repo / "file.txt", "changed\n")

    status = git_status(repo)

    assert status.valid
    assert status.dirty
    assert status.untracked == 1


def test_commit_repositories_commits_dirty_and_skips_clean(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    dirty = init_repo(tmp_path / "dirty")
    clean = init_repo(tmp_path / "clean")
    commit_all(clean)
    write(dirty / "file.txt", "changed\n")

    results = commit_repositories(config, message="batch commit", scope="all", pair_name=None, repo=None)

    by_name = {result.repo.name: result for result in results}
    assert by_name["dirty"].status == "committed"
    assert by_name["dirty"].commit_hash
    assert by_name["clean"].status == "skipped"
    assert "batch commit" in git_log(dirty)


def test_pair_summaries_compacts_pair_check_status(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    provided = init_repo(tmp_path / "project")
    solution = init_repo(tmp_path / "project-solution")
    write(solution / "README.md", "solution\n")
    write(provided / "README.md", "old\n")
    commit_all(solution)
    commit_all(provided)

    summary = pair_summaries(config)[0]

    assert summary.name == "project"
    assert summary.status == "uninitialized"
    assert summary.issue_count == 1

    update_pair(config, discover_pairs(config)[0], apply=True)
    commit_all(provided)
    assert pair_summaries(config)[0].status == "ok"


def test_clean_repositories_dry_run_does_not_remove_artifacts(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".DS_Store", "finder\n")
    write(repo / "main.o", "object\n")
    write(repo / "__pycache__/module.pyc", "cache\n")

    results = clean_repositories(config, scope="all", pair_name=None, repo=None, apply=False)

    assert {result.path for result in results if result.status == "would_remove"} == {
        ".DS_Store",
        "__pycache__",
        "main.o",
    }
    assert (repo / ".DS_Store").exists()
    assert (repo / "main.o").exists()
    assert (repo / "__pycache__/module.pyc").exists()


def test_clean_repositories_apply_removes_untracked_artifacts(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".DS_Store", "finder\n")
    write(repo / "build/output.o", "object\n")

    results = clean_repositories(config, scope="all", pair_name=None, repo=None, apply=True)

    assert any(result.status == "removed" and result.path == ".DS_Store" for result in results)
    assert any(result.status == "removed" and result.path == "build" for result in results)
    assert not (repo / ".DS_Store").exists()
    assert not (repo / "build").exists()


def test_clean_repositories_skips_tracked_artifacts(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = init_repo(tmp_path / "repo")
    write(repo / ".DS_Store", "tracked for some reason\n")
    subprocess.run(["git", "add", "-f", ".DS_Store"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "track ds store",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    results = clean_repositories(config, scope="all", pair_name=None, repo=None, apply=True)

    assert results[0].status == "skipped_tracked"
    assert (repo / ".DS_Store").exists()


def make_config(tmp_path: Path) -> Config:
    return Config(
        path=tmp_path / "classroom-repos.yml",
        repo_roots=(tmp_path,),
        template_root=tmp_path / "templates",
        managed_files=(".gitignore",),
        pair_sync=PairSyncConfig(paths=("README.md",)),
    )


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    configure_git_user(path)
    return path.resolve()


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
            "--allow-empty",
            "-m",
            "commit",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def configure_git_user(repo: Path) -> None:
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)


def git_log(repo: Path) -> str:
    result = subprocess.run(
        ["git", "log", "--oneline", "--max-count=1"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
