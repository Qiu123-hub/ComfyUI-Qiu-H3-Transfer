// Pure transport/widget helpers, independent of the ComfyUI extension lifecycle.
export const PREFIX='H3TRANSFER1:';
export const CLIPBOARD_LIMIT=128*1024*1024;
export const ZIP_LIMIT=512*1024*1024;
const PACKAGE_ID = /^[a-f0-9]{32}$/;
export const CLIPBOARD_TOO_LARGE='交接包超过 128 MiB，请使用「下载交接ZIP」与「导入交接ZIP」。';
export const DEFAULT_PROFILE={prompt:248,reference_video:286,handoff_video:700,
  images:[215,218,221,614,615,618],image_resizes:[214,217,220,613,616,617],
  reference_short_edge:246,reference_video_resize:294};

export function encode(bytes) {
  if (bytes.byteLength > CLIPBOARD_LIMIT) throw new Error(CLIPBOARD_TOO_LARGE);
  if (bytes.toBase64) return PREFIX + bytes.toBase64();
  // A multiple of three keeps padding confined to the last block.
  const parts = [PREFIX];
  const blockSize = 24 * 1024;
  for (let i = 0; i < bytes.length; i += blockSize) {
    parts.push(btoa(String.fromCharCode(...bytes.subarray(i, i + blockSize))));
  }
  return parts.join('');
}

export function decode(text) {
  text=text.trim();
  if(!text.startsWith(PREFIX))throw new Error('不是H3交接码，请复制完整交接码或导入ZIP');
  const payload = text.slice(PREFIX.length);
  const padding = payload.endsWith('==') ? 2 : Number(payload.endsWith('='));
  const size = payload.length / 4 * 3 - padding;
  if (size > CLIPBOARD_LIMIT) throw new Error(CLIPBOARD_TOO_LARGE);
  const invalid = '交接码不完整，请重新复制或使用ZIP导入。';
  if (!payload.length || payload.length % 4 || size <= 0) throw new Error(invalid);
  try {
    if (Uint8Array.fromBase64) {
      const bytes = Uint8Array.fromBase64(payload, {lastChunkHandling: 'strict'});
      if (bytes.length !== size) throw new Error(invalid);
      return bytes;
    }
    const bytes = new Uint8Array(size);
    const blockSize = 256 * 1024;
    let offset = 0;
    for (let i = 0; i < payload.length; i += blockSize) {
      const block = payload.slice(i, i + blockSize);
      if (!/^[A-Za-z0-9+/]+={0,2}$/.test(block) ||
          (i + blockSize < payload.length && block.includes('='))) throw new Error(invalid);
      const binary = atob(block);
      for (let j = 0; j < binary.length; j++) bytes[offset++] = binary.charCodeAt(j);
    }
    if (offset !== size) throw new Error(invalid);
    return bytes;
  } catch {
    throw new Error(invalid);
  }
}

export function managedZipUrl(output, id) {
  if (typeof id !== 'string' || !PACKAGE_ID.test(id)) return null;
  const files = Array.isArray(output?.images) ? output.images : [];
  const zip = files.find(file => typeof file?.filename === 'string' &&
    file.filename.startsWith(id) && file.filename.toLowerCase().endsWith('.zip'));
  const link = zip?.cos_url || zip?.fileUrl;
  if (!link) return null;
  try {
    const url = new URL(link);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export async function fetchPackage({id, size, nodeId, cloudUrl, fetchApi, fetchCloud = fetch}) {
  if (typeof id !== 'string' || !PACKAGE_ID.test(id)) {
    throw new Error('请先运行发送节点生成交接包。');
  }
  const failures = [];
  async function readZip(label, request) {
    try {
      const response = await request();
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (Number(response.headers.get('content-length')) > ZIP_LIMIT) throw new Error('超过 512 MiB');
      const blob = await response.blob();
      const header = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
      if (header.join(',') !== '80,75,3,4') throw new Error('响应不是 ZIP 文件');
      if (blob.size > ZIP_LIMIT || (size > 0 && blob.size !== size)) throw new Error('交接包大小不符');
      return blob;
    } catch (error) {
      failures.push(`${label}：${error.message}`);
      return null;
    }
  }
  const getCloud = url => readZip('云端产物', () => fetchCloud(url, {credentials: 'omit'}));
  const query = new URLSearchParams({filename: `${id}.h3.zip`, subfolder: 'h3_transfer', type: 'output'});
  const blob = (cloudUrl && await getCloud(cloudUrl)) ||
    await readZip('交接接口', () => fetchApi(`/h3_transfer/package/${id}`)) ||
    await readZip('ComfyUI 输出', () => fetchApi(`/view?${query}`));
  if (blob) return blob;

  // The output processor can attach its storage URL after the executed event.
  try {
    const response = await fetchApi('/history?max_items=8');
    if (response.ok) {
      const history = await response.json();
      for (const task of Object.values(history)) {
        const output = task?.outputs?.[nodeId];
        const url = managedZipUrl(output, id);
        if (url && url !== cloudUrl) {
          cloudUrl = url;
          const recovered = await getCloud(url);
          if (recovered) return recovered;
          break;
        }
      }
    }
  } catch {
    // A managed frontend may expose task results separately from ComfyUI history.
  }
  const error = new Error(`读取交接 ZIP 失败（${failures.join('；')}）。请在本次任务结果中下载 ${id}.h3.zip，或重新运行新版发送节点生成产物。`);
  error.downloadUrl = cloudUrl;
  throw error;
}

export function parseProfile(text='{}') {
  const parsed=JSON.parse(text || '{}');
  if(!parsed || typeof parsed!=='object' || Array.isArray(parsed))throw new Error('节点映射必须是JSON对象');
  const result={...DEFAULT_PROFILE,...parsed};
  for(const key of ['images','image_resizes']){
    if(!Array.isArray(result[key]) || result[key].length!==6)throw new Error('参考图映射必须有6个槽位');
  }
  return result;
}

export function applyReceipt(graph,receipt,mapping={}, {validateOnly = false} = {}) {
  const conf={...DEFAULT_PROFILE,...mapping};
  if(!receipt || !Array.isArray(receipt.reference_images) || receipt.reference_images.length!==6 ||
     typeof receipt.prompt!=='string' || !receipt.reference_video?.path || !receipt.first_pass_video?.path)
    throw new Error('接收结果不完整');
  const changes=[],modes=[];
  const node=(id,type)=>{
    const n=graph.getNodeById(id);
    if(!n || (n.comfyClass || n.type)!==type)throw new Error(`节点#${id}不匹配（需要${type}），请检查节点映射`);
    return n;
  };
  const plan=(n,name,value)=>{
    const w=n.widgets?.find(w=>w.name===name);
    if(!w)throw new Error(`节点#${n.id}没有 ${name} 控件`);
    changes.push({n,w,value,old:w.value,oldOptions:Array.isArray(w.options?.values)?[...w.options.values]:null});
  };
  plan(node(conf.prompt,'PrimitiveStringMultiline'),'value',receipt.prompt);
  const reference=node(conf.reference_video,'VHS_LoadVideo');
  plan(reference,'video',receipt.reference_video.path);
  for(const [name,value] of Object.entries(receipt.reference_video.loader || {}))plan(reference,name,value);
  const handoff=node(conf.handoff_video,'VHS_LoadVideo');
  plan(handoff,'video',receipt.first_pass_video.path);
  for(const [name,value] of Object.entries({force_rate:24,custom_width:0,custom_height:0,
    frame_load_cap:0,skip_first_frames:0,select_every_nth:1,format:'AnimateDiff'}))plan(handoff,name,value);
  receipt.reference_images.forEach((item,i)=>{
    if(item.slot!==i+1 || typeof item.enabled!=='boolean' || item.enabled&&!item.path)throw new Error('参考图槽位无效');
    const image=node(conf.images[i],'LoadImage'),resize=node(conf.image_resizes[i],'ResizeImagesByShorterEdge');
    if(item.enabled)plan(image,'image',item.path);
    for(const n of [image,resize])modes.push({n,value:item.enabled?0:4,old:n.mode});
  });
  plan(node(conf.reference_short_edge,'PrimitiveInt'),'value',receipt.common_settings.reference_short_edge);
  const resize=node(conf.reference_video_resize,'ImageScaleToTotalPixels');
  for(const [name,value] of Object.entries(receipt.common_settings.reference_video_resize))plan(resize,name,value);
  if (validateOnly) return;
  // All mappings are checked before the first mutation. Roll back if a custom
  // node callback fails; do not leave a half-filled graph ready to queue.
  try{
    for(const {n,w,value} of changes){
      if(Array.isArray(w.options?.values) && !w.options.values.includes(value))w.options.values.push(value);
      w.value=value;
      w.callback?.call(w,value,null,n);
    }
    for(const {n,value} of modes)n.mode=value;
  }catch(error){
    for(const {w,old,oldOptions} of changes){w.value=old;if(oldOptions)w.options.values=oldOptions;}
    for(const {n,old} of modes)n.mode=old;
    graph.setDirtyCanvas?.(true,true);
    throw new Error(`填入失败，已回滚控件：${error.message}`);
  }
  graph.setDirtyCanvas?.(true,true);
  return {enabled_images:receipt.reference_images.filter(i=>i.enabled).length,changed_widgets:changes.length};
}
