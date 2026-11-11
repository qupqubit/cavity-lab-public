/* Shipped domain-selection functions with synthetic CAD/API responses only. */
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const cpu={detected:true,available_cpus:10,recommended_threads:8,target_fraction:.8,max_threads:1024};
const geometry=(mode='metal_void',selected=2,caps=[2,9])=>({
  id:'synthetic-domain',filename:'synthetic.step',mode,selected,
  domain_detection:{requested_mode:'auto',selected_mode:mode,selected_index:selected,
    confidence:'high',reason:'合成形状の内側に囲まれた領域を選びました。',method:'geometric_enclosure'},
  solids:[0,1,2].map(index=>({index,volume_mm3:100,bbox_mm:[[0,0,0],[10,10,10]]})),
  faces:caps.map(id=>({id,artificial_cap:true,area_mm2:2})),preview:{vertices:[[0,0,0]]},
});

class Element{
  constructor(){this.value='';this.hidden=false;this.checked=false;this.textContent='';this.children=[];this.listeners={};this.classList={add(){},remove(){},toggle(){}};}
  replaceChildren(...children){this.children=children;}
  add(child){this.children.push(child);}
  addEventListener(event,fn){(this.listeners[event]??=[]).push(fn);}
  trigger(event){for(const fn of this.listeners[event]||[])fn({target:this});}
  querySelector(){return new Element();}
}
function app(){
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
  const requests=[];
  const context=vm.createContext({console,Map,Set,Number,Promise,Date,
    FormData:class{constructor(){this.fields={};}append(key,value){this.fields[key]=value;}},
    Option:function(text,value){return{text,value};},
    document:{getElementById(id){assert.ok(nodes.has(id),'Missing DOM element '+id);return nodes.get(id);},querySelectorAll:()=>[]},
    send:async(url,options)=>{requests.push({url,options});if(context.nextError)throw context.nextError;return context.nextGeometry;},
  });
  const declarations=source.split('\n').filter(line=>/^  (const \$ =|const state =|Object.assign\(state|const mappings =|Object.assign\(mappings|const num =|const fmt =|const setText =|const jsonPost =)/.test(line));
  const names=['updateGeometryHelp','resolvedGeometryMode','renderDomainDetection','verifyGeometryMode',
    'geometryWarnings','applyGeometry','loadGeometry','reinspect','selectFace','collectConfig',
    'updateAnalysisMode','openingCandidates','manualPort','syncAutomaticPorts','configureNewOpenings',
    'validatePorts','readPorts','readAntennas','restoreCapPorts','restoreConfig','restoreSourceGeometry',
    'protectThreadSelection','applyCpuPolicy','startJob'];
  const definitions=names.map(name=>{
    const begin=source.search(new RegExp('  (?:async )?function '+name+'\\('));
    assert.ok(begin>=0,'Function not found: '+name);
    const tail=source.slice(begin),next=tail.slice(1).search(/\n  (?:async )?function |\n  (?:const|class) |\n  \$\(/);
    assert.ok(next>=0,'Function boundary not found: '+name);return tail.slice(0,next+1);
  });
  vm.runInContext(declarations.join('\n')+'\n'+definitions.join('\n')+`
    const viewer={setData(){},draw(){}};
    function syncButtons(){} function scheduleEstimate(){} function refreshAntennaSelectors(){} function renderProgress(){}
    function updateLossModel(){} function setStatus(){} function startTransient(){} function endTransient(){}
    function renderAntennas(){} function updatePortCount(){} function renderPorts(){syncAutomaticPorts();}
    function notice(text){globalThis.lastNotice=text;}
    async function api(url,options){return send(url,options);}
    globalThis.domainTest={state,loadGeometry,reinspect,applyGeometry,collectConfig,restoreConfig,
      restoreCapPorts,restoreSourceGeometry,renderDomainDetection,applyCpuPolicy,startJob};
  `,context);
  // Wire the real edit listeners: a late CPU-policy response must respect both input/change.
  for(const event of ['input','change']){
    const line=source.split('\n').find(line=>line.includes('$("threads").addEventListener("'+event+'",'));
    assert.ok(line);vm.runInContext(line,context);
  }
  const defaults={'geometry-mode':'auto','solid-index':'0','analysis-mode':'resonance',
    'loss-model':'none','mode-search':'target','order':'2','n-modes':'3','target-ghz':'8',
    'threads':'1','maxh-mm':'2','spectrum-start-ghz':'.1','spectrum-stop-ghz':'18',
    'spectrum-points':'11','field-resolution':'11'};
  for(const [id,value]of Object.entries(defaults))nodes.get(id).value=value;
  nodes.get('spectrum-enabled').checked=true;
  return {...context.domainTest,context,nodes,requests,config:()=>clone(context.domainTest.collectConfig()),
    async load(geo=geometry(),file){context.nextGeometry=clone(geo);await context.domainTest.loadGeometry(file);},
  };
}

test('initial auto selection is visible but estimates send concrete vacuum domain before upload',()=>{
  assert.match(html,/<option value="auto" selected>/);
  const ui=app(),cfg=ui.config();
  assert.equal(cfg.geometry_mode,'vacuum_solid');assert.equal(cfg.geometry_id,null);assert.equal(cfg.solid_index,0);
});

test('auto upload keeps picker preference and sends the resolved mode/index with automatic ports',async()=>{
  const ui=app(),file={name:'synthetic.step'};await ui.load(geometry(),file);
  assert.equal(ui.requests[0].url,'/api/inspect');
  assert.equal(ui.requests[0].options.body.fields.geometry_mode,'auto');
  assert.equal(ui.requests[0].options.body.fields.file,file);
  assert.equal(ui.nodes.get('geometry-mode').value,'auto');
  const cfg=ui.config();assert.equal(cfg.geometry_mode,'metal_void');assert.equal(cfg.solid_index,2);
  assert.equal(cfg.all_artificial_caps,true);assert.equal(cfg.analysis_mode,'all_openings_qc');
  assert.equal(cfg.spectrum_enabled,false);assert.deepEqual(cfg.ports,[]);
  assert.deepEqual(clone(ui.state.ports.map(port=>port.faces[0])),[2,9]);
  assert.match(ui.nodes.get('domain-selected').textContent,/抽出した空隙.*領域 3/);
  assert.equal(ui.nodes.get('domain-reason').textContent,geometry().domain_detection.reason);
});

test('sample request honors auto and explicit modes and solid selection never enables automatic openings',async()=>{
  for(const requested of ['auto','vacuum_solid']){
    const ui=app();ui.nodes.get('geometry-mode').value=requested;
    await ui.load(geometry('vacuum_solid',0));
    assert.equal(ui.requests[0].url,'/api/example');
    assert.deepEqual(JSON.parse(ui.requests[0].options.body),{geometry_mode:requested});
    assert.equal(ui.nodes.get('geometry-mode').value,requested);
    assert.equal(ui.config().geometry_mode,'vacuum_solid');
    assert.equal(ui.config().all_artificial_caps,false);assert.equal(ui.config().analysis_mode,'resonance');
  }
});

test('new automatic closed region resets a previous openings analysis to resonance',async()=>{
  const ui=app();await ui.load();await ui.load(geometry('metal_void',0,[]));
  assert.equal(ui.nodes.get('geometry-mode').value,'auto');
  assert.equal(ui.config().analysis_mode,'resonance');assert.equal(ui.config().all_artificial_caps,true);
});

test('manual mode failure preserves previous domain, auto preference and port exclusions',async()=>{
  const ui=app();await ui.load();ui.state.excludedCapFaces=[9];const before=ui.config();
  ui.nodes.get('geometry-mode').value='vacuum_solid';ui.context.nextError=new Error('指定領域を生成できません。');
  await ui.reinspect(true);
  assert.deepEqual(JSON.parse(ui.requests.at(-1).options.body),{geometry_mode:'vacuum_solid',solid_index:0});
  assert.equal(ui.nodes.get('geometry-mode').value,'auto');
  assert.equal(ui.nodes.get('solid-index').value,'2');assert.deepEqual(ui.config(),before);
  assert.match(ui.context.lastNotice,/指定領域/);
});

test('explicit import refuses a silent server fallback and restores the last successful preference',async()=>{
  const ui=app();await ui.load();const original=ui.state.sourceGeometry;
  ui.nodes.get('geometry-mode').value='metal_void';await ui.load(geometry('vacuum_solid',0));
  assert.equal(ui.state.sourceGeometry,original);assert.equal(ui.nodes.get('geometry-mode').value,'auto');
  assert.match(ui.context.lastNotice,/指定した解析領域/);assert.equal(ui.config().geometry_mode,'metal_void');
});

test('manual region index under auto becomes explicit and preserves the selected index',async()=>{
  const ui=app();await ui.load();ui.nodes.get('solid-index').value='1';
  ui.context.nextGeometry=geometry('metal_void',1);await ui.reinspect(false);
  assert.deepEqual(JSON.parse(ui.requests.at(-1).options.body),{geometry_mode:'metal_void',solid_index:1});
  assert.equal(ui.nodes.get('geometry-mode').value,'metal_void');assert.equal(ui.config().solid_index,1);
  assert.match(ui.nodes.get('domain-reason').textContent,/手動/);
});

test('failed manual index restores auto preference and its previous selected index',async()=>{
  const ui=app();await ui.load();ui.nodes.get('solid-index').value='1';
  ui.context.nextError=new Error('合成エラー');await ui.reinspect(false);
  assert.equal(ui.nodes.get('geometry-mode').value,'auto');assert.equal(ui.nodes.get('solid-index').value,'2');
  assert.equal(ui.config().solid_index,2);
});

test('switching back to auto requests new detection and uses its chosen domain/index',async()=>{
  const ui=app();ui.nodes.get('geometry-mode').value='vacuum_solid';await ui.load(geometry('vacuum_solid',0));
  ui.nodes.get('geometry-mode').value='auto';ui.context.nextGeometry=geometry('metal_void',2);await ui.reinspect(true);
  assert.deepEqual(JSON.parse(ui.requests.at(-1).options.body),{geometry_mode:'auto',solid_index:0});
  assert.equal(ui.nodes.get('geometry-mode').value,'auto');assert.equal(ui.config().solid_index,2);
  assert.equal(ui.config().geometry_mode,'metal_void');
});

test('saved concrete domain/index and threads survive history geometry restoration and late CPU policy',async()=>{
  const ui=app();await ui.load();
  const saved={geometry_id:'saved-synthetic',geometry_mode:'vacuum_solid',solid_index:1,threads:3,
    analysis_mode:'resonance',ports:[],antennas:[],all_artificial_caps:false};
  ui.restoreConfig(saved);const restored=geometry('vacuum_solid',1);
  ui.state.sourceGeometry=restored;ui.applyGeometry(restored);ui.applyCpuPolicy(cpu);
  const cfg=ui.config();assert.equal(cfg.geometry_mode,'vacuum_solid');assert.equal(cfg.solid_index,1);
  assert.equal(cfg.threads,3);assert.equal(cfg.all_artificial_caps,false);
  assert.equal(ui.nodes.get('geometry-mode').value,'vacuum_solid');
  assert.match(ui.nodes.get('domain-reason').textContent,/保存済み.*自動判定はやり直しません/);
});

test('antenna preview and source restoration retain auto preference and concrete source index',async()=>{
  const ui=app();await ui.load();
  ui.state.antennaPreview=true;ui.applyGeometry({...geometry('metal_void',0),faces:[{id:77,source_face:2}]});
  assert.equal(ui.nodes.get('geometry-mode').value,'auto');assert.equal(ui.config().solid_index,2);
  ui.restoreSourceGeometry();assert.equal(ui.nodes.get('geometry-mode').value,'auto');
  assert.equal(ui.config().solid_index,2);assert.equal(ui.state.antennaPreview,false);
});

test('low-confidence reason is plain text and explicitly asks for domain review',async()=>{
  const ui=app(),geo=geometry();geo.domain_detection.confidence='low';geo.domain_detection.reason='<synthetic reason>';
  await ui.load(geo);assert.match(ui.nodes.get('domain-reason').textContent,/^<synthetic reason>.*確度は低め/);
});

test('actual thread edit listeners protect both input and change values from recommendation',()=>{
  for(const event of ['input','change']){
    const ui=app();ui.nodes.get('threads').value='4';ui.nodes.get('threads').trigger(event);ui.applyCpuPolicy(cpu);
    assert.equal(ui.nodes.get('threads').value,'4');assert.equal(ui.state.threadsProtected,true);
  }
});

test('Run protects selected threads before validation or an asynchronous submit',async()=>{
  const ui=app();await ui.load();ui.nodes.get('threads').value='5';
  // An invalid antenna makes collectConfig fail before any job submission.
  ui.state.antennas=[{name:''}];await ui.startJob();ui.applyCpuPolicy(cpu);
  assert.equal(ui.nodes.get('threads').value,'5');assert.equal(ui.state.threadsProtected,true);
  assert.equal(ui.requests.length,1);
});
