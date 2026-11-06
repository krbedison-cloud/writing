(function(root){
 'use strict';
 const MINUTE=60000,DEFAULT_IDLE=5*MINUTE;
 function duration(ms){const s=Math.floor(ms/1000);return [Math.floor(s/3600),Math.floor(s/60)%60,s%60].map(n=>String(n).padStart(2,'0')).join(':');}
 class Clock{
  constructor(elapsed=0,now=0,idleMs=DEFAULT_IDLE){
   this.elapsed=Number.isFinite(elapsed)&&elapsed>=0?elapsed:0;
   this.idleMs=idleMs;this.since=now;this.lastActivity=now;
  }
  get paused(){return this.since===null;}
  value(now){return this.elapsed+(this.paused?0:Math.max(0,now-this.since));}
  pause(now){if(!this.paused){this.elapsed=this.value(now);this.since=null;}}
  tick(now){if(!this.paused&&now>=this.lastActivity+this.idleMs)this.pause(this.lastActivity+this.idleMs);return this.value(now);}
  activity(now){this.tick(now);if(this.paused)this.since=now;this.lastActivity=now;}
  reset(now){this.elapsed=0;this.since=now;this.lastActivity=now;}
 }
 class Timer{
  constructor({questionId,element}){
   this.element=element;this.key='practice-time-'+questionId;this.now=()=>performance.now();this.lastSave=this.now();
   let elapsed=0,minutes=5;
   try{elapsed=Number(localStorage.getItem(this.key)||0);const saved=Number(localStorage.getItem('practice-idle-minutes'));if([3,5,10,15].includes(saved))minutes=saved;}catch{}
   this.clock=new Clock(elapsed,this.now(),minutes*MINUTE);
   this.output=element.querySelector('output');this.status=element.querySelector('[data-timer-status]');
   this.select=element.querySelector('select');this.select.value=String(minutes);
   this.overlay=document.createElement('div');this.overlay.className='practice-pause';this.overlay.hidden=true;
   this.overlay.innerHTML='<section role="status" aria-live="polite"><p class="eyebrow">休息片刻，继续作答</p><h2>已暂停作答</h2><p>秒表已暂停，移动鼠标、按键或触摸屏幕即可继续。</p><strong data-paused-time></strong></section>';
   document.body.append(this.overlay);
   this.activity=()=>{if(document.hidden)return;this.clock.activity(this.now());this.render();};
   this.events=['pointermove','pointerdown','keydown','input','wheel','touchstart','touchmove'];
   this.events.forEach(type=>document.addEventListener(type,this.activity,{capture:true,passive:true}));
   this.visibility=()=>{if(document.hidden){this.clock.tick(this.now());this.clock.pause(this.now());this.save();this.render();}};
   this.pagehide=()=>{this.clock.tick(this.now());this.clock.pause(this.now());this.save();};
   document.addEventListener('visibilitychange',this.visibility);window.addEventListener('pagehide',this.pagehide);
   this.select.addEventListener('change',()=>{this.clock.idleMs=Number(this.select.value)*MINUTE;this.clock.activity(this.now());try{localStorage.setItem('practice-idle-minutes',this.select.value);}catch{}this.render();});
   element.querySelector('[data-timer-reset]').addEventListener('click',()=>{this.clock.reset(this.now());this.save();this.render();});
   if(document.hidden)this.clock.pause(this.now());
   this.render();this.interval=setInterval(()=>{this.clock.tick(this.now());this.render();if(this.now()-this.lastSave>=5000)this.save();},250);
  }
  save(){try{localStorage.setItem(this.key,String(this.clock.tick(this.now())));}catch{}this.lastSave=this.now();}
  render(){
   const text=duration(this.clock.tick(this.now()));if(this.output.textContent!==text)this.output.textContent=text;
   const paused=this.clock.paused;
   const status=paused?'已暂停':'正在计时';if(this.status.textContent!==status)this.status.textContent=status;
   if(this.overlay.hidden===paused){this.overlay.hidden=!paused;if(paused){this.overlay.querySelector('[data-paused-time]').textContent='本题累计用时 '+text;this.save();}}
  }
  destroy(){clearInterval(this.interval);this.pagehide();this.events.forEach(type=>document.removeEventListener(type,this.activity,true));document.removeEventListener('visibilitychange',this.visibility);window.removeEventListener('pagehide',this.pagehide);this.overlay.remove();}
 }
 root.PracticeTimer={Clock,Timer,duration,DEFAULT_IDLE};
})(globalThis);
