import { test } from 'node:test';
import assert from 'node:assert/strict';
import { load } from './load.mjs';

const { PowerCuts } = load('utils.js', 'powercuts.js')('PowerCuts');
const { describe, districts } = PowerCuts._internals;

test("NGED's categories read as words", () => {
  assert.equal(describe('HV OVERHEAD'), 'High voltage overhead line');
  assert.equal(describe('LV UNDERGROUND'), 'Low voltage underground cable');
  assert.equal(describe('LV GENERIC'), 'Low voltage');
  assert.equal(describe('LV FUSE'), 'Low voltage fuse');
  assert.equal(describe('HV SOMETHING NEW'), 'High voltage something new');
  assert.equal(describe('STORM'), 'Storm');
  assert.equal(describe(''), '');
});

test('postcodes are grouped by district', () => {
  assert.equal(districts(['PL13 2DN', 'PL13 2DJ', 'PL14 1AA']), 'PL13 (2 postcodes), PL14 (1 postcode)');
  assert.equal(districts([]), '');
});

test('a power cut is named after its nearest place', () => {
  assert.equal(PowerCuts.title({ place: 'Looe' }), 'Power cut near Looe');
  assert.equal(PowerCuts.title({}), 'Power cut');
});

test('the tooltip gives the customers off and the estimate', () => {
  const now = Date.UTC(2026, 10, 2, 15) / 1000;
  assert.equal(PowerCuts.summary({ off: 109, etr: now + 9000 }, now), '109 customers off · back on by 17:30');
  assert.equal(PowerCuts.summary({ off: 1, etr: null }, now), '1 customer off · no estimate yet');
});
