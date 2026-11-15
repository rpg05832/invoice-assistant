'use strict';
const rereadTargets=['supplier_name','supplier_tax_id','recipient_tax_id','number','date','due_date','allocation','net','vat','total','notes','check'];
const reviewBeforeSourceTools=review;
review=function(id){
 reviewBeforeSourceTools(id);if(!$('#review-form'))return;
 state.freeLoupe=false;
 const readonly=!editableReview(state.current);
 $('.preview-tools').insertAdjacentHTML('beforeend',`<button type="button" data-action="source-magnifier" aria-pressed="false">זכוכית מגדלת</button><select id="source-reread-field" data-review-no-approve aria-label="שדה לסריקת אזור">${rereadTargets.map(k=>`<option value="${k}">${esc(k==='check'?'מספר צ׳ק':fieldLabels[k])}</option>`).join('')}</select><button type="button" data-action="source-reread-region" ${readonly?'disabled':''}>סריקת אזור</button>`);
 const glass=document.createElement('canvas');glass.id='source-pointer-loupe';glass.width=520;glass.height=280;glass.hidden=true;glass.setAttribute('aria-label','הגדלת המסמך סביב הסמן');$('.document-preview').append(glass);
 const image=$('#source-image');
 image.addEventListener('pointermove',e=>{
  if(!state.freeLoupe||state.mode||$('#region-reading-draft')||!image.naturalWidth){glass.hidden=true;return;}
  const r=image.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*image.naturalWidth,y=(e.clientY-r.top)/r.height*image.naturalHeight;
  const scale=3*r.width/image.naturalWidth,sw=260/scale,sh=140/scale,ctx=glass.getContext('2d');
  ctx.fillStyle='white';ctx.fillRect(0,0,520,280);ctx.drawImage(image,x-sw/2,y-sh/2,sw,sh,0,0,520,280);
  glass.style.left=Math.max(8,Math.min(window.innerWidth-270,e.clientX+18))+'px';glass.style.top=Math.max(8,Math.min(window.innerHeight-150,e.clientY+18))+'px';glass.hidden=false;
 });
 image.addEventListener('pointerleave',()=>glass.hidden=true);
 image.addEventListener('load',()=>glass.hidden=true);
};
function sourceMagnifierOff(){state.freeLoupe=false;$('#source-pointer-loupe')?.setAttribute('hidden','');$('.document-preview')?.classList.remove('free-source-loupe');$('[data-action="source-magnifier"]')?.setAttribute('aria-pressed','false');}
Object.assign(actions,{
 'source-magnifier':()=>{const enable=!state.freeLoupe;sourceMagnifierOff();if(enable){state.freeLoupe=true;$('.document-preview').classList.add('free-source-loupe');$('[data-action="source-magnifier"]').setAttribute('aria-pressed','true');$('#region-hint').textContent='העבירו את הסמן מעל המסמך להגדלה. לחיצה נוספת על זכוכית מגדלת מכבה אותה.';}},
 'source-reread-region':()=>{sourceMagnifierOff();const field=$('#source-reread-field').value;beginInlineRegion(field);}
});
document.addEventListener('focusin',e=>{if(rereadTargets.includes(e.target.name)&&$('#source-reread-field'))$('#source-reread-field').value=e.target.name;});
