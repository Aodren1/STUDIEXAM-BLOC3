"""Identifiants SQL : la liste blanche refuse toute tentative d'injection par un nom de table ou de colonne."""

from __future__ import annotations

import pytest

from fusion.sqlsafe import UnsafeIdentifierError, ident, idents, insert_sql


@pytest.mark.parametrize("name", ["AB_CLIENT", "client_uid", "_t1"])
def test_identifiants_valides(name):
    assert ident(name) == name


@pytest.mark.parametrize(
    "name",
    ["AB_CLIENT; DROP TABLE AB_CLIENT", "a b", "1abc", "", "x" * 65, "t--", 'a"b', None],
)
def test_identifiants_refuses(name):
    with pytest.raises(UnsafeIdentifierError):
        ident(name)


def test_liste_blanche():
    assert idents(["a", "b"], {"a", "b"}) == ["a", "b"]
    with pytest.raises(UnsafeIdentifierError, match="non autorisé"):
        ident("AP_USERS", {"AB_CLIENT"})


def test_insert_parametre():
    sql = insert_sql("client", ["a", "b"], {"client"}, {"a", "b"})
    assert sql == "INSERT INTO client (a, b) VALUES (?, ?)"
    with pytest.raises(UnsafeIdentifierError):
        insert_sql("client", ["a) VALUES (1); --"], {"client"})
