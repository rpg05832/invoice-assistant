'use strict';
const workspaceRequests=new Map();let sessionTimer,sessionTick=0;
const companyBeforeReviewTools=chooseCompany;
chooseCompany=function(id){if(state.company?.id!==id){state.reviewTools=null;state.reviewToolQueue='all';}return companyBeforeReviewTools(id);};
const toolFieldNames=['supplier_id','number','date','due_date','supplier_tax_id','recipient_tax_id','allocation','net','vat','total','currency','expense_account','delivery_refs','notes'];
const toolFieldLabel=k=>k==='supplier_id'?'כרטיס ספק':fieldLabels[k]||k;
const reviewKeyLabel=k=>({'Alt+ArrowLeft':'Alt + חץ שמאלה','Alt+ArrowRight':'Alt + חץ ימינה','Ctrl+Enter':'Ctrl + Enter','PageDown':'Page Down','PageUp':'Page Up'})[k]||k;
async function loadWorkspace(force=false){
 if(!state.company)return null;const id=state.company.id;
 if(!force&&state.reviewTools?.company_id===id)return state.reviewTools;
 state.workspaceVersions??={};let request=workspaceRequests.get(id);
 if(!request||force){const version=(state.workspaceVersions[id]||0)+1;state.workspaceVersions[id]=version;request={version,promise:api(`/companies/${id}/review-workspace`)};workspaceRequests.set(id,request);const current=request;request.promise.finally(()=>{if(workspaceRequests.get(id)===current)workspaceRequests.delete(id);}).catch(()=>{});}
 const value=await request.promise;if(state.company?.id===id&&state.workspaceVersions[id]===request.version)state.reviewTools=value;return value;
}
function queueMatches(d,queue){
 const issues=(d.issues||[]).join(' ');
 if(queue==='amounts')return /סכומ|מע״מ|מסתכמים|סך הכול|זיכוי/.test(issues);
 if(queue==='suppliers')return /ספק|עוסק|לחברה אחרת/.test(issues);
 if(queue==='checks')return /צ׳ק/.test(issues)||(d.suggested_checks?.length&&!d.check_candidates_reviewed);
 if(queue==='revisit')return state.reviewTools?.flags.some(f=>f.document_id===d.id);
 return true;
}
const candidatesBeforeTools=reviewCandidates;
reviewCandidates=function(){const docs=state.reviewToolQueue==='revisit'?state.docs.filter(d=>editableReview(d)&&(!state.selected.size||state.selected.has(d.id))&&(!state.kindFilter||d.fields.kind===state.kindFilter)&&(!state.supplierFilter||d.supplier_id===state.supplierFilter)&&(!state.search||[d.source_name,d.fields.number,d.fields.supplier_name,d.supplier?.name].join(' ').toLowerCase().includes(state.search.toLowerCase()))):candidatesBeforeTools();return docs.filter(d=>queueMatches(d,state.reviewToolQueue||'all'));};
state.reviewNextCandidate=d=>state.reviewToolQueue==='revisit'?editableReview(d)&&queueMatches(d,'revisit'):pendingReview(d);
state.reviewAfterApproval=async id=>{if(state.reviewTools?.flags.some(f=>f.document_id===id)){await api(`/documents/${id}/review-flag`,{method:'PUT',body:{marked:false}});state.reviewTools.flags=state.reviewTools.flags.filter(f=>f.document_id!==id);decorateWorkspaceList();}};
const saveBeforeReviewTools=saveDocument;
saveDocument=async function(approve=false){const id=state.current?.id;const result=await saveBeforeReviewTools(approve);if(approve&&state.docs.find(d=>d.id===id)?.status==='approved'){await loadWorkspace(true);decorateWorkspaceList();}return result;};
function decorateWorkspaceList(){
 if(state.view!=='documents'||!$('.toolbar'))return;
 $('#workspace-list-tools')?.remove();
 const selected=state.selected.size,session=state.reviewTools?.session;
 $('.toolbar').insertAdjacentHTML('beforeend',`<div id="workspace-list-tools" class="inline"><select id="review-queue-kind" aria-label="תור בדיקה">${Object.entries({all:'כל הממתינים',amounts:'סכומים לטיפול',suppliers:'ספקים לטיפול',checks:'צ׳קים לטיפול',revisit:'סומנו לחזרה'}).map(([k,v])=>`<option value="${k}" ${state.reviewToolQueue===k?'selected':''}>${v}</option>`).join('')}</select><button data-action="review-resume" ${session?'':'disabled'}>המשך מהמקום שהפסקתי</button><button data-action="review-bulk" ${selected?'':'disabled'}>תיקון מרוכז · ${selected}</button><button data-action="review-preferences">הסביבה האישית שלי</button><button data-action="review-undo-last" ${state.reviewTools?.last_approval?'':'disabled'}>החזרת אישור אחרון לבדיקה</button></div>`);
 const start=$('[data-action="start-fast-review"]');if(start){const n=reviewCandidates().length;start.textContent=`בדיקה מהירה · ${n} ממתינים`;start.disabled=!n;}
 $$('.review-flag-badge').forEach(el=>el.remove());for(const flag of state.reviewTools?.flags||[]){const cell=$(`[data-open="${flag.document_id}"]`)?.closest('tr')?.querySelector('td:nth-child(2)');if(cell){const label=document.createElement('span');label.className='review-flag-badge';label.textContent='↩ לחזור · '+flag.note;cell.append(label);}}
}
const documentsBeforeTools=renderDocuments;
renderDocuments=function(){documentsBeforeTools();decorateWorkspaceList();loadWorkspace().then(value=>{if(value?.company_id===state.company?.id)decorateWorkspaceList();}).catch(e=>toast(e.message,true));};
function sessionSnapshot(){
 if(!state.current||!$('#review-form'))return null;
 return {document_id:state.current.id,stamp:Date.now()*1000+(sessionTick++%1000),field:toolFieldNames.includes(state.activeReviewField)?state.activeReviewField:'',editor_scroll:$('.editor').scrollTop,source_scroll:$('.document-preview').scrollTop,zoom:state.reviewZooms?.[state.current.id]||1,tab:state.reviewTab||'fields'};
}
state.reviewSessionSaver=async()=>{clearTimeout(sessionTimer);const value=sessionSnapshot();if(!value)return;const id=state.company.id;await api(`/companies/${id}/review-session`,{method:'PUT',body:value});if(state.reviewTools?.company_id===id)state.reviewTools.session=value;};
function scheduleSession(){clearTimeout(sessionTimer);const value=sessionSnapshot(),companyId=state.company?.id;if(!value)return;sessionTimer=setTimeout(()=>api(`/companies/${companyId}/review-session`,{method:'PUT',body:value}).then(()=>{if(state.reviewTools?.company_id===companyId)state.reviewTools.session=value;}).catch(()=>{}),350);}
function applyWorkspacePreferences(){
 const form=$('#review-form'),prefs=state.reviewTools?.preferences;if(!form||!prefs)return;
 if(window.innerWidth>760)$('.review-body').style.gridTemplateColumns=`${100-prefs.source_width}% ${prefs.source_width}%`;
 form.style.setProperty('--review-input-size',prefs.font_size+'px');
 if(prefs.field_order.length&&!$('#personal-review-fields')){
  const grid=document.createElement('div');grid.className='form-grid';grid.id='personal-review-fields';form.querySelector('fieldset').prepend(grid);
  for(const key of prefs.field_order){const input=form.elements.namedItem(key);if(input){const container=input.closest('div');if(container&&container!==grid)grid.append(container);}}
 }
 const keys=prefs.keys;const approve=$('[data-action="review-approve-next"]');if(approve)approve.textContent=`אישור והבא · ${keys.approve} ✓`;
 $('#review-workbench>small').textContent=`Tab לשדה הבא · ${reviewKeyLabel(keys.next)} להבא · ${reviewKeyLabel(keys.previous)} לקודם · ${reviewKeyLabel(keys.approve)} לאישור ומעבר · Esc לשמירה וחזרה`;
}
function renderSupplierSearch(){
 const select=$('#supplier-link');if(!select||$('#review-supplier-search'))return;
 select.insertAdjacentHTML('beforebegin','<div class="supplier-quick-search"><label for="review-supplier-search">חיפוש ספק לפי שם, קוד או מספר עוסק</label><input id="review-supplier-search" role="combobox" aria-expanded="false" aria-controls="review-supplier-results" autocomplete="off" data-review-no-approve placeholder="מתחילים להקליד ובוחרים ב־Enter"><div id="review-supplier-results" role="listbox"></div></div>');
 $('#review-supplier-search').disabled=select.disabled||select.closest('fieldset')?.disabled;
}
function searchSuppliers(){
 const input=$('#review-supplier-search'),q=input.value.trim().toLowerCase();state.quickSuppliers=q?state.suppliers.filter(s=>s.active&&[s.code,s.name,s.tax_id].join(' ').toLowerCase().includes(q)).slice(0,20):[];state.quickSupplierIndex=0;
 $('#review-supplier-results').innerHTML=state.quickSuppliers.map((s,i)=>`<button type="button" role="option" id="quick-supplier-${i}" data-quick-supplier="${i}" aria-selected="${i===0}">${esc(s.code)} · ${esc(s.name)} · ${esc(s.tax_id)}</button>`).join('');input.setAttribute('aria-expanded',String(Boolean(state.quickSuppliers.length)));if(state.quickSuppliers.length)input.setAttribute('aria-activedescendant','quick-supplier-0');else input.removeAttribute('aria-activedescendant');
}
function chooseQuickSupplier(index){
 const supplier=state.quickSuppliers?.[index];if(!supplier)return;
 $('#supplier-link').value=supplier.id;$('#supplier-link').dispatchEvent(new Event('change',{bubbles:true}));$('#review-supplier-search').value=`${supplier.code} · ${supplier.name}`;$('#review-supplier-results').innerHTML='';$('#review-supplier-search').setAttribute('aria-expanded','false');$('#field-number').focus();
}
function drawFieldLoupe(region,label){
 const box=$('#review-field-loupe');if(!box)return;box.querySelector('strong').textContent=label;const canvas=$('canvas',box),image=$('#source-image');
 if(!region){canvas.hidden=true;box.querySelector('small').textContent='אפשר לבחור מילה במקור או ללמד את אזור השדה.';return;}
 canvas.hidden=false;box.querySelector('small').textContent='הגדלת אזור המקור של השדה הפעיל';
 const draw=()=>{if(!canvas.isConnected||!image.naturalWidth)return;const w=region.w*image.naturalWidth,h=region.h*image.naturalHeight;canvas.width=420;canvas.height=Math.max(65,Math.min(160,420*h/Math.max(w,1)));const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,canvas.width,canvas.height);const scale=Math.min(420/w,canvas.height/h);ctx.drawImage(image,region.x*image.naturalWidth,region.y*image.naturalHeight,w,h,(420-w*scale)/2,(canvas.height-h*scale)/2,w*scale,h*scale);};
 if(image.complete)draw();else image.addEventListener('load',draw,{once:true});
}
function activeSourceInput(){return state.activeReviewSelector?$(state.activeReviewSelector):null;}
function copySourceValue(text){
 const input=activeSourceInput();if(!input||input.disabled||input.closest('fieldset')?.disabled)throw Error('בחרו תחילה את השדה שתרצו לתקן');
 const key=input.name;
 if(['net','vat','total'].includes(key)){text=text.replace(/[,₪\s]/g,'');if(!/^-?\d+(\.\d{1,2})?$/.test(text))throw Error('בחרו סכום אחד או הקלידו את התיקון');}
 if(input.type==='date'){const m=text.match(/^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$/);if(m)text=`${m[3]}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`;if(!/^\d{4}-\d{2}-\d{2}$/.test(text))throw Error('התאריך שנבחר דורש תיקון ידני');}
 if(input.tagName==='SELECT'&&!Array.from(input.options).some(o=>o.value===text))throw Error('בחרו ערך מתוך רשימת השדה');
 input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.focus({preventScroll:true});input.select?.();paintReviewChanges();toast('הערך הועתק לשדה הפעיל לבדיקה');
}
async function toggleSourceWords(){
 if($('#review-word-layer')){$('#review-word-layer').remove();return;}
 if(!activeSourceInput())throw Error('לחצו תחילה על שדה לתיקון, ואז הפעילו העתקה מהמקור');
 const id=state.previewDoc||state.current.id;const result=await api(`/documents/${id}/review-source`);if(state.previewDoc!==id)return;
 state.mode=null;$('#source-image').src=`/api/documents/${id}/image`;state.reviewWords=result.tokens;state.reviewWordsSource=id;
 const layer=document.createElement('div');layer.id='review-word-layer';layer.dataset.image=`/api/documents/${id}/image`;layer.innerHTML=result.tokens.map((t,i)=>`<button type="button" tabindex="-1" data-source-word="${i}" aria-label="העתקת ${esc(t.text)}" title="${esc(t.text)}" style="left:${t.x*100}%;top:${t.y*100}%;width:${t.w*100}%;height:${t.h*100}%">${esc(t.text)}</button>`).join('');$('#canvas').append(layer);
 if(!result.tokens.length)toast('אין מילים עם מיקום באזור הזה. אפשר לסמן אזור שדה או להקליד.');else toast('לחצו על המילה או המספר במקור להעתקה לשדה הפעיל');
}
function decorateReviewTools(){
 if(!$('#review-form')||$('#review-workspace-tools'))return;
 $('.review-tabs').insertAdjacentHTML('afterend','<details id="review-workspace-tools"><summary>כלי תיקון מהיר והסביבה האישית</summary><div class="inline"><button data-action="review-copy-source">לחיצה להעתקה מהמקור</button><button data-action="review-calculator">מחשבון מע״מ</button><button data-action="review-snippets">השלמות אישיות</button><button data-action="review-previous-supplier">חשבונית קודמת מהספק</button><button data-action="review-flag">לחזור למסמך הזה</button><button data-action="review-preferences">סדר שדות וקיצורים</button></div><div id="review-snippet-buttons"></div></details>');
 $('.document-preview').insertAdjacentHTML('afterbegin','<div id="review-field-loupe"><strong>מגדלת לשדה הפעיל</strong><small>בחרו שדה כדי לראות את אזור המקור שלו.</small><canvas hidden></canvas></div>');
 $('#review-workbench').insertAdjacentHTML('beforeend',`<details class="review-thumbnails"><summary>תמונות החשבוניות בתור · מעבר ישיר</summary><div>${reviewQueue().map(d=>`<button data-review-thumb="${d.id}" ${d.id===state.current.id?'aria-current="true"':''}><img loading="lazy" src="/api/documents/${d.id}/image" alt="עמוד ${d.page}"><span>${esc(d.fields.number||d.source_name)}</span></button>`).join('')}</div></details>`);
 renderSupplierSearch();applyWorkspacePreferences();renderSnippetButtons();
 new MutationObserver(()=>{const layer=$('#review-word-layer');if(layer&&layer.dataset.image!==$('#source-image').getAttribute('src'))layer.remove();}).observe($('#source-image'),{attributes:true,attributeFilter:['src']});
 $('.editor').addEventListener('scroll',scheduleSession,{passive:true});$('.document-preview').addEventListener('scroll',scheduleSession,{passive:true});
 if(state.resumeSnapshot?.document_id===state.current.id){const resume=state.resumeSnapshot;state.resumeSnapshot=null;applyReviewZoom(resume.zoom);reviewTab(resume.tab);const image=$('#source-image');requestAnimationFrame(async()=>{try{await image.decode();}catch{}if(state.current?.id!==resume.document_id||!image.isConnected)return;const input=$('#review-form')?.elements.namedItem(resume.field);input?.focus({preventScroll:true});$('.editor').scrollTop=resume.editor_scroll;$('.document-preview').scrollTop=resume.source_scroll;});}
}
const reviewBeforeTools=review;
review=function(id){reviewBeforeTools(id);if(!$('#review-form'))return;state.activeReviewField='';state.activeReviewSelector=null;decorateReviewTools();loadWorkspace().then(()=>{if(state.current?.id===id){applyWorkspacePreferences();renderSnippetButtons();}}).catch(e=>toast(e.message,true));};
function currentSnippets(){const supplier=$('#supplier-link')?.value;return (state.reviewTools?.snippets||[]).filter(s=>!s.supplier_id||s.supplier_id===supplier).sort((a,b)=>Boolean(b.supplier_id)-Boolean(a.supplier_id));}
function renderSnippetButtons(){if($('#review-snippet-buttons'))$('#review-snippet-buttons').innerHTML=currentSnippets().map(s=>`<button data-append-snippet="${s.id}" title="${esc(s.content)}">;${esc(s.shortcut)}</button>`).join('');}
function appendSnippet(id){const snippet=currentSnippets().find(s=>s.id===id),input=$('#field-notes');if(!snippet||!input)return;const next=input.value+(input.value?'\n':'')+snippet.content;if(next.length>4000)throw Error('ההערה ארוכה מדי');input.value=next;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();}
Object.assign(actions,{
 'review-copy-source':()=>toggleSourceWords(),
 'review-resume':async()=>{const value=await loadWorkspace(true);if(!value.session)return;state.resumeSnapshot=value.session;review(value.session.document_id);},
 'review-flag':async()=>{await state.reviewSessionSaver();if(reviewDirty())await saveDocument(false);const d=state.current,flag=state.reviewTools.flags.find(f=>f.document_id===d.id);state.flagDoc=d.id;openModal('לחזור למסמך הזה',`<form id="review-flag-form" class="dialog-body"><p>${esc(d.fields.number)} · הסימון אינו משנה את מצב החשבונית.</p><label class="inline"><input name="marked" type="checkbox" ${flag?'checked':'checked'}>להוסיף לתור החזרה שלי</label><label for="review-flag-note">מה נשאר לבדוק?</label><textarea id="review-flag-note" name="note" maxlength="2000">${esc(flag?.note||'')}</textarea><button class="primary">שמירת הסימון וחזרה למסמך</button></form>`);}
});
document.addEventListener('focusin',e=>{
 const input=e.target;if(!input.closest('#review-form'))return;
 if(input.id&&fieldLabels[input.name]){state.activeReviewField=input.name;state.activeReviewSelector='#'+input.id;drawFieldLoupe(state.current.field_sources?.[input.name],toolFieldLabel(input.name));}
 else if(input.hasAttribute('data-check-number')){state.activeReviewField='';state.activeReviewSelector=`[data-check-number="${input.dataset.checkNumber}"]`;drawFieldLoupe(state.checks[Number(input.dataset.checkNumber)]?.region,'מספר הצ׳ק הפעיל');}
 scheduleSession();
});
document.addEventListener('input',e=>{
 if(e.target.id==='review-supplier-search')searchSuppliers();
 if(e.target.id==='field-notes'){
  const input=e.target,left=input.value.slice(0,input.selectionStart),match=left.match(/(?:^|\s);([A-Za-z0-9א-ת_]+) $/),snippet=match&&currentSnippets().find(s=>s.shortcut===match[1]);
  if(snippet){const start=input.selectionStart-match[1].length-2;input.setRangeText(snippet.content,start,input.selectionStart,'end');paintReviewChanges();}
 }
});
document.addEventListener('change',e=>{if(e.target.id==='supplier-link')renderSnippetButtons();if(e.target.id==='review-queue-kind'){state.reviewToolQueue=e.target.value;renderDocuments();}if(e.target.id==='bulk-review-field'){const old=$('#bulk-review-value');old.outerHTML=e.target.value==='purpose'?`<select id="bulk-review-value" name="value">${Object.entries({supplier_invoice:'חשבונית ספק',journal:'פקודת יומן',reconciliation:'התאמה',archive:'ארכיון'}).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select>`:'<input id="bulk-review-value" name="value" maxlength="2000">';}});
document.addEventListener('keydown',e=>{
 if(e.target.id!=='review-supplier-search')return;
 if(['ArrowDown','ArrowUp'].includes(e.key)&&state.quickSuppliers?.length){e.preventDefault();state.quickSupplierIndex=(state.quickSupplierIndex+(e.key==='ArrowDown'?1:-1)+state.quickSuppliers.length)%state.quickSuppliers.length;$$('[data-quick-supplier]').forEach((el,i)=>el.setAttribute('aria-selected',String(i===state.quickSupplierIndex)));e.target.setAttribute('aria-activedescendant','quick-supplier-'+state.quickSupplierIndex);}
 if(e.key==='Enter'){e.preventDefault();chooseQuickSupplier(state.quickSupplierIndex||0);}
 if(e.key==='Escape'){$('#review-supplier-results').innerHTML='';e.target.setAttribute('aria-expanded','false');e.preventDefault();e.stopPropagation();}
});
document.addEventListener('click',e=>{const button=e.target.closest('button');if(!button)return;run(async()=>{
 if(button.dataset.quickSupplier!==undefined)chooseQuickSupplier(Number(button.dataset.quickSupplier));
 if(button.dataset.sourceWord!==undefined){const word=state.reviewWords[Number(button.dataset.sourceWord)];copySourceValue(word.text);$('#source-image').src=`/api/documents/${state.reviewWordsSource}/image`;drawFieldLoupe(word,'הערך שנבחר במקור');}
 if(button.dataset.appendSnippet)appendSnippet(button.dataset.appendSnippet);
 if(button.dataset.reviewThumb)await reviewOperation(async()=>{if(reviewDirty())await saveDocument(false);review(button.dataset.reviewThumb);});
 });});

async function leaveReviewForTool(){
 const id=state.current?.id;if(id&&$('#review-form')){await state.reviewSessionSaver();if(reviewDirty())await saveDocument(false);}return id;
}
actions['review-calculator']=async()=>{
 state.calculatorDoc=await leaveReviewForTool();
 openModal('מחשבון סכומים ומע״מ',`<form id="review-calculator-form" class="dialog-body"><p>התוצאה היא הצעה להעתקה לשדות, לבדיקה מול המקור.</p><label for="calculator-mode">הסכום שידוע לי</label><select id="calculator-mode" name="mode"><option value="net">לפני מע״מ</option><option value="total">כולל מע״מ</option></select><label for="calculator-amount">סכום</label><input id="calculator-amount" name="amount" inputmode="decimal" required><label for="calculator-rate">שיעור מע״מ באחוזים</label><input id="calculator-rate" name="rate" value="18" inputmode="decimal" required><button>חישוב והצגת התוצאה</button><div id="calculator-result"></div></form>`);
};
actions['review-snippets']=async()=>{
 state.snippetDoc=await leaveReviewForTool();await loadWorkspace(true);
 openModal('השלמות וקיצורי הערות אישיים',`<div class="dialog-body"><p>בזמן כתיבה בהערה אפשר להקליד ;קיצור ואחריו רווח. אפשר גם לבחור השלמה בכפתור.</p><form id="review-snippet-form"><label for="snippet-shortcut">קיצור ללא רווחים</label><input id="snippet-shortcut" name="shortcut" maxlength="40" required><label for="snippet-content">הטקסט להשלמה</label><textarea id="snippet-content" name="content" maxlength="2000" required></textarea><label for="snippet-supplier">היכן להציע את ההשלמה?</label><select id="snippet-supplier" name="supplier_id"><option value="">בכל מסמכי החברה</option>${state.suppliers.map(s=>`<option value="${s.id}">${esc(s.code)} · ${esc(s.name)}</option>`).join('')}</select><button class="primary">שמירת השלמה אישית</button></form><div id="snippet-list">${state.reviewTools.snippets.map(s=>`<div class="snippet-row"><strong>;${esc(s.shortcut)}</strong><span>${esc(s.content)}</span><button data-delete-snippet="${s.id}">מחיקה</button></div>`).join('')}</div><button data-action="review-return-tool">חזרה למסמך</button></div>`);
};
actions['review-return-tool']=()=>{const id=state.snippetDoc||state.calculatorDoc||state.preferencesDoc;closeModal();if(id)review(id);};
actions['review-preferences']=async()=>{
 state.preferencesDoc=await leaveReviewForTool();const value=await loadWorkspace(true),prefs=value.preferences;
 const order=[...prefs.field_order,...toolFieldNames.filter(k=>!prefs.field_order.includes(k))];
 openModal('סביבת הבדיקה האישית שלי',`<form id="review-preferences-form" class="dialog-body"><p>ההעדפות נשמרות במאגר האישי של החברה במחשב הזה.</p><div class="form-grid"><div><label for="review-source-width">רוחב המקור</label><select id="review-source-width" name="source_width">${[40,50,60].map(n=>`<option value="${n}" ${prefs.source_width===n?'selected':''}>${n}% מהמסך</option>`).join('')}</select></div><div><label for="review-font-size">גודל אות בשדות</label><select id="review-font-size" name="font_size">${[12,14,16,18,20].map(n=>`<option ${prefs.font_size===n?'selected':''}>${n}</option>`).join('')}</select></div>${Object.entries({approve:'אישור והבא',next:'מעבר להבא',previous:'חזרה לקודם'}).map(([key,label])=>`<div><label for="key-${key}">${label}</label><select id="key-${key}" name="key_${key}">${({approve:['Enter','Ctrl+Enter','F9'],next:['Alt+ArrowLeft','PageDown'],previous:['Alt+ArrowRight','PageUp']})[key].map(k=>`<option ${prefs.keys[key]===k?'selected':''}>${k}</option>`).join('')}</select></div>`).join('')}</div><h3>אילו שדות להציג ראשונים, ובאיזה סדר?</h3><p>סמנו שדות והזיזו אותם למעלה או למטה. שאר השדות יישארו בהמשך הטופס.</p><div id="review-field-order">${order.map(k=>`<div class="review-order-row" data-order-field="${k}"><label class="inline"><input type="checkbox" ${prefs.field_order.includes(k)?'checked':''}>${esc(toolFieldLabel(k))}</label><button type="button" data-order-move="-1" aria-label="הזזת ${esc(toolFieldLabel(k))} למעלה">↑</button><button type="button" data-order-move="1" aria-label="הזזת ${esc(toolFieldLabel(k))} למטה">↓</button></div>`).join('')}</div><button class="primary">שמירת הסביבה האישית</button></form>`);
};
actions['review-bulk']=async()=>{
 await leaveReviewForTool();const ids=[...state.selected];if(!ids.length)throw Error('בחרו מסמכים מהרשימה');state.bulkReviewIds=ids;
 openModal('תיקון מרוכז למסמכים נבחרים',`<form id="review-bulk-form" class="dialog-body"><p>${ids.length} מסמכים נבחרו. חשבוניות שישתנו יחזרו לבדיקה לפני אישור.</p><label for="bulk-review-field">שדה לתיקון</label><select id="bulk-review-field" name="field">${['expense_account','vat_account','transaction_type','cost_center','project','notes','purpose'].map(k=>`<option value="${k}">${esc(toolFieldLabel(k))}</option>`).join('')}</select><label for="bulk-review-value">הערך החדש</label><input id="bulk-review-value" name="value" maxlength="2000"><label class="inline"><input name="only_empty" type="checkbox" checked>מילוי רק כאשר השדה ריק</label><button>תצוגה מקדימה לפני תיקון</button><div id="bulk-review-preview"></div></form>`);
};
actions['review-undo-last']=async()=>{
 await leaveReviewForTool();const value=await loadWorkspace(true),d=value.last_approval;if(!d)throw Error('אין אישור שטרם יוצא להחזיר לבדיקה');state.undoReviewDoc=d;
 openModal('החזרת האישור האחרון לבדיקה',`<div class="dialog-body"><p>${esc(d.supplier?.name||d.fields.supplier_name)} · ${esc(d.fields.number)} · ${esc(moneyLabel(d.fields.total,d.fields.currency))}</p><p>המסמך יחזור לתור הבדיקה. נתוניו נשמרים, והוא לא יהיה מוכן ליצוא עד לאישור נוסף.</p><button class="primary" data-action="review-confirm-undo">החזרת המסמך הזה לבדיקה</button><button data-action="close">סגירה</button></div>`);
};
actions['review-confirm-undo']=async()=>{const d=state.undoReviewDoc;await api(coPath('/review-undo-approval'),{method:'POST',body:{confirmed:true,document_id:d.id,revision:d.revision}});await refresh();await loadWorkspace(true);closeModal();await render();review(d.id);toast('האישור הוחזר לבדיקה');};
actions['review-previous-supplier']=async()=>{
 const id=await leaveReviewForTool(),d=state.current,supplier=$('#supplier-link')?.value||d.supplier_id;
 const previous=state.docs.filter(p=>p.id!==id&&p.supplier_id===supplier&&p.status!=='archived'&&supplier&&((p.fields.date||p.created)<(d.fields.date||d.created)||((p.fields.date||'')===(d.fields.date||'')&&p.created<d.created))).sort((a,b)=>String(b.fields.date||b.created).localeCompare(String(a.fields.date||a.created))||b.created.localeCompare(a.created))[0];
 if(!previous)throw Error('אין עדיין חשבונית נוספת מהספק הזה בחברה');state.previousSupplierComparison={id,previous};
 const copyFields=['expense_account','vat_account','transaction_type','cost_center','project','purpose','currency','notes'];
 openModal('השוואה לחשבונית קודמת מאותו ספק',`<div class="dialog-body"><p>${esc(d.supplier?.name||d.fields.supplier_name)} · בחרו רק פרטים חוזרים שתרצו להעתיק לבדיקה.</p><div class="previous-invoice-images"><figure><figcaption>הנוכחית · ${esc(d.fields.number)}</figcaption><img src="/api/documents/${id}/image" alt="החשבונית הנוכחית"></figure><figure><figcaption>הקודמת · ${esc(previous.fields.number)} · ${esc(previous.fields.date)}</figcaption><img src="/api/documents/${previous.id}/image" alt="חשבונית קודמת מאותו ספק"></figure></div><table><thead><tr><th>להעתיק</th><th>שדה</th><th>נוכחית</th><th>קודמת</th></tr></thead><tbody>${copyFields.map(k=>`<tr><td><input type="checkbox" data-previous-copy="${k}" aria-label="העתקת ${esc(toolFieldLabel(k))}"></td><td>${esc(toolFieldLabel(k))}</td><td>${esc(d.fields[k])}</td><td>${esc(previous.fields[k])}</td></tr>`).join('')}</tbody></table><button class="primary" data-action="review-copy-previous">העתקת השדות שנבחרו לבדיקה</button><button data-action="review-close-previous">חזרה בלי העתקה</button></div>`,'review-dialog');
};
actions['review-copy-previous']=()=>{const selected=$$('[data-previous-copy]:checked').map(el=>el.dataset.previousCopy),{id,previous}=state.previousSupplierComparison;review(id);for(const key of selected){const input=$('#review-form').elements.namedItem(key);if(input){input.value=previous.fields[key]||'';input.dispatchEvent(new Event('input',{bubbles:true}));}}paintReviewChanges();toast('הפרטים החוזרים הועתקו לבדיקה מול המקור');};
actions['review-close-previous']=()=>review(state.previousSupplierComparison.id);

const markBeforeQuickChoices=markCheck;
markCheck=async function(){await markBeforeQuickChoices();const choices=state.checkProposals?.filter(p=>p.number).slice(0,9)||[];if(!choices.length)return;state.quickCheckChoices=choices;const panel=document.createElement('section');panel.id='quick-check-choices';panel.tabIndex=0;panel.dataset.reviewNoApprove='';panel.setAttribute('aria-label','בחירה מהירה בהצעות צ׳ק');panel.innerHTML='<strong>בחירת הצעה במקש 1–9, ואז בדיקה ואישור הצ׳ק</strong>'+choices.map((p,i)=>`<button data-quick-check-choice="${i}">${i+1} · ${esc(p.number)} · ${esc(p.engine)}</button>`).join('');$('#region-actions').append(panel);panel.focus();};
function chooseCheckProposal(index){const proposal=state.quickCheckChoices?.[index],check=state.checks.at(-1);if(!proposal||!check)return;check.number=proposal.number;check.engine=proposal.engine;check.confirmed=false;renderChecks();toast('ההצעה נבחרה. בדקו מול המקור וסמנו את הצ׳ק כאישור שלכם.');}
document.addEventListener('keydown',e=>{if(e.target.closest('#quick-check-choices')&&/^[1-9]$/.test(e.key)){e.preventDefault();e.stopPropagation();if(!e.repeat)chooseCheckProposal(Number(e.key)-1);}});
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;run(async()=>{
 if(b.dataset.quickCheckChoice!==undefined)chooseCheckProposal(Number(b.dataset.quickCheckChoice));
 if(b.dataset.deleteSnippet){await api(coPath('/review-snippets/'+b.dataset.deleteSnippet),{method:'DELETE'});await actions['review-snippets']();}
 if(b.dataset.orderMove){const row=b.closest('[data-order-field]'),other=Number(b.dataset.orderMove)<0?row.previousElementSibling:row.nextElementSibling;if(other){if(Number(b.dataset.orderMove)<0)other.before(row);else other.after(row);}}
 if(b.dataset.applyCalculator){const values=state.reviewCalculator;review(state.calculatorDoc);for(const key of ['net','vat','total']){const input=$('#review-form').elements.namedItem(key);input.value=values[key];input.dispatchEvent(new Event('input',{bubbles:true}));}paintReviewChanges();toast('תוצאת החישוב הועתקה לשלושת השדות לבדיקה');}
 if(b.dataset.applyReviewBulk){const request=state.reviewBulkRequest;await api(coPath('/review-bulk-apply'),{method:'POST',body:{...request,confirmed:true}});closeModal();await refresh();await loadWorkspace(true);await render();toast('התיקון המרוכז נשמר. המסמכים ששונו ממתינים לבדיקה ולאישור.');}
 });});
document.addEventListener('submit',e=>{
 const form=e.target;if(!['review-flag-form','review-calculator-form','review-snippet-form','review-preferences-form','review-bulk-form'].includes(form.id))return;e.preventDefault();e.stopImmediatePropagation();run(async()=>{
 const v=Object.fromEntries(new FormData(form));
 if(form.id==='review-flag-form'){await api(`/documents/${state.flagDoc}/review-flag`,{method:'PUT',body:{marked:v.marked==='on',note:v.note}});await loadWorkspace(true);review(state.flagDoc);toast('סימון החזרה נשמר');}
 if(form.id==='review-calculator-form'){const result=await api('/review-calculator',{method:'POST',body:v});state.reviewCalculator=result;$('#calculator-result').innerHTML=`<dl>${Object.entries(result).map(([k,val])=>`<dt>${esc(toolFieldLabel(k))}</dt><dd dir="ltr">${esc(val)}</dd>`).join('')}</dl><button type="button" data-apply-calculator="true" class="primary">העתקת התוצאה לשדות לבדיקה</button>`;}
 if(form.id==='review-snippet-form'){await api(coPath('/review-snippets'),{method:'POST',body:v});await actions['review-snippets']();toast('ההשלמה האישית נשמרה');}
 if(form.id==='review-preferences-form'){const field_order=$$('[data-order-field]').filter(row=>$('input',row).checked).map(row=>row.dataset.orderField);await api(coPath('/review-preferences'),{method:'PUT',body:{source_width:Number(v.source_width),font_size:Number(v.font_size),field_order,keys:{approve:v.key_approve,next:v.key_next,previous:v.key_previous}}});await loadWorkspace(true);const id=state.preferencesDoc;closeModal();if(id)review(id);else await render();toast('התצוגה וקיצורי המקלדת נשמרו אישית');}
 if(form.id==='review-bulk-form'){const request={ids:state.bulkReviewIds,fields:{[v.field]:v.value},only_empty:v.only_empty==='on'};const result=await api(coPath('/review-bulk-preview'),{method:'POST',body:request});state.reviewBulkRequest={...request,revisions:Object.fromEntries(result.rows.map(r=>[r.id,r.revision]))};$('#bulk-review-preview').innerHTML=`<table><thead><tr><th>מסמך</th><th>לפני</th><th>אחרי</th><th>מצב</th></tr></thead><tbody>${result.rows.map(r=>`<tr><td>${esc(r.number)} · ${esc(r.supplier)}</td><td>${esc(Object.values(r.before).join(' · '))}</td><td>${esc(Object.values(r.after).join(' · ')||'ללא שינוי')}</td><td>${r.editable?'יחזור לבדיקה':'נעול; הוציאו מהבחירה'}</td></tr>`).join('')}</tbody></table><button type="button" class="primary" data-apply-review-bulk="true" ${result.rows.some(r=>!r.editable)?'disabled':''}>אישור התיקון שהוצג למסמכים האלה</button>`;}
 });
},true);
