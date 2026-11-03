"""Self-contained media handoff archives. No ComfyUI or GPU dependency."""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid
import zipfile

MAX_BYTES=512*1024*1024
MAX_MANIFEST=1024*1024
MAX_ASSETS=16
IMAGE_EXT={'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}
VIDEO_EXT={'.mp4','.mov','.mkv','.webm','.gif','.avi','.m4v'}
LOADER_FIELDS=('force_rate','custom_width','custom_height','frame_load_cap','skip_first_frames','select_every_nth','format')
DEFAULT_PROFILE={
    'prompt':248,'reference_video':286,'handoff_video':700,
    'images':[215,218,221,614,615,618],
    'image_resizes':[214,217,220,613,616,617],
    'reference_short_edge':246,'reference_video_resize':294,
    'output_video':633,'previous_video':592,'continuation_guide':588,
    'record_nodes':[209,212,205,211,285,213,210,204,188],
}


def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def profile(value=None):
    result=copy.deepcopy(DEFAULT_PROFILE)
    if value:
        parsed=json.loads(value) if isinstance(value,str) else value
        if not isinstance(parsed,dict) or set(parsed)-set(result):raise ValueError('节点映射字段无效')
        result.update(parsed)
    for key in ['images','image_resizes']:
        if not isinstance(result[key],list) or len(result[key])!=6:raise ValueError('参考图映射必须包含6个槽位')
    for key,value in result.items():
        values=value if isinstance(value,list) else [value]
        if any(type(v) is not int or v<0 for v in values):raise ValueError(f'节点映射 {key} 必须是非负整数ID')
    return result


def inside(path,roots):
    p=Path(path).resolve()
    if not any(p.is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError('素材必须位于 ComfyUI input/output/temp 目录内')
    if not p.is_file():raise ValueError(f'素材不存在：{p.name}')
    return p


def widget(node,name,index,api_prompt=None):
    # Prefer the actual executed API value (e.g. the seed before increment).
    api=(api_prompt or {}).get(str(node['id']),{}).get('inputs',{})
    if name in api and not isinstance(api[name],list):return api[name]
    values=node.get('widgets_values',[])
    if isinstance(values,dict):return values.get(name)
    named=node.get('widgets_values_named',{})
    if isinstance(values,list) and index<len(values):return values[index]
    return named.get(name)


def create_package(workflow,api_prompt,filenames,resolve_input,allowed_output_roots,
                   package_dir,mapping=None,include_previous=True):
    """Snapshot inputs only after the connected VHS output has finished saving."""
    conf=profile(mapping)
    if not isinstance(workflow,dict) or not isinstance(workflow.get('nodes'),list):
        raise ValueError('缺少工作流快照：请正常提交 ComfyUI 工作流并启用元数据')
    nodes={n['id']:n for n in workflow['nodes']}
    def get(key,kind=None):
        nid=conf[key];n=nodes.get(nid)
        if not n or kind and n.get('type')!=kind:raise ValueError(f'节点映射 {key} 不匹配，请检查映射JSON')
        return n
    prompt=widget(get('prompt','PrimitiveStringMultiline'),'value',0,api_prompt)
    if not isinstance(prompt,str):raise ValueError('提示词不是文本')
    if not isinstance(filenames,(tuple,list)) or len(filenames)!=2 or not isinstance(filenames[1],(tuple,list)):
        raise ValueError('请把发送节点连接到一采交接视频的 VHS_FILENAMES 输出')
    candidates=[p for p in filenames[1] if Path(p).suffix.lower() in VIDEO_EXT]
    if not candidates:raise ValueError('一采输出中没有视频文件')
    # VHS documents -1 as the most complete output (usually the muxed audio MP4).
    handoff=inside(candidates[-1],allowed_output_roots)
    assets={};sources={};total=0
    def asset(path,kind):
        nonlocal total
        p=Path(path);ext=p.suffix.lower()
        if ext not in (IMAGE_EXT if kind=='image' else VIDEO_EXT):raise ValueError(f'不支持的{kind}文件格式：{ext}')
        key='assets/'+digest(p)+ext
        if key not in assets:
            size=p.stat().st_size;total+=size
            if total>MAX_BYTES or len(assets)>=MAX_ASSETS:raise ValueError('交接包素材过大或数量过多')
            assets[key]={'sha256':key.split('/')[1].split('.')[0],'size':size,'name':p.name[:200],'kind':kind}
            sources[key]=p
        return key
    video=get('reference_video','VHS_LoadVideo')
    original_name=widget(video,'video',0,api_prompt)
    if not original_name:raise ValueError('原参考视频为空')
    loader={k:widget(video,k,i,api_prompt) for i,k in enumerate(['video',*LOADER_FIELDS]) if k!='video'}
    manifest={'format':'h3-transfer','version':1,
              'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'prompt':prompt,'assets':assets,
              'reference_video':{'asset':asset(resolve_input(original_name),'video'),'loader':loader},
              'reference_images':[],
              'first_pass_video':{'asset':asset(handoff,'video')},
              'common_settings':{},'record_only':{}}
    for index,nid in enumerate(conf['images']):
        n=nodes.get(nid)
        if not n or n.get('type')!='LoadImage':raise ValueError(f'参考图{index+1}映射无效')
        resize=nodes.get(conf['image_resizes'][index])
        enabled=n.get('mode',0)==0 and (resize is None or resize.get('mode',0)==0)
        entry={'slot':index+1,'enabled':enabled,'asset':None}
        if enabled:
            name=widget(n,'image',0,api_prompt)
            if not name:raise ValueError(f'已开启的参考图{index+1}没有文件')
            entry['asset']=asset(resolve_input(name),'image')
        manifest['reference_images'].append(entry)
    short=get('reference_short_edge','PrimitiveInt')
    manifest['common_settings']['reference_short_edge']=widget(short,'value',0,api_prompt)
    resize=get('reference_video_resize','ImageScaleToTotalPixels')
    manifest['common_settings']['reference_video_resize']={name:widget(resize,name,i,api_prompt)
        for i,name in enumerate(['upscale_method','megapixels','resolution_steps'])}
    for nid in conf['record_nodes']:
        n=nodes.get(nid)
        if n:manifest['record_only'][str(nid)]={'type':n.get('type'),'widgets':copy.deepcopy(n.get('widgets_values',[]))}
    guide=nodes.get(conf['continuation_guide'])
    continuing=bool(guide and guide.get('mode',0)==0)
    manifest['continuation']={'enabled':continuing,'previous_video_asset':None}
    if include_previous and continuing:
        n=get('previous_video','VHS_LoadVideo');name=widget(n,'video',0,api_prompt)
        if not name:raise ValueError('续跑已开启，但上一段成品为空')
        manifest['continuation']['previous_video_asset']=asset(resolve_input(name),'video')
    validate_manifest(manifest)
    package_id=uuid.uuid4().hex
    directory=Path(package_dir);directory.mkdir(parents=True,exist_ok=True)
    final=directory/(package_id+'.h3.zip');partial=directory/(package_id+'.part')
    try:
        encoded_manifest=json.dumps(manifest,ensure_ascii=False).encode('utf-8')
        if len(encoded_manifest)>MAX_MANIFEST:raise ValueError('交接清单过大')
        with zipfile.ZipFile(partial,'w',zipfile.ZIP_STORED) as archive:
            archive.writestr('manifest.json',encoded_manifest)
            for key,path in sources.items():archive.write(path,key)
        if partial.stat().st_size>MAX_BYTES:raise ValueError('交接包超过512MiB限制')
        os.replace(partial,final)
    finally:
        if partial.exists():partial.unlink()
    return {'package_id':package_id,'path':str(final),'bytes':final.stat().st_size,
            'asset_count':len(assets),'enabled_images':sum(i['enabled'] for i in manifest['reference_images'])}


def validate_manifest(m):
    if not isinstance(m,dict) or m.get('format')!='h3-transfer' or m.get('version')!=1:
        raise ValueError('不是支持的 H3 交接包')
    if not isinstance(m.get('prompt'),str) or len(m['prompt'])>200000:raise ValueError('提示词格式无效')
    assets=m.get('assets')
    if not isinstance(assets,dict) or not 1<=len(assets)<=MAX_ASSETS:raise ValueError('素材清单无效')
    total=0
    for key,info in assets.items():
        if not isinstance(key,str) or not re.fullmatch(r'assets/[0-9a-f]{64}\.[a-z0-9]{2,5}',key):
            raise ValueError('素材路径无效')
        if not isinstance(info,dict) or info.get('sha256')!=key.split('/')[1].split('.')[0]:raise ValueError('素材校验信息无效')
        if type(info.get('size')) is not int or info['size']<0:raise ValueError('素材大小无效')
        total+=info['size']
        ext=Path(key).suffix
        if info.get('kind') not in ['image','video'] or ext not in (IMAGE_EXT if info['kind']=='image' else VIDEO_EXT):
            raise ValueError('素材类型无效')
    if total>MAX_BYTES:raise ValueError('素材总量超过512MiB')
    def ref(value,kind,required=True):
        if value is None and not required:return
        if not isinstance(value,str) or value not in assets or assets[value]['kind']!=kind:raise ValueError('素材引用无效')
    for name in ['reference_video','first_pass_video']:
        if not isinstance(m.get(name),dict):raise ValueError('缺少视频素材')
        ref(m[name].get('asset'),'video')
    loader=m['reference_video'].get('loader')
    if not isinstance(loader,dict) or set(loader)-set(LOADER_FIELDS):raise ValueError('视频读取参数无效')
    for name,value in loader.items():
        if name=='format':
            if not isinstance(value,str) or len(value)>100:raise ValueError('视频格式参数无效')
        elif type(value) not in [int,float] or not 0<=value<=1000000000:raise ValueError('视频读取数值无效')
    images=m.get('reference_images')
    if not isinstance(images,list) or len(images)!=6 or [i.get('slot') for i in images if isinstance(i,dict)]!=list(range(1,7)):
        raise ValueError('必须包含按顺序排列的6个参考图槽位')
    for item in images:
        if type(item.get('enabled')) is not bool:raise ValueError('参考图开关无效')
        ref(item.get('asset'),'image',item['enabled'])
    common=m.get('common_settings',{})
    if not isinstance(common,dict):raise ValueError('公共设置无效')
    short=common.get('reference_short_edge')
    if type(short) is not int or not 1<=short<=32768:raise ValueError('参考图短边参数无效')
    size=common.get('reference_video_resize')
    if not isinstance(size,dict) or set(size)!={'upscale_method','megapixels','resolution_steps'}:raise ValueError('参考视频尺寸设置无效')
    if size['upscale_method'] not in ['nearest-exact','bilinear','area','bicubic','lanczos']:raise ValueError('缩放方式无效')
    if type(size['megapixels']) not in [float,int] or not 0<size['megapixels']<=100:raise ValueError('像素设置无效')
    if type(size['resolution_steps']) is not int or not 1<=size['resolution_steps']<=4096:raise ValueError('尺寸对齐参数无效')
    continuation=m.get('continuation',{})
    if not isinstance(continuation,dict):raise ValueError('续跑记录无效')
    ref(continuation.get('previous_video_asset'),'video',False)


def import_package(archive_path,input_dir):
    """Validate before committing any assets; never extract a path from the ZIP."""
    src=Path(archive_path)
    if src.stat().st_size>MAX_BYTES:raise ValueError('交接包超过512MiB')
    base=Path(input_dir).resolve();root=base/'h3_transfer'/'imports'
    root.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex;stage=root/('.'+token+'.partial');final=root/token
    assert stage.resolve().is_relative_to(root.resolve()) and final.resolve().is_relative_to(root.resolve())
    stage.mkdir()
    try:
        with zipfile.ZipFile(src) as archive:
            members=archive.infolist();names=[i.filename for i in members]
            if len(names)!=len(set(names)) or len(names)>MAX_ASSETS+1:raise ValueError('交接包包含重复或过多文件')
            if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>MAX_MANIFEST:raise ValueError('交接清单缺失或过大')
            if any(i.flag_bits&1 or stat.S_ISLNK(i.external_attr>>16) for i in members):raise ValueError('不支持加密文件或符号链接')
            if sum(i.file_size for i in members)>MAX_BYTES:raise ValueError('交接包解压后过大')
            manifest=json.loads(archive.read('manifest.json'))
            validate_manifest(manifest)
            if set(names)!={'manifest.json',*manifest['assets']}:raise ValueError('交接包包含未声明文件')
            for key,info in manifest['assets'].items():
                member=archive.getinfo(key)
                if member.file_size!=info['size']:raise ValueError('素材大小校验失败')
                destination=stage/Path(key).name
                h=hashlib.sha256();count=0
                with archive.open(key) as inp,destination.open('xb') as out:
                    for block in iter(lambda:inp.read(1024*1024),b''):
                        count+=len(block)
                        if count>info['size']:raise ValueError('素材大小超出声明')
                        h.update(block);out.write(block)
                if h.hexdigest()!=info['sha256']:raise ValueError('素材内容校验失败')
        def path_for(key):return (final/Path(key).name).relative_to(base).as_posix() if key else None
        receipt={'import_id':token,'prompt':manifest['prompt'],
                 'reference_video':{'path':path_for(manifest['reference_video']['asset']),
                                    'loader':manifest['reference_video']['loader']},
                 'first_pass_video':{'path':path_for(manifest['first_pass_video']['asset'])},
                 'reference_images':[{'slot':i['slot'],'enabled':i['enabled'],'path':path_for(i['asset'])} for i in manifest['reference_images']],
                 'common_settings':manifest['common_settings']}
        (stage/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf-8')
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
        stage.rename(final)
        return receipt
    except Exception:
        # Only the UUID staging directory created above can be removed.
        assert stage.resolve().is_relative_to(root.resolve())
        shutil.rmtree(stage)
        raise


def valid_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise ValueError('交接ID无效')
    return value
