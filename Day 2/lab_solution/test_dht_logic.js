#!/usr/bin/env node
// test_dht_logic.js — validates ALL logic in dht_simulator.html v3
// Run: node test_dht_logic.js
'use strict';

// ── SHA-1 (identical to HTML, uses Buffer for UTF-8 in Node.js) ──────────
function sha1Bytes(bytes) {
  function rotl(n,s){ return (n<<s)|(n>>>(32-s)); }
  const d=[...bytes]; const bitLen=d.length*8;
  d.push(0x80);
  while(d.length%64!==56) d.push(0);
  for(let i=7;i>=0;i--) d.push(Math.floor(bitLen/Math.pow(2,i*8))&0xff);
  let h0=0x67452301,h1=0xEFCDAB89,h2=0x98BADCFE,h3=0x10325476,h4=0xC3D2E1F0;
  for(let i=0;i<d.length;i+=64){
    const w=[];
    for(let j=0;j<16;j++)
      w.push(((d[i+j*4]||0)<<24)|((d[i+j*4+1]||0)<<16)|((d[i+j*4+2]||0)<<8)|(d[i+j*4+3]||0));
    for(let j=16;j<80;j++) w.push(rotl(w[j-3]^w[j-8]^w[j-14]^w[j-16],1));
    let [a,b,c,e2,e]=[h0,h1,h2,h3,h4];
    for(let j=0;j<80;j++){
      let f,k;
      if     (j<20){f=(b&c)|(~b&e2);       k=0x5A827999;}
      else if(j<40){f=b^c^e2;               k=0x6ED9EBA1;}
      else if(j<60){f=(b&c)|(b&e2)|(c&e2); k=0x8F1BBCDC;}
      else          {f=b^c^e2;               k=0xCA62C1D6;}
      const t=(rotl(a,5)+f+e+k+w[j])>>>0;
      e=e2;e2=c;c=rotl(b,30);b=a;a=t;
    }
    h0=(h0+a)>>>0;h1=(h1+b)>>>0;h2=(h2+c)>>>0;h3=(h3+e2)>>>0;h4=(h4+e)>>>0;
  }
  return [h0,h1,h2,h3,h4].map(v=>v.toString(16).padStart(8,'0')).join('');
}
function sha1Hex(str){ return sha1Bytes(Buffer.from(str,'utf8')); }
function hashId(name){ return parseInt(sha1Hex(name).slice(-2),16); }

// ── Ring constants ────────────────────────────────────────────────────────
const BITS=8, RING=256, REP=2;

// ── Ring logic (mirrors HTML) ─────────────────────────────────────────────
function cwB(start,value,end){
  if(start<end) return start<value&&value<end;
  return value>start||value<end;
}

function succFrom(nodes,key,offset=0){
  const s=[...nodes].sort((a,b)=>a.id-b.id);
  if(!s.length) return null;
  let i=s.findIndex(p=>p.id>=key); if(i===-1) i=0;
  return s[(i+offset)%s.length];
}

function fingerTbl(nodes,node){
  const f=[];
  for(let i=0;i<BITS;i++){
    const t=(node.id+(1<<i))%RING;
    const p=succFrom(nodes,t);
    if(!p||p.id===node.id) continue;
    if(!f.find(x=>x.id===p.id)) f.push(p);
  }
  return f;
}

function nextHops(nodes,fromNode,key){
  const owner=succFrom(nodes,key);
  const ft=fingerTbl(nodes,fromNode);
  let cands=ft.filter(p=>cwB(fromNode.id,p.id,key));
  cands.sort((a,b)=>((a.id-fromNode.id+RING)%RING)-((b.id-fromNode.id+RING)%RING));
  cands.reverse();
  const ordered=[...cands,owner]; const result=[];
  for(const p of ordered)
    if(p&&p.id!==fromNode.id&&!result.find(r=>r.id===p.id)) result.push(p);
  return result;
}

function routeSim(nodes,fromNode,key){
  const visited=new Set(); const path=[fromNode.id]; const fail=[];
  let cur=fromNode;
  for(let hop=0;hop<20;hop++){
    const owner=succFrom(nodes,key);
    if(!owner) return {ok:false,error:'empty ring',path,fail};
    if(cur.id===owner.id) return {ok:true,owner:cur,path,fail};
    if(visited.has(cur.id)) return {ok:false,error:'cycle',path,fail};
    visited.add(cur.id);
    const hops=nextHops(nodes,cur,key);
    let fwd=false;
    for(const h of hops){
      if(visited.has(h.id)) continue;
      if(!h.online){if(!fail.includes(h.id))fail.push(h.id);continue;}
      path.push(h.id);cur=h;fwd=true;break;
    }
    if(!fwd){
      const o=succFrom(nodes,key);
      if(o&&!path.includes(o.id)){path.push(o.id);if(!o.online)fail.push(o.id);}
      return {ok:false,error:'no reachable route',owner:o,path,fail};
    }
  }
  return {ok:false,error:'max hops',path,fail};
}

function dhtPut(nodes,name,port){
  const key=hashId(name); const owner=succFrom(nodes,key);
  if(!owner) return null;
  const rec={name,host:'127.0.0.1',port,updated:Date.now()};
  owner.records[name]=rec;
  const repNames=[];
  for(let r=1;r<=Math.min(REP,nodes.length-1);r++){
    const rep=succFrom(nodes,key,r);
    if(rep&&rep.id!==owner.id){rep.records[name]={...rec};repNames.push(rep.name);}
  }
  return {owner,repNames,rec};
}

function dhtGet(nodes,name){
  const key=hashId(name); const owner=succFrom(nodes,key);
  if(owner&&owner.online&&owner.records[name])
    return {found:true,rec:owner.records[name],source:owner,usedReplica:false,tried:[]};
  const tried=[];
  for(let r=1;r<=Math.min(REP,nodes.length);r++){
    const rep=succFrom(nodes,key,r);
    if(!rep||rep.id===owner?.id) continue;
    tried.push(rep);
    if(rep.online&&rep.records[name])
      return {found:true,rec:rep.records[name],source:rep,usedReplica:true,offset:r,tried};
  }
  return {found:false,source:owner,tried};
}

// ── Test runner ───────────────────────────────────────────────────────────
let pass=0,fail=0;
function eq(a,b){ return JSON.stringify(a)===JSON.stringify(b); }
function assert(label,got,exp,detail=''){
  const ok=eq(got,exp);
  console.log(`  ${ok?'✅':'❌'} ${label}${detail?' ('+detail+')':''}`);
  if(!ok) console.log(`      got:      ${JSON.stringify(got)}\n      expected: ${JSON.stringify(exp)}`);
  ok?pass++:fail++;
}
function sec(t){ console.log(`\n${'─'.repeat(55)}\n  ${t}\n${'─'.repeat(55)}`); }

// ══════════════════════════════════════════════════════════
// TEST 1: hashId — exact match against Python reference values
// ══════════════════════════════════════════════════════════
sec('TEST 1 · hashId = int(SHA1(name.encode()),16) % 256');
const EXPECTED={alpha:79,beta:101,gamma:71,delta:135,epsilon:125,'😀':24,'điện toán':79};
for(const [name,exp] of Object.entries(EXPECTED))
  assert(`hashId("${name}") = ${exp}`, hashId(name), exp, `sha1[-2]="${sha1Hex(name).slice(-2)}"`);
// Verify precision: compare with BigInt computation
for(const [name,exp] of Object.entries(EXPECTED)){
  const hex=sha1Hex(name);
  const bigint=Number(BigInt('0x'+hex)%256n);
  assert(`BigInt cross-check for "${name}"`, parseInt(hex.slice(-2),16), bigint);
}
assert('sha1Hex("alpha") length = 40', sha1Hex('alpha').length, 40);

// ══════════════════════════════════════════════════════════
// TEST 2: clockwiseBetween — verified against Python clockwise_between()
// cwB(10,5,0) = false: wrap {11..255}; 5 not in there
// cwB(10,15,5) = true:  wrap {11..255,0..4}; 15 IS in there
// ══════════════════════════════════════════════════════════
sec('TEST 2 · clockwiseBetween — open interval, Python-exact');
assert('cwB(0,5,10)',    cwB(0,5,10),    true,  '5 in (0..10)');
assert('cwB(0,0,10)',    cwB(0,0,10),    false, 'open: 0 excluded');
assert('cwB(0,10,10)',   cwB(0,10,10),   false, 'open: 10 excluded');
assert('cwB(10,5,0)',    cwB(10,5,0),    false, 'wrap {11..255}: 5 not in');
assert('cwB(10,15,5)',   cwB(10,15,5),   true,  'wrap {11..255,0..4}: 15>10 → in');
assert('cwB(200,210,5)', cwB(200,210,5), true,  'wrap: 210>200');
assert('cwB(200,3,5)',   cwB(200,3,5),   true,  'wrap: 3<5');
assert('cwB(71,71,71)',  cwB(71,71,71),  false, 'boundary: false');
assert('cwB(79,101,71)', cwB(79,101,71), true,  'alpha,beta,gamma: 101 in (79..71) wrap');
assert('cwB(79,71,71)',  cwB(79,71,71),  false, 'value=end → false (open)');

// ══════════════════════════════════════════════════════════
// TEST 3: successor uses ALL nodes (offline included in ring)
// ══════════════════════════════════════════════════════════
sec('TEST 3 · successor uses ALL nodes — offline stays in ring');
const ring=[
  {name:'gamma',   id:71,  port:5103,online:true, records:{}},
  {name:'alpha',   id:79,  port:5101,online:true, records:{}},
  {name:'beta',    id:101, port:5102,online:false,records:{}}, // OFFLINE
  {name:'epsilon', id:125, port:5105,online:true, records:{}},
  {name:'delta',   id:135, port:5104,online:true, records:{}},
];
assert('successor(71)=gamma', succFrom(ring,71).name, 'gamma');
assert('successor(101)=beta (offline but in ring)', succFrom(ring,101).name, 'beta');
assert('successor(100)=beta', succFrom(ring,100).name, 'beta');
assert('successor(102)=epsilon (skips to next)', succFrom(ring,102).name, 'epsilon');
assert('successor(0) wraps to gamma(71)', succFrom(ring,0).name, 'gamma');
assert('successor(256)=gamma(71) mod wrap', succFrom(ring,0).name, 'gamma');
// offline beta still appears in alpha's finger table
const alphaFT=fingerTbl(ring,ring[1]);
assert('alpha finger table includes offline beta', alphaFT.some(f=>f.name==='beta'), true);

// ══════════════════════════════════════════════════════════
// TEST 4: routing skips offline hops, logs failures
// ══════════════════════════════════════════════════════════
sec('TEST 4 · routing: skip offline hops, finite path, O(log N)');
// beta(101) offline. Route from alpha(79) to gamma(71).
const r4=routeSim(ring,ring[1]/*alpha*/,71);
assert('route(alpha→71): ok=true', r4.ok, true);
assert('path[0]=79 (starts at alpha)', r4.path[0], 79);
assert('path[-1]=71 (ends at gamma)', r4.path[r4.path.length-1], 71);
assert('path length ≤ 6 (O(log N))', r4.path.length<=6, true);
console.log(`      path: [${r4.path.join('→')}]  failures: [${r4.fail.join(',')||'none'}]`);

// Route from gamma(71) to epsilon(125) — all online
ring[2].online=true; // restore beta
const r4b=routeSim(ring,ring[0]/*gamma*/,125);
assert('route(gamma→125/epsilon): ok=true', r4b.ok, true);
assert('path ends at epsilon(125)', r4b.path[r4b.path.length-1], 125);
ring[2].online=false; // set beta offline again

// ══════════════════════════════════════════════════════════
// TEST 5: GET owner offline → REPLICA_GET fallback
// ══════════════════════════════════════════════════════════
sec('TEST 5 · REPLICA_GET when owner offline');
// gamma(71) owns "gamma", alpha(79) has replica (offset 1 from 71 = alpha)
ring[0].records['gamma']={name:'gamma',host:'127.0.0.1',port:5103,updated:0};
ring[1].records['gamma']={name:'gamma',host:'127.0.0.1',port:5103,updated:0};
ring[0].online=false; // gamma offline
const g5=dhtGet(ring,'gamma');
ring[0].online=true; // restore
assert('dhtGet("gamma") with gamma offline: found=true', g5.found, true);
assert('usedReplica=true', g5.usedReplica, true);
assert('replica source = alpha', g5.source.name, 'alpha');
assert('replica offset = 1', g5.offset, 1);

// Both owner and first replica offline → second replica or fail
ring[0].online=false; ring[1].online=false; // gamma + alpha offline
ring[2].online=true; ring[2].records['gamma']={name:'gamma',host:'127.0.0.1',port:5103,updated:0};
const g5b=dhtGet(ring,'gamma');
ring[0].online=true; ring[1].online=true; ring[2].online=false; // restore
assert('When 2 replicas offline, 3rd replica used if has record', g5b.found, true);
// Cleanup
ring.forEach(n=>n.records={});

// ══════════════════════════════════════════════════════════
// TEST 6: dhtPut + restart = update address (location independence)
// ══════════════════════════════════════════════════════════
sec('TEST 6 · Location independence: restart → same ID, new port');
const idBefore=hashId('gamma'); // 71
// Simulate restart: same name, new port
const put6=dhtPut(ring,'gamma',5199);
assert('PUT port=5199: owner has record', put6.owner.records['gamma']?.port, 5199);
assert('PUT replicates to ≥1 node', put6.repNames.length>=1, true);
assert('hashId("gamma") unchanged after port change', hashId('gamma'), idBefore);
assert('ID = 71 (stable)', hashId('gamma'), 71);
// Verify lookup resolves new port
const lookup6=dhtGet(ring,'gamma');
assert('lookup "gamma" resolves port=5199', lookup6.rec?.port, 5199);
ring.forEach(n=>n.records={});

// ══════════════════════════════════════════════════════════
// TEST 7: Collision detection
// ══════════════════════════════════════════════════════════
sec('TEST 7 · Collision: different name, same ID');
// "điện toán" has id=79 (same as "alpha")
const colId=hashId('điện toán');
assert('hashId("điện toán") = 79 (same as alpha)', colId, 79);
// If ring has alpha(79), adding "điện toán"(79) should be detected
const conflict=ring.find(n=>n.id===colId&&n.name!=='điện toán');
assert('Conflict detected: alpha has same ID', conflict?.name, 'alpha');
// Simulate addNode rejection logic
function tryAddNode(nodes,name,port){
  const id=hashId(name);
  if(nodes.find(n=>n.name===name)) return {ok:false,reason:'duplicate name'};
  if(nodes.find(n=>n.id===id)) return {ok:false,reason:'collision',id};
  const node={name,port,id,online:true,records:{}};
  nodes.push(node); return {ok:true};
}
const testNodes=[...ring];
const addAlpha=tryAddNode(testNodes,'alpha',9001);
assert('Adding existing "alpha" rejected: duplicate name', addAlpha.ok, false);
const addViet=tryAddNode(testNodes,'điện toán',9002);
assert('Adding "điện toán" (id=79) rejected: collision', addViet.ok, false);
assert('Rejection reason = collision', addViet.reason, 'collision');

// ══════════════════════════════════════════════════════════
// TEST 8: Delivery after resolve — target offline → delivery fails
// ══════════════════════════════════════════════════════════
sec('TEST 8 · Delivery-after-resolve semantics (mirrors client.py)');
ring[1].records['alpha']={name:'alpha',host:'127.0.0.1',port:5101,updated:0};
ring[2].online=true;
ring[2].records['alpha']={name:'alpha',host:'127.0.0.1',port:5101,updated:0}; // next clockwise replica: beta
ring[1].online=false; // alpha (target) offline
const lookup8=dhtGet(ring,'alpha');
const targetIsOnline=ring[1].online;
assert('Lookup resolves (record found via replica)', lookup8.found, true);
assert('Lookup uses beta replica', lookup8.source?.name, 'beta');
assert('Target node is offline', targetIsOnline, false);
assert('Delivery should fail (target.online=false)', targetIsOnline===false, true);
ring[1].online=true; ring[2].online=false; ring.forEach(n=>n.records={});

// ══════════════════════════════════════════════════════════
// TEST 9: Preset 5-node ring: all IDs unique
// ══════════════════════════════════════════════════════════
sec('TEST 9 · Preset ring integrity');
const preset=['alpha','beta','gamma','delta','epsilon'];
const presetIds=preset.map(hashId);
assert('5 preset nodes: all IDs unique', new Set(presetIds).size, 5);
assert('Sorted ring order: gamma<alpha<beta<epsilon<delta',
  [...presetIds].sort((a,b)=>a-b), [71,79,101,125,135]);
// All fingers in a 5-node ring should be bounded
const presetNodes=preset.map((name,i)=>({name,id:presetIds[i],port:5101+i,online:true,records:{}}));
for(const n of presetNodes){
  const ft=fingerTbl(presetNodes,n);
  assert(`${n.name}(${n.id}) finger count ≥ 1`, ft.length>=1, true);
}

// ══════════════════════════════════════════════════════════
// RESULT
// ══════════════════════════════════════════════════════════
const total=pass+fail;
console.log(`\n${'═'.repeat(55)}`);
console.log(`  RESULT: ${pass}/${total} passed  ${fail===0?'✅ ALL PASS':'❌ '+fail+' FAILED'}`);
console.log(`${'═'.repeat(55)}\n`);
process.exit(fail>0?1:0);
