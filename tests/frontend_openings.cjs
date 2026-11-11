/* Run with node --test tests/frontend_openings.cjs. Synthetic geometry only. */
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const geometry=(mode='metal_void',caps=[2,9])=>({id:'synthetic-geometry',mode,selected:0,
  faces:[...caps.map(id=>({id,artificial_cap:true,area_mm2:2})),{id:4,artificial_cap:false}],preview:{}});
const manual=(face=4,name='Pmanual')=>({name,faces:[face],kind:'radiation',radiation_epsr:1,impedance_ohm:376.730313668});

class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.attrs={};this.dataset={};this.value='';this.textContent='';this.hidden=false;this.checked=false;this.disabled=false;this.classList={toggle(){},add(){},remove(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  add(child){this.append(child);}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  trigger(name){for(const fn of this.listeners[name]||[])fn({target:this});}
  all(){return [this,...this.children.flatMap(child=>child instanceof Element?child.all():[])];}
}

function app(){
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
  const context=vm.createContext({console,Map,Set,Number,Promise,FormData:class{append(){}},
    Option:function(text,value){const option=new Element('option');option.textContent=text;option.value=value;return option;},
    document:{getElementById(id){assert.ok(nodes.has(id),'Missing DOM element '+id);return nodes.get(id);},
      createElement:tag=>new Element(tag),createTextNode:text=>text,querySelectorAll:()=>[]},
  });
  const declarations=source.split('\n').filter(line=>/^  (const \$ =|const state =|Object.assign\(state|const mappings =|Object.assign\(mappings|const num =|const fmt =|const qfmt =|const elem =|const blank =|const setText =)/.test(line));
  const names=['updateAnalysisMode','updateGeometryHelp','renderDomainDetection','resolvedGeometryMode',
    'verifyGeometryMode','protectThreadSelection','loadGeometry','selectFace','openingCandidates','manualPort',
    'syncAutomaticPorts','configureNewOpenings','setAutomaticOpenings','excludeOpening',
    'editOpening','removeOpening','showOpeningFace','renderAutoOpenings','validatePorts',
    'readPorts','syncPortsJson','updatePortCount','portsChanged','renderPorts','addOpening',
    'collectConfig','restoreCapPorts','restoreConfig','restoreSourceGeometry','antennaChanged',
    'addSelectedAntenna','renderAntennas','readAntennas','portQLabel'];
  const definitions=names.map(name=>{
    const begin=source.search(new RegExp('  (?:async )?function '+name+'\\('));
    assert.ok(begin>=0,'Function not found: '+name);
    const tail=source.slice(begin),next=tail.slice(1).search(/\n  (?:async )?function |\n  (?:const|class) |\n  \$\(/);
    assert.ok(next>=0,'Function boundary not found: '+name);return tail.slice(0,next+1);
  });
  vm.runInContext(declarations.join('\n')+'\n'+definitions.join('\n')+`
    const viewer={draw(){}};
    function syncButtons(){} function scheduleEstimate(){} function refreshAntennaSelectors(){}
    function updateLossModel(){} function setStatus(){} function startTransient(){} function endTransient(){}
    function notice(text){globalThis.lastNotice=text;}
    const jsonPost=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    function applyGeometry(geo){state.geometry=geo;$('geometry-mode').value=state.geometryRequestedMode;}
    async function api(){return globalThis.nextGeometry;}
    globalThis.openingsTest={state,loadGeometry,configureNewOpenings,setAutomaticOpenings,
      openingCandidates,renderPorts,collectConfig,restoreConfig,restoreCapPorts,
      excludeOpening,editOpening,removeOpening,addOpening,addSelectedAntenna,antennaChanged,
      syncAutomaticPorts,showOpeningFace,portQLabel};
  `,context);
  const defaults={'geometry-mode':'metal_void','solid-index':'0','analysis-mode':'resonance',
    'loss-model':'none','mode-search':'target','order':'2','n-modes':'3','target-ghz':'8',
    'threads':'2','maxh-mm':'2','spectrum-start-ghz':'.1','spectrum-stop-ghz':'18',
    'spectrum-points':'11','field-resolution':'11'};
  for(const [id,value]of Object.entries(defaults))nodes.get(id).value=value;
  nodes.get('spectrum-enabled').checked=true;
  const ui={...context.openingsTest,context,nodes,
    config:()=>clone(context.openingsTest.collectConfig()),
    async load(geo=geometry()){context.nextGeometry=clone(geo);await context.openingsTest.loadGeometry();},
    autoFaces:()=>clone(context.openingsTest.state.ports.filter(port=>port.internal_auto).flatMap(port=>port.faces)),
  };
  return ui;
}

test('new extracted metal openings default to auto Qc and send only manual ports',async()=>{
  const ui=app();await ui.load();
  assert.deepEqual(ui.autoFaces(),[2,9]);
  const cfg=ui.config();
  assert.equal(cfg.all_artificial_caps,true);assert.equal(cfg.analysis_mode,'all_openings_qc');
  assert.equal(cfg.spectrum_enabled,false);assert.deepEqual(cfg.ports,[]);
  assert.deepEqual(cfg.excluded_artificial_cap_faces,[]);
  assert.match(ui.nodes.get('auto-openings-summary').textContent,/2 面.*2 個/);
  assert.equal(ui.nodes.get('ports-json').value,'[]');
});

test('new region resets exclusions and zero candidates return to resonance',async()=>{
  const ui=app();await ui.load();ui.excludeOpening(2);ui.renderPorts();
  await ui.load(geometry('metal_void',[]));
  assert.equal(ui.config().all_artificial_caps,true);
  assert.equal(ui.config().analysis_mode,'resonance');
  assert.deepEqual(ui.config().excluded_artificial_cap_faces,[]);
  assert.match(ui.nodes.get('auto-openings-summary').textContent,/0 面/);
  await ui.load(geometry('metal_void',[6]));assert.deepEqual(ui.autoFaces(),[6]);
});

test('automatic selection of vacuum solid does not generate openings even with cap-like metadata',async()=>{
  const ui=app();await ui.load();ui.nodes.get('geometry-mode').value='auto';await ui.load(geometry('vacuum_solid'));
  assert.equal(ui.config().all_artificial_caps,false);
  assert.equal(ui.config().analysis_mode,'resonance');assert.deepEqual(ui.autoFaces(),[]);
  assert.equal(ui.nodes.get('automatic-openings').hidden,true);
});

test('automatic toggle retains manual overrides and exclusions',async()=>{
  const ui=app();await ui.load();ui.state.ports.push(manual());ui.excludeOpening(9);ui.renderPorts();
  ui.setAutomaticOpenings(false);
  assert.deepEqual(ui.autoFaces(),[]);assert.deepEqual(ui.config().ports,[manual()]);
  ui.setAutomaticOpenings(true);
  assert.deepEqual(ui.autoFaces(),[2]);assert.deepEqual(ui.config().excluded_artificial_cap_faces,[9]);
});

test('removing and restoring an automatic card persists a source-face exclusion',async()=>{
  const ui=app();await ui.load();
  const card=ui.nodes.get('port-list').children[0];
  card.all().find(node=>node.attrs['aria-label']?.includes('自動設定から除外')).trigger('click');
  assert.deepEqual(ui.autoFaces(),[9]);assert.deepEqual(ui.config().excluded_artificial_cap_faces,[2]);
  const row=ui.nodes.get('auto-openings-list').children[0];
  row.all().find(node=>node.tag==='button'&&node.textContent==='戻す').trigger('click');
  assert.deepEqual(ui.autoFaces(),[2,9]);assert.deepEqual(ui.config().excluded_artificial_cap_faces,[]);
});

test('editing an automatic card makes a manual port and does not recreate its original face',async()=>{
  const ui=app();await ui.load();
  const automatic=ui.state.ports[0];ui.editOpening(automatic,{name:'custom',faces:[4],radiation_epsr:2});
  const cfg=ui.config();
  assert.deepEqual(ui.autoFaces(),[9]);assert.deepEqual(cfg.excluded_artificial_cap_faces,[2]);
  assert.equal(cfg.ports[0].name,'custom');assert.deepEqual(cfg.ports[0].faces,[4]);
  assert.equal(cfg.ports[0].radiation_epsr,2);assert.ok(!('internal_auto' in cfg.ports[0]));
  ui.removeOpening(automatic);assert.deepEqual(ui.autoFaces(),[9]);
});

test('editing automatic-card permittivity immediately updates the existing remove button label',async()=>{
  const ui=app();await ui.load();
  const card=ui.nodes.get('port-list').children[0];
  const remove=card.all().find(node=>node.attrs['aria-label']?.includes('自動設定から除外'));
  const eps=card.all().find(node=>node.tag==='input'&&node.type==='number'&&node.min==='1');
  eps.value='2.5';eps.trigger('input');
  assert.equal(remove.attrs['aria-label'],'opening_face_2 を削除');
  assert.equal(ui.nodes.get('port-list').children[0],card); // Preserve the input and focus.
  assert.equal(ui.config().ports[0].radiation_epsr,2.5);
});

test('antenna replaces an auto candidate and removal restores it without an exclusion',async()=>{
  const ui=app();await ui.load();ui.state.selectedFace=2;ui.addSelectedAntenna();
  assert.equal(ui.state.antennas.length,1);assert.deepEqual(ui.autoFaces(),[9]);
  assert.deepEqual(ui.config().excluded_artificial_cap_faces,[]);
  ui.nodes.get('antenna-list').all().find(node=>node.attrs['aria-label']==='アンテナ A1 を削除').trigger('click');
  assert.equal(ui.state.antennas.length,0);assert.deepEqual(ui.autoFaces(),[2,9]);
});

test('antenna cannot replace an explicit manual boundary on the same face',async()=>{
  const ui=app();await ui.load();ui.editOpening(ui.state.ports[0],{name:'explicit'});
  ui.state.selectedFace=2;ui.addSelectedAntenna();
  assert.equal(ui.state.antennas.length,0);assert.match(ui.context.lastNotice,/手動の外部境界/);
});

test('old history with missing or false flag stays off after source geometry restoration',async()=>{
  for(const flag of [undefined,false]){
    const ui=app();await ui.load();
    const cfg={geometry_id:'saved',geometry_mode:'metal_void',analysis_mode:'resonance',ports:[manual(2)],antennas:[],spectrum_enabled:true};
    if(flag!==undefined)cfg.all_artificial_caps=flag;
    ui.restoreConfig(cfg);ui.restoreCapPorts(null,geometry());
    assert.equal(ui.config().all_artificial_caps,false);assert.equal(ui.config().analysis_mode,'resonance');
    assert.equal(ui.config().spectrum_enabled,true);assert.deepEqual(ui.config().ports,[manual(2)]);
    assert.deepEqual(ui.autoFaces(),[]);
  }
});

test('history preserves auto flag and same-face manual override despite exclusion',async()=>{
  const ui=app();await ui.load();
  ui.restoreConfig({geometry_id:'saved',geometry_mode:'metal_void',all_artificial_caps:true,
    excluded_artificial_cap_faces:[2],analysis_mode:'resonance',ports:[manual(2,'override')],antennas:[]},
    {external_boundary_selection:{artificial_cap_candidates:[2,9]}});
  assert.deepEqual(ui.autoFaces(),[9]);
  ui.restoreCapPorts(null,geometry());const cfg=ui.config();
  assert.equal(cfg.all_artificial_caps,true);assert.equal(cfg.analysis_mode,'resonance');
  assert.deepEqual(cfg.excluded_artificial_cap_faces,[2]);assert.deepEqual(cfg.ports,[manual(2,'override')]);
  assert.deepEqual(ui.autoFaces(),[9]);
});

test('antenna preview keeps original source IDs and face highlight returns to source CAD',async()=>{
  const ui=app();await ui.load();
  ui.state.geometry={id:'synthetic-geometry',mode:'metal_void',faces:[{id:77,source_face:2,artificial_cap:true}]};
  ui.state.antennaPreview=true;ui.syncAutomaticPorts();assert.deepEqual(ui.autoFaces(),[2,9]);
  ui.showOpeningFace(9);assert.equal(ui.state.antennaPreview,false);assert.equal(ui.state.selectedFace,9);
  assert.deepEqual(ui.autoFaces(),[2,9]);
});

test('automatic names avoid manual-name collision using backend underscore convention',async()=>{
  const ui=app();await ui.load();ui.state.ports.push(manual(4,'opening_face_2'));ui.renderPorts();
  assert.equal(ui.state.ports.find(port=>port.internal_auto&&port.faces[0]===2).name,'opening_face_2_');
});

test('tiny outgoing power is labelled unresolved Qc when backend provides threshold status',()=>{
  const ui=app();assert.equal(ui.portQLabel(null,1e-40,'below_power_threshold'),'流出微小・Qc未確定');
  assert.equal(ui.portQLabel(null,0,'below_power_threshold'),'流出微小・Qc未確定');
  assert.equal(ui.portQLabel(null,null,'unavailable'),'未設定');
  assert.equal(ui.portQLabel(1000,1e-5,'finite'),'1,000');
});
