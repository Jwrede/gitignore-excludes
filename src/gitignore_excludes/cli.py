"""gitignore-excludes: print an exclude file listing everything Git ignores below the given directories."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .formats import render
from .scan import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gitignore-excludes",
        description="List the paths Git ignores below ROOT (in every Git working tree found there) as an "
        "exclude file for restic (--exclude-file) or borg (--exclude-from).",
    )
    parser.add_argument("roots", nargs="+", metavar="ROOT", help="directory to scan; pass the same paths you back up")
    parser.add_argument("-f", "--format", choices=["restic", "borg", "plain"], default="restic")
    parser.add_argument(
        "-k",
        "--keep",
        action="append",
        default=[],
        metavar="GLOB",
        help="never exclude ignored files or directories whose name matches GLOB, e.g. '.env*' (repeatable)",
    )
    parser.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout")
    parser.add_argument("-z", "--null", action="store_true", help="plain format: separate paths with NUL")
    parser.add_argument("-q", "--quiet", action="store_true", help="no summary on stderr")
    parser.add_argument("--git", default=os.environ.get("GIT", "git"), help="git executable (default: git)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    result = scan(args.roots, keep=args.keep, git=args.git)
    data = os.fsencode(render(result.paths, args.format, args.null))
    if args.output:
        tmp = args.output + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, args.output)
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()

    for warning in result.warnings:
        print(f"gitignore-excludes: warning: {warning}", file=sys.stderr)
    if not args.quiet:
        print(
            f"gitignore-excludes: {len(result.repos)} working trees, {len(result.paths)} ignored paths",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
