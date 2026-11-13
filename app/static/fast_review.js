'use strict';

// A review queue belongs to one local company. Navigation saves edits, never approvals.
const editableReview = d => d && !['exported','received','archived'].includes(d.status);
const pendingReview = d => d && ['review','rejected'].includes(d.status);
const reviewLocked = () => Boolean(state.reviewBusy || state.reviewSaving);

function reviewSignature(){
 const form=$('#review-form');
 return form?JSON.stringify([Array.from(new FormData(form)),state.checks,state.items,state.learningRegions]):null;
}
function reviewDirty(){return editableReview(state.current) && $('#review-form') && reviewSignature()!==state.reviewBaseline;}
function reviewCandidates(){
 return state.docs.filter(d=>pendingReview(d) && (!state.selected.size||state.selected.has(d.id)) &&
  (!state.kindFilter||d.fields.kind===state.kindFilter) && (!state.supplierFilter||d.supplier_id===state.supplierFilter) &&
  (!state.search||[d.source_name,d.fields.number,d.fields.supplier_name,d.supplier?.name,d.fields.total].join(' ').toLowerCase().includes(state.search.toLowerCase())));
}
function reviewQueue(){return (state.reviewQueue?.ids||[]).map(id=>state.docs.find(d=>d.id===id)).filter(Boolean);}
function updateReviewControls(){
 const bar=$('#review-workbench');if(!bar)return;
 const docs=reviewQueue(),index=docs.findIndex(d=>d.id===state.current?.id),left=docs.filter(pendingReview).length;
 $('#review-progress').textContent=`מסמך ${index+1} מתוך ${docs.length} · ${left} ממתינים לבדיקה`;
 $$('[data-review-command]',bar).forEach(b=>b.disabled=reviewLocked());
 $('[data-action="review-previous"]',bar).disabled=reviewLocked()||index<=0;
 $('[data-action="review-next"]',bar).disabled=reviewLocked()||index>=docs.length-1;
 const approve=$('[data-action="review-approve-next"]',bar);if(approve)approve.disabled=reviewLocked();
}
function reviewTab(tab){
 const editor=$('.editor');if(!editor)return;
 editor.classList.toggle('review-ocr-mode',tab==='ocr');
 $$('[data-review-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.reviewTab===tab)));
 state.reviewTab=tab;
 if(tab==='ocr'){
  const source=state.docs.find(d=>d.id===state.previewDoc)||state.current;
  $('#review-ocr-panel p').textContent=`עמוד ${source.page} · ${source.engine}. זהו הטקסט שהמנוע קרא, לפני התיקונים שלך.`;
  $('#review-ocr-panel pre').textContent=source.ocr_text||'לא זוהה טקסט. אפשר להזין את הנתונים בלשונית הנתונים.';
  $('#source-image').src=`/api/documents/${source.id}/image`;
  $$('.selection',$('#canvas')).forEach(el=>el.remove());
 }
}
function paintReviewChanges(){
 const d=state.current,form=$('#review-form');if(!d||!form)return;
 $$('[data-original-value]',form).forEach(el=>el.remove());
 for(const [key,original] of Object.entries(d.original_fields||{})){
  const input=form.elements.namedItem(key);if(!input||!fieldLabels[key])continue;
  const changed=String(input.value)!==String(original??'');input.classList.toggle('review-changed',changed);
  if(changed){const note=document.createElement('small');note.dataset.originalValue=key;note.className='review-original';note.textContent=`פענוח מקורי: ${original||'ריק'} · תוקן לבדיקה`;input.after(note);}
 }
 const dirty=$('#review-save-status');if(dirty)dirty.textContent=reviewDirty()?'יש תיקונים · יישמרו במעבר':'כל התיקונים נשמרו';
}
function reviewIssueFocus(){
 if(!$('#review-form'))return;
 reviewTab('fields');
 let control=$('[data-check-confirmed]:not(:checked)');
 if(!control){
  const issues=(state.current?.issues||[]).join(' ');
  const fields=[[/מספר מסמך/,'number'],[/תאריך המסמך/,'date'],[/תאריך הפירעון/,'due_date'],[/כרטיס ספק|שאלת האישור/,'supplier_id'],[/מספר העוסק/,'supplier_tax_id'],[/לחברה אחרת/,'recipient_tax_id'],[/מטבע/,'currency'],[/סכומ|מסתכמים|סך הכול/,'total'],[/סוג המסמך/,'kind'],[/כרטיס הוצאה/,'expense_account'],[/כרטיס מע״מ/,'vat_account'],[/יעד המסמך/,'purpose']];
  const match=fields.find(([pattern])=>pattern.test(issues));control=$('#review-form').elements.namedItem(match?.[1]||'number');
 }
 control?.focus();control?.scrollIntoView({block:'center'});
 if(control?.matches('input[type=text]'))control.select();
}
async function reviewOperation(task){
 if(reviewLocked()||!$('#review-form'))return;
 state.reviewBusy=true;updateReviewControls();
 try{await task();}
 catch(error){
  const issues=$('#review-issues');if(issues){issues.innerHTML='<div class="notice" role="alert"></div>';issues.firstChild.textContent=error.message;reviewIssueFocus();}
  toast(error.message,true);
 }finally{state.reviewBusy=false;updateReviewControls();paintReviewChanges();}
}
async function moveReview(step,onlyPending=false){
 await reviewOperation(async()=>{
  const id=state.current.id;
  if(reviewDirty())await saveDocument(false);
  const docs=reviewQueue(),index=docs.findIndex(d=>d.id===id);
  const next=onlyPending?[...docs.slice(index+1),...docs.slice(0,index)].find(pendingReview):docs[index+step];
  if(next)review(next.id);else toast('אין מסמך נוסף בתור הזה');
 });
}
async function approveReviewNext(){
 await reviewOperation(async()=>{
  const id=state.current.id;if(!editableReview(state.current))return;
  if(state.current.status!=='approved'||reviewDirty())await saveDocument(true);
  // Supplier questions and validation errors must leave this invoice awaiting review.
  if(state.docs.find(d=>d.id===id)?.status!=='approved')return;
  const docs=reviewQueue(),index=docs.findIndex(d=>d.id===id);
  const next=[...docs.slice(index+1),...docs.slice(0,index)].find(pendingReview);
  if(next){review(next.id);toast('אושר · החשבונית הבאה מוכנה לבדיקה');}
  else{closeModal();toast('הבדיקה בתור הושלמה · המסמכים שאישרת מוכנים ליצוא');}
 });
}

const reviewBeforeWorkbench=review;
review=function(id){
 if(!state.reviewQueue||state.reviewQueue.company!==state.company?.id||!state.reviewQueue.ids.includes(id)){
  const visible=$$('[data-open]').filter(b=>!b.closest('tr')?.hidden).map(b=>b.dataset.open);
  const ids=visible.includes(id)?visible:state.docs.filter(d=>d.status!=='archived'||d.id===id).map(d=>d.id);
  state.reviewQueue={company:state.company?.id,ids};
 }
 reviewBeforeWorkbench(id);const d=state.current;if(!d||!$('#review-form'))return;
 const editable=editableReview(d);
 $('.dialog-header').insertAdjacentHTML('afterend',`<section id="review-workbench" tabindex="-1" aria-label="בדיקה מהירה"><div class="review-queue-heading"><strong id="review-progress" aria-live="polite"></strong><span id="review-save-status"></span></div><div class="review-queue-actions"><button data-review-command data-action="review-previous" title="Alt + חץ ימינה">→ הקודם</button><button data-review-command data-action="review-next" title="Alt + חץ שמאלה">הבא ללא אישור ←</button><button data-review-command data-action="review-next-pending">הבא שממתין לבדיקה</button>${editable?'<button data-review-command data-action="review-focus-issue">מעבר לשדה לטיפול · F8</button><button class="primary" data-review-command data-action="review-approve-next">אישור והבא · Enter ✓</button>':''}<button data-review-command data-action="review-list">חזרה לרשימה · Esc</button></div><small>Tab לשדה הבא · Alt + חצים למעבר · Enter לאישור ומעבר · בתוך הערה: Ctrl + Enter</small></section>`);
 $('.editor').insertAdjacentHTML('afterbegin',`<div class="review-tabs" role="tablist" aria-label="תצוגת הפענוח"><button role="tab" data-review-tab="fields" aria-controls="review-form" aria-selected="true">נתונים לתיקון</button><button role="tab" data-review-tab="ocr" aria-controls="review-ocr-panel" aria-selected="false">טקסט הפענוח</button></div><section id="review-ocr-panel" role="tabpanel" aria-label="טקסט הפענוח מול המקור"><p>עמוד ${d.page} · ${esc(d.engine)}. זהו הטקסט שהמנוע קרא, לפני התיקונים שלך.</p><pre dir="auto">${esc(d.ocr_text||'לא זוהה טקסט. אפשר להזין את הנתונים בלשונית הנתונים.')}</pre><h3>שדות שחולצו לפני תיקון</h3><table><tbody>${Object.entries(d.original_fields||{}).filter(([k,v])=>fieldLabels[k]&&v).map(([k,v])=>`<tr><th>${esc(fieldLabels[k])}</th><td dir="auto">${esc(v)}</td></tr>`).join('')}</tbody></table></section>`);
 $('.preview-tools').insertAdjacentHTML('afterbegin','<span class="review-zoom-tools"><button data-action="review-zoom-in" aria-label="הגדלת המקור">＋</button><button data-action="review-zoom-fit">התאם למסך</button><button data-action="review-zoom-out" aria-label="הקטנת המקור">−</button></span>');
 state.reviewZooms??={};applyReviewZoom(state.reviewZooms[id]||1);
 state.reviewBaseline=reviewSignature();reviewTab(state.reviewTab||'fields');paintReviewChanges();updateReviewControls();
 $('#review-workbench').focus({preventScroll:true});
};
function applyReviewZoom(zoom){
 if(!state.current||!$('#canvas'))return;
 zoom=Math.max(1,Math.min(3,zoom));state.reviewZooms[state.current.id]=zoom;
 $('#canvas').classList.add('review-zoom');$('#canvas').style.width=`${zoom*100}%`;
}
const saveBeforeWorkbench=saveDocument;
saveDocument=async function(approve=false){
 if(state.reviewSaving)return false;
 state.reviewSaving=true;updateReviewControls();
 try{return await saveBeforeWorkbench(approve);}
 finally{state.reviewSaving=false;updateReviewControls();}
};
const docsBeforeWorkbench=renderDocuments;
renderDocuments=function(){docsBeforeWorkbench();$('.toolbar')?.insertAdjacentHTML('beforeend',`<button class="primary" data-action="start-fast-review" ${reviewCandidates().length?'':'disabled'}>בדיקה מהירה · ${reviewCandidates().length} ממתינים</button>`);};
Object.assign(actions,{
 'start-fast-review':()=>{const docs=reviewCandidates();if(!docs.length)return;state.reviewQueue={company:state.company.id,ids:docs.map(d=>d.id)};state.reviewTab='fields';review(docs[0].id);},
 'review-previous':()=>moveReview(-1),
 'review-next':()=>moveReview(1),
 'review-next-pending':()=>moveReview(1,true),
 'review-approve-next':()=>approveReviewNext(),
 'review-focus-issue':()=>reviewIssueFocus(),
 'review-list':()=>reviewOperation(async()=>{if(reviewDirty())await saveDocument(false);closeModal();}),
 'review-zoom-in':()=>applyReviewZoom((state.reviewZooms[state.current.id]||1)+.25),
 'review-zoom-out':()=>applyReviewZoom((state.reviewZooms[state.current.id]||1)-.25),
 'review-zoom-fit':()=>applyReviewZoom(1)
});
document.addEventListener('input',e=>{if(e.target.closest('#review-form'))paintReviewChanges();});
document.addEventListener('change',e=>{if(e.target.closest('#review-form'))paintReviewChanges();});
document.addEventListener('change',e=>{if(e.target.id==='preview-page'&&state.reviewTab==='ocr')reviewTab('ocr');});
document.addEventListener('click',e=>{
 const button=e.target.closest('button');if(!button)return;
 if(reviewLocked()&&button.closest('#modal')){e.preventDefault();e.stopImmediatePropagation();return;}
 if(button.dataset.reviewTab){e.preventDefault();reviewTab(button.dataset.reviewTab);return;}
 if(button.dataset.action==='close'&&reviewDirty()){e.preventDefault();e.stopImmediatePropagation();run(()=>actions['review-list']());}
},true);
document.addEventListener('keydown',e=>{
 if(!$('#modal').open||!$('#review-form'))return;
 if(e.isComposing)return;
 let action=null;
 if(e.altKey&&e.key==='ArrowLeft')action=()=>moveReview(1);
 else if(e.altKey&&e.key==='ArrowRight')action=()=>moveReview(-1);
 else if(e.key==='F8')action=()=>reviewIssueFocus();
 else if(e.key==='Enter'&&!e.altKey&&!e.metaKey&&editableReview(state.current)){
  const target=e.target;
  if(target.closest('button,a,select,[contenteditable=true]')||target.matches('input[type=checkbox],input[type=radio],input[type=file]'))return;
  if(target.matches('textarea')&&!e.ctrlKey)return;
  action=()=>approveReviewNext();
 }
 if(!action)return;
 e.preventDefault();e.stopImmediatePropagation();if(!e.repeat&&!reviewLocked())run(action);
},true);
$('#modal').addEventListener('cancel',e=>{
 if(!$('#review-form'))return;
 if(reviewLocked()){e.preventDefault();return;}
 e.preventDefault();run(()=>actions['review-list']());
});
