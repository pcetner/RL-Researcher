"""Point this repository's git hooks at the ones the package ships.

    python -m rl_researcher.install_hooks [--repo DIR] [--uninstall] [--dry-run]

It sets ``core.hooksPath`` rather than copying files into ``.git/hooks``. Copies go stale: the
hook a person is running is then a version of the hook from whenever they last installed it, and
nobody finds out. A path means the hook in the working tree is the hook that runs, so it is
reviewed like everything else and updates with a pull.

``.git/hooks`` is not shared by git, which is why every project ends up with a hook one person
has and the others do not. This is that problem's fix, and it is one line of config.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

HOOKS_DIR = ".githooks"
KEY = "core.hooksPath"


def package_hooks() -> Path:
    """Where the shipped hooks live, whether this is a checkout or an installed package."""
    return Path(__file__).resolve().parent.parent / HOOKS_DIR


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          check=False)


def find_repo(start: Optional[Path] = None) -> Optional[Path]:
    done = git(Path(start or Path.cwd()), "rev-parse", "--show-toplevel")
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", default=None, help="the repository (default: the one you are in)")
    p.add_argument("--uninstall", action="store_true", help=f"unset {KEY}")
    p.add_argument("--dry-run", action="store_true", help="say what would change")
    a = p.parse_args(argv)

    repo = Path(a.repo).resolve() if a.repo else find_repo()
    if repo is None:
        print("not inside a git repository; pass --repo")
        return 1

    if a.uninstall:
        if a.dry_run:
            print(f"would unset {KEY} in {repo}")
            return 0
        git(repo, "config", "--unset", KEY)
        print(f"unset {KEY} in {repo}; the hooks in .git/hooks apply again")
        return 0

    # A project's own hooks win: it may have more than the package ships, and a package that
    # silently replaced them would be taking away a check rather than adding one.
    own = repo / HOOKS_DIR
    hooks = own if (own / "pre-commit").is_file() else package_hooks()
    if not (hooks / "pre-commit").is_file():
        print(f"no pre-commit hook at {hooks}")
        return 1

    rel = hooks
    try:
        rel = hooks.relative_to(repo)
    except ValueError:
        pass                                   # an installed package: an absolute path it is
    if a.dry_run:
        print(f"would set {KEY} = {rel} in {repo}")
        return 0
    done = git(repo, "config", KEY, str(rel).replace("\\", "/"))
    if done.returncode != 0:
        print(f"git config failed: {done.stderr.strip()}")
        return 1
    print(f"{KEY} = {str(rel).replace(chr(92), '/')} in {repo}")
    print(f"  the hook is {hooks / 'pre-commit'}, and it is the file in the tree — not a copy")
    print("  it refuses a commit that stages a path under a run whose lock is alive (C06)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
