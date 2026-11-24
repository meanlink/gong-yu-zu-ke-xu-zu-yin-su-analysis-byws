import {leaseLabels, renderLease} from './lease-desk/workspace.js';
import {residentLabels,renderResident} from './resident-operations.js';
import {api, saveSession, clearSession, session} from './api.js';
import {el, table, toast, badge} from './components.js';

const state = {user: session(), page: location.hash.slice(1) || 'dashboard'};
const app = document.querySelector('#app');
const pages = ['dashboard','properties','services','renewals',...Object.keys(residentLabels)];
const labels = {dashboard:'运营总览', properties:'房源档案', services:'报修服务', renewals:'问卷与跟进', ...residentLabels, ...leaseLabels};

function loginView(){
  app.innerHTML='';
  const shell = el('main',{class:'login-shell'},[
    el('section',{class:'login-visual'},[
      el('div',{class:'eyebrow',text:'HARBORSTAY / RETENTION SIGNALS'}),
      el('h1',{text:'把续租意愿，拆成可以跟进的信号。'}),
      el('p',{text:'连接房源、服务事件和租客问卷，让客户经营从月底猜测变成有证据的动作。'}),
      el('div',{class:'signal-line'},[el('span',{text:'住房运营分析'}),el('span',{text:'2026 Q4'}),el('span',{text:'租客关系组'})])
    ]),
    el('section',{class:'login-panel'},[
      el('div',{class:'panel-kicker',text:'内部工作台'}), el('h2',{text:'登录续租信号台'}),
      el('p',{class:'muted',text:'选择角色账号进入对应业务动作。演示密码：rental123'}),
      el('form',{id:'login-form'},[
        el('label',{},['账号',el('input',{name:'email',value:'analyst@harborstay.local',required:true})]),
        el('label',{},['密码',el('input',{name:'password',type:'password',value:'rental123',required:true})]),
        el('button',{class:'button primary',type:'submit',text:'进入工作台'})
      ]),
      el('div',{class:'role-notes'},[el('div',{},[badge('分析师','blue'),'运行模型、复核样本']),el('div',{},[badge('客户经理','orange'),'审批分析、处理跟进']),el('div',{},[badge('服务协调','green'),'维护报修事件'])])
    ])
  ]);
  app.append(shell);
  document.querySelector('#login-form').addEventListener('submit', async e=>{e.preventDefault(); const fd=new FormData(e.target); try{const data=await api('/auth/login',{method:'POST',body:JSON.stringify({email:fd.get('email'),password:fd.get('password')})}); saveSession(data); state.user=data.user; render();}catch(err){toast(err.message,'error')}});
}
function shell(){
  app.innerHTML='';
  const nav = el('aside',{class:'side-nav'},[
    el('div',{class:'brand'},[el('div',{class:'brand-mark',text:'HS'}),el('div',{},[el('strong',{text:'海棠湾'}),el('small',{text:'续租信号台'})])]),
    el('div',{class:'nav-label',text:'工作区'}),
    ...pages.map(p=>el('a',{href:`#${p}`,class:state.page===p?'active':''},[el('span',{class:'nav-dot'}),labels[p]])),
    el('div',{class:'nav-spacer'}),
    el('div',{class:'user-block'},[el('small',{text:state.user.full_name}),el('span',{text:state.user.role==='customer_manager'?'客户经理':state.user.role==='analyst'?'数据分析师':'服务协调员'})]),
    el('button',{class:'button ghost',onClick:()=>{clearSession();state.user=null;loginView();},text:'退出'})
  ]);
  const main = el('main',{class:'main-area'});
  app.append(nav,main); return main;
}
async function dashboard(main){
  const [d, a, tasks] = await Promise.all([api('/dashboard'), api('/analysis/latest'), api('/followups')]);
  main.append(el('div',{class:'page-head'},[
    el('div',{},[
      el('div',{class:'eyebrow',text:'RETENTION OPERATIONS / 2026 Q4'}),
      el('h1',{text:'续租信号总览'}),
      el('p',{class:'muted',text:'从服务体验到续租动作，今天先处理最需要被听见的租客。'})
    ]),
    el('div',{class:'head-meta',text:`更新于 ${new Date().toLocaleString('zh-CN',{hour12:false})}`})
  ]));
  main.append(el('section',{class:'metric-row'},[
    metric('在管房源',d.properties,'units'),
    metric('活跃租客',d.active_tenants,'residents'),
    metric('待跟进',d.followups,'actions'),
    metric('平均意愿',Number(d.avg_intent).toFixed(1)+'/10','intent')
  ]));
  const taskRows = tasks.slice(0,4).map(t => el('div',{class:'task-row'},[
    badge(t.priority,t.priority==='high'?'red':'orange'),
    el('div',{},[
      el('strong',{text:t.tenant_name}),
      el('small',{text:t.reason})
    ]),
    el('span',{class:'task-date',text:t.due_date})
  ]));
  main.append(el('section',{class:'split-grid'},[
    el('div',{class:'panel large'},[
      el('div',{class:'panel-title'},[el('h2',{text:'模型摘要'}),badge('Python 计算','blue')]),
      el('div',{class:'finding'},[
        el('div',{class:'finding-number',text:String(a.partial_correlation)}),
        el('div',{},[
          el('strong',{text:'偏相关系数'}),
          el('p',{class:'muted',text:a.partial_corr_label}),
          el('p',{class:'interpret',text:a.explanation})
        ])
      ]),
      el('div',{class:'model-strip'},[
        el('span',{text:`样本 ${a.sample_size} 份`}),
        el('span',{text:`R² ${a.stepwise.r2}`}),
        el('span',{text:`入模 ${a.stepwise.selected_features.join(' · ')}`})
      ])
    ]),
    el('div',{class:'panel'},[
      el('div',{class:'panel-title'},[
        el('h2',{text:'今日跟进清单'}),
        el('a',{href:'#renewals',class:'text-link',text:'查看全部'})
      ]),
      ...taskRows
    ])
  ]));
  main.append(el('section',{class:'callout'},[
    el('div',{},[
      el('div',{class:'eyebrow',text:'下一步'}),
      el('h2',{text:'让一次报修，成为一次关系修复。'}),
      el('p',{class:'muted',text:'先看服务解决时长和服务评分，再决定谁需要电话、谁适合发续租方案。'})
    ]),
    el('a',{href:'#renewals',class:'button secondary',text:'打开续租复核'})
  ]));
}
function metric(title,value,kind){return el('div',{class:`metric ${kind}`},[el('span',{class:'metric-label',text:title}),el('strong',{text:String(value)}),el('span',{class:'metric-rule',text:kind==='actions'?'需要客户经理动作':'来自实时业务表'})]);}
async function properties(main){ const [ps,ts]=await Promise.all([api('/properties'),api('/tenants')]); main.append(pageTitle('房源档案','看清租金、房龄和在住关系，为续租解释保留上下文。')); main.append(el('section',{class:'panel'},[el('div',{class:'panel-title'},[el('h2',{text:'房源与租客关系'}),badge(`${ps.length} 套房源`,'blue')]),table(['房源','片区','租金','楼层','房龄','租客','状态'],ps.map(p=>{const t=ts.find(x=>x.property_id===p.id);return [p.code,p.neighborhood,`¥${p.monthly_rent}`,`${p.floor}F`,`${p.property_age_years}年`,t?.full_name||'—',badge(t?.status==='active'?'在住':'已退租',t?.status==='active'?'green':'neutral').textContent]}))])); }
async function services(main){ const [events,ts]=await Promise.all([api('/service-events'),api('/tenants')]); main.append(pageTitle('报修服务','服务事件是续租解释的重要前因，先让处理过程可见。')); const form=el('form',{class:'inline-form',id:'service-form'},[select('tenant_id','租客',ts.filter(t=>t.status==='active').map(t=>[t.id,t.full_name])),select('category','问题类别',[['漏水','漏水'],['空调','空调'],['网络','网络'],['清洁','清洁'],['电梯','电梯']]),select('severity','紧急度',[['routine','常规'],['urgent','紧急'],['emergency','特急']]),el('input',{name:'summary',placeholder:'简述服务事件',required:true}),el('button',{class:'button primary',type:'submit',text:'登记事件'})]); main.append(el('section',{class:'panel'},[el('div',{class:'panel-title'},[el('h2',{text:'新增服务事件'}),badge('服务协调员 / 客户经理','green')]),form])); main.append(el('section',{class:'panel'},[el('div',{class:'panel-title'},[el('h2',{text:'近期服务记录'}),badge(`${events.length} 条`,'blue')]),table(['租客','房源','类别','紧急度','耗时','评分','状态'],events.map(e=>[e.tenant_name,e.property_code,e.category,e.severity,e.resolution_days?`${e.resolution_days} 天`:'处理中',e.satisfaction_score||'—',e.status==='resolved'?'已解决':'处理中']))])); document.querySelector('#service-form').addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(e.target);try{await api('/service-events',{method:'POST',body:JSON.stringify({tenant_id:Number(fd.get('tenant_id')),category:fd.get('category'),severity:fd.get('severity'),summary:fd.get('summary'),status:'resolved',satisfaction_score:4})});toast('服务事件已登记','success');render()}catch(err){toast(err.message,'error')}}); }
async function renewals(main){
  const [surveys,tasks,conflicts,batches,tenants] = await Promise.all([
    api('/surveys'),api('/followups'),api('/survey-conflicts'),api('/survey-imports'),api('/tenants')]);
  main.append(pageTitle('问卷与跟进','把问卷采集、纸质补录、解释模型和客户经营复核放在同一条链上。'));
  const tenantOptions = [...new Map(surveys.map(s=>[s.tenant_id,[s.tenant_id,s.tenant_name]])).values()];
  const form = el('form',{class:'survey-form',id:'survey-form'},[
    select('tenant_id','选择租客',tenantOptions),
    el('input',{name:'lease_period',value:'2026-2027',placeholder:'租期',required:true}),
    scoreInput('cleanliness_score','整洁'),
    scoreInput('service_score','服务'),
    scoreInput('value_score','性价比'),
    scoreInput('community_score','社区'),
    el('input',{name:'renewal_intent',type:'number',min:1,max:10,step:1,value:'6',placeholder:'续租意愿 1-10'}),
    el('input',{name:'comments',placeholder:'租客原话或跟进备注'}),
    el('button',{class:'button primary',type:'submit',text:'采集问卷'})
  ]);
  main.append(el('section',{class:'panel'},[
    el('div',{class:'panel-title'},[el('h2',{text:'问卷采集'}),badge('重复问卷将被阻断','orange')]),
    form
  ]));
  main.append(paperImportPanel(conflicts.open.length,batches.batches,tenants,['analyst','customer_manager'].includes(state.user.role)));
  if (conflicts.open.length) main.append(conflictPanel(conflicts.open));
  if (conflicts.resolved.length) main.append(resolvedConflictPanel(conflicts.resolved));
  const surveyRows = surveys.map(s=>[
    s.tenant_name,s.lease_period,`${s.service_score}/${s.renewal_intent}`,
    `${s.origin==='paper_import'?'纸质入样':'人工采集'}${s.sample_version>1?` · 换卷 v${s.sample_version}`:''}`,
    s.status==='reviewed'?'已复核':'待复核',s.comments
  ]);
  const taskCards = tasks.map(t=>{
    const close = t.status!=='closed' && state.user.role==='customer_manager' ? el('button',{class:'button tiny',text:'标记已跟进',onClick:async()=>{
      await api(`/followups/${t.id}/close`,{method:'POST'}); toast('任务已关闭','success'); render();
    }}) : null;
    return el('div',{class:'task-card'},[
      el('div',{class:'task-card-top'},[badge(t.priority,t.priority==='high'?'red':'orange'),el('strong',{text:t.tenant_name})]),
      el('p',{text:t.reason}),
      el('small',{text:`${t.property_code} · 截止 ${t.due_date}`}),
      close
    ]);
  });
  main.append(el('section',{class:'split-grid'},[
    el('div',{class:'panel'},[
      el('div',{class:'panel-title'},[el('h2',{text:'问卷样本'}),badge(`${surveys.length} 份`,'blue')]),
      table(['租客','租期','服务/意愿','来源','状态','备注'],surveyRows)
    ]),
    el('div',{class:'panel'},[
      el('div',{class:'panel-title'},[el('h2',{text:'跟进清单'}),badge(`${tasks.length} 项`,'red')]),
      ...taskCards
    ])
  ]));
  const analysisButton = el('button',{class:'button secondary',text:'运行偏相关与逐步回归',onClick:async()=>{
    try{const a=await api('/analysis/run',{method:'POST'});toast(`分析完成，偏相关 ${a.partial_correlation}`,'success');render();}
    catch(err){toast(err.message,'error');}
  }});
  const reviewButton = state.user.role==='customer_manager' ? el('button',{class:'button orange',text:'客户经营复核并生成清单',onClick:async()=>{
    try{const r=await api('/reviews/approve',{method:'POST',body:JSON.stringify({note:'已复核模型解释与租客原话，安排分级沟通'})});toast(`已生成 ${r.created_followups} 条跟进`,'success');render();}
    catch(err){toast(err.message,'error');}
  }}) : null;
  main.append(el('section',{class:'action-bar'},[
    el('div',{},[
      el('strong',{text:'复核动作'}),
      el('p',{class:'muted',text:'客户经理确认后，只有活跃租客会进入跟进清单。已退租和重复问卷在后端被阻断。'})
    ]),
    el('div',{class:'action-row'},[analysisButton,reviewButton])
  ]));
  document.querySelector('#survey-form').addEventListener('submit',async e=>{
    e.preventDefault();
    const fd=new FormData(e.target);
    try{
      await api('/surveys',{method:'POST',body:JSON.stringify({
        tenant_id:Number(fd.get('tenant_id')),lease_period:fd.get('lease_period'),
        cleanliness_score:Number(fd.get('cleanliness_score')),service_score:Number(fd.get('service_score')),
        value_score:Number(fd.get('value_score')),community_score:Number(fd.get('community_score')),
        renewal_intent:Number(fd.get('renewal_intent')),comments:fd.get('comments')
      })});
      toast('问卷已采集','success'); render();
    }catch(err){toast(err.message,'error');}
  });
}

// 一次导入的表单只生成一个批次号，失败重试沿用同一号码，后端按幂等键去重。
const importKeys = new WeakMap();
function importKeyFor(form){
  if(!importKeys.has(form)) importKeys.set(form, (globalThis.crypto?.randomUUID?.() ||
    `paper-${Date.now()}-${Math.random().toString(16).slice(2)}`));
  return importKeys.get(form);
}
function parseSheetLines(text){
  return text.split(/\r?\n/).map(line=>line.trim()).filter(line=>line && !line.startsWith('#')).map((line,index)=>{
    const parts = line.split(/[,，\t]/).map(part=>part.trim());
    if(parts.length < 8) throw new Error(`第 ${index+1} 行需要 8 列：租客ID,租期,整洁,服务,性价比,社区,意愿,答卷编号[,原话]`);
    const [tenant_id,lease_period,cleanliness_score,service_score,value_score,community_score,renewal_intent,sheet_ref,...rest]=parts;
    return {tenant_id:Number(tenant_id),lease_period,cleanliness_score:Number(cleanliness_score),service_score:Number(service_score),
      value_score:Number(value_score),community_score:Number(community_score),renewal_intent:Number(renewal_intent),
      sheet_ref,comments:rest.join(',')};
  });
}
function paperImportPanel(openCount,batches,tenants,canImport){
  const form = el('form',{class:'resident-fields',id:'paper-import-form'},[
    el('label',{},['纸质答卷（每行：租客ID,租期,整洁,服务,性价比,社区,意愿,答卷编号,原话）',
      el('textarea',{name:'sheets',rows:5,placeholder:'例如：\n3,2026-2027,4.2,3.8,3.5,4.0,7,ZX-2026-001,愿意续租'})]),
    el('label',{},['文件（CSV 或 JSON，可选）',el('input',{name:'file',type:'file',accept:'.csv,.json,text/csv,application/json'})]),
    el('label',{},['导入备注',el('input',{name:'note',placeholder:'例如：10 月纸质问卷第一批'})]),
    el('p',{class:'resident-feedback',role:'status'}),
    el('button',{class:'button primary',type:'submit',text:'导入答卷'})
  ]);
  const tenantHints = tenants.filter(t=>t.status==='active').map(t=>`${t.id}=${t.full_name}`).join(' · ');
  bindImport(form,tenants,tenantHints);
  const batchRows = batches.map(b=>[`#${b.id}`,b.source,`${b.sheet_count} 份`,b.accepted_count,b.staged_count,b.duplicate_count,b.conflict_count]);
  return el('section',{class:'panel'},[
    el('div',{class:'panel-title'},[el('h2',{text:'纸质问卷补录'}),badge(openCount ? `${openCount} 组待裁定` : '无冲突待裁定',openCount?'orange':'green')]),
    el('p',{class:'muted',text:'无冲突答卷直接入样；同一租客同一租期出现不同答案时整组暂存，原问卷继续用于分析。导入中断后用同一批次号重试不会重复入样。'}),
    el('p',{class:'muted',text:`在住租客：${tenantHints}`}),
    canImport ? form : el('p',{class:'muted',text:'仅分析师和客户经理可以导入纸质答卷。'}),
    batchRows.length ? table(['批次','来源','答卷','入样','暂存','重复','冲突组'],batchRows) : el('p',{class:'muted',text:'尚无导入批次'})
  ]);
}
function bindImport(form,tenants,tenantHints){
  const feedback = form.querySelector('[role=status]');
  form.addEventListener('submit',async e=>{
    e.preventDefault();
    const data = new FormData(form);
    const button = form.querySelector('[type=submit]');
    button.disabled = true; feedback.textContent = '';
    try{
      const file = data.get('file');
      let sheets = [];
      if(file && file.size){
        const text = await file.text();
        if(/\.json$/i.test(file.name)){
          const parsed = JSON.parse(text);
          sheets = Array.isArray(parsed) ? parsed : parsed.sheets;
          if(!Array.isArray(sheets)) throw new Error('JSON 里没有 sheets 数组');
        } else sheets = parseSheetLines(text);
      }
      const typed = String(data.get('sheets')||'').trim();
      if(typed) sheets = sheets.concat(parseSheetLines(typed));
      if(!sheets.length) throw new Error('请粘贴答卷或选择文件');
      const unknown = sheets.find(s=>!tenants.some(t=>t.id===Number(s.tenant_id)));
      if(unknown) throw new Error(`租客 ${unknown.tenant_id} 不存在，请核对编号（${tenantHints}）`);
      const result = await api('/survey-imports',{method:'POST',body:JSON.stringify({
        idempotency_key:importKeyFor(form),note:data.get('note')||null,sheets
      })});
      const b = result.batch;
      toast(result.replayed ? `该批次已导入过：入样 ${b.accepted_count}，暂存 ${b.staged_count}` :
        `入样 ${b.accepted_count} 份，暂存 ${b.staged_count} 份，重复 ${b.duplicate_count} 份`,'success');
      render();
    }catch(err){ feedback.textContent = err.message; }
    finally{ button.disabled = false; }
  });
}
function scoreLine(sheet){
  return `整洁 ${sheet.cleanliness_score} · 服务 ${sheet.service_score} · 性价比 ${sheet.value_score} · 社区 ${sheet.community_score} · 意愿 ${sheet.renewal_intent}/10`;
}
function conflictPanel(rows){
  const analyst = state.user.role==='analyst';
  const cards = rows.map(row=>{
    const dispatched = Boolean(row.followup);
    const options = row.sheets.map(sheet=>el('option',{value:sheet.id,
      text:`${sheet.sheet_ref} · ${scoreLine(sheet)}${sheet.comments?` · ${sheet.comments}`:''}`}));
    const sheetSelect = el('select',{name:'sheet_id'},options);
    const note = el('input',{name:'note',placeholder:'裁定原因（至少 4 个字）',minlength:4,maxlength:300,required:true});
    const feedback = el('p',{class:'resident-feedback',role:'status'});
    const adopt = el('button',{class:'button primary',type:'button',text:'采用所选答卷入样',
      onClick:()=>resolveConflict(row.id,'adopted',sheetSelect.value,note,feedback,adopt,keep)});
    const keep = el('button',{class:'button secondary',type:'button',text:'保留原答案',
      onClick:()=>resolveConflict(row.id,'kept_original',null,note,feedback,adopt,keep)});
    if(dispatched){adopt.disabled=true;adopt.title='原问卷已派出跟进，只能保留原答案并写明原因';}
    return el('article',{class:'panel resident-record'},[
      el('header',{},[el('h3',{text:`${row.tenant_name} · ${row.lease_period}`}),badge(dispatched?`已派单 #${row.followup.id}`:'待裁定',dispatched?'orange':'red')]),
      el('p',{class:'muted resident-meta',text:`${row.property_code} · 原租期结束 ${row.lease_end_date} · ${row.reason_code==='answers_disagree'?'几份答卷彼此不一致':'导入答案与原问卷不一致'}`}),
      el('dl',{},[
        el('div',{},[el('dt',{text:'原问卷（分析样本）'}),el('dd',{text:row.original?`${scoreLine(row.original)} · ${row.original.comments||'无原话'}`:'尚无原问卷'})]),
        ...row.sheets.map(sheet=>el('div',{},[el('dt',{text:`暂存答卷 ${sheet.sheet_ref}`}),el('dd',{text:`${scoreLine(sheet)} · ${sheet.comments||'无原话'}`})]))
      ]),
      dispatched?el('p',{class:'resident-note',text:'原问卷已经派出跟进，裁定只能保留原答案并写明原因，跟进依据保持不变。'}):null,
      analyst?el('form',{class:'resident-fields resident-actions',onSubmit:e=>e.preventDefault()},[
        el('label',{},['采用哪份答卷',sheetSelect]),
        el('label',{},['裁定原因',note]),feedback,
        el('div',{class:'action-row'},[adopt,keep])
      ]):el('p',{class:'muted',text:'等待数据分析师裁定。'})
    ]);
  });
  return el('section',{},[
    el('div',{class:'page-head'},[el('div',{},[el('h2',{text:'问卷冲突待裁定'}),el('p',{class:'muted',text:'整组暂存不进入样本；裁定前原问卷照常用于分析和跟进。'})])]),
    ...cards
  ]);
}
async function resolveConflict(id,resolution,sheetId,note,feedback,adopt,keep){
  if(!note.value.trim() || note.value.trim().length<4){ feedback.textContent='请填写至少 4 个字的裁定原因'; return; }
  adopt.disabled = true; keep.disabled = true; feedback.textContent='';
  try{
    const body = {resolution,note:note.value.trim()};
    if(resolution==='adopted') body.sheet_id=Number(sheetId);
    await api(`/survey-conflicts/${id}/resolve`,{method:'POST',body:JSON.stringify(body)});
    toast(resolution==='adopted'?'已采用所选答卷，样本已换卷':'已保留原答案，冲突关闭','success');
    render();
  }catch(err){ feedback.textContent = err.message; adopt.disabled = false; keep.disabled = false; }
}
function resolvedConflictPanel(rows){
  return el('section',{class:'panel'},[
    el('div',{class:'panel-title'},[el('h2',{text:'已裁定冲突'}),badge(`${rows.length} 组`,'green')]),
    table(['租客','租期','裁定','选中答卷','原因','裁定时间'],rows.map(row=>{
      const chosen = row.sheets.find(sheet=>sheet.id===row.resolved_sheet_id);
      return [row.tenant_name,row.lease_period,row.resolution==='adopted'?'采用导入答卷':'保留原答案',
        chosen ? chosen.sheet_ref : '—',row.note,
        row.resolved_at ? new Date(row.resolved_at).toLocaleString('zh-CN',{hour12:false}) : '—'];
    }))
  ]);
}
function scoreInput(name,label){return el('label',{class:'compact'},[label,el('input',{name,type:'number',min:1,max:5,step:.1,value:'4'})]);}
function select(name,label,options){return el('label',{class:'compact'},[label,el('select',{name},options.map(o=>el('option',{value:o[0],text:o[1]})))]);}
function pageTitle(title,desc){return el('div',{class:'page-head'},[el('div',{},[el('div',{class:'eyebrow',text:'HARBORSTAY / OPERATIONS'}),el('h1',{text:title}),el('p',{class:'muted',text:desc})])]);}
async function render(){ if(!state.user){loginView();return;} const main=shell(); try{if(state.page==='dashboard')await dashboard(main);if(state.page==='properties')await properties(main);if(state.page==='services')await services(main);if(state.page==='renewals')await renewals(main);if(residentLabels[state.page])await renderResident(main,state.page,state.user,render);if(leaseLabels[state.page])await renderLease(main,state.page,state.user,render);}catch(err){main.append(el('div',{class:'error-state'},[el('h2',{text:'页面加载失败'}),el('p',{text:err.message}),el('button',{class:'button primary',text:'重试',onClick:render})]))}}
window.addEventListener('hashchange',()=>{state.page=location.hash.slice(1)||'dashboard';render()}); render();
