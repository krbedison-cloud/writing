'use strict';
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const READING_DEFAULTS={size:'20',leading:'2',font:'sans',theme:'paper',layout:'auto'};
const READING_OPTIONS={size:['18','20','22','24'],leading:['1.8','2','2.2'],font:['sans','serif'],theme:['paper','light','night'],layout:['auto','stacked']};
let readingPreferences={...READING_DEFAULTS};
let focused=false;
function applyReadingPreferences(preferences, persist=true){
 for(const [key,values] of Object.entries(READING_OPTIONS)){
  const value=values.includes(String(preferences[key]))?String(preferences[key]):READING_DEFAULTS[key];
  readingPreferences[key]=value;
  document.documentElement.dataset['reading'+key[0].toUpperCase()+key.slice(1)]=value;
  const control=$('#reading-'+key);if(control)control.value=value;
 }
 if(persist){try{localStorage.setItem('reading-preferences',JSON.stringify(readingPreferences))}catch{/* Reading remains usable if browser storage is full. */}}
}
function restoreReadingPreferences(){let saved={};try{saved=JSON.parse(localStorage.getItem('reading-preferences')||'{}')||{}}catch{}applyReadingPreferences(saved,false);}
function setFocusMode(value){
 focused=value;
 document.documentElement.dataset.focus=String(value);
 $('#focus-exit').hidden=!value;
 $$('[data-action="toggle-focus"]').filter(x=>x.id!=='focus-exit').forEach(x=>{x.textContent=value?'退出专注':'专注作答';x.setAttribute('aria-pressed',String(value));});
}
function readingText(value){
 // Reflow PDF line wraps for display only; the canonical materials and API payload stay intact.
 const blocks=[];let lines=[];
 const flush=()=>{if(lines.length){const text=lines.reduce((a,b)=>a+(a&&/[A-Za-z0-9]$/.test(a)&&/^[A-Za-z0-9]/.test(b)?' ':'')+b,'');blocks.push(`<p>${esc(text)}</p>`);lines=[];}};
 for(const raw of String(value||'').split(/\r?\n/)){
  const line=raw.trim();
  if(!line){flush();continue;}
  if(/^(?:给定)?(?:材料|资料)\s*[（(]?[0-9一二三四五六七八九十]+[）)]?\s*(?:[:：].*)?$/.test(line)){flush();blocks.push(`<h3>${esc(line)}</h3>`);continue;}
  if(/^要求[：:]/.test(line)){flush();lines.push(line);if(/[。！？；][”’」』）)]*$/.test(line))flush();continue;}
  if(/^[（(]?[0-9一二三四五六七八九十]+[）).．、]/.test(line)){flush();blocks.push(`<p class="reading-item">${esc(line)}</p>`);continue;}
  lines.push(line);if(/[。！？；][”’」』）)]*$/.test(line))flush();
 }
 flush();return blocks.join('');
}
const kinds = {summary:'概括 / 短简报',practical:'应用文 / 短评',essay:'大作文'};
const full = {summary:20,practical:30,essay:50};
const statuses = {queued:'排队中',running:'批改中',completed:'已完成',partial:'部分模型失败',failed:'批改失败',summary_failed:'汇总失败',interrupted:'已中断'};
const dimensions = ['审题立意','要点提炼','材料运用','结构逻辑','语言表达','文体规范'];
const paths = {
 overview:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 papers:'<path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5M8 12h8M8 16h6"/>',
 history:'<path d="M3 10a9 9 0 1 1 1 7M3 4v6h6M12 7v5l3 2"/>',
 profile:'<path d="m12 2 9 5v10l-9 5-9-5V7zM12 2v20M3 7l18 10M21 7 3 17"/>',
 models:'<rect x="5" y="5" width="14" height="14" rx="3"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
 arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 check:'<path d="m5 12 4 4 10-10"/>',
 trend:'<path d="m3 17 6-7 4 3 8-10M15 3h6v6"/>',
 close:'<path d="m6 6 12 12M6 18 18 6"/>',
 export:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>'
};
const icon = key => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[key]||paths.papers}</svg>`;
const source = p => `${esc(p.year)} · ${esc(p.province)} · ${esc(p.volume)}`;
const date = value => new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const badge = (text, color='') => `<span class="badge ${color}">${esc(text)}</span>`;
const list = xs => `<ul class="result-list">${(xs||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
const btn = (action, text, cls='', attrs='') => `<button class="btn ${cls}" data-action="${action}" ${attrs}>${text}</button>`;
const empty = (text, help, action='') => `<div class="empty">${icon('papers')}<h3>${esc(text)}</h3><p>${esc(help)}</p>${action}</div>`;
const state = {providers:[],paper:null,attempt:null,jobs:[],routeToken:0,reviewProvider:null,referenceVersions:{}};
state.referenceVersions=state.referenceVersions||{};
let toastTimer;
function toast(text) {const el=$('#toast');el.textContent=text;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),5500);}
async function api(path, options={}) {
 const headers = options.body instanceof FormData ? {} : {'Content-Type':'application/json'};
 const response = await fetch('/api'+path,{...options,headers:{...headers,...options.headers}});
 if(!response.ok){let body;try{body=await response.json()}catch{body={detail:`请求失败 (${response.status})`}};throw Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));}
 return response.json();
}
const post = (path,body={})=>api(path,{method:'POST',body:JSON.stringify(body)});
const option = (value,label,selected=false)=>`<option value="${esc(value)}"${selected?' selected':''}>${esc(label)}</option>`;
function modelOptions(selected='') {return state.providers.map(p=>option(p.id,`${p.name} · ${p.model}`,p.id===selected)).join('');}
function head(title, description, actions='', eyebrow='YOUR PERSONAL WRITING SPACE') {return `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="view-actions">${actions}</div></div>`;}
function renderRadar(values) {
 const cx=160,cy=125,r=80;
 const point=(i,n)=>[cx+Math.sin(i*Math.PI/3)*r*n,cy-Math.cos(i*Math.PI/3)*r*n];
 const polygon=n=>dimensions.map((_,i)=>point(i,n).join(',')).join(' ');
 const has=dimensions.some(k=>values[k]!=null);
 return `<svg class="radar" viewBox="0 0 320 245" role="img" aria-label="六维能力雷达图${has?'':'，暂无数据'}">${[.25,.5,.75,1].map(n=>`<polygon class="grid" points="${polygon(n)}"/>`).join('')}${dimensions.map((_,i)=>`<line class="grid" x1="160" y1="125" x2="${point(i,1)[0]}" y2="${point(i,1)[1]}"/>`).join('')}${has?`<polygon class="area" points="${dimensions.map((k,i)=>point(i,(values[k]||0)/100).join(',')).join(' ')}"/>${dimensions.map((k,i)=>`<circle class="point" cx="${point(i,(values[k]||0)/100)[0]}" cy="${point(i,(values[k]||0)/100)[1]}" r="3"/>`).join('')}`:''}${dimensions.map((k,i)=>{const p=point(i,1.32);return `<text x="${p[0]}" y="${p[1]}" text-anchor="middle" dominant-baseline="middle">${k}${values[k]!=null?' '+values[k]:''}</text>`}).join('')}</svg>`;
}
function renderTrend(timeline) {
 const rows=timeline.slice(-20),w=580,h=150;
 if(!rows.length)return empty('成长曲线，从第一份作答开始','完成批改后，会在这里留下你的进步轨迹');
 const x=i=>40+(rows.length===1?.5:i/(rows.length-1))*(w-60),y=v=>h-20-v/100*(h-40);
 return `<svg class="chart" viewBox="0 0 ${w} ${h+20}" role="img" aria-label="最近20次练习得分率趋势">${[0,50,100].map(v=>`<line class="base" x1="35" x2="570" y1="${y(v)}" y2="${y(v)}"/><text x="25" y="${y(v)+4}" text-anchor="end">${v}%</text>`).join('')}<polyline class="trend" points="${rows.map((a,i)=>`${x(i)},${y(a.score)}`).join(' ')}"/>${rows.map((a,i)=>`<a href="#attempt/${a.id}"><circle cx="${x(i)}" cy="${y(a.score)}" r="4" fill="#789862"><title>${esc(date(a.date))} · ${a.score}%</title></circle></a>`).join('')}<text x="40" y="167">${esc(date(rows[0].date))}</text><text x="570" y="167" text-anchor="end">最近 ${rows.length} 次 · 不同题型按得分率比较</text></svg>`;
}
function stat(label,value,unit,foot,ico) {return `<div class="stat"><div class="stat-top">${label}${icon(ico)}</div><div class="stat-value">${esc(value)}<small>${unit}</small></div><div class="stat-foot">${foot}</div></div>`;}
function paperItem(p) {return `<div class="paper-item"><div class="paper-icon">${icon('papers')}</div><div class="item-main"><h3>${esc(p.title)}</h3><p>${source(p)} · ${p.questions.length} 道题</p></div><a class="btn small" href="#paper/${p.id}">进入 ${icon('arrow')}</a></div>`;}
async function overview(){
 const [s,papers]=await Promise.all([api('/profile'),api('/papers')]);
 return head('每一笔，都算数。','把模糊的“还不够好”，变成看得见的下一步。',btn('upload',`${icon('plus')} 导入真题`,'primary'))+
 `<section class="hero"><div class="hero-copy"><div class="eyebrow">A LITTLE BETTER, EVERY DAY</div><h2>从认真作答，到有据可循的进步。</h2><p>多位 AI 教练独立批改，一份诊断看清得失。<br>让每一次练习，都成为下一次高分的起点。</p><a class="btn primary" href="#papers">开始今日练习 ${icon('arrow')}</a></div><svg class="hero-art" viewBox="0 0 260 230" aria-hidden="true"><circle class="orbit" cx="144" cy="132" r="95"/><circle class="orbit" cx="144" cy="132" r="75"/><path class="book" d="M50 77q46-15 83 9v108q-39-24-83-9z"/><path class="book" d="M133 86q41-29 83-24v109q-48-3-83 23z"/><path d="m69 102 42 5m-42 15 42 5m-42 15 29 4m54-42 44-14m-44 32 44-14m-44 33 28-9" class="orbit"/><path d="m194 16 8 15 17 3-12 12 2 17-15-8-15 8 3-17-12-12 16-3z" fill="#b4c68f"/></svg></section>`+
 `<div class="stats">${stat('累计有效练习',s.graded_count,'次','每份作答，都有成长的价值','history')}${stat('浙江三题预估',s.estimated_total??'—','/ 100',s.estimated_total==null?'完成三类题型后解锁':'各题型最近 5 次均分 · 非官方预测','trend')}${stat('我的真题库',s.paper_count,'套','好材料，值得反复练','papers')}${stat('成长等级','Lv. '+s.level,'',`${s.xp} XP · 完成一份新答案 +25 XP`,'profile')}</div>`+
 `<div class="grid-main"><div class="stack"><section class="card"><div class="card-head"><h2>继续练习</h2><a class="link" href="#papers">全部真题 →</a></div>${papers.length?papers.slice(0,3).map(paperItem).join(''):empty('你的真题库还是空的','上传一份 PDF，开始构建自己的训练地图',btn('upload','导入第一份真题','small'))}</section><section class="card"><div class="card-head"><h2>进步的轨迹</h2><span class="subtitle">用每一次真实作答记录成长</span></div>${renderTrend(s.timeline)}</section></div><section class="card"><div class="card-head"><h2>我的能力画像</h2><a class="link" href="#profile">查看诊断 →</a></div>${renderRadar(s.dimensions)}<p class="radar-note">${s.graded_count?'基于全部 '+s.graded_count+' 次有效练习 · 六维均分':'尚无有效练习 · 完成批改后点亮画像'}</p><div class="level-box"><div class="row between"><b>Lv. ${s.level} ${s.level<3?'积累中的写作者':'持续进阶的写作者'}</b><span class="tiny muted">${s.level_progress} / 100 XP</span></div><progress class="progress" value="${s.level_progress}" max="100"></progress><div class="tiny muted">等级奖励坚持，能力由真实表现衡量。</div></div></section></div>`;
}
async function library(){
 const papers=await api('/papers');state.papers=papers;
 return head('真题库','把散落的试卷，整理成自己的练习地图。',btn('upload',`${icon('upload')} 导入 PDF`,'primary'),'EXAM LIBRARY')+
 `<div class="filter-row"><input id="paper-search" placeholder="搜索年份、省份、卷别…" aria-label="搜索试卷"><span class="small muted">共 ${papers.length} 套试卷 · 支持文字 PDF 与中文扫描件</span></div><div class="library" id="paper-list">${paperCards(papers)}</div>`;
}
function paperCards(papers){return papers.map(p=>`<article class="card paper-card"><div class="row between">${badge(sourceText(p))}${badge(p.confirmed?'已校对':'待校对',p.confirmed?'':'amber')}</div><h2>${esc(p.title)}</h2><p class="small muted">${p.questions.length} 道题 · ${p.questions.map(q=>full[q.kind]).join(' / ')} 分训练档</p><div class="paper-card-foot"><span class="tiny muted">导入于 ${esc(date(p.created_at))}</span><a href="#paper/${p.id}" class="btn small">查看试卷 ${icon('arrow')}</a></div></article>`).join('')+`<button class="upload-card" data-action="upload">${icon('plus')}<span>添加一份新真题</span><small>PDF 格式 · 不超过 25 MB / 80 页</small></button>`;}
const sourceText=p=>`${p.year} · ${p.province} · ${p.volume}`;
async function paperView(id){
 const p=await api('/papers/'+id);state.paper=p;
 return head(p.title,sourceText(p),btn('edit-paper','校对试卷')+`<a class="btn" href="/api/papers/${id}/pdf" target="_blank" rel="noopener">原始 PDF ↗</a>`,'EXAM DETAILS')+
 (p.requires_material_selection?'<div class="notice warn">部分题干未能确定材料范围，请点击“校对试卷”，逐题选择材料几到几或全部材料。</div>':'')+
 (!p.confirmed?'<div class="notice warn">自动识别结果需要你校对：确认年份、省份、卷别、题干与训练档后即可作答。短简报按 20 分归档，仍按简报文体批改。</div>':'')+
 `<div class="row wrap section-gap"><label class="field">辅助识别模型<select id="classify-provider">${modelOptions(localStorage.getItem('master')||'')}</select></label>${btn('classify-paper','用 AI 重新识别','',state.providers.length?'':'disabled')}<span class="tiny muted">可选 · 会发送完整试卷文本到所选 API，并替换当前识别结果</span></div><div class="stack section-gap">${p.questions.map(q=>`<section class="card question-card"><div class="question-no">${String(q.number).padStart(2,'0')}</div><div class="content"><div class="row between"><h2>第 ${q.number} 题</h2><div class="row">${badge(kinds[q.kind])}${badge(full[q.kind]+' 分','amber')}</div></div><p class="prompt">${esc(q.prompt)}</p><div class="row between wrap"><span class="tiny muted">${esc(q.classification_note)}<br>${esc(q.material_scope?.note||'')} · ${q.material_scope?.character_count??0} 字符材料</span>${p.confirmed&&!q.material_scope?.requires_choice?`<a class="btn primary" href="#practice/${p.id}/${q.id}">开始作答 ${icon('arrow')}</a>`:btn('edit-paper','校对后开始作答','primary')}</div></div></section>`).join('')}</div><details class="plain-details"><summary>查看提取的原始文字</summary><pre class="prose">${esc(p.raw_text)}</pre></details>`;
}
let paperEditorInstance=null,materialHighlighter=null,practiceTimer=null;
function paperEditorHtml(q,draft){
 const spec=PaperLayout.specFor(q);
 return `<div class="paper-editor" id="paper-editor" data-mode="grid"><div class="paper-toolbar"><strong>25 格 × ${spec.rows} 行 · 共 ${spec.cells} 格${q.paper_rows?'（手动设置行数）':''} <span data-paper-view title="按行号从上往下、从左向右连续书写；Tab 空两格，Enter 换段">全文展开</span></strong><button type="button" class="btn small" data-paper-mode>原文编辑</button><button type="button" class="btn small" data-paper-select-overflow hidden title="选中超出纸面容量的全部内容，再按 Backspace 删除">选中超出部分</button><div class="paper-format-tools"><button type="button" class="btn small" data-paper-format="indent">空两格</button><button type="button" class="btn small" data-paper-format="left">顶格</button><button type="button" class="btn small" data-paper-format="center">标题居中</button><button type="button" class="btn small" data-paper-format="right">落款右齐</button><label><input id="paper-indent" type="checkbox" checked>换段空两格</label></div></div><div class="paper-viewport" id="paper-viewport"><div class="paper-surface" id="paper-surface"><div class="paper-grid" id="paper-grid" aria-hidden="true"></div><textarea class="paper-native answer-area" name="answer" id="answer" aria-label="格子纸作答，每行25格" wrap="off" maxlength="20000" required>${esc(draft)}</textarea></div></div></div>`;
}
function paperFeedback(check){
 $('#word-count').textContent=`${check.word_count} 字 · 占 ${check.used_rows}/${state.question.paper_spec.rows} 行`;
 $('#paper-usage').textContent=`已占 ${check.occupied_cells}/${state.question.paper_spec.cells} 格 · 缩进及换行空耗 ${check.formatting_cells} 格`;
 const warning=$('#paper-warning');warning.hidden=check.valid;warning.textContent=check.reasons.join('；')+'。'+(check.overflow_cells?'红色超出区域仅供编辑，不属于本题答题纸容量。':'')+'可提交批改，系统会带上实际超限情况。';
}
let phoneInbox=null;
function applyPhoneCapture(record,operation,auto=false){
 const input=$('#answer');if(!input||state.question.id!==record.question_id||paperEditorInstance?.composing)throw Error('请完成当前输入法选字后再录入手机文字');
 const old=input.value,previousFocus=document.activeElement;
 try{localStorage.setItem('phone-draft-backup-'+state.question.id,JSON.stringify({text:old,at:new Date().toISOString()}));}catch{throw Error('本地空间不足，未替换草稿；手机文字仍保存在录入记录中');}
 const append=operation==='append';paperEditorInstance.insert((append&&old&&!old.endsWith('\n')?'\n':'')+record.text,append?old.length:0,old.length);
 $('#draft-status').textContent='已录入手机文字并保存草稿';
 if(auto&&previousFocus&&previousFocus!==input)previousFocus.focus({preventScroll:true});
 $('#phone-restore-draft').hidden=false;
}
function mountPractice(){
 practiceTimer=new PracticeTimer.Timer({questionId:state.question.id,element:$('#practice-stopwatch')});
 paperEditorInstance=new GridPaper.Editor({spec:PaperLayout.specFor(state.question),onChange:paperFeedback,toast});
 materialHighlighter=new MaterialHighlighter({element:$('#question-materials'),question:state.question,paperId:state.paper.id,api,toast});
 phoneInbox=new PhoneCaptureInbox({questionId:state.question.id,signature:state.question.mobile_input_signature,api,apply:applyPhoneCapture,canAutoApply:()=>!paperEditorInstance?.composing&&!$('#answer').value.trim(),toast});
 $('#phone-restore-draft').hidden=!localStorage.getItem('phone-draft-backup-'+state.question.id);
}
async function practice(pid,qid){
 const data=await api('/papers/'+pid+'/questions/'+qid),p=data.paper,q=data.question;state.paper=p;state.question=q;
 const selected=JSON.parse(localStorage.getItem('selected-models')||'[]');
 const draft=localStorage.getItem('draft-'+qid)??'　　';
 return head('专注这一份作答',sourceText(p)+' · 第 '+q.number+' 题',`<div class="practice-stopwatch" id="practice-stopwatch"><span>本题用时 <output aria-label="本题累计用时">00:00:00</output></span><span data-timer-status>正在计时</span><label>无操作<select aria-label="自动暂停时长"><option value="3">3分钟</option><option value="5" selected>5分钟</option><option value="10">10分钟</option><option value="15">15分钟</option></select>暂停</label><button type="button" class="btn small" data-timer-reset>重新计时</button></div>`+btn('toggle-focus','专注作答','','aria-pressed="false"')+`<a class="btn" href="#paper/${pid}">返回试卷</a>`,'WRITING SESSION')+
 `<div class="practice-grid"><section class="card reading-pane"><div class="card-head"><h2>题目与材料</h2>${badge(kinds[q.kind]+' · '+full[q.kind]+' 分')}</div><div class="pane-scroll"><div class="prose question-stem reading-text">${readingText(q.prompt)}</div><div class="notice ${q.material_scope.needs_review?'warn':''} section-gap">${esc(q.material_scope.note)} · 本题材料 ${q.material_scope.character_count} 字符${q.material_scope.saved_percent?' · 较整卷材料减少 '+q.material_scope.saved_percent+'% 文字':''}${q.material_scope.needs_review?' · 可在试卷校对中调整':''}</div><section class="material-section"><p id="highlight-count">选中文字后可高亮，默认亮黄色</p><div class="material reading-text" id="question-materials">${readingText(q.materials)}</div></section></div></section><section class="card writing-pane"><div class="card-head"><h2>我的答案</h2><span class="subtitle">自动保存到当前浏览器</span></div><div id="phone-capture-inbox" aria-live="polite"></div><button id="phone-restore-draft" type="button" class="btn small" data-action="restore-phone-draft" hidden>恢复上次手机录入前的草稿</button><form id="answer-form" class="answer-form">${paperEditorHtml(q,draft)}<div class="answer-foot"><span id="draft-status">${draft.trim()?'已恢复本地草稿':'尚未输入'}</span><div class="paper-counts"><span id="word-count"></span><span class="paper-hint" id="paper-usage"></span></div></div><p class="paper-warning" id="paper-warning" hidden></p><div class="answer-controls"><details class="grading-settings"><summary>选择批改模型 <span class="muted small" id="grading-summary">· 已选 ${state.providers.filter(p=>selected.includes(p.id)||(!selected.length&&state.providers[0].id===p.id)).length} 位教练</span></summary><div class="grading-settings-body"><h3>独立批改的教练</h3><p class="tiny muted">可多选 · 同一份答案并行批改，最多同时请求 4 个接口</p><div class="model-select">${state.providers.length?state.providers.map(p=>`<label class="check-pill"><input type="checkbox" name="provider" value="${p.id}" ${selected.includes(p.id)||(!selected.length&&state.providers[0].id===p.id)?'checked':''}>${esc(p.name)}</label>`).join(''):'<a class="link" href="#models">先添加你的第一个模型接口 →</a>'}</div><label class="field">主模型 · 汇总诊断与历史画像<select name="master" required>${modelOptions(localStorage.getItem('master')||'')}</select></label><p class="tiny muted">批改仅发送本题所选材料、题干、你的答案与评分规则；批改成功后，主模型还会基于全部历史摘要更新画像。按接口实际计费。</p></div></details><div class="submit-row">${btn('submit-answer',`${icon('check')} 提交并开始批改`,'primary full-width',state.providers.length?'type="submit"':'disabled type="submit"')}</div></div></form></section></div>`;
}
async function history(){
 const rows=await api('/attempts');state.history=rows;
 return head('练习历史','当时的答案、每位教练的评价，每一次都为你保留。',`<a class="btn" href="/api/export">${icon('export')} 导出全部记录</a>`,'PRACTICE HISTORY')+
 `<div class="filter-row"><select id="history-filter" class="filter" aria-label="筛选题型">${option('','全部题型')}${Object.entries(kinds).map(([k,v])=>option(k,v)).join('')}</select><span class="small muted">${rows.length} 份作答记录</span></div><div class="card table-wrap" id="history-list">${historyTable(rows)}</div>`;
}
function historyTable(rows){return rows.length?`<table><thead><tr><th>时间</th><th>来源与题目</th><th>训练档</th><th>均分</th><th>状态</th><th></th></tr></thead><tbody>${rows.map(a=>`<tr><td>${esc(date(a.created_at))}</td><td><div class="source">${esc(a.source.title)}</div><span class="tiny muted">第 ${a.question_number} 题 · ${a.answer_length} 字</span></td><td>${badge(kinds[a.kind])}</td><td><b>${a.mean_score??'—'}</b><span class="muted tiny"> / ${full[a.kind]}</span></td><td>${badge(statuses[a.status],a.status==='completed'?'':a.status==='running'?'blue':'amber')}</td><td><a class="link" href="#attempt/${a.id}">查看 →</a></td></tr>`).join('')}</tbody></table>`:empty('还没有练习记录','开始一次作答，让努力留下痕迹。','<a class="btn small" href="#papers">前往真题库</a>');}
async function attemptView(id){
 const token=state.routeToken,a=await api('/attempts/'+id);
 if(token!==state.routeToken)return '';
 if(state.attempt?.id!==id){state.reviewProvider=null;state.referenceVersions={};}
 state.attempt=a;return renderAttempt(a);
}
function renderAttempt(a){
 const q=a.question_snapshot,good=a.providers.map(p=>a.grades.find(g=>g.provider_id===p.id&&g.status==='completed')).filter(Boolean);
 const activeReview=good.find(g=>g.provider_id===state.reviewProvider)||good[0];
 if(activeReview)state.reviewProvider=activeReview.provider_id;
 const busy=['queued','running'].includes(a.status);
 return '<div id="attempt-progress">'+head('这一次，离更好的自己近一点',sourceText(q.source)+' · 第 '+q.number+' 题 · '+date(a.created_at),`<a class="btn" href="#history">返回历史</a>${!busy&&a.status!=='completed'?btn('retry',a.status==='summary_failed'&&good.length===a.providers.length?'重试汇总':'重试失败项','primary'):''}`,'REVIEW & REFLECTION')+
 (busy?'<div class="notice"><span class="busy-dot"></span>'+esc(a.grading_stage||'各教练批改中')+'。已返回的评分可先阅读。你可以离开此页，后台继续执行。</div>':'')+
 (!a.grading_policy||a.grades.some(g=>g.status==='completed'&&!g.grading_policy)?'<div class="notice warn">此记录使用旧版批改提示。若评价与题干不符，可保留本记录，并回原题使用原答案重新提交批改。</div>':'')+
 (q.kind==='essay'&&good.some(g=>!g.calibration_policy)?'<div class="notice warn">本记录含未使用新版大作文分档标准的评分。新标准用于后续批改；单独重写范文不会改变原评分。</div>':'')+
 (q.kind==='practical'&&good.some(g=>g.calibration_policy!=='practical-v1')?'<div class="notice warn">本记录含未使用新版应用文分档标准的评分。新标准用于后续批改；单独重写范文不会改变原评分。</div>':'')+
 (a.error?`<div class="notice error">${esc(a.error)}</div>`:'')+retryHistory(a)+
 `<div class="score-cards"><div class="score-card"><p>有效批改均分</p><div class="score">${a.mean_score??'—'}<small> / ${full[q.kind]}</small></div><span class="tiny muted">${good.length} / ${a.providers.length} 个模型成功 · 分差 ${a.score_spread??'—'}</span></div>${a.providers.map(p=>coachScoreCard(a,p,busy)).join('')}</div>`+
 (a.status==='partial'?'<div class="notice warn">仅成功模型参与均分。失败项未被算作零分；可重试失败项，成功项不会重复调用。</div>':'')+
 `</div><div class="grid-main"><div class="stack"><section class="card" id="attempt-synthesis"><div class="card-head"><h2>主模型 · 汇总诊断</h2>${badge(a.master.name)} <span class="tiny muted">${esc(a.master.model)}</span></div>${!busy&&a.summary_status==='failed'?btn('replace-master','更换汇总接口','small section-gap'):''}${a.synthesis?`<div class="prose">${esc(a.synthesis.analysis)}</div><div class="grade-section"><h3>教练们的共识</h3>${list(a.synthesis.consensus)}<h3>需要辨别的分歧</h3>${a.synthesis.disagreements.length?list(a.synthesis.disagreements):'<p class="small muted">本次未报告明显分歧。</p>'}<h3>下一次，就这样改</h3>${list(a.synthesis.action_plan)}</div>`:empty(busy?'诊断正在路上':'尚无汇总诊断',busy?'先认真写，再一起看清得失。':'请检查失败原因后重试。')}</section></div><section class="card"><div class="card-head"><h2>当时的原答案</h2>${badge(kinds[q.kind])}</div><div class="prose">${esc(a.answer)}</div><details class="plain-details"><summary>查看本次题干与材料快照</summary><div class="prose">${esc(q.prompt)}</div><h3 class="section-gap">${esc(q.material_scope?.note||'当时存档的材料')}</h3><div class="material reading-text">${readingText(q.materials)}</div></details><a class="btn small section-gap" href="#practice/${a.paper_id}/${a.question_id}">再练一次 ${icon('arrow')}</a></section></div>`+
 `<section class="card section-gap"><div class="card-head"><h2>每位教练的详细批改与参考范文</h2><span class="subtitle">AI 生成 · 非官方标准答案</span></div><div id="review-tabs" class="tabs">${good.map(g=>`<button data-action="review-tab" data-id="${g.provider_id}" class="${g.provider_id===activeReview?.provider_id?'active':''}">${esc(g.name)} · ${g.result.score} 分</button>`).join('')}</div><div id="review-body">${activeReview?reviewBody(activeReview):empty('等待第一份批改结果','各模型的评分、证据与范文会在这里分别呈现。')}</div></section>`;
}
const coachStages={queued:'排队等待接口',requesting:'批改请求中',validating:'校验评分',reference_repair:'评分已返回 · 范文校正中',completed:'已完成',failed:'失败',interrupted:'已中断'};
function duration(seconds){seconds=Math.max(0,Math.floor(seconds));return seconds<60?seconds+'秒':Math.floor(seconds/60)+'分'+seconds%60+'秒';}
function updateCoachClocks(){
 $$('[data-clock-start]').forEach(node=>{
  const start=Date.parse(node.dataset.clockStart),end=node.dataset.clockEnd?Date.parse(node.dataset.clockEnd):Date.now();
  node.textContent=Number.isFinite(start)&&Number.isFinite(end)?(node.dataset.clockEnd?'总用时 ':'本阶段已等待 ')+duration((end-start)/1000):'';
 });
}
setInterval(updateCoachClocks,1000);
function retryHistory(a){
 if(!a.retry_history?.length||['queued','running'].includes(a.status))return '';
 return `<details class="plain-details"><summary>重试与接口更换记录</summary>${a.retry_history.map(item=>`<p class="small">${esc(date(item.at))} · ${item.role==='master'?'汇总接口':'失败批改项'}：${esc(item.previous.name)}（${esc(item.previous.model)}） → ${esc(item.replacement.name)}（${esc(item.replacement.model)}）${item.failed_grade?.error?'<br>'+esc('上次失败：'+item.failed_grade.error):''}</p>`).join('')}</details>`;
}
async function retryEditor(providerId=null){
 state.providers=await api('/providers');
 state.retryProvider=providerId;state.retryAttemptId=state.attempt.id;
 const a=state.attempt, old=providerId?a.providers.find(p=>p.id===providerId):a.master;
 const choices=state.providers.filter(p=>!providerId||!a.providers.some(other=>other.id===p.id&&other.id!==providerId));
 const preferred=choices.find(p=>p.id!==old.id)||choices[0];
 showDialog(`<div class="dialog-head"><h2>${providerId?'更换接口并补批':'更换汇总接口'}</h2>${btn('close-dialog',icon('close'),'small','aria-label="关闭"')}</div><p>原接口：${esc(old.name)} · ${esc(old.model)}</p><p class="small section-gap">${providerId?'仅补批这一项，原答案和其他成功评分保留。结果将标注实际使用的新模型。':'只重新生成汇总，不重复调用已完成的批改模型。'}${providerId&&a.master.id===providerId?' 原接口也负责汇总，将一并使用新接口。':''}</p>${choices.length?`<form id="retry-form"><label class="field section-gap">${providerId?'补批接口':'汇总接口'}<select name="replacement" required>${choices.map(p=>option(p.id,p.name+' · '+p.model,p.id===preferred?.id)).join('')}</select></label><button type="submit" class="btn primary section-gap">${providerId?'开始单独补批':'重新汇总'}</button></form>`:`<p class="notice section-gap">暂无可替换的接口，请先添加新的模型接口。</p>${btn('retry-add-model','前往模型设置','primary')}`}`);
}
function coachScoreCard(a,p,busy){
 const g=a.grades.find(g=>g.provider_id===p.id),completed=g?.status==='completed';
 const retrying=busy&&(a.retry_provider_ids||[]).includes(p.id);
 const retryable=!completed&&(!busy||g?.status==='failed'||retrying);
 const status=coachStages[g?.stage]||(completed?'已完成':retrying?'正在重试':g?.status==='failed'?'失败':busy?'等待中':'未完成');
 const config=state.providers.find(item=>item.id===p.id)||p;
 const reconfigured=!completed&&state.providers.some(item=>item.id===p.id)&&['name','model','protocol','base_url'].some(key=>config[key]!==p[key]);
 const currentLimit=config.max_tokens||6000;
 const suggested=[32768,65536,131072].find(value=>value>currentLimit&&value>=Math.min(131072,Math.max(32768,currentLimit*2)));
 const truncated=g?.error_code==='output_truncated'||g?.error?.includes('输出被截断');
 const response=g?.request_started_at&&g?.response_received_at?'<p class="tiny muted">批改响应 '+duration((Date.parse(g.response_received_at)-Date.parse(g.request_started_at))/1000)+'</p>':'';
 const clock=g?.stage_started_at?`<p class="tiny muted" data-clock-start="${esc(g.finished_at?(g.queued_at||g.stage_started_at):g.stage_started_at)}" data-clock-end="${esc(g.finished_at||'')}"></p>`:'';
 const usage=g?.usage?`<p class="tiny muted">${g.usage.input_tokens!=null?'输入 '+g.usage.input_tokens+' tokens'+(g.usage.cached_tokens!=null?'（缓存 '+g.usage.cached_tokens+'）':'')+' · ':''}${g.usage.output_tokens!=null?'输出 '+g.usage.output_tokens+' tokens':''}${g.usage.reasoning_tokens!=null?'（含思考 '+g.usage.reasoning_tokens+'）':''}</p>`:'';
 return `<div class="score-card" data-provider="${esc(p.id)}"><p>${esc(p.name)}</p>${completed?`<div class="score">${g.result.score}<small> / ${full[a.question_snapshot.kind]}</small></div>`:''}${badge(status,g?.status==='failed'&&!retrying?'red':'blue')}${clock}${response}${usage}<span class="tiny muted">${esc(p.model)}</span>${reconfigured?`<p class="small">当前接口已更新为 ${esc(config.name)} · ${esc(config.model)}，单独重试将使用此配置。</p>`:''}${g?.error&&!retrying?`<p class="small">${esc(g.error)}</p>`:''}${retryable?`<div class="section-gap"><button type="button" class="btn small" data-action="retry-coach" data-provider="${esc(p.id)}" ${busy?'disabled title="本次任务仍在执行，完成后可单独重试"':''}>${retrying?'正在重试':busy?'稍后重试':'单独重试'}</button><button type="button" class="btn small section-gap" data-action="replace-coach" data-provider="${esc(p.id)}" ${busy?'disabled':''}>更换接口重试</button>${busy&&!retrying?'<p class="tiny muted">本次任务完成后可重试</p>':''}${truncated&&suggested?`<button type="button" class="btn small section-gap" data-action="boost-retry" data-provider="${esc(p.id)}" data-limit="${suggested}" ${busy?'disabled':''}>提高至 ${suggested} 并重试</button><p class="tiny muted">请确认服务商支持此输出上限</p>`:''}</div>`:''}</div>`;
}
function calibrationHint(g){
 const text={'essay-v1':'大作文训练校准：30分一般，33分二类起点，35分二类中上，36–38分高水平；超过38分须有充分依据。此为自定训练标准。','practical-v1':'应用文30分训练校准：17–19分中等较差，20–22分中等，23–25分中等偏上；25分以上须有特别优秀的原答证据。按本题要点给分，具体要求依题干文体，分档不设保底或封顶。此为自定训练标准。'}[g.calibration_policy];
 return text?`<p class="paper-hint">${text}</p>`:'';
}
function reviewBody(g){const r=g.result;return `${calibrationHint(g)}<div class="row between wrap"><h3>${esc(g.name)} <span class="tiny muted">${esc(g.model)}</span></h3><span class="tiny muted">范文来自此模型，未由主模型改写</span></div>${r.task_analysis?`<div class="paper-check section-gap"><h3>本题审题 · ${esc(r.task_analysis.genre)}</h3><p>${esc(r.task_analysis.task)}</p><h4>题干要求</h4>${list(r.task_analysis.requirements)}<h4>本题材料依据</h4>${list(r.task_analysis.material_basis)}</div>`:'<p class="paper-hint section-gap">模型未提供有效审题分析，请结合题干与评分证据核对。</p>'}<div class="row wrap section-gap">${dimensions.map(k=>badge(k+' '+r.dimensions[k])).join(' ')}</div><div class="grid-main grade-section"><div><h3>值得保持</h3>${list(r.strengths)}<h3>不足与扣分依据</h3>${list(r.weaknesses)}${list(r.evidence)}</div><div><h3>具体改法</h3>${list(r.suggestions)}</div></div>${referenceBody(g)}`;}
function referencePlanHtml(g,ref,running){
 const plan=ref.plan||(ref.id==='original'?g.result.high_score_plan:null);
 return `<section class="reference-plan section-gap"><div class="row between wrap"><h4>范文构思说明 · ${esc(g.name)}</h4><button type="button" class="btn small" data-action="explain-reference" data-provider="${g.provider_id}" data-reference="${ref.id}" ${running?'disabled':''}>${plan?'重新生成说明':'生成构思说明'}</button></div><p class="tiny muted">对应当前范文版本，不计入范文字数与格纸；单独生成说明不重写范文或评分。</p>${plan?`<h5>论点框架</h5><p class="prose">${esc(plan.framework)}</p><h5>层次递进与串联</h5><p class="prose">${esc(plan.progression)}</p><h5>材料运用与取舍</h5>${list(plan.material_use)}`:'<p class="small muted">本版本未提供有效构思说明，可点击按钮单独补充。</p>'}</section>`;
}
function referencePriorityHtml(ref){
 const p=ref.material_priority;if(!p)return '';
 const preferred=p.preferred_ids||[],secondary=p.secondary_ids||[],unknown=p.unresolved_prior_questions||[];
 let text=!p.available?'材料范围未能可靠对应，未自动降权；可在校对试卷中确认各题材料范围。':preferred.length?`优先材料 ${preferred.join('、')} 构思论点与主要案例；${secondary.length?'材料 '+secondary.join('、')+' 仅在必要时辅助补用。':'未发现与已确认前题范围重叠。'}`:'前题涉及本题全部可识别材料，按论点和论证需要正常取材。';
 if(unknown.length)text+=` 第 ${unknown.join('、')} 题范围未能可靠映射，未据此排除材料。`;
 if(p.source==='current_paper')text+=' 本版本依据当前试卷校对范围设置优先级。';
 return `<p class="paper-hint reference-material-priority"><b>大作文取材偏好：</b>${esc(text)}不改变原答评分。</p>`;
}
function referenceBody(g){
 const refs=g.references||[{id:'original',answer:g.result.high_score_answer,model_self_check:g.result.high_score_self_check,direction:'initial',created_at:state.attempt.created_at}];
 const selected=state.referenceVersions[g.provider_id];const ref=refs.find(r=>r.id===selected)||refs.at(-1);
 const spec=ref.paper_spec||state.attempt.paper_spec||PaperLayout.specFor(state.attempt.question_snapshot),check=ref.layout_check||PaperLayout.check(ref.answer,spec),self=ref.model_self_check;
 const running=state.jobs.some(j=>j.kind==='reference'&&j.target_id===state.attempt.id&&['queued','running'].includes(j.status));
 const mismatch=self&&((self.word_count!=null&&self.word_count!==check.word_count)||(self.used_rows!=null&&self.used_rows!==check.used_rows));
 const selfCount=self?[(self.word_count!=null?self.word_count+' 字':''),(self.used_rows!=null?self.used_rows+' 行':'')].filter(Boolean).join(' / '):'';
 return `<h3>高分参考答案 · ${esc(g.name)}</h3><div class="paper-check ${check.valid?'':'invalid'} section-gap"><b>程序占格校验：${check.valid?'符合字数及纸面限制':'需要修订'}</b><br>${check.word_count} 字 · 占用 ${check.used_rows}/${spec.rows} 行（每行25格） · 含缩进和换行空耗 ${check.formatting_cells} 格${check.reasons.length?'<br>'+check.reasons.map(esc).join('；'):''}<br><span class="small">模型自检：${self?`${selfCount?selfCount+' · ':'计数由程序核验 · '}${self.complies?'自报符合':'自报需修订'} · ${esc(self.format_note||'已返回自检')}`:'未提供有效自检，以上为程序按本软件格纸规则计算'}${mismatch?' · 模型计数与程序不同，以程序核验为准':''}</span>${ref.repair_error?'<br>'+esc('自动修订未完成：'+ref.repair_error):''}</div>${ref.style_check?`<div class="paper-check ${ref.style_check.valid?'':'invalid'} section-gap"><b>${ref.style_check.policy==='essay-v1'?'大作文':'短评'}形式检查：${ref.style_check.valid?'通过':'需要修订'}</b><p>正文 ${ref.style_check.body_paragraphs} 段 · ${ref.style_check.quantities.length?'检测到数量表达':'未检出明显数量表达'}</p>${ref.style_check.reasons.map(reason=>`<p>${esc(reason)}</p>`).join('')}<p class="small">段首观点、案例概括和材料依据请结合模型自检与原材料核对。</p></div>`:''}<div class="reference-actions"><button type="button" class="btn" data-action="regenerate-reference" data-direction="rewrite" data-provider="${g.provider_id}" data-reference="${ref.id}" ${running?'disabled':''}>重新生成范文</button><button type="button" class="btn" data-action="regenerate-reference" data-direction="more" data-provider="${g.provider_id}" data-reference="${ref.id}" ${running?'disabled':''}>字数少了</button><button type="button" class="btn" data-action="regenerate-reference" data-direction="less" data-provider="${g.provider_id}" data-reference="${ref.id}" ${running?'disabled':''}>字数多了</button><label class="small">版本 <select class="reference-version" data-provider="${g.provider_id}" aria-label="范文版本">${refs.map((v,i)=>option(v.id,(i===0?'原始范文':v.direction==='more'?'补充字数':v.direction==='less'?'压缩字数':v.direction==='rewrite'?'重新生成':'自动修订')+' · '+date(v.created_at),v.id===ref.id)).join('')}</select></label></div><p class="paper-hint">${running?(state.jobs.some(j=>j.kind==='reference'&&j.subtype==='plan'&&j.target_id===state.attempt.id&&['queued','running'].includes(j.status))?'正在生成构思说明，请稍候。':'正在单独生成范文，请稍候。'):'仅重新生成此模型的范文，不改分数和批评意见；字数、纸面或范文形式不符时最多自动修订1次，按接口实际计费。'}</p>${referencePriorityHtml(ref)}<div class="reference-layout"><section><h4>范文原文</h4><div class="answer-reference prose">${esc(ref.answer)}</div></section><section class="reference-preview" data-provider="${g.provider_id}" data-reference="${ref.id}"><h4>25格纸面 · 当前范文</h4><div class="reference-paper-target"></div></section></div>${referencePlanHtml(g,ref,running)}`;
}
const liveMarkup=new Map();
const liveRegions=['attempt-progress','attempt-synthesis','review-tabs','review-body'];
function mountReferencePapers(){
 $$('.reference-preview').forEach(panel=>{
  const target=$('.reference-paper-target',panel);if(!target||target.childNodes.length)return;
  const g=state.attempt?.grades.find(g=>g.provider_id===panel.dataset.provider);
  const refs=g?.references||[];const ref=refs.find(r=>r.id===panel.dataset.reference);
  if(ref)target.append(GridPaper.preview(ref.answer,ref.paper_spec||state.attempt.paper_spec));
  else if(g)target.append(GridPaper.preview(g.result.high_score_answer,state.attempt.paper_spec));
 });
}
function updateLiveRegion(id,html){
 const node=document.getElementById(id);if(!node)return;
 const template=document.createElement('template');template.innerHTML=html;const canonical=template.innerHTML;
 if(liveMarkup.get(id)===canonical)return;
 node.replaceChildren(template.content);liveMarkup.set(id,canonical);
}
function setReviewBody(g){updateLiveRegion('review-body',reviewBody(g));mountReferencePapers();}
function primeAttempt(){
 liveMarkup.clear();for(const id of liveRegions){const node=document.getElementById(id);if(node)liveMarkup.set(id,node.innerHTML);}
 mountReferencePapers();updateCoachClocks();
}
let attemptRefreshToken=0;
async function refreshAttempt(){
 const hash=location.hash,token=state.routeToken,id=hash.slice(1).split('/')[1];
 if(!hash.startsWith('#attempt/')||state.attempt?.id!==id||!$('#attempt-progress'))return;
 const refresh=++attemptRefreshToken,a=await api('/attempts/'+id);
 if(hash!==location.hash||token!==state.routeToken||refresh!==attemptRefreshToken)return;
 state.attempt=a;const template=document.createElement('template');template.innerHTML=renderAttempt(a);
 for(const id of liveRegions){const node=template.content.querySelector('#'+id);if(node)updateLiveRegion(id,node.innerHTML);}
 mountReferencePapers();updateCoachClocks();
}
async function models(){return head('找到适合你的 AI 教练','连接不同视角，让每一份评价都经得起对照。',btn('add-model',`${icon('plus')} 添加接口`,'primary'),'MODEL CONNECTIONS')+
 '<div class="notice">支持任意数量的接口配置：OpenAI 兼容（含 DeepSeek、通义、硅基流动与 Ollama）、Anthropic Claude、Google Gemini。API Key 在服务器本地加密保存，浏览器只能看到是否已设置。</div>'+
 `<div class="model-grid">${state.providers.map((p,i)=>`<article class="card model-card"><div class="row between"><span class="model-logo">${['◈','✳','◇','✦'][i%4]}</span>${badge(p.has_key?'密钥已设置':'未设密钥',p.has_key?'':'amber')}</div><h2>${esc(p.name)}</h2><div class="model-name">${esc(p.model)}</div><div class="endpoint">${esc(p.base_url)}<br>${esc(p.protocol)} · 输出上限 ${p.max_tokens} tokens · 等待时限 ${p.timeout_seconds??600} 秒</div><div class="row wrap">${btn('test-model','测试连接','small',`data-id="${p.id}"`)}${btn('edit-model','编辑','small',`data-id="${p.id}"`)}${btn('delete-model','删除','small danger',`data-id="${p.id}"`)}</div></article>`).join('')}<button class="upload-card" data-action="add-model">${icon('plus')}<span>添加一位 AI 教练</span><small>同一厂商的不同模型也可分别添加</small></button></div><section class="card section-gap"><h3>地址填写参考</h3><p class="prose small section-gap">OpenAI：https://api.openai.com/v1　·　DeepSeek：https://api.deepseek.com/v1<br>Claude：https://api.anthropic.com/v1　·　Gemini：https://generativelanguage.googleapis.com/v1beta<br>智谱官方：https://open.bigmodel.cn/api/paas/v4（OpenAI 兼容，自动使用 max_tokens）<br>Mac 上的 Ollama：http://host.docker.internal:11434/v1（协议选择 OpenAI 兼容，密钥可留空）<br>第三方服务请填写其文档给出的 API 根地址，不要重复添加 /chat/completions、/messages。</p></section>`;}
async function profile(){const s=await api('/profile'),p=s.portrait;return head('看清自己，然后继续向上','六个维度，三类题型，一条属于你的成长路线。',state.providers.length?btn('refresh-profile','重新生成要点','primary'):'','ABILITY & GROWTH')+
 `<div class="grid-main"><section class="card"><div class="card-head"><h2>六维能力画像</h2>${badge(s.graded_count+' 次有效练习')}</div>${renderRadar(s.dimensions)}<p class="radar-note">全部有效作答的等权均分 · 每个维度满分 100</p></section><section class="card"><div class="card-head"><h2>浙江省考三题预估</h2>${badge('训练估计','amber')}</div><div class="forecast-total">${s.estimated_total??'—'} <small>/ 100</small></div><p class="small muted section-gap">${s.estimated_total==null?'尚缺 '+Object.values(s.forecast).filter(f=>f.score==null).map(f=>f.label).join('、')+' 的有效练习，暂不预估总分。':'按各题型最近 5 次有效作答的均分相加。'}</p><div class="level-box section-gap"><div class="row between"><b>Lv. ${s.level} · 稳步积累</b><span>${s.xp} XP</span></div><progress class="progress" value="${s.level_progress}" max="100"></progress><div class="tiny muted">新答案有效批改 +25 XP，相同题目与相同答案不重复奖励。</div></div></section></div>`+
 `<div class="forecast section-gap">${Object.values(s.forecast).map(f=>`<div class="score-card"><div><p>${esc(f.label)}</p><div class="score">${f.score??'—'}<small> / ${f.full_score}</small></div></div><span class="tiny muted">累计 ${f.count} 次 · 计入最近 ${f.recent_count} 次</span></div>`).join('')}</div><div class="notice section-gap">${esc(s.note)} 等级只体现练习积累，不代表竞争排名。建议使用同一组模型观察长期趋势。</div>`+
 `<section class="card section-gap"><div class="card-head"><h2>AI 的长期观察 · 要点</h2>${p?badge(p.name+' · '+p.history_count+' 份历史'):''}</div>${s.portrait_stale?'<div class="notice warn">历史记录有更新，这份文字诊断尚未同步；数值指标已实时更新。</div>':''}${p&&!s.portrait_needs_refresh?`<p class="tiny muted">更新于 ${esc(date(p.updated_at))} · ${esc(p.model)}</p><div class="portrait-verdict"><h3>当前结论</h3><p class="prose">${esc(p.result.analysis)}</p></div><div class="portrait-notes"><section><h3>已经做得不错</h3>${list(p.result.strengths)}</section><section><h3>优先补什么</h3>${list(p.result.weaknesses)}</section><section><h3>下一次怎么练</h3>${list(p.result.action_plan)}</section></div>`:p?`<div class="notice section-gap">这份观察是旧版长报告，请点击“重新生成要点”，按新版要求重新总结。${state.providers.length?btn('refresh-profile','重新生成要点','primary section-gap'):''}</div>`:empty('还没有训练观察','完成有效批改后，将生成简短的能力结论和训练动作。')}<details class="plain-details"><summary>查看最近的画像任务</summary>${state.jobs.filter(j=>j.kind==='profile').slice(0,3).map(j=>`<p class="small muted">${esc(date(j.created_at))} · ${esc(statuses[j.status])} ${esc(j.error||j.stage||'')}</p>`).join('')||'<p class="small muted">暂无任务</p>'}</details></section><section class="card section-gap"><div class="card-head"><h2>长期趋势</h2><span class="subtitle">最近 20 次 · 点击节点查看原作答</span></div>${renderTrend(s.timeline)}</section>`;}
const navs=[['overview','学习概览'],['papers','真题练习'],['history','练习历史'],['profile','能力与竞争力'],['models','模型设置'],['phone','手机拍照录入']];
$('#navigation').innerHTML=navs.map(([k,v])=>`<a href="#${k}" data-nav="${k}" aria-label="${v}">${icon(k)}<span>${v}</span></a>`).join('');
function setNavigationOpen(open){
 $('#navigation-drawer').dataset.open=String(open);
 $('#nav-reveal').setAttribute('aria-expanded',String(open));
 $('#nav-reveal').setAttribute('aria-label',open?'收起导航':'展开导航');
}
const navigationDrawer=$('#navigation-drawer');
navigationDrawer.addEventListener('pointerenter',event=>{if(event.pointerType!=='touch')setNavigationOpen(true);});
navigationDrawer.addEventListener('pointerleave',event=>{if(event.pointerType!=='touch')setNavigationOpen(false);});
navigationDrawer.addEventListener('focusin',event=>{if(event.target.matches(':focus-visible'))setNavigationOpen(true);});
navigationDrawer.addEventListener('focusout',()=>queueMicrotask(()=>{if(!navigationDrawer.contains(document.activeElement))setNavigationOpen(false);}));
$('#nav-reveal').addEventListener('click',()=>setNavigationOpen(navigationDrawer.dataset.open!=='true'));
document.addEventListener('pointerdown',event=>{if(!navigationDrawer.contains(event.target))setNavigationOpen(false);});
navigationDrawer.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();$('#nav-reveal').focus();setNavigationOpen(false);}});

async function route(quiet=false){
 phoneInbox?.destroy();phoneInbox=null;practiceTimer?.destroy();practiceTimer=null;paperEditorInstance?.destroy();materialHighlighter?.destroy();paperEditorInstance=null;materialHighlighter=null;
 setFocusMode(false);
 const token=++state.routeToken;
 const [view='overview',id,qid]=(location.hash.slice(1)||'overview').split('/');
 $('#main').dataset.view=view;document.documentElement.dataset.view=view;setNavigationOpen(false);
 const nav=['paper','practice'].includes(view)?'papers':view==='attempt'?'history':view;
 $$('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===nav));
 $('#crumb').textContent=navs.find(n=>n[0]===nav)?.[1]||'学习空间';
 if(!quiet)$('#main').innerHTML='<div class="loading"><span class="busy-dot"></span> 正在打开你的学习空间…</div>';
 try{
  state.providers=await api('/providers');
  const fn={overview, papers:library,paper:()=>paperView(id),practice:()=>practice(id,qid),history,attempt:()=>attemptView(id),models,profile,phone:phoneView}[view]||overview;
  const content=await fn();
  if(token!==state.routeToken)return;
  $('#main').innerHTML=content;
  if(view==='paper'&&state.paper.requires_material_selection&&!(state.promptedScopes||[]).includes(id)){state.promptedScopes=[...(state.promptedScopes||[]),id];paperEditor();}
  if(view==='practice')mountPractice();
  if(view==='attempt')primeAttempt();
  if(!quiet)window.scrollTo(0,0);
 }catch(e){if(token===state.routeToken)$('#main').innerHTML=head('暂时无法打开',e.message,'<a class="btn" href="#overview">回到概览</a>');}
}
window.addEventListener('hashchange',()=>route());
function showDialog(content){$('#editor-content').innerHTML=content;$('#editor').showModal();}
function closeDialog(){$('#editor').close();}
function field(label,name,value='',type='text',attrs=''){return `<label class="field">${label}<input name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;}
function glmReasoning(p){try{return p.protocol==='openai'&&new URL(p.base_url).hostname==='open.bigmodel.cn'&&p.model?.toLowerCase()==='glm-5.3';}catch{return false;}}
function updateReasoningSetting(){
 const form=$('#model-form');if(!form)return;
 const setting=form.querySelector('[name="reasoning_effort"]');if(!setting)return;
 const supported=glmReasoning({protocol:form.elements.namedItem('protocol').value,base_url:form.elements.namedItem('base_url').value,model:form.elements.namedItem('model').value});
 setting.disabled=!supported;setting.closest('label').hidden=!supported;
}
function modelEditor(id){const p=state.providers.find(p=>p.id===id)||{protocol:'openai',max_tokens:32768,timeout_seconds:600};state.editProvider=id;
 showDialog(`<div class="dialog-head"><h2>${id?'编辑模型接口':'连接一位 AI 教练'}</h2>${btn('close-dialog',icon('close'),'small','aria-label="关闭"')}</div><form id="model-form"><div class="field-grid">${field('显示名称','name',p.name||'','text','required maxlength="80"')}<label class="field">接口协议<select name="protocol" id="protocol">${option('openai','OpenAI 兼容',p.protocol==='openai')}${option('anthropic','Anthropic Claude',p.protocol==='anthropic')}${option('gemini','Google Gemini',p.protocol==='gemini')}</select></label><label class="field full">API 根地址<input name="base_url" type="url" value="${esc(p.base_url||'https://api.openai.com/v1')}" required placeholder="https://api.openai.com/v1"></label>${field('模型 ID','model',p.model||'','text','required placeholder="填写服务商提供的准确模型 ID" maxlength="160"')}${field('输出 token 上限','max_tokens',p.max_tokens,'number','min="1000" max="131072" required')}${field('等待时限（秒）','timeout_seconds',p.timeout_seconds??600,'number','min="60" max="1800" step="1" required')}<label class="field full" hidden>思考强度 · 智谱官方 GLM-5.3<select name="reasoning_effort" disabled>${option('default','服务商默认（GLM-5.3 默认为 max）',!p.reasoning_effort||p.reasoning_effort==='default')}${option('low','low · 更快，推理深度可能降低',p.reasoning_effort==='low')}${option('high','high · 兼顾速度与推理',p.reasoning_effort==='high')}${option('max','max · 更充分的思考',p.reasoning_effort==='max')}</select><span class="tiny muted">仅该官方模型启用；保持默认不会改变当前思考强度，其他接口不发送此参数。</span></label><label class="field full">API Key ${id?'· 留空保留当前密钥':''}<input name="api_key" type="password" autocomplete="new-password" placeholder="${p.has_key?'已加密保存，留空不修改':'仅发送到你配置的接口'}"></label></div><div class="notice">接口地址和密钥需由你向服务商申请。每次请求默认最多等待600秒，可为慢模型设置60–1800秒；新接口默认输出上限32768 tokens，可设1000–131072；已有配置保留。提高上限须以服务商支持值为准，思考token也可能占用输出预算。使用本地模型时密钥可留空。</div><button type="submit" class="btn primary">保存接口</button></form>`);updateReasoningSetting();
}
function questionEditor(q={number:1,kind:'summary',prompt:'',word_min:0,word_max:0,classification_note:'人工校对'}){
 const mode=q.material_scope?.requires_choice?'':(q.material_mode||'auto');
 const ids=q.material_ids?.length?q.material_ids:(q.material_scope?.requires_choice?[]:(q.material_scope?.matched_ids||[]));
 const blocks=state.paper?.material_blocks||[];
 return `<div class="edit-q"><div class="row between"><h3>题目 ${esc(q.number)}</h3>${btn('remove-question','移除','small danger','type="button"')}</div><div class="field-grid">${field('题号','number',q.number,'number','required min="1" max="100"')}<label class="field">训练档<select name="kind">${Object.entries(kinds).map(([k,v])=>option(k,v+' · '+full[k]+' 分',k===q.kind)).join('')}</select></label>${field('最多字数（题干未注明时使用）','word_max',q.word_max,'number','min="0" max="5000"')}${field('最少字数','word_min',q.word_min,'number','min="0" max="5000"')}${field('答题纸行数（0自动，每行25格）','paper_rows',q.paper_rows||0,'number','min="0" max="400"')}</div><label class="field">题干与原始作答要求<textarea name="prompt" required maxlength="16000">${esc(q.prompt)}</textarea></label>${field('归类说明','classification_note',q.classification_note)}<label class="field">本题使用的材料<select name="material_mode" required>${mode===''?'<option value="" selected disabled>请选择材料范围或全部材料</option>':''}${option('auto','自动按题干引用匹配',mode==='auto')}${option('selected','手动选择资料编号',mode==='selected')}${option('all','全部材料',mode==='all')}${option('custom','人工粘贴 / 校对本题材料',mode==='custom')}</select></label><p class="tiny muted">${esc(q.material_scope?.note||'保存时将按题干重新匹配材料')}。自动匹配支持“资料3”“资料8、9”“材料一至三”等写法。</p><div data-material-selection ${mode==='selected'||mode===''?'':'hidden'}>${blocks.length?`<div class="row wrap"><label class="field">从材料<select data-range-start>${blocks.map(b=>option(b.id,b.title,false)).join('')}</select></label><label class="field">到材料<select data-range-end>${blocks.map(b=>option(b.id,b.title,false)).join('')}</select></label>${btn('apply-material-range','使用此范围','small','type="button"')}</div>`:''}<div class="model-select">${blocks.map(b=>`<label class="check-pill"><input type="checkbox" name="material_id" value="${b.id}" ${ids.includes(b.id)?'checked':''}>${esc(b.title)} · ${b.text.length} 字符</label>`).join('')||'<p class="small muted">未识别出编号段落，请选择人工粘贴材料。</p>'}</div></div><label class="field section-gap" data-custom-materials ${mode==='custom'?'':'hidden'}>仅用于本题的材料<textarea name="custom_materials" maxlength="160000">${esc(q.custom_materials||'')}</textarea></label></div>`;
}
function paperEditor(){const p=state.paper;showDialog(`<div class="dialog-head"><h2>校对试卷与训练档</h2>${btn('close-dialog',icon('close'),'small','aria-label="关闭"')}</div><form id="paper-form"><label class="field">试卷名称<input name="title" value="${esc(p.title)}" required maxlength="180"></label><div class="field-grid">${field('年份','year',p.year)}${field('省份','province',p.province)}${field('卷别','volume',p.volume)}</div><div id="questions-editor">${p.questions.map(questionEditor).join('')}</div>${btn('add-question','添加题目','small','type="button"')}<details class="plain-details"><summary>校对给定材料</summary><label class="field"><textarea name="materials" maxlength="160000">${esc(p.materials)}</textarea></label></details><div class="notice section-gap">训练分值固定为 20 / 30 / 50；原题分数仅保留在题干作为来源。请核对原题文体与字数，避免识别错误影响批改。已有练习历史保留提交时的快照。</div><label class="check-pill"><input type="checkbox" name="confirmed" ${p.confirmed?'checked':''}>我已核对题目、材料和来源，允许开始作答</label><button type="submit" class="btn primary section-gap">保存校对结果</button></form>`);}
async function phoneView(){
 const [devices,captures]=await Promise.all([api('/phone/devices'),api('/phone/captures?status=all')]);
 const local=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
 return head('把纸上的认真，带回练习里','安卓端只负责拍照、识别、校对与录入，批改仍在这里完成。','','PHONE COMPANION')+`<div class="grid-main"><section class="card"><h2>连接安卓手机</h2><p>手机与 Mac 连接同一局域网。在安卓 App 中填写电脑地址，再输入这里生成的配对码。</p><p class="prose">${local?'手机请填写 Mac 的局域网地址，例如 http://192.168.1.20:'+location.port:esc(location.origin)}</p><button class="btn primary" data-action="phone-pair-code">生成十分钟配对码</button> <a class="btn" href="https://github.com/krbedison-cloud/writing/releases/download/v0.1.28/shenlun-companion.apk" target="_blank" rel="noopener">下载安卓 App</a><div id="phone-pair-code" class="phone-pair-code" aria-live="polite"></div><p class="small muted">配对码只能使用一次。OCR 专用模型和密钥在手机 App 内配置，照片直接发送给你选择的 OCR 接口。</p></section><section class="card"><h2>已连接设备</h2>${devices.filter(d=>!d.revoked).map(d=>`<div class="row between section-gap"><div><b>${esc(d.name)}</b><p class="small muted">最近连接 ${esc(date(d.last_seen_at))}</p></div><button class="btn small" data-action="phone-revoke" data-device="${esc(d.id)}">断开</button></div>`).join('')||'<p class="muted">尚未连接手机</p>'}</section></div><section class="card section-gap"><h2>手机录入记录 <span class="small muted">· ${captures.pending_count} 份待接收</span></h2><p class="small muted">打开对应题目的练习页，空草稿会自动接收；已有草稿会提供追加或替换选项。</p>${captures.items.map(c=>`<article class="phone-incoming"><div class="row between wrap"><div><b>${esc(c.paper_title)} · 第 ${c.question_number} 题</b><p class="small muted">${esc(c.device_name)} · ${esc(date(c.created_at))} · ${{pending:'待录入',accepted:'已录入',dismissed:'已忽略'}[c.status]}</p></div><a class="btn small" href="#practice/${c.paper_id}/${c.question_id}">打开本题</a></div><details><summary>查看文字</summary><div class="prose">${esc(c.text)}</div></details></article>`).join('')||empty('等待第一份手写答案','在手机上校对识别结果后，点击同步即可。')}</section>`;
}
async function act(el){const action=el.dataset.action;
 if(action==='phone-pair-code'){const result=await post('/phone/pair-code',{});$('#phone-pair-code').textContent=result.code;return;}
 if(action==='phone-revoke'){await api('/phone/devices/'+el.dataset.device,{method:'DELETE'});toast('手机连接已断开');return route(true);}
 if(action==='restore-phone-draft'){
  const saved=JSON.parse(localStorage.getItem('phone-draft-backup-'+state.question.id)||'null');if(!saved)return;
  const current=$('#answer').value;paperEditorInstance.insert(saved.text,0,current.length);localStorage.setItem('phone-draft-backup-'+state.question.id,JSON.stringify({text:current,at:new Date().toISOString()}));toast('已恢复录入前草稿，可再次点击切换回来');return;
 }

 if(action==='toggle-focus')return setFocusMode(!focused);
 if(action==='reader-reset')return applyReadingPreferences(READING_DEFAULTS);
 if(action==='upload')return $('#pdf-input').click();
 if(action==='close-dialog')return closeDialog();
 if(action==='add-model')return modelEditor();
 if(action==='edit-model')return modelEditor(el.dataset.id);
 if(action==='edit-paper')return paperEditor();
 if(action==='add-question'){$('#questions-editor').insertAdjacentHTML('beforeend',questionEditor({number:$$('.edit-q').length+1,kind:'summary',word_min:0,word_max:0}));return;}
 if(action==='apply-material-range'){const q=el.closest('.edit-q'),start=Number($('[data-range-start]',q).value),end=Number($('[data-range-end]',q).value);if(end<start)throw Error('结束材料编号不能小于起始编号');$$('[name="material_id"]',q).forEach(box=>box.checked=Number(box.value)>=start&&Number(box.value)<=end);$('[name="material_mode"]',q).value='selected';$('[data-custom-materials]',q).hidden=true;return;}
 if(action==='remove-question'){el.closest('.edit-q').remove();return;}
 if(action==='review-tab'){state.reviewProvider=el.dataset.id;$$('[data-action="review-tab"]').forEach(b=>b.classList.toggle('active',b===el));setReviewBody(state.attempt.grades.find(g=>g.provider_id===el.dataset.id));return;}
 if(action==='delete-model'){if(!confirm('删除这个接口配置？已有批改结果仍保留，未完成任务可能需要重新配置。'))return;await api('/providers/'+el.dataset.id,{method:'DELETE'});toast('接口已删除，历史批改保留');return route(true);}
 if(action==='test-model'){await post('/providers/'+el.dataset.id+'/test');toast('连接成功，模型已返回有效结果');return;}
 if(action==='classify-paper'){
  if(!confirm('AI 识别将替换当前试卷的题干与归类，已有历史记录仍保留。是否继续？'))return;
  const j=await post('/papers/'+state.paper.id+'/classify',{provider_id:$('#classify-provider').value});previousJobs.set(j.id,j.status);state.promptedScopes=(state.promptedScopes||[]).filter(id=>id!==state.paper.id);toast('AI 识别已开始，完成后自动刷新');return;
 }
 if(action==='explain-reference'){state.reviewProvider=el.dataset.provider;state.referenceVersions[el.dataset.provider]=el.dataset.reference;const j=await post('/attempts/'+state.attempt.id+'/reference-plan',{provider_id:el.dataset.provider,reference_id:el.dataset.reference});state.jobs=[j,...state.jobs];toast('正在生成当前范文的构思说明，评分和范文保留');return refreshAttempt();}
 if(action==='regenerate-reference'){state.reviewProvider=el.dataset.provider;delete state.referenceVersions[el.dataset.provider];const j=await post('/attempts/'+state.attempt.id+'/references',{provider_id:el.dataset.provider,direction:el.dataset.direction,reference_id:el.dataset.reference});state.jobs=[j,...state.jobs];toast('已开始单独重写范文，原批改分数保留');return refreshAttempt();}
 if(action==='boost-retry'){
  const p=state.providers.find(p=>p.id===el.dataset.provider),limit=Number(el.dataset.limit);
  if(!p||!Number.isInteger(limit)||limit<=p.max_tokens||limit>131072)throw Error('请刷新后重试，或在模型设置调整输出上限');
  const updated=await api('/providers/'+p.id,{method:'PUT',body:JSON.stringify({name:p.name,protocol:p.protocol,base_url:p.base_url,model:p.model,max_tokens:limit,timeout_seconds:p.timeout_seconds??600,reasoning_effort:p.reasoning_effort||'default',api_key:''})});
  state.providers=state.providers.map(item=>item.id===p.id?updated:item);
  await post('/attempts/'+state.attempt.id+'/retry',{provider_id:p.id});toast('已提高输出上限并单独重试，其他评分保留');return refreshAttempt();
 }
 if(action==='replace-coach')return retryEditor(el.dataset.provider);
 if(action==='replace-master')return retryEditor();
 if(action==='retry-add-model'){closeDialog();location.hash='models';return;}
 if(action==='retry-coach'){await post('/attempts/'+state.attempt.id+'/retry',{provider_id:el.dataset.provider});toast('开始重试这位教练，其他已成功结果保留');return refreshAttempt();}
 if(action==='retry'){await post('/attempts/'+state.attempt.id+'/retry');toast('开始重试失败项');return refreshAttempt();}
 if(action==='refresh-profile'){
  const id=localStorage.getItem('master')||state.providers[0]?.id;
  if(!id)throw Error('请先添加模型接口');
  await post('/profile/refresh',{provider_id:id});toast('正在从全部练习中总结要点');return;
 }
}
document.addEventListener('click',async event=>{
 const el=event.target.closest('[data-action]');if(!el||el.dataset.action==='submit-answer')return;
 event.preventDefault();if(el.disabled)return;
 el.disabled=true;
 try{await act(el)}catch(e){toast(e.message)}finally{el.disabled=false;}
});
document.addEventListener('submit',async event=>{
 const form=event.target;if(!['answer-form','model-form','paper-form','retry-form'].includes(form.id))return;
 event.preventDefault();const submit=$('button[type="submit"]',form);if(submit.disabled)return;submit.disabled=true;
 try{
  const f=new FormData(form),v=Object.fromEntries(f);
  if(form.id==='retry-form'){
   const body=state.retryProvider?{provider_id:state.retryProvider,replacement_provider_id:v.replacement}:{master_id:v.replacement};
   await post('/attempts/'+state.retryAttemptId+'/retry',body);closeDialog();toast(state.retryProvider?'已开始单独补批，其他成功评分保留':'已开始重新汇总');await refreshAttempt();
  }else if(form.id==='model-form'){
   v.max_tokens=Number(v.max_tokens);v.timeout_seconds=Number(v.timeout_seconds);await api('/providers'+(state.editProvider?'/'+state.editProvider:''),{method:state.editProvider?'PUT':'POST',body:JSON.stringify(v)});closeDialog();toast('接口已保存');await route(true);
  }else if(form.id==='paper-form'){
   const questions=$$('.edit-q',form).map(q=>{const get=name=>$(`[name="${name}"]`,q).value;return {number:Number(get('number')),kind:get('kind'),word_min:Number(get('word_min')),word_max:Number(get('word_max')),paper_rows:Number(get('paper_rows')),prompt:get('prompt'),classification_note:get('classification_note')||'人工校对',material_mode:get('material_mode'),material_ids:$$('[name="material_id"]:checked',q).map(x=>Number(x.value)),custom_materials:get('custom_materials')};});
   const p={title:v.title,year:v.year,province:v.province,volume:v.volume,materials:v.materials,confirmed:v.confirmed==='on',questions};
   await api('/papers/'+state.paper.id,{method:'PUT',body:JSON.stringify(p)});closeDialog();toast('校对已保存');await route(true);
  }else{
   const ids=f.getAll('provider');if(!ids.length)throw Error('至少选择一个批改模型');
   localStorage.setItem('selected-models',JSON.stringify(ids));localStorage.setItem('master',v.master);
   const result=await post('/attempts',{question_id:state.question.id,answer:v.answer,provider_ids:ids,master_id:v.master});
   toast('原答案已存档，开始批改');location.hash='attempt/'+result.attempt_id;
  }
 }catch(e){toast(e.message)}finally{submit.disabled=false;}
});
document.addEventListener('input',event=>{
 const el=event.target;
 if(el.closest('#model-form'))updateReasoningSetting();
 if(el.id==='answer'){
  try{localStorage.setItem('draft-'+state.question.id,el.value);$('#draft-status').textContent='草稿已保存到当前浏览器'}catch{$('#draft-status').textContent='本地空间不足，请及时提交存档'}
  paperFeedback(PaperLayout.check(el.value,PaperLayout.specFor(state.question)));
 }
 if(el.id==='paper-search'){const text=el.value.toLowerCase();$('#paper-list').innerHTML=paperCards(state.papers.filter(p=>(p.title+sourceText(p)).toLowerCase().includes(text)));}
});
document.addEventListener('change',event=>{
 const el=event.target;
 if(el.classList.contains('reference-version')){state.referenceVersions[el.dataset.provider]=el.value;setReviewBody(state.attempt.grades.find(g=>g.provider_id===el.dataset.provider));}
 if(el.id?.startsWith('reading-')){const key=el.id.slice(8);if(READING_OPTIONS[key])applyReadingPreferences({...readingPreferences,[key]:el.value});}
 if(el.name==='provider'&&$('#grading-summary'))$('#grading-summary').textContent='· 已选 '+$$('#answer-form [name="provider"]:checked').length+' 位教练';
 if(el.name==='material_id'){const q=el.closest('.edit-q');$('[name="material_mode"]',q).value='selected';}
 if(el.name==='material_mode'){const q=el.closest('.edit-q');$('[data-material-selection]',q).hidden=el.value!=='selected';$('[data-custom-materials]',q).hidden=el.value!=='custom';}
 if(el.id==='history-filter')$('#history-list').innerHTML=historyTable(state.history.filter(a=>!el.value||a.kind===el.value));
 if(el.id==='protocol'){
  const base=$('[name="base_url"]',$('#model-form'));
  base.value={openai:'https://api.openai.com/v1',anthropic:'https://api.anthropic.com/v1',gemini:'https://generativelanguage.googleapis.com/v1beta'}[el.value];
 }
 if(el.closest('#model-form'))updateReasoningSetting();
});
$('#pdf-input').addEventListener('change',async event=>{
 const file=event.target.files[0];if(!file)return;
 if(file.size>25*1024*1024){toast('文件超过 25 MB，请拆分后上传');event.target.value='';return;}
 const form=new FormData();form.append('file',file);$('#activity').textContent='正在解析 PDF…';toast('正在提取 PDF，扫描件识别会慢一些');
 try{const p=await api('/papers/import',{method:'POST',body:form});toast(`已识别 ${p.questions.length} 道题，请校对后开始练习`);location.hash='paper/'+p.id;}catch(e){toast(e.message)}finally{event.target.value='';$('#activity').textContent='准备就绪';}
});
let previousJobs=new Map();
async function poll(){
 try{
  const jobs=await api('/jobs');state.jobs=jobs;
  const active=jobs.filter(j=>['queued','running'].includes(j.status));
  $('#activity').textContent=active.length?`${active.length} 项任务进行中`:'准备就绪';
  const changed=jobs.filter(j=>(previousJobs.has(j.id)?previousJobs.get(j.id)!==j.status:j.kind==='reference')&&!['queued','running'].includes(j.status));
  previousJobs=new Map(jobs.map(j=>[j.id,j.status]));
  const view=location.hash.slice(1).split('/')[0];
  if(view==='practice'&&!document.hidden)await phoneInbox?.refresh();
  if(view==='attempt'&&state.attempt&&['queued','running'].includes(state.attempt.status)&&!$('#editor').open)await refreshAttempt();
  else if(view==='attempt'&&changed.some(j=>j.kind==='reference'&&j.target_id===state.attempt?.id)&&!$('#editor').open)await refreshAttempt();
  else if(changed.length&&!$('#editor').open&&['profile','overview','history','paper'].includes(view))await route(true);
  for(const j of changed)if(j.status==='failed')toast(j.error||'任务失败，请查看详情');
 }catch{/* Keep entered answers intact during transient disconnections. */}
 setTimeout(poll,2500);
}
document.addEventListener('click',event=>{const menu=$('#reading-menu');if(menu.open&&!menu.contains(event.target))menu.open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('dialog[open]')&&$('#material-toolbar').hidden){if($('#reading-menu').open)$('#reading-menu').open=false;else if(focused)setFocusMode(false);}});
restoreReadingPreferences();route();poll();
