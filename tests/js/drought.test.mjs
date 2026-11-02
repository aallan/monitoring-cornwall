import { test } from 'node:test';
import assert from 'node:assert/strict';
import { load } from './load.mjs';

const { Drought } = load('utils.js', 'drought.js')('Drought');
const { worst, pillText } = Drought._internals;

const SEVERE = { stage: 'Severe drought', text: 'Parts of Devon and Cornwall …' };
const DROUGHT = { stage: 'Drought', text: 'Remaining parts of Devon, Cornwall and Isles of Scilly …' };

test('the pill shows the most severe stage', () => {
  assert.equal(worst([DROUGHT, SEVERE]), 'Severe drought');
  assert.equal(worst([{ stage: 'Recovery', text: '' }]), 'Recovery');
  assert.equal(worst([]), null);
  assert.equal(worst([{ stage: 'Something new', text: '' }]), null);
});

test("it says 'in parts' when the county is split between stages", () => {
  assert.equal(pillText([SEVERE, DROUGHT]), 'Severe drought in parts');
  assert.equal(pillText([DROUGHT]), 'Drought');
  assert.equal(pillText([]), null);
});
