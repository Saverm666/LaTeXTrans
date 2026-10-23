'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const labels = {running:'正在翻译',stopping:'正在停止',stopped:'已停止',success:'翻译完成',partial:'部分失败',failed:'翻译失败'};
const stages = [['parse','解析源码','识别章节、公式与引用'],['translate','翻译正文','等待模型逐段返回译文'],['repair','校验与修复','检查命令、括号和占位符'],['compile','生成 PDF','输出单语与双语译文']];
const state = {settings:null,jobs:[],job:null,jobId:localStorage.getItem('latextrans.activeJob'),inputMode:localStorage.getItem('latextrans.inputMode') || 'upload',uploads:[],uploading:0,detail:'issues',view:'workspace',busy:false,lastList:0,issueSignature:'',historySignature:'',submitting:false};
let toastTimer;
const modelLists = {primary:[],repair:[]};
let openModelPicker = null;
let pickingOutputDirectory = false;

function modelField(target) { return target === 'repair' ? $('#setting-repair-model') : $('#setting-model'); }
function closeModelPicker() {
  if (!openModelPicker) return;
  const {target} = openModelPicker;
  $('#' + target + '-model-menu').hidden = true;
  modelField(target).setAttribute('aria-expanded','false');
  modelField(target).removeAttribute('aria-activedescendant');
  $(`[data-model-toggle="${target}"]`).setAttribute('aria-expanded','false');
  openModelPicker = null;
}
function positionModelPicker() {
  if (!openModelPicker) return;
  const root = $(`[data-model-picker="${openModelPicker.target}"]`);
  const menu = $('#' + openModelPicker.target + '-model-menu');
  const field = root.getBoundingClientRect(), dialog = $('#settings-dialog').getBoundingClientRect();
  const lower = Math.min(dialog.bottom,window.innerHeight)-field.bottom-12;
  const upper = field.top-Math.max(dialog.top,0)-12;
  const below = lower >= 200 || lower >= upper;
  menu.style.width = `${field.width}px`;
  menu.style.left = `${field.left}px`;
  menu.style.maxHeight = `${Math.max(90,Math.min(300,below ? lower : upper))}px`;
  menu.style.top = `${below ? field.bottom+6 : field.top-menu.getBoundingClientRect().height-6}px`;
}
function showModelPicker(target,query='') {
  if (openModelPicker?.target !== target) closeModelPicker();
  const field = modelField(target), models = modelLists[target];
  const matching = models.filter(model => model.toLowerCase().includes(query.trim().toLowerCase()));
  openModelPicker = {target,models:matching,active:-1};
  const menu = $('#' + target + '-model-menu'), options = $('#' + target + '-model-options');
  $('.model-menu-count',menu).textContent = query.trim() ? `匹配 ${matching.length} / ${models.length} 个模型` : `全部 ${models.length} 个模型`;
  options.innerHTML = matching.length ? matching.map((model,index) => `<button type="button" role="option" tabindex="-1" class="model-option" id="${target}-model-option-${index}" data-model-index="${index}" aria-selected="${model === field.value}">${esc(model)}</button>`).join('') : `<div class="model-options-empty">${models.length ? '没有匹配的模型，可继续手动填写。' : '请先点击“获取模型”，也可以手动填写名称。'}</div>`;
  menu.hidden = false; options.scrollTop = 0;
  field.setAttribute('aria-expanded','true'); field.removeAttribute('aria-activedescendant');
  $(`[data-model-toggle="${target}"]`).setAttribute('aria-expanded','true');
  positionModelPicker();
}
function chooseModel(index) {
  if (!openModelPicker || !openModelPicker.models[index]) return;
  const field = modelField(openModelPicker.target);
  field.value = openModelPicker.models[index];
  field.dispatchEvent(new Event('change',{bubbles:true}));
  closeModelPicker(); field.focus();
  // Focusing the input may open it again; selection should finish with it closed.
  closeModelPicker();
}
function initModelPickers() {
  for (const target of ['primary','repair']) {
    const field = modelField(target), menu = $('#' + target + '-model-menu');
    field.addEventListener('focus',() => showModelPicker(target));
    field.addEventListener('click',() => showModelPicker(target));
    field.addEventListener('input',() => showModelPicker(target,field.value));
    field.addEventListener('keydown',event => {
      if (event.key === 'Escape' && openModelPicker) { event.preventDefault(); event.stopPropagation(); closeModelPicker(); return; }
      if (event.key === 'Tab') { closeModelPicker(); return; }
      if (event.key === 'Enter' && openModelPicker) {
        event.preventDefault();
        if (openModelPicker.active >= 0) chooseModel(openModelPicker.active); else closeModelPicker();
        return;
      }
      if (!['ArrowDown','ArrowUp'].includes(event.key)) return;
      event.preventDefault(); if (!openModelPicker) showModelPicker(target);
      const picker = openModelPicker, count = picker.models.length; if (!count) return;
      picker.active = event.key === 'ArrowDown' ? (picker.active+1)%count : (picker.active <= 0 ? count-1 : picker.active-1);
      $$('.model-option',menu).forEach((option,index) => option.classList.toggle('active',index === picker.active));
      const active = $('#' + target + '-model-option-' + picker.active);
      field.setAttribute('aria-activedescendant',active.id); active.scrollIntoView({block:'nearest'});
    });
    $(`[data-model-toggle="${target}"]`).addEventListener('click',() => {
      const wasOpen = openModelPicker?.target === target;
      field.focus();
      if (wasOpen) closeModelPicker(); else showModelPicker(target);
    });
    menu.addEventListener('mousedown',event => event.preventDefault());
    menu.addEventListener('click',event => { const option = event.target.closest('[data-model-index]'); if (option) chooseModel(Number(option.dataset.modelIndex)); });
  }
  document.addEventListener('click',event => { if (!event.target.closest('.model-picker')) closeModelPicker(); });
  $('#settings-dialog').addEventListener('close',closeModelPicker);
  $('.dialog-body').addEventListener('scroll',closeModelPicker);
  window.addEventListener('resize',positionModelPicker);
}

function duration(seconds) {
  const value = Math.max(0,Math.floor(seconds || 0));
  return `${String(Math.floor(value / 60)).padStart(2,'0')}:${String(value % 60).padStart(2,'0')}`;
}
function bytes(size) { return size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`; }
function date(seconds) { return new Date(seconds * 1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); }
function clock(seconds) { return new Date(seconds * 1000).toLocaleTimeString('zh-CN',{hour12:false}); }
function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.classList.toggle('error',error); el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.hidden = true, error ? 6000 : 3000);
}
function showError(selector,message) { const el = $(selector); el.textContent = message || ''; el.hidden = !message; }
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),options.timeout || 25000);
  const headers = {...(options.body && typeof options.body === 'string' ? {'Content-Type':'application/json'} : {}),...options.headers};
  try {
    const response = await fetch(path,{...options,headers,signal:controller.signal});
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || `请求失败（HTTP ${response.status}）`);
      error.status = response.status;
      throw error;
    }
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求等待超时，请检查后台服务是否仍在运行。');
    if (error instanceof TypeError) throw new Error('无法连接工作台服务，请检查启动终端后重试。');
    throw error;
  } finally { clearTimeout(timer); }
}
const post = (path, body) => api(path,{method:'POST',body:JSON.stringify(body)});
function connection(connected) {
  const el = $('#server-state'); el.className = `server-state ${connected ? 'connected' : 'disconnected'}`;
  el.innerHTML = `<i></i>${connected ? '本地服务已连接' : '服务连接中断'}`;
}
function setView(view) {
  state.view = view;
  $('#workspace-view').hidden = view !== 'workspace'; $('#history-view').hidden = view !== 'history';
  $('#breadcrumb').textContent = view === 'workspace' ? '翻译工作台' : '任务记录';
  $$('.nav-item[data-view]').forEach(button => { button.classList.toggle('active',button.dataset.view === view); button.setAttribute('aria-current',button.dataset.view === view ? 'page' : 'false'); });
  renderHistory();
}
function setInput(mode, focus = false) {
  if (!['upload','arxiv','local'].includes(mode)) mode = 'upload';
  state.inputMode = mode; localStorage.setItem('latextrans.inputMode',mode);
  $$('[data-input]').forEach(button => { const selected = button.dataset.input === mode; button.setAttribute('aria-selected',String(selected)); button.tabIndex = selected ? 0 : -1; });
  ['upload','arxiv','local'].forEach(name => $('#input-' + name).hidden = name !== mode);
  if (focus) $('#tab-' + mode).focus();
  showError('#input-error','');
}
function setDetail(name) {
  state.detail = name;
  $$('[data-detail]').forEach(button => button.setAttribute('aria-selected',String(button.dataset.detail === name)));
  ['issues','requests','logs'].forEach(key => $('#' + key + '-panel').hidden = key !== name);
}
function applySettings(settings) {
  state.settings = settings;
  $('#model-summary').textContent = settings.model || '选择翻译模型';
  $('#model-summary').title = settings.model || '打开模型设置';
  for (const name of ['source_language','target_language']) {
    const select = $('#' + name.replace('_','-'));
    if (![...select.options].some(option => option.value === settings[name])) select.add(new Option(settings[name],settings[name]));
    select.value = settings[name];
  }
}
function openSettings() {
  if (!state.settings) return toast('配置尚未加载，请稍后重试。',true);
  const form = $('#settings-form');
  closeModelPicker();
  for (const [name,value] of Object.entries(state.settings)) {
    const field = form.elements.namedItem(name); if (!field) continue;
    if (field.type === 'checkbox') field.checked = String(value).toLowerCase() === 'true'; else field.value = value ?? '';
  }
  for (const name of ['key','repair_key']) {
    const field = form.elements.namedItem(name); field.value = ''; field.type = 'password';
    field.placeholder = state.settings[name + '_saved'] ? '已保存密钥，留空保持不变' : '填写 API Key';
  }
  $$('[data-toggle-key]').forEach(button => button.textContent = '显示');
  $('#config-path').textContent = `配置文件：${state.settings.config_path}`;
  showError('#settings-error',''); $('#settings-dialog').showModal();
}
function formSettings() {
  const result = Object.fromEntries(new FormData($('#settings-form')));
  result.max_concurrent_requests = Number(result.max_concurrent_requests);
  result.mode = Number(result.mode); result.update_term = $('#settings-form').elements.update_term.checked ? 'True' : 'False';
  return result;
}
async function saveSettings(event) {
  event.preventDefault();
  if (pickingOutputDirectory) return;
  const button = $('#save-settings'); button.disabled = true;
  showError('#settings-error','');
  try { applySettings(await post('/api/settings',formSettings())); $('#settings-dialog').close(); toast('设置已保存，将用于下一次翻译。'); }
  catch (error) { showError('#settings-error',error.message); }
  finally { button.disabled = false; }
}
async function getModels(button) {
  button.disabled = true; const target = button.dataset.models; const feedback = $('#' + target + '-feedback');
  feedback.hidden = false; feedback.className = 'connection-feedback'; feedback.textContent = '正在获取模型列表…';
  try {
    const result = await post('/api/models',{settings:formSettings(),target});
    modelLists[target] = result.models;
    const field = target === 'repair' ? $('#setting-repair-model') : $('#setting-model');
    if (!field.value) field.value = result.models[0] || '';
    feedback.textContent = `已获取 ${result.models.length} 个模型。点击输入框或右侧箭头查看完整列表，输入文字可搜索。`;
    feedback.className = 'connection-feedback success'; field.focus(); showModelPicker(target);
  } catch (error) { feedback.textContent = error.message; feedback.className = 'connection-feedback failed'; }
  finally { button.disabled = false; }
}
async function pickOutputDirectory() {
  const button = $('#pick-output-directory'), feedback = $('#directory-feedback');
  pickingOutputDirectory = true; $('#save-settings').disabled = true;
  button.disabled = true; feedback.hidden = false; feedback.textContent = '请在文件管理器中选择输出目录…';
  showError('#settings-error',''); closeModelPicker();
  try {
    const result = await api('/api/directories/pick',{method:'POST',body:'{}',timeout:610000});
    if (result.cancelled) feedback.textContent = '已取消选择。';
    else {
      $('#setting-output').value = result.path;
      feedback.textContent = '已选择目录，点击“保存设置”生效。';
    }
  } catch (error) {
    feedback.textContent = '选择目录失败。';
    showError('#settings-error',error.status === 404 ? '当前后台仍是旧版本，请关闭启动窗口后重新启动，再选择目录。' : error.message);
  }
  finally { button.disabled = false; pickingOutputDirectory = false; $('#save-settings').disabled = false; }
}
async function probe(button) {
  const target = button.dataset.probe; const feedback = $('#' + target + '-feedback');
  button.disabled = true; feedback.hidden = false; feedback.className = 'connection-feedback';
  feedback.textContent = button.dataset.probeKind === 'translation' ? '正在测试约 1,000 字符的 LaTeX 翻译，最长等待 100 秒…' : '正在发送最小生成请求，最长等待 100 秒…';
  try {
    const {id} = await post('/api/probes',{settings:formSettings(),target,kind:button.dataset.probeKind || 'connect'});
    let result;
    do {
      await new Promise(resolve => setTimeout(resolve,1000));
      result = await api('/api/probes/' + id);
      feedback.textContent = `${result.model} · 已等待 ${Math.max(0,Math.floor(Date.now()/1000-result.started))} 秒\n${result.endpoint}\n${clock(result.started)} 开始 · ${result.test}`;
    } while (result.status === 'running');
    feedback.className = 'connection-feedback ' + result.status;
    feedback.textContent = `${result.status === 'success' ? '连接成功' : '连接失败'} · ${result.model} · ${result.elapsed} 秒\n${result.endpoint}\n${clock(result.started)} · ${result.test}\n${result.message}`;
  } catch (error) { feedback.className = 'connection-feedback failed'; feedback.textContent = error.message; }
  finally { button.disabled = false; }
}
function renderUploads() {
  $('#uploaded-files').innerHTML = state.uploads.map((file,index) => `<div class="uploaded-file">${icon('file')}<span title="${esc(file.name)}">${esc(file.name)}</span><small>${file.pending ? '上传中…' : bytes(file.size)}</small><button aria-label="移除 ${esc(file.name)}" data-remove-upload="${index}" ${file.pending ? 'disabled' : ''}>${icon('close')}</button></div>`).join('');
  updateStartButton();
}
async function uploadFiles(files) {
  showError('#input-error','');
  for (const file of files) {
    if (!/\.(zip|tar|tar\.gz|tgz)$/i.test(file.name)) { showError('#input-error','请上传 ZIP、TAR 或 TAR.GZ 格式的源码压缩包。'); continue; }
    if (!file.size || file.size > 100 * 1024 * 1024) { showError('#input-error','压缩包不能为空，单个文件不能超过 100 MB。'); continue; }
    const item = {name:file.name,size:file.size,pending:true}; state.uploads.push(item); state.uploading++; renderUploads();
    try {
      const uploaded = await api('/api/uploads',{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},timeout:120000});
      Object.assign(item,uploaded,{pending:false});
    } catch (error) { state.uploads = state.uploads.filter(entry => entry !== item); showError('#input-error',error.message); }
    finally { state.uploading--; renderUploads(); }
  }
  $('#source-files').value = '';
}
function updateStartButton() {
  const running = state.jobs.some(job => job.running) || state.job?.running;
  $('#start-button').disabled = !!(running || state.uploading || state.submitting);
  $('#start-button').innerHTML = `${state.submitting ? '正在启动…' : running ? '任务运行中' : state.uploading ? '正在上传…' : '开始翻译'}${icon('arrow')}`;
}
function inputPayload() {
  const payload = {paper_list:[],project_items:[],upload_ids:[],fresh:!$('#resume').checked};
  if (state.inputMode === 'upload') payload.upload_ids = state.uploads.filter(file => !file.pending).map(file => file.id);
  if (state.inputMode === 'arxiv') payload.paper_list = $('#arxiv-input').value.split(/[,\n]+/).map(value => value.trim()).filter(Boolean);
  if (state.inputMode === 'local') { payload.project_items = $('#local-input').value.split(/\n+/).map(value => value.trim()).filter(Boolean); payload.all_existing = $('#all-existing').checked; }
  return payload;
}
async function startTask(payload = inputPayload()) {
  if (state.submitting) return;
  showError('#input-error',''); showError('#page-alert','');
  if (!payload.paper_list.length && !payload.project_items.length && !(payload.upload_ids || []).length && !payload.all_existing) {
    showError('#input-error','请先添加论文：上传源码、粘贴 arXiv 链接，或填写本地工程路径。'); return;
  }
  if (!state.settings?.model || !state.settings?.url) { openSettings(); showError('#settings-error','请先配置翻译模型和服务地址。'); return; }
  state.submitting = true; updateStartButton();
  try {
    applySettings(await post('/api/settings',{source_language:$('#source-language').value,target_language:$('#target-language').value}));
    const job = await post('/api/jobs',payload);
    await selectJob(job.id); await refreshJobs();
    toast('任务已开始，运行状态会持续更新。');
  } catch (error) { showError('#input-error',error.message); }
  finally { state.submitting = false; updateStartButton(); }
}
async function selectJob(id) {
  const job = await api('/api/jobs/' + id);
  state.jobId = id; state.job = job; state.issueSignature = ''; localStorage.setItem('latextrans.activeJob',id);
  setView('workspace'); renderJob(job); updateStartButton();
}
function issueTitle(issue) {
  const statuses = {401:'身份验证失败',403:'服务拒绝访问',429:'接口限流',500:'模型服务内部错误',502:'服务网关错误',503:'模型暂不可用'};
  if (issue.status) return `${statuses[issue.status] || '请求失败'}（HTTP ${issue.status}）`;
  if ((issue.error_type || '').toLowerCase().includes('timeout')) return '模型响应超时';
  return issue.error || '发生异常';
}
function issueHelp(issue) {
  const error = `${issue.error || ''} ${issue.error_type || ''}`.toLowerCase();
  if (error.includes('timeout') || error.includes('超时')) return '本次请求未在时限内返回。自动重试会显示在下方；持续超时可停止后降低并发或更换模型。';
  if (issue.status === 429 || error.includes('429')) return '模型服务触发限流，正在按策略重试。频繁出现时可降低并发数。';
  if ([401,403].includes(issue.status)) return '请检查 API Key、服务地址和模型权限。修改后可从已保存的阶段继续。';
  return '完整原因保留在异常记录中；可下载诊断记录查看详细信息。';
}
function renderJob(job) {
  $('.workspace-grid').classList.add('has-task');
  const now = Date.now()/1000, elapsed = duration((job.ended || now)-job.started);
  const activeIssues = job.issues.filter(issue => !issue.resolved);
  const latest = [...activeIssues].reverse().find(issue => issue.type !== 'request_retry') || activeIssues.at(-1);
  const failed = ['failed','partial'].includes(job.status);
  let notice = '';
  if (failed) notice = `<div class="notice error" role="alert"><strong>${esc(job.error || issueTitle(latest || {}))}</strong>${esc(latest ? issueHelp(latest) : '查看下方异常记录，处理后可继续翻译。')}</div>`;
  else if (job.status === 'stopped') notice = '<div class="notice warning"><strong>任务已停止</strong>已保存的阶段仍在。可点击下方“继续此任务”恢复。</div>';
  else if (job.status === 'stopping') notice = '<div class="notice warning"><strong>正在终止翻译和编译进程</strong>请稍候，界面会自动更新。</div>';
  else if (latest) notice = `<div class="notice warning" role="alert"><strong>${esc(issueTitle(latest))}</strong>${esc(issueHelp(latest))}</div>`;
  else if (job.status === 'success') notice = '<div class="notice success"><strong>译文已准备好</strong>生成的 PDF 可在下方下载。</div>';
  let waiting = '';
  if (job.running && job.requests.length) {
    const max = Math.max(...job.requests.map(request => now-request.at));
    waiting = `<p class="waiting-caption">${max >= 60 ? '模型响应较慢 · ' : ''}最长已等待 ${Math.max(0,Math.floor(max))} 秒 / 单次上限 100 秒</p>`;
  } else if (job.running && job.retry_waits.length) {
    const retry = job.retry_waits[0];
    waiting = `<p class="waiting-caption">${Math.max(0,Math.ceil(retry.at+retry.delay-now))} 秒后重试 · 第 ${retry.attempt}/3 次尝试</p>`;
  } else if (job.running && now-job.last_activity >= 30) waiting = `<p class="waiting-caption">连续 ${Math.floor(now-job.last_activity)} 秒没有新事件，可查看日志或停止任务。</p>`;
  const stageIndex = stages.findIndex(stage => stage[0] === job.stage);
  const progress = job.status === 'success' ? 100 : Math.round(job.stage_progress*100);
  $('#monitor-content').innerHTML = `<div class="monitor-status"><div class="status-heading"><h3>${esc(labels[job.status] || job.status)}</h3><time>${elapsed}</time></div><p class="project-name" title="${esc(job.project)}">${esc(job.project)}</p>${notice}<div class="monitor-stats"><div><strong>${job.completed + job.failed}<span class="subtle"> / ${job.total || '—'}</span></strong><span>工程完成</span></div><div><strong>${job.requests.length}</strong><span>请求进行中</span></div><div><strong>${job.retry_count}</strong><span>自动重试</span></div></div><div class="monitor-progress" role="progressbar" aria-label="当前阶段进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span style="width:${Math.min(100,Math.max(0,progress))}%"></span></div><div class="progress-caption"><span>${esc(job.stage_detail || '准备输入')}</span><span>${progress}%</span></div>${waiting}</div><ol class="stage-list active-stages">${stages.map(([name,label,detail],index) => { const done = job.status === 'success' || index < stageIndex; const current = index === stageIndex && job.status !== 'success'; return `<li class="${done ? 'done' : current ? 'current' : ''}"><span>${done ? icon('check') : index+1}</span><div>${label}<small>${detail}</small></div></li>`; }).join('')}</ol>`;
  const badge = $('#live-badge'); badge.textContent = job.running ? '每秒更新' : '任务已结束'; badge.classList.toggle('running',job.running);
  $('#monitor-actions').hidden = false; $('#stop-button').hidden = !job.running;
  $('#stop-button').disabled = job.status === 'stopping'; $('#diagnostics-link').href = `/api/jobs/${job.id}/diagnostics`;
  const actions = $('#monitor-actions'); let resume = $('#retry-current');
  if (!job.running) {
    if (!resume) { resume = document.createElement('button'); resume.id = 'retry-current'; resume.className = 'button secondary small'; resume.textContent = '继续此任务'; resume.addEventListener('click',() => startTask({...state.job.inputs,fresh:false,all_existing:state.job.all_existing})); actions.prepend(resume); }
    resume.hidden = false;
  } else if (resume) resume.hidden = true;
  $('#activity-section').hidden = false; $('#issue-count').textContent = String(job.issues.length);
  renderIssues(job.issues);
  $('#requests-panel').innerHTML = job.requests.length ? `<div class="request-list"><div class="request-row header"><span>片段</span><span>模型</span><span>尝试</span><span>等待</span></div>${job.requests.map(request => `<div class="request-row"><span>${esc(request.part)}</span><span>${esc(request.model)}</span><span>${request.attempt}/3</span><span>${Math.max(0,Math.floor(now-request.at))} 秒</span></div>`).join('')}</div>` : '<div class="empty-detail">当前没有进行中的模型请求。</div>';
  const log = $('#log-output'), text = job.logs.slice(-300).join('\n') || '等待日志…';
  if (log.textContent !== text) { const bottom = log.scrollTop + log.clientHeight >= log.scrollHeight-35; log.textContent = text; if (bottom) log.scrollTop = log.scrollHeight; }
  $('#results-section').hidden = !job.files.length;
  $('#result-files').innerHTML = job.files.map(file => `<div class="file-row"><div class="file-icon">${icon('file')}</div><div class="file-info"><strong>${esc(file.label)}</strong><small>${esc(file.name)} · ${bytes(file.size)}</small></div><a class="button secondary small" href="${esc(file.url)}" download>${icon('download')}下载 PDF</a></div>`).join('');
}
function renderIssues(issues) {
  const signature = JSON.stringify(issues); if (signature === state.issueSignature) return;
  state.issueSignature = signature;
  const open = new Set($$('#issues-panel details[open]').map(element => element.dataset.issue));
  $('#issues-panel').innerHTML = issues.length ? [...issues].reverse().slice(0,30).map((issue,index) => {
    const id = String(issue.at || '') + issue.type + String(issue.request_id || '');
    return `<details class="issue ${issue.resolved ? 'resolved' : ''}" data-issue="${esc(id)}" ${open.has(id) || (index === 0 && !issue.resolved) ? 'open' : ''}><summary>${icon(issue.resolved ? 'check' : 'alert')}<span class="issue-title">${issue.resolved ? '已恢复 · ' : ''}${esc(issueTitle(issue))}</span><time>${clock(issue.at || Date.now()/1000)}</time></summary><div class="issue-content">${esc(issueHelp(issue))}${issue.part ? `<p>片段 ${esc(issue.part)} · ${esc(issue.model || '')} · 第 ${esc(issue.attempt || 1)} 次尝试</p>` : ''}<pre>${esc(issue.traceback || issue.error || '')}</pre></div></details>`;
  }).join('') : '<div class="empty-detail">目前没有异常。超时、重试和任务失败会保留在这里。</div>';
}
function jobRows(jobs) {
  if (!jobs.length) return '<div class="history-empty">' + icon('history') + '<h3>还没有翻译任务</h3><p>添加第一篇论文，译文和运行记录将保留在这里。</p></div>';
  return jobs.map(job => `<article class="history-row"><div class="history-icon">${icon(job.status === 'success' ? 'check' : job.status === 'failed' ? 'alert' : 'file')}</div><div class="history-info"><strong>${esc(job.project)}</strong><small>${date(job.started)} · ${esc(job.model || '—')} · ${job.completed} 成功${job.failed ? ` / ${job.failed} 失败` : ''}</small></div><span class="tag ${esc(job.status)}">${esc(labels[job.status] || job.status)}</span><button class="button quiet small" data-open-job="${job.id}">查看${icon('chevron')}</button></article>`).join('');
}
function renderHistory() {
  const signature = JSON.stringify(state.jobs); if (signature === state.historySignature) return;
  state.historySignature = signature;
  $('#history-jobs').innerHTML = jobRows(state.jobs);
  $('#recent-jobs').innerHTML = state.jobs.length ? jobRows(state.jobs.slice(0,3)) : '<div class="empty-history">' + icon('history') + '<span>还没有任务记录。完成的翻译会保留在这里。</span></div>';
}
async function refreshJobs() {
  state.jobs = await api('/api/jobs'); state.lastList = Date.now(); renderHistory(); updateStartButton();
}
async function tick() {
  if (state.busy || document.hidden) return;
  state.busy = true;
  try {
    if (state.jobId) { state.job = await api('/api/jobs/' + state.jobId); renderJob(state.job); }
    if (Date.now()-state.lastList > 4000) await refreshJobs();
    connection(true); updateStartButton();
    if ($('#page-alert').dataset.network === 'true') { showError('#page-alert',''); delete $('#page-alert').dataset.network; }
  } catch (error) {
    connection(false); showError('#page-alert',error.message + ' 页面会自动尝试重新连接。'); $('#page-alert').dataset.network = 'true';
  } finally { state.busy = false; }
}
async function init() {
  setInput(state.inputMode); setDetail('issues');
  for (const name of ['arxiv','local']) { const field = $('#' + name + '-input'); field.value = localStorage.getItem('latextrans.' + name) || ''; field.addEventListener('input',() => localStorage.setItem('latextrans.' + name,field.value)); }
  try {
    const bootstrap = await api('/api/bootstrap'); applySettings(bootstrap.settings); state.jobs = bootstrap.jobs; connection(true); renderHistory();
    const active = state.jobs.find(job => job.running);
    if (active) state.jobId = active.id;
    if (state.jobId && state.jobs.some(job => job.id === state.jobId)) await selectJob(state.jobId);
    else { state.jobId = null; localStorage.removeItem('latextrans.activeJob'); }
    updateStartButton();
  } catch (error) { connection(false); showError('#page-alert',error.message); }
  setInterval(tick,1000);
}

document.addEventListener('click',event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.view) setView(button.dataset.view);
  if (button.dataset.action === 'settings') openSettings();
  if (button.dataset.input) setInput(button.dataset.input);
  if (button.dataset.detail) setDetail(button.dataset.detail);
  if (button.dataset.models) getModels(button);
  if (button.dataset.probe) probe(button);
  if (button.dataset.toggleKey) { const input = $('#' + button.dataset.toggleKey); input.type = input.type === 'password' ? 'text' : 'password'; button.textContent = input.type === 'password' ? '显示' : '隐藏'; }
  if (button.dataset.removeUpload !== undefined) { state.uploads.splice(Number(button.dataset.removeUpload),1); renderUploads(); }
  if (button.dataset.openJob) selectJob(button.dataset.openJob).catch(error => toast(error.message,true));
});
$('.input-tabs').addEventListener('keydown',event => {
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault(); const modes = ['upload','arxiv','local'], index = modes.indexOf(state.inputMode);
  setInput(modes[event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3],true);
});
$('#settings-form').addEventListener('submit',saveSettings);
$('#pick-output-directory').addEventListener('click',pickOutputDirectory);
$('#close-settings').addEventListener('click',() => $('#settings-dialog').close());
$('#cancel-settings').addEventListener('click',() => $('#settings-dialog').close());
$('#settings-dialog').addEventListener('click',event => { if (event.target === $('#settings-dialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
$('#start-button').addEventListener('click',() => startTask());
$('#source-files').addEventListener('change',event => uploadFiles(event.target.files));
$('#source-files').addEventListener('focus',() => $('#dropzone').classList.add('dragging'));
$('#source-files').addEventListener('blur',() => $('#dropzone').classList.remove('dragging'));
const dropzone = $('#dropzone');
for (const name of ['dragenter','dragover']) dropzone.addEventListener(name,event => { event.preventDefault(); dropzone.classList.add('dragging'); });
for (const name of ['dragleave','drop']) dropzone.addEventListener(name,event => { event.preventDefault(); dropzone.classList.remove('dragging'); });
dropzone.addEventListener('drop',event => uploadFiles(event.dataTransfer.files));
$('#stop-button').addEventListener('click',async () => {
  if (!state.jobId) return;
  $('#stop-button').disabled = true;
  try { state.job = await post('/api/jobs/' + state.jobId + '/stop',{}); renderJob(state.job); await refreshJobs(); toast('已请求停止任务。'); }
  catch (error) { toast(error.message,true); $('#stop-button').disabled = false; }
});
initModelPickers();
init();
