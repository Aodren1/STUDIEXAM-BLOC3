// Tests unitaires du formatage côté navigateur (node --test), sans dépendance.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const Fmt = createRequire(import.meta.url)('../../fusion/web/static/js/format.js');
const NBSP = ' ';

test('milliers séparés par une espace insécable (exigence de l’énoncé : 1 000)', () => {
  assert.equal(Fmt.int(1000), `1${NBSP}000`);
  assert.equal(Fmt.int(1234567.4), `1${NBSP}234${NBSP}567`);
  assert.ok(!Fmt.int(6000).includes(' '));
});

test('pourcentages et décimales à la française', () => {
  assert.equal(Fmt.pct(53.64), `53,6${NBSP}%`);
  assert.equal(Fmt.dec(6.55, 2), '6,55');
});

test('montants compacts', () => {
  assert.equal(Fmt.eurCompact(8203116), `8,2${NBSP}M€`);
  assert.equal(Fmt.eurCompact(43120), `43${NBSP}k€`);
  assert.equal(Fmt.eurCompact(950), `950${NBSP}€`);
  assert.equal(Fmt.eur(1234.4), `1${NBSP}234${NBSP}€`);
});

test('valeurs absentes', () => {
  for (const f of [Fmt.int, Fmt.dec, Fmt.pct, Fmt.eur, Fmt.eurCompact]) assert.equal(f(null), '—');
  assert.equal(Fmt.date(null), '—');
});

test('dates et mois', () => {
  assert.equal(Fmt.date('2026-10-31'), '31 octobre 2026');
  assert.match(Fmt.month('2026-01'), /janv\.? 26/);
});
