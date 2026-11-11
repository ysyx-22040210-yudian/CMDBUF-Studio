'use strict';
window.FormalStudio = {
  items: [], info: null, current: null, inspected: null, mappingIndex: null,
  uploading: false, maxEdges: 100000, initialized: false, preview: null,

  async refresh() {
    this.info = await api('formal-state');
    if (!this.initialized) {
      this.items = (this.info.selection?.sources || []).map(s => ({...s, enabled:true}));
      this.maxEdges = this.info.selection?.max_edges || 100000;
      this.initialized = true;
    }
    if (!this.current && this.info.exports.length) this.current = this.info.exports[0].id;
  },
  key(s) { return s.kind === 'import' ? `import:${s.id}` : `${s.batch}:${s.run}`; },
  add(source) {
    if (this.items.some(s => this.key(s) === this.key(source))) return;
    const used = new Set(this.items.map(s => s.pattern)); let pattern = 1;
    while (used.has(pattern)) pattern++;
    this.items.push({...source, enabled:true, pattern, scope:'tb_cmdbuf_rd.dut', mapping:{}});
  },
  selected() { return this.items.filter(s => s.enabled).map(({enabled,...s}) => s); },

  async render() {
    await this.refresh();
    const exports = this.info.exports;
    const summary = exports.find(s => s.id === this.current);
    $('#main').innerHTML = head('波形转 Formal', '从 FSDB 提取复位后的输入输出，为每个 pattern 生成 assume、assert 与终点 cover。',
      btn('formal-import','导入 FSDB','upload') + btn('formal-generate','生成 Formal','code','primary'), 'WAVEFORM TO PROPERTIES') +
      `<div class="formal-workflow"><span><b>01</b> 选择波形</span>${icon('right')}<span><b>02</b> 确认实例与映射</span>${icon('right')}<span><b>03</b> 生成与下载</span><span class="spacer"></span><span class="tag teal">NPI · 直接解析 FSDB</span></div>
      <div class="formal-grid"><section class="panel formal-sources"><div class="panel-heading"><div><h2>输入波形 <span id="formal-selected-count" class="muted small">${this.selected().length} 个已选</span></h2><p class="panel-note">每个 FSDB 对应一个独立 pattern 编号</p></div>${btn('formal-add-regression','选择回归波形','plus','small')}</div>
      <div class="formal-source-list">${this.items.length ? this.items.map((s,i) => this.sourceRow(s,i)).join('') : empty('添加一个 FSDB','可从本机导入，也可选择已有回归生成的波形。','wave')}</div>
      <div class="formal-imports">${this.info.imports.length ? `<label>已导入文件</label><select id="formal-import-list"><option value="">选择一个已导入的 FSDB…</option>${this.info.imports.map(s => `<option value="${s.id}">${esc(s.name)} · ${(s.size/1024/1024).toFixed(1)} MB</option>`).join('')}</select>` : ''}</div></section>
      <section class="panel formal-rules"><div class="panel-heading"><div><h2>提取规则</h2><p class="panel-note">当前 CMDBUF 接口与时序协议</p></div>${icon('check')}</div><div class="formal-rule-body">
      <div><span class="formal-rule-label">待验证 DUT</span><strong>${esc(this.info.dut_name)}</strong><p>验证包使用“DUT 接入”页的当前实现；参考输入输出来自所选 FSDB。</p></div>
      <div><span class="formal-rule-label">采样时机</span><strong>每个时钟上升沿的沿前值</strong><p>保留空拍、背压和流水线位置，与 SVA 的采样语义一致。</p></div>
      <div><span class="formal-rule-label">复位与用例选择</span><strong>pattern + reset_segment</strong><p>复位前数据不生成业务属性。多次复位后的区间分别保留，从各自复位初态验证。</p></div>
      <div><span class="formal-rule-label">输出比较</span><strong>控制逐拍，负载按有效信号</strong><p>无效 R 负载及无请求时的 arb_addr 只记录。可比较值含 X/Z 或波形缺失时停止生成。</p></div>
      <div class="field"><label for="formal-max-edges">每个波形的采样沿上限</label><input id="formal-max-edges" class="input" type="number" min="1" max="1000000" value="${this.maxEdges}"><small>超过上限会报错，不会静默截断用例。</small></div>
      </div></section></div>
      <section id="formal-mapping" class="panel formal-mapping ${this.mappingIndex === null ? 'hidden':''}"></section>
      <section class="panel formal-output"><div class="panel-heading"><div><h2>生成结果</h2><p class="panel-note">生成代码与 Formal 验证结论分别记录</p></div><select id="formal-export-list" aria-label="选择 Formal 导出">${exports.length ? exports.map(s => `<option value="${s.id}" ${s.id===this.current?'selected':''}>${shortID(s.id)} · ${s.counts.patterns} patterns · ${s.counts.cycles} cycles</option>`).join('') : '<option>尚无生成结果</option>'}</select></div>
      <div id="formal-output-body">${summary ? this.resultMarkup(summary) : empty('尚未生成 Formal 代码','确认实例路径与 pattern 编号后，点击右上角“生成 Formal”。','code')}</div></section>
      <input id="formal-fsdb-input" type="file" accept=".fsdb" multiple hidden>`;
    click('#formal-import', () => $('#formal-fsdb-input').click());
    $('#formal-fsdb-input').onchange = e => this.upload(e.target.files).catch(e => toast(e.message,true));
    click('#formal-add-regression', () => this.regressionDialog());
    click('#formal-generate', () => this.generate());
    $('#formal-max-edges').oninput = e => this.maxEdges = Number(e.target.value);
    change('#formal-import-list', async e => { const s=this.info.imports.find(s=>s.id===e.target.value); if(s){this.add({kind:'import',id:s.id,name:s.name});await this.render();} });
    change('#formal-export-list', async e => {this.current=e.target.value;await this.render();});
    this.items.forEach((s,i) => {
      change(`#formal-enable-${i}`, e => {s.enabled=e.target.checked;e.target.closest('.formal-source').classList.toggle('selected',s.enabled);$('#formal-selected-count').textContent=`${this.selected().length} 个已选`;});
      $(`#formal-pattern-${i}`).oninput = e => s.pattern=Number(e.target.value);
      $(`#formal-scope-${i}`).oninput = e => {s.scope=e.target.value.trim();s.mapping={};};
      click(`#formal-inspect-${i}`, () => this.inspect(i));
      click(`#formal-map-${i}`, () => {this.mappingIndex=i;this.drawMapping();$('#formal-mapping').scrollIntoView({block:'start',behavior:'smooth'});});
      click(`#formal-remove-${i}`, async () => {this.items.splice(i,1);this.mappingIndex=null;await this.render();});
    });
    if(this.mappingIndex!==null) this.drawMapping();
    if(summary) this.bindResult(summary);
    $$('button[id^="formal-"]').filter(b=>!b.id.includes('download')).forEach(b=>b.dataset.idle='');
    updateBusy();
  },

  sourceRow(s,i) {
    return `<div class="formal-source ${s.enabled?'selected':''}"><div class="formal-source-title"><input id="formal-enable-${i}" type="checkbox" ${s.enabled?'checked':''} aria-label="选择 ${esc(s.name)}"><span class="formal-fsdb-icon">FSDB</span><div><strong>${esc(s.name)}</strong><small>${s.kind==='import'?'本机导入文件':esc(s.batch)}</small></div><button id="formal-remove-${i}" class="icon-button" title="从当前选择移除">${icon('close')}</button></div><div class="formal-source-fields"><label>pattern<input id="formal-pattern-${i}" class="input mono" type="number" min="0" max="2147483647" value="${s.pattern}"></label><label>FSDB 中的 DUT 实例<input id="formal-scope-${i}" class="input mono" value="${esc(s.scope)}" spellcheck="false"></label></div><div class="formal-source-actions"><span class="muted small">${Object.keys(s.mapping||{}).length?'已自定义信号映射':'按同名端口映射'}</span><span class="spacer"></span>${btn('formal-map-'+i,'端口映射','settings','small subtle')}${btn('formal-inspect-'+i,'解析层级','search','small')}</div></div>`;
  },

  drawMapping() {
    const index=this.mappingIndex, s=this.items[index], el=$('#formal-mapping');
    if(!s||!el)return;
    const meta=this.inspected?.key===this.key(s) ? this.inspected.data : null;
    el.classList.remove('hidden');
    el.innerHTML=`<div class="panel-heading"><div><h2>端口映射 · ${esc(s.name)}</h2><p class="panel-note">左侧是验证环境的接口名，右侧填写 FSDB 的完整信号路径。</p></div>${btn('formal-close-map','收起','up','small subtle')}</div>${meta?`<div class="formal-discovery"><span class="tag pass">NPI 已解析</span><span>${meta.signals.length} 个端口 / ${meta.scopes.length} 个层级 · ${esc(meta.meta.scale)}</span><span class="spacer"></span><label>已发现层级 <select id="formal-scope-choice">${meta.scopes.map(n=>`<option ${n===s.scope?'selected':''}>${esc(n)}</option>`).join('')}</select></label></div>`:''}<div class="formal-map-scroll"><table><thead><tr><th>环境信号</th><th>用途</th><th>位宽</th><th>FSDB 信号路径</th><th>匹配</th></tr></thead><tbody>${this.info.signals.map((p,k)=>{const path=s.mapping[p.name]||`${s.scope}.${p.name}`;const match=meta?.signals.find(x=>x.name===path);return `<tr><td class="mono">${esc(p.name)}</td><td>${p.name==='clk'?'采样时钟':p.name==='rst_n'?'低有效复位':p.direction==='input'?'输入 → assume':'输出 → assert'}</td><td class="mono">${p.width}</td><td><input class="input mono" data-formal-map="${k}" value="${esc(path)}" spellcheck="false"></td><td>${!meta?'—':match?.width===p.width?'<span class="positive">一致</span>':'<span class="negative">待确认</span>'}</td></tr>`;}).join('')}</tbody></table></div>`;
    $$('[data-formal-map]',el).forEach(input=>input.oninput=e=>{s.mapping[this.info.signals[Number(input.dataset.formalMap)].name]=e.target.value.trim();});
    click('#formal-close-map',()=>{this.mappingIndex=null;el.classList.add('hidden');});
    change('#formal-scope-choice',async e=>{s.scope=e.target.value;s.mapping={};$(`#formal-scope-${index}`).value=s.scope;await this.inspect(index);});
  },

  async inspect(index) {
    this.mappingIndex=index;
    const s=this.items[index];this.pendingKey=this.key(s);
    await launch('fsdb_inspect',{sources:[s],max_edges:this.maxEdges});
  },
  async generate() {
    const sources=this.selected();
    if(!sources.length)throw Error('请至少选择一个 FSDB 波形');
    await launch('fsdb_formal',{sources,max_edges:this.maxEdges});
  },
  async onJob(job) {
    if(job.state!=='DONE'||!job.formal_id)return;
    if(job.kind==='fsdb_inspect') {
      const data=await api('formal-inspection?id='+job.formal_id);
      this.inspected={key:this.key(data.source),data};
      this.mappingIndex=this.items.findIndex(s=>this.key(s)===this.key(data.source));
      if(this.mappingIndex<0)this.mappingIndex=null;
    } else this.current=job.formal_id;
    if(app.page==='formal')await this.render();
  },

  async upload(files) {
    if(!files?.length)return;
    this.uploading=true;updateBusy();
    try {
      for(const file of files) {
        const meta=await api('fsdb-upload-start',{name:file.name,size:file.size});
        for(let offset=0;offset<file.size;offset+=8*1024*1024) {
          const data=file.slice(offset,offset+8*1024*1024);
          const response=await fetch(`/api/fsdb-upload-chunk?id=${meta.id}&offset=${offset}`,{method:'POST',headers:{'X-Studio-Token':token},body:data});
          const result=await response.json();if(!response.ok)throw Error(result.error);
          $('#footer-context').textContent=`导入 ${file.name} · ${Math.round(result.received/result.size*100)}%`;
        }
        const result=await api('fsdb-upload-finish',{id:meta.id});
        this.add({kind:'import',id:result.id,name:result.name});
      }
      await this.render();toast('FSDB 已导入，可解析实例层级或直接生成 Formal');
    } finally {this.uploading=false;$('#footer-context').textContent='VCS 仿真 · Verdi 调试';updateBusy();}
  },

  async regressionDialog() {
    const history=app.state.history.filter(r=>r.waves);
    if(!history.length)throw Error('尚无生成波形的回归');
    dialog('选择回归 FSDB',`<div class="field"><label>回归批次</label><select id="formal-reg-batch">${history.map(s=>`<option value="${s.id}">${shortID(s.id)} · ${esc(s.dut)}</option>`).join('')}</select></div><div id="formal-reg-runs" class="formal-reg-runs"></div>`,btn('formal-add-selected','添加所选波形','plus','primary'));
    let current;
    const load=async()=>{current=await api('results?batch='+$('#formal-reg-batch').value);$('#formal-reg-runs').innerHTML=current.results.filter(r=>r.waves_enabled!==false).map(r=>`<label><input type="checkbox" data-formal-reg-run="${r.id}"><span class="mono">${esc(r.case)} <small class="muted">seed ${r.seed}</small></span>${status(r.status)}</label>`).join('');};
    change('#formal-reg-batch',load);await load();
    click('#formal-add-selected',async()=>{$$('[data-formal-reg-run]:checked').forEach(el=>{const r=current.results.find(r=>r.id===el.dataset.formalRegRun);this.add({kind:'regression',batch:current.id,run:r.id,name:`${r.case} · seed ${r.seed}`});});$('#dialog').close();await this.render();});
  },

  resultMarkup(s) {
    const c=s.counts;
    return `<div class="formal-result-head"><span class="tag teal">已生成</span><span class="muted">尚未运行 Formal · 请同时检查 assert 与终点 cover</span><span class="spacer"></span>${btn('formal-download','下载完整验证包','external','primary')}</div><div class="formal-metrics">${[[c.patterns,'patterns'],[c.cycles,'采样周期'],[c.input_assumptions,'输入 assume'],[c.output_assertions,'输出 assert'],[c.terminal_covers,'终点 cover']].map(([v,l])=>`<div><strong>${v.toLocaleString()}</strong><span>${l}</span></div>`).join('')}</div><div class="table-wrap"><table><thead><tr><th>pattern</th><th>来源用例</th><th>复位后区间</th><th>采样周期</th></tr></thead><tbody>${s.patterns.map(p=>`<tr><td class="mono">${p.pattern}</td><td>${esc(p.name)}</td><td>${p.segments.map(x=>`segment ${x.segment} · ${x.cycles} cycles`).join(' / ')}</td><td class="mono">${p.cycles}</td></tr>`).join('')}</tbody></table></div><div class="formal-preview-bar"><select id="formal-preview-file">${[['trace_properties.sv','原生 SVA · assume / assert'],['formal_top.sv','Formal 顶层与 DUT 例化'],['samples.csv','逐拍输入输出数据'],['transactions.json','握手事务汇总'],['integration.json','初始化与工具接入说明'],['run_bmc.sby','SBY 有界验证入口'],['run_cover.sby','SBY 终点覆盖入口']].map(([f,l])=>`<option value="${f}">${l}</option>`).join('')}</select><span class="spacer"></span>${btn('formal-download-file','下载此文件','save','small subtle')}</div><div id="formal-code-preview"></div><div id="formal-preview-note" class="formal-preview-note"></div>`;
  },
  bindResult(s) {
    click('#formal-download',()=>this.download(s.id,'package.zip'));
    click('#formal-download-file',()=>this.download(s.id,$('#formal-preview-file').value));
    change('#formal-preview-file',()=>this.showPreview(s.id,$('#formal-preview-file').value));
    this.showPreview(s.id,'trace_properties.sv').catch(e=>toast(e.message,true));
  },
  async showPreview(id,file) {
    const data=await api(`formal-preview?id=${id}&file=${file}`);
    const el=$('#formal-code-preview');if(!el)return;
    el.innerHTML='';
    this.preview=CodeMirror(el,{value:data.text,mode:file.endsWith('.sv')?'text/x-systemverilog':'text/plain',lineNumbers:true,readOnly:true,lineWrapping:false,viewportMargin:10});
    $('#formal-preview-note').textContent=data.truncated?'预览显示前 120 KB；下载文件和验证包保留完整内容。':'代码来自本次 FSDB 提取结果。原生 SVA 与 SBY 适配副本分别保存。';
  },
  async download(id,file) {
    const response=await fetch(`/api/formal-download?id=${id}&file=${file}`,{headers:{'X-Studio-Token':token}});
    if(!response.ok)throw Error((await response.json()).error);
    const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');
    a.href=url;a.download=file==='package.zip'?`cmdbuf_formal_${id}.zip`:file;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
};
