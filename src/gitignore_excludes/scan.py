"""Find the paths Git ignores below one or more directories."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field


@dataclass
class ScanResult:
    paths: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def git_ignored(directory: str, git: str = "git") -> list[tuple[str, bool]]:
    """Return (absolute path, is_dir) for every ignored, untracked entry below `directory`.

    Uses `git ls-files --others --ignored --exclude-standard --directory`, so the rules are exactly
    Git's: nested .gitignore files, negation, .git/info/exclude and core.excludesFile. A directory
    whose whole content is ignored is reported once instead of file by file. Tracked files are never
    reported, even if a pattern matches them.
    """
    proc = subprocess.run(
        [git, "-C", directory, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(os.fsdecode(proc.stderr).strip() or f"git exited with {proc.returncode}")
    out = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        is_dir = rel.endswith("/")
        out.append((os.path.join(directory, rel.rstrip("/")), is_dir))
    return out


def _is_repo_root(directory: str) -> bool:
    return os.path.lexists(os.path.join(directory, ".git"))


def _inside_work_tree(directory: str, git: str) -> bool:
    proc = subprocess.run(
        [git, "-C", directory, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _matches(name: str, keep: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in keep)


def _contains_kept(directory: str, keep: list[str]) -> bool:
    for _, dirs, files in os.walk(directory):
        if any(_matches(name, keep) for name in dirs + files):
            return True
    return False


def _exclude_entries(path: str, is_dir: bool, keep: list[str]) -> list[str]:
    """Paths to exclude for one ignored entry, leaving out anything that matches a keep pattern.

    An ignored directory that contains a kept file is not excluded as a whole; its other children
    are excluded one by one instead.
    """
    if keep and _matches(os.path.basename(path), keep):
        return []
    if not is_dir or not keep or not _contains_kept(path, keep):
        return [path]
    out = []
    with os.scandir(path) as it:
        for entry in sorted(it, key=lambda e: e.name):
            out += _exclude_entries(entry.path, entry.is_dir(follow_symlinks=False), keep)
    return out


def scan(roots: list[str], keep: list[str] | None = None, git: str = "git") -> ScanResult:
    """Walk `roots`, ask Git in every working tree found, and collect the ignored paths.

    Directories that Git ignores are not descended into, so `node_modules` and build trees cost one
    entry each. Symlinks are not followed.
    """
    keep = keep or []
    result = ScanResult()
    excluded: set[str] = set()

    def visit(directory: str, force_git: bool) -> None:
        if force_git or _is_repo_root(directory):
            try:
                entries = git_ignored(directory, git)
            except RuntimeError as e:
                result.warnings.append(f"{directory}: {e}")
                entries = []
            else:
                result.repos.append(directory)
            for path, is_dir in entries:
                if path in excluded:
                    continue
                excluded.add(path)
                result.paths += _exclude_entries(path, is_dir, keep)
        try:
            with os.scandir(directory) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as e:
            result.warnings.append(f"{directory}: {e.strerror}")
            return
        for entry in children:
            if entry.name == ".git" or not entry.is_dir(follow_symlinks=False) or entry.path in excluded:
                continue
            visit(entry.path, False)

    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            result.warnings.append(f"{root}: not a directory")
            continue
        visit(root, not _is_repo_root(root) and _inside_work_tree(root, git))
    result.paths = sorted(set(result.paths))
    return result
