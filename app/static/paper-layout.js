(function(root){
 'use strict';
 const whitespace=/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
 const zero=/[\p{M}\p{Cf}]/u;
 function wordRequirement(value){
  let text=String(value||'').normalize('NFKC').replace(/(?<=\d)\s+(?=\d)/g,'');
  const digits=Object.fromEntries(Array.from('零〇一二两三四五六七八九').map((c,i)=>[c,[0,0,1,2,2,3,4,5,6,7,8,9][i]]));
  text=text.replace(/[零〇一二两三四五六七八九十百千]+(?=\s*(?:字|[-—–~～至到]))/g,label=>{
   if(!/[十百千]/.test(label))return Array.from(label,c=>digits[c]).join('');
   let total=0,current=0;for(const c of label){if(c in digits)current=digits[c];else{total+=(current||1)*({十:10,百:100,千:1000}[c]);current=0;}}return String(total+current);
  }).replace(/(?:标题|小标题|前置句)(?:字数)?\s*(?:不超过|不得超过|最多|限|控制在)?\s*\d{1,4}\s*字(?:以内|以下|左右)?/g,'');
  const number=String.raw`(?<!\d)(\d{2,4})(?!\d)`,find=pattern=>text.match(new RegExp(pattern));
  const range=find(number+String.raw`\s*(?:字\s*)?[-—–~～至到]\s*(\d{2,4})\s*字`);
  let lo=range?Number(range[1]):null,hi=range?Number(range[2]):null,exclusive=false;
  const lower=find(String.raw`(?:不少于|不低于|不得少于|不能少于|不可少于|至少|不小于)\s*`+number+String.raw`\s*字`)||find(number+String.raw`\s*字(?:以上|起)`);
  if(lower)lo=Number(lower[1]);
  const over=find(String.raw`(?<!不)(?<!不得)(?<!不能)(?<!不可)(?:超过|大于|多于)\s*`+number+String.raw`\s*字`);if(over)lo=Number(over[1])+1;
  const upper=find(String.raw`(?:不超过|不得超过|不能超过|不大于|不多于|最多|至多|字数(?:为|是|控制在|在)?[:：]?)\s*`+number+String.raw`\s*字`)||find(number+String.raw`\s*字(?:以内|以下|为限|左右|上下)`);
  if(upper)hi=Number(upper[1]);
  const strict=find(String.raw`(?:不到|不足|不满|(?<!不)(?<!不得)(?<!不能)(?<!不可)少于|(?<!不)(?<!不得)(?<!不能)(?<!不可)小于)\s*`+number+String.raw`\s*字`);if(strict){hi=Number(strict[1]);exclusive=true;}
  if(hi===null&&lo===null){
   const bare=Array.from(text.matchAll(new RegExp(number+String.raw`\s*字`,'g')));
   if(bare.length===1){const prefix=text.slice(Math.max(0,bare[0].index-24),bare[0].index);if(text.trim().length<=15||/字数|要求|(?:撰写|拟写|写|作答)[^。；\n]{0,18}$/.test(prefix))hi=Number(bare[0][1]);}
  }
  return {word_min:lo,word_max:hi,max_exclusive:exclusive};
 }
 function specFor(q){
  if(q.paper_spec)return q.paper_spec;
  const limits=wordRequirement(q.prompt);let lo=limits.word_min??q.word_min??0,stated=limits.word_max??q.word_max??0;
  if(limits.word_min===null&&stated>0&&lo>stated)lo=0;
  if(limits.word_max===null&&limits.word_min!==null&&stated>0&&stated<lo)stated=0;
  const rows=q.paper_rows||Math.max(1,Math.ceil((stated||Math.max(lo,({summary:350,practical:600,essay:1200}[q.kind]||600)))/25)),strict=limits.max_exclusive;
  return {layout_version:2,columns:25,rows,cells:rows*25,word_min:lo,word_max:strict?stated-1:stated,stated_word_max:stated,max_exclusive:strict};
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
 const tools={analyze,check:publicCheck,specFor,wordRequirement};
 root.PaperLayout=tools;if(typeof module!=='undefined'&&module.exports)module.exports=tools;
})(typeof window==='undefined'?globalThis:window);
