"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const state = { geometry: null, sourceGeometry: null, antennas: [], antennaPreview: false, result: null, jobId: null, active: false, loading: false, selectedMode: 0, selectedFace: null, poll: null, generation: 0, exampleAvailable: true, startedAt: null, executionLocation: "unknown", fieldCache: new Map(), fieldData: null, fieldGeneration: 0, ports: [], portsJsonDirty: false, estimate: null, estimateDetailParts: [], estimateTimer: null, estimateGeneration: 0, jobEstimate: null, transient: null, historyEstimates: [], transientSamples: new Map(), memoryPlan: null, jobMemory: null, memoryLimitState: "pending", memoryConfig: null };
  Object.assign(state,{memoryGeneration:0,memoryTimer:null,memoryRefreshing:false,memoryError:"",memoryFetchedAt:null,infoGeneration:0,jobStatus:null,jobMemoryAt:null,jobMemoryConfig:null});
  Object.assign(state,{autoOpenings:false,excludedCapFaces:[],autoCandidateHints:[]});
  const statuses = { queued: "順番待ち", running: "計算中", completed: "計算完了", failed: "計算エラー", cancelled: "停止済み", ready: "形状読込済み", loading: "形状を確認中" };
  const mappings = { "geometry-mode": "geometry_mode", "solid-index": "solid_index", "maxh-mm": "maxh_mm", "order": "order", "n-modes": "n_modes", "target-ghz": "target_ghz", "threads": "threads", "rs-nohm": "rs_nohm", "bulk-epsr": "bulk_epsr", "bulk-tand": "bulk_tand", "oxide-thickness-nm": "oxide_thickness_nm", "oxide-epsr": "oxide_epsr", "oxide-tand": "oxide_tand", "loss-model": "loss_model", "temperature-k": "temperature_k", "photon-number": "photon_number", "tls-nc": "tls_nc", "tls-beta": "tls_beta", "q-res": "q_res" };
  Object.assign(mappings, {"analysis-mode":"analysis_mode","mode-search":"mode_search","spectrum-start-ghz":"spectrum_start_ghz","spectrum-stop-ghz":"spectrum_stop_ghz","spectrum-points":"spectrum_points","field-resolution":"field_resolution"});
  const num = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const fmt = (value, digits=4) => num(value) ? Number(value).toLocaleString("en-US", { maximumFractionDigits: digits }) : "—";
  const qfmt = value => !num(value) ? "未設定" : Number(value) >= 1e5 || (Number(value) > 0 && Number(value) < .001) ? Number(value).toExponential(3) : fmt(value, 2);
  const sci = value => num(value) ? Number(value).toExponential(3) : "—";
  const elem = (tag, text, className) => { const el=document.createElement(tag); if (text!==undefined) el.textContent=text; if(className) el.className=className; return el; };
  const blank = text => elem("p", text, "empty-inline");
  const setText = (id,text) => { $(id).textContent=text; };
  function notice(message, error=false) { $("notice").hidden=false; $("notice").classList.toggle("error",error); setText("notice-text",message); }
  function setStatus(status) { const el=$("status-pill"); el.className="status-pill "+status; el.replaceChildren(elem("span"), document.createTextNode(statuses[status] || status)); }
  function syncButtons() {
    const busy=state.loading || state.active;
    $("run-button").disabled=busy || !state.geometry;
    setText("run-label",state.active?"解析を実行中":state.loading?"形状を確認中":"解析を実行");
    setText("run-hint",state.active?(state.executionLocation==="local"?"計算と結果の保存をこのPCで実行しています":"計算と結果の保存を接続先のPCで実行しています"):state.loading?"CAD の読み込みと面の確認をしています":state.geometry?"設定した条件でローカル計算を開始":"先に STEP 形状を読み込んでください");
    $("example-button").disabled=busy || !state.exampleAvailable;
    $("step-file").disabled=busy;
    $("drop-zone").classList.toggle("busy",busy);
    $("geometry-mode").disabled=busy;
    $("solid-index").disabled=busy || !state.geometry;
    $("cancel-button").hidden=!state.active;
    $("add-port-button").disabled=state.selectedFace===null || busy || state.antennaPreview;
    $("review-caps-button").disabled=busy || !(state.sourceGeometry?.faces||state.geometry?.faces||[]).some(f=>f.artificial_cap);
    $("apply-ports-json").disabled=busy;
    $("add-antenna-button").disabled=state.selectedFace===null || busy || state.antennaPreview || state.antennas.length>=8;
    $("preview-antennas").disabled=busy || !state.geometry || !state.antennas.length;
    $("restore-source").hidden=!state.antennaPreview;
    renderAutoOpenings();
  }
  function updateExecutionLocation(info=null){
    const loopback=["localhost","127.0.0.1","[::1]","::1"].includes(window.location.hostname);
    const connected=info!==null,local=connected&&info.local_only===true&&loopback;
    state.executionLocation=!connected?"unknown":local?"local":"server";
    setText("runtime-label",!connected?"実行先に未接続":local?"このPCで計算・保存":"接続先のPCで計算・保存");
    $("runtime-dot").className="local-dot "+(!connected?"checking":local?"":"remote");
    $("runtime-location").title=!connected?"このPCでアプリを起動すると、ブラウザから計算を操作できます。":local?"ブラウザで操作し、このPCのソルバーで計算します。STEP と解析結果もこのPCに保存します。":"ブラウザの接続先で計算し、STEP と解析結果を保存します。ブラウザが動くPCとは異なる場合があります。";
    syncButtons();
  }
  async function api(path, options={}) {
    const {timeoutMs=0,...requestOptions}=options,controller=timeoutMs?new AbortController():null;
    const timer=controller?setTimeout(()=>controller.abort(),timeoutMs):null;
    try {
      const response=await fetch(path,controller?{...requestOptions,signal:controller.signal}:requestOptions);
      let data;
      try { data=await response.json(); } catch(e) { if(controller?.signal.aborted)throw e;throw new Error("サーバーから有効な応答を取得できませんでした ("+response.status+")"); }
      if(!response.ok) {
        const d=data.detail || data.error || response.statusText;
        throw new Error(Array.isArray(d)?d.map(x=>(x.loc||[]).join(".")+": "+x.msg).join("\n"):typeof d==="string"?d:JSON.stringify(d));
      }
      return data;
    } catch(e) {if(controller?.signal.aborted)throw new Error("応答の待ち時間を超えました。起動状態を確認して再取得してください。");throw e;}
    finally {if(timer!==null)clearTimeout(timer);}
  }
  const jsonPost = body => ({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  function updateAnalysisMode(){
    const openings=$("analysis-mode").value==="all_openings_qc";
    $("spectrum-enabled").disabled=openings;if(openings)$("spectrum-enabled").checked=false;
    setText("analysis-help",openings?"指定した開口からの流出電力と、空洞の蓄積エネルギーを基準とする Qc を求めます。この解析では S11 を計算しません。全ての実開口を指定したことは自動では保証されません。":"固有モードと内部損失を求め、同軸アンテナを指定した場合は S11 を計算します。");
    scheduleEstimate();
  }
  function duration(seconds){
    if(!num(seconds))return "未推定";const value=Math.max(0,Number(seconds));
    if(value<60)return Math.ceil(value)+" 秒";
    if(value<3600)return Math.ceil(value/60)+" 分";
    const h=Math.floor(value/3600),m=Math.ceil((value%3600)/60);return h+" 時間"+(m?" "+m+" 分":"");
  }
  function finishTime(value){
    if(value===null||value===undefined||value==="")return "";
    const d=new Date(typeof value==="number"?value*1000:value);if(!Number.isFinite(d.getTime()))return "";
    return d.toLocaleString("ja-JP",{timeZone:"Asia/Tokyo",month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})+" JST";
  }
  function remainingEstimate(estimate){return Math.max(0,Number(estimate.remaining_s??estimate.total_s??0)-(estimate.receivedAt?(Date.now()-estimate.receivedAt)/1000:0));}
  function estimateShort(estimate){const remaining=remainingEstimate(estimate),time=finishTime(estimate.estimated_finish_at);return (remaining>0?"残り約 "+duration(remaining):"見積り更新待ち")+(time?" · 完了目安 "+time:"");}
  function estimateBasis(estimate){const confidence=({low:"概算",medium:"過去の実測を参考",high:"近い条件の実測を参考"})[estimate.confidence]||"概算";return confidence+(estimate.basis?" · "+(Array.isArray(estimate.basis)?estimate.basis.join(" / "):typeof estimate.basis==="string"?estimate.basis:JSON.stringify(estimate.basis)):"");}
  function memoryGiB(bytes){return num(bytes)&&Number(bytes)>=0?fmt(Number(bytes)/1073741824,2)+" GiB":"未取得";}
  function memoryLow(memory){return ["low_memory","insufficient_memory","budget_exceeded","system_reserve_low"].includes(memory.status)||memory.violated===true;}
  function renderMemory(){
    const memory=state.memoryPlan||{},ready=state.memoryLimitState==="ready",pending=state.memoryLimitState==="pending";
    $("memory-panel").classList.toggle("memory-low",ready&&memoryLow(memory));
    $("refresh-memory").disabled=state.memoryRefreshing;setText("refresh-memory",state.memoryRefreshing?"取得中…":"再取得");
    setText("memory-status",pending?(state.memoryPlan?"前回の取得値を表示・更新中…":"メモリ情報を取得中…"):state.memoryLimitState==="unavailable"?"取得できませんでした。「再取得」で再試行できます。":state.memoryLimitState==="invalid"?"要素次数とモード数を確認してください。":memory.detected===false||memory.status==="unavailable"?"このPCのメモリ情報を取得できません。":memoryLow(memory)?"現在の空きメモリでは余裕が不足しています。":"計算を実行するPCの検出値");
    setText("memory-total",memoryGiB(memory.total_bytes));setText("memory-available",memoryGiB(memory.available_bytes));setText("memory-budget",memoryGiB(memory.budget_bytes));
    setText("memory-dof",ready&&num(memory.dof_limit)?fmt(memory.dof_limit,0):pending?"更新中":state.memoryLimitState==="invalid"?"条件入力後に表示":"未取得");
    const config=state.memoryConfig;
    setText("memory-context",(ready&&config?config.order+" 次・"+config.n_modes+" モードの設定で見積もり":state.memoryPlan?"前回取得した値です。現在の条件では未確認です。":"現在の要素次数とモード数で上限を見積もります。")+(state.memoryFetchedAt?" · 取得 "+finishTime(state.memoryFetchedAt):""));
    const note=state.memoryError||memory.reason||memory.note;$("memory-note").hidden=!note;setText("memory-note",typeof note==="string"?note:"");
    const job=state.jobMemory;$("memory-job").hidden=!job;if(!job)return;
    $("memory-job").classList.toggle("memory-low",memoryLow(job));
    const running=["queued","running"].includes(state.jobStatus),stopped=["failed","cancelled"].includes(state.jobStatus);
    setText("memory-job-title",running?"この計算の実行中":stopped?"この計算の停止時":"この計算の記録");
    const jobConfig=state.jobMemoryConfig||job.model;
    setText("memory-job-context",(running?"実行中の計算に適用する値です。":"保存された診断値です。上の現在値とは異なります。")+(jobConfig?" · "+jobConfig.order+" 次・"+(jobConfig.n_modes??jobConfig.requested_modes)+" モード":"")+(state.jobMemoryAt?" · "+finishTime(state.jobMemoryAt):""));
    setText("memory-job-budget",memoryGiB(job.budget_bytes));setText("memory-job-limit",num(job.dof_limit)?fmt(job.dof_limit,0):"未取得");
    setText("memory-job-dofs",num(job.actual_dofs)?fmt(job.actual_dofs,0):"未計測");
    setText("memory-job-peak",num(job.actual_dofs)?memoryGiB(job.estimated_peak_bytes):"未計測");
    $("memory-live").hidden=!num(job.worker_rss_bytes);setText("memory-live","計算プロセスの使用量"+(running?"":"（最終取得）")+": "+memoryGiB(job.worker_rss_bytes));
    const jobNote=job.reason||job.note;$("memory-job-note").hidden=!jobNote;setText("memory-job-note",typeof jobNote==="string"?jobNote:"");
  }
  function scheduleMemory(){
    clearTimeout(state.memoryTimer);const generation=++state.memoryGeneration;
    state.memoryLimitState="pending";state.memoryError="";renderMemory();
    state.memoryTimer=setTimeout(()=>requestMemory(generation),300);
  }
  async function requestMemory(generation=++state.memoryGeneration){
    if(generation!==state.memoryGeneration)return false;
    const order=Number($("order").value),n_modes=Number($("n-modes").value);
    if(!Number.isInteger(order)||order<1||order>3||!Number.isInteger(n_modes)||n_modes<1||n_modes>12){state.memoryLimitState="invalid";renderMemory();return false;}
    state.memoryLimitState="pending";state.memoryError="";renderMemory();
    try {
      const data=await api("/api/memory?order="+order+"&n_modes="+n_modes,{cache:"no-store",timeoutMs:12000});
      if(generation!==state.memoryGeneration)return false;
      if(!data.memory||typeof data.memory!=="object")throw new Error("メモリ情報が応答に含まれていません。アプリを更新して起動し直してください。");
      state.memoryPlan=data.memory;state.memoryConfig={order,n_modes};state.memoryFetchedAt=data.memory.sampled_at||Date.now()/1000;state.memoryLimitState="ready";renderMemory();return true;
    }catch(e){if(generation===state.memoryGeneration){state.memoryLimitState="unavailable";state.memoryError=e.message;renderMemory();}return false;}
  }
  async function loadServerInfo(){
    const generation=++state.infoGeneration;
    try {
      const info=await api("/api/info",{cache:"no-store",timeoutMs:12000});if(generation!==state.infoGeneration)return false;
      if(!state.memoryPlan&&info.memory&&typeof info.memory==="object")state.memoryPlan=info.memory;
      renderMemory();updateExecutionLocation(info);setText("solver-label",(info.solver||"FEM")+" "+(info.version||""));setText("app-version",info.app_version?"v"+info.app_version:"");
      $("app-version").title="画面ファイル: "+(document.documentElement.dataset.assetVersion||"未確認");state.exampleAvailable=info.example_available!==false;syncButtons();return true;
    }catch(e){if(generation===state.infoGeneration){updateExecutionLocation();setText("solver-label","サーバー接続エラー");notice("ローカルサーバーに接続できません。起動状態を確認してください。 "+e.message,true);}return false;}
  }
  async function refreshMemory(){
    if(state.memoryRefreshing)return;
    clearTimeout(state.memoryTimer);state.memoryRefreshing=true;renderMemory();
    try{await Promise.allSettled([loadServerInfo(),requestMemory()]);if(!state.active)scheduleEstimate(false);}
    finally{state.memoryRefreshing=false;renderMemory();}
  }
  function scheduleEstimate(refreshMemoryPlan=true){
    clearTimeout(state.estimateTimer);state.estimateGeneration++;
    if(refreshMemoryPlan)scheduleMemory();
    if(state.active)return;
    state.estimate=null;setText("estimate-duration","条件を確認中…");setText("estimate-detail","形状・設定・過去の実測から推定します。");
    const generation=state.estimateGeneration;state.estimateTimer=setTimeout(()=>requestEstimate(generation),650);
  }
  async function requestEstimate(generation){
    if(generation!==state.estimateGeneration||state.active)return;
    let config;try{config=collectConfig(true);}catch{if(generation===state.estimateGeneration){setText("estimate-duration","条件入力後に表示");setText("estimate-detail","入力中の寸法・境界・掃引条件を確認してください。");}return;}
    try{
      const data=await api("/api/estimate",{...jsonPost(config),timeoutMs:12000});if(generation!==state.estimateGeneration||state.active)return;
      const estimate=data.estimate;state.estimate=estimate;
      setText("estimate-title","全解析の所要時間（概算）");
      setText("estimate-duration",num(estimate.lower_s)&&num(estimate.upper_s)?duration(estimate.lower_s)+"〜"+duration(estimate.upper_s):"約 "+duration(estimate.total_s??estimate.remaining_s));
      const parts=[estimateBasis(estimate)];if(Number(estimate.queued_wait_s)>0)parts.push("順番待ち約 "+duration(estimate.queued_wait_s));const scope=["形状・メッシュ・固有モード・電場出力"];if(config.spectrum_enabled&&config.antennas.length)scope.push("S11");if(config.antenna_sweep)scope.push("長さ掃引");parts.push(scope.join("・")+"を含む。条件によって増減します。");state.estimateDetailParts=parts;renderPreflightFinish();
    }catch{if(generation===state.estimateGeneration){setText("estimate-duration","見積りを取得できません");setText("estimate-detail","設定を変更するか、メモリ欄の「再取得」で再試行できます。");}}
  }
  function renderPreflightFinish(){
    if(state.active||!state.estimate)return;
    const estimate=state.estimate,seconds=num(estimate.remaining_s)?Number(estimate.remaining_s):Number(estimate.total_s||0)+Number(estimate.queued_wait_s||0);
    const finish=finishTime(Date.now()/1000+Math.max(0,seconds));
    setText("estimate-detail",[...state.estimateDetailParts,"今開始した場合の完了目安 "+finish].join(" · "));
  }
  function startTransient(label,low,high){const previous=state.transientSamples.get(label);state.transient={label,startedAt:Date.now(),low:previous?Math.max(.5,previous*.5):1,high:previous?Math.max(3,previous*3):high,calibrated:!!previous};renderProgress();}
  function endTransient(){if(state.transient)state.transientSamples.set(state.transient.label,(Date.now()-state.transient.startedAt)/1000);state.transient=null;renderProgress();}
  function renderProgress(){
    const panel=$("work-progress");panel.hidden=!state.active&&!state.transient;
    if(state.transient){const elapsed=(Date.now()-state.transient.startedAt)/1000;setText("work-progress-title",state.transient.label);setText("work-progress-time","経過 "+duration(elapsed));setText("work-progress-detail",(state.transient.calibrated?"この画面での前回処理を参考にした目安: ":"初回・未校正の参考幅: ")+duration(state.transient.low)+"〜"+duration(state.transient.high)+"。形状の複雑さにより長くなる場合があります。");return;}
    if(!state.active)return;
    const e=state.jobEstimate;setText("work-progress-title",e?.stage||"解析を実行中");setText("work-progress-time",e?estimateShort(e):"残り時間を推定中");
    const parts=[];if(e){if(num(e.lower_s)&&num(e.upper_s)){const elapsed=(Date.now()-(e.receivedAt||Date.now()))/1000;parts.push("残りの目安 "+duration(Math.max(0,e.lower_s-elapsed))+"〜"+duration(Math.max(0,e.upper_s-elapsed)));}if(num(e.total_units)&&Number(e.total_units)>0)parts.push("処理単位 "+fmt(e.completed_units,0)+" / "+fmt(e.total_units,0));if(Number(e.queued_wait_s)>0)parts.push("順番待ち約 "+duration(e.queued_wait_s));parts.push(estimateBasis(e));}
    setText("work-progress-detail",parts.join(" · ")+" · 実際の完了時刻を保証するものではありません。");
  }
  function portQLabel(value,power,status){
    if(num(value))return qfmt(value);
    if(status==="below_power_threshold")return "流出微小・Qc未確定";
    if(num(power))return Number(power)<=0?"∞（流出 0）":"算出範囲外";
    return "未設定";
  }
  function renderPortResults(mode){
    const cavity=state.result?.config?.analysis_mode==="all_openings_qc",ports=mode.ports||[],root=$("port-results"),summary=$("port-energy-summary");
    setText("port-results-title",cavity?"各開口の空洞基準 Qc と流出電力":"各ポートの外部 Qc と流出電力");
    setText("port-results-method",cavity?"Qc,p = ω Ucavity / Pp。空洞に蓄えられたエネルギーを基準に評価します。S11 からの推定ではありません。":"Qc,p = ω Utotal / Pp。全解析領域の蓄積エネルギーを基準に評価します。");
    setText("loss-method-note",cavity?"空洞基準 Qc と全領域基準の負荷 QL は異なる正規化です。放射面は局所平面波の近似で、PML は使用していません。指定した面が実開口の全てかは形状と境界条件から確認してください。":"QL⁻¹ = Qi⁻¹ + Σ Qc,p⁻¹（全領域エネルギー基準）。未設定の損失パラメータは、数値として推定しません。");
    root.replaceChildren();summary.replaceChildren();summary.hidden=!ports.length;
    if(!ports.length){root.append(blank("ポート未定義 · 実機の Qc は未評価"));return;}
    const facts=[["空洞 U",mode.cavity_energy_j_normalized,"J"],["全領域 U",mode.total_energy_j_normalized,"J"],["全流出電力",mode.outgoing_power_w_normalized,"W"],[cavity?"合成 Qc（空洞基準）":"合成 Qc（全領域基準）",cavity?mode.q_external_cavity:mode.q_external_total_energy,""]];
    for(const [label,value,unit]of facts){const item=elem("div");item.append(elem("span",label),elem("strong",(unit?sci(value):qfmt(value))+(unit?" "+unit:"")));summary.append(item);}
    const table=elem("table",undefined,"data-table"),head=elem("thead"),tr=elem("tr"),body=elem("tbody");for(const label of ["境界 / 原形状の面","流出電力 W",cavity?"空洞 Qc":"外部 Qc"])tr.append(elem("th",label));head.append(tr);
    for(const port of ports){const row=elem("tr"),name=elem("td",port.name),faces=port.source_faces||port.faces||[];const kind=({radiation:"放射近似",surface_impedance:"表面波 Z",local_impedance:"表面波 Z",coax_tem:"同軸 TEM"})[port.kind]||port.kind||"";name.append(elem("small",kind+(port.selection==="artificial_cap_candidate"?" · 自動":"")+(faces.length?" · 面 "+faces.join(", "):""),"port-result-detail"));row.append(name,elem("td",sci(port.power_w_normalized)),elem("td",portQLabel(cavity?(port.qc_cavity??port.qc):(port.qc_total_energy??port.qc),port.power_w_normalized,port.qc_status)));body.append(row);}
    table.append(head,body);const scroll=elem("div",undefined,"table-scroll");scroll.append(table);root.append(scroll);
    root.append(elem("p","表示した U と P は固有モードの任意振幅に対応します。Qc は振幅に依存しません。","help-note"));
  }
  function updateLossModel() {
    const model=$("loss-model").value;
    $("rs-field").hidden=model!=="surface_rs";
    $("rs-nohm").required=model==="surface_rs";
    $("tls-settings").hidden=model!=="tls_qres";
    $("qres-field").hidden=model!=="tls_qres";
  }
  function updateGeometryHelp() {
    setText("geometry-mode-help",$("geometry-mode").value==="metal_void" ? "金属の外接直方体から金属を引いた空隙です。開口は人工キャップで閉じられます。面一覧で確認してください。" : "選択したソリッド全体を電磁場の領域とします。金属の体積を空洞として誤選択しないよう確認してください。");
  }
  function geometryWarnings(geo) { return [...(Array.isArray(geo.warnings)?geo.warnings:[]), ...(geo.warning?[geo.warning]:[])]; }
  function applyGeometry(geo) {
    state.geometry=geo;
    if(geo.mode) $("geometry-mode").value=geo.mode;
    updateGeometryHelp();
    const solids=Array.isArray(geo.solids)?geo.solids:[];
    const faces=Array.isArray(geo.faces)?geo.faces:[];
    $("file-summary").hidden=false;
    setText("filename",geo.filename || "STEP 形状");
    $("filename").title=geo.filename || "";
    setText("file-facts",solids.length+" 領域 · "+faces.length+" 面");
    setText("geometry-badge","読込済み");
    $("geometry-badge").classList.add("loaded");
    setText("solid-count",solids.length || "1");
    setText("face-count",faces.length);
    $("solid-index").replaceChildren(...solids.map((s,i)=>new Option("領域 "+(Number(s.index??i)+1)+" · "+fmt(s.volume_mm3,1)+" mm³",s.index??i)));
    if(!solids.length) $("solid-index").add(new Option("領域 1","0"));
    $("solid-index").value=String(geo.selected??0);
    const selected=solids.find(s=>s.index===geo.selected) || solids[0];
    const bbox=geo.bbox_mm || selected?.bbox_mm;
    setText("bbox-value",Array.isArray(bbox)&&bbox.length===2?bbox[0].map((x,i)=>fmt(bbox[1][i]-x,2)).join(" × "):"—");
    $("face-select").replaceChildren(new Option("未選択",""));
    for(const f of faces) {
      const id=f.id??f.index;
      if(id===undefined) continue;
      $("face-select").add(new Option("面 "+id+(f.artificial_cap?" · 人工キャップ":"")+" · "+fmt(f.area_mm2,1)+" mm²",id));
    }
    $("face-select").disabled=!faces.length;
    viewer.setData(geo.preview);
    selectFace(null);
    setText("viewer-subtitle",geo.mode==="metal_void"?"抽出した空隙・人工キャップを確認":"選択した電磁場の解析領域");
    if(!geo.preview?.vertices?.length) {
      $("viewer-empty").hidden=false;
      $("viewer-empty").querySelector("h3").textContent="プレビューを取得できませんでした";
      $("viewer-empty").querySelector("p").textContent="面一覧と領域寸法を確認してください。";
    }
    const warnings=geometryWarnings(geo);
    if(warnings.length) notice(warnings.join(" "));
    syncButtons();
  }
  async function loadGeometry(file) {
    if(state.loading || state.active) return;
    state.loading=true;startTransient("STEP 形状の読み込み",30,180); syncButtons(); setStatus("loading"); $("notice").hidden=true;
    try {
      let geo;
      if(file) { const form=new FormData(); form.append("file",file); form.append("geometry_mode",$("geometry-mode").value); geo=await api("/api/inspect",{method:"POST",body:form}); }
      else geo=await api("/api/example",{method:"POST"});
      state.ports=[];$("cap-review").hidden=true;$("sweep-enabled").checked=false;state.antennas=[];state.antennaPreview=false;state.sourceGeometry=geo;configureNewOpenings(geo);renderAntennas();
      applyGeometry(geo); setStatus("ready");
    } catch(e) { notice(e.message,true); setStatus("failed"); }
    finally { state.loading=false;endTransient(); syncButtons(); $("step-file").value="";scheduleEstimate(); }
  }
  async function reinspect(changeMode=false) {
    updateGeometryHelp();
    if(!state.geometry || state.loading || state.active) return;
    const original=state.geometry;
    state.loading=true;startTransient("解析領域の確認",30,180);syncButtons();setStatus("loading");
    try {
      const geo=await api("/api/geometries/"+encodeURIComponent(original.id)+"/inspect",jsonPost({geometry_mode:$("geometry-mode").value,solid_index:changeMode?0:Number($("solid-index").value)}));
      state.sourceGeometry=geo;state.antennas=[];state.ports=[];state.antennaPreview=false;$("sweep-enabled").checked=false;configureNewOpenings(geo);renderAntennas();applyGeometry(geo);$("cap-review").hidden=true;setStatus("ready");
      notice(geometryWarnings(geo).concat(["領域の変更に伴い、ポート面の設定をクリアしました。"]).join(" "));
    } catch(e) { $("geometry-mode").value=original.mode || "vacuum_solid"; $("solid-index").value=String(original.selected??0);updateGeometryHelp();notice(e.message,true);setStatus("failed"); }
    finally {state.loading=false;endTransient();syncButtons();scheduleEstimate();}
  }
  function selectFace(id) {
    state.selectedFace=id===null || id===""?null:Number(id);
    $("face-chip").hidden=state.selectedFace===null;
    const f=(state.geometry?.faces||[]).find(x=>x.id===state.selectedFace);
    setText("selected-face",state.selectedFace===null?"—":state.selectedFace+(f?.artificial_cap?" · 人工キャップ":""));
    $("face-select").value=state.selectedFace===null?"":String(state.selectedFace);
    syncButtons(); viewer.draw();
  }
  function openingCandidates(){
    const source=state.sourceGeometry||(!state.antennaPreview?state.geometry:null);
    if(source&&Array.isArray(source.faces))return source.mode==="metal_void"?source.faces.filter(f=>f.artificial_cap):[];
    return $("geometry-mode").value==="metal_void"?state.autoCandidateHints:[];
  }
  function manualPort(port){const {internal_auto,...manual}=port;return {...manual,faces:[...port.faces]};}
  function syncAutomaticPorts(){
    const manual=state.ports.filter(port=>!port.internal_auto);
    state.ports=manual;
    if(!state.autoOpenings)return;
    const assigned=new Set(manual.flatMap(port=>port.faces)),names=new Set([...manual,...state.antennas].map(item=>item.name));
    for(const face of openingCandidates()){
      const id=face.id??face.index;
      if(state.excludedCapFaces.includes(id)||assigned.has(id)||state.antennas.some(ant=>ant.face===id))continue;
      let name="opening_face_"+id;while(names.has(name))name+="_";names.add(name);
      state.ports.push({name,faces:[id],kind:"radiation",radiation_epsr:1,impedance_ohm:376.730313668,internal_auto:true});
    }
  }
  function configureNewOpenings(geometry){
    state.excludedCapFaces=[];state.autoCandidateHints=[];state.autoOpenings=geometry.mode==="metal_void";
    $("analysis-mode").value=state.autoOpenings&&openingCandidates().length?"all_openings_qc":"resonance";
    if(state.autoOpenings)$("spectrum-enabled").checked=false;
    updateAnalysisMode();renderPorts();
  }
  function setAutomaticOpenings(enabled){
    const mode=state.sourceGeometry?.mode||state.geometry?.mode||$("geometry-mode").value;
    state.autoOpenings=enabled&&mode==="metal_void";
    if(state.autoOpenings){$("analysis-mode").value=openingCandidates().length?"all_openings_qc":"resonance";$("spectrum-enabled").checked=false;}
    updateAnalysisMode();renderPorts();syncButtons();viewer.draw();
  }
  function excludeOpening(face,excluded=true){
    state.excludedCapFaces=state.excludedCapFaces.filter(id=>id!==face);
    if(excluded)state.excludedCapFaces.push(face);
    state.excludedCapFaces.sort((a,b)=>a-b);syncAutomaticPorts();
  }
  function editOpening(port,updates){
    if(port.internal_auto){
      for(const face of port.faces)if(!state.excludedCapFaces.includes(face))state.excludedCapFaces.push(face);
      delete port.internal_auto;
      if(!state.ports.includes(port))state.ports.push(port);
    }
    Object.assign(port,updates);syncAutomaticPorts();portsChanged();
  }
  function removeOpening(port){
    if(port.internal_auto)for(const face of port.faces)excludeOpening(face);
    else state.ports=state.ports.filter(item=>item!==port);
    renderPorts();portsChanged();viewer.draw();
  }
  function showOpeningFace(face){if(state.antennaPreview)restoreSourceGeometry();selectFace(face);}
  function renderAutoOpenings(){
    const mode=state.sourceGeometry?.mode||state.geometry?.mode||$("geometry-mode").value;
    $("automatic-openings").hidden=mode!=="metal_void";
    $("auto-openings").checked=state.autoOpenings;$("auto-openings").disabled=state.loading||state.active;
    const candidates=openingCandidates(),count=state.ports.filter(port=>port.internal_auto).length;
    $("auto-port-legend").hidden=!count;
    setText("auto-openings-summary",!candidates.length?"抽出した開口候補は 0 面です。必要な開口を CAD で選び、外部境界に追加してください。":candidates.length+" 面を検出 · 自動ポート "+count+" 個"+(state.autoOpenings?"":" · 自動設定はオフ"));
    const list=$("auto-openings-list");list.replaceChildren();
    for(const face of candidates){
      const id=face.id??face.index,manual=state.ports.some(port=>!port.internal_auto&&port.faces.includes(id)),antenna=state.antennas.some(ant=>ant.face===id),excluded=state.excludedCapFaces.includes(id);
      const row=elem("div",undefined,"auto-opening-row"),show=elem("button","面 "+id,"text-button");show.type="button";show.addEventListener("click",()=>showOpeningFace(id));
      row.append(show,elem("span",antenna?"アンテナ":manual?"手動設定":excluded?"除外中":state.autoOpenings?"自動設定":"未設定"));
      if(!manual&&!antenna){const action=elem("button",excluded?"戻す":"除外","text-button");action.type="button";action.disabled=!state.autoOpenings||state.loading||state.active;action.addEventListener("click",()=>{excludeOpening(id,!excluded);renderPorts();viewer.draw();});row.append(action);}
      list.append(row);
    }
  }
  function validatePorts(ports) {
    if(!Array.isArray(ports))throw new Error("外部境界の定義は配列 [...] で指定してください。");
    const faces=new Set(),names=new Set();
    return ports.map(port=>{
      if(!port||typeof port.name!=="string"||!port.name.trim())throw new Error("各外部境界に名前を指定してください。");
      if(names.has(port.name))throw new Error("外部境界の名前が重複しています。");names.add(port.name);
      if(!Array.isArray(port.faces)||!port.faces.length||port.faces.some(f=>!Number.isInteger(f)||f<1))throw new Error(port.name+": 正の整数の面 ID を指定してください。");
      for(const f of port.faces){if(faces.has(f))throw new Error("面 "+f+" が複数の境界に指定されています。");faces.add(f);}
      const kind=port.kind||"surface_impedance";
      if(!["surface_impedance","radiation"].includes(kind))throw new Error(port.name+": 境界の種類を選択してください。");
      const z=port.impedance_ohm??376.730313668,eps=port.radiation_epsr??1;
      if(kind==="surface_impedance"&&(!num(z)||Number(z)<=0))throw new Error(port.name+": 表面波インピーダンスは正の値にしてください。");
      if(kind==="radiation"&&(!num(eps)||Number(eps)<1))throw new Error(port.name+": 外部媒質の εr は 1 以上にしてください。");
      return {...port,kind,impedance_ohm:Number(z),radiation_epsr:Number(eps),faces:[...port.faces]};
    });
  }
  function readPorts(){syncAutomaticPorts();return validatePorts(state.ports.filter(port=>!port.internal_auto).map(manualPort));}
  function syncPortsJson(){if(!state.portsJsonDirty)$("ports-json").value=JSON.stringify(state.ports.filter(port=>!port.internal_auto).map(manualPort),null,2);}
  function updatePortCount(){setText("port-count",(state.ports.length+state.antennas.length)+" PORTS");refreshAntennaSelectors();scheduleEstimate();}
  function portsChanged(){syncPortsJson();updatePortCount();syncButtons();viewer.draw();}
  function renderPorts(){
    syncAutomaticPorts();
    const list=$("port-list");list.replaceChildren();
    if(!state.ports.length)list.append(blank("外部境界は未設定です。指定しない面は導体壁として扱います。"));
    state.ports.forEach((port,index)=>{
      const card=elem("article",undefined,"antenna-card port-card"),head=elem("div",undefined,"antenna-card-heading");
      const origin=elem("span",port.internal_auto?"自動設定":"手動設定","port-origin");
      head.append(elem("strong","OPENING "+String(index+1).padStart(2,"0")),origin);
      const edit=updates=>{editOpening(port,updates);origin.textContent="手動設定";remove.setAttribute("aria-label",port.name+" を削除");};
      const remove=elem("button","×","text-button");remove.type="button";remove.setAttribute("aria-label",port.name+(port.internal_auto?" を自動設定から除外":" を削除"));remove.addEventListener("click",()=>removeOpening(port));head.append(remove);card.append(head);
      const show=elem("button","原形状の面 "+port.faces.join(", ")+" を表示","text-button");show.type="button";show.addEventListener("click",()=>showOpeningFace(port.faces[0]));card.append(show);
      const fields=elem("div",undefined,"field-grid"),nameLabel=elem("label","名前","field"),name=elem("input");name.type="text";name.value=port.name;name.required=true;name.addEventListener("input",()=>edit({name:name.value}));name.addEventListener("change",renderPorts);nameLabel.append(name);
      const faceLabel=elem("label","原形状の面 ID","field"),face=elem("input");face.type="text";face.value=port.faces.join(", ");face.required=true;face.addEventListener("input",()=>edit({faces:face.value.split(/[,、\s]+/).filter(Boolean).map(Number)}));face.addEventListener("change",renderPorts);faceLabel.append(face);fields.append(nameLabel,faceLabel);card.append(fields);
      const kindLabel=elem("label","境界の種類","field"),kind=elem("select");kind.append(new Option("放射境界（平面波近似）","radiation"),new Option("表面波インピーダンスを指定","surface_impedance"));kind.value=port.kind||"surface_impedance";kindLabel.append(kind);card.append(kindLabel);
      const epsLabel=elem("label","外部媒質の εr","field"),eps=elem("input");eps.type="number";eps.min="1";eps.step="any";eps.value=port.radiation_epsr??1;eps.required=true;eps.addEventListener("input",()=>edit({radiation_epsr:eps.value===""?null:Number(eps.value)}));eps.addEventListener("change",renderPorts);epsLabel.append(eps);
      const zLabel=elem("label","表面波インピーダンス Ω","field"),z=elem("input");z.type="number";z.min="0.000001";z.step="any";z.value=port.impedance_ohm??376.730313668;z.required=true;z.addEventListener("input",()=>edit({impedance_ohm:z.value===""?null:Number(z.value)}));z.addEventListener("change",renderPorts);zLabel.append(z);
      const updateKind=()=>{const radiation=kind.value==="radiation";epsLabel.hidden=!radiation;eps.disabled=!radiation;zLabel.hidden=radiation;z.disabled=radiation;};kind.addEventListener("change",()=>{edit({kind:kind.value});renderPorts();});updateKind();card.append(epsLabel,zLabel);list.append(card);
    });
    state.portsJsonDirty=false;syncPortsJson();setText("ports-json-state","手動カードと同期済み");updatePortCount();renderAutoOpenings();
  }
  function addOpening(face){
    if(state.ports.some(p=>p.faces.includes(face))||state.antennas.some(a=>a.face===face))throw new Error("面 "+face+" は外部境界またはアンテナに指定済みです。");
    let index=state.ports.length+1;while([...state.ports,...state.antennas].some(p=>p.name==="P"+index))index++;
    state.ports.push({name:"P"+index,faces:[face],kind:"radiation",radiation_epsr:1,impedance_ohm:376.730313668});
  }
  function reviewCaps(){
    const faces=(state.sourceGeometry?.faces||state.geometry?.faces||[]).filter(f=>f.artificial_cap),list=$("cap-review-list");list.replaceChildren();
    for(const face of faces){const id=face.id??face.index,label=elem("label",undefined,"check-field"),input=elem("input");input.type="checkbox";input.value=id;input.dataset.capFace=id;input.disabled=state.ports.some(p=>p.faces.includes(id))||state.antennas.some(a=>a.face===id);label.append(input,document.createTextNode("面 "+id+" · "+fmt(face.area_mm2,2)+" mm²"+(input.disabled?" · 設定済み":"")));list.append(label);}
    $("cap-review").hidden=false;
  }
  function collectConfig(silent=false) {
    for(const input of document.querySelectorAll(".controls input[type=number]")) {
      if(input.disabled)continue;
      if(input.id==="rs-nohm" && $("loss-model").value!=="surface_rs") continue;
      if(!input.checkValidity()) {if(!silent){input.closest("details")?.setAttribute("open","");input.reportValidity();}throw new Error("入力条件を確認してください: "+input.closest("label").childNodes[0].textContent.trim());}
    }
    const config={geometry_id:state.geometry?.id??null,all_artificial_caps:state.autoOpenings,excluded_artificial_cap_faces:[...state.excludedCapFaces].sort((a,b)=>a-b),ports:readPorts(),antennas:readAntennas(),spectrum_enabled:$("spectrum-enabled").checked,driven_port:$("driven-port").value||null};
    for(const [id,key] of Object.entries(mappings)) config[key]=["geometry-mode","loss-model","mode-search","analysis-mode"].includes(id)?$(id).value:$(id).value===""?null:Number($(id).value);
    if(config.analysis_mode==="all_openings_qc")config.spectrum_enabled=false;
    if(config.spectrum_enabled && config.spectrum_stop_ghz<=config.spectrum_start_ghz)throw new Error("周波数スキャンの終了値は開始値より大きくしてください。");
    config.antenna_sweep=null;
    for(const ant of config.antennas){if(config.ports.some(p=>p.name===ant.name))throw new Error("アンテナと外部境界の名前が重複しています。");if(config.ports.some(p=>p.faces.includes(ant.face)))throw new Error("面 "+ant.face+" にアンテナと外部境界を重複して指定できません。");}
    if($("sweep-enabled").checked){const lengths=$("sweep-lengths").value.split(/[,、\s]+/).filter(Boolean).map(Number);if(!$("sweep-antenna").value || !lengths.length || lengths.length>21 || lengths.some(v=>!Number.isFinite(v)||v<0))throw new Error("掃引アンテナと、0 mm 以上の突出長さを 21 点以内で指定してください。");config.antenna_sweep={antenna:$("sweep-antenna").value,lengths_mm:[...new Set(lengths)]};}
    return config;
  }
  function restoreCapPorts(resultGeometry,sourceGeometry=null){
    if(sourceGeometry)state.sourceGeometry=sourceGeometry;
    const ids=resultGeometry?.external_boundary_selection?.artificial_cap_candidates;
    if(Array.isArray(ids))state.autoCandidateHints=ids.map(id=>({id,artificial_cap:true}));
    else if(Array.isArray(resultGeometry?.external_ports)){
      const faces=resultGeometry.external_ports.flatMap(port=>port.source_faces?.length?port.source_faces:(port.faces||[]).map(id=>resultGeometry.faces?.find(face=>face.id===id)?.source_face).filter(id=>Number.isInteger(id)&&id>0));
      state.autoCandidateHints=[...new Set(faces)].map(id=>({id,artificial_cap:true}));
    }
    renderPorts();
  }
  function restoreConfig(config,resultGeometry=null) {
    if(!config)return;
    state.sourceGeometry=null;state.geometry=config.geometry_id?{id:config.geometry_id,mode:config.geometry_mode,selected:config.solid_index}:null;state.autoCandidateHints=[];
    for(const [id,key] of Object.entries(mappings))if(key in config)$(id).value=config[key]??"";
    state.autoOpenings=config.all_artificial_caps===true;
    state.excludedCapFaces=[...(config.excluded_artificial_cap_faces||[])];
    $("analysis-mode").value=config.analysis_mode||"resonance";
    state.ports=(config.ports||[]).map(manualPort);
    state.antennas=(config.antennas||[]).map(a=>({...a}));state.antennaPreview=false;
    restoreCapPorts(resultGeometry);renderAntennas();
    $("spectrum-enabled").checked=config.spectrum_enabled===true;
    $("driven-port").value=config.driven_port||"";
    $("sweep-enabled").checked=!!config.antenna_sweep;
    if(config.antenna_sweep){$("sweep-antenna").value=config.antenna_sweep.antenna;$("sweep-lengths").value=config.antenna_sweep.lengths_mm.join(", ");}
    updateLossModel();updateAnalysisMode();updatePortCount();
  }
  function switchTab(name) {
    for(const tab of document.querySelectorAll("[data-tab]")) {
      const active=tab.dataset.tab===name;
      tab.classList.toggle("active",active);tab.setAttribute("aria-selected",String(active));tab.tabIndex=active?0:-1;
      $("panel-"+tab.dataset.tab).hidden=!active;
    }
    if(name==="field")loadField();
  }
  function resetResults() {
    state.result=null;state.selectedMode=0;
    for(const id of ["metric-frequency","metric-qi","metric-ql","metric-g"]) {setText(id,"—");$(id).classList.remove("unset");}
    setText("metric-mode","モード未選択");
    $("download-results").hidden=true;$("download-fields").hidden=true;
    const td=elem("td","計算中です。完了すると共振モードが表示されます。","table-empty");td.colSpan=5;
    const row=elem("tr");row.append(td);$("modes-body").replaceChildren(row);
    $("computation-facts").hidden=true;$("result-warnings").hidden=true;$("mode-search-summary").hidden=true;
    $("port-energy-summary").hidden=true;$("participation-results").replaceChildren(blank("解析結果がありません"));
    $("port-results").replaceChildren(blank("解析結果がありません"));
    state.fieldData=null;state.fieldGeneration++;$("field-empty").hidden=false;setText("field-empty","解析完了後に電場分布を表示します。");$("field-legend").hidden=true;$("download-field-json").hidden=true;
    $("field-mode").replaceChildren(new Option("未計算",""));$("spectrum-mode").replaceChildren(new Option("未計算",""));renderSpectrum(null);renderSweep(null);drawField();
    syncButtons();
  }
  async function startJob() {
    if(!state.geometry || state.active || state.loading)return;
    try {
      const config=collectConfig();
      state.active=true;clearTimeout(state.estimateTimer);state.estimateGeneration++;setText("estimate-title","実行中の解析");setText("estimate-duration","進行状況に残り時間を表示");setText("estimate-detail","所要時間は最新の処理状況から更新されます。");syncButtons();$("notice").hidden=true;
      const job=await api("/api/jobs",jsonPost(config));
      clearTimeout(state.poll);state.generation++;state.jobId=job.id;
      state.startedAt=job.created_at?job.created_at*1000:Date.now();
      resetResults();switchTab("log");renderJob(job);refreshJobs();
      if(["queued","running"].includes(job.status)) pollJob(state.generation);
    } catch(e) {state.active=false;syncButtons();scheduleEstimate();notice(e.message,true);}
  }
  function renderJob(job) {
    state.jobId=job.id;state.active=["queued","running"].includes(job.status);
    state.jobStatus=job.status;const jobMemory=job.memory||job.result?.memory_policy;
    state.jobMemory=jobMemory&&typeof jobMemory==="object"?jobMemory:null;
    state.jobMemoryAt=state.jobMemory?.sampled_at||job.finished_at||null;state.jobMemoryConfig=job.config||job.result?.config||null;renderMemory();
    state.jobEstimate=job.estimate?{...job.estimate,receivedAt:Date.now()}:null;for(const item of state.historyEstimates)if(item.id===job.id)item.estimate=state.jobEstimate;renderProgress();
    if(job.created_at)state.startedAt=job.created_at*1000;
    setStatus(job.status);setText("log-stage",job.stage || statuses[job.status] || job.status);
    const log=$("job-log"),nearEnd=log.scrollHeight-log.clientHeight-log.scrollTop<60;
    setText("job-log",Array.isArray(job.log)?job.log.join("\n"):job.log || job.stage || "計算の開始を待っています。");
    if(nearEnd)log.scrollTop=log.scrollHeight;
    $("log-active").hidden=!state.active;
    const elapsed=job.result?.timings?.total_s || (job.finished_at&&job.created_at?job.finished_at-job.created_at:state.startedAt?(Date.now()-state.startedAt)/1000:null);
    setText("elapsed-time",num(elapsed)?fmt(elapsed,1)+" s":"");
    if(job.status==="completed"&&job.result)renderResult(job.result);
    if(job.status==="failed") {notice(job.error || "計算に失敗しました。ログを確認してください。",true);setText("results-subtitle","計算に失敗しました。条件とログを確認してください。");}
    if(job.status==="cancelled")setText("results-subtitle","計算は停止されました。条件を調整して再実行できます。");
    syncButtons();
  }
  async function pollJob(generation) {
    if(generation!==state.generation)return;
    try {
      const job=await api("/api/jobs/"+encodeURIComponent(state.jobId));
      if(generation!==state.generation)return;
      const wasActive=state.active;renderJob(job);
      if(state.active)state.poll=setTimeout(()=>pollJob(generation),1800);
      else {refreshJobs();scheduleEstimate();if(job.status==="completed"&&wasActive){switchTab("modes");notice("解析が完了しました。モード一覧と損失・participation を確認できます。");}}
    } catch(e) {
      if(generation!==state.generation)return;
      notice("進行状況を取得できませんでした。5 秒後に再接続します。 "+e.message,true);
      state.poll=setTimeout(()=>pollJob(generation),5000);
    }
  }
  async function openJob(id) {
    if(state.active && state.jobId!==id){notice("現在の計算が終わってから別の履歴を開いてください。");return;}
    if(state.loading)return;
    clearTimeout(state.poll);state.generation++;
    try {
      const job=await api("/api/jobs/"+encodeURIComponent(id));
      resetResults();restoreConfig(job.result?.config||job.config,job.result?.geometry);
      const config=job.result?.config||job.config;
      if(config?.geometry_id) {
        state.loading=true;startTransient("保存した形状の復元",30,180);syncButtons();
        try {const geo=await api("/api/geometries/"+encodeURIComponent(config.geometry_id)+"/inspect",jsonPost({geometry_mode:config.geometry_mode,solid_index:config.solid_index}));state.sourceGeometry=geo;applyGeometry(geo);restoreCapPorts(job.result?.geometry,geo);}
        catch(e){notice("結果は読み込めましたが、形状プレビューを復元できませんでした。 "+e.message,true);}
        finally{state.loading=false;endTransient();}
      }
      renderJob(job);switchTab(job.status==="completed"?"modes":"log");if(!state.active)scheduleEstimate();
      if(state.active)pollJob(state.generation);
    } catch(e){notice(e.message,true);syncButtons();}
  }
  function modeClassLabel(mode) {
    return ({cavity_like:"空洞主体",feed_dominated:"同軸主体",strongly_damped:"強い減衰"})[mode.mode_class]||"未分類";
  }
  function modeClassDetails(mode) {
    const fraction=num(mode.cavity_electric_energy_fraction)?"空洞内の電場エネルギー比: "+fmt(Number(mode.cavity_electric_energy_fraction)*100,2)+"%。 ":"";
    return fraction+(mode.classification_rule?"分類は診断指標です。 "+mode.classification_rule:"分類情報はありません。");
  }
  function modeOptionLabel(mode) {
    return "MODE "+String(mode.mode).padStart(2,"0")+" · "+modeClassLabel(mode)+" · "+fmt(mode.frequency_ghz,6)+" GHz";
  }
  function renderResult(result) {
    state.result=result;
    const modes=Array.isArray(result.modes)?result.modes:[];
    setText("results-subtitle",modes.length+" モード · "+(result.solver||"FEM")+" "+(result.solver_version||"")+" · "+(result.config?.loss_model==="tls_qres"?"Qi は指定した TLS 係数からの予測":"選択条件で計算"));
    $("download-results").hidden=false;$("download-results").href="/api/jobs/"+encodeURIComponent(state.jobId)+"/result.json";
    $("modes-body").replaceChildren(...modes.map((mode,index)=>{
      const tr=elem("tr",undefined,"mode-row");tr.tabIndex=0;tr.setAttribute("aria-label","モード "+mode.mode+" · "+modeClassLabel(mode)+" を選択");
      const cell=elem("td"),badge=elem("span",modeClassLabel(mode),"mode-kind "+(mode.mode_class||"unclassified"));badge.title=modeClassDetails(mode);badge.setAttribute("aria-label",modeClassLabel(mode)+"。"+modeClassDetails(mode));cell.append(elem("span",undefined,"mode-dot"),document.createTextNode(String(mode.mode).padStart(2,"0")),badge);tr.append(cell);
      tr.append(elem("td",Number(mode.frequency_ghz).toFixed(6),"number-main"),elem("td",qfmt(mode.qi)),elem("td",qfmt(mode.q_loaded)),elem("td",sci(mode.residual)));
      tr.addEventListener("click",()=>selectMode(index));tr.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();selectMode(index);}});
      return tr;
    }));
    const facts=[["四面体",fmt(result.mesh?.elements,0)],["自由度",fmt(result.mesh?.dofs,0)],["メッシュ",fmt(result.timings?.mesh_s,2)+" s"],["固有値計算",fmt(result.timings?.solve_s,2)+" s"],["合計",fmt(result.timings?.total_s,2)+" s"]];
    if(num(result.timings?.spectrum_s))facts.push(["S11",fmt(result.timings.spectrum_s,2)+" s"]);
    if(num(result.timings?.including_sweep_s))facts.push(["掃引を含む合計",fmt(result.timings.including_sweep_s,2)+" s"]);
    $("computation-facts").replaceChildren(...facts.map(([label,value])=>{const el=elem("span",label);el.append(elem("strong",value));return el;}));$("computation-facts").hidden=false;
    $("result-warnings").replaceChildren(...(result.warnings||[]).map(w=>elem("p",w)));$("result-warnings").hidden=!result.warnings?.length;
    renderModeSearch(result);
    selectMode(Math.min(state.selectedMode,Math.max(modes.length-1,0)));
    renderSpectrum(result.spectrum);renderSweep(result.antenna_sweep);
    $("field-mode").replaceChildren(...modes.map((m,i)=>new Option(modeOptionLabel(m),i)));
    $("field-mode").value=String(state.selectedMode);
    $("spectrum-mode").replaceChildren(...modes.map((m,i)=>new Option(modeOptionLabel(m),i)));
    $("spectrum-mode").value=String(state.selectedMode);
    syncButtons();
  }
  function renderModeSearch(result) {
    const search=result.mode_search,root=$("mode-search-summary");root.replaceChildren();root.hidden=!search;if(!search)return;
    const lowest=search.selection==="lowest",facts=elem("div",undefined,"search-facts");
    if(lowest&&Array.isArray(search.band_ghz))facts.append(elem("span","探索帯域 "+fmt(search.band_ghz[0],6)+"–"+fmt(search.band_ghz[1],6)+" GHz"));
    if(Array.isArray(search.shifts_ghz))facts.append(elem("span","探索シフト "+search.shifts_ghz.length+" 点"));
    facts.append(elem("span","検出 "+fmt(search.distinct_modes_found,0)+" モード / 表示 "+fmt(search.returned_modes,0)+" モード"));
    const frequencies=(result.modes||[]).map(m=>m.frequency_ghz).filter(num);if(frequencies.length)facts.append(elem("strong",(lowest?"検出一覧の最低周波数 ":"表示モードの最低周波数 ")+Number(Math.min(...frequencies)).toFixed(9)+" GHz"));
    root.append(facts);
    root.append(elem("p","最低周波数は、同軸主体・強い減衰のモードも対象とする表示一覧全体に対する値です。分類と電場分布を合わせて確認してください。","help-note"));
    if(search.completeness_proven===false)root.append(elem("p",lowest?"指定範囲で見つかった最低モードです。全モードの完全性は未証明です。帯域・探索点・メッシュを変えて確認してください。":"目標周波数付近の探索結果です。最低モードの保証ではありません。", "help-note"));
  }
  function selectMode(index) {
    state.selectedMode=index;
    const mode=state.result?.modes?.[index];if(!mode)return;
    [...$("modes-body").children].forEach((r,i)=>{r.classList.toggle("selected",i===index);r.setAttribute("aria-selected",String(i===index));});
    setText("metric-frequency",num(mode.frequency_ghz)?Number(mode.frequency_ghz).toFixed(6):"—");
    setText("metric-mode","MODE "+String(mode.mode).padStart(2,"0")+" · "+modeClassLabel(mode));$("metric-mode").title=modeClassDetails(mode);
    setText("metric-qi",qfmt(mode.qi));$("metric-qi").classList.toggle("unset",!num(mode.qi));
    setText("metric-ql",qfmt(mode.q_loaded));$("metric-ql").classList.toggle("unset",!num(mode.q_loaded));
    setText("metric-g",fmt(mode.geometry_factor_ohm,2));
    $("field-mode").value=String(index);
    $("spectrum-mode").value=String(index);
    $("field-mode").title=modeClassDetails(mode);$("spectrum-mode").title=modeClassDetails(mode);
    if(state.result?.spectrum)renderSpectrum(state.result.spectrum);
    if(!$("panel-field").hidden)loadField();
    $("download-fields").hidden=false;$("download-fields").href="/api/jobs/"+encodeURIComponent(state.jobId)+"/mode/"+mode.mode+".vtu";
    const participation=mode.participation||{};
    $("participation-results").replaceChildren(...Object.entries(participation).map(([name,value])=>{
      const wrap=elem("div");const row=elem("div",undefined,"participation-row");row.append(elem("span",({bulk:"バルク",oxide:"表面酸化膜"})[name]||name),elem("strong",sci(value)));
      const track=elem("div",undefined,"participation-track"),bar=elem("i");bar.style.width=(num(value)?Math.max(0,Math.min(100,Number(value)*100)):0)+"%";track.append(bar);wrap.append(row,track);return wrap;
    }));
    if(!Object.keys(participation).length)$("participation-results").append(blank("Participation データがありません"));
    renderPortResults(mode);
    if(mode.surface_participation?.length) {
      const details=elem("details",undefined,"advanced");
      details.append(elem("summary","面ごとの酸化膜 participation"));
      const table=elem("table",undefined,"data-table");const body=elem("tbody");
      for(const face of mode.surface_participation) {const row=elem("tr");row.append(elem("td","面 "+face.face+(face.is_port?" · ポート（壁面集計から除外）":"")),elem("td",sci(face.p_oxide)));body.append(row);}
      table.append(body);details.append(table);$("participation-results").append(details);
    }
  }
  async function refreshJobs() {
    try {
      const data=await api("/api/jobs");const jobs=Array.isArray(data)?data:data.jobs||[];
      state.historyEstimates=[];
      $("job-history").replaceChildren(...jobs.slice(0,12).map(job=>{
        const btn=elem("button",undefined,"history-item");btn.type="button";
        const c=job.config||{};
        const label=job.filename || "計算 "+job.id.slice(0,8);
        btn.append(elem("span",undefined,"history-state "+job.status),elem("span",label+(c.maxh_mm?" · "+c.maxh_mm+" mm / "+c.order+" 次":"")+" · "+(statuses[job.status]||job.status),"history-title"));
        const date=job.created_at?new Date(typeof job.created_at==="number"?job.created_at*1000:job.created_at).toLocaleString("ja-JP",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):"";
        const meta=elem("span",date,"history-meta");if(["queued","running"].includes(job.status)){state.historyEstimates.push({id:job.id,element:meta,estimate:job.estimate?{...job.estimate,receivedAt:Date.now()}:null});meta.textContent=job.estimate?estimateShort(job.estimate):"所要時間を推定中";}btn.append(meta,elem("span","↗","history-arrow"));btn.addEventListener("click",()=>openJob(job.id));return btn;
      }));
      if(!jobs.length)$("job-history").append(blank("保存された計算はありません"));
    } catch(e) {$("job-history").replaceChildren(blank("計算履歴を取得できませんでした。更新で再試行できます。"));}
  }
  function refreshAntennaSelectors() {
    const driver=$("driven-port").value,sweep=$("sweep-antenna").value;
    const names=[...new Set(state.antennas.map(a=>a.name).filter(Boolean))];
    $("driven-port").replaceChildren(new Option("自動選択",""),...names.map(n=>new Option(n,n)));
    $("driven-port").value=names.includes(driver)?driver:"";
    $("sweep-antenna").replaceChildren(...state.antennas.map(a=>new Option(a.name,a.name)));
    if(!state.antennas.length)$("sweep-antenna").add(new Option("アンテナを追加してください",""));
    if(state.antennas.some(a=>a.name===sweep))$("sweep-antenna").value=sweep;
  }
  function restoreSourceGeometry() {
    if(!state.sourceGeometry)return;
    state.antennaPreview=false;applyGeometry(state.sourceGeometry);
    setText("viewer-subtitle","原形状の取り付け面を選択");syncButtons();
  }
  function antennaChanged() {
    if(state.antennaPreview)restoreSourceGeometry();
    renderPorts();syncButtons();viewer.draw();
  }
  function addSelectedAntenna(){
    if(state.selectedFace===null||state.antennaPreview)return;
    if(state.ports.some(port=>!port.internal_auto&&port.faces.includes(state.selectedFace))||state.antennas.some(ant=>ant.face===state.selectedFace)){notice("選択面は手動の外部境界またはアンテナに指定済みです。",true);return;}
    let index=state.antennas.length+1;while([...state.antennas,...state.ports].some(item=>item.name==="A"+index))index++;
    state.antennas.push({name:"A"+index,face:state.selectedFace,length_mm:5,pin_radius_mm:.5,coax_outer_radius_mm:1.67,dielectric_epsr:2.1,feed_length_mm:3,center_mm:null,inward_axis:null});
    antennaChanged();renderAntennas();
  }
  function renderAntennas() {
    const list=$("antenna-list");list.replaceChildren();
    if(!state.antennas.length)list.append(blank("CAD で取り付け面を選択してください。長さは選択面から空洞内部への突出量です。"));
    state.antennas.forEach((ant,index)=>{
      const card=elem("article",undefined,"antenna-card"),head=elem("div",undefined,"antenna-card-heading");
      head.append(elem("strong","ANTENNA "+String(index+1).padStart(2,"0")),elem("span","原形状の面 "+ant.face));
      const remove=elem("button","×","text-button");remove.type="button";remove.setAttribute("aria-label","アンテナ "+ant.name+" を削除");
      remove.addEventListener("click",()=>{state.antennas.splice(index,1);antennaChanged();renderAntennas();});head.append(remove);card.append(head);
      const inputField=(label,key,options={})=>{
        const wrap=elem("label",label,"field"),input=elem("input");
        input.type=options.text?"text":"number";input.value=ant[key]??"";input.dataset.antennaKey=key;
        input.setAttribute("aria-label",ant.name+" "+label);
        if(!options.text){input.min=String(options.min??0);input.step="any";if(options.max!==undefined)input.max=String(options.max);}
        input.required=true;
        input.addEventListener("input",()=>{ant[key]=options.text?input.value:Number(input.value);antennaChanged();});
        wrap.append(input);return wrap;
      };
      const primary=elem("div",undefined,"field-grid");
      primary.append(inputField("名前","name",{text:true}),inputField("突出長さ mm","length_mm",{min:0}));card.append(primary);
      const advanced=elem("details",undefined,"advanced");advanced.append(elem("summary","ピン・同軸の寸法"));
      const pair=elem("div",undefined,"field-grid");pair.append(inputField("ピン半径 mm","pin_radius_mm",{min:.001}),inputField("外導体の内半径 mm","coax_outer_radius_mm",{min:.001}));advanced.append(pair);
      const pair2=elem("div",undefined,"field-grid");pair2.append(inputField("同軸内の εr","dielectric_epsr",{min:1,max:100}),inputField("外側の給電長 mm","feed_length_mm",{min:.01,max:100}));advanced.append(pair2);
      if(ant.dielectric_tand===undefined)ant.dielectric_tand=0;
      advanced.append(inputField("同軸内の tan δ","dielectric_tand",{min:0,max:.1}));
      advanced.append(elem("p","初期寸法は例です。対象の同軸構造と基準面に合わせて変更してください。","help-note"));card.append(advanced);list.append(card);
    });
    refreshAntennaSelectors();syncButtons();
  }
  function readAntennas() {
    const names=new Set(),faces=new Set();
    return state.antennas.map(a=>{
      if(!a.name?.trim() || names.has(a.name))throw new Error("アンテナ名を入力し、名前の重複を解消してください。");
      names.add(a.name);
      if(!Number.isInteger(a.face)||a.face<1)throw new Error(a.name+": 原形状の取り付け面を指定してください。");
      if(faces.has(a.face))throw new Error("同じ面に複数のアンテナは配置できません。");faces.add(a.face);
      if(!num(a.length_mm)||a.length_mm<0)throw new Error(a.name+": 突出長さは 0 mm 以上で指定してください。");
      for(const key of ["pin_radius_mm","coax_outer_radius_mm","dielectric_epsr","feed_length_mm"])if(!num(a[key])||Number(a[key])<=0)throw new Error(a.name+": アンテナ寸法と誘電率に正の値を指定してください。");
      if(a.coax_outer_radius_mm<=a.pin_radius_mm)throw new Error(a.name+": 外導体の内半径はピン半径より大きくしてください。");
      if(a.dielectric_epsr<1)throw new Error(a.name+": 比誘電率は 1 以上にしてください。");
      return {...a,center_mm:a.center_mm??null,inward_axis:a.inward_axis??null};
    });
  }
  async function previewAntennas() {
    if(state.loading||state.active||!state.geometry)return;
    try {
      const config=collectConfig();if(!config.antennas.length)return;
      state.loading=true;startTransient("アンテナ挿入形状の確認",15,120);syncButtons();setStatus("loading");
      if(!state.sourceGeometry)state.sourceGeometry=state.geometry;
      const result=await api("/api/geometries/"+encodeURIComponent(state.geometry.id)+"/antennas",jsonPost(config));
      const geo=result.geometry||result;
      applyGeometry({...state.sourceGeometry,...geo,id:state.sourceGeometry.id,filename:state.sourceGeometry.filename});
      state.antennaPreview=true;setText("viewer-subtitle","アンテナ挿入後の解析領域");setStatus("ready");
      notice("挿入後の形状を表示しています。新しいアンテナの取り付け面は「原形状に戻る」で選択してください。");
    }catch(e){notice(e.message,true);setStatus("failed");}
    finally{state.loading=false;endTransient();syncButtons();scheduleEstimate();}
  }
  function plotWarnings(id,warnings) {
    const target=$(id);target.replaceChildren(...(warnings||[]).map(w=>elem("p",typeof w==="string"?w:JSON.stringify(w))));
    target.hidden=!warnings?.length;
  }
  function svgElement(name,attrs={},text) {
    const node=document.createElementNS("http://www.w3.org/2000/svg",name);
    for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
    if(text!==undefined)node.textContent=text;return node;
  }
  function lineChart(target,points,{xLabel,yLabel,logY=false,markers=[],xRange=null}={}) {
    const valid=points.filter(p=>num(p.x)&&num(p.y)&&(!logY||p.y>0));
    target.replaceChildren();
    if(!valid.length){target.append(blank("表示できる計算値がありません。"));return;}
    const width=720,height=300,left=65,right=22,top=25,bottom=48;
    let xmin=xRange?xRange[0]:Math.min(...valid.map(p=>Number(p.x))),xmax=xRange?xRange[1]:Math.max(...valid.map(p=>Number(p.x)));
    let ymin=Math.min(...valid.map(p=>logY?Math.log10(p.y):Number(p.y))),ymax=Math.max(...valid.map(p=>logY?Math.log10(p.y):Number(p.y)));
    if(xmin===xmax){xmin-=.5;xmax+=.5;}if(ymin===ymax){ymin-=.5;ymax+=.5;}else{const pad=(ymax-ymin)*.08;ymin-=pad;ymax+=pad;}
    const px=x=>left+(Number(x)-xmin)/(xmax-xmin)*(width-left-right);
    const py=y=>height-bottom-((logY?Math.log10(y):Number(y))-ymin)/(ymax-ymin)*(height-top-bottom);
    const svg=svgElement("svg",{viewBox:"0 0 "+width+" "+height,role:"img","aria-label":xLabel+" に対する "+yLabel});
    svg.append(svgElement("rect",{x:0,y:0,width,height,fill:"#fbfdfe",rx:7}));
    for(let i=0;i<=5;i++){
      const x=xmin+(xmax-xmin)*i/5,y=ymin+(ymax-ymin)*i/5,sx=px(x),sy=height-bottom-i/5*(height-top-bottom);
      svg.append(svgElement("line",{x1:sx,x2:sx,y1:top,y2:height-bottom,stroke:"#e7eef2","stroke-width":1}),svgElement("line",{x1:left,x2:width-right,y1:sy,y2:sy,stroke:"#e7eef2","stroke-width":1}));
      svg.append(svgElement("text",{x:sx,y:height-bottom+19,"text-anchor":"middle",fill:"#7c919f","font-size":10},fmt(x,3)));
      svg.append(svgElement("text",{x:left-10,y:sy+4,"text-anchor":"end",fill:"#7c919f","font-size":10},logY?Math.pow(10,y).toExponential(1):fmt(y,2)));
    }
    svg.append(svgElement("text",{x:(width+left-right)/2,y:height-8,"text-anchor":"middle",fill:"#607d8f","font-size":11},xLabel),svgElement("text",{x:13,y:16,fill:"#607d8f","font-size":10},yLabel));
    for(const mark of markers){if(!num(mark.x)||mark.x<xmin||mark.x>xmax)continue;svg.append(svgElement("line",{x1:px(mark.x),x2:px(mark.x),y1:top,y2:height-bottom,stroke:"#c0cdd5","stroke-dasharray":"3 4"}));}
    let segment=[];const finish=()=>{if(segment.length)svg.append(svgElement("polyline",{points:segment.join(" "),fill:"none",stroke:"#108f91","stroke-width":2.1,"stroke-linejoin":"round"}));segment=[];};
    for(const p of points){if(!num(p.x)||!num(p.y)||(logY&&p.y<=0)){finish();continue;}segment.push(px(p.x)+","+py(p.y));}
    finish();
    for(const p of valid){const dot=svgElement("circle",{cx:px(p.x),cy:py(p.y),r:valid.length>120?1.5:2.5,fill:"#108f91"});dot.append(svgElement("title",{},xLabel+": "+fmt(p.x,6)+" · "+yLabel+": "+qfmt(p.y)));svg.append(dot);}
    target.append(svg);
  }
  function renderSpectrum(spectrum) {
    if(!spectrum?.frequencies_ghz?.length){$("spectrum-chart").replaceChildren(blank(state.result?.config?.analysis_mode==="all_openings_qc"?"指定開口の Qc 解析では S11 を計算しません。「損失・Participation」で流出電力と空洞 Qc を確認してください。":spectrum?.reason||"S11 の計算結果はありません。アンテナと周波数スキャンを設定してください。"));setText("spectrum-description","実計算した周波数応答を表示します。");plotWarnings("spectrum-warnings",spectrum?.warnings);return;}
    const phase=$("spectrum-quantity").value==="phase",yLabel=phase?"S11 位相 / °":"S11 / dB";
    const points=spectrum.frequencies_ghz.map((x,i)=>{const real=spectrum.s11_real?.[i],imag=spectrum.s11_imag?.[i];return{x,y:phase?(num(real)&&num(imag)&&Math.hypot(real,imag)>0?Math.atan2(imag,real)*180/Math.PI:null):spectrum.s11_db?.[i]};});
    const breakPhase=series=>{if(!phase)return series;const split=[];let previous=null;for(const point of series){if(previous&&num(previous.y)&&num(point.y)&&Math.abs(point.y-previous.y)>180)split.push({x:point.x,y:null});split.push(point);previous=point;}return split;};
    const mode=state.result?.modes?.[state.selectedMode],zoom=$("spectrum-view").value==="mode";
    let detail="";
    if(zoom && mode && num(mode.q_loaded) && mode.q_loaded>0) {
      const linewidth=mode.frequency_ghz/mode.q_loaded,linewidthHz=linewidth*1e9,factor=linewidthHz>=1e6?1e6:linewidthHz>=1e3?1e3:1,unit=factor===1e6?"MHz":factor===1e3?"kHz":"Hz";
      const narrow=points.filter(p=>Math.abs(p.x-mode.frequency_ghz)<=20*linewidth).map(p=>({x:(p.x-mode.frequency_ghz)*1e9/factor,y:p.y}));
      if(narrow.length<2)$("spectrum-chart").replaceChildren(blank("選択モード付近の計算点が不足しています。この共振を含む周波数スキャンを再実行してください。"));
      else lineChart($("spectrum-chart"),breakPhase(narrow),{xLabel:"共振中心からの周波数差 / "+unit,yLabel,xRange:[-20*linewidthHz/factor,20*linewidthHz/factor],markers:[{x:0}]});
      detail=" · 中心 "+Number(mode.frequency_ghz).toFixed(12)+" GHz · 線幅 "+fmt(linewidthHz/factor,6)+" "+unit+" · 周辺 "+narrow.length+" 点";
    } else if(zoom) {$("spectrum-chart").replaceChildren(blank("線幅の拡大には、選択モードの有限な QL が必要です。"));}
    else lineChart($("spectrum-chart"),breakPhase(points),{xLabel:"周波数 / GHz",yLabel,markers:(state.result?.modes||[]).map(m=>({x:m.frequency_ghz}))});
    setText("spectrum-description","励振: "+(spectrum.driven_port||"未指定")+" · 全 "+points.length+" 点"+(spectrum.method?" · 周波数領域 FEM・同軸 TEM":"")+detail+(!zoom&&spectrum.adaptive_sampling?" · 狭い共振は「選択モード」で拡大":""));
    plotWarnings("spectrum-warnings",spectrum.warnings);
  }
  function renderSweep(sweep) {
    $("sweep-table").replaceChildren();
    if(!sweep?.rows?.length){$("sweep-chart").replaceChildren(blank("長さ掃引の結果はありません。"));setText("sweep-description","長さ掃引を有効にすると、各長さの共振周波数と結合を比較できます。");plotWarnings("sweep-warnings",sweep?.warnings);return;}
    const rows=sweep.rows;
    lineChart($("sweep-chart"),rows.map(row=>({x:row.length_mm,y:row.qc})),{xLabel:"アンテナ突出長さ / mm",yLabel:"Qc（対数）",logY:true});
    setText("sweep-description",(sweep.antenna||"アンテナ")+" · "+rows.length+" 条件 · "+(sweep.mode_tracking==="nearest_previous_frequency"?"前の共振周波数に最も近いモードを追跡":"各条件の共振モードを比較"));
    const table=elem("table",undefined,"data-table"),head=elem("thead"),hr=elem("tr"),body=elem("tbody");
    for(const label of ["長さ mm","MODE","周波数 GHz","Qi","Qc","状態"])hr.append(elem("th",label));head.append(hr);
    for(const row of rows){const tr=elem("tr");tr.append(elem("td",fmt(row.length_mm,3)),elem("td",row.mode??"—"),elem("td",fmt(row.frequency_ghz,6)),elem("td",qfmt(row.qi)),elem("td",qfmt(row.qc)),elem("td",row.status==="invalid_geometry"?"形状不適合":row.status==="completed"?"完了":row.status||"—"));if(row.error)tr.title=row.error;body.append(tr);}
    table.append(head,body);$("sweep-table").append(table);plotWarnings("sweep-warnings",[...(sweep.warnings||[]),...rows.filter(row=>row.error).map(row=>fmt(row.length_mm,3)+" mm: "+row.error)]);
  }
  async function loadField() {
    const mode=state.result?.modes?.[state.selectedMode];
    if(!mode||!state.jobId){state.fieldData=null;drawField();return;}
    const generation=++state.fieldGeneration,key=state.jobId+":"+mode.mode;
    const path="/api/jobs/"+encodeURIComponent(state.jobId)+"/mode/"+mode.mode+"/field.json";
    $("download-field-json").href=path;$("download-field-json").hidden=true;
    $("field-empty").hidden=false;setText("field-empty","電場断面を読み込み中です。");
    try{
      const data=state.fieldCache.get(key)||await api(path);
      if(generation!==state.fieldGeneration)return;
      state.fieldCache.set(key,data);state.fieldData=data;
      const planes=data.planes||[],current=$("field-plane").value;
      $("field-plane").replaceChildren(...planes.map(p=>new Option(p.plane.toUpperCase(),p.plane)));
      if(planes.some(p=>p.plane===current))$("field-plane").value=current;
      const normalization=typeof data.normalization==="string"?data.normalization:data.normalization?.kind==="maximum over all three sampled planes"?"3 断面の共通最大値で正規化（最大 1）":"固有モードの相対振幅";
      setText("field-normalization","MODE "+mode.mode+" · "+modeClassLabel(mode)+(num(mode.cavity_electric_energy_fraction)?" · 空洞内の電場エネルギー "+fmt(Number(mode.cavity_electric_energy_fraction)*100,2)+"%":"")+" · "+normalization);$("field-normalization").title=modeClassDetails(mode);
      $("download-field-json").hidden=false;drawField();
    }catch(e){if(generation!==state.fieldGeneration)return;state.fieldData=null;drawField();setText("field-empty","電場断面を取得できませんでした。新しい解析を実行してください。");}
  }
  function fieldColor(t) {
    const stops=[[14,29,47],[28,82,117],[28,150,157],[113,208,175],[245,225,116]],value=Math.max(0,Math.min(1,t))*(stops.length-1),i=Math.min(stops.length-2,Math.floor(value)),f=value-i;
    return "rgb("+stops[i].map((v,k)=>Math.round(v*(1-f)+stops[i+1][k]*f)).join(",")+")";
  }
  function drawField() {
    const canvas=$("field-canvas"),ctx=canvas.getContext("2d"),rect=$("field-plot-wrap").getBoundingClientRect(),w=Math.max(320,rect.width),h=340,dpr=Math.min(window.devicePixelRatio||1,2);
    canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
    const plane=state.fieldData?.planes?.find(p=>p.plane===$("field-plane").value);
    if(!plane){$("field-empty").hidden=false;$("field-legend").hidden=true;return;}
    const us=plane.u_mm||[],vs=plane.v_mm||[],values=(plane.magnitude||[]).flat(),valid=values.filter(num).map(Number);
    if(!us.length||!vs.length||!valid.length){$("field-empty").hidden=false;setText("field-empty","この断面は解析領域と交差していません。");$("field-legend").hidden=true;return;}
    $("field-empty").hidden=true;$("field-legend").hidden=false;
    const allValues=state.fieldData.planes.flatMap(p=>(p.magnitude||[]).flat()).filter(num).map(Number),max=Math.max(...allValues),positive=allValues.filter(v=>v>0),logMin=positive.length?Math.max(Math.min(...positive),max*1e-6):1e-12,log=$("field-scale").value==="log";
    const u0=Math.min(...us),u1=Math.max(...us),v0=Math.min(...vs),v1=Math.max(...vs),du=(u1-u0)/(Math.max(1,us.length-1)),dv=(v1-v0)/(Math.max(1,vs.length-1));
    const spanU=(u1-u0)||1,spanV=(v1-v0)||1,scale=Math.min((w-110)/spanU,(h-70)/spanV),plotW=spanU*scale,plotH=spanV*scale,ox=(w-plotW)/2,oy=(h-plotH)/2;
    ctx.fillStyle="#f0f5f7";ctx.fillRect(ox-du*scale/2,oy-dv*scale/2,plotW+du*scale,plotH+dv*scale);
    for(let j=0;j<vs.length;j++)for(let i=0;i<us.length;i++){
      const value=values[j*us.length+i];if(!num(value))continue;
      const t=log?(Math.log10(Math.max(logMin,Number(value)))-Math.log10(logMin))/(Math.log10(Math.max(max,logMin*1.001))-Math.log10(logMin)):max>0?Number(value)/max:0;
      ctx.fillStyle=fieldColor(t);ctx.fillRect(ox+(us[i]-u0)*scale-du*scale/2,oy+(v1-vs[j])*scale-dv*scale/2,Math.max(1,du*scale+.5),Math.max(1,dv*scale+.5));
    }
    ctx.strokeStyle="#ccdce3";ctx.lineWidth=1;ctx.strokeRect(ox-du*scale/2,oy-dv*scale/2,plotW+du*scale,plotH+dv*scale);
    ctx.font="10px -apple-system, sans-serif";ctx.fillStyle="#76909f";ctx.textAlign="center";
    ctx.fillText(fmt(u0,2),ox,h-15);ctx.fillText(fmt(u1,2),ox+plotW,h-15);ctx.fillText(plane.plane[0].toUpperCase()+" / mm",w/2,h-3);
    ctx.textAlign="right";ctx.fillText(fmt(v1,2),ox-10,oy+4);ctx.fillText(fmt(v0,2),ox-10,oy+plotH);ctx.fillText(plane.plane[1].toUpperCase()+" / mm",ox-10,oy-13);
    setText("field-min",log?sci(logMin):"0");setText("field-max",sci(max));setText("field-unit",plane.units||"相対振幅");
    const normal=({xy:"Z",xz:"Y",yz:"X"})[plane.plane]||"断面";setText("field-coordinate",normal+" = "+fmt(plane.coordinate_mm,3)+" mm");
  }

  const viewer = {
    canvas:$("cad-canvas"), ctx:$("cad-canvas").getContext("2d"), points:[], triangles:[], faceIds:[], projected:[], drawn:[], yaw:-0.65, pitch:0.45, zoom:1, wire:false, size:[1,1], center:[0,0,0], span:1, scheduled:false,
    setData(data) {
      this.points=data?.vertices||[];this.triangles=data?.triangles||[];this.faceIds=data?.face_ids||[];
      if(this.points.length) {
        const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
        for(const p of this.points)for(let i=0;i<3;i++){min[i]=Math.min(min[i],p[i]);max[i]=Math.max(max[i],p[i]);}
        this.center=min.map((v,i)=>(v+max[i])/2);this.span=Math.max(...min.map((v,i)=>max[i]-v))||1;
      }
      this.yaw=-0.65;this.pitch=0.45;this.zoom=1;$("viewer-empty").hidden=!!this.points.length;this.draw();
    },
    draw() {if(this.scheduled)return;this.scheduled=true;requestAnimationFrame(()=>{this.scheduled=false;this.render();});},
    render() {
      const [w,h]=this.size,ctx=this.ctx;ctx.clearRect(0,0,w,h);
      const spacing=32;ctx.strokeStyle="rgba(130,175,192,.038)";ctx.lineWidth=1;
      ctx.beginPath();for(let x=w/2%spacing;x<w;x+=spacing){ctx.moveTo(x,0);ctx.lineTo(x,h);}for(let y=h/2%spacing;y<h;y+=spacing){ctx.moveTo(0,y);ctx.lineTo(w,y);}ctx.stroke();
      if(!this.points.length)return;
      const cy=Math.cos(this.yaw),sy=Math.sin(this.yaw),cp=Math.cos(this.pitch),sp=Math.sin(this.pitch);
      const scale=Math.min(w,h)*.71*this.zoom/this.span;
      this.projected=this.points.map(p=>{
        const x=p[0]-this.center[0],y=p[1]-this.center[1],z=p[2]-this.center[2];
        const rx=cy*x-sy*y,ry=sy*x+cy*y;
        return [w/2+rx*scale,h/2-(cp*z-sp*ry)*scale,sp*z+cp*ry];
      });
      this.drawn=[];
      for(let i=0;i<this.triangles.length;i++) {
        const tri=this.triangles[i],a=this.projected[tri[0]],b=this.projected[tri[1]],c=this.projected[tri[2]];
        if(!a||!b||!c)continue;
        const area=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
        const pa=this.points[tri[0]],pb=this.points[tri[1]],pc=this.points[tri[2]];
        const u=pb.map((v,j)=>v-pa[j]),v=pc.map((v,j)=>v-pa[j]);
        const n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
        const norm=Math.hypot(...n)||1;
        const light=.55+.3*Math.abs((n[0]*.3+n[1]*-.5+n[2]*.8)/norm);
        this.drawn.push({a,b,c,depth:(a[2]+b[2]+c[2])/3,id:this.faceIds[i]??null,light,area});
      }
      this.drawn.sort((a,b)=>a.depth-b.depth);
      const caps=new Set((state.geometry?.faces||[]).filter(f=>f.artificial_cap).map(f=>f.id));
      const automaticSource=new Set(state.ports.filter(port=>port.internal_auto).flatMap(port=>port.faces));
      const automaticFaces=new Set((state.geometry?.faces||[]).filter(face=>automaticSource.has(face.source_face??(!state.antennaPreview?face.id:null))).map(face=>face.id));
      for(const t of this.drawn) {
        const selected=state.selectedFace!==null && Number(t.id)===state.selectedFace;
        const cap=caps.has(t.id);
        ctx.beginPath();ctx.moveTo(t.a[0],t.a[1]);ctx.lineTo(t.b[0],t.b[1]);ctx.lineTo(t.c[0],t.c[1]);ctx.closePath();
        if(!this.wire) {
          const base=selected?[85,213,186]:automaticFaces.has(t.id)?[205,166,104]:cap?[128,150,173]:[114,168,182];
          ctx.fillStyle="rgb("+base.map(x=>Math.round(x*t.light)).join(",")+")";ctx.fill();
        }
        ctx.strokeStyle=selected?"rgba(157,255,220,.9)":this.wire?"rgba(126,190,201,.42)":"rgba(180,225,230,.14)";
        ctx.lineWidth=selected?.8:.45;ctx.stroke();
      }
    },
    resize() {const rect=this.canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);this.size=[rect.width,rect.height];this.canvas.width=Math.round(rect.width*dpr);this.canvas.height=Math.round(rect.height*dpr);this.ctx.setTransform(dpr,0,0,dpr,0,0);this.draw();},
    pick(x,y) {
      for(let i=this.drawn.length-1;i>=0;i--) {
        const t=this.drawn[i],p=[x,y],cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
        const d1=cross(t.a,t.b,p),d2=cross(t.b,t.c,p),d3=cross(t.c,t.a,p);
        if(!((d1<0||d2<0||d3<0)&&(d1>0||d2>0||d3>0)) && Math.abs(t.area)>0.01){selectFace(t.id);return;}
      }
      selectFace(null);
    }
  };
  let drag=null;
  viewer.canvas.addEventListener("pointerdown",e=>{drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY};viewer.canvas.setPointerCapture(e.pointerId);});
  viewer.canvas.addEventListener("pointermove",e=>{if(!drag)return;viewer.yaw+=(e.clientX-drag.x)*.008;viewer.pitch=Math.max(-1.5,Math.min(1.5,viewer.pitch+(e.clientY-drag.y)*.008));drag.x=e.clientX;drag.y=e.clientY;viewer.draw();});
  viewer.canvas.addEventListener("pointerup",e=>{if(drag&&Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)<5){const r=viewer.canvas.getBoundingClientRect();viewer.pick(e.clientX-r.left,e.clientY-r.top);}drag=null;});
  viewer.canvas.addEventListener("pointercancel",()=>{drag=null;});
  viewer.canvas.addEventListener("wheel",e=>{e.preventDefault();viewer.zoom=Math.max(.2,Math.min(6,viewer.zoom*Math.exp(-e.deltaY*.001)));viewer.draw();},{passive:false});
  new ResizeObserver(()=>viewer.resize()).observe($("viewer-wrap"));

  $("step-file").addEventListener("change",()=>{if($("step-file").files[0])loadGeometry($("step-file").files[0]);});
  $("drop-zone").addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();$("step-file").click();}});
  for(const event of ["dragover","dragenter"])$("drop-zone").addEventListener(event,e=>{e.preventDefault();if(!state.active&&!state.loading)$("drop-zone").classList.add("dragover");});
  for(const event of ["dragleave","drop"])$("drop-zone").addEventListener(event,e=>{e.preventDefault();$("drop-zone").classList.remove("dragover");});
  $("drop-zone").addEventListener("drop",e=>{if(e.dataTransfer.files[0])loadGeometry(e.dataTransfer.files[0]);});
  $("example-button").addEventListener("click",()=>loadGeometry());
  $("notice-close").addEventListener("click",()=>{$("notice").hidden=true;});
  $("geometry-mode").addEventListener("change",()=>reinspect(true));
  $("solid-index").addEventListener("change",()=>reinspect(false));
  $("loss-model").addEventListener("change",updateLossModel);
  $("run-button").addEventListener("click",startJob);
  $("cancel-button").addEventListener("click",async()=>{if(!state.active)return;$("cancel-button").disabled=true;try{renderJob(await api("/api/jobs/"+encodeURIComponent(state.jobId)+"/cancel",{method:"POST"}));clearTimeout(state.poll);state.generation++;refreshJobs();if(!state.active)scheduleEstimate();}catch(e){notice(e.message,true);}finally{$("cancel-button").disabled=false;}});
  $("ports-json").addEventListener("input",()=>{state.portsJsonDirty=true;setText("ports-json-state","未適用 · 計算には上のカードを使用");});
  $("apply-ports-json").addEventListener("click",()=>{try{state.ports=validatePorts(JSON.parse($("ports-json").value)).map(manualPort);renderPorts();notice("JSON の手動境界設定をカードに反映しました。自動ポートは別に生成されます。");}catch(e){notice("JSON を適用できません: "+e.message,true);}});
  $("auto-openings").addEventListener("change",()=>setAutomaticOpenings($("auto-openings").checked));
  $("review-caps-button").addEventListener("click",reviewCaps);
  $("cancel-caps-button").addEventListener("click",()=>{$("cap-review").hidden=true;});
  $("apply-caps-button").addEventListener("click",()=>{try{const selected=[...$("cap-review-list").querySelectorAll("input:checked")];for(const input of selected)addOpening(Number(input.value));renderPorts();$("cap-review").hidden=true;notice(selected.length+" 個の開口候補を追加しました。実際の開口かどうか、CAD と境界カードで確認してください。");}catch(e){notice(e.message,true);}});
  $("add-antenna-button").addEventListener("click",addSelectedAntenna);
  $("preview-antennas").addEventListener("click",previewAntennas);
  $("restore-source").addEventListener("click",restoreSourceGeometry);
  $("field-mode").addEventListener("change",()=>{if($("field-mode").value!=="")selectMode(Number($("field-mode").value));});
  $("spectrum-mode").addEventListener("change",()=>{if($("spectrum-mode").value!=="")selectMode(Number($("spectrum-mode").value));});
  $("spectrum-view").addEventListener("change",()=>renderSpectrum(state.result?.spectrum));
  $("spectrum-quantity").addEventListener("change",()=>renderSpectrum(state.result?.spectrum));
  $("field-plane").addEventListener("change",drawField);
  $("field-scale").addEventListener("change",drawField);
  new ResizeObserver(()=>{if(!$("panel-field").hidden)drawField();}).observe($("field-plot-wrap"));
  $("add-port-button").addEventListener("click",()=>{if(state.selectedFace===null||state.antennaPreview)return;try{addOpening(state.selectedFace);renderPorts();}catch(e){notice(e.message,true);}});
  $("face-select").addEventListener("change",()=>selectFace($("face-select").value));
  $("clear-face").addEventListener("click",()=>selectFace(null));
  $("reset-view").addEventListener("click",()=>{viewer.yaw=-.65;viewer.pitch=.45;viewer.zoom=1;viewer.draw();});
  $("wire-toggle").addEventListener("click",()=>{viewer.wire=!viewer.wire;$("wire-toggle").setAttribute("aria-pressed",String(viewer.wire));viewer.draw();});
  const tabs=[...document.querySelectorAll("[data-tab]")];
  tabs.forEach(tab=>{tab.addEventListener("click",()=>switchTab(tab.dataset.tab));tab.addEventListener("keydown",e=>{if(["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();const visible=tabs.filter(item=>!item.hidden),index=visible.indexOf(tab);const next=visible[(index+(e.key==="ArrowRight"?1:visible.length-1))%visible.length];switchTab(next.dataset.tab);next.focus();}});});
  $("refresh-jobs").addEventListener("click",refreshJobs);
  $("refresh-memory").addEventListener("click",refreshMemory);
  $("analysis-mode").addEventListener("change",updateAnalysisMode);
  document.querySelector(".controls").addEventListener("input",e=>{if(e.target.id!=="ports-json")scheduleEstimate();});
  document.querySelector(".controls").addEventListener("change",e=>{if(e.target.id!=="ports-json")scheduleEstimate();});
  setInterval(()=>{renderProgress();renderPreflightFinish();for(const item of state.historyEstimates)if(item.element.isConnected&&item.estimate)item.element.textContent=estimateShort(item.estimate);},1000);
  setInterval(()=>{if(state.historyEstimates.length)refreshJobs();},10000);
  updateGeometryHelp();updateLossModel();updateAnalysisMode();switchTab("modes");syncButtons();refreshJobs();
  loadServerInfo();
})();
