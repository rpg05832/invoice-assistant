'use strict';
// Focused reading stays beside the active field. A proposal never changes data
// until the reviewer explicitly accepts it; late replies cannot cross documents.
function regionHost(context){
 if(context.field==='check')return $('#inline-check-result');
 if(context.field==='document')return $('#local-document-reread-host');
 return $('#review-form').elements.namedItem(context.field)?.parentElement;
}
function regionActive(context){return state.inlineRegion===context&&state.current?.id===context.document&&state.company?.id===context.company&&Boolean($('#review-form'));}
function dismissInlineRegion(){state.inlineRegion=null;$('#region-reading-draft')?.remove();state.mode=null;updateReviewControls();}
function inlineDraft(context,result=null){
 $('#region-reading-draft')?.remove();const host=regionHost(context);if(!host)return;
 const box=document.createElement('section');box.id='region-reading-draft';box.className='region-reading-draft';box.dataset.reviewNoApprove='';box.setAttribute('aria-label','בדיקת פענוח האזור');
 if(!result){box.innerHTML='<p role="status">מפענח את האזור המסומן במחשב…</p><button type="button" data-action="region-dismiss">ביטול הפענוח</button>';host.append(box);return;}
 context.result=result;
 const choices=context.field==='check'?(result.proposals||[]).filter(p=>p.number&&p.available!==false).map(p=>({value:p.number,engine:p.engine})):result.values.map(value=>({value,engine:result.engine}));
 context.choices=choices.filter((p,i)=>choices.findIndex(q=>q.value===p.value)===i).slice(0,20);
 const value=context.field==='check'?result.number:context.choices.length===1?context.choices[0].value:'';
 box.innerHTML=`<strong>${context.field==='check'?'בדיקת מספר הצ׳ק':'בדיקת '+esc(fieldLabels[context.field])}</strong><img class="check-preview" alt="האזור שסומן לפענוח" src="/api/documents/${result.source_document_id}/region-image?${new URLSearchParams(result.region)}"><p>השוו למקור, בחרו הצעה או תקנו, ואז אשרו. דחייה משאירה את הנתונים הקיימים.</p><div class="region-reading-choices" tabindex="0" aria-label="בחירת הצעה במקש 1 עד 9">${context.choices.map((p,i)=>`<button type="button" data-region-choice="${i}">${i<9?i+1+' · ':''}${esc(p.value)} · ${esc(p.engine)}</button>`).join('')}</div><label for="inline-region-value">הערך שבדקתי מול המקור</label><input id="inline-region-value" value="${esc(value||'')}" dir="${context.field==='supplier_name'?'auto':'ltr'}" maxlength="${context.field==='check'?30:4000}" ${context.field==='check'?'inputmode="numeric"':''}><div class="inline"><button type="button" class="primary" data-action="region-accept">${context.field==='check'?'אישור מספר הצ׳ק':'אישור והעתקה לשדה'}</button><button type="button" data-action="region-dismiss">דחייה וחזרה לשדה</button><button type="button" data-action="region-redraw">סימון אזור מחדש</button>${context.field==='check'?'<button type="button" data-action="region-additional">השוואה במנוע הנוסף שבחרתי</button>':''}</div>`;
 host.append(box);box.scrollIntoView({block:'nearest'});$('#inline-region-value').focus({preventScroll:true});
}
function beginInlineRegion(field,index=null){
 if(!editableReview(state.current))return;
 dismissInlineRegion();const context={field,index,check:index===null?null:state.checks[index],document:state.current.id,company:state.company.id};state.inlineRegion=context;
 state.mode='check';fullPage();$('#region-hint').textContent=`סמנו מסגרת סביב ${field==='check'?'מספר הצ׳ק':fieldLabels[field]}. הפענוח יתחיל בסיום הגרירה ויופיע ליד השדה לאישור או דחייה.`;
 $('.document-preview').scrollTop=0;$('#canvas').classList.add('region-reading-selecting');
}
async function readInlineRegion(context,region){
 if(!regionActive(context)||context.busy)return;context.busy=true;state.mode=null;
 const source=state.previewDoc||context.document;inlineDraft(context);$('#region-actions').innerHTML='';
 try{
  const result=await api(`/documents/${context.document}/${context.field==='check'?'check-region':'field-region'}`,{method:'POST',body:{field:context.field,region,source_document_id:source,writer_id:context.check?.writer_id||$('#learning-writer')?.value||null}});
  if(!regionActive(context))return;inlineDraft(context,result);
 }catch(error){if(regionActive(context)){dismissInlineRegion();toast(error.message,true);}}
 finally{context.busy=false;if(regionActive(context))$('#canvas')?.classList.remove('region-reading-selecting');}
}
function acceptInlineRegion(){
 const context=state.inlineRegion;if(!context?.result||!regionActive(context))return;
 const value=$('#inline-region-value').value.trim();if(!value)throw Error('השלימו את הערך שבדקתם מול המקור');
 const result=context.result;
 if(context.field==='check'){
  if(!/^[0-9]{1,30}$/.test(value))throw Error('מספר צ׳ק חייב להכיל ספרות בלבד, כולל אפסים מובילים');
  const selected=context.choices.find(p=>p.value===value);
  const check={...context.check,...result,number:value,engine:selected?.engine||'manual',confirmed:true};
  if(context.check){const index=state.checks.indexOf(context.check);if(index<0)throw Error('הצ׳ק השתנה בזמן הפענוח; סמנו אותו שוב');state.checks[index]=check;}else state.checks.push(check);
  dismissInlineRegion();renderChecks();const input=$(`[data-check-number="${context.check?state.checks.indexOf(check):state.checks.length-1}"]`);input?.focus();
 }else{
  if(['net','vat','total'].includes(context.field)&&!/^[-+]?\d[\d,]*(?:\.\d{1,2})?$/.test(value))throw Error('בחרו סכום אחד או הקלידו סכום תקין');
  const input=$('#review-form').elements.namedItem(context.field);input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
  state.learningRegions??={};state.learningRegions[context.field]={region:result.region,source_document_id:result.source_document_id};state.current.field_sources??={};state.current.field_sources[context.field]={...result.region,source_document_id:result.source_document_id};dismissInlineRegion();input.focus();
 }
 paintReviewChanges();toast('הערך שאישרתם הועתק לשדה. אישור החשבונית נשאר פעולה נפרדת.');
}
function renderInlineCandidates(candidates){
 $('#inline-check-candidates')?.remove();if(!candidates.length){toast('לא נמצאו הצעות ברורות. אפשר לסמן אזור ליד שדה הצ׳ק.');return;}
 state.inlineCheckCandidates=candidates;$('#checks').insertAdjacentHTML('beforebegin',`<section id="inline-check-candidates" class="notice"><strong>${candidates.length} הצעות מהעמוד — בדקו את השיוך לחשבונית</strong><div class="inline">${candidates.map((p,i)=>`<button type="button" data-region-candidate="${i}">${esc(p.number||'אזור כתב יד')} · הצגת מקור ובדיקה</button>`).join('')}</div></section>`);$('#inline-check-candidates').scrollIntoView({block:'nearest'});
}
function openInlineCandidate(proposal){
 dismissInlineRegion();const context={field:'check',index:null,check:null,document:state.current.id,company:state.company.id};state.inlineRegion=context;
 const result={...proposal,source_document_id:proposal.source_document_id||state.current.id,proposals:proposal.proposals||[{number:proposal.number,engine:proposal.engine}]};
 showCheckSource(result.region,result.source_document_id);inlineDraft(context,result);
}
const checksBeforeRegion=renderChecks;
renderChecks=function(readonly=false){checksBeforeRegion(readonly);if(readonly)return;$$('#checks .check-row').forEach((row,index)=>{const input=$('[data-check-number]',row),wrapper=document.createElement('div');wrapper.className='check-number-with-scan';input.replaceWith(wrapper);wrapper.append(input);wrapper.insertAdjacentHTML('beforeend',`<button type="button" data-region-check="${index}" title="סימון אזור חדש ופענוח חוזר לצ׳ק הזה">סימון / פענוח חוזר</button>`);});};
const reviewBeforeRegion=review;
review=function(id){state.inlineRegion=null;state.mode=null;reviewBeforeRegion(id);const d=state.current;if(!d||!$('#review-form'))return;
 $('#checks').insertAdjacentHTML('beforebegin',`<div class="inline inline-check-actions"><button type="button" data-action="inline-new-check" ${editableReview(d)?'':'disabled'}>סימון ופענוח צ׳ק</button><button type="button" data-action="suggest-checks" ${editableReview(d)?'':'disabled'}>חיפוש חוזר של מספרי צ׳קים</button></div><div id="inline-check-result"></div>`);
 $('.review-advanced-actions').insertAdjacentHTML('afterbegin',`<button data-action="local-document-reread" ${editableReview(d)?'':'disabled'}>פענוח מקומי חוזר למסמך</button>`);$('.review-advanced-actions').insertAdjacentHTML('afterend','<div id="local-document-reread-host"></div>');
 const initial=$('#initial-check-suggestions');if(initial)$('#checks').before(initial);
 if(editableReview(d))for(const field of ['supplier_name','supplier_tax_id','number','net','vat','total']){
  const input=$('#review-form').elements.namedItem(field);input?.insertAdjacentHTML('afterend',`<button type="button" class="text-button field-region-button" data-region-field="${field}">סימון ופענוח ${esc(fieldLabels[field])}</button>`);
 }
 if(d.amount_review_reason){$('#field-vat').parentElement.insertAdjacentHTML('beforeend',`<div class="notice">${esc(d.amount_review_reason)}<label class="inline"><input type="checkbox" name="amount_reviewed" ${d.amount_reviewed?'checked':''} ${editableReview(d)?'':'disabled'}>בדקתי במקור את הסכומים עם ובלי מע״מ</label></div>`);}
 $('#canvas').addEventListener('pointerup',()=>{const context=state.inlineRegion;if(context&&state.checkRegion&&state.mode==='check')run(()=>readInlineRegion(context,structuredClone(state.checkRegion)));});
 state.reviewBaseline=reviewSignature();
};
const saveBeforeRegion=saveDocument;saveDocument=async function(approve=false){if(approve&&$('#region-reading-draft'))throw Error('אשרו או דחו תחילה את פענוח האזור שליד השדה');return saveBeforeRegion(approve);};
Object.assign(actions,{
 'inline-new-check':()=>beginInlineRegion('check'),
 'local-document-reread':async()=>{
  dismissInlineRegion();const context={field:'document',document:state.current.id,company:state.company.id};state.inlineRegion=context;inlineDraft(context);
  const r=await api(`/documents/${context.document}/local-reread`,{method:'POST'});if(!regionActive(context))return;context.result=r;
  const fields=['supplier_name','supplier_tax_id','recipient_tax_id','number','date','due_date','net','vat','total','currency','kind','allocation'].filter(k=>r.fields[k]&&!sameRecognizedValue(k,r.fields[k],$('#review-form').elements.namedItem(k)?.value));
  if(!fields.length){$('#region-reading-draft').innerHTML='<strong>לא נמצאו שינויים בפענוח החוזר</strong><p>כל הערכים שחולצו זהים לערכים המוצגים כעת בטופס. הנתונים והאישור לא השתנו.</p><button type="button" data-action="region-dismiss">חזרה למסמך</button>';return;}
  $('#region-reading-draft').innerHTML=`<strong>פענוח מקומי חוזר — בחירת השדות להעתקה</strong><p>התיקונים שטרם שמרתם מוצגים בעמודת הקיים. רק שדות שתבחרו יועתקו; אישור החשבונית נשאר נפרד.</p><div class="table-scroll"><table><thead><tr><th>להעתיק</th><th>שדה</th><th>קיים בטופס</th><th>פענוח חוזר</th></tr></thead><tbody>${fields.map(k=>{const current=$('#review-form').elements.namedItem(k)?.value||'';return `<tr><td><input type="checkbox" data-local-reread-field="${k}" ${current?'':'checked'}></td><th>${esc(fieldLabels[k])}</th><td dir="auto">${esc(current)}</td><td dir="auto">${esc(r.fields[k])}</td></tr>`;}).join('')}</tbody></table></div><button type="button" data-action="local-reread-select-all">בחירת כל ההצעות המוצגות</button><button type="button" class="primary" data-action="local-reread-accept">העתקת השדות שסומנו לבדיקה</button><button type="button" data-action="region-dismiss">דחייה וחזרה לטופס</button><details><summary>טקסט הפענוח החוזר</summary><pre dir="auto">${esc(r.text)}</pre></details>`;
  $('#region-reading-draft').scrollIntoView({block:'nearest'});
 },
 'local-reread-select-all':()=>$$('[data-local-reread-field]').forEach(el=>el.checked=true),
 'local-reread-accept':()=>{
  const context=state.inlineRegion;if(context?.field!=='document'||!context.result||!regionActive(context))return;
  const selected=$$('[data-local-reread-field]:checked').map(el=>el.dataset.localRereadField);state.learningRegions??={};state.current.field_sources??={};
  for(const field of selected){const input=$('#review-form').elements.namedItem(field);if(!input||input.disabled)continue;input.value=context.result.fields[field];input.dispatchEvent(new Event('input',{bubbles:true}));const source=context.result.field_sources[field];if(source){const {source_document_id,...region}=source;state.learningRegions[field]={region,source_document_id};state.current.field_sources[field]=source;}}
  dismissInlineRegion();paintReviewChanges();toast('ההצעות שסימנתם הועתקו לטופס לבדיקה. החשבונית עדיין לא אושרה.');
 },
 'region-dismiss':()=>dismissInlineRegion(),
 'region-additional':async()=>{const context=state.inlineRegion;if(!context?.result||context.busy)return;context.busy=true;try{const value=await api(`/documents/${context.document}/recognize`,{method:'POST',body:{confirmed:true,action:'check',region:context.result.region,source_document_id:context.result.source_document_id}});if(regionActive(context)){const result={...context.result,proposals:[...context.result.proposals,...value.checks.map(number=>({engine:value.engine,number}))]};inlineDraft(context,result);}}finally{context.busy=false;}},
 'region-accept':()=>acceptInlineRegion(),
 'region-redraw':()=>{const c=state.inlineRegion;if(c)beginInlineRegion(c.field,c.index);},
 'suggest-checks':async()=>{const id=state.current.id,result=await api(`/documents/${id}/check-suggestions`,{method:'POST'});if(state.current?.id===id&&$('#review-form'))renderInlineCandidates(result.candidates);}
});
document.addEventListener('click',e=>{
 const button=e.target.closest('button');if(!button)return;
 if(button.hasAttribute('data-auto-check')){e.preventDefault();e.stopImmediatePropagation();openInlineCandidate(state.current.suggested_checks[Number(button.dataset.autoCheck)]);return;}
 if(button.dataset.regionCheck!==undefined)beginInlineRegion('check',Number(button.dataset.regionCheck));
 if(button.dataset.regionField)beginInlineRegion(button.dataset.regionField);
 if(button.dataset.regionCandidate!==undefined)openInlineCandidate(state.inlineCheckCandidates[Number(button.dataset.regionCandidate)]);
 if(button.dataset.regionChoice!==undefined){const p=state.inlineRegion?.choices[Number(button.dataset.regionChoice)];if(p)$('#inline-region-value').value=p.value;}
},true);
document.addEventListener('keydown',e=>{
 if(e.target.id==='inline-region-value'&&e.key==='Enter'){e.preventDefault();e.stopImmediatePropagation();if(!e.repeat)run(()=>acceptInlineRegion());return;}
 const choices=e.target.closest('.region-reading-choices');if(!choices||!/^[1-9]$/.test(e.key))return;e.preventDefault();e.stopImmediatePropagation();const p=state.inlineRegion?.choices[Number(e.key)-1];if(p)$('#inline-region-value').value=p.value;
},true);
