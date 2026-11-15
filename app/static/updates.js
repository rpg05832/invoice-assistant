'use strict';
$('.sidebar-bottom').insertAdjacentHTML('afterbegin','<button class="app-update-button" data-action="app-updates">גרסה ועדכון התוכנה</button>');
let appUpdatePoll=null;
function updateView(value){
 const r=value.release,j=value.job;
 return `<div class="dialog-body" id="application-updates"><h3>גרסה מותקנת: ${esc(value.current.version)}</h3><p>עדכונים יורדים מהמאגר של התוכנה ב־GitHub. המסמכים, החברות והלמידה האישית נשארים במחשב.</p>${!value.supported?'<p>התקנה מתוך התוכנה זמינה בחבילת Windows המותקנת.</p>':''}<button data-action="app-update-check">בדיקת עדכון חדש</button><div id="application-update-status" role="status">${r?`<h3>גרסה זמינה: ${esc(r.version)}</h3><p>גודל ההורדה: ${Math.round(r.size/1e6)} MB</p><details><summary>פרטי הגרסה</summary><pre dir="auto">${esc(r.notes)}</pre></details>${j?.state==='ready'?'<p class="good">ההורדה ואימות הקובץ הסתיימו. ההתקנה תיצור גיבוי נתונים, תסגור את התוכנה ותפתח אותה מחדש.</p><button class="primary" data-action="app-update-install">התקנה והפעלה מחדש</button>':j?.state==='downloading'?`<p>מוריד עדכון · ${j.progress}%</p><progress value="${j.progress}" max="100"></progress>`:`<button class="primary" data-action="app-update-download" ${value.supported?'':'disabled'}>הורדת העדכון מ־GitHub</button>`}${j?.state==='failed'?`<p class="warning-text">${esc(j.message)}</p>`:''}`:'<p>לחצו על בדיקת עדכון כדי לבדוק אם פורסמה גרסה חדשה.</p>'}</div><button data-action="close">סגירה</button></div>`;
}
async function refreshAppUpdates(check=false){
 const value=await api(check?'/updates/check':'/updates',check?{method:'POST'}:{});if(!$('#application-updates'))return;
 $('#application-updates').outerHTML=updateView(value);clearInterval(appUpdatePoll);
 if(check&&!value.release)$('#application-update-status').textContent='התוכנה מעודכנת לגרסה הזמינה כרגע.';
 if(value.job?.state==='downloading')appUpdatePoll=setInterval(()=>{if(!$('#application-updates')){clearInterval(appUpdatePoll);return;}refreshAppUpdates().catch(e=>toast(e.message,true));},1000);
}
Object.assign(actions,{
 'app-updates':async()=>{if($('#review-form')&&reviewDirty())await saveDocument(false);const value=await api('/updates');openModal('עדכון התוכנה',updateView(value));await refreshAppUpdates();},
 'app-update-check':()=>refreshAppUpdates(true),
 'app-update-download':async()=>{await api('/updates/download',{method:'POST'});await refreshAppUpdates();},
 'app-update-install':async()=>{try{await api('/updates/install',{method:'POST',body:{confirmed:true}});}catch(error){await refreshAppUpdates();throw error;}$('#application-update-status').textContent='העדכון מתחיל. התוכנה תיסגר ותיפתח מחדש בסיום ההתקנה.';}
});

api('/updates').then(v=>{$('#app-version-label').textContent='גרסה '+v.current.version;}).catch(()=>{});
