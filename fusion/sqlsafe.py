"""Identifiants SQL sûrs.

Les noms de tables et de colonnes ne peuvent pas être passés en paramètres liés (`?`) : ils sont interpolés.
Ils proviennent toujours de constantes du code ou du mapping validé, jamais d'une saisie ; par défense en
profondeur, tout identifiant interpolé passe par `ident()`, qui refuse ce qui n'est pas un nom SQL simple ou
ce qui n'appartient pas à la liste blanche fournie.
"""

from __future__ import annotations

import re
from collections.abc import Collection

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class UnsafeIdentifierError(ValueError):
    """Identifiant SQL refusé (forme invalide ou absent de la liste blanche)."""


def ident(name: str, allowed: Collection[str] | None = None) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise UnsafeIdentifierError(f"identifiant SQL invalide : {name!r}")
    if allowed is not None and name not in allowed:
        raise UnsafeIdentifierError(f"identifiant SQL non autorisé : {name!r}")
    return name


def idents(names: Collection[str], allowed: Collection[str] | None = None) -> list[str]:
    return [ident(n, allowed) for n in names]


def insert_sql(
    table: str,
    columns: Collection[str],
    allowed_tables: Collection[str],
    allowed_columns: Collection[str] | None = None,
) -> str:
    """Requête INSERT paramétrée : identifiants en liste blanche, valeurs toujours liées par « ? »."""
    cols = ", ".join(idents(columns, allowed_columns))
    marks = ", ".join("?" * len(columns))
    return f"INSERT INTO {ident(table, allowed_tables)} ({cols}) VALUES ({marks})"  # nosec B608  # noqa: S608
