(function(root){
 'use strict';
 const whitespace=/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
 const zero=/[\p{M}\p{Cf}]/u;
 function specFor(q){
  if(q.paper_spec)return q.paper_spec;
  const stated=q.word_max||0,rows=q.paper_rows||Math.max(1,Math.ceil((stated||({summary:350,practical:600,essay:1200}[q.kind]||600))/25));
  const strict=new RegExp('(?:不到|不足|少于|不满)\\s*'+stated+'\\s*字').test(q.prompt||'');
  return {layout_version:2,columns:25,rows,cells:rows*25,word_min:q.word_min||0,word_max:strict?stated-1:stated,stated_word_max:stated,max_exclusive:strict};
 }
 function analyzeLegacy(value,spec){
  const text=String(value).replace(/\r\n?/g,'\n');
  let row=0,col=0,words=0,written=0,spaces=0,loss=0,index=0,overflowStart=null;
  const slots=[],carets=[];
  const coordinate=()=>({row:row+(col===spec.columns?1:0),col:col===spec.columns?0:col});
  carets[0]=coordinate();
  let previous;
  for(const char of text){
   const start=index;index+=char.length;
   carets[start]=coordinate();
   if(char.length===2)carets[start+1]=coordinate();
   if(char==='\n'){loss+=spec.columns-col;row++;col=0;}
   else if(zero.test(char)){if(previous){previous.text+=char;previous.end=index;}}
   else{
    const width=char==='\t'?2:1;
    for(let unit=0;unit<width;unit++){
     if(col===spec.columns){row++;col=0;}
     const slot={row,col,start,end:index,text:char==='\t'?' ':char};slots[row*spec.columns+col]=slot;previous=slot;col++;
    }
    written+=width;if(whitespace.test(char))spaces+=width;else words++;
   }
   if(overflowStart===null&&written+loss>spec.cells)overflowStart=start;
   carets[index]=coordinate();
  }
  const occupied=written+loss,usedRows=occupied?Math.ceil(occupied/spec.columns):0,reasons=[];
  if(spec.word_min&&words<spec.word_min)reasons.push(`字数不足：${words} / 至少 ${spec.word_min} 字`);
  if(spec.word_max&&words>spec.word_max)reasons.push(`字数超限：${words} / 最多 ${spec.word_max} 字`);
  if(usedRows>spec.rows)reasons.push(`纸面超限：占用 ${usedRows} / ${spec.rows} 行（含缩进和换行损失）`);
  return {text,slots,carets,overflow_start:overflowStart,word_count:words,occupied_cells:occupied,used_rows:usedRows,whitespace_cells:spaces,line_break_loss:loss,formatting_cells:spaces+loss,remaining_cells:spec.cells-occupied,overflow_cells:Math.max(0,occupied-spec.cells),valid:reasons.length===0,reasons};
 }

 function analyze(value,spec){
  if((spec.layout_version||1)<2)return analyzeLegacy(value,spec);
  const text=String(value).replace(/\r\n?/g,'\n'),columns=spec.columns;
  const tokens=/[A-Za-z0-9]+(?:[.:%/+\-][A-Za-z0-9]+)*[.%]?|——|……|[\s\S]/gu;
  const ascii=/^[A-Za-z0-9]+(?:[.:%/+\-][A-Za-z0-9]+)*[.%]?$/;
  const open='（〔［｛〈《「『【〖“‘([{',close='，。、；：！？）》」』】〕］｝〉〗”’)]},.!?;:';
  let row=0,col=0,written=0,spaces=0,loss=0,overflowStart=null,previous=null;
  const slots=[],carets=[],coordinate=()=>({row:row+(col===columns?1:0),col:col===columns?0:col});carets[0]=coordinate();
  const words=Array.from(text).filter(c=>!whitespace.test(c)&&!zero.test(c)).length;
  for(const match of text.matchAll(tokens)){
   const token=match[0],start=match.index,end=start+token.length;
   carets[start]=coordinate();
   if(token==='\n'){
    loss+=columns-col;row++;col=0;previous=null;
   }else if(zero.test(token)){
    if(previous){previous.text+=token;previous.end=end;}
   }else{
    const run=ascii.test(token),width=run?Math.ceil(token.length/2):['——','……','\t'].includes(token)?2:1;
    if(close.includes(token)&&col===columns&&previous&&!whitespace.test(previous.text)){
     if(previous.hanging_start===undefined)previous.hanging_start=previous.text.length;
     carets[start]={row,col:columns-.05};
     previous.text+=token;previous.end=end;previous.hanging=true;
    }else{
     if((open.includes(token)&&col===columns-1)||(width<=columns&&col+width>columns)){
      loss+=columns-col;row++;col=0;
     }
     let offset=start;
     for(let unit=0;unit<width;unit++){
      if(col===columns){row++;col=0;}
      const part=run?token.slice(unit*2,unit*2+2):token==='\t'?' ':width===2?token[unit]:token;
      const length=token==='\t'?token.length:part.length;
      const slot={row,col,start:offset,end:offset+length,text:part,packed:run&&part.length>1};
      slots[row*columns+col]=slot;previous=slot;
      for(let j=0;j<length;j++)carets[offset+j]={row,col:col+(run?j/2:0)};
      col++;if(token!=='\t')offset+=length;
     }
     written+=width;if(whitespace.test(token))spaces+=width;
    }
   }
   if(overflowStart===null&&written+loss>spec.cells)overflowStart=slots[spec.cells]?.start??start;
   carets[end]=coordinate();
  }
  const occupied=written+loss,usedRows=occupied?Math.ceil(occupied/columns):0,reasons=[];
  if(spec.word_min&&words<spec.word_min)reasons.push(`字数不足：${words} / 至少 ${spec.word_min} 字`);
  if(spec.word_max&&words>spec.word_max)reasons.push(`字数超限：${words} / 最多 ${spec.word_max} 字`);
  if(usedRows>spec.rows)reasons.push(`纸面超限：占用 ${usedRows} / ${spec.rows} 行（含缩进和换行损失）`);
  return {text,slots,carets,overflow_start:overflowStart,word_count:words,occupied_cells:occupied,used_rows:usedRows,whitespace_cells:spaces,line_break_loss:loss,formatting_cells:spaces+loss,remaining_cells:spec.cells-occupied,overflow_cells:Math.max(0,occupied-spec.cells),valid:!reasons.length,reasons};
 }
 function publicCheck(value,spec){const r=analyze(value,spec);return Object.fromEntries(Object.entries(r).filter(([k])=>!['text','slots','carets','overflow_start'].includes(k)));}
 const tools={analyze,check:publicCheck,specFor};
 root.PaperLayout=tools;if(typeof module!=='undefined'&&module.exports)module.exports=tools;
})(typeof window==='undefined'?globalThis:window);
