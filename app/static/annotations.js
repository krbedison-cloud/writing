(function(root){
 'use strict';
 const colorPattern=/^#[0-9a-f]{6}$/i;
 const white=/[\s\u0085\u001c-\u001f]/u;
 const canonical=value=>Array.from(value).filter(c=>!white.test(c)).join('');
 const rules=new Set();
 function colorRule(color){
  color=color.toLowerCase();if(!colorPattern.test(color)||rules.has(color))return;
  const channels=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  const light=.2126*channels[0]+.7152*channels[1]+.0722*channels[2];
  GridPaper.rule(`mark.material-mark[data-color="${color}"]`).style.cssText=`background-color:${color};color:${light>.179?'#101a12':'#ffffff'};`;
  rules.add(color);
 }
 class Highlighter{
  constructor({element,question,paperId,api,toast}){
   this.element=element;this.q=question;this.api=api;this.toast=toast;this.items=[];this.saved=null;this.base=`/papers/${paperId}/questions/${question.id}/highlights`;
   this.toolbar=document.querySelector('#material-toolbar');this.color=this.toolbar.querySelector('#highlight-color');this.abort=new AbortController();const options={signal:this.abort.signal};
   let preferred='#ffff00';try{const stored=localStorage.getItem('highlight-color');if(colorPattern.test(stored||''))preferred=stored;}catch{}
   this.color.value=preferred;
   document.addEventListener('selectionchange',()=>{clearTimeout(this.timer);this.timer=setTimeout(()=>this.capture(),100);},options);
   document.addEventListener('pointerdown',event=>{if(!this.toolbar.contains(event.target)&&!this.element.contains(event.target))this.hide();},options);
   this.element.addEventListener('pointerup',()=>setTimeout(()=>this.capture(),0),options);
   this.element.addEventListener('click',event=>{const mark=event.target.closest('mark.material-mark');if(mark&&getSelection().isCollapsed){const item=this.items.find(x=>x.id===mark.dataset.highlightId);if(item){this.saved={...item};this.show(mark.getBoundingClientRect(),true);}}},options);
   this.toolbar.querySelectorAll('button').forEach(button=>button.addEventListener('pointerdown',event=>event.preventDefault(),options));
   this.toolbar.querySelector('[data-highlight-save]').addEventListener('click',()=>this.save(),options);
   this.toolbar.querySelector('[data-highlight-delete]').addEventListener('click',()=>this.remove(),options);
   this.toolbar.querySelector('[data-highlight-close]').addEventListener('click',()=>this.hide(),options);
   this.color.addEventListener('input',()=>{try{localStorage.setItem('highlight-color',this.color.value)}catch{}},options);
   document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!this.toolbar.hidden){this.hide();event.stopImmediatePropagation();}},options);
   window.addEventListener('resize',()=>this.hide(),options);
   document.addEventListener('scroll',()=>{if(!this.toolbar.hidden&&getSelection().rangeCount&&!getSelection().isCollapsed)this.position(getSelection().getRangeAt(0).getBoundingClientRect());}, {...options,capture:true});
   this.load();
  }
  destroy(){clearTimeout(this.timer);this.abort.abort();this.hide();}
  async load(){try{const data=await this.api(this.base);if(this.abort.signal.aborted)return;this.signature=data.signature;this.items=data.highlights;this.render();}catch(e){this.toast(e.message);}}
  capture(){
   if(this.abort.signal.aborted||this.toolbar.contains(document.activeElement))return;
   const selection=getSelection();if(!selection.rangeCount||selection.isCollapsed)return;
   const range=selection.getRangeAt(0);
   if(!this.element.contains(range.startContainer)||!this.element.contains(range.endContainer))return;
   const prefix=document.createRange();prefix.selectNodeContents(this.element);prefix.setEnd(range.startContainer,range.startOffset);
   const start=Array.from(canonical(prefix.toString())).length,quote=canonical(range.toString());
   if(!quote)return;
   this.saved={signature:this.signature||this.q.material_signature,start,end:start+Array.from(quote).length,quote};
   this.show(range.getBoundingClientRect(),false);
  }
  show(rect,existing){
   this.toolbar.hidden=false;
   this.toolbar.querySelector('[data-highlight-save]').textContent=existing?'更新颜色':'高亮';
   this.toolbar.querySelector('[data-highlight-delete]').hidden=!existing;
   if(existing&&colorPattern.test(this.saved.color))this.color.value=this.saved.color;
   this.position(rect);
  }
  position(rect){
   const width=this.toolbar.offsetWidth,height=this.toolbar.offsetHeight;
   const x=Math.max(8,Math.min(innerWidth-width-8,rect.left+rect.width/2-width/2));
   const y=rect.top>height+88?rect.top-height-10:Math.min(innerHeight-height-8,rect.bottom+10);
   const rule=GridPaper.rule('#material-toolbar');rule.style.setProperty('left',Math.round(x)+'px');rule.style.setProperty('top',Math.max(8,Math.round(y))+'px');
  }
  hide(){this.toolbar.hidden=true;this.saved=null;}
  async save(){
   if(!this.saved)return;
   const button=this.toolbar.querySelector('[data-highlight-save]');button.disabled=true;
   const value={...this.saved,color:this.color.value};
   try{
    const item=value.id?await this.api('/highlights/'+value.id,{method:'PATCH',body:JSON.stringify({color:value.color})}):await this.api(this.base,{method:'POST',body:JSON.stringify(value)});
    if(this.abort.signal.aborted)return;
    this.items=this.items.filter(x=>x.id!==item.id).concat(item);this.render();getSelection().removeAllRanges();this.hide();this.toast('高亮已保存');
   }catch(e){this.toast(e.message);}finally{button.disabled=false;}
  }
  async remove(){
   if(!this.saved?.id)return;
   const id=this.saved.id;try{await this.api('/highlights/'+id,{method:'DELETE'});this.items=this.items.filter(x=>x.id!==id);this.render();this.hide();this.toast('已取消这处高亮');}catch(e){this.toast(e.message);}
  }
  render(){
   if(!this.element.isConnected)return;
   this.element.querySelectorAll('mark.material-mark').forEach(mark=>mark.replaceWith(...mark.childNodes));this.element.normalize();
   const items=[...this.items].sort((a,b)=>a.updated_at.localeCompare(b.updated_at));items.forEach(x=>colorRule(x.color));
   const active=[];for(const item of items)for(let i=item.start;i<item.end;i++)active[i]=item;
   const walker=document.createTreeWalker(this.element,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
   let index=0;
   for(const node of nodes){
    const fragment=document.createDocumentFragment();let run='',current=null;
    const flush=()=>{if(!run)return;if(current){const mark=document.createElement('mark');mark.className='material-mark';mark.dataset.highlightId=current.id;mark.dataset.color=current.color;mark.textContent=run;fragment.append(mark);}else fragment.append(document.createTextNode(run));run='';};
    for(const char of node.data){const item=active[index]||null;if(item?.id!==current?.id){flush();current=item;}run+=char;if(!white.test(char))index++;}
    flush();node.replaceWith(fragment);
   }
   const count=document.querySelector('#highlight-count');if(count)count.textContent=`已保存 ${this.items.length} 处高亮 · 选中文字可继续标记`;
  }
 }
 root.MaterialHighlighter=Highlighter;
})(window);
