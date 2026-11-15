'use strict';
const documentsBeforeDeletion=renderDocuments;
renderDocuments=function(){documentsBeforeDeletion();$('.table-bottom .inline')?.insertAdjacentHTML('beforeend',`<button class="danger" data-action="delete-selected-documents" ${state.selected.size?'':'disabled'}>מחיקה לצמיתות</button>`);};
const reviewBeforeDeletion=review;
review=function(id){reviewBeforeDeletion(id);if(!state.current)return;$('.review-footer .inline')?.insertAdjacentHTML('afterbegin','<button type="button" class="danger" data-action="delete-current-document">מחיקה לצמיתות</button>');};
async function previewDocumentDeletion(ids,returnId=null){
 const company=state.company.id;
 const value=await api(`/companies/${company}/documents/delete-preview`,{method:'POST',body:{ids}});
 state.documentDeletion={ids,token:value.token,company,returnId};
 openModal('מחיקת מסמכים לצמיתות',`<div class="dialog-body"><p>המחיקה כוללת את קובצי המקור הבאים ואת כל העמודים, הפיצולים ועמודי ההמשך המחוברים אליהם. בדקו את הרשימה לפני האישור.</p><p><strong>${value.documents.length} רשומות · ${value.sources.length} קובצי מקור</strong></p><div class="table-scroll"><table><thead><tr><th>קובץ</th><th>עמוד</th><th>מספר מסמך</th><th>מצב</th></tr></thead><tbody>${value.documents.map(d=>`<tr><td>${esc(d.name)}</td><td>${d.page}</td><td>${esc(d.number||'—')}</td><td>${badge(d.status)}</td></tr>`).join('')}</tbody></table></div><p>הספקים והלמידה האישית, כולל ${value.retained_examples} דוגמאות למידה מהמסמכים האלה, נשמרים בנפרד. עותקים בגיבויים קודמים אינם נמחקים.</p>${value.blocked.length?'<div class="notice" role="alert">בקבוצה יש מסמכים שתועדו במנת יצוא. אי אפשר למחוק את הקבוצה ולפגוע בתיעוד היצוא והקליטה.</div>':value.training_active?'<div class="notice" role="alert">יש לסיים או לעצור את האימון האישי לפני המחיקה.</div>':'<label class="inline"><input id="delete-documents-confirm" type="checkbox">בדקתי את הרשימה ואני מאשר מחיקה לצמיתות של כל המסמכים וקובצי המקור המוצגים</label><p>לא ניתן לבטל את המחיקה מתוך התוכנה.</p>'}<div class="form-actions">${value.can_delete?'<button class="danger" data-action="confirm-document-deletion" disabled>מחיקת המסמכים וקובצי המקור לצמיתות</button>':''}<button data-action="cancel-document-deletion">ביטול וחזרה</button></div></div>`);
}
Object.assign(actions,{
 'delete-selected-documents':()=>previewDocumentDeletion([...state.selected]),
 'delete-current-document':async()=>{const id=state.current.id;if(reviewDirty())await saveDocument(false);await previewDocumentDeletion([id],id);},
 'cancel-document-deletion':()=>{const id=state.documentDeletion?.returnId;if(id)review(id);else closeModal();},
 'confirm-document-deletion':async()=>{if(!$('#delete-documents-confirm')?.checked)throw Error('אשרו את רשימת המסמכים למחיקה');const request=state.documentDeletion;const result=await api(`/companies/${request.company}/documents/delete-permanently`,{method:'POST',body:{ids:request.ids,token:request.token,confirmed:true}});closeModal();state.selected.clear();state.reviewQueue=null;state.reviewTools=null;await refresh();await render();toast(result.cleanup_pending?'הרשומות נמחקו. ניקוי חלק מהקבצים טרם הושלם; ננסה שוב בהפעלה הבאה.':`${result.deleted} רשומות וקובצי המקור שלהן נמחקו לצמיתות`);}
});
document.addEventListener('change',e=>{if(e.target.id==='delete-documents-confirm')$('[data-action="confirm-document-deletion"]').disabled=!e.target.checked;});
