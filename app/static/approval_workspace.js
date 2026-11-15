'use strict';
Object.assign(names,{documents:'מסמכים לאישור'});
Object.assign(descriptions,{documents:'פותחים מסמך, בודקים את העמוד המלא מול הנתונים ומאשרים למעבר הבא.'});
const approvalReviewBefore=review;
function approvalField(container,key){const input=$('#review-form')?.elements.namedItem(key);if(!input)return;const div=input.closest('div');if(div&&div!==container)container.append(div);}
function approvalCard(title,cls=''){const section=document.createElement('section');section.className='approval-card '+cls;section.innerHTML=`<h3>${esc(title)}</h3><div class="approval-fields"></div>`;return section;}
function fitApprovalPage(){
 const stage=$('#approval-source-stage'),image=$('#source-image'),canvas=$('#canvas');if(!stage||!image.naturalWidth||!state.current||!$('#modal').open)return;
 const zoom=state.reviewZooms?.[state.current.id]||1;
 const width=Math.min(stage.clientWidth-20,(stage.clientHeight-20)*image.naturalWidth/image.naturalHeight);
 if(width<=0)return;canvas.style.width=Math.max(1,width)*zoom+'px';stage.classList.toggle('approval-source-zoomed',zoom>1);
}
review=function(id){
 state.approvalObserver?.disconnect();if(state.resumeSnapshot?.document_id!==id){state.reviewZooms??={};state.reviewZooms[id]=1;}approvalReviewBefore(id);if(!$('#review-form'))return;
 const modal=$('#modal');modal.classList.add('approval-workspace');
 const editor=$('.editor'),form=$('#review-form'),fieldset=$('fieldset',form);
 const essentials=document.createElement('div');essentials.id='approval-essentials';
 const supplier=approvalCard('הספק'),invoice=approvalCard('פרטי החשבונית','approval-invoice'),money=approvalCard('סכומים','approval-amounts'),checks=approvalCard('צ׳קים','approval-checks');
 for(const k of ['supplier_name','supplier_tax_id','supplier_id'])approvalField($('.approval-fields',supplier),k);
 const remember=$('#remember')?.closest('div');if(remember)$('.approval-fields',supplier).append(remember);
 for(const k of ['kind','number','date','due_date','recipient_tax_id','allocation'])approvalField($('.approval-fields',invoice),k);
 for(const k of ['net','vat','total','currency','expense_account','notes'])approvalField($('.approval-fields',money),k);
 for(const selector of ['.inline-check-actions','#inline-check-result','#initial-check-suggestions','#inline-check-candidates','#checks','[data-action="add-check"]']){const node=$(selector,form);if(node)$('.approval-fields',checks).append(node);}
 const writer=$('#learning-writer')?.closest('div');if(writer){writer.classList.add('approval-writer');$('.approval-fields',checks).prepend(writer);}
 const amountNotice=$('[name=amount_reviewed]',money)?.closest('.notice');if(amountNotice){amountNotice.classList.add('approval-amount-warning');$('.approval-fields',money).append(amountNotice);}
 essentials.append(supplier,invoice,money,checks);
 const more=document.createElement('details');more.id='approval-more';more.innerHTML='<summary>פרטים נוספים, שורות פריטים ולמידה</summary><div></div>';
 const rest=$('div',more);while(fieldset.firstChild)rest.append(fieldset.firstChild);fieldset.append(essentials,more);
 const actionsBar=$('.review-advanced-actions');if(actionsBar){const tools=document.createElement('details');tools.id='approval-extra-actions';tools.innerHTML='<summary>כלי מסמך נוספים</summary>';actionsBar.before(tools);tools.append(actionsBar);const local=$('[data-action="local-document-reread"]',actionsBar);if(local)tools.before(local);}
 const issues=$('#review-issues');if(issues){const details=document.createElement('details');details.id='approval-validation';details.innerHTML=`<summary>${state.current.issues.length?state.current.issues.length+' נושאים לבדיקה · F8 למעבר':'שדות החובה תקינים · בדקו מול המקור'}</summary>`;issues.before(details);details.append(issues);supplier.prepend(details);}
 // Keep all secondary controls reachable, while the ordinary approval surface is flat.
 const tabs=$('.review-tabs');if(tabs)editor.prepend(tabs);
 const stage=document.createElement('div');stage.id='approval-source-stage';$('#canvas').before(stage);stage.append($('#canvas'));
 $('#source-image').addEventListener('load',fitApprovalPage);requestAnimationFrame(fitApprovalPage);
 const observer=new ResizeObserver(fitApprovalPage);state.approvalObserver=observer;observer.observe(stage);$('#source-image').addEventListener('load',()=>{if(!stage.isConnected)observer.disconnect();},{once:true});
 state.reviewBaseline=reviewSignature();
};
const approvalZoomBefore=applyReviewZoom;applyReviewZoom=function(zoom){approvalZoomBefore(zoom);fitApprovalPage();};
const approvalPreferencesBefore=applyWorkspacePreferences;applyWorkspacePreferences=function(){approvalPreferencesBefore();if($('#approval-essentials')){const personal=$('#personal-review-fields');if(personal&&!personal.contains($('#approval-essentials'))){for(const card of $$('.approval-card')){const grid=$('.approval-fields',card);for(const child of Array.from(personal.children)){const keys=$$('input,select,textarea',child).map(el=>el.name);if(keys.some(k=>({הספק:['supplier_name','supplier_tax_id','supplier_id'], 'פרטי החשבונית':['kind','number','date','due_date','recipient_tax_id','allocation'],סכומים:['net','vat','total','currency','expense_account','notes']})[$('h3',card).textContent]?.includes(k)))grid.append(child);}}personal.remove();const wrapper=document.createElement('div');wrapper.id='personal-review-fields';$('#approval-essentials').before(wrapper);wrapper.append($('#approval-essentials'));}fitApprovalPage();}};
const approvalIssueBefore=reviewIssueFocus;reviewIssueFocus=function(){const issues=(state.current?.issues||[]).join(' ');if(/כרטיס הוצאה|כרטיס מע״מ|סוג תנועה|פרויקט|מרכז עלות/.test(issues)&&$('#approval-more'))$('#approval-more').open=true;approvalIssueBefore();};
const approvalCloseBefore=closeModal;closeModal=function(){state.approvalObserver?.disconnect();approvalCloseBefore();$('#modal').classList.remove('approval-workspace');};
const approvalModalBefore=openModal;openModal=function(...args){$('#modal').classList.remove('approval-workspace');approvalModalBefore(...args);};
function invoicePendingChanges(){return Boolean($('#review-form')&&(reviewDirty()||$('#region-reading-draft')));}
window.addEventListener('resize',fitApprovalPage);
