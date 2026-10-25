'use strict';

let applicationLayoutFrame=0, applicationLayoutSequence=0, lastApplicationLayout='', forceApplicationLayout=false;

// Only the local shell uses this geometry. Remote application pages never receive
// its channel; their views and browsing profiles belong to the native host.
function intersectAppRect(a,b) {
  const x=Math.max(a.x,b.x),y=Math.max(a.y,b.y);
  const right=Math.min(a.x+a.width,b.x+b.width),bottom=Math.min(a.y+a.height,b.y+b.height);
  return right>x&&bottom>y?{x,y,width:right-x,height:bottom-y}:null;
}
function subtractAppRect(rect,cover) {
  const overlap=intersectAppRect(rect,cover);
  if(!overlap)return [rect];
  return [
    {x:rect.x,y:rect.y,width:rect.width,height:overlap.y-rect.y},
    {x:rect.x,y:overlap.y+overlap.height,width:rect.width,height:rect.y+rect.height-overlap.y-overlap.height},
    {x:rect.x,y:overlap.y,width:overlap.x-rect.x,height:overlap.height},
    {x:overlap.x+overlap.width,y:overlap.y,width:rect.x+rect.width-overlap.x-overlap.width,height:overlap.height}
  ].filter(value=>value.width>.1&&value.height>.1);
}
function appRect(node) {
  const rect=node.getBoundingClientRect();
  return {x:rect.x,y:rect.y,width:rect.width,height:rect.height};
}
function applicationPlaceholder(surface,message,focus=false) {
  const placeholder=surface.querySelector('.application-placeholder');
  if(!placeholder)return;
  placeholder.querySelector('p').textContent=message;
  placeholder.querySelector('button').hidden=!focus;
}
function clearApplicationPreviews() {
  document.querySelectorAll('.application-snapshot').forEach(node=>{node.hidden=true;node.removeAttribute('src');});
}
function applicationPreviews(event) {
  if($('scrim').hidden||event.epoch!==state?.application_epoch)return;
  for(const preview of event.previews||[]) {
    const node=cards.get(preview.id)?.node.querySelector('.application-snapshot');
    if(node&&typeof preview.image==='string'&&preview.image.startsWith('data:image/png;base64,')) {
      node.src=preview.image;node.hidden=false;
    }
  }
}
function applicationSlots() {
  if(!state)return [];
  const viewport=appRect($('viewport'));
  const surfaces=[...$('world').querySelectorAll('.surface')].filter(node=>!node.hidden)
    .sort((a,b)=>(Number(getComputedStyle(a).zIndex)||0)-(Number(getComputedStyle(b).zIndex)||0));
  const controls=[...document.querySelectorAll('.canvas-tools > *, .composer, .canvas-bottom > *, .toast, .source-tray, .agent-bubble')]
    .filter(node=>node.getClientRects().length&&!node.hidden).map(appRect);
  const slots=[];
  const applications=surfaces.map((surface,index)=>({surface,index})).filter(({surface})=>surface.querySelector('[data-application-viewport]'));
  applications.sort((a,b)=>(b.surface.dataset.node===selectedCard)-(a.surface.dataset.node===selectedCard)||b.index-a.index);
  for(const {index,surface} of applications) {
    const node=surface.querySelector('[data-application-viewport]');
    const app=items.get(surface.dataset.node)?.application;
    applicationPlaceholder(surface,app?.error||app?.message||'Opening application…');
    if(!$('scrim').hidden||document.hidden||app?.live===false)continue;
    const rect=appRect(node),visible=intersectAppRect(rect,viewport);
    if(!visible)continue;
    if(slots.length===12){applicationPlaceholder(surface,'Focus this app to interact. Up to 12 apps can be visible at once.',true);continue;}
    let clips=[visible];
    for(const cover of [...controls,...surfaces.slice(index+1).map(appRect)]) {
      clips=clips.flatMap(part=>subtractAppRect(part,cover));
      if(clips.length>48){applicationPlaceholder(surface,'Focus this app for an unobstructed view.',true);clips=[];break;}
    }
    if(clips.length)slots.push({id:surface.dataset.node,rect,clips,zoom:camera.zoom,z:index});
  }
  return slots.sort((a,b)=>a.z-b.z);
}
function requestApplicationLayout(force=false) {
  forceApplicationLayout=forceApplicationLayout||force;
  if(applicationLayoutFrame)return;
  applicationLayoutFrame=requestAnimationFrame(()=>{
    applicationLayoutFrame=0;
    if(!native||isBar||!state)return;
    const mode=document.hidden?'hidden':!$('scrim').hidden?'modal':'visible';
    const slots=applicationSlots(),value={kind:'app_layout',space:state.space.id,epoch:state.application_epoch||'preview',mode,slots};
    const serialized=JSON.stringify(value);
    if(!lastApplicationLayout&&!slots.length&&!state.items.some(item=>item.kind==='application'))return;
    if(forceApplicationLayout||serialized!==lastApplicationLayout) {
      lastApplicationLayout=serialized;
      send({...value,sequence:++applicationLayoutSequence});
    }
    forceApplicationLayout=false;
  });
}

function applicationUrl(input) {
  const text=input.value.trim();
  const local=/^(localhost|127\.0\.0\.1)(:\d+)?([/?#]|$)/i.test(text);
  try {
    const url=new URL(/^[a-z][a-z\d+.-]*:\/\//i.test(text)?text:(local?'http://':'https://')+text);
    if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw Error();
    input.setCustomValidity('');
    return url.href;
  } catch {
    input.setCustomValidity('Enter a web address, such as https://vscode.dev.');
    input.reportValidity();
    return null;
  }
}
function openWebAppDialog() {
  openDialog('Open a web app','<p>Bring an application into this workspace.</p><form class="dialog-form" id="web-app-form"><label for="web-app-url">Web address</label><input id="web-app-url" type="text" inputmode="url" placeholder="https://" maxlength="8192" autocomplete="off" spellcheck="false" required><div class="buttons"><button class="button primary" type="submit">Open in canvas '+icon('arrow')+'</button></div></form><div class="application-shortcuts"><button class="button" data-web-app-url="https://www.microsoft365.com/">'+icon('doc')+' Microsoft 365</button><button class="button" data-web-app-url="https://vscode.dev/">'+icon('globe')+' VS Code</button></div>','web_app');
}
function openWebApp(url) {
  if(!state)return;
  closeDialog();
  send({kind:'new_web_app',url});
}

function applicationButton(id,action,glyph,label) {
  return '<button type="button" class="icon-button application-'+action+'" data-application="'+esc(id)+'" data-application-action="'+action+'" aria-label="'+esc(label)+'" title="'+esc(label)+'">'+icon(glyph)+'</button>';
}
function renderApplicationBody(body,item) {
  const app=item.application;
  body.className='card-body application-body';
  if(!body.querySelector('[data-application-viewport]')) {
    body.innerHTML='<form class="application-navigation" data-application-address="'+esc(item.id)+'">'+
      applicationButton(item.id,'back','chevron','Back')+applicationButton(item.id,'forward','chevron','Forward')+
      applicationButton(item.id,'reload','refresh','Reload page')+
      '<label class="application-address">'+icon('globe')+'<input type="text" inputmode="url" aria-label="Address for '+esc(item.title)+'" maxlength="8192" autocomplete="off" spellcheck="false" autocapitalize="off"></label>'+
      '<button type="submit" class="icon-button application-go" aria-label="Go to address" title="Go to address">'+icon('arrow')+'</button></form>'+
      '<div class="application-viewport" data-application-viewport="'+esc(item.id)+'"><div class="application-placeholder">'+icon('globe')+'<p>Opening application…</p>'+itemButton(item.id,'focus','Focus this app','button')+'</div><img class="application-snapshot" alt="" aria-hidden="true" hidden></div>';
  }
  const address=body.querySelector('.application-address input');
  if(document.activeElement!==address&&address.value!==app.url)address.value=app.url;
  body.querySelector('[data-application-action="back"]').disabled=!app.can_back;
  body.querySelector('[data-application-action="forward"]').disabled=!app.can_forward;
  body.querySelector('.application-placeholder p').textContent=app.error||app.message||'Opening application…';
  body.querySelector('.application-placeholder button').hidden=true;
  body.classList.toggle('loading',!!app.loading);
  body.setAttribute('aria-busy',String(!!app.loading));
}
function applicationFooter(item) {
  const app=item.application,label=app.loading?'Loading…':app.error?'Page unavailable':'Web application';
  return '<footer class="surface-footer"><span>'+esc(label)+'</span><div class="footer-actions">'+
    '<button data-application="'+esc(item.id)+'" data-application-action="ask_agent" title="Ask the agent to work in this app">'+icon('agent')+' Ask agent</button>'+
    '<button data-application="'+esc(item.id)+'" data-application-action="capture"'+(app.can_capture?'':' disabled')+'>Use page as source '+icon('plus')+'</button>'+
    '<button data-application="'+esc(item.id)+'" data-application-action="external" aria-label="Open in external browser" title="Open in external browser">'+icon('external')+'</button></div></footer>';
}
