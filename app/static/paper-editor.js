(function(root){
 'use strict';
 const ruleCache=new Map();
 function rule(selector){
  if(ruleCache.has(selector))return ruleCache.get(selector);
  const sheet=[...document.styleSheets].find(s=>s.href?.endsWith('/static/paper.css'));
  if(!sheet)throw Error('稿纸样式尚未加载，请刷新页面');
  const index=sheet.insertRule(selector+'{}',sheet.cssRules.length),r=sheet.cssRules[index];ruleCache.set(selector,r);return r;
 }
 function cells(grid,number,columns){
  const cache=[];
  for(let row=0;row<number;row++){
   const line=document.createElement('div');line.className='paper-row';
   const label=document.createElement('span');label.className='paper-row-number';label.textContent=String(row+1);line.append(label);
   for(let col=0;col<columns;col++){
    const cell=document.createElement('span');cell.className='paper-cell';cell.dataset.cell=String(row*columns+col);line.append(cell);cache.push(cell);
   }
   grid.append(line);
  }
  return cache;
 }
 function paintCell(node,slot){
  const value=slot?.text||'',hanging=slot?.hanging_start;
  if(node.paperValue!==value||node.paperHanging!==hanging){
   node.paperValue=value;node.paperHanging=hanging;
   if(Number.isInteger(hanging)){
    const base=document.createElement('span'),punctuation=document.createElement('span');
    base.className='paper-hanging-base';base.textContent=value.slice(0,hanging);
    punctuation.className='paper-hanging-punctuation';punctuation.textContent=value.slice(hanging);
    node.replaceChildren(base,punctuation);
   }else node.textContent=value;
  }
  node.classList.toggle('paper-packed',!!slot?.packed);
  node.classList.toggle('paper-hanging',!!slot?.hanging);
 }
 class Editor{
  constructor({spec,onChange,toast}){
   this.spec=spec;this.onChange=onChange;this.toast=toast;
   this.root=document.querySelector('#paper-editor');this.grid=document.querySelector('#paper-grid');this.input=document.querySelector('#answer');this.viewport=document.querySelector('#paper-viewport');this.surface=document.querySelector('#paper-surface');this.cellNodes=[];this.rows=0;this.drag=null;this.composing=false;
   this.abort=new AbortController();const options={signal:this.abort.signal};
   this.input.addEventListener('input',()=>this.render(true),options);
   this.input.addEventListener('compositionstart',()=>{this.composing=true;},options);
   this.input.addEventListener('compositionend',()=>{this.composing=false;this.render(true);},options);
   this.input.addEventListener('keydown',event=>this.key(event),options);
   this.surface.addEventListener('pointerdown',event=>{if(this.root.dataset.mode==='grid')this.pointerDown(event);},options);
   this.grid.addEventListener('pointermove',event=>this.pointerMove(event),options);
   this.grid.addEventListener('pointerup',()=>{this.drag=null;},options);
   this.grid.addEventListener('pointercancel',()=>{this.drag=null;},options);
   this.grid.addEventListener('lostpointercapture',()=>{this.drag=null;},options);
   document.addEventListener('selectionchange',()=>{if(document.activeElement===this.input)this.selection();},options);
   this.input.addEventListener('focus',()=>this.selection(),options);this.input.addEventListener('blur',()=>this.selection(),options);
   this.root.querySelectorAll('[data-paper-format]').forEach(button=>button.addEventListener('click',()=>this.format(button.dataset.paperFormat),options));
   this.root.querySelector('[data-paper-mode]').addEventListener('click',()=>this.toggleMode(),options);
   this.root.querySelector('[data-paper-select-overflow]').addEventListener('click',()=>this.selectOverflow(),options);
   const indent=this.root.querySelector('#paper-indent');
   try{indent.checked=localStorage.getItem('paper-auto-indent')!=='0';}catch{}
   indent.addEventListener('change',()=>{try{localStorage.setItem('paper-auto-indent',indent.checked?'1':'0')}catch{}},options);
   this.observer=new ResizeObserver(()=>this.fit());this.observer.observe(this.viewport);
   this.preferencesObserver=new MutationObserver(()=>this.fit());
   this.preferencesObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-reading-size','data-focus','data-reading-layout']});
   this.render(false);
  }
  destroy(){this.abort.abort();this.observer.disconnect();this.preferencesObserver.disconnect();}
  render(scroll){
   this.layout=PaperLayout.analyze(this.input.value,this.spec);
   const total=Math.max(this.spec.rows,this.layout.used_rows,1);
   if(total!==this.rows){this.grid.replaceChildren();this.cellNodes=cells(this.grid,total,this.spec.columns);this.rows=total;}
   this.cellNodes.forEach((node,index)=>{
    const slot=this.layout.slots[index];
    paintCell(node,slot);
    node.classList.toggle('paper-overflow',index>=this.spec.cells);
   });
   this.root.querySelector('[data-paper-select-overflow]').hidden=this.layout.overflow_start===null;
   this.onChange?.(PaperLayout.check(this.input.value,this.spec));
   this.fit();
   this.selection(scroll);
  }
  fit(){
   if(!this.rows||!this.viewport.isConnected||this.root.dataset.mode!=='grid')return;
   const width=this.viewport.clientWidth,height=this.viewport.clientHeight;
   if(width<1||height<1)return;
   const preferred=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--reading-size'))||20;
   let best={cell:0,pages:1,rows:this.rows};
   // Consecutive rows flow down each paper column, then continue in the next.
   // Choose the largest square that keeps every row inside the available area.
   for(let pages=1;pages<=Math.min(this.rows,12);pages++){
    const rows=Math.ceil(this.rows/pages);
    const cell=Math.floor(Math.min(preferred+4,(width-(preferred*2+28)-(preferred*2+12)*(pages-1)-22*pages)/(25*pages),(height-30)/(rows*1.28))*2)/2;
    if(cell>best.cell+.4)best={cell,pages,rows};
   }
   const cell=Math.max(1,best.cell),style=rule('#paper-editor').style;
   style.setProperty('--paper-cell',cell+'px');
   style.setProperty('--paper-font',Math.min(preferred,cell*.84)+'px');
   style.setProperty('--paper-row',cell*1.28+'px');
   style.setProperty('--paper-page-rows',String(best.rows));
   style.setProperty('--paper-page-gap',(preferred*2+12)+'px');
   this.grid.querySelectorAll('.paper-row').forEach((row,i)=>{
    row.classList.toggle('paper-page-start',i%best.rows===0);
    row.classList.toggle('paper-page-end',i%best.rows===best.rows-1||i===this.rows-1);
   });
   const status=this.root.querySelector('[data-paper-view]');
   if(status)status.textContent=best.pages>1?`全文展开 · ${best.pages} 栏连续书写`:'全文展开';
   this.root.dataset.paperColumns=String(best.pages);
   this.selection();
  }
  selection(scroll=false){
   if(!this.layout||!this.input.isConnected)return;
   const start=this.input.selectionStart,end=this.input.selectionEnd,focus=this.input.selectionDirection==='backward'?start:end;
   const cursor=this.layout.carets[focus]||this.layout.carets.at(-1)||{row:0,col:0};
   let index=Math.floor(cursor.row*this.spec.columns+cursor.col);
   const after=index>=this.cellNodes.length;index=Math.min(index,this.cellNodes.length-1);
   const active=document.activeElement===this.input;
   const from=this.layout.carets[start],to=this.layout.carets[end];
   const fromCell=from?from.row*this.spec.columns+from.col:0,toCell=to?to.row*this.spec.columns+to.col:0;
   this.cellNodes.forEach((node,i)=>{
    const slot=this.layout.slots[i];
    node.classList.toggle('paper-selected',start!==end&&((i>=fromCell&&i<toCell)||(!!slot&&slot.start<end&&slot.end>start)));
    node.classList.toggle('paper-caret',active&&start===end&&i===index);
    node.classList.toggle('paper-caret-end',after&&i===index);
   });
   const cell=this.cellNodes[index];if(!cell)return;
   if(this.root.dataset.mode==='grid'){
    const rect=cell.getBoundingClientRect(),base=this.surface.getBoundingClientRect(),fraction=cursor.col-Math.floor(cursor.col);
    rule('#paper-grid .paper-caret::before').style.setProperty('left',after?'100%':fraction*100+'%');
    rule('#paper-editor[data-mode="grid"] #answer').style.setProperty('left',Math.max(0,rect.left-base.left+(after?rect.width-2:fraction*rect.width))+'px');
    rule('#paper-editor[data-mode="grid"] #answer').style.setProperty('top',Math.max(0,rect.top-base.top)+'px');
   }
  }
  atCell(index,after=false){
   const slot=this.layout.slots[index];if(slot)return after?slot.end:slot.start;
   const row=Math.floor(index/this.spec.columns),col=index%this.spec.columns;
   let best=this.input.value.length,distance=Infinity;
   this.layout.carets.forEach((point,i)=>{
    const code=this.input.value.charCodeAt(i),previous=this.input.value.charCodeAt(i-1);
    if(code>=0xdc00&&code<=0xdfff&&previous>=0xd800&&previous<=0xdbff)return;
    const d=Math.abs(point.row-row)*this.spec.columns+Math.abs(point.col-col);
    if(d<distance||(d===distance&&i>best)){best=i;distance=d;}
   });
   return best;
  }
  hitCell(x,y){
   // The native input follows the caret; ignore its two-pixel overlay while dragging.
   const hit=document.elementsFromPoint(x,y).find(node=>node.classList.contains('paper-cell')&&this.grid.contains(node));
   if(hit)return hit;
   let nearest,distance=Infinity;
   for(const row of this.grid.children){
    const rect=row.getBoundingClientRect(),dx=Math.max(rect.left-x,0,x-rect.right),dy=Math.max(rect.top-y,0,y-rect.bottom);
    const d=dx*dx+dy*dy;if(d<distance){nearest=row;distance=d;}
   }
   if(!nearest)return null;
   const first=nearest.children[1].getBoundingClientRect();
   const col=Math.max(0,Math.min(this.spec.columns-1,Math.floor((x-first.left)/first.width)));
   return nearest.children[col+1];
  }
  atPoint(cell,x){
   const index=Number(cell.dataset.cell),rect=cell.getBoundingClientRect(),slot=this.layout.slots[index];
   if(Number.isInteger(slot?.hanging_start)){
    if(x>=rect.right)return slot.end;
    const base=Array.from(slot.text.slice(0,slot.hanging_start)),count=Math.round(Math.max(0,Math.min(1,(x-rect.left)/rect.width))*base.length);
    return slot.start+base.slice(0,count).join('').length;
   }
   if(slot&&slot.packed){const chars=Array.from(slot.text),count=Math.round(Math.max(0,Math.min(1,(x-rect.left)/rect.width))*chars.length);return slot.start+chars.slice(0,count).join('').length;}
   return this.atCell(index,x>rect.left+rect.width/2);
  }
  range(anchor,end){this.input.focus({preventScroll:true});this.input.setSelectionRange(Math.min(anchor,end),Math.max(anchor,end),end<anchor?'backward':'forward');this.selection();}
  pointerDown(event){
   const cell=this.hitCell(event.clientX,event.clientY);if(!cell||event.button!==0)return;
   event.preventDefault();const rect=cell.getBoundingClientRect();const index=this.atPoint(cell,event.clientX);
   const anchor=event.shiftKey?(this.input.selectionDirection==='backward'?this.input.selectionEnd:this.input.selectionStart):index;
   this.drag=anchor;this.grid.setPointerCapture(event.pointerId);this.range(anchor,index);
  }
  pointerMove(event){
   if(this.drag===null)return;
   const cell=this.hitCell(event.clientX,event.clientY);if(!cell)return;
   const rect=cell.getBoundingClientRect();this.range(this.drag,this.atPoint(cell,event.clientX));
  }
  selectOverflow(){
   const start=this.layout.overflow_start;if(start===null)return;
   this.range(start,this.input.value.length);
   this.toast?.('已选中超出纸面的内容，按 Backspace 删除；可撤销恢复');
  }
  insert(text,start=this.input.selectionStart,end=this.input.selectionEnd){
   this.input.focus({preventScroll:true});this.input.setSelectionRange(start,end);
   if(!document.execCommand('insertText',false,text)){
    this.input.setRangeText(text,start,end,'end');this.input.dispatchEvent(new Event('input',{bubbles:true}));
   }
   this.render(true);
  }
  key(event){
   if(event.isComposing||this.composing||event.keyCode===229)return;
   if(event.key==='Escape'&&this.root.dataset.mode==='grid'){event.stopPropagation();this.root.querySelector('[data-paper-mode]').focus();return;}
   if(event.key==='Tab'&&!event.shiftKey){event.preventDefault();this.insert('\u3000\u3000');return;}
   if(event.key==='Enter'){event.preventDefault();this.insert('\n'+(this.root.querySelector('#paper-indent').checked?'\u3000\u3000':''));return;}
   if(this.root.dataset.mode!=='grid'||event.ctrlKey||event.metaKey)return;
   if(['ArrowUp','ArrowDown','Home','End'].includes(event.key)){
    event.preventDefault();const focus=this.input.selectionDirection==='backward'?this.input.selectionStart:this.input.selectionEnd;const position=this.layout.carets[focus]||this.layout.carets.at(-1),col=position.col;
    const row=Math.max(0,position.row+(event.key==='ArrowUp'?-1:event.key==='ArrowDown'?1:0));
    const target=row*this.spec.columns+(event.key==='Home'?0:event.key==='End'?this.spec.columns-1:col);
    const end=this.atCell(target,event.key==='End');this.range(event.shiftKey?(this.input.selectionDirection==='backward'?this.input.selectionEnd:this.input.selectionStart):end,end);this.selection(true);
   }
  }
  format(mode){
   const value=this.input.value,at=this.input.selectionStart,start=at>0?value.lastIndexOf('\n',at-1)+1:0,tail=value.indexOf('\n',at),end=tail<0?value.length:tail;
   const text=value.slice(start,end).replace(/^[\s\u3000]+|[\s\u3000]+$/g,'');
   const width=PaperLayout.analyze(text,this.spec).occupied_cells;
   if((mode==='center'||mode==='right')&&width>this.spec.columns){this.toast?.('该行超过25格，先缩短这一行再对齐');return;}
   const pad=mode==='indent'?2:mode==='center'?Math.floor((this.spec.columns-width)/2):mode==='right'?this.spec.columns-width:0;
   this.insert('\u3000'.repeat(Math.max(0,pad))+text,start,end);
  }
  toggleMode(){
   const plain=this.root.dataset.mode!=='plain';this.root.dataset.mode=plain?'plain':'grid';this.grid.hidden=plain;
   this.root.querySelector('[data-paper-mode]').textContent=plain?'返回格子纸':'原文编辑';this.input.focus({preventScroll:true});this.fit();this.selection();
  }
 }
 function preview(answer,spec){
  const container=document.createElement('div');container.className='paper-preview';
  const layout=PaperLayout.analyze(answer,spec),grid=document.createElement('div');grid.className='paper-grid';
  const nodes=cells(grid,Math.max(spec.rows,layout.used_rows,1),spec.columns);
  nodes.forEach((node,index)=>{paintCell(node,layout.slots[index]);node.classList.toggle('paper-overflow',index>=spec.cells);});
  container.append(grid);return container;
 }
 root.GridPaper={Editor,preview,rule};
})(window);
