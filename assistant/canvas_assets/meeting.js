'use strict';

// Meeting cards use the existing verified package and revision-checked workflow.
// This view owns local editing only; an enqueue acknowledgement is not a save.
let meetingEditor = null;
function meetingPresentation(item) {
  if(item.meeting?.kind!=='source')return item;
  const started=new Date(item.meeting.source.started_at*1000);
  if(Number.isNaN(started.getTime()))return item;
  const time=started.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const day=started.toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'});
  return {...item,title:item.title+' · '+time,origin:'Transcript · '+day};
}
function meetingCitations(citations = []) {
  return citations.map((citation,index) => '<button class="cite" data-meeting-cite="' + esc(JSON.stringify(citation)) + '" title="Read the original passage">' +
    icon('link') + 'Source ' + (index+1) + ' · line ' + citation.line + '</button>').join('');
}
function meetingFooter(item) {
  const card=item.meeting;
  return '<footer class="surface-footer"><span>' + (card.revision == null ? card.kind==='task'?'Saved meeting work':'Saved meeting source' : 'Saved revision ' + card.revision) + '</span><div class="footer-actions">' +
    (['draft','action'].includes(card.kind) ? '<button data-meeting-edit="' + esc(item.id) + '">Review & edit ' + icon('arrow') + '</button>' : card.kind==='task'?actionButton('native_work','Open work '+icon('arrow'),''):itemButton(item.id,'focus','Read in focus '+icon('expand'))) + '</div></footer>';
}
function meetingBody(item) {
  const card=item.meeting;
  const warning=state.meeting?.readiness.kind==='unconfirmed'?'<p class="meeting-warning">'+esc(state.meeting.readiness.reason)+'</p>':'';
  if(card.kind==='notes') {
    const statements = (values, className='') => values.map(value => '<div class="meeting-statement '+className+'"><p>'+esc(value.text)+'</p><div class="meeting-citations">'+meetingCitations(value.citations)+'</div></div>').join('');
    return warning+'<div class="result-kicker"><span>MEETING NOTES</span><span class="tag">Linked to transcripts</span></div><div class="result-copy"><h2>What came out of it.</h2>'+
      (card.decisions.length?'<h3>Decisions</h3>'+statements(card.decisions,'meeting-decision'):'')+'<h3>Notes</h3>'+statements(card.notes)+'</div>';
  }
  if(card.kind==='source') {
    const minutes=Math.round(card.source.duration_seconds/60);
    return '<p class="meeting-eyebrow">TRANSCRIPT · '+minutes+' MIN'+(card.state.kind==='changed'?' · ORIGINAL SNAPSHOT':'')+'</p><pre class="transcript-text">'+esc(card.source.text)+'</pre>';
  }
  if(card.kind==='task') {
    const observed=card.work.kind==='observed'?card.work.task:card.work.previous;
    const cards=state.meeting?.cards||[];
    const counts=[['transcript',cards.filter(value=>value.kind==='source').length],['note',cards.filter(value=>value.kind==='notes').reduce((sum,value)=>sum+value.notes.length,0)],['action',cards.filter(value=>value.kind==='action').length],['unsent draft',cards.filter(value=>value.kind==='draft').length]];
    const detail=counts.filter(([,count])=>count).map(([label,count])=>count+' '+label+(count===1?'':'s')).join(' · ');
    return warning+'<div class="meeting-task-label"><span class="dot"></span>'+esc(observed?.label || 'Awaiting observation')+'</div><h2 class="meeting-title">'+esc(state.meeting?.title || item.title)+'</h2><p class="meeting-detail">'+esc(detail)+'</p>';
  }
  if(card.kind==='draft') {
    return warning+'<p class="meeting-eyebrow">UNSENT DRAFT · v'+card.revision+'</p><h2 class="meeting-title">'+esc(card.content.subject)+'</h2><p class="meeting-addressees">'+esc(card.content.recipients.map(p=>p.email? p.name+' <'+p.email+'>':p.name).join(', ')||'Recipients not specified')+'</p><div class="meeting-draft-copy">'+esc(card.content.body)+'</div><div class="meeting-citations">'+meetingCitations(card.citations)+'</div>';
  }
  return warning+'<p class="meeting-eyebrow">FOLLOW-UP · '+esc(card.content.status.toUpperCase())+'</p><h2 class="meeting-title">'+esc(card.content.text)+'</h2><div class="meeting-facts"><span>Owner</span><strong>'+esc(card.content.owner||'Not stated')+'</strong><span>Due</span><strong>'+esc(card.content.due||'Not stated')+'</strong></div><div class="meeting-citations">'+meetingCitations(card.citations)+'</div>';
}
function showMeetings() {
  const meetings=state.meetings;
  const packages=meetings?.packages||[];
  openDialog('Meetings, connected','<p class="resource-description">Open saved transcripts, decisions, actions, and drafts together.</p><div class="resource-list">'+packages.map(value=>
    '<button class="resource-row" data-meeting-open="'+esc(JSON.stringify({task_id:value.task_id,version:value.version}))+'">'+icon('layers')+'<span><strong>'+esc(value.title)+'</strong><small>'+value.counts.sources+' transcripts · '+value.counts.notes+' notes · '+value.counts.drafts+' drafts'+(value.readiness.kind==='unconfirmed'?' · Unconfirmed':'')+'</small></span>'+icon('arrow')+'</button>').join('')+'</div>'+
    (!packages.length?'<p class="resource-description">No saved meeting packages are available yet.</p>':'')+
    (meetings?.problems||[]).map(text=>'<p class="meeting-warning">'+esc(text)+'</p>').join(''),'meetings',true);
}
function showMeetingCitation(citation) {
  const source=state.meeting?.cards.find(card=>card.id===citation.card_id);
  openDialog(source?.title || 'Original passage','<p class="resource-description">Saved transcript · line '+citation.line+'</p><blockquote class="citation-quote">'+esc(citation.quote)+'</blockquote>'+itemButton(citation.card_id,'focus','Read the transcript '+icon('arrow'),'button primary'),'citation',true);
}
function showMeetingEditor(id) {
  const card=items.get(id)?.meeting;
  if(!card || !['draft','action'].includes(card.kind))return;
  if(state.meeting.readiness.kind!=='verified'){toast(state.meeting.readiness.reason);return;}
  meetingEditor={id,reference:card.reference,revision:card.revision,content:structuredClone(card.content),attempt:null};
  let fields;
  if(card.kind==='draft') {
    fields='<label for="meeting-subject">Subject</label><input id="meeting-subject" name="subject" maxlength="500" required value="'+esc(card.content.subject)+'">'+
      '<label for="meeting-body">Draft</label><textarea id="meeting-body" name="body" rows="13" required>'+esc(card.content.body)+'</textarea>'+
      '<p class="resource-description">To: '+esc(card.content.recipients.map(p=>p.name+(p.email?' <'+p.email+'>':'')).join(', ')||'Not specified')+'</p>';
  }else{
    fields='<label for="meeting-action">Action</label><textarea id="meeting-action" name="text" rows="4" maxlength="4000" required>'+esc(card.content.text)+'</textarea>'+
      '<div class="meeting-edit-grid"><div><label for="meeting-owner">Owner</label><input id="meeting-owner" name="owner" maxlength="240" value="'+esc(card.content.owner||'')+'"></div><div><label for="meeting-due">Due</label><input id="meeting-due" name="due" maxlength="240" value="'+esc(card.content.due||'')+'"></div></div>'+
      '<label for="meeting-status">Status</label><select id="meeting-status" name="status"><option value="open"'+(card.content.status==='open'?' selected':'')+'>Open</option><option value="done"'+(card.content.status==='done'?' selected':'')+'>Done</option></select>';
  }
  openDialog(card.kind==='draft'?'Review the draft':'Review the action','<form class="dialog-form meeting-form" id="meeting-edit-form">'+fields+
    '<p class="meeting-save-status" id="meeting-save-status" role="status">Editing saved revision '+card.revision+'</p><div class="buttons"><button class="button ghost" type="button" data-action="close">Close</button><button class="button primary" type="submit">Save edit '+icon('check')+'</button></div></form>','meeting-edit',true);
}
function submitMeetingEdit(form) {
  if(!meetingEditor || meetingEditor.attempt)return;
  const content=structuredClone(meetingEditor.content);
  for(const name of Object.keys(content)){
    const input=form.elements.namedItem(name);
    if(input)content[name]=['owner','due'].includes(name)?input.value.trim()||null:input.value;
  }
  const reference=meetingEditor.reference;
  const change={kind:reference.kind,task_id:reference.task_id,version:reference.version,expected_revision:meetingEditor.revision,attempt_id:crypto.randomUUID(),content};
  change[reference.kind+'_id']=reference[reference.kind+'_id'];
  meetingEditor.attempt=change.attempt_id;
  meetingEditor.content=content;
  form.querySelectorAll('input,textarea,select,button[type=submit]').forEach(node=>node.disabled=true);
  $('meeting-save-status').textContent='Saving this revision…';
  send({kind:'meeting_edit',change});
}
function updateMeetingReceipt() {
  if(modal!=='meeting-edit' || !meetingEditor?.attempt)return;
  const receipt=state.meeting?.receipts.find(value=>value.change.attempt_id===meetingEditor.attempt);
  if(!receipt)return;
  const saved=receipt.kind.endsWith('_saved');
  if(saved)meetingEditor.revision=receipt.saved.revision;
  $('meeting-save-status').textContent=saved?'Saved revision '+receipt.saved.revision+(meetingEditor.reference.kind==='draft'?' · unsent':''):receipt.reason;
  $('meeting-edit-form').querySelectorAll('input,textarea,select,button[type=submit]').forEach(node=>node.disabled=false);
  meetingEditor.attempt=null;
}
function meetingProblem(message) {
  if(modal==='meeting-edit' && $('meeting-save-status')) {
    $('meeting-save-status').textContent=message;
    $('meeting-edit-form').querySelectorAll('input,textarea,select,button[type=submit]').forEach(node=>node.disabled=false);
    if(meetingEditor)meetingEditor.attempt=null;
  }else toast(message);
}
