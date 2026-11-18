/* Plugin "Kampagnen": Oberflaeche (ES-Modul, wird vom Kern geladen). Nutzt nur ctx und die Plugin-API unter /api/plugins/campaigns. */
let C=null;                                   // ctx des Kerns: {root, $, api, post, esc, badge, fmt, getContacts, openCall}
let $,api,post,esc,badge,fmt,contactList=[];
window.P_campaigns=window.P_campaigns||{};
export async function view(ctx){
 C=ctx;({$,api,post,esc,badge,fmt}=ctx);
 const want=ctx.sel;
 await campaignsView();
 if(want)await campOpen(want);
}
function openCall(id){C.openCall(id)}
/* Hinweis zum Terminpool: keine freien Termine / zu wenige / Stand der Angebote */
function poolAlert(c){
 if(!c.pool_id)return "";
 const box=(bg,bd,t)=>`<div style="margin:8px 0;padding:9px 12px;border-radius:8px;border-left:4px solid ${bd};background:${bg};font-size:13px;line-height:1.5">${t}</div>`;
 const f=c.pool_free,o=c.notoffered_open,tot=(c.stats&&c.stats.total)||0;let h="";
 if(c.pool_error)h+=box("rgba(245,158,11,.14)","#f59e0b",`<b>Kalender nicht erreichbar:</b> ${esc(c.pool_error)}`);
 if(c.open_n>0&&f===0)h+=box("rgba(229,72,77,.14)","#e5484d",`<b>Keine freien Termine mehr.</b> ${o?`${o} von ${c.open_n} offenen Personen haben <b>noch kein Terminangebot</b> erhalten.`:"Alle offenen Personen hatten bereits ein Angebot."} Es werden keine weiteren Anrufe gestartet, bis wieder Termine frei sind.`);
 else if(c.open_n>0&&o>f)h+=box("rgba(245,158,11,.14)","#f59e0b",`<b>Nur ${f} ${f===1?"freier Termin":"freie Termine"}</b> für ${o} Personen, die noch kein Angebot erhalten haben – nicht alle bekommen ein Angebot.`);
 else if(c.status==="done"&&c.notoffered_total>0)h+=box("rgba(245,158,11,.14)","#f59e0b",`<b>${c.notoffered_total} ${c.notoffered_total===1?"Person hat":"Personen haben"} kein Terminangebot erhalten</b> (nicht erreicht oder keine Termine mehr).`);
 if(tot)h+=`<div class=hint style="margin:2px 0 6px">Termine frei: <b>${f}</b>${c.pool_total?` von ${c.pool_total}`:""} · Terminangebot erhalten: <b>${c.offered_n}</b> von ${tot}${c.notoffered_total?` · noch ohne Angebot: <b>${c.notoffered_total}</b>`:""}</div>`;
 return h;
}
async function getContacts(){contactList=await C.getContacts();return contactList}
const WD=["","Mo","Di","Mi","Do","Fr","Sa","So"];
const CST={pending:["offen","neu"],calling:["wird angerufen","warn"],booked:["Termin","ok"],done:["erledigt","ok"],voicemail:["Nachricht hinterlassen","info"],exhausted:["aufgegeben","bad"],failed:["fehlgeschlagen","bad"]};
const daysText=s=>{const d=(s||"").split(",").filter(Boolean).map(Number);return d.length===7||!d.length?"täglich":d.join()==="1,2,3,4,5"?"Mo–Fr":d.map(i=>WD[i]).join(", ")};
let campData=null;
async function campaignsView(){
 C&&C.route&&C.route(null);
 campData=await api("/api/plugins/campaigns/campaigns");campData.gcal=await api("/api/plugins/campaigns/gcal");
 const cs=campData.campaigns;
 $("#m").innerHTML=`<div class=head><div><h2><svg class=ic><use href="#i-campaign"/></svg>Kampagnen</h2><p class=sub style="margin:0">Mehrere Personen nacheinander anrufen lassen, z. B. um Termine zu vereinbaren.</p></div><button class=p onclick="P_campaigns.campEdit(null)">+ Neue Kampagne</button></div><div id=ced></div>
 <div class=grid>`+(cs.map(c=>{const s=c.stats,open=(s.pending||0)+(s.calling||0);
  return `<div class="acard ${c.status==="active"?"":"off"}"><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><h3>${esc(c.name)}</h3>${c.status==="active"?(c.wait_note?badge("wartet: "+c.wait_note,"warn"):badge(c.in_window?"läuft":"wartet auf Anrufzeit","ok",c.in_window)):c.status==="done"?badge("fertig","info"):badge("pausiert","neu")}</div>
  <div class=chips>${badge(open+" offen","neu")}${s.booked?badge(s.booked+" Termine","ok"):""}${s.done?badge(s.done+" erledigt","ok"):""}${s.voicemail?badge(s.voicemail+" Anrufbeantworter","info"):""}${s.exhausted?badge(s.exhausted+" aufgegeben","bad"):""}</div>
  ${poolAlert(c)}
  <div class=kv><span>Ziele</span><span>${s.total||0}</span><span>Anrufzeiten</span><span>${daysText(c.days)}, ${esc(c.t_from)}–${esc(c.t_to)}</span><span>Versuche</span><span>bis zu ${c.max_attempts}, Abstand ${c.retry_min>=60?Math.round(c.retry_min/60*10)/10+" h":c.retry_min+" min"}</span><span>Anrufbeantworter</span><span>${{retry:"später erneut",message:"Nachricht sprechen",hangup:"auflegen"}[c.vm_mode]}</span><span>Terminpool</span><span>${esc((campData.pools.find(p=>p.id===c.pool_id)||{}).name||"–")}</span></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap"><button class=${c.status==="active"?"s":"p"} onclick="P_campaigns.campToggle(${c.id},'${c.status==="active"?"paused":"active"}')">${c.status==="active"?"Pausieren":"Starten"}</button><button class=s onclick="P_campaigns.campOpen(${c.id})">Ziele ansehen</button><button class=s onclick="P_campaigns.campEdit(${c.id})">Bearbeiten</button></div></div>`}).join("")||`<div class="panel empty" style="grid-column:1/-1">Noch keine Kampagne.</div>`)+`</div>
 <h2 style="font-size:18px;margin:28px 0 8px">Terminpools</h2><div id=gcalbox style="margin-bottom:12px"></div><p class=hint style="margin-top:0">Freie Termine, aus denen die KI bucht und die sie den Angerufenen anbietet. Jeder Termin wird nur einmal vergeben.</p><div id=pools></div>`;
 drawPools();
 $("#gcalbox").innerHTML=gcalPanel(campData.gcal||{});
}
async function campToggle(id,st){const r=await post(`/api/plugins/campaigns/campaigns/${id}/status`,{status:st});if(!r.ok)window.toast((await r.json()).detail||"Fehler");campaignsView()}
function campEdit(id){
 const c=(id&&campData.campaigns.find(x=>x.id===id))||{name:"",task:"",agent_id:"",days:"1,2,3,4,5",t_from:"09:00",t_to:"17:00",max_attempts:3,retry_min:120,vm_mode:"retry",vm_text:"",vm_stop:0,pool_id:"",owner:"",parallel:1};
 const ds=(c.days||"").split(",");
 $("#ced").innerHTML=`<div class="panel pad" style="margin-bottom:18px"><h2 style="font-size:18px;margin-bottom:12px">${id?"Kampagne bearbeiten":"Neue Kampagne"}</h2>
 <div class=stabs id=ctabs><button data-k=a onclick="P_campaigns.campTab('a')">Allgemein</button><button data-k=z onclick="P_campaigns.campTab('z')">Zeiten und Wiederholungen</button><button data-k=v onclick="P_campaigns.campTab('v')">Anrufbeantworter</button></div><div class=ct data-k=a>
 <div class=row><div><label style="margin-top:0">Name</label><input id=k_name value="${esc(c.name)}" placeholder="z. B. Terminerinnerung Oktober"></div><div><label style="margin-top:0">Agent</label><select id=k_agent>${campData.agents.map(a=>`<option value=${a.id} ${a.id===c.agent_id?"selected":""}>${esc(a.name)}</option>`).join("")}</select></div></div>
 <label>Auftrag an die KI (<code>{name}</code> wird durch den Namen der angerufenen Person ersetzt)</label><textarea id=k_task rows=4 placeholder="z. B. Rufe {name} an und vereinbare einen Beratungstermin. Frage, ob er/sie Zeit hat, und biete freie Termine an.">${esc(c.task)}</textarea>
 <div class=row><div><label>Auftraggeber (wird genannt, optional)</label><input id=k_owner value="${esc(c.owner)}" placeholder="z. B. Praxis Müller"></div><div><label>Terminpool (optional)</label><select id=k_pool><option value="">– keiner –</option>${campData.pools.map(p=>`<option value=${p.id} ${p.id===c.pool_id?"selected":""}>${esc(p.name)}${p.kind==="gcal"?" · Google Kalender":""} (${p.free} frei)</option>`).join("")}</select></div></div>
 <div class=row><div><label>Pause zwischen Terminen (Minuten)</label><input id=k_pause type=number min=0 max=480 value="${c.pause_min||0}"><div class=hint>Mindestabstand zwischen zwei gebuchten Terminen und zu anderen Kalendereinträgen. Termine, die näher liegen, werden nicht angeboten.</div></div>
 <div><label>E-Mail zu jedem Anruf der Kampagne (optional)</label><input id=k_mail value="${esc(c.notify_mail||"")}" placeholder="name@firma.de, weitere mit Komma"><div class=hint>Mit Zusammenfassung, Ergebnis und Transkript. Benötigt den E-Mail-Versand unter Einstellungen.</div></div>
 <div><label>Titel des Kalendereintrags (bei Google-Kalender-Pools)</label><input id=k_evt value="${esc(c.event_title||"")}" placeholder="Termin: {name}"><div class=hint>Platzhalter: <code>{name}</code>, <code>{kampagne}</code></div></div></div>
</div><div class=ct data-k=z hidden>
 <div class=sec>Anrufzeiten und Wiederholungen</div>
 <div class=chips style="gap:14px;margin-bottom:8px">${[1,2,3,4,5,6,7].map(i=>`<label class=chk style="margin:0"><input type=checkbox class=k_day value=${i} ${ds.includes(String(i))?"checked":""}> ${WD[i]}</label>`).join("")}</div>
 <div class=row3><div><label style="margin-top:0">Anrufe von</label><input id=k_from type=time value="${esc(c.t_from)}"></div><div><label style="margin-top:0">bis</label><input id=k_to type=time value="${esc(c.t_to)}"></div><div><label style="margin-top:0">Gleichzeitige Anrufe</label><select id=k_par>${[1,2,3].map(n=>`<option ${c.parallel===n?"selected":""}>${n}</option>`).join("")}</select></div></div>
 <div class=row><div><label>Höchstens … Versuche pro Person</label><input id=k_att type=number min=1 max=20 value="${c.max_attempts}"></div><div><label>Abstand zwischen Versuchen (Minuten)</label><input id=k_retry type=number min=5 value="${c.retry_min}"></div></div>
</div><div class=ct data-k=v hidden>
 <div class=sec>Anrufbeantworter</div>
 <div class=row><div><label style="margin-top:0">Wenn ein Anrufbeantworter drangeht</label><select id=k_vm onchange="document.getElementById('k_vmbox').hidden=this.value!=='message'"><option value=retry ${c.vm_mode==="retry"?"selected":""}>Auflegen und später erneut versuchen</option><option value=message ${c.vm_mode==="message"?"selected":""}>Nachricht sprechen</option><option value=hangup ${c.vm_mode==="hangup"?"selected":""}>Auflegen</option></select></div></div>
 <div id=k_vmbox ${c.vm_mode==="message"?"":"hidden"}><label>Nachricht (<code>{name}</code> möglich)</label><textarea id=k_vmtext rows=3 placeholder="z. B. Guten Tag {name}, hier ist der Telefonassistent der Praxis Müller. Bitte rufen Sie uns zurück, um einen Termin zu vereinbaren.">${esc(c.vm_text)}</textarea>
 <label class=chk><input type=checkbox id=k_vmstop ${c.vm_stop?"checked":""}> Nach der Nachricht nicht erneut anrufen</label></div>
</div>
 <div class=actions><button class=p onclick="P_campaigns.campSave(${id||0})">Speichern</button><button class=s onclick="$('#ced').innerHTML=''">Abbrechen</button>${id?`<button class=d style="margin-left:auto" onclick="P_campaigns.campDelete(${id})">Kampagne löschen</button>`:""}</div></div>`;
 campTab("a");$("#ced").scrollIntoView({behavior:"smooth"});
}
function campTab(k){
 document.querySelectorAll("#ced .ct").forEach(e=>e.hidden=e.dataset.k!==k);
 document.querySelectorAll("#ctabs button").forEach(b=>b.classList.toggle("on",b.dataset.k===k));
}
async function campSave(id){
 const d={name:$("#k_name").value,task:$("#k_task").value,agent_id:+$("#k_agent").value,owner:$("#k_owner").value,pool_id:$("#k_pool").value,pause_min:+$("#k_pause").value||0,event_title:$("#k_evt").value,notify_mail:$("#k_mail").value,days:[...document.querySelectorAll(".k_day:checked")].map(e=>e.value).join(","),t_from:$("#k_from").value,t_to:$("#k_to").value,parallel:+$("#k_par").value,max_attempts:+$("#k_att").value,retry_min:+$("#k_retry").value,vm_mode:$("#k_vm").value,vm_text:$("#k_vmtext").value,vm_stop:$("#k_vmstop")?.checked?1:0};
 const r=await fetch(id?"/api/plugins/campaigns/campaigns/"+id:"/api/plugins/campaigns/campaigns",{method:id?"PUT":"POST",headers:{"content-type":"application/json"},body:JSON.stringify(d)}),j=await r.json().catch(()=>({}));
 if(!r.ok)return window.toast(j.detail||"Speichern fehlgeschlagen");
 if(!id)return campOpen(j.id);
 campaignsView();
}
async function campDelete(id){if(!await window.askConfirm("Kampagne mit allen Zielen löschen? Gebuchte Termine werden wieder frei."))return;await fetch("/api/plugins/campaigns/campaigns/"+id,{method:"DELETE"});campaignsView()}
async function campOpen(id){
 C&&C.route&&C.route(id);
 const d=await api("/api/plugins/campaigns/campaigns/"+id),c=d.campaign;campExisting=new Set(d.targets.map(t=>t.number.replace(/\D/g,"")));campCurId=id;
 if(campF.id!==id)campF={id,st:"all",q:"",sort:"status",max:60};
 campT=d.targets;campC=c;
 $("#m").innerHTML=`<div class=head><div><h2>${esc(c.name)}</h2><p class=sub style="margin:0">${daysText(c.days)}, ${esc(c.t_from)}–${esc(c.t_to)} · ${c.status==="active"?(c.wait_note?"wartet: "+esc(c.wait_note):c.in_window?"läuft gerade":"wartet auf die nächste Anrufzeit"):c.status==="done"?"fertig":"pausiert"}</p></div>
  <div style="display:flex;gap:8px"><button class=s onclick="P_campaigns.campaignsView()">← Übersicht</button><button class=${c.status==="active"?"s":"p"} onclick="P_campaigns.campToggle2(${id},'${c.status==="active"?"paused":"active"}')">${c.status==="active"?"Pausieren":"Starten"}</button></div></div>
  ${poolAlert(c)}
  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0 8px">
   <input id=k_q type=search placeholder="Ziele suchen: Name, Nummer, Ergebnis, Termin …" style="flex:1;min-width:220px" value="${esc(campF.q)}" oninput="P_campaigns.campSearch()">
   <select id=k_sort onchange="P_campaigns.campSort()" style="width:auto"><option value=status ${campF.sort==="status"?"selected":""}>Sortierung: Status</option><option value=name ${campF.sort==="name"?"selected":""}>Sortierung: Name</option><option value=termin ${campF.sort==="termin"?"selected":""}>Sortierung: Termin</option><option value=update ${campF.sort==="update"?"selected":""}>Sortierung: zuletzt geändert</option></select></div>
  <div id=k_chips style="margin-bottom:10px;display:flex;gap:6px;flex-wrap:wrap"></div>
  <div class=panel style="margin-bottom:10px"><table><tr><th>Name</th><th>Nummer</th><th>Status</th><th>Versuche</th><th>Ergebnis</th><th></th></tr><tbody id=k_tbody></tbody></table></div>
  <div id=k_more style="margin:0 0 16px;text-align:center"></div>
  <details class="panel pad" style="margin-bottom:14px;overflow:visible"${d.targets.length?"":" open"}><summary style="cursor:pointer;font-weight:600">＋ Ziele hinzufügen</summary><div style="margin-top:12px">
  <div><label style="margin-top:0">Ziel hinzufügen</label>
   <div style="position:relative"><input id=k_in autocomplete=off placeholder="Name oder Nummer eintippen – Vorschläge aus dem Telefonbuch …" oninput="P_campaigns.acInput()" onkeydown="P_campaigns.acKey(event)" onblur="setTimeout(()=>{const d=document.getElementById('k_dd');if(d)d.style.display='none'},150)">
    <div id=k_dd style="display:none;position:absolute;left:0;right:0;top:100%;z-index:20;max-height:280px;overflow:auto;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.15);margin-top:4px"></div></div>
   <div class=hint id=k_msg style="margin-top:8px">Auswahl per Klick oder ↑↓ und Enter. Eine eingetippte Nummer (oder „Name; Nummer“) wird mit Enter direkt übernommen.</div>
   <details style="margin-top:10px"><summary class=hint style="cursor:pointer">Mehrere auf einmal einfügen</summary>
    <textarea id=k_lines rows=4 style="margin-top:8px" placeholder="Anna Beispiel; 0171 1234567&#10;Max Muster; 030 555123"></textarea>
    <div class=actions style="margin-top:8px;padding-top:8px"><button class=p onclick="P_campaigns.campAddTargets(${id})">Hinzufügen</button></div></details></div></div></details>`;
 campBody();
}
let campT=[],campC=null,campF={id:0,st:"all",q:"",sort:"status",max:60};
const STORD={calling:0,booked:1,pending:2,voicemail:3,exhausted:4,failed:5,done:6};
function campFiltered(){
 const q=campF.q.trim().toLowerCase(),qd=q.replace(/\D/g,"");
 const r=campT.filter(t=>(campF.st==="all"||t.status===campF.st)&&(!q||(t.name||"").toLowerCase().includes(q)||(qd&&t.number.replace(/\D/g,"").includes(qd))||(t.result||"").toLowerCase().includes(q)||(t.slot||"").toLowerCase().includes(q)));
 const nm=(a,b)=>(a.name||a.number).localeCompare(b.name||b.number,"de");
 const by={status:(a,b)=>(STORD[a.status]??9)-(STORD[b.status]??9)||nm(a,b),name:nm,termin:(a,b)=>(a.slot_ts||9e12)-(b.slot_ts||9e12)||nm(a,b),update:(a,b)=>(b.updated||0)-(a.updated||0)};
 return r.sort(by[campF.sort]||by.status);
}
function campRow(t,c,id){return `<tr><td><b>${esc(t.name||"–")}</b></td><td class=num>${esc(t.number)}</td><td>${badge((CST[t.status]||[t.status])[0],(CST[t.status]||[0,"neu"])[1])}${t.offered?`<div class=mut style="font-size:12px">Angebot erhalten</div>`:(["pending","calling","exhausted","failed","voicemail"].includes(t.status)&&c.pool_id?`<div style="font-size:12px;color:#d97706">noch kein Angebot</div>`:"")}${t.status==="pending"&&t.next_at>Date.now()/1000?`<div class=mut style="font-size:12px">ab ${fmt(t.next_at)}</div>`:""}</td><td>${t.attempts}/${c.max_attempts}</td>
   <td>${esc(t.slot||t.result||"")}${t.summary?`<div class=mut style="font-size:12px">${esc(t.summary)}</div>`:""}${t.last_call_id>0?`<div style="font-size:12px"><a href="#/calls/${t.last_call_id}" onclick="P_campaigns.openCall(${t.last_call_id});return false">letzter Anruf</a></div>`:""}</td>
   <td style="text-align:right;white-space:nowrap">${t.status==="booked"?`<button class=s onclick="P_campaigns.campRelease(${id},${t.id})" title="Gebuchten Termin wieder freigeben (kein neuer Anruf)">Termin freigeben</button> `:""}<button class=s onclick="P_campaigns.campRetry(${id},${t.id})" title="Zurücksetzen und erneut anrufen">Neu</button> <button class=d onclick="P_campaigns.campDelTarget(${id},${t.id})">✕</button></td></tr>`}
function campBody(){
 const c=campC,id=c.id,all=campFiltered(),shown=all.slice(0,campF.max);
 const st=campT.reduce((m,t)=>(m[t.status]=(m[t.status]||0)+1,m),{});
 const chip=(k,label,n)=>`<button class=s style="${campF.st===k?"outline:2px solid var(--acc);":""}" onclick="P_campaigns.campFilter('${k}')">${label} <b>${n}</b></button>`;
 const ch=$("#k_chips");if(ch)ch.innerHTML=chip("all","Alle",campT.length)+Object.keys(CST).filter(k=>st[k]).map(k=>chip(k,CST[k][0],st[k])).join("");
 const tb=$("#k_tbody");if(tb)tb.innerHTML=shown.map(t=>campRow(t,c,id)).join("")||`<tr><td colspan=6 class=empty>${campT.length?"Keine Treffer für diese Auswahl.":"Noch keine Ziele."}</td></tr>`;
 const mo=$("#k_more");if(mo)mo.innerHTML=all.length>shown.length?`<button class=s onclick="P_campaigns.campMore()">Weitere ${Math.min(60,all.length-shown.length)} anzeigen (${shown.length} von ${all.length})</button>`:`<span class=hint>${all.length} ${all.length===1?"Eintrag":"Einträge"}</span>`;
}
function campFilter(k){campF.st=k;campF.max=60;campBody()}
function campSearch(){campF.q=$("#k_q").value;campF.max=60;campBody()}
function campSort(){campF.sort=$("#k_sort").value;campBody()}
function campMore(){campF.max+=60;campBody()}
let campExisting=new Set(),campCurId=0,acList=[],acSel=-1;
const acNorm=s=>(s||"").toLowerCase();
function acMatches(){
 const q=acNorm($("#k_in")?.value).trim(),qd=q.replace(/\D/g,"");if(!q)return[];
 return contactList.filter(x=>!campExisting.has(x.number.replace(/\D/g,""))&&(acNorm(x.name).includes(q)||acNorm(x.note).includes(q)||acNorm(x.number).includes(q)||(qd&&x.number.replace(/\D/g,"").includes(qd)))).slice(0,8);
}
async function acInput(){
 if(!contactList.length)await getContacts();
 acList=acMatches();acSel=acList.length?0:-1;acDraw();
}
function acDraw(){
 const d=$("#k_dd");if(!d)return;
 if(!acList.length){d.style.display="none";return}
 d.style.display="block";
 d.innerHTML=acList.map((x,i)=>`<div onmousedown="event.preventDefault();P_campaigns.acPick(${i})" onmouseenter="P_campaigns.acHover(${i})" style="padding:8px 12px;cursor:pointer;display:flex;justify-content:space-between;gap:12px;${i===acSel?"background:var(--acc-soft)":""}"><b>${esc(x.name)}</b><span class=num>${esc(x.number)}</span></div>`).join("");
}
function acHover(i){
 if(i===acSel)return;acSel=i;      // nur die Hervorhebung wechseln, die Zeilen nicht neu aufbauen (sonst geht der Klick verloren)
 [...document.querySelectorAll("#k_dd>div")].forEach((el,j)=>{el.style.background=j===i?"var(--acc-soft)":""});
}
function acKey(e){
 if(e.key==="ArrowDown"&&acList.length){e.preventDefault();acSel=(acSel+1)%acList.length;acDraw()}
 else if(e.key==="ArrowUp"&&acList.length){e.preventDefault();acSel=(acSel-1+acList.length)%acList.length;acDraw()}
 else if(e.key==="Escape"){acList=[];acDraw()}
 else if(e.key==="Enter"){e.preventDefault();if(acSel>=0&&acList[acSel])acPick(acSel);else acFree()}
}
async function acAdd(line,label){
 const r=await post(`/api/plugins/campaigns/campaigns/${campCurId}/targets`,{lines:line}),j=await r.json().catch(()=>({}));
 if(!r.ok)return window.toast(j.detail||"Fehler");
 if(j.invalid&&j.invalid.length)return window.toast("Ungültig: "+j.invalid.join(" | "));
 await campOpen(campCurId);window.toast(j.added?label+" hinzugefügt.":label+" ist schon dabei.");
 document.querySelector("#m details")?.setAttribute("open","");const i=$("#k_in");if(i)i.focus();
}
function acPick(i){const x=acList[i];if(x)acAdd(`${x.name}; ${x.number}`,x.name)}
function acFree(){const v=($("#k_in")?.value||"").trim();if(v.replace(/\D/g,"").length>=3)acAdd(v,v.split(";")[0])}
async function campToggle2(id,st){const r=await post(`/api/plugins/campaigns/campaigns/${id}/status`,{status:st});if(!r.ok)window.toast((await r.json()).detail||"Fehler");campOpen(id)}
async function campAddTargets(id){const r=await post(`/api/plugins/campaigns/campaigns/${id}/targets`,{lines:$("#k_lines").value}),j=await r.json();if(!r.ok)return $("#k_msg").textContent=j.detail||"Fehler";await campOpen(id);$("#k_msg").textContent=`${j.added} hinzugefügt${j.invalid.length?`, ungültig: ${j.invalid.join(" | ")}`:""}`}
async function campRelease(id,t){
 if(!await window.askConfirm("Den gebuchten Termin dieser Person freigeben? Der Eintrag im Kalender wird gelöscht. Die Person wird nicht erneut angerufen (dafür „Neu“ wählen)."))return;
 const r=await post(`/api/plugins/campaigns/targets/${t}/release-appointment`),j=await r.json().catch(()=>({}));
 if(!r.ok)return window.toast(j.detail||"Fehler");window.toast("Termin freigegeben.");campOpen(id);
}
async function campRetry(id,t){await post(`/api/plugins/campaigns/targets/${t}/retry`);campOpen(id)}
async function campDelTarget(id,t){await fetch("/api/plugins/campaigns/targets/"+t,{method:"DELETE"});campOpen(id)}
function drawPools(){
 const ps=campData.pools,g=campData.gcal||{};
 $("#pools").innerHTML=`<div class=panel><table><tr><th>Pool</th><th>Termine</th><th>frei</th><th>gebucht</th><th></th></tr>`+(ps.map(p=>`<tr><td><b>${esc(p.name)}</b>${p.kind==="gcal"?`<div class=mut style="font-size:12px">Google Kalender · Präfix „${esc(p.prefix)}“ · ${p.slot_min} min</div>`:""}</td><td>${p.total}</td><td>${p.free}</td><td>${p.booked}</td><td style="text-align:right;white-space:nowrap">${p.kind==="gcal"?`<button class=s onclick="P_campaigns.poolSync(${p.id})">Abgleichen</button> <button class=s onclick="P_campaigns.poolEdit(${p.id})">Bearbeiten</button> `:`<button class=s onclick="P_campaigns.poolGen(${p.id})">Termine erzeugen</button> `}<button class=s onclick="P_campaigns.poolShow(${p.id})">Anzeigen</button> <button class=d onclick="P_campaigns.poolDel(${p.id})">✕</button></td></tr>`).join("")||`<tr><td colspan=5 class=empty>Noch kein Pool.</td></tr>`)+`
  <tr><td colspan=5><div class=row style="grid-template-columns:1fr auto auto;gap:8px"><input id=pl_name placeholder="Neuer Pool, z. B. Beratung Oktober"><button class=s onclick="P_campaigns.poolAdd()">Manueller Pool</button><button class=s onclick="P_campaigns.poolEdit(0)" ${g.configured?"":"disabled title='Zuerst Google Kalender verbinden'"}>Kalender-Pool …</button></div></td></tr></table></div><div id=pbox style="margin-top:12px"></div>`;
}
function gcalPanel(g){
 return `<div class="panel pad" style="margin-bottom:6px"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><h3 style="margin:0">Google Kalender ${g.configured?badge("verbunden","ok"):badge("nicht verbunden","neu")}</h3></div>
  <p class=hint style="margin:6px 0 10px">Terminpools können aus einem Google Kalender gebildet werden: Platzhalter-Termine mit einem Präfix im Titel (z. B. „[Termin] Beratung“) legen fest, wann Termine vergeben werden dürfen. Gebuchte Termine trägt die KI als eigene Einträge ein. Das Dienstkonto richten Sie unter <b>Einstellungen → Google Kalender</b> ein.</p>
  ${g.configured?`<div class=hint style="margin-bottom:8px">Dienstkonto: <b>${esc(g.email)}</b> – den Kalender mit dieser Adresse teilen (Berechtigung „Änderungen an Terminen vornehmen“).</div>`:`<div class=hint style="margin-bottom:8px">Noch kein Dienstkonto eingerichtet. <button class=s onclick="location.hash='#/settings'">Dienstkonto in den Einstellungen einrichten →</button></div>`}
  <div class=row><div><label style="margin-top:0">Standard-Kalender-ID für neue Kalender-Pools</label><input id=gc_cal value="${esc(g.default_cal||"")}" placeholder="z. B. abc123@group.calendar.google.com"><div class=hint>Zu finden in den Einstellungen des Kalenders unter „Kalender integrieren“.</div></div><div></div></div>
  <div class=actions style="margin-top:10px;padding-top:10px"><button class=p onclick="P_campaigns.gcalSave()">Speichern</button><button class=s onclick="P_campaigns.gcalTest()" ${g.configured?"":"disabled"}>Verbindung testen</button><span class=hint id=gc_msg style="align-self:center"></span></div></div>`;
}
async function gcalSave(){
 const r=await fetch("/api/plugins/campaigns/gcal",{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify({default_cal:$("#gc_cal").value})}),j=await r.json().catch(()=>({}));
 if(!r.ok)return window.toast(j.detail||"Fehler");window.toast("Gespeichert.");campaignsView();
}
async function gcalTest(){
 const m=$("#gc_msg");m.textContent="Prüfe …";
 const r=await post("/api/plugins/campaigns/gcal/test",{calendar_id:$("#gc_cal").value}),j=await r.json().catch(()=>({}));
 m.textContent=r.ok?`Verbunden: „${j.title}“, ${j.events_30d} Termine in den nächsten 30 Tagen.`:(j.detail||"Fehler");
}
function poolEdit(id){
 const p=(id&&campData.pools.find(x=>x.id===id))||{name:$("#pl_name")?.value||"",kind:"gcal",cal_id:campData.gcal?.default_cal||"",prefix:"[Termin]",slot_min:30,horizon_days:60};
 $("#pbox").innerHTML=`<div class="panel pad"><h3 style="margin:0 0 8px">${id?"Kalender-Pool bearbeiten":"Neuer Kalender-Pool"}</h3>
  <div class=row><div><label style="margin-top:0">Name</label><input id=pe_name value="${esc(p.name)}" placeholder="z. B. Beratung"></div><div><label style="margin-top:0">Kalender-ID</label><input id=pe_cal value="${esc(p.cal_id)}" placeholder="leer = Standard-Kalender"></div></div>
  <div class=row3><div><label>Präfix der Platzhalter-Termine</label><input id=pe_pre value="${esc(p.prefix)}" placeholder="[Termin]"></div><div><label>Dauer je Termin (Minuten)</label><input id=pe_min type=number min=5 value="${p.slot_min}"></div><div><label>Vorausschau (Tage)</label><input id=pe_hor type=number min=1 max=365 value="${p.horizon_days}"></div></div>
  <div class=hint style="margin-top:8px">Im Kalender legen Sie Termine an, deren Titel mit dem Präfix beginnt (z. B. „[Termin] Beratung“, Dienstag 9–12 Uhr). Sie werden in Termine der angegebenen Dauer geteilt, abzüglich anderer Kalendereinträge und der Pause der Kampagne.</div>
  <div class=actions><button class=p onclick="P_campaigns.poolSaveCal(${id||0})">Speichern</button><button class=s onclick="$('#pbox').innerHTML=''">Abbrechen</button></div></div>`;
 $("#pbox").scrollIntoView({behavior:"smooth"});
}
async function poolSaveCal(id){
 const d={name:$("#pe_name").value,kind:"gcal",cal_id:$("#pe_cal").value,prefix:$("#pe_pre").value,slot_min:+$("#pe_min").value||30,horizon_days:+$("#pe_hor").value||60};
 const r=await fetch(id?`/api/plugins/campaigns/pools/${id}`:"/api/plugins/campaigns/pools",{method:id?"PUT":"POST",headers:{"content-type":"application/json"},body:JSON.stringify(d)}),j=await r.json().catch(()=>({}));
 if(!r.ok)return window.toast(j.detail||"Fehler");await campaignsView();if(!id)poolSync(j.id);
}
async function poolSync(id){
 const r=await post(`/api/plugins/campaigns/pools/${id}/sync`),j=await r.json().catch(()=>({}));
 if(!r.ok)return window.toast(j.detail||"Fehler");window.toast(`Kalender abgeglichen: ${j.added} neu, ${j.removed} entfernt.`,"ok");await campaignsView();poolShow(id);
}
async function poolAdd(){const n=$("#pl_name").value.trim();if(!n)return;await post("/api/plugins/campaigns/pools",{name:n});campaignsView()}
async function poolDel(id){if(!await window.askConfirm("Pool mit allen Terminen löschen?"))return;const r=await fetch("/api/plugins/campaigns/pools/"+id,{method:"DELETE"});if(!r.ok)window.toast((await r.json()).detail||"Fehler");campaignsView()}
function poolGen(id){
 const t=new Date(Date.now()+864e5).toISOString().slice(0,10);
 $("#pbox").innerHTML=`<div class="panel pad"><h3 style="margin:0 0 8px">Termine erzeugen</h3><div class=row3><div><label style="margin-top:0">Von (Datum)</label><input id=g_d1 type=date value="${t}"></div><div><label style="margin-top:0">Bis (Datum)</label><input id=g_d2 type=date value="${t}"></div><div><label style="margin-top:0">Dauer je Termin (min)</label><input id=g_min type=number min=5 value=30></div></div>
 <div class=chips style="gap:14px;margin:10px 0">${[1,2,3,4,5,6,7].map(i=>`<label class=chk style="margin:0"><input type=checkbox class=g_day value=${i} ${i<6?"checked":""}> ${WD[i]}</label>`).join("")}</div>
 <div class=row><div><label style="margin-top:0">Täglich von</label><input id=g_from type=time value="09:00"></div><div><label style="margin-top:0">bis</label><input id=g_to type=time value="17:00"></div></div>
 <div class=actions><button class=p onclick="P_campaigns.poolDoGen(${id})">Erzeugen</button><button class=s onclick="$('#pbox').innerHTML=''">Abbrechen</button><span class=hint id=g_msg style="align-self:center"></span></div></div>`;
 $("#pbox").scrollIntoView({behavior:"smooth"});
}
async function poolDoGen(id){const r=await post(`/api/plugins/campaigns/pools/${id}/generate`,{date_from:$("#g_d1").value,date_to:$("#g_d2").value,minutes:+$("#g_min").value,days:[...document.querySelectorAll(".g_day:checked")].map(e=>+e.value),t_from:$("#g_from").value,t_to:$("#g_to").value}),j=await r.json();if(!r.ok)return $("#g_msg").textContent=j.detail||"Fehler";if(!j.added)return $("#g_msg").textContent="Keine Termine erzeugt: Wochentage, Zeitraum und Uhrzeit prüfen (Zeiten in der Vergangenheit und schon vorhandene zählen nicht).";await campaignsView();$("#pbox").innerHTML=`<div class=hint>${j.added} Termine erzeugt.</div>`}
async function poolShow(id){
 const rows=await api(`/api/plugins/campaigns/pools/${id}/slots`);
 $("#pbox").innerHTML=`<div class=panel><table><tr><th>Termin</th><th>Status</th><th></th></tr>`+(rows.map(s=>`<tr><td>${esc(s.label)}</td><td>${s.target_id?badge("gebucht: "+(s.target_name||s.target_number||""),"ok"):badge("frei","neu")}</td><td style="text-align:right">${s.target_id?`<button class=s onclick="P_campaigns.slotRel(${s.id},${id})">Freigeben</button>`:`<button class=d onclick="P_campaigns.slotDel(${s.id},${id})">✕</button>`}</td></tr>`).join("")||`<tr><td colspan=3 class=empty>Keine kommenden Termine.</td></tr>`)+`</table></div>`;
 $("#pbox").scrollIntoView({behavior:"smooth"});
}
async function slotRel(s,p){await post(`/api/plugins/campaigns/slots/${s}/release`);poolShow(p)}
async function slotDel(s,p){const r=await fetch("/api/plugins/campaigns/slots/"+s,{method:"DELETE"});if(!r.ok)window.toast((await r.json()).detail||"Fehler");poolShow(p)}

Object.assign(window.P_campaigns,{campFilter,campSearch,campSort,campMore,campRelease,gcalSave,gcalTest,poolEdit,poolSaveCal,poolSync,campaignsView,campToggle,campEdit,campTab,campSave,campDelete,campOpen,acInput,acKey,acPick,acHover,campToggle2,campAddTargets,campRetry,campDelTarget,drawPools,poolAdd,poolDel,poolGen,poolDoGen,poolShow,slotRel,slotDel,openCall});
