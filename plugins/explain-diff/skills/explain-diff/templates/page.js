(function(){
'use strict';
var NS='http://www.w3.org/2000/svg';
var mobile=window.matchMedia('(max-width:899px)');
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function svgEl(name,attrs){var e=document.createElementNS(NS,name);for(var k in attrs){e.setAttribute(k,attrs[k])}return e}
function svgText(attrs,text){var t=svgEl('text',attrs);t.textContent=text;return t}
function safe(name,container,fn){try{fn()}catch(err){console.error('[explain-diff] '+name+' failed',err);if(container){container.appendChild(el('div','fail',name+' failed to load'))}}}
function readJSON(id){return JSON.parse(document.getElementById(id).textContent)}
function show(v){return typeof v==='string'?v:JSON.stringify(v,null,2)}

/* ---- Brief / Full ---- */
safe('Mode toggle',document.querySelector('.modes'),function(){
 var btns=document.querySelectorAll('.modes button'),bt=document.getElementById('bg-toggle'),bg=document.getElementById('bg-body');
 function setBg(o){bg.classList.toggle('open',o);bt.setAttribute('aria-expanded',o?'true':'false');bt.textContent=o?'Hide background':'Show background'}
 function set(mode){document.body.classList.toggle('brief',mode==='brief');
  btns.forEach(function(b){b.setAttribute('aria-pressed',b.dataset.mode===mode?'true':'false')});
  setBg(mode==='full')}
 btns.forEach(function(b){b.addEventListener('click',function(){set(b.dataset.mode)})});
 bt.addEventListener('click',function(){setBg(!bg.classList.contains('open'))});
});

/* ---- Code panel ---- */
var SNIP={};
var panel=document.getElementById('panel');
var pFile=panel.querySelector('.panel-file bdi'),pRange=panel.querySelector('.panel-range'),pCode=panel.querySelector('.panel-code'),pUnpin=document.getElementById('unpin');
var Panel={pinned:null};
function rangeText(s){if(s.newStart!=null){return s.newStart===s.newEnd?'line '+s.newStart:'lines '+s.newStart+'-'+s.newEnd}
 if(s.oldStart!=null){return s.oldStart===s.oldEnd?'old line '+s.oldStart:'old lines '+s.oldStart+'-'+s.oldEnd}return ''}
function emptyPanel(){pFile.textContent='No code selected';pRange.textContent='';pCode.textContent='';pCode.appendChild(el('div','panel-empty','Hover or tap a file reference to see its code here.'));pUnpin.hidden=true}
Panel.render=function(id){var s=SNIP[id];if(!s)return;
 pFile.textContent=s.file;pRange.textContent=rangeText(s);pCode.textContent='';
 var pre=el('pre'),code=el('code');
 s.lines.forEach(function(L){var row=el('span','l '+L.kind+(L.focus?' focus':''));
  row.appendChild(el('span','g',L.old==null?'':String(L.old)));row.appendChild(el('span','g',L.new==null?'':String(L.new)));
  row.appendChild(el('span','m',L.kind==='add'?'+':(L.kind==='del'?'-':' ')));row.appendChild(el('span','t',L.text));code.appendChild(row)});
 pre.appendChild(code);pCode.appendChild(pre);
 var f=code.querySelector('.focus');if(f){pCode.scrollTop=Math.max(0,f.offsetTop-pCode.clientHeight/2+f.offsetHeight/2)}
 pUnpin.hidden=!Panel.pinned};
Panel.restore=function(){if(Panel.pinned){Panel.render(Panel.pinned)}else{emptyPanel()}};
Panel.pin=function(id){if(!SNIP[id])return;Panel.pinned=id;Panel.render(id);if(mobile.matches){panel.classList.add('open')}};
Panel.unpin=function(){Panel.pinned=null;emptyPanel();panel.classList.remove('open')};
safe('Code snippets',null,function(){
 try{SNIP=readJSON('snippet-data')}catch(err){console.error('[explain-diff] snippet data',err);pCode.textContent='';pCode.appendChild(el('div','panel-fail','Code snippets failed to load'));return}
 emptyPanel();
});
pUnpin.addEventListener('click',Panel.unpin);
document.getElementById('sheet-close').addEventListener('click',function(){panel.classList.remove('open')});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!document.querySelector('dialog[open]')){Panel.unpin()}});
function refOf(t){return t&&t.closest?t.closest('[data-ref]'):null}
document.addEventListener('mouseover',function(e){var r=refOf(e.target);if(r&&!mobile.matches&&SNIP[r.dataset.ref]){Panel.render(r.dataset.ref)}});
document.addEventListener('mouseout',function(e){var r=refOf(e.target);if(r&&!mobile.matches){Panel.restore()}});
document.addEventListener('focusin',function(e){var r=refOf(e.target);if(r&&SNIP[r.dataset.ref]){Panel.render(r.dataset.ref)}});
document.addEventListener('focusout',function(e){var r=refOf(e.target);if(r){Panel.restore()}});
document.addEventListener('click',function(e){var r=refOf(e.target);if(r&&r.dataset.ref){Panel.pin(r.dataset.ref)}});
document.addEventListener('keydown',function(e){var r=refOf(e.target);if(r&&r.namespaceURI===NS&&(e.key==='Enter'||e.key===' ')){e.preventDefault();r.dispatchEvent(new MouseEvent('click',{bubbles:true}))}});

/* ---- Visualizations ---- */
var VIZ={};
try{VIZ=readJSON('viz-data')}catch(err){console.error('[explain-diff] viz data',err)}

function nodeW(n){if(n.w)return n.w;if(n.x!=null)return 116;return Math.min(230,Math.max(116,String(n.label).length*7+20))}
/* Positions from x/y, or row/col on a grid, or one vertical column in list order. */
function layout(nodes){
 var maxw=0,pos={},right=0,bottom=0;nodes.forEach(function(n){maxw=Math.max(maxw,nodeW(n))});
 nodes.forEach(function(n,i){var w=nodeW(n),x=n.x!=null?n.x:20+maxw/2+(n.col||0)*(maxw+40),y=n.y!=null?n.y:32+(n.row!=null?n.row:i)*80;
  pos[n.id]={x:x,y:y,w:w,h:34};right=Math.max(right,x+w/2);bottom=Math.max(bottom,y+17)});
 return {pos:pos,right:right,bottom:bottom}}
function clip(a,b){var dx=b.x-a.x,dy=b.y-a.y;if(!dx&&!dy)return [a.x,a.y];
 var sx=dx?(a.w/2+2)/Math.abs(dx):Infinity,sy=dy?(a.h/2+2)/Math.abs(dy):Infinity,s=Math.min(sx,sy);return [a.x+dx*s,a.y+dy*s]}
/* Draws nodes and edges into svg; returns element maps. */
function drawGraph(svg,spec,opts){
 var L=layout(spec.nodes),edgeEls={},nodeEls={},lanes=0,maxX=L.right;
 var defs=svgEl('defs',{}),mk=svgEl('marker',{id:spec.id+'-ar',viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'9',markerHeight:'9',markerUnits:'userSpaceOnUse',orient:'auto-start-reverse'});
 mk.appendChild(svgEl('path',{d:'M0 0L10 5L0 10z',fill:'#8c959f'}));defs.appendChild(mk);svg.appendChild(defs);
 (spec.edges||[]).forEach(function(ed){var a=L.pos[ed.from],b=L.pos[ed.to],pts;
  if(ed.points){pts=ed.points}
  else if(!a||!b){console.error('[explain-diff] edge with unknown node',ed);return}
  else if(b.y<a.y-1&&Math.abs(b.x-a.x)<a.w/2){lanes++;var x0=L.right+14*lanes;maxX=Math.max(maxX,x0+8);pts=[[a.x+a.w/2,a.y],[x0,a.y],[x0,b.y],[b.x+b.w/2+2,b.y]]}
  else{pts=[clip(a,b),clip(b,a)]}
  var id=ed.id||(ed.from+'-'+ed.to),p=svgEl('path',{'class':'edge',d:'M'+pts.map(function(q){return q[0]+' '+q[1]}).join(' L'),'marker-end':'url(#'+spec.id+'-ar)'});
  if(ed.dashed){p.setAttribute('stroke-dasharray','5 4')}
  svg.appendChild(p);edgeEls[id]=p;
  if(ed.label){var m=Math.floor((pts.length-1)/2),q0=pts[m],q1=pts[m+1],lx=ed.lx!=null?ed.lx:(q0[0]+q1[0])/2+6,ly=ed.ly!=null?ed.ly:(q0[1]+q1[1])/2-4;
   svg.appendChild(svgText({'class':'elabel',x:lx,y:ly},ed.label))}});
 spec.nodes.forEach(function(n){var P=L.pos[n.id],g=svgEl('g',{'class':'node'+(n.changed?' changed':'')});
  if(n.ref){g.setAttribute('data-ref',n.ref)}
  if(n.ref||opts.focusable){g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('aria-label',n.label+(n.ref?', show code':''))}
  g.appendChild(svgEl('rect',{x:P.x-P.w/2,y:P.y-17,width:P.w,height:34,rx:8}));g.appendChild(svgText({x:P.x,y:P.y},n.label));svg.appendChild(g);nodeEls[n.id]=g});
 svg.setAttribute('viewBox',spec.viewBox||('0 0 '+Math.ceil(maxX+20)+' '+Math.ceil(L.bottom+20)));
 return {edges:edgeEls,nodes:nodeEls}}
function controls(){var ctl=el('div','ctl'),b={prev:el('button',null,'Prev'),next:el('button',null,'Next'),reset:el('button',null,'Reset'),play:el('button',null,'Play')},count=el('span','count');
 [b.prev,b.next,b.reset,b.play].forEach(function(x){x.type='button';ctl.appendChild(x)});ctl.appendChild(count);b.ctl=ctl;b.count=count;return b}
function altList(host,title,items){var d=el('details','alt');d.appendChild(el('summary',null,title));var ol=el('ol');items.forEach(function(t){ol.appendChild(el('li',null,t))});d.appendChild(ol);host.appendChild(d)}
function stateTable(table,state){table.textContent='';for(var k in (state||{})){var tr=el('tr');tr.appendChild(el('th',null,k));tr.appendChild(el('td',null,show(state[k])));table.appendChild(tr)}}
function player(b,count,getLen,getIdx,setIdx,draw){var timer=null;
 function stop(){if(timer){clearInterval(timer);timer=null;b.play.textContent='Play'}}
 b.prev.addEventListener('click',function(){stop();if(getIdx()>0){setIdx(getIdx()-1);draw(true)}});
 b.next.addEventListener('click',function(){stop();if(getIdx()<getLen()-1){setIdx(getIdx()+1);draw(true)}});
 b.reset.addEventListener('click',function(){stop();setIdx(0);draw(true)});
 b.play.addEventListener('click',function(){if(timer){stop();return}if(getIdx()>=getLen()-1){setIdx(0)}b.play.textContent='Pause';draw(true);
  timer=setInterval(function(){if(getIdx()<getLen()-1){setIdx(getIdx()+1);draw(true)}else{stop()}},1400)});
 return stop}

function Stepper(host,spec){
 var cases=spec.cases||[{name:'',steps:spec.steps}],cur={ci:0,si:0};
 var svg=svgEl('svg',{'class':'flow',role:'img','aria-label':spec.alt||spec.title||'Flow diagram'}),G=drawGraph(svg,spec,{});
 var seg=el('div','seg');seg.setAttribute('role','group');seg.setAttribute('aria-label','Input case');
 var ba=el('div','ba'),beforeBox=el('div','before'),afterBox=el('div','after');ba.appendChild(beforeBox);ba.appendChild(afterBox);
 var b=controls(),capEl=el('div','stepcap'),table=el('table','state');capEl.setAttribute('aria-live','polite');
 if(cases.length>1){cases.forEach(function(c,i){var x=el('button',null,c.name);x.type='button';x.addEventListener('click',function(){stop();cur.ci=i;cur.si=0;draw(true)});seg.appendChild(x)});host.appendChild(seg)}
 var hasBA=cases.some(function(c){return c.before||c.after});if(hasBA){host.appendChild(ba)}
 host.appendChild(svg);host.appendChild(b.ctl);host.appendChild(capEl);host.appendChild(table);
 function setBox(box,label,txt){box.textContent='';box.appendChild(el('b',null,label));box.appendChild(document.createTextNode(txt||''))}
 function draw(pin){var c=cases[cur.ci],s=c.steps[cur.si];
  Array.prototype.forEach.call(seg.children,function(x,i){x.setAttribute('aria-pressed',i===cur.ci?'true':'false')});
  if(hasBA){setBox(beforeBox,'Before this change: ',c.before);setBox(afterBox,'After this change: ',c.after)}
  for(var k in G.nodes){G.nodes[k].classList.toggle('on',(s.nodes||[]).indexOf(k)>=0)}
  for(var j in G.edges){G.edges[j].classList.toggle('on',(s.edges||[]).indexOf(j)>=0)}
  capEl.textContent=s.caption;b.count.textContent='Step '+(cur.si+1)+' of '+c.steps.length;stateTable(table,s.state);
  b.prev.disabled=cur.si===0;b.next.disabled=cur.si===c.steps.length-1;
  if(pin&&s.ref&&!mobile.matches){Panel.pin(s.ref)}}
 var stop=player(b,b.count,function(){return cases[cur.ci].steps.length},function(){return cur.si},function(i){cur.si=i},draw);
 draw(false);
 cases.forEach(function(c){altList(host,'Text version'+(c.name?': '+c.name:''),c.steps.map(function(s){return s.caption}))})}

function Graph(host,spec){
 var svg=svgEl('svg',{'class':'flow',role:'img','aria-label':spec.alt||spec.title||'Dependency graph'}),G=drawGraph(svg,spec,{focusable:true});
 var detail=el('div','graph-detail');detail.setAttribute('aria-live','polite');host.appendChild(svg);host.appendChild(detail);
 var byId={};spec.nodes.forEach(function(n){byId[n.id]=n});var pinned=null;
 function rel(id){var out=[],inn=[];(spec.edges||[]).forEach(function(e){if(e.from===id)out.push(e.to);if(e.to===id)inn.push(e.from)});return {out:out,inn:inn}}
 function text(id){var n=byId[id],r=rel(id),lab=function(x){return byId[x]?byId[x].label:x};
  return n.label+(n.detail?': '+n.detail:'')+(r.out.length?'. Uses '+r.out.map(lab).join(', '):'')+(r.inn.length?'. Used by '+r.inn.map(lab).join(', '):'')+'.'}
 function mark(id){var r=id?rel(id):{out:[],inn:[]},near=r.out.concat(r.inn);
  for(var k in G.nodes){G.nodes[k].classList.toggle('pinned',k===id);G.nodes[k].classList.toggle('near',near.indexOf(k)>=0)}
  (spec.edges||[]).forEach(function(e){var p=G.edges[e.id||(e.from+'-'+e.to)];if(p)p.classList.toggle('on',!!id&&(e.from===id||e.to===id))});
  detail.textContent=id?text(id):(spec.hint||'Select a node to highlight what it uses and what uses it.')}
 Object.keys(G.nodes).forEach(function(id){var g=G.nodes[id];
  g.addEventListener('click',function(){pinned=pinned===id?null:id;mark(pinned)});
  g.addEventListener('mouseenter',function(){mark(id)});g.addEventListener('mouseleave',function(){mark(pinned)});
  g.addEventListener('focus',function(){mark(id)});g.addEventListener('blur',function(){mark(pinned)});
  if(!g.dataset.ref){g.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();pinned=pinned===id?null:id;mark(pinned)}})}});
 mark(null);altList(host,'Text version',spec.nodes.map(function(n){return text(n.id)}))}

function Sequence(host,spec){
 var A=spec.actors,M=spec.messages,gap=Math.max(130,Math.max.apply(null,A.map(function(a){return String(a.label).length*7.4+30}))),xs={},cur=0;
 A.forEach(function(a,i){xs[a.id]=20+gap/2+i*gap});
 var H=80+M.length*42,W=20+gap*A.length,svg=svgEl('svg',{'class':'flow',viewBox:'0 0 '+W+' '+(H+10),role:'img','aria-label':spec.alt||spec.title||'Sequence diagram'});
 var defs=svgEl('defs',{}),mk=svgEl('marker',{id:spec.id+'-ar',viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'9',markerHeight:'9',markerUnits:'userSpaceOnUse',orient:'auto-start-reverse'});
 mk.appendChild(svgEl('path',{d:'M0 0L10 5L0 10z',fill:'#8c959f'}));defs.appendChild(mk);svg.appendChild(defs);
 A.forEach(function(a){var x=xs[a.id],g=svgEl('g',{'class':'actor'}),w=gap-24;g.appendChild(svgEl('rect',{x:x-w/2,y:8,width:w,height:30,rx:6}));g.appendChild(svgText({x:x,y:23},a.label));svg.appendChild(g);
  svg.appendChild(svgEl('line',{'class':'life',x1:x,y1:38,x2:x,y2:H}))});
 var rows=M.map(function(m,i){var y=70+i*42,x1=xs[m.from],x2=xs[m.to],g=svgEl('g',{'class':'msgrow'}),p;
  if(m.ref){g.setAttribute('data-ref',m.ref);g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('aria-label',(i+1)+'. '+m.label+', show code')}
  if(x1===x2){p=svgEl('path',{'class':'msg',d:'M'+x1+' '+(y-8)+' h28 v16 h-26','marker-end':'url(#'+spec.id+'-ar)'})}
  else{p=svgEl('path',{'class':'msg',d:'M'+x1+' '+y+' L'+(x2+(x2>x1?-3:3))+' '+y,'marker-end':'url(#'+spec.id+'-ar)'})}
  if(m.dashed){p.setAttribute('stroke-dasharray','5 4')}
  var t=svgText({'class':'mlabel',x:x1===x2?x1+34:(x1+x2)/2,y:y-6},(i+1)+'. '+m.label);if(x1===x2){t.setAttribute('text-anchor','start')}
  g.appendChild(p);g.appendChild(t);svg.appendChild(g);return {g:g,p:p,t:t}});
 var b=controls(),capEl=el('div','stepcap');capEl.setAttribute('aria-live','polite');
 host.appendChild(svg);host.appendChild(b.ctl);host.appendChild(capEl);
 function draw(pin){rows.forEach(function(r,i){r.p.classList.toggle('on',i===cur);r.p.classList.toggle('past',i<cur);r.t.classList.toggle('on',i===cur);r.g.style.opacity=i>cur?'.35':'1'});
  var m=M[cur];capEl.textContent=(cur+1)+'. '+m.label+(m.note?': '+m.note:'');b.count.textContent='Message '+(cur+1)+' of '+M.length;
  b.prev.disabled=cur===0;b.next.disabled=cur===M.length-1;if(pin&&m.ref&&!mobile.matches){Panel.pin(m.ref)}}
 player(b,b.count,function(){return M.length},function(){return cur},function(i){cur=i},draw);
 draw(false);altList(host,'Text version',M.map(function(m){var f=A.filter(function(a){return a.id===m.from})[0],t=A.filter(function(a){return a.id===m.to})[0];
  return (f?f.label:m.from)+' to '+(t?t.label:m.to)+': '+m.label+(m.note?' ('+m.note+')':'')}))}

function Compare(host,spec){
 var marks=Object.create(null);(spec.marks||[]).forEach(function(m){marks[m.path]=m});
 var SYM={add:['+','added'],del:['-','removed'],chg:['~','changed']};
 function tree(v,path,side){var ul=el('ul');
  var keys=Array.isArray(v)?v.map(function(_,i){return i}):Object.keys(v);
  keys.forEach(function(k){var p=path?path+'.'+k:String(k),val=v[k],li=el('li'),m=marks[p];
   var visible=m&&((side==='before'&&m.kind!=='add')||(side==='after'&&m.kind!=='del'));
   li.appendChild(el('span','sym',visible?SYM[m.kind][0]:' '));
   if(val!==null&&typeof val==='object'){li.appendChild(document.createTextNode(k+':'))}
   else{li.appendChild(document.createTextNode(k+': '+JSON.stringify(val)))}
   if(visible){li.classList.add('mk-'+m.kind);li.appendChild(el('span','note',SYM[m.kind][1]+(m.note?': '+m.note:'')))}
   if(val!==null&&typeof val==='object'){li.appendChild(tree(val,p,side))}
   ul.appendChild(li)});return ul}
 var seg=el('div','seg');seg.setAttribute('role','group');seg.setAttribute('aria-label','Compare view');
 var wrap=el('div','cmp'),bs=el('div','side'),as=el('div','side');
 bs.appendChild(el('h4',null,spec.beforeLabel||'Before'));bs.appendChild(tree(spec.before,'','before'));
 as.appendChild(el('h4',null,spec.afterLabel||'After'));as.appendChild(tree(spec.after,'','after'));
 wrap.appendChild(bs);wrap.appendChild(as);
 [['both','Side by side'],['before','Before only'],['after','After only']].forEach(function(o,i){var x=el('button',null,o[1]);x.type='button';x.setAttribute('aria-pressed',i===0?'true':'false');
  x.addEventListener('click',function(){Array.prototype.forEach.call(seg.children,function(y){y.setAttribute('aria-pressed',y===x?'true':'false')});
   bs.hidden=o[0]==='after';as.hidden=o[0]==='before';wrap.classList.toggle('one',o[0]!=='both')});seg.appendChild(x)});
 host.appendChild(seg);host.appendChild(wrap);
 altList(host,'Text version',(spec.marks||[]).map(function(m){return SYM[m.kind][1]+': '+m.path+(m.note?' ('+m.note+')':'')}))}

function Scrubber(host,spec){
 var S=spec.stages,box=el('div','scrub'),lab=el('label'),id=spec.id+'-range',r=el('input');
 r.type='range';r.min='0';r.max=String(S.length-1);r.value='0';r.step='1';r.id=id;lab.htmlFor=id;lab.textContent='Stage';
 var stage=el('div','stage'),pre=el('pre'),note=el('div','cap');stage.setAttribute('aria-live','polite');
 box.appendChild(lab);box.appendChild(r);box.appendChild(stage);box.appendChild(pre);box.appendChild(note);host.appendChild(box);
 function draw(pin){var i=+r.value,s=S[i];stage.textContent='Stage '+(i+1)+' of '+S.length+': '+s.name;pre.textContent=show(s.value);note.textContent=s.note||'';
  r.setAttribute('aria-valuetext',s.name);if(pin&&s.ref&&!mobile.matches){Panel.pin(s.ref)}}
 r.addEventListener('input',function(){draw(true)});draw(false);
 altList(host,'Text version',S.map(function(s){return s.name+': '+show(s.value)+(s.note?' ('+s.note+')':'')}))}

var RENDER={stepper:Stepper,graph:Graph,sequence:Sequence,compare:Compare,scrubber:Scrubber};
document.querySelectorAll('[data-viz]').forEach(function(host){safe('Visualization',host,function(){
 var spec=VIZ[host.dataset.spec],fn=RENDER[host.dataset.viz];if(!spec||!fn)throw new Error('no spec or renderer for '+host.dataset.spec);fn(host,spec)})});

document.querySelectorAll('.expand').forEach(function(btn){
 var dlg=document.getElementById('vizdlg'),body=document.getElementById('vizdlg-body');
 btn.addEventListener('click',function(){if(dlg.open)return;var v=btn.closest('.viz'),home=document.createElement('span');v.parentNode.insertBefore(home,v);body.appendChild(v);
  btn.setAttribute('aria-expanded','true');
  dlg.addEventListener('close',function back(){home.parentNode.insertBefore(v,home);home.remove();btn.setAttribute('aria-expanded','false');dlg.removeEventListener('close',back)});dlg.showModal()});
});
document.getElementById('vizdlg-close').addEventListener('click',function(){document.getElementById('vizdlg').close()});

/* ---- Gate ---- */
var Gate={quiz:function(){return {done:0,score:0,wrong:0,of:5,attempt:1}},gov:function(){return {present:false,controls:[],marks:Object.create(null)}},retake:function(){}};
Gate.update=function(){safe('Gate',document.getElementById('gate-status'),function(){
 var q=Gate.quiz(),g=Gate.gov(),status=document.getElementById('gate-status'),label=document.getElementById('gate-label'),list=document.getElementById('gate-reasons');
 var pill=document.getElementById('gate-pill'),retake=document.getElementById('gate-retake'),rem=document.getElementById('remediate'),pass=document.getElementById('gate-pass'),att=document.getElementById('gate-attestation');
 var marked=g.controls.filter(function(c){return g.marks[c.id]}),notmet=g.controls.filter(function(c){return g.marks[c.id]==='notmet'});
 var reasons=[],state;
 if(q.wrong>0){reasons.push('Quiz failed: '+q.wrong+' wrong answer'+(q.wrong>1?'s':'')+' in attempt '+q.attempt+'. Retake the quiz.')}
 else if(q.done<q.of){reasons.push('Quiz: '+q.done+' of '+q.of+' answered')}
 if(notmet.length){reasons.push('Governance failed: '+notmet.map(function(c){return c.id}).join(', ')+' marked Not met.')}
 else if(g.present&&marked.length<g.controls.length){reasons.push('Governance: '+marked.length+' of '+g.controls.length+' controls marked')}
 if(!g.present){reasons.push('Governance: no governance documents in docs/governance/, so the gate needs the quiz only.')}
 state=(q.wrong>0||notmet.length)?'failed':((q.done<q.of||(g.present&&marked.length<g.controls.length))?'blocked':'passed');
 status.dataset.state=state;pill.dataset.state=state;
 label.textContent=state==='passed'?'Gate passed: quiz '+q.score+' of '+q.of+', governance marked.':(state==='failed'?'Failed':'Blocked');
 pill.textContent='Gate: '+state;
 list.textContent='';reasons.forEach(function(r){if(state!=='passed'||/^Governance: no/.test(r))list.appendChild(el('li',null,r))});
 retake.hidden=!(q.wrong>0);rem.hidden=!notmet.length;
 pass.hidden=state!=='passed';
 if(state==='passed'){var meta={};try{meta=readJSON('gate-data')}catch(e){console.error('[explain-diff] gate data',e)}
  att.value=JSON.stringify({source:'explain-diff',change:meta.change||null,commit:meta.commit||null,base:meta.base||null,baseCommit:meta.baseCommit||null,mergeBase:meta.mergeBase||null,uncommitted:!!meta.uncommitted,
   quiz:{correct:q.score,of:q.of,attempt:q.attempt},
   governance:g.controls.map(function(c){return {id:c.id,mark:g.marks[c.id]}}),governanceDocuments:(g.documents||[]).map(function(d){return d.path}),provenance:meta.provenance||null,at:new Date().toISOString()},null,2)}
 else{att.value=''}
})};

/* ---- Governance ---- */
safe('Governance',document.getElementById('gov-host'),function(){
 var G=readJSON('governance-data'),marks=Object.create(null),summary=document.getElementById('gov-summary');
 var cards=document.querySelectorAll('.gov-control');
 function draw(){cards.forEach(function(card){var m=marks[card.dataset.control]||null;
   card.querySelectorAll('[data-result]').forEach(function(b){b.setAttribute('aria-pressed',b.dataset.result===m?'true':'false')})});
  var n=(G.controls||[]).filter(function(c){return marks[c.id]}).length;
  if(summary){summary.textContent=n+' of '+(G.controls||[]).length+' controls marked.'}
  Gate.update()}
 cards.forEach(function(card){card.querySelectorAll('[data-result]').forEach(function(b){
  b.addEventListener('click',function(){marks[card.dataset.control]=b.dataset.result;draw()})})});
 document.getElementById('gov-reset').addEventListener('click',function(){marks=Object.create(null);draw()});
 Gate.gov=function(){return {present:!!G.present,controls:G.controls||[],documents:G.documents||[],marks:marks}};
 var dlg=document.getElementById('remdlg'),lst=document.getElementById('remdlg-list');
 document.getElementById('remediate').addEventListener('click',function(){lst.textContent='';
  (G.controls||[]).filter(function(c){return marks[c.id]==='notmet'}).forEach(function(c){var li=el('li');li.appendChild(el('b',null,c.id+' '+(c.title||'')+'. '));
   li.appendChild(document.createTextNode('Requirement: '+c.requirement+' Failure means: '+(c.failure||'')));lst.appendChild(li)});dlg.showModal()});
 document.getElementById('remdlg-close').addEventListener('click',function(){dlg.close()});
});

/* ---- Quiz ---- */
safe('Quiz',document.getElementById('quiz-host'),function(){
 var Q=readJSON('quiz-data'),host=document.getElementById('quiz-host'),openBtn=document.getElementById('quiz-open');
 var attempt=1,answers=[],summary=el('p',null,'');summary.setAttribute('aria-live','polite');
 function shuffled(n){var a=[];for(var i=0;i<n;i++)a.push(i);for(var j=n-1;j>0;j--){var k=Math.floor(Math.random()*(j+1)),t=a[j];a[j]=a[k];a[k]=t}return a}
 function stats(){var done=0,score=0;answers.forEach(function(a){if(a!==null){done++;if(a)score++}});return {done:done,score:score,wrong:done-score,of:Q.length,attempt:attempt}}
 function tally(){var t=stats();summary.textContent='Attempt '+attempt+': '+t.done+' of '+t.of+' answered, '+t.score+' correct.';Gate.update()}
 function render(shuffle){host.textContent='';answers=Q.map(function(){return null});
  Q.forEach(function(q,qi){var card=el('div','q');card.appendChild(el('p',null,(qi+1)+'. '+q.prompt));
   var opts=el('div','opts'),fb=el('div','fb');fb.hidden=true;var btns={};
   var order=shuffle?shuffled(q.options.length):q.options.map(function(_,i){return i});
   order.forEach(function(oi){var b=el('button','opt',q.options[oi]);b.type='button';btns[oi]=b;
    b.addEventListener('click',function(){if(answers[qi]!==null)return;
     Object.keys(btns).forEach(function(k){btns[k].disabled=true});
     var ok=oi===q.correct;answers[qi]=ok;b.classList.add('sel',ok?'right':'wrong');if(!ok){btns[q.correct].classList.add('right')}
     fb.hidden=false;fb.textContent='';fb.appendChild(el('strong',null,ok?'✓ Correct. ':'✗ Not quite. '));fb.appendChild(document.createTextNode(q.explain));
     if(q.ref&&SNIP[q.ref]){fb.appendChild(document.createTextNode(' '));var r=el('button','ref',q.refLabel||SNIP[q.ref].label||q.ref);r.type='button';r.dataset.ref=q.ref;fb.appendChild(r)}
     tally()});
    opts.appendChild(b)});
   card.appendChild(opts);card.appendChild(fb);host.appendChild(card)});
  host.appendChild(summary);tally()}
 Gate.quiz=stats;
 function retake(){attempt++;render(true);host.hidden=false;openBtn.setAttribute('aria-expanded','true');openBtn.textContent='Hide quiz'}
 Gate.retake=retake;
 document.getElementById('quiz-reset').addEventListener('click',retake);
 document.getElementById('gate-retake').addEventListener('click',function(){retake();document.getElementById('quiz').scrollIntoView()});
 openBtn.addEventListener('click',function(){var o=host.hidden;host.hidden=!o;openBtn.setAttribute('aria-expanded',o?'true':'false');openBtn.textContent=o?'Hide quiz':'Test yourself'});
 render(false);
});

safe('Gate copy',null,function(){var b=document.getElementById('gate-copy'),t=document.getElementById('gate-attestation');
 b.addEventListener('click',function(){t.select();if(navigator.clipboard){navigator.clipboard.writeText(t.value).then(function(){b.textContent='Copied'},function(){b.textContent='Select and copy'})}else{b.textContent='Select and copy'}})});
Gate.update();
})();
