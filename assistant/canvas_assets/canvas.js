'use strict';

// Layout and visual vocabulary are the connected-work sketch. State and actions
// arrive through the local native bridge; this page never executes shell text.
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const icon = name => '<svg class="ico" aria-hidden="true"><use href="#' + name + '"/></svg>';
const actionButton = (action, text, classes = 'button ghost') => '<button class="' + classes + '" data-action="' + action + '">' + text + '</button>';
const itemButton = (id, action, text, classes = '', label = '') => '<button class="' + classes + '" data-item="' + esc(id) + '" data-item-action="' + action + '"' + (label ? ' aria-label="' + esc(label) + '"' : '') + '>' + text + '</button>';
const number = value => /^-?\d+(\.\d+)?$/.test(value) ? new Intl.NumberFormat('en-GB').format(Number(value)) : value;
const cards = new Map();
const dirtyEdits = new Map();
const composerEdits = new Map();
let native = null, state = null, positions = new Map(), items = new Map();
let camera = {x:470, y:360, zoom:.9}, mode = 'canvas', savedCamera = null;
let drag = null, selectedCard = null, modal = '', lastFocus = null, cameraTimer = null, statusTimer = null;
let launcherIndex = 0, launcherEntries = [], renderedFrame = 0, pendingState = null;
const parameters = new URLSearchParams(location.search);
const isBar = parameters.get('view') === 'bar';
document.body.classList.toggle('bar', isBar);
document.body.classList.toggle('hosted', parameters.get('hosted') === 'true' && !isBar);

function send(value) {
  if (native) native.command(JSON.stringify(value));
  else document.dispatchEvent(new CustomEvent('canvas-command', {detail: value}));
}
function announce(text) { $('announcement').textContent = text; }
function toast(text) {
  if (!text) return;
  clearTimeout(statusTimer);
  $('toast').textContent = text;
  $('toast').hidden = false;
  statusTimer = setTimeout(() => { $('toast').hidden = true; requestApplicationLayout(); }, 4500);
  requestApplicationLayout();
  announce(text);
}
function visibleIds() { return [...positions].filter(([,value]) => !value.parked).map(([id]) => id); }
function cardHeader(item) {
  const selected = state.space.selection.includes(item.id);
  const mark = item.kind==='application'?icon('globe'):item.icon === 'table' ? 'X' : item.icon === 'globe' ? 'e' : icon(item.icon || 'doc');
  const colour = item.kind === 'result' ? 'agent' : item.kind === 'draft' ? 'outlook' : item.icon === 'globe' ? 'edge' : item.icon === 'chat' ? 'teams' : '';
  const selection = item.kind==='application'?'':itemButton(item.id, 'select', icon('check'), 'select-source', 'Use ' + item.title + ' as context').replace('class="select-source"', 'class="select-source" aria-pressed="' + selected + '"');
  return '<header class="surface-head" data-drag="' + esc(item.id) + '"><div class="title">' + selection +
    '<span class="app-mark ' + colour + '">' + mark + '</span><span><strong>' + esc(item.title) + '</strong><small>' + esc(item.origin) + '</small></span></div>' +
    '<div class="surface-actions">' + itemButton(item.id, 'focus', icon('expand'), 'icon-button', 'Focus ' + item.title) +
    itemButton(item.id, 'park', icon('park'), 'icon-button', 'Park ' + item.title) +
    itemButton(item.id, 'menu', '···', 'icon-button', 'Options for ' + item.title) + '</div></header>';
}
function sourceFooter(item) {
  if(item.meeting)return meetingFooter(item);
  if(item.kind==='application')return applicationFooter(item);
  const receipt = state.draft_receipts?.[item.id];
  const actions = item.kind === 'source'
    ? item.captured_app ? itemButton(item.id,'open','Show app '+icon('external')) : item.source_kind === 'excerpt' ? itemButton(item.id, 'copy', 'Copy excerpt') : itemButton(item.id, 'refresh', 'Read again') + itemButton(item.id, 'open', 'Open original ' + icon('external'))
    : item.kind === 'draft'
      ? itemButton(item.id, 'copy', 'Copy') + itemButton(item.id, 'export', 'Export') +
        '<button data-save-draft="' + esc(item.id) + '" data-revision="' + item.revision + '"' + (receipt?.status === 'pending' ? ' disabled' : '') + '>Save email draft ' + icon('external') + '</button>'
      : itemButton(item.id, 'copy', 'Copy');
  const label = item.kind === 'draft' ? (receipt?.label || 'Your writing · saved locally') : 'v' + item.revision + (item.scope ? ' · ' + item.scope : '');
  return '<footer class="surface-footer"><span title="' + esc(label + (receipt?.path ? ' · ' + receipt.path : '')) + '">' + esc(label) +
    '</span><div class="footer-actions">' + actions + '</div></footer>';
}
function tableBody(item) {
  const rows = item.rows.map((row, index) => {
    const total = row[0].toLowerCase() === 'total';
    return '<tr class="' + (total ? 'total' : 'range-row') + '"><td class="row-no">' + (index + 2) + '</td>' +
      row.map(cell => '<td>' + esc(number(cell)) + '</td>').join('') + '</tr>';
  }).join('');
  return '<div class="range-label"><span>Observed table / selected source</span><span>' + item.rows.length + ' rows</span></div>' +
    '<table class="budget-table"><thead><tr><th class="row-no"></th>' + item.columns.map(column => '<th>' + esc(column) + '</th>').join('') +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
}
function sourceTags(item) {
  return (item.sources || []).map(source => itemButton(source.id, 'focus', esc(source.title), 'cite', 'Show source ' + source.title)).join('');
}
function resultBody(item) {
  const running = ['running','question','review','attention'].includes(item.status);
  let content = item.stale ? '<div class="stale-banner"><span>Source changed · result needs updating</span>' + itemButton(item.id, 'refresh', 'Update') + '</div>' : '';
  content += '<div class="result-kicker"><span>' + (item.example ? 'EXAMPLE COMPARISON' : running ? 'WORK IN PROGRESS' : 'LINKED RESULT') + '</span><span class="tag">' +
    (item.sources || []).length + ' sources · v' + item.revision + '</span></div>';
  content += '<div class="result-copy">' + item.html + '</div>';
  if (running || item.status === 'failed' || item.status === 'stopped') {
    content += '<div class="result-running"><span class="dot"></span>' + esc(item.label) + '</div>';
  }
  if (item.status === 'question') {
    content += '<form class="question-box" data-question-item="' + esc(item.id) + '" data-question="' + esc(item.question.id) + '"><h3>' +
      esc(item.question.title) + '</h3><p>' + esc(item.question.detail) + '</p><input aria-label="Answer the agent question" name="answer" maxlength="8000" required autocomplete="off">' +
      '<button class="button primary" type="submit">Continue ' + icon('arrow') + '</button></form>';
  } else if (item.status === 'review') {
    content += '<div class="question-box"><h3>' + esc(item.review.title) + '</h3><p>' + esc(item.review.detail) + '</p>' +
      actionButton('native_work', 'Review action ' + icon('arrow'), 'button primary') + '</div>';
  }
  content += '<div class="result-buttons">';
  if (item.status === 'attention') content += itemButton(item.id, 'retry', 'Retry submission', 'button primary');
  else if (!running) content += itemButton(item.id, 'draft', 'Make a draft ' + icon('arrow'), 'button primary');
  if (running) content += itemButton(item.id, 'stop', 'Stop', 'button ghost');
  content += itemButton(item.id, 'sources', 'Sources', 'button ghost') + '</div>';
  content += '<div class="result-provenance">' + icon('link') + ((item.sources || []).length ? 'Based on ' + sourceTags(item) : 'No original source snapshots are attached.') + '</div>';
  if (item.steps?.length) content += '<details class="result-steps"><summary>' + item.steps.length + ' steps</summary><ol>' +
    item.steps.map(step => '<li>' + esc(step) + '</li>').join('') + '</ol></details>';
  return content;
}
function renderCard(item, placement) {
  let record = cards.get(item.id);
  if (!record) {
    const node = document.createElement('section');
    node.className = 'surface';
    node.dataset.node = item.id;
    node.setAttribute('aria-label', item.title);
    node.innerHTML = '<div class="header-slot"></div><div class="card-body"></div><div class="footer-slot"></div><div class="resize-handle" data-resize="' + esc(item.id) + '" aria-hidden="true"></div>';
    $('world').append(node);
    record = {node, itemKey:null, headerKey:null};
    cards.set(item.id, record);
  }
  const {node} = record;
  node.setAttribute('aria-label', item.title);
  node.hidden = placement.parked;
  node.classList.toggle('selected', state.space.selection.includes(item.id));
  node.classList.toggle('result', item.kind === 'result' || item.meeting?.kind === 'notes');
  node.classList.toggle('mail', item.kind === 'draft' || item.meeting?.kind === 'draft');
  node.classList.toggle('application', item.kind === 'application');
  const headerKey = JSON.stringify([item.title, item.origin, item.kind, item.icon, state.space.selection.includes(item.id)]);
  if (headerKey !== record.headerKey) {
    const focusedAction = node.contains(document.activeElement) ? document.activeElement?.dataset.itemAction : null;
    node.querySelector('.header-slot').innerHTML = cardHeader(item);
    record.headerKey = headerKey;
    if (focusedAction === 'select') node.querySelector('.select-source').focus({preventScroll:true});
  }
  const body = node.querySelector('.card-body');
  const key = JSON.stringify([item,state.draft_receipts?.[item.id]]);
  if (key !== record.itemKey) {
    if(item.kind==='application') {
      renderApplicationBody(body,item);
    } else if(item.meeting) {
      const kind=item.meeting.kind;
      body.className='card-body meeting-body '+(kind==='source'?'doc transcript':kind==='draft'?'meeting-paper':kind==='notes'?'':'message');
      body.innerHTML=meetingBody(item);
    } else if (item.kind === 'note' || item.kind === 'draft') {
      body.className = 'card-body ' + (item.kind === 'note' ? 'message' : '');
      let editor = body.querySelector('textarea');
      if (!editor) {
        body.innerHTML = (item.kind === 'draft' ? '<div class="draft-source"></div>' : '') +
          '<textarea class="note-editor" aria-label="' + esc('Edit ' + item.title) + '" maxlength="64000" spellcheck="false"></textarea>';
        editor = body.querySelector('textarea');
        editor.value = item.text;
        editor.addEventListener('input', () => {
          dirtyEdits.set(item.id, editor.value);
          send({kind:'edit', id:item.id, text:editor.value});
        });
      }
      if (dirtyEdits.get(item.id) === item.text) dirtyEdits.delete(item.id);
      if (!dirtyEdits.has(item.id) && document.activeElement !== editor && editor.value !== item.text) editor.value = item.text;
      const source = body.querySelector('.draft-source');
      if (source) source.textContent = item.stale ? 'A newer result is available. Your writing stays as you left it.' : 'From ' + (item.sources?.[0]?.title || 'your selected result');
    } else if (item.kind === 'result') {
      const answer = body.querySelector('.question-box input');
      const oldAnswer = answer?.value, hadFocus = answer === document.activeElement, question = body.querySelector('form')?.dataset.question;
      const openSteps = body.querySelector('details')?.open;
      body.className = 'card-body';
      body.innerHTML = resultBody(item);
      const nextAnswer = body.querySelector('.question-box input');
      if (nextAnswer && question === item.question?.id && oldAnswer != null) {
        nextAnswer.value = oldAnswer;
        if (hadFocus) nextAnswer.focus({preventScroll:true});
      }
      if (openSteps && body.querySelector('details')) body.querySelector('details').open = true;
    } else if (item.source_kind === 'table') {
      body.className = 'card-body sheet';
      body.innerHTML = tableBody(item);
    } else if (item.source_kind === 'window') {
      body.className = 'card-body';
      body.innerHTML = '<div class="window-preview" data-preview="' + esc(item.id) + '">' + icon('canvas') + '<p>' + esc(item.origin) +
        '</p>' + itemButton(item.id, 'open', 'Open original ' + icon('external'), 'button') + '</div>';
    } else {
      body.className = 'card-body ' + (item.source_kind === 'excerpt' ? 'message' : 'doc');
      body.innerHTML = item.html;
    }
    node.querySelector('.footer-slot').innerHTML = item.kind === 'result' || item.meeting?.kind === 'notes' ? '' : sourceFooter(item);
    record.itemKey = key;
  }
  positionCard(item.id, placement);
}
function positionCard(id, p) {
  const node = cards.get(id)?.node;
  if (!node) return;
  Object.assign(node.style, {left:p.x+'px', top:p.y+'px', width:p.width+'px', height:p.height+'px'});
  node.classList.toggle('focused', mode === 'focus' && id === selectedCard);
  requestApplicationLayout();
}
function renderGroup() {
  const sources = visibleIds().filter(id => items.get(id)?.meeting ? items.get(id).meeting.kind==='source' : ['source','note','application'].includes(items.get(id)?.kind));
  for (const id of ['source-group','group-heading','group-badge','source-tray']) $(id).hidden = !sources.length;
  if (!sources.length) return;
  const p = sources.map(id => positions.get(id));
  const left = Math.min(...p.map(x => x.x)) - 31, top = Math.min(...p.map(x => x.y)) - 73;
  const right = Math.max(...p.map(x => x.x + x.width)) + 33, bottom = Math.max(...p.map(x => x.y + x.height)) + 27;
  Object.assign($('source-group').style, {left:left+'px',top:top+'px',width:(right-left)+'px',height:(bottom-top)+'px'});
  Object.assign($('group-heading').style, {left:(left+29)+'px',top:(top+16)+'px'});
  $('group-heading').querySelector('span').textContent = state.meeting ? 'Meeting sources' : state.space.id === 'canvas-example' ? 'Launch review' : 'Working set';
  Object.assign($('group-badge').style, {left:(right-164)+'px',top:(top+17)+'px'});
  $('group-badge').textContent = sources.length + ' resources';
  const firstColumn = p.filter(x => x.x < left + 200);
  const trayTop = Math.max(...firstColumn.map(x => x.y+x.height)) + 23;
  $('source-tray').hidden = !!state.meeting || bottom - trayTop < 65;
  Object.assign($('source-tray').style, {left:(left+33)+'px',top:trayTop+'px',width:(firstColumn[0]?.width || 402)+'px'});
}
function renderWires() {
  const parts = [];
  for (const id of visibleIds()) {
    const item = items.get(id), to = positions.get(id);
    for (const [index, source] of (item?.sources || []).entries()) {
      const from = positions.get(source.id);
      if (!from || from.parked) continue;
      const sx = from.x+from.width, sy = from.y+from.height*.56, tx=to.x, ty=to.y+137+index*41;
      const bend = Math.max(44, Math.abs(tx-sx)*.5);
      parts.push('<path class="wire active" d="M'+sx+','+sy+' C'+(sx+bend)+','+sy+' '+(tx-bend)+','+ty+' '+tx+','+ty+'"/><circle class="wire-point" cx="'+tx+'" cy="'+ty+'" r="2.3"/>');
    }
    const citations=item?.meeting?.kind==='notes'?[...item.meeting.notes,...item.meeting.decisions].flatMap(value=>value.citations):item?.meeting?.citations||[];
    for(const citation of new Map(citations.map(value=>[value.card_id,value])).values()) {
      const from=positions.get(citation.card_id);
      if(!from || from.parked)continue;
      parts.push('<path class="wire '+(item.meeting.kind==='notes'?'active':'secondary')+'" d="M'+(from.x+from.width)+','+(from.y+from.height/2)+' C'+(from.x+from.width+70)+','+(from.y+from.height/2)+' '+(to.x-70)+','+(to.y+120)+' '+to.x+','+(to.y+120)+'"/>');
    }
  }
  $('wires').innerHTML = parts.join('');
}
function renderScope() {
  const selected = state.space.selection.map(id => items.get(id)).filter(Boolean);
  const key = JSON.stringify(selected.map(item => [item.id,item.title,item.icon]));
  if ($('scope-row').dataset.key !== key) {
    $('scope-row').dataset.key = key;
    $('scope-row').innerHTML = icon('layers') + (selected.length ? selected.slice(0,4).map(item => itemButton(item.id,'focus',icon(item.icon)+' '+esc(item.title),'scope-chip')).join('') : '<span>Ask about this workspace</span>') +
      '<span class="count">' + selected.length + ' selected</span>';
  }
  $('composer').hidden = !selected.length && !state.space.composer && document.activeElement !== $('request');
  if (composerEdits.get(state.space.id) === state.space.composer) composerEdits.delete(state.space.id);
  if (document.activeElement !== $('request') && !composerEdits.has(state.space.id)) $('request').value = state.space.composer;
  const parked = [...positions].filter(([,p])=>p.parked);
  $('parked-tray').innerHTML = actionButton('keyboard',icon('keyboard')+'Keyboard help','parked-button') +
    (parked.length ? actionButton('parked',icon('park')+'Parked · '+parked.length,'parked-button') : '');
}
function renderSnapshot(next) {
  if (!next?.space) return;
  const previous = state;
  const switched = state?.space.id !== next.space.id;
  if(state?.application_epoch!==next.application_epoch)clearApplicationPreviews();
  state = next;
  items = new Map(state.items.map(item=>[item.id,meetingPresentation(item)]));
  $('workspace-title').textContent = state.space.title;
  $('bar-layer').textContent = state.layer || 'BASE';
  $('clock').textContent = state.clock || '';
  $('agent-status').textContent = state.agent || 'Agent ready';
  $('record').querySelector('span').textContent = state.recording ? 'Stop recording' : 'Record';
  $('record').classList.toggle('recording', !!state.recording);
  $('stop-agent').hidden = !state.busy;
  if (isBar) return;
  if (switched && drag) {
    const pointer=drag.pointer;
    drag=null;
    if($('viewport').hasPointerCapture(pointer))$('viewport').releasePointerCapture(pointer);
    $('viewport').classList.remove('panning');
  }
  const oldPositions = positions;
  positions = new Map(state.placements.map(p=>[p.id,{...p}]));
  if (drag?.id && oldPositions.has(drag.id)) positions.set(drag.id,oldPositions.get(drag.id));
  if (switched) {
    camera = {...state.space.camera};
    mode = 'canvas'; savedCamera = null; selectedCard = null;
    $('request').value = composerEdits.get(state.space.id) ?? state.space.composer;
  }
  for (const [id, record] of cards) if (!positions.has(id) || !items.has(id)) { record.node.remove(); cards.delete(id); dirtyEdits.delete(id); }
  for (const [id,p] of positions) if (items.has(id)) renderCard(items.get(id),p);
  $('canvas-title').textContent = state.space.id === 'canvas-example' ? 'Launch recommendation' : state.space.title;
  const visible = visibleIds(), sourceCount = visible.filter(id=>items.get(id)?.meeting ? items.get(id).meeting.kind==='source' : ['source','note'].includes(items.get(id)?.kind)).length;
  const appCount=visible.filter(id=>items.get(id)?.kind==='application').length;
  const resultCount = visible.length-sourceCount-appCount;
  $('view-description').textContent = (appCount?appCount+' app'+(appCount===1?'':'s')+' · ':'')+sourceCount + ' source' + (sourceCount===1?'':'s') + ' · ' + resultCount + ' result' + (resultCount===1?'':'s');
  $('example-tag').hidden = state.space.id !== 'canvas-example';
  $('empty-workspace').hidden = !!visible.length;
  renderGroup(); renderWires(); renderScope(); applyCamera();
  if (state.status && state.status !== previous?.status) toast(state.status);
  if (modal === 'launcher' && JSON.stringify(state.catalogue) !== JSON.stringify(previous?.catalogue)) renderSearch();
  if (modal === 'work' && JSON.stringify(state.work) !== JSON.stringify(previous?.work)) renderWork();
  updateMeetingReceipt();
  if (previous && !switched && visible.length > previous.placements.filter(p=>!p.parked).length && !drag) frame(visible,'canvas');
  publishPreviews();
}
function queueSnapshot(next) {
  pendingState=next;
  if (renderedFrame) return;
  renderedFrame=requestAnimationFrame(()=>{renderedFrame=0;renderSnapshot(pendingState);pendingState=null;});
}
function applyCamera() {
  const rect=$('viewport').getBoundingClientRect();
  const x=rect.width/2-camera.x*camera.zoom, y=rect.height/2-camera.y*camera.zoom;
  $('world').style.transform='translate('+x+'px,'+y+'px) scale('+camera.zoom+')';
  $('viewport').style.setProperty('--grid-x',x+'px');
  $('viewport').style.setProperty('--grid-y',y+'px');
  $('viewport').style.setProperty('--grid-size',(24*camera.zoom)+'px');
  $('zoom-label').textContent=Math.round(camera.zoom*100)+'%';
  document.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',button.dataset.view===mode));
  requestApplicationLayout();
}
function saveCamera() {
  clearTimeout(cameraTimer);
  const space=state?.space.id, value={...camera};
  cameraTimer=setTimeout(()=>{if(space)send({kind:'camera',space,...value});},120);
}
function bounds(ids) {
  const p=ids.map(id=>positions.get(id)).filter(value=>value && !value.parked);
  if (!p.length) return null;
  const x=Math.min(...p.map(p=>p.x))-31, y=Math.min(...p.map(p=>p.y))-73;
  return {x,y,width:Math.max(...p.map(p=>p.x+p.width))-x+33,height:Math.max(...p.map(p=>p.y+p.height))-y+27};
}
function frame(ids, nextMode='canvas') {
  const box=bounds(ids); if(!box)return;
  const rect=$('viewport').getBoundingClientRect(), width=rect.width-45, height=Math.max(200,rect.height-185);
  const zoom=Math.max(.25,Math.min(width/box.width,height/box.height,nextMode==='focus'?1.25:nextMode==='overview'?.7:1.12));
  const originX=(rect.width-box.width*zoom)/2-box.x*zoom;
  const originY=72+(height-box.height*zoom)/2-box.y*zoom;
  camera={x:(rect.width/2-originX)/zoom,y:(rect.height/2-originY)/zoom,zoom};
  mode=nextMode;applyCamera();saveCamera();publishPreviews();
}
function focusCard(id) {
  if (!positions.has(id)) { toast('This source is not on this canvas. Its saved observation is available under Sources.'); return; }
  if (positions.get(id).parked) send({kind:'item',id,action:'restore'});
  if(mode==='canvas')savedCamera={...camera};
  selectedCard=id;frame([id],'focus');
  for(const [key,p] of positions)positionCard(key,p);
}
function zoom(factor, anchor) {
  const rect=$('viewport').getBoundingClientRect();
  const point=anchor||{x:rect.width/2,y:rect.height/2};
  const z=Math.max(.25,Math.min(1.5,camera.zoom*factor));
  camera={x:camera.x+(point.x-rect.width/2)*(1/camera.zoom-1/z),y:camera.y+(point.y-rect.height/2)*(1/camera.zoom-1/z),zoom:z};
  applyCamera();saveCamera();publishPreviews();
}
function setInert(value) {
  [...$('desktop').children].filter(node=>!['scrim','announcement'].includes(node.id)).forEach(node=>node.inert=value);
}
function openDialog(title, body, type='', wide=false) {
  if($('scrim').hidden)lastFocus=document.activeElement;
  modal=type;
  $('dialog').className='dialog'+(wide?' wide':'');
  $('dialog').dataset.kind=type;
  $('dialog').innerHTML='<header class="dialog-head"><h2 id="dialog-title">'+esc(title)+'</h2><button class="icon-button" data-action="close" aria-label="Close dialog">'+icon('close')+'</button></header><div class="dialog-body">'+body+'</div>';
  $('scrim').hidden=false;setInert(true);
  ($('dialog').querySelector('input,textarea')||$('dialog')).focus();
  publishPreviews();
}
function closeDialog() {
  const wasOpen=!$('scrim').hidden;
  $('scrim').hidden=true;modal='';setInert(false);
  clearApplicationPreviews();
  if(wasOpen)(lastFocus?.isConnected?lastFocus:$('viewport')).focus({preventScroll:true});
  publishPreviews();
}
function launcher() {
  openDialog('Find something to work with','<input class="search-input" id="resource-search" role="combobox" aria-expanded="true" aria-controls="search-results" aria-autocomplete="list" aria-label="Find apps, windows, documents and results" placeholder="Search apps, windows, documents, results…" autocomplete="off"><div class="resource-list" id="search-results" role="listbox" aria-label="Applications and saved sources"></div><div class="resource-description">Find a specific thing. Add it to this work, or open its application.</div>','launcher');
  launcherIndex=0;renderSearch();
}
function renderSearch() {
  if(modal!=='launcher')return;
  const query=$('resource-search').value.toLowerCase();
  const entries=[...(state?.catalogue||[]),...(state?.items||[]).map(item=>({id:item.id,title:item.title,detail:item.origin,icon:item.icon,kind:'saved'}))];
  const seen=new Set();
  launcherEntries=entries.filter(entry=>{if(seen.has(entry.id))return false;seen.add(entry.id);return(entry.title+' '+entry.detail).toLowerCase().includes(query);}).slice(0,80);
  launcherIndex=Math.max(0,Math.min(launcherIndex,launcherEntries.length-1));
  $('search-results').innerHTML=launcherEntries.length?launcherEntries.map((entry,index)=>'<button class="resource-row '+(index===launcherIndex?'selected':'')+'" id="launcher-option-'+index+'" role="option" aria-selected="'+(index===launcherIndex)+'" tabindex="-1" data-launch-index="'+index+'">'+icon(entry.icon||'canvas')+'<span><strong>'+esc(entry.title)+'</strong><small>'+esc(entry.detail)+'</small></span><span class="row-end">'+(entry.kind==='app'?'Open':'Add to canvas')+'</span>'+icon('arrow')+'</button>').join(''):'<p class="resource-description">No matching sources. Add a document or start a note.</p>';
  if(launcherEntries.length)$('resource-search').setAttribute('aria-activedescendant','launcher-option-'+launcherIndex);
  else $('resource-search').removeAttribute('aria-activedescendant');
}
function launchEntry(index) {
  const entry=launcherEntries[index];if(!entry)return;
  closeDialog();send({kind:'activate',id:entry.id});
}
function showSpaces() {
  openDialog('Your workspaces','<div class="resource-list">'+state.spaces.map(space=>'<button class="resource-row" data-space="'+esc(space.id)+'">'+icon('layers')+'<span><strong>'+esc(space.title)+'</strong><small>'+space.count+' saved items</small></span><span class="row-end">'+(space.id===state.space.id?'Here':'Open')+'</span>'+icon('arrow')+'</button>').join('')+'</div><div class="result-buttons">'+actionButton('new_space','New workspace '+icon('plus'),'button primary')+actionButton('example','Explore the example','button ghost')+'</div>','spaces');
}
function showWork() {
  openDialog('All work','<div id="work-results"></div>','work',true);renderWork();
}
function renderWork() {
  const work=state.work;
  if(!work){$('work-results').innerHTML='<p class="resource-description">Your tasks continue across conversations and workspaces.</p>'+actionButton('native_work','Open work overview '+icon('arrow'),'button primary');return;}
  $('work-results').innerHTML=actionButton('meetings',icon('layers')+'<span><strong>Meeting workspaces</strong><small>Transcripts, notes, actions, and drafts</small></span>'+icon('arrow'),'resource-row')+(work.status?'<p class="resource-description">'+esc(work.status)+'</p>':'')+'<div class="resource-list">'+work.entries.map(entry=>'<div class="resource-row">'+icon('agent')+'<span><strong>'+esc(entry.title)+'</strong><small>'+esc(entry.phase)+' · '+esc(entry.preview)+'</small></span>'+(entry.can_pin?'<button class="button" data-pin-task="'+esc(entry.task_id)+'">Pin result '+icon('plus')+'</button>':'<span class="row-end">'+esc(entry.phase)+'</span>')+'</div>').join('')+'</div><div class="result-buttons">'+actionButton('native_work','Full work overview '+icon('arrow'),'button ghost')+'</div>';
}
function showSources(item) {
  openDialog('Sources · '+item.title,(item.sources||[]).map(source=>'<section class="source-evidence"><h3>'+esc(source.title)+'</h3><small>'+esc(source.origin)+' · observed v'+source.revision+(source.scope?' · '+esc(source.scope):'')+'</small><pre>'+esc(source.text)+'</pre>'+itemButton(source.id,'focus','Show on canvas '+icon('arrow'),'button')+'</section>').join('')||'<p class="resource-description">No source observations are attached.</p>','sources',true);
}
function showItemMenu(item) {
  openDialog(item.title,'<div class="resource-list">'+[
    ['focus','expand','Focus this item'],...(item.kind==='application'?[]:[['copy','doc','Copy text'],['export','external','Export text']]),['park','park','Park for later'],
    ['remove','minus','Remove from this workspace'],['delete_confirm','close','Delete saved canvas item']
  ].map(([action,glyph,title])=>itemButton(item.id,action,icon(glyph)+'<span><strong>'+title+'</strong></span>'+icon('arrow'),'resource-row')).join('')+'</div>','item');
}
function runAction(action) {
  if(isBar){send({kind:action});return;}
  switch(action) {
    case 'canvas': closeDialog();break;
    case 'launcher': launcher();break;
    case 'web_app': openWebAppDialog();break;
    case 'spaces': showSpaces();break;
    case 'work': showWork();break;
    case 'meetings': showMeetings();break;
    case 'native_work': closeDialog();send({kind:'native_work'});break;
    case 'close': closeDialog();break;
    case 'fit': frame(visibleIds());break;
    case 'zoom_in': zoom(1.15);break;
    case 'zoom_out': zoom(1/1.15);break;
    case 'new_space':
      openDialog('Give this work a place','<form class="dialog-form" id="space-form"><label for="space-name">Workspace name</label><input id="space-name" name="title" maxlength="80" placeholder="What are you working on?" required><div class="buttons"><button class="button primary" type="submit">Create workspace '+icon('arrow')+'</button></div></form>','new_space');break;
    case 'add':
      openDialog('Add to this work','<div class="resource-list">'+[
        ['web_app','globe','Open a web app','Use Office, VS Code, and other web apps inside the canvas.'],
        ['launcher','search','Find an app, window or saved result','Keep the parts you need close by.'],
        ['add_file','doc','Add a document','Text, Markdown, tables, and original files.'],
        ['paste','chat','Paste a clipboard snapshot','Keep an excerpt as a source.'],
        ['new_note','doc','Start a note','An editable place for your own thinking.'],
        ['connect_edge','globe','Connect Edge','Add a readable excerpt from a specific tab.']
      ].map(([action,glyph,title,detail])=>actionButton(action,icon(glyph)+'<span><strong>'+title+'</strong><small>'+detail+'</small></span>'+icon('arrow'),'resource-row')).join('')+'</div>','add');break;
    case 'parked':
      openDialog('Parked for later','<div class="resource-list">'+[...positions].filter(([,p])=>p.parked).map(([id])=>itemButton(id,'restore',icon(items.get(id)?.icon||'doc')+'<span><strong>'+esc(items.get(id)?.title)+'</strong><small>Restore to its saved position</small></span>'+icon('plus'),'resource-row')).join('')+'</div>','parked');break;
    case 'settings':
      openDialog('Desktop','<div class="resource-list">'+[
        ['taskbar','canvas',state.replace_taskbar?'Restore the Windows taskbar':'Use Toolkit as the taskbar'],
        ['arrange','grid','Arrange selected apps beside the canvas'],['restore_apps','expand','Restore app positions'],
        ['keyboard','keyboard','Keyboard help'],['overlay','layers','Toggle the keyboard overlay']
      ].map(([action,glyph,title])=>actionButton(action,icon(glyph)+'<span><strong>'+title+'</strong></span>'+icon('arrow'),'resource-row')).join('')+'</div>','settings');break;
    default: closeDialog();send({kind:action});
  }
}
function publishPreviews() {
  requestApplicationLayout();
  // Geometry is an observation; native code resolves sources and validates bounds.
  if(!native || isBar || !state)return;
  const rect=$('viewport').getBoundingClientRect();
  const previews=[];
  if($('scrim').hidden && !document.hidden)for(const [id,record] of cards){
    const node=record.node.querySelector('[data-preview]');
    if(!node || record.node.hidden)continue;
    const box=node.getBoundingClientRect();
    const centre=document.elementFromPoint(box.x+box.width/2,box.y+box.height/2);
    if(box.left>=rect.left&&box.top>=rect.top&&box.right<=rect.right&&box.bottom<=rect.bottom&&node.contains(centre))previews.push({id,x:box.x,y:box.y,width:box.width,height:box.height});
  }
  send({kind:'previews',space:state.space.id,previews:previews.slice(0,12)});
}
document.addEventListener('click',event=>{
  const link=event.target.closest('a[href]');
  if(link){event.preventDefault();send({kind:'link',url:link.getAttribute('href')});return;}
  const button=event.target.closest('button');if(!button||button.disabled)return;
  if(button.dataset.webAppUrl){openWebApp(button.dataset.webAppUrl);return;}
  if(button.dataset.applicationAction){send({kind:'web_app',space:state.space.id,id:button.dataset.application,action:button.dataset.applicationAction});return;}
  const id=button.dataset.item, action=button.dataset.itemAction;
  if(action){
    if(action==='focus'){closeDialog();focusCard(id);}
    else if(action==='sources')showSources(items.get(id));
    else if(action==='menu')showItemMenu(items.get(id));
    else if(action==='delete_confirm')openDialog('Delete this saved item?','<p class="resource-description">Remove '+esc(items.get(id)?.title)+' from every workspace. Linked results retain their saved observations.</p><div class="result-buttons">'+itemButton(id,'delete','Delete saved item','button primary')+actionButton('close','Cancel')+'</div>','delete');
    else{if(modal)closeDialog();send({kind:'item',id,action});}
    return;
  }
  if(button.dataset.action){runAction(button.dataset.action);return;}
  if(button.dataset.space){closeDialog();send({kind:'space',id:button.dataset.space});return;}
  if(button.dataset.pinTask){send({kind:'pin_task',id:button.dataset.pinTask});closeDialog();return;}
  if(button.dataset.saveDraft){send({kind:'save_draft',id:button.dataset.saveDraft,revision:Number(button.dataset.revision)});return;}
  if(button.dataset.meetingOpen){const value=JSON.parse(button.dataset.meetingOpen);closeDialog();send({kind:'meeting_open',...value});return;}
  if(button.dataset.meetingEdit){showMeetingEditor(button.dataset.meetingEdit);return;}
  if(button.dataset.meetingCite){showMeetingCitation(JSON.parse(button.dataset.meetingCite));return;}
  if(button.dataset.launchIndex!=null){launchEntry(Number(button.dataset.launchIndex));return;}
  if(button.dataset.view){
    if(button.dataset.view==='canvas'){if(savedCamera){camera=savedCamera;savedCamera=null;mode='canvas';applyCamera();saveCamera();}else frame(visibleIds());}
    else if(button.dataset.view==='focus'){const ids=state.space.selection.length?state.space.selection:selectedCard?[selectedCard]:visibleIds().slice(-1);savedCamera={...camera};selectedCard=ids[0];frame(ids,'focus');}
    else{if(mode==='canvas')savedCamera={...camera};frame(visibleIds(),'overview');}
  }
});
document.addEventListener('submit',event=>{
  event.preventDefault();
  if(event.target===$('composer')){
    const text=$('request').value.trim();if(!text)return;
    send({kind:'submit',space:state.space.id,text});
  }else if(event.target.id==='space-form'){
    const title=$('space-name').value.trim();if(title){closeDialog();send({kind:'new_space',title});}
  }else if(event.target.id==='web-app-form'){
    const url=applicationUrl($('web-app-url'));if(url)openWebApp(url);
  }else if(event.target.matches('[data-application-address]')){
    const url=applicationUrl(event.target.querySelector('input'));
    if(url)send({kind:'web_app',space:state.space.id,id:event.target.dataset.applicationAddress,action:'navigate',url});
  }else if(event.target.id==='meeting-edit-form'){
    submitMeetingEdit(event.target);
  }else if(event.target.matches('[data-question-item]')){
    const input=event.target.querySelector('input');if(input.value.trim())send({kind:'answer',id:event.target.dataset.questionItem,question:event.target.dataset.question,text:input.value});
  }
});
$('request').addEventListener('input',()=>{if(!state)return;composerEdits.set(state.space.id,$('request').value);send({kind:'compose',space:state.space.id,text:$('request').value});});
document.addEventListener('input',event=>{
  if(event.target.id==='resource-search'){launcherIndex=0;renderSearch();}
  if(event.target.id==='web-app-url'||event.target.closest('[data-application-address]'))event.target.setCustomValidity('');
});
$('scrim').addEventListener('click',event=>{if(event.target===$('scrim'))closeDialog();});
$('viewport').addEventListener('pointerdown',event=>{
  if(event.button!==0||event.target.closest('button,input,textarea,a'))return;
  const resize=event.target.closest('[data-resize]'),header=event.target.closest('[data-drag]');
  if(!resize&&!header&&event.target.closest('.surface'))return;
  const id=resize?.dataset.resize||header?.dataset.drag;
  drag={id,resize:!!resize,pointer:event.pointerId,startX:event.clientX,startY:event.clientY,original:id?{...positions.get(id)}:{...camera},space:state.space.id};
  $('viewport').setPointerCapture(event.pointerId);$('viewport').classList.add('panning');event.preventDefault();
});
$('viewport').addEventListener('pointermove',event=>{
  if(!drag||drag.pointer!==event.pointerId)return;
  const dx=(event.clientX-drag.startX)/camera.zoom,dy=(event.clientY-drag.startY)/camera.zoom;
  if(drag.id){
    const app=items.get(drag.id)?.kind==='application';
    const value=drag.resize?{...drag.original,width:Math.max(app?560:300,Math.min(1600,drag.original.width+dx)),height:Math.max(app?320:220,Math.min(1600,drag.original.height+dy))}:
      {...drag.original,x:Math.max(-98000,Math.min(98000,drag.original.x+dx)),y:Math.max(-98000,Math.min(98000,drag.original.y+dy))};
    positions.set(drag.id,value);positionCard(drag.id,value);renderGroup();renderWires();
  }else{camera={...camera,x:drag.original.x-dx,y:drag.original.y-dy};applyCamera();}
});
function endDrag(event){
  if(!drag||event.pointerId!==drag.pointer)return;
  if(drag.id){const p=positions.get(drag.id);send({kind:'move',space:drag.space,id:drag.id,x:p.x,y:p.y,width:p.width,height:p.height});}
  else saveCamera();
  drag=null;$('viewport').classList.remove('panning');publishPreviews();
}
$('viewport').addEventListener('pointerup',endDrag);
$('viewport').addEventListener('pointercancel',endDrag);
$('viewport').addEventListener('wheel',event=>{
  if(event.target.closest('input,textarea'))return;
  const scrollable=event.target.closest('.card-body');
  if(!event.ctrlKey && scrollable && scrollable.scrollHeight>scrollable.clientHeight)return;
  event.preventDefault();
  const rect=$('viewport').getBoundingClientRect();
  if(event.ctrlKey||event.metaKey)zoom(Math.exp(-event.deltaY*.005),{x:event.clientX-rect.left,y:event.clientY-rect.top});
  else{camera={...camera,x:camera.x+event.deltaX/camera.zoom,y:camera.y+event.deltaY/camera.zoom};applyCamera();saveCamera();publishPreviews();}
},{passive:false});
document.addEventListener('keydown',event=>{
  const editing=event.target.matches('input,textarea,[contenteditable="true"]');
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();runAction('launcher');return;}
  if(event.key==='Escape'){
    event.preventDefault();
    if(modal)closeDialog();
    else if(mode!=='canvas'){if(savedCamera){camera=savedCamera;savedCamera=null;mode='canvas';applyCamera();saveCamera();}else frame(visibleIds());}
    else send({kind:'hide'});
    return;
  }
  if(modal==='launcher'&&event.target.id==='resource-search'&&['ArrowDown','ArrowUp','Enter'].includes(event.key)){
    event.preventDefault();
    if(event.key==='Enter')launchEntry(launcherIndex);
    else{launcherIndex=(launcherIndex+(event.key==='ArrowDown'?1:-1)+launcherEntries.length)%Math.max(1,launcherEntries.length);renderSearch();$('search-results').querySelector('.selected')?.scrollIntoView({block:'nearest'});}
    return;
  }
  if(event.key==='Tab'&&modal){
    const elements=[...$('dialog').querySelectorAll('button,input,textarea,select,a[href],[tabindex]')].filter(node=>!node.disabled&&node.tabIndex>=0&&node.getClientRects().length);
    const first=elements[0],last=elements.at(-1);
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===$('dialog'))){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||document.activeElement===$('dialog'))){event.preventDefault();first?.focus();}
  }
  if(editing||modal||event.altKey)return;
  if(event.key==='0'){event.preventDefault();frame(visibleIds());}
  if(event.key==='+'||event.key==='='){event.preventDefault();zoom(1.15);}
  if(event.key==='-'){event.preventDefault();zoom(1/1.15);}
});
new ResizeObserver(()=>{if(!isBar&&state){applyCamera();publishPreviews();}}).observe($('viewport'));
document.addEventListener('visibilitychange',publishPreviews);
window.canvasUI={render:renderSnapshot,frame,focus:focusCard,state:()=>state,camera:()=>camera,ready:false};
window.ToolkitCanvasBridge.connect({
    changed:text=>queueSnapshot(JSON.parse(text)),
    display:text=>{
      if(isBar)return;
      const event=JSON.parse(text);
      requestAnimationFrame(()=>{
        if(pendingState){renderSnapshot(pendingState);pendingState=null;cancelAnimationFrame(renderedFrame);renderedFrame=0;}
        if(event.panel==='composer'){$('composer').hidden=false;$('request').focus();}
        else if(event.panel==='submitted'){
          if(state?.space.id===event.space && $('request').value.trim()===event.text){$('request').value='';composerEdits.delete(event.space);}
        }
        else if(event.panel==='focus')focusCard(event.id);
        else if(event.panel==='fit')frame(visibleIds());
        else if(event.panel==='frame')frame(event.ids);
        else if(event.panel==='app_layout')requestApplicationLayout(true);
        else if(event.panel==='app_previews')applicationPreviews(event);
        else if(event.panel==='problem')meetingProblem(event.message);
        else if(event.panel==='meeting_queued')updateMeetingReceipt();
        else runAction(event.panel);
      });
    },
    connected:bridge=>{
      native=bridge;
      window.canvasUI.ready=true;
      native.ready(isBar?'bar':'canvas');
    }
});
