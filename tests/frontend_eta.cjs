/* Run shipped ETA/job lifecycle functions with a controlled clock and synthetic responses. */
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const initial=Date.parse('2026-11-11T00:00:00Z');
const response=data=>({ok:true,status:200,json:async()=>data});
const settle=()=>new Promise(setImmediate);

function app(){
  let now=initial,id=0;
  const timers=new Map(),intervals=new Map();
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],{
    textContent:'',value:'',hidden:false,disabled:false,scrollHeight:0,scrollTop:0,clientHeight:0,
    classList:{toggle(){},add(){},remove(){}},
  }]));
  class Clock extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
  const context=vm.createContext({console,Map,Number,Promise,Date:Clock,AbortController,
    document:{getElementById(name){assert.ok(nodes.has(name),'Missing DOM '+name);return nodes.get(name);}},
    setTimeout(fn,ms){timers.set(++id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
    setInterval(fn,ms){intervals.set(++id,{fn,ms});return id;},
    fetch:async()=>{throw new Error('unexpected request');},
  });
  const declarations=source.split('\n').filter(line=>/^  (const \$ =|const state =|Object.assign\(state|const statuses =|const num =|const fmt =|const setText =|const jsonPost =)/.test(line));
  const names=['api','syncButtons','duration','finishTime','estimateElapsed','remainingEstimate',
    'estimateShort','liveEstimateDisplay','estimateBasis','scheduleEstimate','requestEstimate',
    'renderPreflightFinish','renderProgress','startJob','renderJob','pollJob','openJob','protectThreadSelection'];
  const definitions=names.map(name=>{
    const begin=source.search(new RegExp('  (?:async )?function '+name+'\\('));assert.ok(begin>=0,name);
    const tail=source.slice(begin),next=tail.slice(1).search(/\n  (?:async )?function |\n  (?:const|class) |\n  \$\(/);
    assert.ok(next>=0,name);return tail.slice(0,next+1);
  });
  vm.runInContext(declarations.join('\n')+'\n'+definitions.join('\n')+`
    function renderAutoOpenings(){} function renderMemory(){} function scheduleMemory(){}
    function setStatus(status){globalThis.lastStatus=status;}
    function notice(message){globalThis.lastNotice=message;}
    function resetResults(){state.result=null;}
    function renderResult(result){state.result=result;}
    function refreshJobs(){} function switchTab(tab){globalThis.lastTab=tab;}
    function restoreConfig(config){state.geometry={id:config?.geometry_id||'synthetic'};scheduleEstimate();}
    function restoreCapPorts(){} function startTransient(){} function endTransient(){}
    function applyGeometry(geometry){state.geometry=geometry;}
    function collectConfig(){return {geometry_id:'synthetic',threads:3,spectrum_enabled:false,antennas:[]};}
    globalThis.etaTest={state,startJob,renderJob,pollJob,openJob,scheduleEstimate,requestEstimate,renderPreflightFinish,renderProgress,estimateShort};
  `,context);
  const ticker=source.split('\n').find(line=>line.startsWith('  setInterval(()=>{renderProgress();'));
  assert.ok(ticker,'Real one-second renderer was not found');vm.runInContext(ticker,context);
  context.etaTest.state.geometry={id:'synthetic'};
  return {...context.etaTest,context,nodes,timers,
    text:name=>nodes.get(name).textContent,
    now:()=>now,
    tick(seconds){now+=seconds*1000;for(const timer of intervals.values())if(timer.ms===1000)timer.fn();},
    async timer(ms){const found=[...timers].find(([,timer])=>timer.ms===ms);assert.ok(found,'No '+ms+' ms timer');timers.delete(found[0]);await found[1].fn();},
  };
}
function estimate(ui,extra={}){return {remaining_s:45,total_s:100,lower_s:20,upper_s:60,
  estimated_finish_at:ui.now()/1000+45,stage:'電場を出力中',completed_units:2,total_units:6,
  confidence:'medium',basis:'合成モデルの記録',queued_wait_s:0,...extra};}
function job(ui,status='running',extra={}){return {id:'synthetic-job',status,stage:'固有モードを計算中',created_at:initial/1000-20,
  config:{geometry_id:'synthetic',threads:3},estimate:estimate(ui),...extra};}

test('prominent card and small progress share live remaining time, finish time, range and stage',()=>{
  const ui=app();ui.renderJob(job(ui));
  assert.equal(ui.text('estimate-duration'),ui.text('work-progress-time'));
  assert.match(ui.text('estimate-duration'),/残り約 45 秒.*完了目安.*09:00:45 JST/);
  assert.match(ui.text('estimate-detail'),/電場を出力中.*20 秒〜1 分.*2 \/ 6.*合成モデル/);
  assert.equal(ui.text('estimate-detail'),ui.text('work-progress-detail'));
  assert.equal(ui.text('estimate-title'),'実行中の解析');
});

test('actual one-second renderer counts down both cards and keeps the same finish instant',()=>{
  const ui=app();ui.renderJob(job(ui));ui.tick(5);
  assert.match(ui.text('estimate-duration'),/残り約 40 秒.*09:00:45 JST/);
  assert.match(ui.text('estimate-detail'),/15 秒〜55 秒/);
  assert.equal(ui.text('estimate-duration'),ui.text('work-progress-time'));
});

test('queued job includes waiting time and a subsequent poll updates stage, range and finish',async()=>{
  const ui=app();ui.renderJob(job(ui,'queued',{estimate:estimate(ui,{queued_wait_s:20,stage:'先行する計算を待機中'})}));
  assert.equal(ui.text('estimate-title'),'順番待ちの解析');assert.match(ui.text('estimate-detail'),/順番待ち約 20 秒/);
  ui.tick(5);assert.match(ui.text('estimate-detail'),/順番待ち約 15 秒/);
  ui.context.fetch=async()=>response(job(ui,'running',{estimate:estimate(ui,{remaining_s:30,lower_s:10,upper_s:50,estimated_finish_at:ui.now()/1000+30,stage:'メッシュを生成中'})}));
  await ui.pollJob(ui.state.generation);
  assert.equal(ui.text('estimate-title'),'実行中の解析');assert.match(ui.text('estimate-duration'),/残り約 30 秒.*09:00:35 JST/);
  assert.match(ui.text('estimate-detail'),/メッシュを生成中.*10 秒〜50 秒/);
});

test('submitting a new run clears prior job/preflight ETA before the POST returns',async()=>{
  const ui=app();ui.state.jobId='previous';ui.state.jobEstimate=estimate(ui,{remaining_s:9999});
  ui.state.estimate=estimate(ui,{remaining_s:8888});let submit;
  ui.context.fetch=async(url,options)=>{
    if(url==='/api/jobs'){assert.equal(JSON.parse(options.body).threads,3);return new Promise(resolve=>{submit=resolve;});}
    return response(job(ui,'queued'));
  };
  const pending=ui.startJob();
  assert.equal(ui.text('estimate-title'),'解析の開始を確認中');assert.equal(ui.text('estimate-duration'),'残り時間を推定中');
  assert.equal(ui.state.jobEstimate,null);assert.equal(ui.state.estimate,null);assert.equal(ui.state.jobId,null);
  assert.equal(ui.nodes.get('cancel-button').hidden,true);assert.equal(ui.state.threadsProtected,true);
  ui.tick(2);assert.equal(ui.text('estimate-duration'),'残り時間を推定中');
  submit(response(job(ui,'queued')));await pending;await settle();
  assert.equal(ui.text('estimate-title'),'順番待ちの解析');assert.match(ui.text('estimate-duration'),/残り約 45 秒/);
});

test('failed submission restores preflight mode and clears the running progress panel',async()=>{
  const ui=app();ui.context.fetch=async()=>{throw new Error('synthetic submit failure');};await ui.startJob();
  assert.equal(ui.state.active,false);assert.equal(ui.nodes.get('work-progress').hidden,true);
  assert.equal(ui.text('estimate-title'),'全解析の所要時間（概算）');assert.equal(ui.text('estimate-duration'),'条件を確認中…');
  assert.match(ui.context.lastNotice,/synthetic submit failure/);
  await ui.timer(650);assert.equal(ui.text('estimate-duration'),'見積りを取得できません');
  assert.doesNotMatch(ui.text('estimate-title'),/実行中/);
});

test('running history shows live ETA immediately, including while its CAD preview is restoring',async()=>{
  const ui=app();let restore;
  ui.context.fetch=async url=>url.includes('/geometries/')?new Promise(resolve=>{restore=resolve;}):response(job(ui));
  const opening=ui.openJob('synthetic-job');await settle();
  assert.ok(restore);assert.equal(ui.state.loading,true);assert.equal(ui.state.active,true);
  assert.match(ui.text('estimate-duration'),/残り約 45 秒/);ui.tick(3);
  assert.match(ui.text('estimate-duration'),/残り約 42 秒/);
  restore(response({id:'synthetic',mode:'vacuum_solid'}));await opening;await settle();
  assert.equal(ui.state.loading,false);assert.equal(ui.state.threadsProtected,true);
  assert.equal(ui.text('estimate-duration'),ui.text('work-progress-time'));
});

test('completed, failed and cancelled jobs clear live ETA and return to current-config preflight',async()=>{
  for(const status of ['completed','failed','cancelled']){
    const ui=app();ui.renderJob(job(ui));ui.tick(2);
    ui.context.fetch=async()=>response({estimate:estimate(ui,{total_s:30,remaining_s:30})});
    ui.renderJob(job(ui,status,{finished_at:ui.now()/1000}));
    assert.equal(ui.state.jobEstimate,null);assert.equal(ui.state.active,false);
    assert.equal(ui.nodes.get('work-progress').hidden,true);assert.equal(ui.text('estimate-title'),'全解析の所要時間（概算）');
    assert.equal(ui.text('estimate-duration'),'条件を確認中…');await ui.timer(650);
    assert.match(ui.text('estimate-detail'),/今開始した場合の完了目安/);
    assert.doesNotMatch(ui.text('estimate-duration'),/残り/);
  }
});

test('poll failure marks last-known estimate, expires honestly and recovers on a fresh response',async()=>{
  const ui=app();ui.renderJob(job(ui));ui.context.fetch=async()=>{throw new Error('synthetic disconnect');};
  await ui.pollJob(ui.state.generation);
  assert.match(ui.text('estimate-detail'),/進捗を再取得中.*前回取得した見積り/);
  ui.tick(70);assert.equal(ui.text('estimate-duration'),'見積り更新待ち');
  assert.doesNotMatch(ui.text('estimate-duration'),/完了目安|残り約 0/);
  assert.doesNotMatch(ui.text('estimate-detail'),/0 秒〜0 秒/);
  ui.context.fetch=async()=>response(job(ui));await ui.timer(5000);
  assert.equal(ui.state.jobPollError,false);assert.match(ui.text('estimate-duration'),/残り約 45 秒/);
  assert.doesNotMatch(ui.text('estimate-detail'),/再取得中/);
});

test('a timed-out poll becomes retryable instead of leaving an indefinite fresh-looking estimate',async()=>{
  const ui=app();ui.renderJob(job(ui));
  ui.context.fetch=(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  const pending=ui.pollJob(ui.state.generation);await ui.timer(12000);await pending;
  assert.equal(ui.state.jobPollError,true);assert.match(ui.context.lastNotice,/待ち時間を超えました/);
  assert.ok([...ui.timers.values()].some(timer=>timer.ms===5000));
});

test('missing or malformed live ETA never becomes zero, NaN, an epoch finish, or a prior job ETA',()=>{
  for(const value of [null,{}, {remaining_s:null,total_s:null},{remaining_s:'bad',total_s:'bad'}]){
    const ui=app();ui.renderJob(job(ui));ui.renderJob(job(ui,'running',{estimate:value,stage:'進捗確認中'}));
    assert.equal(ui.text('estimate-duration'),'残り時間を推定中');
    assert.match(ui.text('estimate-detail'),/進捗確認中/);assert.doesNotMatch(ui.text('estimate-duration'),/NaN|1970|45 秒|0 秒/);
  }
});

test('late preflight replies cannot overwrite a running history or new job estimate',async()=>{
  const ui=app();let resolve;ui.context.fetch=()=>new Promise(done=>{resolve=done;});
  const pending=ui.requestEstimate(ui.state.estimateGeneration);ui.renderJob(job(ui));
  resolve(response({estimate:estimate(ui,{remaining_s:9999})}));await pending;
  assert.equal(ui.text('estimate-title'),'実行中の解析');assert.match(ui.text('estimate-duration'),/残り約 45 秒/);
  assert.equal(ui.state.estimate,null);
});

test('a response from a superseded poll cannot restore its old estimate or status',async()=>{
  const ui=app();ui.renderJob(job(ui));let resolve;ui.context.fetch=()=>new Promise(done=>{resolve=done;});
  const pending=ui.pollJob(ui.state.generation);ui.state.generation++;
  ui.renderJob(job(ui,'running',{id:'new-job',estimate:estimate(ui,{remaining_s:12})}));
  resolve(response(job(ui,'completed')));await pending;
  assert.equal(ui.state.jobId,'new-job');assert.equal(ui.state.active,true);assert.match(ui.text('estimate-duration'),/残り約 12 秒/);
});

test('idle preflight duration stays fixed while its hypothetical finish time advances',async()=>{
  const ui=app();ui.context.fetch=async()=>response({estimate:estimate(ui,{remaining_s:30,lower_s:20,upper_s:40})});
  await ui.requestEstimate(ui.state.estimateGeneration);const duration=ui.text('estimate-duration');
  assert.match(ui.text('estimate-detail'),/今開始した場合.*09:00:30 JST/);
  ui.tick(10);assert.equal(ui.text('estimate-duration'),duration);
  assert.match(ui.text('estimate-detail'),/今開始した場合.*09:00:40 JST/);
});
