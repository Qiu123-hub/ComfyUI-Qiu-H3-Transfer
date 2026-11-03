// Pure transport/widget helpers, independent of the ComfyUI extension lifecycle.
export const PREFIX='H3TRANSFER1:';
export const CLIPBOARD_LIMIT=32*1024*1024;
export const DEFAULT_PROFILE={prompt:248,reference_video:286,handoff_video:700,
  images:[215,218,221,614,615,618],image_resizes:[214,217,220,613,616,617],
  reference_short_edge:246,reference_video_resize:294};

export function encode(bytes) {
  if(bytes.byteLength>CLIPBOARD_LIMIT)throw new Error('交接包超过32MiB，请使用「下载交接ZIP」与「导入ZIP」');
  const parts=[];
  for(let i=0;i<bytes.length;i+=0x8000)parts.push(String.fromCharCode(...bytes.subarray(i,i+0x8000)));
  return PREFIX+btoa(parts.join(''));
}

export function decode(text) {
  text=text.trim();
  if(!text.startsWith(PREFIX))throw new Error('不是H3交接码，请复制完整交接码或导入ZIP');
  const payload=text.slice(PREFIX.length);
  if(payload.length>Math.ceil(CLIPBOARD_LIMIT/3)*4+4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(payload))
    throw new Error('交接码过大或内容不完整，请使用ZIP导入');
  let binary;
  try{binary=atob(payload);}catch{throw new Error('交接码不完整，请重新复制');}
  return Uint8Array.from(binary,c=>c.charCodeAt(0));
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

export function applyReceipt(graph,receipt,mapping={}) {
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
