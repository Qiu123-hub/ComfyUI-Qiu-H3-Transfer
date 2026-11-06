import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const extension=fs.readFileSync(path.join(root,'web/h3_transfer.js'),'utf8');
for(const base of ['https://host.example/extensions/H3/h3_transfer.js',
                   'https://host.example/proxy/token/extensions/H3/h3_transfer.js']){
  const relative=extension.match(/import \{app\} from '([^']+)'/)[1];
  const resolved=new URL(relative,base).pathname;
  assert.equal(resolved,base.includes('/proxy/')?'/proxy/token/scripts/app.js':'/scripts/app.js');
}
// A data module exercises the shipped pure JS without a bundler/package.json.
const source=fs.readFileSync(path.join(root,'web/transfer_core.js'),'utf8');
const {encode,decode,parseProfile,applyReceipt,fetchPackage,managedZipUrl}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const bytes=Uint8Array.from({length:10000},(_,i)=>i%256);
assert.deepEqual(decode(encode(bytes)),bytes);
assert.throws(()=>decode('wrong prefix'));
assert.throws(()=>decode('H3TRANSFER1:invalid?'));
assert.throws(()=>parseProfile('{"images":[1]}'));

// Match the reported package size and exercise the non-native browser path.
const large = new Uint8Array(Math.round(40.2 * 1024 * 1024));
for (let i = 0; i < large.length; i++) large[i] = (i ^ (i >>> 11)) & 255;
const expected = Buffer.from(large.buffer).toString('base64');
const nativeEncode = Object.getOwnPropertyDescriptor(Uint8Array.prototype, 'toBase64');
const nativeDecode = Object.getOwnPropertyDescriptor(Uint8Array, 'fromBase64');
try {
  if (nativeEncode) delete Uint8Array.prototype.toBase64;
  if (nativeDecode) delete Uint8Array.fromBase64;
  const text = encode(large);
  assert.equal(text, 'H3TRANSFER1:' + expected);
  assert.equal(Buffer.compare(Buffer.from(decode(text).buffer), Buffer.from(large.buffer)), 0);
} finally {
  if (nativeEncode) Object.defineProperty(Uint8Array.prototype, 'toBase64', nativeEncode);
  if (nativeDecode) Object.defineProperty(Uint8Array, 'fromBase64', nativeDecode);
}

const id = 'c'.repeat(32);
const zipBytes = Uint8Array.of(80, 75, 3, 4, 11, 22);
const cloudUrl = 'https://assets.example/run/package.zip';
const output = {images: [{filename: `${id}.h3_managed.zip`, cos_url: cloudUrl}]};
const response = () => new Response(zipBytes);
const missing = () => new Response(null, {status: 404});
assert.equal(managedZipUrl(output, id), cloudUrl);
assert.equal(managedZipUrl(output, 'b'.repeat(32)), null);
assert.equal(managedZipUrl({images: [{filename: `${id}.zip`, cos_url: 'javascript:alert(1)'}]}, id), null);
const fromView = await fetchPackage({id, size: zipBytes.length, nodeId: 900,
  fetchApi: async path => path.startsWith('/view?') ? response() : missing()});
assert.deepEqual(new Uint8Array(await fromView.arrayBuffer()), zipBytes);
const fromHistory = await fetchPackage({id, size: zipBytes.length, nodeId: 900,
  fetchApi: async path => path.startsWith('/history?')
    ? Response.json({task: {outputs: {900: output}}}) : missing(),
  fetchCloud: async (url, options) => {
    assert.equal(url, cloudUrl);
    assert.equal(options.credentials, 'omit');
    return response();
  }});
assert.deepEqual(new Uint8Array(await fromHistory.arrayBuffer()), zipBytes);
await assert.rejects(fetchPackage({id, nodeId: 900, cloudUrl,
  fetchApi: async path => path.startsWith('/view?') ? new Response('<html>') : missing(),
  fetchCloud: async () => {throw new TypeError('CORS');}}), error =>
  error.downloadUrl === cloudUrl && error.message.includes('响应不是 ZIP 文件') && error.message.includes('HTTP 404'));

const workflow=JSON.parse(fs.readFileSync(path.join(root,'examples/stage2_cloud_receiver.json'),'utf8'));
function fixture(){
  const map=new Map();
  for(const n of workflow.nodes){
    const copy=structuredClone(n);
    copy.widgets=n.inputs.filter(i=>i.widget).map(p=>({name:p.widget.name,
      value:n.widgets_values_named?.[p.widget.name] ?? (typeof n.widgets_values==='object'&&!Array.isArray(n.widgets_values)?n.widgets_values[p.widget.name]:null),
      options:{values:[]},callback(){}}));
    map.set(n.id,copy);
  }
  return {getNodeById:id=>map.get(id),setDirtyCanvas(){},map};
}
const receipt={import_id:'a'.repeat(32),prompt:'中文提示词 <Picture 1> 🐱',
  reference_video:{path:'h3_transfer/imports/a/original.mp4',loader:{force_rate:24,custom_width:0,custom_height:0,frame_load_cap:0,skip_first_frames:0,select_every_nth:1,format:'AnimateDiff'}},
  first_pass_video:{path:'h3_transfer/imports/a/handoff.mp4'},
  reference_images:Array.from({length:6},(_,i)=>({slot:i+1,enabled:i===0,path:i===0?'h3_transfer/imports/a/ref.png':null})),
  common_settings:{reference_short_edge:736,reference_video_resize:{upscale_method:'nearest-exact',megapixels:0.8,resolution_steps:32}}};
const graph=fixture();
const beforeSampler=JSON.stringify(graph.getNodeById(185).widgets);
const result=applyReceipt(graph,receipt);
assert.equal(result.enabled_images,1);
const value=(id,name)=>graph.getNodeById(id).widgets.find(w=>w.name===name).value;
assert.equal(value(248,'value'),receipt.prompt);
assert.equal(value(286,'video'),receipt.reference_video.path);
assert.equal(value(700,'video'),receipt.first_pass_video.path);
assert.equal(value(246,'value'),736);
assert.equal(value(294,'megapixels'),0.8);
assert.equal(JSON.stringify(graph.getNodeById(185).widgets),beforeSampler);
for(const id of [218,217,221,220,614,613,615,616,618,617])assert.equal(graph.getNodeById(id).mode,4);
assert.equal(graph.getNodeById(215).mode,0);
// Wrong destination mapping must fail before mutating any field.
const wrong=fixture(),snapshot=JSON.stringify([...wrong.map]);
assert.throws(()=>applyReceipt(wrong,receipt,{reference_short_edge:99999}));
assert.equal(JSON.stringify([...wrong.map]),snapshot);
// A node callback failure must roll back all planned values and modes.
const broken=fixture(),old=JSON.stringify([...broken.map]);
broken.getNodeById(700).widgets.find(w=>w.name==='video').callback=()=>{throw new Error('simulated callback failure');};
assert.throws(()=>applyReceipt(broken,receipt),/回滚/);
assert.equal(JSON.stringify([...broken.map]),old);
console.log('Frontend: 40.2 MiB clipboard round trip, ZIP 404 fallback and import state checks passed.');
