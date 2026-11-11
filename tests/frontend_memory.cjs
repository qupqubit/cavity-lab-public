/* Run with node --test tests/frontend_memory.cjs. No browser dependencies. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const GIB = 1073741824;
const policy = (extra = {}) => ({detected:true,status:'ok',total_bytes:16*GIB,
  available_bytes:10*GIB,budget_bytes:6*GIB,dof_limit:180000,sampled_at:1800000000,...extra});
const response = data => ({ok:true,status:200,json:async()=>data});

function app() {
  // Execute the shipped functions, without invoking CAD/canvas initialization.
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], {
    textContent:'',value:'',hidden:false,disabled:false,scrollHeight:0,scrollTop:0,clientHeight:0,
    classList:{toggle(){}},
  }]));
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({console,AbortController,Date,Promise,Map,Number,
    document:{getElementById(id){assert.ok(nodes.has(id),'Missing DOM element: '+id);return nodes.get(id);},
      documentElement:{dataset:{assetVersion:'0.1.4-test'}}},
    setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id;},
    clearTimeout(id){timers.delete(id);},
    fetch:async()=>{throw new Error('unexpected request');},
  });
  const declarations = source.split('\n').filter(line =>
    /^  (const \$ =|const state =|Object.assign\(state|const num =|const fmt =|const setText =)/.test(line));
  const names = ['api','updateAnalysisMode','duration','finishTime','estimateBasis','memoryGiB','memoryLow',
    'renderMemory','scheduleMemory','requestMemory','loadServerInfo','refreshMemory','applyCpuPolicy','protectThreadSelection',
    'scheduleEstimate','requestEstimate','renderPreflightFinish','renderJob'];
  const definitions = names.map(name => {
    const begin = source.search(new RegExp('  (?:async )?function '+name+'\\('));
    assert.ok(begin >= 0,'Function not found: '+name);
    const tail = source.slice(begin);
    const next = tail.slice(1).search(/\n  (?:async )?function |\n  (?:const|class) |\n  \$\(/);
    assert.ok(next >= 0,'Function boundary not found: '+name);
    return tail.slice(0,next+1);
  });
  vm.runInContext(declarations.join('\n')+'\n'+definitions.join('\n')+`
    const statuses={failed:'計算エラー',running:'計算中',completed:'計算完了'};
    const jsonPost=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    function syncButtons(){} function setStatus(){} function renderProgress(){}
    function updateGeometryHelp(){} function updateLossModel(){} function switchTab(){} function refreshJobs(){}
    function renderResult(){} function notice(message){globalThis.lastNotice=message;}
    function updateExecutionLocation(info){globalThis.connected=!!info;}
    function collectConfig(){throw new Error('unrelated incomplete geometry inputs');}
    globalThis.memoryTest={state,renderMemory,renderJob,requestMemory,loadServerInfo,refreshMemory,applyCpuPolicy,protectThreadSelection};
  `,context);
  nodes.get('order').value='2';nodes.get('n-modes').value='6';
  nodes.get('threads').value='1';
  nodes.get('analysis-mode').value='resonance';
  const startup=source.match(/^  updateGeometryHelp\(\);.*\n  loadServerInfo\(\);$/m);
  assert.ok(startup,'Startup sequence not found');
  return {...context.memoryTest,context,nodes,timers,
    start:()=>vm.runInContext(startup[0],context),
    text:id=>nodes.get(id).textContent,
    flush(ms){for(const [id,timer] of [...timers])if(timer.ms===ms){timers.delete(id);timer.fn();}},
  };
}

test('normal page initialization requests current memory without user input', async () => {
  const ui=app(),requests=[];
  ui.context.fetch=async url=>{
    requests.push(url);
    return response(url==='/api/info'?{app_version:'0.1.4',local_only:true}:
      {memory:policy({dof_limit:150000})});
  };
  const boot=ui.start();ui.flush(300);await boot;await new Promise(setImmediate);
  assert.deepEqual(requests,['/api/info','/api/memory?order=2&n_modes=6']);
  assert.equal(ui.state.memoryLimitState,'ready');
  assert.equal(ui.text('memory-dof'),'150,000');
});

test('stopped-job guard values survive current-PC refresh and remain distinct', () => {
  const ui=app();
  ui.state.memoryPlan=policy({budget_bytes:8*GIB});ui.state.memoryLimitState='ready';
  ui.state.memoryConfig={order:2,n_modes:6};
  ui.renderJob({id:'failed-job',status:'failed',config:{order:3,n_modes:6},finished_at:1800000010,
    error:'メモリ上限を超えました。',memory:policy({status:'budget_exceeded',budget_bytes:3*GIB,
      dof_limit:100000,actual_dofs:190000,estimated_peak_bytes:5.5*GIB,reason:'停止時の診断'})});
  assert.equal(ui.state.active,false);
  assert.equal(ui.text('memory-job-title'),'この計算の停止時');
  assert.equal(ui.text('memory-budget'),'8 GiB');
  assert.equal(ui.text('memory-job-budget'),'3 GiB');
  assert.equal(ui.text('memory-job-dofs'),'190,000');
  assert.equal(ui.text('memory-job-limit'),'100,000');
  assert.equal(ui.text('memory-job-peak'),'5.5 GiB');
  assert.match(ui.text('memory-job-context'),/現在値とは異なります/);
  ui.state.memoryPlan=policy({budget_bytes:5*GIB,dof_limit:210000});ui.renderMemory();
  assert.equal(ui.text('memory-budget'),'5 GiB');
  assert.equal(ui.text('memory-job-budget'),'3 GiB');
  assert.equal(ui.text('memory-job-note'),'停止時の診断');
});

test('unknown and zero values are distinct; plan peak is not shown as actual mesh peak', () => {
  const ui=app();
  ui.state.memoryPlan=policy({total_bytes:null,available_bytes:null,budget_bytes:0,dof_limit:0});
  ui.state.memoryLimitState='ready';ui.renderMemory();
  assert.equal(ui.text('memory-total'),'未取得');
  assert.equal(ui.text('memory-budget'),'0 GiB');
  assert.equal(ui.text('memory-dof'),'0');
  assert.equal(ui.nodes.get('memory-job').hidden,true);
  ui.renderJob({id:'old-job',status:'completed',memory:policy({estimated_peak_bytes:6*GIB})});
  assert.equal(ui.text('memory-job-title'),'この計算の記録');
  assert.equal(ui.text('memory-job-dofs'),'未計測');
  assert.equal(ui.text('memory-job-peak'),'未計測');
});

test('a late old memory response cannot replace a newer order/mode estimate', async () => {
  const ui=app(),pending=[];
  ui.context.fetch=(url)=>new Promise(resolve=>pending.push({url,resolve}));
  const old=ui.requestMemory();
  ui.nodes.get('order').value='3';ui.nodes.get('n-modes').value='12';
  const current=ui.requestMemory();
  assert.match(pending[0].url,/order=2&n_modes=6/);
  assert.match(pending[1].url,/order=3&n_modes=12/);
  pending[1].resolve(response({memory:policy({dof_limit:123456})}));await current;
  pending[0].resolve(response({memory:policy({dof_limit:999999})}));await old;
  assert.equal(ui.text('memory-dof'),'123,456');
  assert.match(ui.text('memory-context'),/3 次・12 モード/);
});

test('timed-out initial requests become retryable and explicit refresh recovers independently', async () => {
  const ui=app(),requests=[];
  ui.context.fetch=(url,options)=>new Promise((resolve,reject)=>{
    requests.push(url);options.signal.addEventListener('abort',()=>reject(new Error('aborted')));
  });
  const info=ui.loadServerInfo(),memory=ui.requestMemory();
  ui.flush(12000);await Promise.all([info,memory]);
  assert.equal(ui.state.memoryLimitState,'unavailable');
  assert.doesNotMatch(ui.text('memory-status'),/取得中|更新中/);
  assert.match(ui.text('memory-status'),/再取得/);
  ui.context.fetch=async url=>{
    requests.push(url);
    return response(url==='/api/info'?{app_version:'0.1.4',solver:'NGSolve',version:'6.2.2506',local_only:true}:
      {memory:policy({dof_limit:150000})});
  };
  await ui.refreshMemory();
  assert.equal(ui.state.memoryLimitState,'ready');
  assert.equal(ui.text('memory-dof'),'150,000');
  assert.equal(ui.text('app-version'),'v0.1.4');
  assert.equal(ui.nodes.get('refresh-memory').disabled,false);
  assert.equal(ui.context.connected,true);
  // Full-config validation may fail afterwards, but memory remains available.
  ui.flush(650);await Promise.resolve();
  assert.equal(ui.state.memoryLimitState,'ready');
  assert.equal(ui.text('estimate-duration'),'条件入力後に表示');
  assert.ok(requests.filter(url=>url==='/api/info').length===2);
  assert.ok(requests.filter(url=>url.startsWith('/api/memory')).length===2);
});

test('invalid mode count settles without fetching and does not erase job diagnostics', async () => {
  const ui=app();
  ui.state.jobMemory=policy({actual_dofs:190000});ui.state.jobStatus='failed';
  ui.nodes.get('n-modes').value='';
  assert.equal(await ui.requestMemory(),false);
  assert.equal(ui.state.memoryLimitState,'invalid');
  assert.equal(ui.text('memory-dof'),'条件入力後に表示');
  assert.equal(ui.text('memory-job-dofs'),'190,000');
});

const cpu = extra => ({detected:true,available_cpus:10,recommended_threads:8,
  target_fraction:.8,max_threads:1024,...extra});

test('first detected CPU policy applies recommended threads once and explains variable CPU usage', async () => {
  const ui=app();ui.context.fetch=async()=>response({cpu:cpu()});
  assert.equal(ui.nodes.get('threads').value,'1');await ui.loadServerInfo();
  assert.equal(ui.nodes.get('threads').value,'8');assert.equal(ui.nodes.get('threads').max,'1024');
  assert.match(ui.text('threads-help'),/10 スレッド.*8.*80%.*CPU 使用率.*変わります/);
  ui.context.fetch=async()=>response({cpu:cpu({available_cpus:20,recommended_threads:16})});
  await ui.loadServerInfo();assert.equal(ui.nodes.get('threads').value,'8');
  assert.match(ui.text('threads-help'),/20 スレッド.*16/);
});

test('CPU detection can recover after failure without treating unavailable values as measured zero', async () => {
  const ui=app();ui.context.fetch=async()=>response({cpu:cpu({detected:false,available_cpus:null,recommended_threads:1})});
  await ui.loadServerInfo();assert.equal(ui.nodes.get('threads').value,'1');
  assert.equal(ui.state.threadsDefaultApplied,false);assert.match(ui.text('threads-help'),/取得できません/);
  ui.context.fetch=async()=>response({cpu:cpu()});await ui.loadServerInfo();
  assert.equal(ui.nodes.get('threads').value,'8');
});

test('late info response does not overwrite threads edited while the request was pending', async () => {
  const ui=app();let resolve;
  ui.context.fetch=()=>new Promise(done=>{resolve=done;});
  const pending=ui.loadServerInfo();
  ui.nodes.get('threads').value='3';ui.protectThreadSelection();
  resolve(response({cpu:cpu()}));await pending;
  assert.equal(ui.nodes.get('threads').value,'3');
  assert.match(ui.text('threads-help'),/推奨 8/);
});

test('out-of-order server info cannot overwrite the latest CPU policy or its applied default', async () => {
  const ui=app(),pending=[];ui.context.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const old=ui.loadServerInfo(),current=ui.loadServerInfo();
  pending[1](response({cpu:cpu({available_cpus:5,recommended_threads:4})}));await current;
  pending[0](response({cpu:cpu()}));await old;
  assert.equal(ui.nodes.get('threads').value,'4');assert.match(ui.text('threads-help'),/5 スレッド.*推奨 4/);
});

test('running or previously submitted jobs prevent an initial late default from replacing their threads',()=>{
  for(const status of ['queued','running','completed']){
    const ui=app();ui.nodes.get('threads').value='2';ui.renderJob({id:'synthetic-job',status});ui.applyCpuPolicy(cpu());
    assert.equal(ui.nodes.get('threads').value,'2');assert.equal(ui.state.threadsProtected,true);
  }
});
