(function(root){
 'use strict';
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 class PhoneCaptureInbox{
  constructor({questionId,signature,api,apply,canAutoApply,toast}){
   this.questionId=questionId;this.signature=signature;this.api=api;this.apply=apply;this.canAutoApply=canAutoApply;this.toast=toast;
   this.host=document.querySelector('#phone-capture-inbox');this.records=[];this.dead=false;this.busy=false;this.processing=false;this.markup='';this.seen=new Set();
   try{this.seen=new Set(JSON.parse(localStorage.getItem('phone-applied-captures')||'[]'));}catch{}
   this.abort=new AbortController();
   this.host.addEventListener('click',async event=>{
    const button=event.target.closest('[data-phone-operation]');if(!button||this.processing)return;
    const record=this.records.find(item=>item.id===button.dataset.capture);if(!record)return;
    try{await this.process(record,button.dataset.phoneOperation);}catch(error){this.toast(error.message);}
   },{signal:this.abort.signal});
   this.refresh();
  }
  destroy(){this.dead=true;this.abort.abort();}
  async acknowledge(id,status){return this.api('/phone/captures/'+id+'/ack',{method:'POST',body:JSON.stringify({status})});}
  remember(id){this.seen.add(id);try{localStorage.setItem('phone-applied-captures',JSON.stringify([...this.seen].slice(-250)));}catch{}}
  async process(record,operation,auto=false){
   if(this.dead||this.processing)return;
   this.processing=true;
   try{
    if(operation!=='dismissed'){
     this.apply(record,operation,auto);this.remember(record.id);
    }
    await this.acknowledge(record.id,operation==='dismissed'?'dismissed':'accepted');
    this.records=this.records.filter(item=>item.id!==record.id);this.render();
    if(!auto)this.toast(operation==='dismissed'?'本次手机录入已忽略，记录仍保留':'手机文字已录入草稿，批改由你手动提交');
   }finally{this.processing=false;}
  }
  async refresh(){
   if(this.dead||this.busy||this.processing)return;this.busy=true;
   try{
    const data=await this.api('/phone/captures?question_id='+encodeURIComponent(this.questionId));
    if(this.dead)return;
    this.records=data.items;
    for(const record of [...this.records]){
     if(this.dead)return;
     if(this.seen.has(record.id)){
      await this.acknowledge(record.id,'accepted');this.records=this.records.filter(item=>item.id!==record.id);
     }else if(record.question_signature===this.signature&&this.canAutoApply())await this.process(record,'replace',true);
    }
    this.render();
   }catch{/* Connection loss leaves both the local draft and durable server inbox intact. */}finally{this.busy=false;}
  }
  render(){
   if(this.dead)return;
   const html=this.records.map(item=>`<section class="phone-incoming"><h3>收到手机录入 · ${escape(item.device_name)}</h3>${item.question_signature!==this.signature?`<p class="notice warn">本题在同步后已修改，未自动录入；请核对后再选择追加或替换。</p><details><summary>查看同步时的题干</summary><div class="prose">${escape(item.question_prompt||'旧记录未保存题干')}</div></details>`:''}<p class="small">当前草稿已保留，请选择如何录入这份文字。</p><details><summary>查看识别文字 · ${[...item.text].length} 字符</summary><div class="prose">${escape(item.text)}</div></details><div class="row wrap section-gap"><button type="button" class="btn primary small" data-phone-operation="append" data-capture="${escape(item.id)}">追加到末尾</button><button type="button" class="btn small" data-phone-operation="replace" data-capture="${escape(item.id)}">替换草稿并保留备份</button><button type="button" class="btn small" data-phone-operation="dismissed" data-capture="${escape(item.id)}">忽略</button></div></section>`).join('');
   if(html!==this.markup){this.host.innerHTML=html;this.markup=html;}
  }
 }
 root.PhoneCaptureInbox=PhoneCaptureInbox;
})(window);
