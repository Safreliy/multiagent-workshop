const slides=[...document.querySelectorAll('.slide')];
function hashIndex(){return location.hash==='#demo'?slides.findIndex(s=>s.classList.contains('demo-slide')):Math.max(0,Math.min(slides.length-1,(Number(location.hash.slice(1))||1)-1));}
let current=hashIndex();
const stage=document.querySelector('.stage');
function resize(){const scale=Math.min(innerWidth/1600,innerHeight/900);stage.style.width=`${innerWidth/scale}px`;stage.style.height=`${innerHeight/scale}px`;stage.style.transform=`scale(${scale})`;stage.style.left='0px';stage.style.top='0px';}
function go(n){current=Math.max(0,Math.min(slides.length-1,n));slides.forEach((s,i)=>s.classList.toggle('active',i===current));history.replaceState(null,'',`#${current+1}`);document.title=`${current+1}. ${slides[current].dataset.title} — Масштабирование интеллекта`;}
slides.forEach((s,i)=>{s.insertAdjacentHTML('beforeend',`<div class="slide-no"><span>МАСШТАБИРОВАНИЕ ИНТЕЛЛЕКТА</span><span>${String(i+1).padStart(2,'0')} / ${slides.length}</span></div>`)});

function toggle(id){const el=document.getElementById(id);const open=!el.classList.contains('open');document.querySelectorAll('.overlay').forEach(e=>e.classList.remove('open'));el.classList.toggle('open',open);if(open)el.querySelector('button').focus();}
document.querySelectorAll('.close').forEach(b=>b.onclick=()=>b.closest('.overlay').classList.remove('open'));
const fullscreen=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch{}};
document.querySelector('.overview-grid').innerHTML=slides.map((s,i)=>`<button data-slide="${i}"><span>${String(i+1).padStart(2,'0')}</span>${s.dataset.title}</button>`).join('');document.querySelectorAll('[data-slide]').forEach(b=>b.onclick=()=>{go(Number(b.dataset.slide));document.querySelector('#overview').classList.remove('open')});
document.addEventListener('keydown',e=>{if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;if(e.key==='Escape'){document.querySelectorAll('.overlay').forEach(x=>x.classList.remove('open'));return}if(e.key.toLowerCase()==='o'){toggle('overview');return}if(e.key.toLowerCase()==='s'){toggle('sources');return}if(document.querySelector('.overlay.open'))return;if(['ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();go(current+1)}if(['ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();go(current-1)}if(e.key==='Home')go(0);if(e.key==='End')go(slides.length-1);if(e.key.toLowerCase()==='f')fullscreen()});
window.addEventListener('resize',resize);window.addEventListener('hashchange',()=>go(hashIndex()));
let route='orch',routeStep=0;
const routes={orch:['o','p','o','s','o','q','o','a','o'],swarm:['p','q','a']};
const routeNames={o:'Orchestrator',p:'Plan Analyst',s:'Stats Analyst',q:'Query Rewriter',a:'Auditor'};
function updateRoute(){document.querySelectorAll('.route-node').forEach(n=>n.classList.toggle('on',n.id===`route-${routes[route][routeStep]}`));document.querySelector('#route-state').textContent=`${routeStep+1} / ${routes[route].length} · Управление: ${routeNames[routes[route][routeStep]]}`;document.querySelector('#route-path').textContent=route==='orch'?'Специалист возвращает результат руководителю':'Handoff передаёт управление следующему специалисту';document.querySelector('#route-o').style.opacity=route==='orch'?'1':'0';document.querySelector('#orch-edges').style.opacity=route==='orch'?'1':'0';document.querySelector('#swarm-edges').style.opacity=route==='swarm'?'1':'0';document.querySelector('#route-s').style.opacity=route==='orch'?'1':'.25';}
document.querySelectorAll('[data-route]').forEach(b=>b.onclick=()=>{route=b.dataset.route;routeStep=0;document.querySelectorAll('[data-route]').forEach(x=>x.classList.toggle('selected',x===b));updateRoute()});document.querySelector('#route-step').onclick=()=>{routeStep=(routeStep+1)%routes[route].length;updateRoute()};
function cost(){const p=Number(document.querySelector('#parallel').value)/100;const c=Number(document.querySelector('#coord').value);const workers=4;const work=(1-p)*100+p*100/workers;const total=work+c;document.querySelector('#parallel-value').textContent=`${Math.round(p*100)}%`;document.querySelector('#coord-value').textContent=c;document.querySelector('#cost-work').style.width=`${work/1.6}%`;document.querySelector('#cost-coord').style.width=`${c/1.6}%`;document.querySelector('#cost-total').textContent=total.toFixed(0);document.querySelector('#cost-verdict').textContent=total<100?`В этой модели: ${(100/total).toFixed(2)}× быстрее`:`В этой модели: ${(total/100).toFixed(2)}× медленнее`;}
document.querySelectorAll('.cost-input').forEach(x=>x.addEventListener('input',cost));
const demoFrame=document.querySelector('#demo-frame');
const demoContext=document.querySelector('#demo-context');
let demoRequest=0;
async function selectDemo(kind){
 const request=++demoRequest,live=kind==='live',fallback=document.querySelector('#demo-unavailable');
 document.querySelectorAll('[data-demo]').forEach(x=>x.classList.toggle('selected',x.dataset.demo===kind));
 fallback.hidden=true;
 if(!live){demoFrame.src='demo.html?embed=1&v=publish-12';demoContext.textContent='Записи реальных запусков: Single, Orchestrator, Handoff и Board Swarm.';return;}
 demoFrame.src='about:blank';demoContext.textContent='Свой SQL и пожелания в чате · DeepSeek или OpenRouter';
 const timeout=new AbortController(),timer=setTimeout(()=>timeout.abort(),2500);
 try{await fetch('http://127.0.0.1:8769/api/config',{mode:'no-cors',cache:'no-store',signal:timeout.signal});if(request===demoRequest)demoFrame.src='http://127.0.0.1:8769/live.html?embed=1';}
 catch{if(request===demoRequest)fallback.hidden=false;}
 finally{clearTimeout(timer);}
}
document.querySelectorAll('[data-demo]').forEach(b=>b.addEventListener('click',()=>selectDemo(b.dataset.demo)));
document.querySelector('#retry-live').addEventListener('click',()=>selectDemo('live'));
document.querySelector('#show-sql').addEventListener('click',()=>toggle('sql-view'));
window.addEventListener('message',e=>{if(e.source!==demoFrame.contentWindow)return;if(e.data?.type==='deck-nav'&&[-1,1].includes(e.data.direction))go(current+e.data.direction);});
resize();go(current);updateRoute();cost();
