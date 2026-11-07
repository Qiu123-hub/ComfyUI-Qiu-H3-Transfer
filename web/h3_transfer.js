import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {encode, decode, parseProfile, applyReceipt, CLIPBOARD_LIMIT, CLIPBOARD_TOO_LARGE,
  ZIP_LIMIT, fetchPackage, managedZipUrl} from './transfer_core.js';
import {importOnRunningHub, isRunningHub} from './runninghub_import.js';
import {addRunningHubSender} from './runninghub_send.js';

const value=(node,name)=>node.widgets?.find(w=>w.name===name)?.value;
const setValue=(node,name,newValue)=>{const w=node.widgets?.find(w=>w.name===name);if(w)w.value=newValue;};
function show(text){window.alert(text);}
function showError(error) {
  if (!error.downloadUrl) return show(error.message || String(error));
  const dialog = document.createElement('dialog');
  const message = document.createElement('p');
  message.textContent = error.message;
  const link = document.createElement('a');
  link.href = error.downloadUrl;
  link.textContent = '直接打开云端 ZIP 下载链接';
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  const close = document.createElement('button');
  close.textContent = '关闭';
  close.onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  dialog.append(message, link, close);
  document.body.append(dialog);
  dialog.showModal();
}
function manualCopy(text){
  const dialog=document.createElement('dialog');
  const title=document.createElement('p');title.textContent='浏览器未允许自动复制，请全选下面的交接码复制：';
  const area=document.createElement('textarea');area.value=text;area.style.cssText='width:70vw;height:45vh';
  const close=document.createElement('button');close.textContent='关闭';close.onclick=()=>dialog.close();
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  dialog.append(title,area,close);document.body.append(dialog);dialog.showModal();area.focus();area.select();
}
function pasteDialog(onSubmit){
  const dialog=document.createElement('dialog'),area=document.createElement('textarea');
  area.placeholder='在此粘贴完整H3交接码';area.style.cssText='width:70vw;height:40vh';
  const button=document.createElement('button');button.textContent='导入并填写输入';
  const cancel=document.createElement('button');cancel.textContent='取消';cancel.onclick=()=>{dialog.close();dialog.remove();};
  button.onclick=()=>{const text=area.value;dialog.close();dialog.remove();onSubmit(text);};
  dialog.append(area,button,cancel);document.body.append(dialog);dialog.showModal();area.focus();
}
async function responseData(response){
  if (response.status === 413) throw new Error('接收端拒绝了文件大小（HTTP 413）。请检查平台上传上限。');
  if (response.status === 403) throw new Error('接收端拒绝了交接导入请求（HTTP 403）。');
  if (response.status === 404) throw new Error('接收端交接导入接口不可用（HTTP 404）。请确认接收端已加载新版插件。');
  if(!response.ok){let message='请求失败';try{message=(await response.json()).error || message;}catch{}
    throw new Error(`${message}（HTTP ${response.status}；确认本端已安装H3交接插件）`);}
  return response;
}
function pickZip() {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.zip,application/zip';
    input.hidden = true;
    const finish = () => {
      resolve(input.files?.[0] || null);
      input.remove();
    };
    input.addEventListener('change', finish, {once: true});
    input.addEventListener('cancel', finish, {once: true});
    document.body.append(input);
    input.click();
  });
}
function button(node,label,action){
  let busy=false;
  const w=node.addWidget('button',label,null,async()=>{
    if(busy)return;busy=true;const old=w.name;w.name='处理中…';
    node.graph?.setDirtyCanvas(true,true);
    try{await action();}catch(error){showError(error);}
    finally{busy=false;w.name=old;node.graph?.setDirtyCanvas(true,true);}
  });
  w.options={...(w.options || {}),serialize:false};w.serialize=false;w.serializeValue=()=>undefined;
}

app.registerExtension({
  name:'h3.transfer',
  rh: {type: 'nodes', nodes: ['H3TransferSend', 'H3TransferReceive']},
  nodeCreated(node){
    const kind=node.comfyClass || node.type;
    if(!['H3TransferSend','H3TransferReceive'].includes(kind))return;
    if(kind==='H3TransferSend'){
      if (['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) {
        addRunningHubSender(node, api, button);
      }
      let cloudArtifact;
      function download(id) {
        return fetchPackage({id, size: node.properties?.h3_package_bytes, nodeId: node.id,
          cloudUrl: cloudArtifact?.id === id ? cloudArtifact.url : null,
          fetchApi: path => api.fetchApi(path)});
      }
      button(node,'复制本次交接码（含素材）',async()=>{
        if (node.properties?.h3_package_bytes > CLIPBOARD_LIMIT) throw new Error(CLIPBOARD_TOO_LARGE);
        const textPromise = download(node.properties?.h3_package_id).then(async blob => {
          if (blob.size > CLIPBOARD_LIMIT) throw new Error(CLIPBOARD_TOO_LARGE);
          return encode(new Uint8Array(await blob.arrayBuffer()));
        });
        if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
          // Start the clipboard write during the click, while the ZIP is fetching.
          const data = textPromise.then(text => new Blob([text], {type: 'text/plain'}));
          data.catch(() => {});
          try {
            await navigator.clipboard.write([new ClipboardItem({'text/plain': data})]);
            show('已复制完整交接码。在接收节点点击「粘贴并填写二采输入」。');
            return;
          } catch {
            // Retry with writeText on browsers without asynchronous ClipboardItem support.
          }
        }
        const text = await textPromise;
        try {
          await navigator.clipboard.writeText(text);
          show('已复制完整交接码。在接收节点点击「粘贴并填写二采输入」。');
        } catch {
          manualCopy(text);
        }
      });
      button(node,'下载交接ZIP（大视频用此项）',async()=>{
        const id = node.properties?.h3_package_id;
        const blob = await download(id);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `h3-transfer-${id}.h3.zip`;
        a.hidden = true;
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      });
      const executed=node.onExecuted;
      node.onExecuted=function(message){
        executed?.apply(this,arguments);
        const item=message?.h3_transfer?.[0];
        if(item){this.properties ||= {};this.properties.h3_package_id=item.package_id;this.properties.h3_package_bytes=item.bytes;
          this.title=`H3发送：已打包 ${item.enabled_images} 图 / ${(item.bytes/1048576).toFixed(1)}MiB`;}
        const id = this.properties?.h3_package_id;
        const url = managedZipUrl(message, id);
        if (url) cloudArtifact = {id, url};
      };
    }else{
      let importing = false;
      async function receive(bytes){
        if ((bytes.size ?? bytes.byteLength) > ZIP_LIMIT) throw new Error('交接 ZIP 超过 512 MiB。');
        if (importing) throw new Error('接收节点正在导入，请稍候。');
        const mapping=parseProfile(value(node,'mapping_json'));
        const graph = node.graph || app.graph;
        // A connected status output still requires a receipt from the Python node.
        const managed = isRunningHub(window.location.hostname) && !node.outputs?.some(port => port.links?.length);
        importing = true;
        try {
          let receipt;
          if (managed) {
            receipt = await importOnRunningHub(bytes, (path, options) => api.fetchApi(path, options),
              draft => applyReceipt(graph, draft, mapping, {validateOnly: true}));
          } else {
            const response = await responseData(await api.fetchApi('/h3_transfer/import', {
              method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: bytes,
            }));
            receipt = await response.json();
          }
          const result = applyReceipt(graph, receipt, mapping);
          setValue(node, 'import_id', receipt.import_id);
          node.title = `H3接收：已填入 ${result.enabled_images} 图 + 2视频 + 提示词`;
          show('二采输入已填好，参考图开关也已同步。检查二采分辨率后正常点击运行即可。');
        } finally {
          importing = false;
        }
      }
      button(node,'粘贴并填写二采输入',async()=>{
        try{const text=await navigator.clipboard.readText();await receive(decode(text));}
        catch(error){
          if(error.name==='NotAllowedError' || error.name==='SecurityError' || !navigator.clipboard){
            pasteDialog(text=>Promise.resolve().then(()=>receive(decode(text))).catch(e=>show(e.message)));
          }else throw error;
        }
      });
      button(node,'导入交接ZIP（大视频用此项）',async()=>{
        const file = await pickZip();
        if (file) await receive(file);
      });
    }
    node.size[0]=Math.max(node.size[0],420);
  },
});
