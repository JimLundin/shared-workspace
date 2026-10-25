import {test,expect,type Page} from '@playwright/test';
async function createWorkspace(page:Page,title='Launch work',first=true){
  if(first)await page.goto('/tests/host.html');
  else {await page.getByRole('button',{name:/Current workspace:/}).click();await page.getByRole('button',{name:'Create workspace',exact:true}).click()}
  await page.getByLabel('Workspace name',{exact:true}).fill(title);
  await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await expect(page.getByRole('button',{name:'Current workspace: '+title})).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
}
async function createNote(page:Page,title='Launch brief',body='The launch starts with three pilot teams.'){
  await page.getByRole('button',{name:'Add a note',exact:true}).click();
  await page.getByLabel('Title',{exact:true}).fill(title);await page.getByLabel('Content',{exact:true}).fill(body);
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.locator('.editor')).toHaveCount(0);await expect(page.getByRole('button',{name:'Open '+title,exact:true})).toBeVisible();
}
async function switchTo(page:Page,title:string){await page.getByRole('button',{name:/Current workspace:/}).click();await page.getByRole('dialog').getByRole('button',{name:title,exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0)}
async function openNote(page:Page,title='Launch brief'){await page.getByRole('button',{name:'Open '+title,exact:true}).click()}
async function share(page:Page){
  const share=page.getByRole('button',{name:'Share this workspace',exact:true});
  if(!await share.isVisible())await page.getByRole('button',{name:'Connections',exact:true}).click();
  await share.click();
}
async function openJoin(page:Page){await page.getByRole('button',{name:/Current workspace:/}).click();await page.getByRole('button',{name:'Join existing work',exact:true}).click()}

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
  await createWorkspace(page);await createNote(page);await openNote(page);await page.getByText('Organize & history',{exact:true}).click();
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
  await page.getByLabel('Title',{exact:true}).fill('Corrected title');await page.evaluate(()=>window.fixture.offline());await page.getByRole('button',{name:'Save note',exact:true}).click();await expect(page.locator('.editor')).toHaveCount(0);await expect(page.locator('.paper h2')).toHaveText('Corrected title');await expect(page.locator('.connection-status')).toContainText('Offline');
});

test('typed capture keeps workspace across navigation and failed receipt retry cannot duplicate it',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');
  await page.getByRole('button',{name:'Type a command',exact:true}).click();await page.getByLabel('Command text',{exact:true}).fill('Remember the pilot decision');await page.getByRole('button',{name:'Close dialog'}).click();await switchTo(page,'Private');
  await page.getByRole('button',{name:'Continue captured input',exact:false}).click();await expect(page.getByLabel('Command text',{exact:true})).toHaveValue('Remember the pilot decision');await expect(page.getByRole('button',{name:'Run command →'})).toBeDisabled();await page.getByRole('button',{name:'Return to Launch work'}).click();
  await page.evaluate(()=>window.fixture.loseReceipt());await page.getByRole('button',{name:'Run command →'}).click();await page.getByRole('dialog').getByRole('button',{name:'Retry this change'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  const commands=await page.evaluate(()=>window.fixture.commands());expect(commands.at(-1)).toEqual(commands.at(-2));expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(1);
  await page.getByRole('button',{name:'Type a command',exact:false}).click();await expect(page.getByLabel('Command text')).toHaveValue('');
});

test('voice transcript stays addressed and cancelled late speech cannot overwrite newer input',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');
  await page.evaluate(()=>document.addEventListener('focusin',event=>{if((event.target as HTMLElement).id==='command-text')document.documentElement.dataset.commandFocusSeen='true'}));
  await page.getByRole('button',{name:'Speak a command',exact:true}).click();expect(await page.locator('html').getAttribute('data-command-focus-seen')).toBeNull();await page.evaluate(()=>window.fixture.speech('Remember a spoken thought'));
  await expect(page.getByLabel('Spoken transcript · editable')).toHaveValue('Remember a spoken thought');await page.getByRole('button',{name:'Close dialog'}).click();await switchTo(page,'Private');
  await page.evaluate(()=>window.fixture.speech('Late voice must not replace the thought',0));await page.getByRole('button',{name:/Continue captured input/}).click();await expect(page.getByLabel('Spoken transcript · editable')).toHaveValue('Remember a spoken thought');await expect(page.getByRole('button',{name:'Run command →'})).toBeDisabled();
  await page.getByRole('button',{name:'Return to Launch work'}).click();await page.getByRole('button',{name:'Run command →'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  expect((await page.evaluate(()=>window.fixture.resources()))[0]?.heads[0]?.body).toBe('a spoken thought');
  await page.evaluate(()=>window.fixture.failSpeech());await page.getByRole('button',{name:'Speak a command',exact:true}).click();await expect(page.locator('.voice-status')).toContainText('Type your command');await page.getByLabel('Command text').fill('Remember typed fallback');await page.getByRole('button',{name:'Run command →'}).click();expect(await page.evaluate(()=>window.fixture.resources().length)).toBe(2);
});

test('unfinished editor survives workspace switch and cannot be replaced by a new note',async({page})=>{
  await createWorkspace(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');await page.getByRole('button',{name:'Add a note',exact:true}).click();await page.getByLabel('Title',{exact:true}).fill('Unfinished');await page.getByLabel('Content',{exact:true}).fill('Keep this thought');
  await switchTo(page,'Private');await page.getByRole('button',{name:'Add a note',exact:true}).click();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('Keep this thought');await expect(page.getByRole('button',{name:'Current workspace: Launch work'})).toBeVisible();
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
  await page.evaluate(()=>window.fixture.holdNextJoin());await page.getByRole('button',{name:'Try joining again',exact:true}).click();await page.evaluate(()=>window.fixture.joinStage('saving_workspace'));await expect(page.getByRole('button',{name:'Joining…',exact:true})).toBeDisabled();await page.evaluate(()=>window.fixture.finishJoin(['Received work']));await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByRole('button',{name:'Current workspace: Received work'})).toBeVisible();
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
  const trigger=page.getByRole('button',{name:/Current workspace:/});await trigger.click();await expect(page.locator('dialog:modal')).toHaveCount(1);await page.locator('[data-action=new-note]').evaluate(el=>(el as HTMLElement).focus());expect(await page.evaluate(()=>!!document.activeElement?.closest('dialog'))).toBe(true);
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
    await page.getByRole('button',{name:/Current workspace:/}).click();await expect(page.getByRole('button',{name:'Close dialog'})).toBeInViewport();await page.getByRole('button',{name:'Close dialog'}).click();
  }
  await page.getByRole('button',{name:/Type a command/}).click();await page.getByLabel('Command text').fill('Remember a thought');await page.getByRole('button',{name:'Run command →'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('opening from a scrolled list starts reader at title and polling preserves reader position',async({page})=>{
  await page.setViewportSize({width:390,height:620});await createWorkspace(page);for(let i=0;i<5;i++)await createNote(page,'Note '+i,'A long page.\n'.repeat(100));
  await page.getByRole('button',{name:'Open Note 4',exact:true}).click();await expect(page.getByRole('heading',{name:'Note 4',exact:true})).toBeInViewport();await expect(page.getByRole('button',{name:'Edit',exact:true})).toBeInViewport();await expect(page.getByRole('heading',{name:'Note 4',exact:true})).toBeFocused();
  await page.locator('.main').evaluate(el=>el.scrollTop=400);await page.evaluate(()=>window.fixture.refresh());expect(await page.locator('.main').evaluate(el=>el.scrollTop)).toBe(400);
});

test('editing a reference opens the same resource immediately in its original workspace',async({page})=>{
  await createWorkspace(page);await createNote(page);await createWorkspace(page,'Private',false);await switchTo(page,'Launch work');await openNote(page);await page.getByText('Organize & history',{exact:true}).click();await page.getByRole('button',{name:'Add reference',exact:true}).click();await switchTo(page,'Private');await openNote(page);
  await page.getByRole('button',{name:'Edit',exact:true}).click();await expect(page.getByLabel('Content',{exact:true})).toHaveValue('The launch starts with three pilot teams.');await expect(page.getByRole('button',{name:'Current workspace: Launch work'})).toBeVisible();await page.getByLabel('Content',{exact:true}).fill('Edited through a reference');await page.getByRole('button',{name:'Save note',exact:true}).click();await switchTo(page,'Private');await expect(page.locator('.note-body')).toHaveText('Edited through a reference');
  const resources=await page.evaluate(()=>window.fixture.resources());expect(resources).toHaveLength(1);
});

test('system dictation focuses the input before handing control to Windows',async({page})=>{
  await page.goto('/tests/host.html?dictation');await page.getByLabel('Workspace name',{exact:true}).fill('Work');await page.getByRole('button',{name:'Create workspace',exact:true}).click();await page.getByRole('button',{name:'Speak a command',exact:true}).click();
  await expect(page.getByLabel('Command text',{exact:true})).toBeFocused();await page.getByLabel('Command text',{exact:true}).fill('Remember Windows dictation');await page.getByRole('button',{name:'Finish input'}).click();await page.getByRole('button',{name:'Run command →'}).click();expect((await page.evaluate(()=>window.fixture.resources()))[0]?.heads[0]?.body).toBe('Windows dictation');
});
