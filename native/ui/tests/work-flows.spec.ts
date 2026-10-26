/** Synthetic contract fixtures only: no live provider, peer or physical-device claims. */
import {test,expect,type Page} from '@playwright/test';
import type { WorkRevision } from '../src/protocol.ts';
async function start(page:Page){await page.goto('/tests/host.html');await page.getByLabel('Workspace name',{exact:true}).fill('Studio');await page.locator('#workspace-form button[type=submit]').click();await expect(page.locator('#workspace-picker')).toHaveAttribute('aria-label','Current workspace: Studio');}
async function add(page:Page){const button=page.locator('.capture-dock [data-action=add]');if(await button.isVisible())await button.click();else{await page.getByRole('button',{name:'Page actions',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Add to workspace',exact:true}).click();}}
async function transcript(page:Page){await add(page);await page.getByRole('dialog').getByRole('button',{name:/^Note /}).click();await page.getByLabel('Title',{exact:true}).fill('Tuesday transcript');await page.getByLabel('Content',{exact:true}).fill('Lea will share the revised identity on Friday.');await page.getByRole('button',{name:'Save note',exact:true}).click();}
async function draft(page:Page){await add(page);await page.getByRole('dialog').getByRole('button',{name:/^Meeting work /}).click();await expect(page.getByLabel('Work title',{exact:true})).toBeFocused();await page.getByLabel('Work title',{exact:true}).fill('Identity review');await page.getByLabel('Desired outcome',{exact:true}).fill('Prepare notes, actions and follow-up for review.');}
async function createWork(page:Page){await draft(page);await page.getByRole('checkbox',{name:'Tuesday transcript',exact:true}).check();await page.getByRole('button',{name:'Create work',exact:true}).click();await expect(page.locator('.meeting-work')).toBeVisible();}
async function prepared(page:Page){await start(page);await transcript(page);await createWork(page);await page.getByRole('button',{name:'Prepare meeting material',exact:true}).click();await expect(page.locator('.meeting-work')).toContainText('Material ready for review');}
async function work(page:Page){return page.evaluate(()=>window.fixture.resources().find(r=>r.heads.some(h=>'work' in h))!);}
async function touchGeometry(page:Page){
  // Passive renders replace these controls. Resolve and measure both in one browser turn.
  return page.evaluate(()=>{
    const bounds=(selector:string)=>{
      const visible=[...document.querySelectorAll(selector)].flatMap(element=>{
        if(!(element instanceof HTMLElement)||!element.isConnected||getComputedStyle(element).visibility!=='visible')return[];
        const rect=element.getBoundingClientRect();
        return element.getClientRects().length&&rect.width>0&&rect.height>0?[{x:rect.x,y:rect.y,width:rect.width,height:rect.height}]:[];
      });
      const [only]=visible;
      if(visible.length!==1||!only)throw new Error(`Expected one visible connected touch control: ${selector}`);
      return only;
    };
    return{n:bounds('#navigation-toggle'),d:bounds('.capture-dock')};
  });
}
async function touchGeometryDuringRefresh(page:Page){
  // Exercise the existing observation path while the journey samples its controls.
  const timer=await page.evaluate(()=>window.setInterval(()=>{void window.fixture.refresh()},0));
  try{return await touchGeometry(page)}
  finally{await page.evaluate(timer=>window.clearInterval(timer),timer)}
}

test('explicit preparation pins sources, publishes reviewed outputs and keeps user corrections',async({page})=>{
  await start(page);await transcript(page);await createWork(page);expect(await page.evaluate(()=>window.fixture.meetingRequests())).toHaveLength(0);
  const before=await work(page);await page.evaluate(()=>window.fixture.remoteEdit());await page.getByRole('button',{name:'Prepare meeting material',exact:true}).click();await expect(page.locator('.meeting-work')).toContainText('Material ready for review');
  expect(await page.evaluate(()=>window.fixture.meetingRequests())).toHaveLength(1);expect(await page.evaluate(()=>window.fixture.resources())).toHaveLength(4);
  await page.locator('.work-sources').getByRole('button',{name:'Tuesday transcript'}).click();await expect(page.locator('.pinned-source .note-body')).toHaveText('Lea will share the revised identity on Friday.');await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Correct owners or deadlines'}).click();await page.getByLabel('Owner',{exact:true}).fill('Jim');await page.getByLabel('Deadline',{exact:true}).fill('Next Tuesday');await page.getByRole('button',{name:'Save corrections'}).click();
  await expect(page.locator('.meeting-action')).toContainText('Jim · user correction');await expect(page.locator('.meeting-action')).toContainText('Next Tuesday · user correction');
  const after=(await work(page)).heads[0] as WorkRevision;expect(after.work.sources).toEqual((before.heads[0] as WorkRevision).work.sources);expect(after.work.state.type).toBe('materials_ready');if(after.work.state.type==='materials_ready')expect(after.work.state.actions[0]?.basis.type).toBe('transcript');
  await page.getByRole('button',{name:'Open and edit follow-up'}).click();await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByLabel('Content',{exact:true}).fill('Shortened by the user.');await page.getByRole('button',{name:'Save note',exact:true}).click();await page.locator('.workspace-navigation').getByRole('button',{name:'Identity review',exact:true}).click();await page.getByRole('button',{name:'Open and edit follow-up'}).click();await expect(page.locator('.note-body').first()).toHaveText('Shortened by the user.');
});

test('lost create receipt survives reopen and exact retry creates one work',async({page})=>{
  await start(page);await transcript(page);await draft(page);await page.getByRole('checkbox',{name:'Tuesday transcript',exact:true}).check();await page.evaluate(()=>window.fixture.loseReceipt());await page.getByRole('button',{name:'Create work',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('no confirmed receipt');
  const sent=await page.evaluate(()=>window.fixture.commands().at(-1));await page.evaluate(()=>window.fixture.restart());await expect(page.locator('.main')).toContainText('no confirmed receipt');expect(await page.evaluate(()=>window.fixture.meetingRequests())).toHaveLength(0);await page.getByRole('button',{name:'Check the same change',exact:true}).click();await expect(page.locator('.meeting-work')).toBeVisible();expect(await page.evaluate(()=>window.fixture.commands().at(-1))).toEqual(sent);expect(await page.evaluate(()=>window.fixture.resources().filter(r=>r.heads.some(h=>'work' in h)))).toHaveLength(1);await expect(page.getByRole('button',{name:'Continue work draft'})).toHaveCount(0);
});

test('transcript import splits UTF8 without truncation and rejects more than 16 chunks before writing',async({page})=>{
  await start(page);await draft(page);const text='😀文å'.repeat(17000);await page.locator('#transcript-file').setInputFiles({name:'transcript.txt',mimeType:'text/plain',buffer:Buffer.from(text)});await expect(page.getByRole('dialog')).toContainText('2 transcript parts imported');
  const notes=await page.evaluate(()=>window.fixture.resources().flatMap(r=>r.heads.flatMap(h=>'body' in h?[h.body]:[])));expect(notes.join('')).toBe(text);for(const note of notes)expect(Buffer.byteLength(note)).toBeLessThanOrEqual(128*1024);
  await page.locator('#transcript-file').setInputFiles({name:'too-large.txt',mimeType:'text/plain',buffer:Buffer.alloc(16*128*1024+1,'a')});await expect(page.getByRole('dialog')).toContainText('no text was imported');expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(2);
});

test('an Applied transcript stays pinned while a newer workspace snapshot is pending',async({page})=>{
  await start(page);await draft(page);
  await page.evaluate(()=>window.fixture.snapshots.holdAfterApplied());
  try{
    const input=page.getByLabel('Import transcript files',{exact:true});
    await input.setInputFiles({name:'recording.txt',mimeType:'text/plain',buffer:Buffer.from('Lea will share the revised identity on Friday.')});
    await expect.poll(()=>page.evaluate(()=>window.fixture.snapshots.pending())).toBe(1);
    await page.evaluate(()=>{void window.fixture.refresh()});
    expect(await page.evaluate(()=>window.fixture.snapshots.pending())).toBe(2);
    await page.evaluate(()=>window.fixture.snapshots.releaseFirst());
    await expect(input).toBeEnabled();
    const sources=page.getByRole('dialog',{name:'Meeting work',exact:true}).getByRole('checkbox');
    expect(await sources.count(),'APPLIED_TRANSCRIPT_NOT_PINNED_DURING_PENDING_REFRESH').toBe(1);
    await expect(sources).toBeChecked();
    expect(await page.evaluate(()=>window.fixture.snapshots.pending())).toBe(1);
    const imported=await page.evaluate(()=>window.fixture.commands().find(command=>command.type==='create_resource'));
    if(!imported||imported.type!=='create_resource')throw new Error('No transcript command was submitted');
    expect(imported.body).toBe('Lea will share the revised identity on Friday.');
    await page.evaluate(()=>window.fixture.snapshots.resume());
    await page.getByRole('button',{name:'Create work',exact:true}).click();
    await expect(page.locator('.meeting-work')).toBeVisible();
    await expect(page.locator('.work-sources').getByRole('button',{name:'recording.txt',exact:true})).toBeVisible();
    const request=await page.evaluate(()=>window.fixture.commands().find(command=>command.type==='create_work'));
    expect(request).toMatchObject({type:'create_work',sources:[{workspace_id:imported.workspace_id,resource_id:imported.resource_id,revision_id:imported.operation_id}]});
    expect(await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_resource'))).toHaveLength(1);
  }finally{await page.evaluate(()=>window.fixture.snapshots.resume())}
});

test('an unconfirmed transcript stops the batch and retains only its exact retry',async({page})=>{
  await start(page);await draft(page);await page.evaluate(()=>window.fixture.loseReceipt());
  await page.getByLabel('Import transcript files',{exact:true}).setInputFiles([
    {name:'first-recording.txt',mimeType:'text/plain',buffer:Buffer.from('The first transcript was saved before its receipt was lost.')},
    {name:'second-recording.txt',mimeType:'text/plain',buffer:Buffer.from('This transcript must wait for another explicit import.')},
  ]);
  await expect(page.getByRole('dialog')).toContainText('Import paused after 0 confirmed parts.');
  const sent=await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_resource'));
  expect(sent).toHaveLength(1);expect(sent[0]).toMatchObject({title:'first-recording.txt',body:'The first transcript was saved before its receipt was lost.'});
  await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.restart());
  await expect(page.locator('.main')).toContainText('No save receipt was received.');
  expect(await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_resource'))).toEqual(sent);
  await page.getByRole('button',{name:'Retry this change',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry this change',exact:true})).toHaveCount(0);
  const retried=await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_resource'));
  expect(retried).toHaveLength(2);expect(retried[1]).toEqual(sent[0]);
  await page.getByRole('button',{name:'Continue work draft',exact:true}).click();
  await expect(page.getByLabel('Desired outcome',{exact:true})).toHaveValue('Prepare notes, actions and follow-up for review.');
  await expect(page.getByRole('checkbox',{name:'first-recording.txt',exact:true})).not.toBeChecked();
  expect(await page.getByRole('checkbox').count()).toBe(1);
  expect(await page.evaluate(()=>window.fixture.resources())).toHaveLength(1);
});

test('a rejected transcript stops the batch without pinning an unsaved source',async({page})=>{
  await start(page);await draft(page);await page.evaluate(()=>window.fixture.rejectNext());
  await page.getByLabel('Import transcript files',{exact:true}).setInputFiles([
    {name:'rejected-recording.txt',mimeType:'text/plain',buffer:Buffer.from('This transcript was rejected.')},
    {name:'later-recording.txt',mimeType:'text/plain',buffer:Buffer.from('This transcript must not be submitted automatically.')},
  ]);
  await expect(page.getByRole('dialog')).toContainText('Import paused after 0 confirmed parts.');
  expect(await page.getByRole('checkbox').count()).toBe(0);
  const sent=await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_resource'));
  expect(sent).toHaveLength(1);expect(sent[0]).toMatchObject({title:'rejected-recording.txt'});
  expect(await page.evaluate(()=>window.fixture.resources())).toHaveLength(0);
  await page.evaluate(()=>window.fixture.restart());
  await page.getByRole('button',{name:'Continue work draft',exact:true}).click();
  await expect(page.getByLabel('Desired outcome',{exact:true})).toHaveValue('Prepare notes, actions and follow-up for review.');
  expect(await page.getByRole('checkbox').count()).toBe(0);
  expect(await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_resource'))).toEqual(sent);
});

test('work dictation inserts literal words into its retained outcome after reopening',async({page})=>{
  await start(page);await draft(page);await page.getByLabel('Desired outcome').fill('Review: ');await page.getByLabel('Desired outcome').evaluate(el=>(el as HTMLTextAreaElement).setSelectionRange(8,8));await page.getByRole('button',{name:'Dictate outcome'}).click();await page.evaluate(()=>window.fixture.speech('Open Word'));await page.getByRole('button',{name:'Stop recording'}).click();await page.getByRole('button',{name:'Close dialog'}).click();await page.evaluate(()=>window.fixture.restart());await page.getByRole('button',{name:'Review capture'}).click();await page.getByRole('button',{name:'Insert into draft'}).click();await expect(page.getByLabel('Desired outcome')).toHaveValue('Review: Open Word');expect(await page.evaluate(()=>window.fixture.resources())).toHaveLength(0);
});

test('local storage failure prevents host submission and preserves the editable draft',async({page})=>{
  await start(page);await draft(page);const before=await page.evaluate(()=>window.fixture.commands().length);await page.evaluate(()=>window.fixture.failRetention(true));await page.getByLabel('Desired outcome').fill('Keep this outcome');await page.getByRole('button',{name:'Create work'}).click();expect(await page.evaluate(()=>window.fixture.commands().length)).toBe(before);await expect(page.getByLabel('Desired outcome')).toHaveValue('Keep this outcome');expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(true);await page.evaluate(()=>window.fixture.failRetention(false));await page.getByRole('button',{name:'Create work'}).click();await expect(page.locator('.meeting-work')).toBeVisible();
});

test('desktop layout toggles, update and composition retain the same editor and selection',async({page})=>{
  await start(page);await transcript(page);await page.getByRole('button',{name:'Open Tuesday transcript',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).click();const input=page.getByLabel('Content',{exact:true}),node=await input.elementHandle();await input.focus();await input.evaluate(el=>(el as HTMLTextAreaElement).setSelectionRange(4,10));
  await page.locator('#navigation-toggle').click();expect(await node!.evaluate(el=>el===document.getElementById('note-body'))).toBe(true);await expect(input).toBeFocused();expect(await input.evaluate(el=>[(el as HTMLTextAreaElement).selectionStart,(el as HTMLTextAreaElement).selectionEnd])).toEqual([4,10]);
  await input.dispatchEvent('compositionstart');await page.evaluate(()=>window.fixture.offerUpdate());await page.evaluate(()=>window.fixture.refresh());expect(await node!.evaluate(el=>el===document.getElementById('note-body'))).toBe(true);await input.dispatchEvent('compositionend');await expect(page.getByRole('button',{name:'Update available'})).toBeVisible();expect(await node!.evaluate(el=>el===document.getElementById('note-body'))).toBe(true);await expect(input).toBeFocused();
});

test('touch navigator and page controls stay separate and preferred hand mirrors them',async({browser,baseURL})=>{
  for(const size of [{width:390,height:844},{width:1024,height:900}]){const context=await browser.newContext({...(baseURL?{baseURL}:{}),viewport:size,hasTouch:true,isMobile:true});const page=await context.newPage();try{
    await start(page);await transcript(page);const nav=page.locator('#navigation-toggle'),{n,d}=await touchGeometryDuringRefresh(page);expect(n.y+n.height).toBeLessThan(d.y);expect(n.width).toBeGreaterThanOrEqual(44);if(size.width>590)expect(n.x+n.width).toBeLessThan(d.x);
    await nav.click();await page.getByRole('dialog').getByRole('button',{name:'Settings',exact:true}).click();await page.getByLabel('Preferred hand').selectOption('left');await page.getByRole('button',{name:'Close dialog'}).click();const left=await touchGeometry(page);expect(left.d.x).toBeLessThan(30);if(size.width>590)expect(left.n.x).toBeGreaterThan(900);else expect(left.n.x).toBeLessThan(30);
    await page.screenshot({path:`/tmp/work-continuity-ui/touch-${size.width}.png`});
  }finally{await context.close();}}
});

test('owner dictation remains literal and survives a retained correction dialog',async({page})=>{
  await prepared(page);await page.getByRole('button',{name:'Correct owners or deadlines'}).click();await page.getByLabel('Owner',{exact:true}).fill('');await page.getByRole('button',{name:'Dictate owner',exact:true}).click();await page.evaluate(()=>window.fixture.speech('Open Word'));await page.getByRole('button',{name:'Stop recording'}).click();await page.getByRole('button',{name:'Close dialog'}).click();await page.evaluate(()=>window.fixture.restart());await page.getByRole('button',{name:'Review capture'}).click();await page.getByRole('button',{name:'Insert into draft'}).click();await expect(page.getByLabel('Owner',{exact:true})).toHaveValue('Open Word');await page.getByRole('button',{name:'Save corrections'}).click();await expect(page.locator('.meeting-action')).toContainText('Open Word · user correction');expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(4);
});

test('reopening during preparation waits for an explicit origin-host resume',async({page})=>{
  await start(page);await transcript(page);await createWork(page);await page.evaluate(()=>window.fixture.holdPreparation());await page.getByRole('button',{name:'Prepare meeting material',exact:true}).click();await expect.poll(()=>page.evaluate(()=>window.fixture.meetingRequests().length)).toBe(1);await page.evaluate(()=>window.fixture.restart());await expect(page.locator('.main')).toContainText('Preparation was interrupted');expect(await page.evaluate(()=>window.fixture.meetingRequests().length)).toBe(1);await page.getByRole('button',{name:'Resume this preparation',exact:true}).click();await expect(page.locator('.meeting-work')).toContainText('Material ready for review');expect(await page.evaluate(()=>window.fixture.meetingRequests().length)).toBe(2);await page.evaluate(()=>window.fixture.finishPreparation());expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(4);
});

test('editor node stays the same through inspector and OS theme changes; reopen retains unsaved text',async({page})=>{
  await start(page);await transcript(page);await page.getByRole('button',{name:'Open Tuesday transcript',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).click();const content=page.getByLabel('Content',{exact:true});await content.fill('Unsubmitted thoughts 👋');const node=await content.elementHandle();await content.focus();await page.getByRole('button',{name:'Related details',exact:true}).click();expect(await node!.evaluate(el=>el===document.getElementById('note-body'))).toBe(true);await expect(content).toBeFocused();await page.emulateMedia({colorScheme:'dark'});expect(await node!.evaluate(el=>el===document.getElementById('note-body'))).toBe(true);await page.evaluate(()=>window.fixture.restart());await expect(content).toHaveValue('Unsubmitted thoughts 👋');expect(await page.evaluate(()=>window.fixture.resources()[0]?.heads[0])).toHaveProperty('body','Lea will share the revised identity on Friday.');
});

test('prepared work layout remains usable on desktop and touch with real fixture state',async({page,browser,baseURL})=>{
  await page.setViewportSize({width:1512,height:982});await prepared(page);await page.screenshot({path:'/tmp/work-continuity-ui/meeting-desktop.png'});
  const context=await browser.newContext({...(baseURL?{baseURL}:{}),viewport:{width:390,height:844},hasTouch:true,isMobile:true});try{const phone=await context.newPage();await prepared(phone);await phone.screenshot({path:'/tmp/work-continuity-ui/meeting-phone.png'});expect(await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await phone.getByRole('button',{name:'Open and edit follow-up'}).click();await phone.getByRole('button',{name:'Edit',exact:true}).click();await phone.getByLabel('Content',{exact:true}).fill('Short follow-up.');await phone.getByRole('button',{name:'Save note'}).click();await expect(phone.locator('.note-body').first()).toHaveText('Short follow-up.');}finally{await context.close();}
});

test('all selected recordings import serially and remain pinned in one request',async({page})=>{
  await start(page);await draft(page);const bodies=['Lea will share the revised identity on Friday.','Jim will collect feedback.','No owner was named for the last task.'];await page.locator('#transcript-file').setInputFiles(bodies.map((text,index)=>({name:`recording-${index+1}.txt`,mimeType:'text/plain',buffer:Buffer.from(text)})));await expect(page.getByRole('dialog')).toContainText('3 transcript parts imported and pinned from 3 files');expect(await page.evaluate(()=>window.fixture.resources().flatMap(r=>r.heads.flatMap(h=>'body' in h?[h.body]:[])))).toEqual(bodies);const create=page.getByRole('button',{name:'Create work',exact:true});await create.scrollIntoViewIfNeeded();const button=await create.elementHandle(),bounds=(await create.boundingBox())!;
  await page.evaluate(()=>window.addEventListener('click',event=>{const target=event.target as HTMLElement;if(!target.closest('#meeting-form button[type=submit]'))return;window.fixture.offerUpdate();document.documentElement.dataset.clickControlConnected=String(target.isConnected);queueMicrotask(()=>window.fixture.refresh());},{once:true,capture:true}));
  await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  expect(await button!.evaluate(node=>node.isConnected&&node===document.querySelector('#meeting-form button[type=submit]')),'OBSERVATION_REPLACED_PRESSED_CREATE_CONTROL').toBe(true);
  await page.mouse.up();await expect(page.locator('.meeting-work')).toBeVisible();await expect(page.locator('html')).toHaveAttribute('data-click-control-connected','true');expect(((await work(page)).heads[0] as WorkRevision).work.sources).toHaveLength(3);
});

test('discarding and replacing a correction draft fences late dictation for the same work',async({page})=>{
  await prepared(page);await page.getByRole('button',{name:'Correct owners or deadlines'}).click();await page.getByLabel('Owner',{exact:true}).fill('');await page.getByRole('button',{name:'Dictate owner',exact:true}).click();await page.evaluate(()=>window.fixture.speech('Morgan'));await page.getByRole('button',{name:'Stop recording'}).click();await page.getByRole('button',{name:'Close dialog'}).click();await page.getByRole('button',{name:'Continue work draft'}).click();await page.getByRole('button',{name:'Discard corrections'}).click();await page.getByRole('button',{name:'Correct owners or deadlines'}).click();await page.getByLabel('Owner',{exact:true}).fill('');await page.getByRole('button',{name:'Close dialog'}).click();await page.getByRole('button',{name:'Review capture'}).click();await page.getByRole('button',{name:'Insert into draft'}).click();await expect(page.getByRole('dialog')).toContainText('original work draft changed');await expect(page.getByLabel('Spoken transcript · editable')).toHaveValue('Morgan');await page.getByRole('button',{name:'Close dialog'}).click();await page.getByRole('button',{name:'Continue work draft'}).click();await expect(page.getByLabel('Owner',{exact:true})).toHaveValue('');
});

test('stale corrections show current versions and only save after explicit review',async({page})=>{
  await prepared(page);await page.getByRole('button',{name:'Correct owners or deadlines'}).click();await page.getByLabel('Owner',{exact:true}).fill('Jim');await page.evaluate(()=>window.fixture.peerCorrection());await page.keyboard.press('Tab');await expect(page.getByRole('dialog')).toContainText('Saved actions changed');await expect(page.getByRole('dialog').locator('.comparison')).toContainText('Next Tuesday');await expect(page.getByLabel('Owner',{exact:true})).toHaveValue('Jim');await expect(page.getByRole('button',{name:'Save corrections',exact:true})).toBeDisabled();const before=await page.evaluate(()=>window.fixture.commands().length);await page.getByRole('button',{name:'Save my reviewed corrections'}).click();await expect(page.locator('.meeting-action')).toContainText('Jim · user correction');const commands=await page.evaluate(()=>window.fixture.commands());expect(commands).toHaveLength(before+1);expect(commands.at(-1)).toMatchObject({type:'edit_work',expected_heads:['fixture-peer-correction'],change:{type:'correct_actions'}});
});

test('pointer cancellation and disposal release deferred views without losing the editor',async({page})=>{
  await start(page);await add(page);await page.getByRole('dialog').getByRole('button',{name:/^Note /}).click();await page.getByLabel('Title',{exact:true}).fill('Retained editor');await page.getByLabel('Content',{exact:true}).fill('Keep this input through cancellation.');
  const actions=page.getByRole('button',{name:'Page actions',exact:true});await actions.scrollIntoViewIfNeeded();let bounds=(await actions.boundingBox())!;
  await page.evaluate(()=>window.addEventListener('pointerdown',event=>{document.documentElement.dataset.testPointerId=String(event.pointerId);},{once:true,capture:true}));
  await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();await page.evaluate(()=>window.fixture.offerUpdate());await expect(page.getByRole('button',{name:'Update available',exact:true})).toHaveCount(0);
  await page.evaluate(()=>window.dispatchEvent(new PointerEvent('pointercancel',{pointerId:Number(document.documentElement.dataset.testPointerId),isPrimary:true})));
  await expect(page.getByRole('button',{name:'Update available',exact:true})).toBeVisible();await page.mouse.move(1,1);await page.mouse.up();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Keep this input through cancellation.');
  await actions.click();await expect(page.getByRole('dialog',{name:'Page actions',exact:true})).toBeVisible();await page.keyboard.press('Escape');
  bounds=(await actions.boundingBox())!;await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.restart());await page.mouse.move(1,1);await page.mouse.up();
  await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Keep this input through cancellation.');await page.evaluate(()=>window.fixture.offerUpdate());await expect(page.getByRole('button',{name:'Update available',exact:true})).toBeVisible();await actions.click();await expect(page.getByRole('dialog',{name:'Page actions',exact:true})).toBeVisible();
});

test('phone contextual icons retain accessible Add material and All material names',async({browser,baseURL})=>{
  const context=await browser.newContext({...(baseURL?{baseURL}:{}),viewport:{width:390,height:844},hasTouch:true,isMobile:true});try{
    const page=await context.newPage();await start(page);await expect(page.locator('.capture-dock [data-action=add]')).toHaveAccessibleName('Add material');await page.getByRole('button',{name:'Add material',exact:true}).tap();await page.getByRole('dialog').getByRole('button',{name:/^Note /}).tap();await page.getByLabel('Title',{exact:true}).fill('Phone note');await page.getByLabel('Content',{exact:true}).fill('An accessible route back.');await page.getByRole('button',{name:'Save note',exact:true}).tap();await page.getByRole('button',{name:'Open Phone note',exact:true}).tap();await expect(page.locator('.capture-dock [data-action=all-work]')).toHaveAccessibleName('All material');await page.getByRole('button',{name:'All material',exact:true}).tap();await expect(page.getByRole('button',{name:'Open Phone note',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Add material',exact:true})).toBeVisible();
  }finally{await context.close();}
});

test('Space activation keeps its form through observers and IME Enter cannot leave a key fence',async({page})=>{
  await start(page);await page.locator('#workspace-picker').click();await page.getByRole('dialog').getByRole('button',{name:'Create workspace',exact:true}).click();await page.getByLabel('Workspace name',{exact:true}).fill('Keyboard destination');const create=page.locator('#workspace-form button[type=submit]');await create.focus();const node=await create.elementHandle();
  await page.evaluate(()=>window.addEventListener('click',event=>{const target=event.target as HTMLElement;if(!target.closest('#workspace-form button[type=submit]'))return;window.fixture.offerUpdate(false);document.documentElement.dataset.keyControlConnected=String(target.isConnected);queueMicrotask(()=>window.fixture.refresh());},{once:true,capture:true}));
  await page.keyboard.down('Space');await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.offerUpdate());await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));expect(await node!.evaluate(button=>button.isConnected&&button===document.querySelector('#workspace-form button[type=submit]')),'OBSERVATION_REPLACED_SPACE_ACTIVATION_CONTROL').toBe(true);expect(await page.evaluate(()=>window.fixture.commands().filter(command=>command.type==='create_workspace').length)).toBe(1);
  await page.keyboard.up('Space');await expect(page.locator('html')).toHaveAttribute('data-key-control-connected','true');await expect(page.locator('#workspace-picker')).toHaveAttribute('aria-label','Current workspace: Keyboard destination');await expect(page.getByRole('dialog')).toHaveCount(0);expect(await page.evaluate(()=>window.fixture.commands().at(-1))).toMatchObject({type:'create_workspace',title:'Keyboard destination'});
  await page.evaluate(()=>window.fixture.offerUpdate(false));await add(page);await page.getByRole('dialog').getByRole('button',{name:/^Note /}).click();const title=page.getByLabel('Title',{exact:true});await title.fill('IME draft');const titleNode=await title.elementHandle();await title.dispatchEvent('compositionstart');await title.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true});await page.evaluate(()=>window.fixture.offerUpdate());await expect(page.getByRole('button',{name:'Update available',exact:true})).toHaveCount(0);await title.dispatchEvent('compositionend');await expect(page.getByRole('button',{name:'Update available',exact:true})).toBeVisible();expect(await titleNode!.evaluate(field=>field===document.getElementById('note-title'))).toBe(true);await expect(title).toHaveValue('IME draft');
});
