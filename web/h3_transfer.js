import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {encode,decode,parseProfile,applyReceipt,CLIPBOARD_LIMIT} from './transfer_core.js';

const value=(node,name)=>node.widgets?.find(w=>w.name===name)?.value;
const setValue=(node,name,newValue)=>{const w=node.widgets?.find(w=>w.name===name);if(w)w.value=newValue;};
function show(text){window.alert(text);}
function manualCopy(text){
  const dialog=document.createElement('dialog');
  const title=document.createElement('p');title.textContent='浏览器未允许自动复制，请全选下面的交接码复制：';
  const area=document.createElement('textarea');area.value=text;area.style.cssText='width:70vw;height:45vh';
  const close=document.createElement('button');close.textContent='关闭';close.onclick=()=>{dialog.close();dialog.remove();};
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
  if(!response.ok){let message='请求失败';try{message=(await response.json()).error || message;}catch{}
    throw new Error(`${message}（HTTP ${response.status}；确认本端已安装H3交接插件）`);}
  return response;
}
function button(node,label,action){
  let busy=false;
  const w=node.addWidget('button',label,null,async()=>{
    if(busy)return;busy=true;const old=w.name;w.name='处理中…';
    try{await action();}catch(error){show(error.message || String(error));}
    finally{busy=false;w.name=old;node.graph?.setDirtyCanvas(true,true);}
  });
  w.options={...(w.options || {}),serialize:false};w.serializeValue=()=>undefined;
}

app.registerExtension({
  name:'h3.transfer',
  nodeCreated(node){
    const kind=node.comfyClass || node.type;
    if(!['H3TransferSend','H3TransferReceive'].includes(kind))return;
    if(kind==='H3TransferSend'){
      async function download(){
        const id=node.properties?.h3_package_id;
        if(!id)throw new Error('请先完成一次一采，发送节点会在交接视频保存后自动创建包');
        const response=await responseData(await api.fetchApi(`/h3_transfer/package/${id}`));
        return response;
      }
      button(node,'复制本次交接码（含素材）',async()=>{
        if(node.properties?.h3_package_bytes>CLIPBOARD_LIMIT)throw new Error('交接包超过32MiB，请点「下载交接ZIP」，云端用「导入ZIP」');
        const bytes=new Uint8Array(await (await download()).arrayBuffer());
        const text=encode(bytes);
        try{await navigator.clipboard.writeText(text);show('已复制完整交接码。在接收节点点击「粘贴并填写二采输入」。');}
        catch{manualCopy(text);}
      });
      button(node,'下载交接ZIP（大视频用此项）',async()=>{
        const blob=await (await download()).blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=url;a.download=`h3-transfer-${node.properties.h3_package_id}.h3.zip`;a.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      });
      const executed=node.onExecuted;
      node.onExecuted=function(message){
        executed?.apply(this,arguments);
        const item=message?.h3_transfer?.[0];
        if(item){this.properties ||= {};this.properties.h3_package_id=item.package_id;this.properties.h3_package_bytes=item.bytes;
          this.title=`H3发送：已打包 ${item.enabled_images} 图 / ${(item.bytes/1048576).toFixed(1)}MiB`;}
      };
    }else{
      async function receive(bytes){
        // Parse the mapping before uploading; applyReceipt checks every target
        // widget before making the first graph change.
        const mapping=parseProfile(value(node,'mapping_json'));
        const response=await responseData(await api.fetchApi('/h3_transfer/import',{
          method:'POST',headers:{'Content-Type':'application/octet-stream'},body:bytes}));
        const receipt=await response.json();
        const result=applyReceipt(node.graph || app.graph,receipt,mapping);
        setValue(node,'import_id',receipt.import_id);
        node.title=`H3接收：已填入 ${result.enabled_images} 图 + 2视频 + 提示词`;
        show('二采输入已填好，参考图开关也已同步。检查二采分辨率后正常点击运行即可。');
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
        const input=document.createElement('input');input.type='file';input.accept='.zip,.h3.zip';
        input.onchange=async()=>{try{const file=input.files?.[0];if(file)await receive(file);}catch(error){show(error.message);}};
        input.click();
      });
    }
    node.size[0]=Math.max(node.size[0],420);
  },
});
