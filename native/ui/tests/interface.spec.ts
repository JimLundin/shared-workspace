import {test,expect,type Page} from '@playwright/test';
async function openPicker(page:Page){const picker=page.locator('#workspace-picker');if(await picker.isVisible())await picker.click();else{await page.locator('#navigation-toggle').click();await page.getByRole('dialog').getByRole('button',{name:'Workspaces',exact:true}).click();}}
async function openSettings(page:Page){const settings=page.locator('#workspace-settings');if(await settings.isVisible())await settings.click();else{await page.locator('#navigation-toggle').click();await page.getByRole('dialog').getByRole('button',{name:'Settings',exact:true}).click();}}
async function addMaterial(page:Page){const add=page.locator('.capture-dock [data-action=add]');if(await add.isVisible())await add.click();else{await page.getByRole('button',{name:'Page actions',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Add to workspace',exact:true}).click();}}
async function typeInput(page:Page){const type=page.getByRole('dialog').getByRole('button',{name:'Type instead',exact:true});if(!await type.isVisible())await page.getByRole('button',{name:'Page actions',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Type instead',exact:true}).click();}
async function settingsReturn(page:Page){await expect(await page.locator('#workspace-settings').isVisible()?page.locator('#workspace-settings'):page.locator('#navigation-toggle')).toBeFocused();}

async function createWorkspace(page:Page,title='Launch work',first=true){
  if(first)await page.goto('/tests/host.html');
  else {await openPicker(page);await page.getByRole('button',{name:'Create workspace',exact:true}).click()}
  await page.getByLabel('Workspace name',{exact:true}).fill(title);
  await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await expect(page.locator('#workspace-picker')).toHaveAttribute('aria-label','Current workspace: '+title);
  await expect(page.getByRole('dialog')).toHaveCount(0);
}
async function startNote(page:Page){await addMaterial(page);await page.getByRole('dialog').getByRole('button',{name:/^Note /}).click()}
async function createNote(page:Page,title='Launch brief',body='The launch starts with three pilot teams.'){
  await startNote(page);
  await page.getByLabel('Title',{exact:true}).fill(title);await page.getByLabel('Content',{exact:true}).fill(body);
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.locator('.editor')).toHaveCount(0);await expect(page.getByRole('button',{name:'Open '+title,exact:true})).toBeVisible();
}
async function switchTo(page:Page,title:string){await openPicker(page);await page.getByRole('dialog').getByRole('button',{name:title,exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)}
async function openNote(page:Page,title='Launch brief'){await page.getByRole('button',{name:'Open '+title,exact:true}).click()}
async function share(page:Page){await openPicker(page);await page.getByRole('dialog').getByRole('button',{name:'Share this workspace',exact:true}).click();}
async function openJoin(page:Page){await openPicker(page);await page.getByRole('button',{name:'Join existing work',exact:true}).click()}

test('new user can create or join and browser without native host never invents records',async({page})=>{
  await page.goto('/');await expect(page.getByRole('heading',{name:'Open your native workspace'})).toBeVisible();await expect(page.getByRole('button',{name:'Create workspace',exact:true})).toHaveCount(0);
  await page.goto('/tests/host.html');await expect(page.getByLabel('Workspace name',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Join existing work',exact:true})).toBeVisible();
  await expect(page.getByText('Toolkit',{exact:true})).toHaveCount(0);
});

test('phone shows material and only displays workspace picker on request',async({page})=>{
  await page.setViewportSize({width:390,height:844});await createWorkspace(page);await createNote(page);
  await expect(page.locator('nav')).toHaveCount(0);await expect(page.getByRole('button',{name:'Canvas',exact:true})).toHaveCount(0);
  await expect(page.locator('.note-body')).toBeVisible();expect((await page.locator('.resource-card').boundingBox())!.y).toBeLessThan(270);
  await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');
  await expect(page.getByRole('button',{name:'Private',exact:true})).toHaveCount(0);
  await openNote(page);await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByLabel('Content',{exact:true}).fill('Phone editing is direct.');await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.locator('.note-body')).toHaveText('Phone editing is direct.');
  await page.evaluate(()=>window.fixture.restart());await expect(page.getByRole('button',{name:'Edit',exact:true})).toBeVisible();await expect(page.locator('.note-body')).toHaveText('Phone editing is direct.');
});

test('local close/reopen sends no mutation and uncertain retry uses exact change',async({page})=>{
  await createWorkspace(page);await createNote(page);await openNote(page);await page.locator('.main summary').filter({hasText:'Organize & history'}).click();
  const count=await page.evaluate(()=>window.fixture.commands().length);
  await page.getByRole('button',{name:'Close local view Launch brief',exact:true}).click();await expect(page.locator('article.resource-card')).toHaveCount(0);expect(await page.evaluate(()=>window.fixture.commands().length)).toBe(count);
  await page.getByRole('button',{name:'Reopen views',exact:true}).click();await openNote(page);
  await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByLabel('Content',{exact:true}).fill('My revision');await page.evaluate(()=>window.fixture.loseReceipt());await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.getByLabel('Content',{exact:true})).toHaveValue('My revision');await page.getByRole('button',{name:'Retry this change'}).click();
  const commands=await page.evaluate(()=>window.fixture.commands());expect(commands.at(-1)).toEqual(commands.at(-2));await expect(page.locator('.editor')).toHaveCount(0);
});

test('late revision preserves local edit and requires explicit reconciliation',async({page})=>{
  await createWorkspace(page);await createNote(page);await openNote(page);await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByLabel('Content',{exact:true}).fill('My local text');await page.evaluate(()=>window.fixture.remoteEdit());await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.getByText('Newer material arrived',{exact:true})).toBeVisible();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('My local text');await page.getByRole('button',{name:'Save my edit as a new revision',exact:true}).click();
  const last=(await page.evaluate(()=>window.fixture.commands())).at(-1);expect(last?.type).toBe('edit_resource');if(last?.type==='edit_resource'){expect(last.expected_heads).toEqual(['remote-head']);expect(last.body).toBe('My local text')}
});

test('known rejection and network loss retain editable input with honest local save',async({page})=>{
  await createWorkspace(page);await createNote(page);await openNote(page);await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByLabel('Title',{exact:true}).fill('Rejected title');await page.evaluate(()=>window.fixture.rejectNext());await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.locator('.status-message')).toContainText('Change not saved');await expect(page.getByRole('button',{name:'Retry this change'})).toHaveCount(0);await expect(page.getByLabel('Title',{exact:true})).toHaveValue('Rejected title');
  await page.getByLabel('Title',{exact:true}).fill('Corrected title');await page.evaluate(()=>window.fixture.offline());await page.getByRole('button',{name:'Save note',exact:true}).click();await expect(page.locator('.editor')).toHaveCount(0);await expect(page.locator('.paper h2')).toHaveText('Corrected title');await expect(page.locator('.topbar')).not.toContainText('Saved here');await expect(page.getByRole('button',{name:'Check connections'})).toBeVisible();
});

test('typed capture keeps workspace across navigation and failed receipt retry cannot duplicate it',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');
  await typeInput(page);await page.getByLabel('Your words',{exact:true}).fill('Remember the pilot decision');await page.getByRole('button',{name:'Close dialog'}).click();await switchTo(page,'Private');
  await page.getByRole('button',{name:'Review capture',exact:false}).click();await expect(page.getByLabel('Your words',{exact:true})).toHaveValue('Remember the pilot decision');await expect(page.locator('#run-command')).toBeDisabled();await page.getByRole('button',{name:'Return to Launch work'}).click();
  await page.evaluate(()=>window.fixture.loseReceipt());await page.locator('#run-command').click();await page.getByRole('dialog').getByRole('button',{name:'Retry this change'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  const commands=await page.evaluate(()=>window.fixture.commands());expect(commands.at(-1)).toEqual(commands.at(-2));expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(1);
  await typeInput(page);await expect(page.getByLabel('Your words')).toHaveValue('');
});

test('voice transcript stays addressed and cancelled late speech cannot overwrite newer input',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');
  await page.evaluate(()=>document.addEventListener('focusin',event=>{if((event.target as HTMLElement).id==='command-text')document.documentElement.dataset.commandFocusSeen='true'}));
  await page.getByRole('button',{name:'Speak',exact:true}).click();expect(await page.locator('html').getAttribute('data-command-focus-seen')).toBeNull();await page.evaluate(()=>window.fixture.speech('Remember a spoken thought'));
  await expect(page.getByLabel('Spoken transcript · editable')).toHaveValue('Remember a spoken thought');await page.getByRole('button',{name:'Close dialog'}).click();await switchTo(page,'Private');
  await page.evaluate(()=>window.fixture.speech('Late voice must not replace the thought',0));await page.getByRole('button',{name:'Review capture'}).click();await expect(page.getByLabel('Spoken transcript · editable')).toHaveValue('Remember a spoken thought');await expect(page.locator('#run-command')).toBeDisabled();
  await page.getByRole('button',{name:'Return to Launch work'}).click();await page.locator('#run-command').click();await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await page.evaluate(()=>{const h=window.fixture.resources()[0]?.heads[0];return h&&'body' in h?h.body:null})).toBe('a spoken thought');
  await page.evaluate(()=>window.fixture.failSpeech());await page.getByRole('button',{name:'Speak',exact:true}).click();await expect(page.locator('.voice-status')).toContainText('Type your command');await page.getByRole('dialog').getByRole('button',{name:'Type instead',exact:true}).click();await page.getByLabel('Your words').fill('Remember typed fallback');await page.locator('#run-command').click();expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(2);
});

test('unfinished editor survives workspace switch and cannot be replaced by a new note',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');await startNote(page);await page.getByLabel('Title',{exact:true}).fill('Unfinished');await page.getByLabel('Content',{exact:true}).fill('Keep this thought');
  await switchTo(page,'Private');await startNote(page);await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Keep this thought');await expect(page.getByRole('button',{name:'Current workspace: Launch work'})).toBeVisible();
  await page.getByRole('button',{name:'Keep draft & go back'}).click();await page.getByRole('button',{name:'Continue edit'}).click();await expect(page.getByLabel('Title',{exact:true})).toHaveValue('Unfinished');
});

test('share uses explicit selected workspaces and hides invitation secrets',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await share(page);
  const shared=page.getByRole('checkbox',{name:'Launch work',exact:true}),privateSpace=page.getByRole('checkbox',{name:'Private',exact:true});await expect(shared).not.toBeChecked();await expect(privateSpace).toBeChecked();await shared.check();await page.getByRole('button',{name:'Create invitation',exact:true}).click();
  await expect(page.getByLabel('Pairing ticket',{exact:true})).toBeHidden();await expect(page.locator('.invite-panel .access-scope')).toContainText('Launch work');await expect(page.locator('.invite-panel .access-scope')).toContainText('Private');
  await page.getByText('Show invitation for copy and paste',{exact:true}).click();await page.evaluate(()=>window.fixture.refresh());await expect(page.getByLabel('Pairing ticket',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Change selected workspaces',exact:true}).click();await privateSpace.uncheck();await page.getByRole('button',{name:'Create invitation',exact:true}).click();const selections=await page.evaluate(()=>window.fixture.inviteSelections());expect(selections[0]).toHaveLength(2);expect(selections[1]).toHaveLength(1);expect(selections[0]).toContain(selections[1]![0]);await expect(page.locator('.invite-panel .access-scope')).not.toContainText('Private');
});

test('phone join shows real stages, retry retains ticket, committed join opens received work',async({page})=>{
  await page.setViewportSize({width:390,height:844});await createWorkspace(page);await openJoin(page);await page.getByRole('button',{name:'Scan invitation QR'}).click();expect(await page.evaluate(()=>window.fixture.joins())).toBe(0);await expect(page.getByLabel('Invitation to join')).toBeHidden();
  await page.evaluate(()=>window.fixture.holdNextJoin());await page.getByRole('button',{name:'Join selected workspaces',exact:true}).click();await expect(page.locator('.join-progress')).toContainText('Waiting for a confirmed exchange');await page.evaluate(()=>window.fixture.joinStage('connecting_relay'));await expect(page.locator('[aria-current="step"]')).toHaveText('Connecting this device');await page.evaluate(()=>window.fixture.failHeldJoin());
  await expect(page.getByRole('alert')).toContainText('relay connection');await expect(page.getByRole('alert')).not.toContainText('may have accepted');await expect(page.getByRole('alert')).not.toContainText('Untrusted remote');
  await page.evaluate(()=>window.fixture.holdNextJoin());await page.getByRole('button',{name:'Try joining again',exact:true}).click();await page.evaluate(()=>window.fixture.joinStage('saving_workspace'));await expect(page.getByRole('button',{name:'Joining…',exact:true})).toBeDisabled();await page.evaluate(()=>window.fixture.finishJoin(['Received work']));await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('#workspace-picker')).toHaveAttribute('aria-label','Current workspace: Received work');
  const attempts=await page.evaluate(()=>window.fixture.joinTickets());expect(attempts).toHaveLength(2);expect(attempts[0]).toBe(attempts[1]);
});

test('multiple joined spaces offer only received work, repeated grant asks instead of guessing',async({page})=>{
  await createWorkspace(page);await openJoin(page);await page.getByRole('button',{name:'Scan invitation QR'}).click();await page.evaluate(()=>window.fixture.holdNextJoin());await page.getByRole('button',{name:'Join selected workspaces',exact:true}).click();await page.evaluate(()=>window.fixture.finishJoin(['Received work','Received private']));
  const picker=page.getByRole('dialog',{name:'Switch workspace',exact:true});await expect(picker.getByRole('button',{name:'Launch work',exact:true})).toHaveCount(0);await expect(picker.getByRole('button',{name:'Received private',exact:true})).toBeVisible();await picker.getByRole('button',{name:'Received work',exact:true}).click();
  await openJoin(page);await page.getByRole('button',{name:'Scan invitation QR'}).click();await page.getByRole('button',{name:'Join selected workspaces',exact:true}).click();await expect(picker).toBeVisible();await expect(picker).toContainText('Choose the workspace');
});

test('dialogs are top layer, trap focus, preserve background scroll and restore focus on Escape/Back',async({page})=>{
  await page.setViewportSize({width:390,height:620});await createWorkspace(page);for(let i=0;i<3;i++)await createNote(page,'Note '+i,'Text');
  await page.locator('.main').evaluate(el=>el.scrollTop=180);const scroll=await page.locator('.main').evaluate(el=>el.scrollTop);
  const trigger=page.locator('#navigation-toggle');await trigger.click();await expect(page.locator('dialog:modal')).toHaveCount(1);await page.locator('[data-action=add]').evaluate(el=>(el as HTMLElement).focus());expect(await page.evaluate(()=>!!document.activeElement?.closest('dialog'))).toBe(true);
  const close=page.getByRole('button',{name:'Close dialog'});await close.focus();await page.evaluate(()=>window.fixture.refresh());await expect(close).toBeFocused();
  for(let i=0;i<12;i++)await page.keyboard.press('Tab');expect(await page.evaluate(()=>!!document.activeElement?.closest('dialog'))).toBe(true);
  await page.keyboard.press('Escape');await expect(trigger).toBeFocused();expect(await page.locator('.main').evaluate(el=>el.scrollTop)).toBe(scroll);
  await trigger.click();await page.goBack();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(trigger).toBeFocused();
});

test('long titles, large text and keyboard-like narrow/landscape viewports remain reachable',async({page})=>{
  await page.setViewportSize({width:320,height:568});await createWorkspace(page,'A very long workspace title that should stay understandable');await createNote(page,'Unbroken'.repeat(22),'A long paragraph '.repeat(100));
  await page.evaluate(()=>document.documentElement.style.fontSize='20px');
  for(const viewport of [{width:320,height:568},{width:740,height:340},{width:390,height:380}]){
    await page.setViewportSize(viewport);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await openPicker(page);await expect(page.getByRole('button',{name:'Close dialog'})).toBeInViewport();await page.getByRole('button',{name:'Close dialog'}).click();
  }
  await typeInput(page);await page.getByLabel('Your words').fill('Remember a thought');await page.locator('#run-command').click();await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('opening from a scrolled list starts reader at title and polling preserves reader position',async({page})=>{
  await page.setViewportSize({width:390,height:620});await createWorkspace(page);for(let i=0;i<5;i++)await createNote(page,'Note '+i,'A long page.\n'.repeat(100));
  await page.getByRole('button',{name:'Open Note 4',exact:true}).click();await expect(page.getByRole('heading',{name:'Note 4',exact:true})).toBeInViewport();await expect(page.getByRole('button',{name:'Edit',exact:true})).toBeInViewport();await expect(page.getByRole('heading',{name:'Note 4',exact:true})).toBeFocused();
  await page.locator('.main').evaluate(el=>el.scrollTop=400);await page.evaluate(()=>window.fixture.refresh());expect(await page.locator('.main').evaluate(el=>el.scrollTop)).toBe(400);
});

test('editing a reference opens the same resource immediately in its original workspace',async({page})=>{
  await createWorkspace(page);await createNote(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');await openNote(page);await page.locator('.main summary').filter({hasText:'Organize & history'}).click();await page.getByRole('button',{name:'Add reference',exact:true}).click();await switchTo(page,'Private');await openNote(page);
  await page.getByRole('button',{name:'Edit',exact:true}).click();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('The launch starts with three pilot teams.');await expect(page.getByRole('button',{name:'Current workspace: Launch work'})).toBeVisible();await page.getByLabel('Content',{exact:true}).fill('Edited through a reference');await page.getByRole('button',{name:'Save note',exact:true}).click();await switchTo(page,'Private');await expect(page.locator('.note-body')).toHaveText('Edited through a reference');
  const resources=await page.evaluate(()=>window.fixture.resources());expect(resources).toHaveLength(1);
});

test('system dictation focuses the input before handing control to Windows',async({page})=>{
  await page.goto('/tests/host.html?dictation');await page.getByLabel('Workspace name',{exact:true}).fill('Work');await page.getByRole('button',{name:'Create workspace',exact:true}).click();await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect(page.getByLabel('Your words',{exact:true})).toBeFocused();await page.getByLabel('Your words',{exact:true}).fill('Remember Windows dictation');await page.getByRole('button',{name:'Finish input'}).click();await page.locator('#run-command').click();expect(await page.evaluate(()=>{const h=window.fixture.resources()[0]?.heads[0];return h&&'body' in h?h.body:null})).toBe('Windows dictation');
});

test('themes persist locally and explicit selection survives system color changes',async({page})=>{
  await page.emulateMedia({colorScheme:'light'});await createWorkspace(page);
  await openSettings(page);await page.getByRole('button',{name:'Appearance',exact:true}).click();await page.getByRole('button',{name:'Dark',exact:true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');await page.emulateMedia({colorScheme:'dark'});await page.emulateMedia({colorScheme:'light'});await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.getByRole('button',{name:'Close dialog'}).click();await page.evaluate(()=>window.fixture.restart());await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await openSettings(page);await page.getByRole('button',{name:'Appearance',exact:true}).click();await page.getByRole('button',{name:'Light',exact:true}).click();await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await page.getByRole('button',{name:'System',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
});

test('dictation inserts into captured editor field without creating another note',async({page})=>{
  await createWorkspace(page);await startNote(page);await page.getByLabel('Title',{exact:true}).fill('Thought');await page.getByLabel('Content',{exact:true}).fill('First. ');await page.getByLabel('Content',{exact:true}).press('End');
  const before=await page.evaluate(()=>window.fixture.commands().length);await page.getByRole('button',{name:'Speak',exact:true}).click();await page.evaluate(()=>window.fixture.speech('Open Word, then review the brief.'));await page.getByRole('button',{name:'Stop recording',exact:true}).click();await page.getByRole('button',{name:'Insert into draft',exact:true}).click();
  await expect(page.getByLabel('Content',{exact:true})).toHaveValue('First. Open Word, then review the brief.');expect(await page.evaluate(()=>window.fixture.commands().length)).toBe(before);await page.getByRole('button',{name:'Save note',exact:true}).click();expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(1);
});

test('provider setup and exact retry keep keys out of the interface, free speech becomes a note',async({page})=>{
  await page.setViewportSize({width:390,height:667});await page.goto('/tests/host.html?provider');await page.getByLabel('Workspace name',{exact:true}).fill('Work');await page.getByRole('button',{name:'Create workspace',exact:true}).click();await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'OpenAI connection',exact:true})).toBeVisible();const key='synthetic-only-connection-key';await page.getByLabel('OpenAI API key',{exact:true}).fill(key);await page.evaluate(()=>window.fixture.loseConnectionReceipt());await page.getByRole('button',{name:'Save connection',exact:true}).click();expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(true);await page.getByRole('button',{name:'Retry connection change',exact:true}).click();expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(false);
  await expect(page.getByLabel('Replace API key',{exact:true})).toHaveValue('');const writes=await page.evaluate(()=>window.fixture.connectionWrites());expect(writes).toHaveLength(2);expect(writes[0]).toEqual(writes[1]);expect(await page.locator('body').innerHTML()).not.toContain(key);expect(JSON.stringify(await page.evaluate(()=>window.fixture.resources()))).not.toContain(key);
  await page.getByRole('button',{name:'Continue to voice'}).click();await page.getByRole('button',{name:'Start speaking'}).click();await expect(page.locator('#command-text')).toHaveCount(0);await page.getByRole('button',{name:'Stop recording',exact:true}).click();await expect(page.locator('.voice-stage h3')).toHaveText('Transcribing');expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(true);await page.evaluate(()=>window.fixture.finishSpeech('A thought without a command prefix.'));await expect(page.getByRole('button',{name:'Save as note',exact:true})).toBeFocused();expect((await page.locator('#command-text').boundingBox())!.y).toBeLessThan(320);const saveBounds=(await page.locator('#run-command').boundingBox())!;expect(saveBounds.y+saveBounds.height).toBeLessThan(650);await page.getByRole('button',{name:'Save as note',exact:true}).click();
  await expect(page.locator('.note-body')).toHaveText('A thought without a command prefix.');await share(page);await expect(page.getByRole('dialog')).toContainText('saved OpenAI connections');
});

test('dictation review focus survives completion during Stop activation and later renders',async({page})=>{
  await page.goto('/tests/host.html?provider');
  await page.getByLabel('Workspace name',{exact:true}).fill('Work');await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await page.getByRole('button',{name:'Speak',exact:true}).click();await page.getByLabel('OpenAI API key',{exact:true}).fill('synthetic-only-connection-key');await page.getByRole('button',{name:'Save connection',exact:true}).click();
  await page.getByRole('button',{name:'Continue to voice',exact:true}).click();await page.getByRole('button',{name:'Start speaking',exact:true}).click();
  await page.evaluate(()=>document.addEventListener('click',event=>{
    if(event.target instanceof Element&&event.target.closest('[data-action="stop-voice"]'))window.fixture.finishSpeech('Review this captured thought.');
  },{once:true}));
  await page.getByRole('button',{name:'Stop recording',exact:true}).click();
  const review=page.getByRole('button',{name:'Save as note',exact:true});
  await expect(review).toBeVisible();await expect(review).toBeFocused();
  await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.offerUpdate());
  await expect(review).toBeFocused();await expect(page.locator('#command-text')).toHaveValue('Review this captured thought.');
  await expect(page.locator('.capture-address')).toHaveText('Work');
  await review.click();await expect(page.locator('.note-body')).toHaveText('Review this captured thought.');
});

test('dictation review focus survives completion before the same Stop keyup',async({page})=>{
  await createWorkspace(page);await page.getByRole('button',{name:'Speak',exact:true}).click();
  await page.getByRole('button',{name:'Stop recording',exact:true}).focus();await page.keyboard.down(' ');
  await page.evaluate(()=>window.fixture.finishSpeech('Review after the Space press.'));
  await expect(page.locator('#run-command')).toHaveCount(0);
  await page.keyboard.up(' ');
  const review=page.getByRole('button',{name:'Save as note',exact:true});await expect(review).toBeFocused();
  await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.offerUpdate());
  await expect(review).toBeFocused();await expect(page.locator('#command-text')).toHaveValue('Review after the Space press.');
});

test('a later keyboard action takes precedence over pending dictation review focus',async({page})=>{
  await createWorkspace(page);await page.getByRole('button',{name:'Speak',exact:true}).click();
  await page.getByRole('button',{name:'Stop recording',exact:true}).focus();await page.keyboard.down(' ');
  await page.evaluate(()=>window.fixture.finishSpeech('Keep this thought while navigating.'));
  await expect(page.locator('#run-command')).toHaveCount(0);
  await page.keyboard.press('Shift+Tab');await page.keyboard.up(' ');
  const close=page.getByRole('button',{name:'Close dialog',exact:true});
  await expect(page.getByRole('button',{name:'Save as note',exact:true})).toBeVisible();await expect(close).toBeFocused();
  await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.offerUpdate());
  await expect(close).toBeFocused();await expect(page.locator('#command-text')).toHaveValue('Keep this thought while navigating.');
});

test('dismissed dictation review focus cannot return or revive discarded input',async({page})=>{
  await createWorkspace(page);await page.getByRole('button',{name:'Speak',exact:true}).click();
  await page.getByRole('button',{name:'Stop recording',exact:true}).focus();await page.keyboard.down(' ');
  await page.evaluate(()=>window.fixture.finishSpeech('The dismissed capture.'));
  await expect(page.locator('#run-command')).toHaveCount(0);
  await page.keyboard.press('Escape');await page.keyboard.up(' ');await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button',{name:'Review capture',exact:true}).click();
  const close=page.getByRole('button',{name:'Close dialog',exact:true});await expect(close).toBeFocused();
  await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.offerUpdate());
  await expect(close).toBeFocused();await expect(page.locator('#command-text')).toHaveValue('The dismissed capture.');
  await page.getByRole('button',{name:'New capture',exact:true}).click();await page.getByRole('button',{name:'Type instead',exact:true}).click();
  await page.getByLabel('Your words',{exact:true}).fill('A replacement typed thought.');
  await page.evaluate(()=>window.fixture.refresh());await page.evaluate(()=>window.fixture.offerUpdate(false));
  await expect(page.getByLabel('Your words',{exact:true})).toHaveValue('A replacement typed thought.');await expect(page.getByLabel('Your words',{exact:true})).toBeFocused();
  expect(await page.evaluate(()=>window.fixture.resources())).toHaveLength(0);
});

test('code document edits use a retained editor and Office opens through an honest external action',async({page})=>{
  await createWorkspace(page);await addMaterial(page);await page.getByRole('dialog').getByRole('button',{name:/^Code document /}).click();await page.getByLabel('Title',{exact:true}).fill('example.ts');await page.getByLabel('Language',{exact:true}).selectOption('typescript');await page.getByRole('textbox',{name:'Code content'}).fill('const example = 1;');await page.evaluate(()=>window.fixture.refresh());await expect(page.getByRole('textbox',{name:'Code content'})).toHaveText('const example = 1;');
  await page.getByRole('button',{name:'Save document',exact:true}).click();await expect(page.getByRole('heading',{name:'example.ts',exact:true})).toBeVisible();await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByRole('textbox',{name:'Code content'}).fill('const example = 2;');await page.getByRole('button',{name:'Save document',exact:true}).click();
  const resources=await page.evaluate(()=>window.fixture.resources());expect(resources).toHaveLength(1);const head=resources[0]?.heads[0];expect(head&&'surface' in head&&head.surface.type==='code_document'?head.surface.text:null).toBe('const example = 2;');
  await addMaterial(page);await page.getByRole('dialog').getByRole('button',{name:/^Word /}).click();await expect(page.getByRole('link',{name:'Open Word ↗'})).toHaveAttribute('href','https://word.cloud.microsoft/');await expect(page.locator('iframe')).toHaveCount(0);await expect(page.locator('.application-paper')).toContainText('browser tab');
});


test('plus picker preserves an unfinished draft and voice app capture recovers a lost receipt',async({page})=>{
  await createWorkspace(page);await startNote(page);await page.getByLabel('Title',{exact:true}).fill('Keep me');await page.getByLabel('Content',{exact:true}).fill('An unfinished idea');await page.getByRole('button',{name:'Keep draft & go back'}).click();
  await addMaterial(page);await page.getByRole('dialog').getByRole('button',{name:/^Code document /}).click();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('An unfinished idea');await page.getByRole('button',{name:'Discard local edit',exact:true}).click();
  await page.getByRole('button',{name:'Speak',exact:true}).click();await page.evaluate(()=>window.fixture.speech('Open Word'));await page.getByRole('button',{name:'Stop recording',exact:true}).click();await page.evaluate(()=>window.fixture.loseReceipt());await page.locator('#run-command').click();await page.getByRole('dialog').getByRole('button',{name:'Retry this change'}).click();
  await expect(page.getByRole('link',{name:'Open Word ↗'})).toBeVisible();expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(1);await page.locator('.capture-dock').getByRole('button',{name:'All material',exact:true}).click();await page.getByRole('button',{name:'Speak',exact:true}).click();await expect(page.locator('.capture-address')).toHaveText('Launch work');
});

test('dictation cannot insert into a replacement draft and can recover as a separate note',async({page})=>{
  await createWorkspace(page);await startNote(page);await page.getByLabel('Title',{exact:true}).fill('First draft');await page.getByLabel('Content',{exact:true}).fill('Original');await page.getByRole('button',{name:'Speak',exact:true}).click();await page.evaluate(()=>window.fixture.speech('Open Word'));await page.getByRole('button',{name:'Stop recording',exact:true}).click();await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Discard local edit'}).click();await startNote(page);await page.getByLabel('Title',{exact:true}).fill('Replacement');await page.getByLabel('Content',{exact:true}).fill('Original');await page.getByRole('button',{name:'Review capture'}).click();await page.getByRole('button',{name:'Insert into draft'}).click();
  await expect(page.getByRole('dialog')).toContainText('original editor is no longer open');await page.getByRole('button',{name:'Keep as a separate note'}).click();await page.getByRole('button',{name:'Save as note'}).click();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Original');const saved=await page.evaluate(()=>window.fixture.resources());expect(saved).toHaveLength(1);expect(saved[0]?.heads[0]).toHaveProperty('body','Open Word');
});


test('code dictation replaces the captured selection and retains its editor',async({page})=>{
  await createWorkspace(page);await addMaterial(page);await page.getByRole('dialog').getByRole('button',{name:/^Code document /}).click();await page.getByRole('textbox',{name:'Code content'}).fill('const draft = 1;');await page.getByRole('textbox',{name:'Code content'}).press('Control+Home');for(let index=0;index<6;index++)await page.keyboard.press('Shift+ArrowRight');
  await page.getByRole('button',{name:'Speak',exact:true}).click();await page.evaluate(()=>window.fixture.speech('let '));await page.getByRole('button',{name:'Stop recording'}).click();await page.getByRole('button',{name:'Insert into draft'}).click();await expect(page.getByRole('textbox',{name:'Code content'})).toHaveText('let draft = 1;');expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(0);
});


test('failed rerecording keeps the previous transcript and exposes the error',async({page})=>{
  await createWorkspace(page);await page.getByRole('button',{name:'Speak',exact:true}).click();await page.evaluate(()=>window.fixture.speech('Keep the previous thought'));await page.getByRole('button',{name:'Stop recording'}).click();await page.evaluate(()=>window.fixture.failSpeech());await page.getByRole('button',{name:'Record again'}).click();await expect(page.locator('.voice-status')).toContainText('Voice could not start');await expect(page.getByLabel('Spoken transcript · editable')).toHaveValue('Keep the previous thought');
});


test('update is conditional at the top level, preserves drafts and guards empty recording',async({page})=>{
  await createWorkspace(page);await expect(page.getByRole('button',{name:'Update available',exact:true})).toHaveCount(0);await startNote(page);await page.getByLabel('Content',{exact:true}).fill('Keep this unfinished draft');await page.evaluate(()=>window.fixture.offerUpdate());
  await expect(page.locator('.topbar').getByRole('button',{name:'Update available',exact:true})).toBeVisible();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Keep this unfinished draft');expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(true);
  await page.getByRole('button',{name:'Update available',exact:true}).click();expect(await page.evaluate(()=>window.fixture.updateOpens())).toBe(1);await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Keep this unfinished draft');await page.evaluate(()=>window.fixture.offerUpdate(false));await expect(page.getByRole('button',{name:'Update available',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Discard local edit'}).click();await page.getByRole('button',{name:'Speak',exact:true}).click();expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(true);await page.getByRole('button',{name:'Cancel recording'}).click();expect(await page.evaluate(()=>window.fixture.hasUnsubmittedInput())).toBe(false);
});

test('Settings has its own destination while workspace switching stays focused',async({page})=>{
  await createWorkspace(page);await openPicker(page);await expect(page.getByRole('dialog').getByRole('button',{name:'Create workspace',exact:true})).toBeVisible();await expect(page.getByRole('dialog').getByRole('button',{name:'Appearance',exact:true})).toHaveCount(0);await expect(page.getByRole('dialog').getByRole('button',{name:'Connections',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Close dialog'}).click();await openSettings(page);await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Appearance',exact:true})).toBeVisible();await expect(page.getByRole('dialog').getByRole('button',{name:'Create workspace',exact:true})).toHaveCount(0);await page.keyboard.press('Escape');await settingsReturn(page);
});

test('live resize retains the editor and Settings across desktop, split-window, phone and landscape sizes',async({page})=>{
  await createWorkspace(page);await createNote(page);await openNote(page);await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByLabel('Content',{exact:true}).fill('An unfinished draft stays put during resizing.');const editor=await page.getByLabel('Content',{exact:true}).elementHandle();await page.evaluate(()=>window.fixture.offerUpdate());
  const sizes=[{width:1440,height:900},{width:1024,height:768},{width:744,height:1133},{width:540,height:720},{width:320,height:568},{width:844,height:390}];
  // Capture the post-update element: the update announcement may legitimately render.
  const retained=await page.getByLabel('Content',{exact:true}).elementHandle();
  for(const size of sizes){await page.setViewportSize(size);await expect(page.getByLabel('Content',{exact:true})).toHaveValue('An unfinished draft stays put during resizing.');expect(await retained!.evaluate(node=>node===document.getElementById('note-body'))).toBe(true);const bounds=await page.evaluate(()=>({width:document.documentElement.scrollWidth,viewport:innerWidth,targets:Array.from(document.querySelectorAll('.topbar button,.capture-dock button')).map(el=>{const b=el.getBoundingClientRect();return {width:b.width,height:b.height,left:b.left,right:b.right}})}));expect(bounds.width).toBeLessThanOrEqual(bounds.viewport);for(const target of bounds.targets){expect(target.width).toBeGreaterThanOrEqual(32);expect(target.height).toBeGreaterThanOrEqual(32);expect(target.left).toBeGreaterThanOrEqual(0);expect(target.right).toBeLessThanOrEqual(size.width)}}
  await openSettings(page);await page.getByRole('button',{name:'Appearance',exact:true}).focus();
  for(const size of sizes){await page.setViewportSize(size);await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Appearance',exact:true})).toBeFocused();const box=(await page.getByRole('dialog').boundingBox())!;expect(box.x).toBeGreaterThanOrEqual(0);expect(box.y).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(size.width);expect(box.y+box.height).toBeLessThanOrEqual(size.height)}
  await page.keyboard.press('Escape');await settingsReturn(page);await expect(page.getByLabel('Content',{exact:true})).toHaveValue('An unfinished draft stays put during resizing.');await editor?.dispose();await retained?.dispose();
});

test('glass preferences provide solid readable controls and preserve keyboard access',async({page})=>{
  await createWorkspace(page);await createNote(page);await expect(page.locator('.topbar')).toHaveCSS('backdrop-filter','none');await expect(page.locator('.resource-card')).toHaveCSS('backdrop-filter','none');
  const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-transparency',value:'reduce'},{name:'prefers-reduced-motion',value:'reduce'}]});expect(await page.evaluate(()=>matchMedia('(prefers-reduced-transparency: reduce)').matches)).toBe(true);await expect(page.locator('.topbar')).toHaveCSS('backdrop-filter','none');await expect(page.locator('.capture-dock')).toHaveCSS('backdrop-filter','none');await openSettings(page);await expect(page.getByRole('dialog')).toHaveCSS('backdrop-filter','none');await page.keyboard.press('Escape');await settingsReturn(page);
  await cdp.send('Emulation.setEmulatedMedia',{features:[]});await page.emulateMedia({contrast:'more',reducedMotion:'reduce'});await expect(page.locator('.topbar')).toHaveCSS('backdrop-filter','none');await expect(page.locator('.topbar')).toHaveCSS('border-top-width','2px');await page.emulateMedia({forcedColors:'active'});await expect(page.locator('.topbar')).toHaveCSS('backdrop-filter','none');await openSettings(page);await expect(page.getByRole('button',{name:'Appearance',exact:true})).toBeVisible();await cdp.detach();
});

test('touch Settings navigation survives a tablet split-window resize',async({browser,baseURL})=>{
  const context=await browser.newContext({...(baseURL?{baseURL}:{}),viewport:{width:1024,height:768},isMobile:true,hasTouch:true});try{const page=await context.newPage();await createWorkspace(page);await openSettings(page);await page.setViewportSize({width:540,height:720});await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();await page.getByRole('button',{name:'Appearance',exact:true}).tap();await page.getByRole('button',{name:'Dark',exact:true}).tap();await expect(page.locator('html')).toHaveAttribute('data-theme','dark');await page.getByRole('button',{name:'Close dialog'}).tap();await settingsReturn(page);expect(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches)).toBe(true)}finally{await context.close()}
});


test('Settings addresses the named workspace and retains separately scoped key input',async({page})=>{
  await page.goto('/tests/host.html?provider');await page.getByLabel('Workspace name',{exact:true}).fill('Work');await page.getByRole('button',{name:'Create workspace',exact:true}).click();await typeInput(page);await page.getByLabel('Your words').fill('Keep this idea in Work');await page.getByRole('button',{name:'Close dialog'}).click();await createWorkspace(page,'Private',false);
  await openSettings(page);await expect(page.getByRole('heading',{name:'Private',exact:true})).toBeVisible();await page.getByRole('button',{name:'OpenAI',exact:true}).click();await expect(page.locator('.voice-settings .capture-address')).toHaveText('Private');await page.getByLabel('OpenAI API key').fill('SYNTHETIC_ONLY_UNSUBMITTED_KEY');await page.getByRole('button',{name:'Close dialog'}).click();await switchTo(page,'Work');
  await openSettings(page);await page.getByRole('button',{name:'OpenAI',exact:true}).click();await expect(page.locator('.voice-settings .capture-address')).toHaveText('Private');await expect(page.getByLabel('OpenAI API key')).toHaveValue('SYNTHETIC_ONLY_UNSUBMITTED_KEY');await expect(page.getByRole('dialog')).toContainText('unfinished connection change belongs');expect(await page.evaluate(()=>window.fixture.connectionWrites().length)).toBe(0);await page.getByRole('button',{name:'Close dialog'}).click();await page.getByRole('button',{name:'Review capture'}).click();await page.getByRole('button',{name:'Record again'}).click();await expect(page.locator('.voice-settings .capture-address')).toHaveText('Private');await expect(page.getByLabel('OpenAI API key')).toHaveValue('SYNTHETIC_ONLY_UNSUBMITTED_KEY');
});
