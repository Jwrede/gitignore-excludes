from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gitignore_excludes.cli import main
from gitignore_excludes.formats import borg_line, render, restic_line
from gitignore_excludes.scan import scan

# Files whose names need escaping in restic or borg patterns. All match `*.log` or `*.bak*`.
WEIRD = ["a*b.log", "q?.log", "x$HOME.log", "[x].log", "back\\slash.log", "t.bak ", " lead.log"]

FILES = {
    "notes.txt": "",
    "other/build/out.o": "",  # not in a repo: never excluded
    "proj/.gitignore": "node_modules/\n*.log\n!keep.log\nbuild/\n.env\nsecret/\n*.bak*\n",
    "proj/src/main.py": "",
    "proj/app.log": "",
    "proj/keep.log": "",
    "proj/tracked.log": "",  # tracked with -f: never excluded
    "proj/node_modules/pkg/index.js": "",
    "proj/build/out.o": "",
    "proj/.env": "",
    "proj/secret/.env.local": "",
    "proj/secret/key.bin": "",
    "proj/sub/.gitignore": "*.tmp\n",
    "proj/sub/x.tmp": "",
    "proj/sub/y.txt": "",
    "proj/nested/.gitignore": "dist/\n",
    "proj/nested/lib.py": "",
    "proj/nested/dist/lib.whl": "",
    "proj/weird/README": "",  # tracked, so git lists the ignored names one by one
    **{f"proj/weird/{name}": "" for name in WEIRD},
}

IGNORED = {
    "proj/app.log",
    "proj/node_modules",
    "proj/build",
    "proj/.env",
    "proj/secret",
    "proj/sub/x.tmp",
    "proj/nested/dist",
    *(f"proj/weird/{name}" for name in WEIRD),
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True
    )


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Keep the user's global excludesFile out of the tests.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "root"
    for rel, content in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for repo in ("proj", "proj/nested"):
        _git(root / repo, "init", "-q")
    _git(root / "proj", "add", "-f", "tracked.log")
    _git(root / "proj", "add", ".gitignore", "src", "sub", "keep.log", "weird/README")
    _git(root / "proj", "commit", "-qm", "init")
    return root


def _rel(root: Path, paths: list[str]) -> set[str]:
    return {os.path.relpath(p, root) for p in paths}


def test_scan_reports_exactly_what_git_ignores(tree: Path) -> None:
    result = scan([str(tree)])
    assert _rel(tree, result.paths) == IGNORED
    assert _rel(tree, result.repos) == {"proj", "proj/nested"}
    assert result.warnings == []


def test_ignored_directories_are_not_descended(tree: Path) -> None:
    # A repository inside an ignored directory belongs to the ignored directory.
    _git(tree / "proj" / "node_modules" / "pkg", "init", "-q")
    result = scan([str(tree)])
    assert "proj/node_modules/pkg" not in _rel(tree, result.repos)
    assert _rel(tree, result.paths) == IGNORED


def test_keep_splits_ignored_directories(tree: Path) -> None:
    result = scan([str(tree)], keep=[".env*"])
    expected = (IGNORED - {"proj/.env", "proj/secret"}) | {"proj/secret/key.bin"}
    assert _rel(tree, result.paths) == expected


def test_root_inside_a_work_tree(tree: Path) -> None:
    result = scan([str(tree / "proj" / "sub")])
    assert _rel(tree, result.paths) == {"proj/sub/x.tmp"}


def test_missing_root_is_a_warning(tree: Path) -> None:
    result = scan([str(tree / "nope")])
    assert result.paths == [] and "not a directory" in result.warnings[0]


def test_restic_escaping() -> None:
    assert restic_line("/a/b c/d") == "/a/b c/d"
    assert restic_line("/a/x*y?[z]\\w") == "/a/x\\*y\\?\\[z]\\\\w"
    assert restic_line("/a/$HOME") == "/a/$$HOME"
    assert restic_line("/a/t ") == "/a/t[ ]"
    assert restic_line("/a/l\nm") == "/a/l?m"


def test_borg_escaping() -> None:
    assert borg_line("/a/x*y") == "pp:/a/x*y"
    assert borg_line("/a/t* ") == "fm:/a/t[*][ ]"


def test_render_plain_null() -> None:
    assert render(["/a", "/b"], "plain", null=True) == "/a\0/b\0"
    assert render(["/a"], "restic").splitlines()[1:] == ["/a"]


def test_cli_writes_output_file(tree: Path, tmp_path: Path) -> None:
    out = tmp_path / "excludes.txt"
    assert main([str(tree), "-o", str(out), "-q"]) == 0
    lines = out.read_text().splitlines()
    assert lines[0].startswith("#")
    assert len(lines) == 1 + len(IGNORED)


# End-to-end: back up the tree with the real tools and compare what landed in the snapshot.


def _expected_files(tree: Path, ignored: set[str]) -> set[str]:
    out = set()
    for dirpath, dirs, files in os.walk(tree):
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), tree)
            if ".git" in Path(rel).parts:
                continue
            if any(rel == i or rel.startswith(i + "/") for i in ignored):
                continue
            out.add(rel)
    return out


@pytest.mark.skipif(shutil.which("restic") is None, reason="restic not installed")
@pytest.mark.parametrize("keep", [[], [".env*"]])
def test_restic_backup(tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep: list[str]) -> None:
    monkeypatch.setenv("RESTIC_PASSWORD", "test")
    monkeypatch.setenv("RESTIC_REPOSITORY", str(tmp_path / "repo"))
    monkeypatch.setenv("RESTIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", "/should-not-be-expanded")
    excludes = tmp_path / "ex.txt"
    main([str(tree), "-o", str(excludes), "-q", *[arg for k in keep for arg in ("--keep", k)]])
    run = lambda *a: subprocess.run(["restic", "-q", *a], check=True, capture_output=True, text=True).stdout
    run("init")
    run("backup", str(tree), "--exclude-file", str(excludes))
    listed = {line for line in run("ls", "latest").splitlines() if line.startswith(str(tree) + "/")}
    files = {os.path.relpath(p, tree) for p in listed if os.path.isfile(p)}
    files = {f for f in files if ".git" not in Path(f).parts}
    ignored = scan([str(tree)], keep=keep).paths
    assert files == _expected_files(tree, _rel(tree, ignored))
    if keep:
        assert {"proj/.env", "proj/secret/.env.local"} <= files


@pytest.mark.skipif(shutil.which("borg") is None, reason="borg not installed")
def test_borg_backup(tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BORG_BASE_DIR", str(tmp_path / "borgbase"))
    monkeypatch.setenv("BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK", "yes")
    repo = tmp_path / "borgrepo"
    excludes = tmp_path / "ex.txt"
    main([str(tree), "-f", "borg", "-o", str(excludes), "-q"])
    run = lambda *a: subprocess.run(["borg", *a], check=True, capture_output=True, text=True).stdout
    run("init", "-e", "none", str(repo))
    run("create", "--exclude-from", str(excludes), f"{repo}::a", str(tree))
    listed = {"/" + line for line in run("list", "--short", f"{repo}::a").splitlines()}
    files = {os.path.relpath(p, tree) for p in listed if p.startswith(str(tree) + "/") and os.path.isfile(p)}
    files = {f for f in files if ".git" not in Path(f).parts}
    assert files == _expected_files(tree, IGNORED)
