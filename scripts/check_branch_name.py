"""Vérifie que le nom de la branche courante respecte la convention de CONTRIBUTING.md.

Usage : python scripts/check_branch_name.py [nom-de-branche]
Sans argument, lit GITHUB_HEAD_REF (PR), puis GITHUB_REF_NAME (push), puis `git branch --show-current`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

PATTERN = re.compile(
    r"^(main|develop)$"
    r"|^(feature|fix|hotfix|docs|ci|chore|test|refactor)/FUS-[0-9]+-[a-z0-9]+(-[a-z0-9]+){1,5}$"
    r"|^release/[0-9]+\.[0-9]+\.[0-9]+$"
)


def is_valid(branch: str) -> bool:
    """Indique si `branch` respecte la convention de nommage."""
    return bool(PATTERN.match(branch))


def current_branch() -> str:
    for var in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        if os.environ.get(var):
            return os.environ[var]
    out = subprocess.run(  # noqa: S603 - commande fixe, sans entrée utilisateur
        ["git", "branch", "--show-current"], capture_output=True, text=True, check=True  # noqa: S607
    )
    return out.stdout.strip()


def main(argv: list[str]) -> int:
    branch = argv[1] if len(argv) > 1 else current_branch()
    if branch.startswith("v") and re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+$", branch):
        print(f"tag {branch} : contrôle de branche non applicable")
        return 0
    if is_valid(branch):
        print(f"branche « {branch} » : conforme")
        return 0
    print(f"branche « {branch} » : NON conforme à <type>/FUS-<n>-<description> (voir CONTRIBUTING.md)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
