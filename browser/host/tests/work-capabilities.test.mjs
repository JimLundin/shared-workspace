import assert from 'node:assert/strict';
import { test } from 'node:test';
import { requireMeetingWorkSupport, workCapability } from '../src/types.ts';

function signed(type) {
  return { version: 1, operation: { id: 'operation', workspace_id: 'meeting', author: 'origin', parents: [], action: { type } }, signature: 'fixture' };
}

test('work transfers require support for every work operation, including mixed history', () => {
  for (const type of ['create_work', 'edit_work', 'publish_meeting_materials']) {
    const operations = [signed('create_note'), signed(type), signed('edit_note')];
    assert.throws(() => requireMeetingWorkSupport(operations, undefined), /Update Shared Workspace/, `legacy transfer must reject ${type}`);
    assert.doesNotThrow(() => requireMeetingWorkSupport(operations, 1));
  }
});

test('legacy transfers retain note and surface compatibility without a work declaration', () => {
  assert.doesNotThrow(() => requireMeetingWorkSupport([], undefined));
  assert.doesNotThrow(() => requireMeetingWorkSupport([
    signed('create_workspace'), signed('grant'), signed('create_note'), signed('edit_note'), signed('create_surface'), signed('edit_surface'),
  ], undefined));
});

test('only the current work version or an absent declaration enters a typed transfer', () => {
  assert.equal(workCapability(undefined), undefined);
  assert.equal(workCapability(1), 1);
  for (const value of [null, false, true, 0, 2, '1', { work: 1 }, []]) {
    assert.throws(() => workCapability(value), /Unsupported meeting work capability/);
  }
});
