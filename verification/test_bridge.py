import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType,SimpleNamespace
import unittest
import warnings
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('core',ROOT/'bridge_core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


def fixture(base):
    inp=base/'local/input';out=base/'local/output';tmp=base/'local/temp'
    for path in [inp,out,tmp]:path.mkdir(parents=True)
    (inp/'参考图.png').write_bytes(b'png-file-content')
    (inp/'原视频.mp4').write_bytes(b'source-video-exact-bytes')
    (inp/'上一段.mp4').write_bytes(b'previous-video')
    (out/'handoff.mp4').write_bytes(b'final-audio-video')
    (out/'silent.mp4').write_bytes(b'intermediate-video')
    workflow={'nodes':[
        {'id':248,'type':'PrimitiveStringMultiline','widgets_values':['旧UI提示词']},
        {'id':286,'type':'VHS_LoadVideo','widgets_values':{'video':'原视频.mp4','force_rate':24,
            'custom_width':0,'custom_height':0,'frame_load_cap':0,'skip_first_frames':0,'select_every_nth':1,'format':'AnimateDiff'}},
        {'id':246,'type':'PrimitiveInt','widgets_values':[928,'fixed']},
        {'id':294,'type':'ImageScaleToTotalPixels','widgets_values':['nearest-exact',0.9,32]},
        {'id':592,'type':'VHS_LoadVideo','widgets_values':{'video':'上一段.mp4'}},
        {'id':588,'type':'MiniMaxH3AddGuide','mode':0},
        {'id':205,'type':'RandomNoise','widgets_values':[123,'increment']},
    ]}
    for i,(image,resize) in enumerate(zip(core.DEFAULT_PROFILE['images'],core.DEFAULT_PROFILE['image_resizes'])):
        workflow['nodes'] += [{'id':image,'type':'LoadImage','mode':0 if i==0 else 4,
            'widgets_values':['参考图.png' if i==0 else '不存在.png','image']},
            {'id':resize,'type':'ResizeImagesByShorterEdge','mode':0 if i==0 else 4}]
    return inp,out,tmp,workflow


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'verification')
        self.base=Path(self.temp.name).resolve()
        assert self.base.is_relative_to((ROOT/'verification').resolve())
        self.inp,self.out,self.tmp,self.workflow=fixture(self.base)
        self.api={'248':{'inputs':{'value':'实际提示词 <Picture 1> 中文 🐱'}},'205':{'inputs':{'noise_seed':122}}}
    def tearDown(self):
        assert self.base.is_relative_to((ROOT/'verification').resolve())
        self.temp.cleanup()
    def package(self,**kwargs):
        return core.create_package(self.workflow,self.api,
            (True,[str(self.out/'silent.mp4'),str(self.out/'handoff.mp4')]),
            lambda name:core.inside(self.inp/name,[self.inp]),[self.out,self.tmp],self.out/'packages',**kwargs)
    def manifest(self,result):
        with zipfile.ZipFile(result['path']) as archive:return json.loads(archive.read('manifest.json'))
    def rewrite(self,manifest=None,extra=None,change=None):
        result=self.package();dest=self.base/'modified.zip'
        with zipfile.ZipFile(result['path']) as src,zipfile.ZipFile(dest,'w') as dst:
            for info in src.infolist():
                data=src.read(info)
                if info.filename=='manifest.json' and manifest:data=json.dumps(manifest(json.loads(data))).encode()
                if change and info.filename.startswith('assets/'):data=change(data)
                dst.writestr(info,data)
            if extra:dst.writestr(*extra)
        return dest

    def test_round_trip_preserves_inputs_states_and_complete_output(self):
        result=self.package();m=self.manifest(result)
        self.assertEqual(m['prompt'],'实际提示词 <Picture 1> 中文 🐱')
        self.assertEqual([i['enabled'] for i in m['reference_images']],[True,False,False,False,False,False])
        cloud=self.base/'cloud/input'
        receipt=core.import_package(result['path'],cloud)
        self.assertEqual(receipt['prompt'],m['prompt'])
        self.assertEqual((cloud/receipt['reference_video']['path']).read_bytes(),b'source-video-exact-bytes')
        self.assertEqual((cloud/receipt['first_pass_video']['path']).read_bytes(),b'final-audio-video')
        self.assertEqual((cloud/receipt['reference_images'][0]['path']).read_bytes(),b'png-file-content')
        self.assertTrue(all(i['path'] is None for i in receipt['reference_images'][1:]))
        self.assertEqual(receipt['common_settings']['reference_short_edge'],928)
        self.assertNotIn(str(self.inp),json.dumps(receipt))

    def test_disabled_assets_are_not_read(self):
        self.assertEqual(self.package()['enabled_images'],1)

    def test_identical_assets_are_deduplicated(self):
        for n in self.workflow['nodes']:
            if n['id'] in [218,217]:n['mode']=0
            if n['id']==218:n['widgets_values'][0]='参考图.png'
        m=self.manifest(self.package())
        self.assertEqual(m['reference_images'][0]['asset'],m['reference_images'][1]['asset'])
        self.assertEqual(len(m['assets']),4)

    def test_previous_video_can_be_recorded_or_omitted(self):
        self.assertIsNotNone(self.manifest(self.package())['continuation']['previous_video_asset'])
        self.assertIsNone(self.manifest(self.package(include_previous=False))['continuation']['previous_video_asset'])
        for n in self.workflow['nodes']:
            if n['id']==588:n['mode']=4
            if n['id']==592:n['widgets_values']['video']=''
        self.assertFalse(self.manifest(self.package())['continuation']['enabled'])

    def test_rejects_input_and_output_paths_outside_roots(self):
        outside=self.base/'private.mp4';outside.write_bytes(b'private')
        with self.assertRaises(ValueError):core.inside(outside,[self.inp])
        with self.assertRaises(ValueError):core.create_package(self.workflow,self.api,(True,[str(outside)]),
            lambda name:self.inp/name,[self.out],self.out/'packages')

    def test_hash_corruption_rejected_and_staging_cleaned(self):
        broken=self.rewrite(change=lambda data:b'X'*len(data));cloud=self.base/'cloud'
        with self.assertRaisesRegex(ValueError,'校验失败'):core.import_package(broken,cloud)
        self.assertEqual(list((cloud/'h3_transfer/imports').iterdir()),[])

    def test_undeclared_traversal_file_rejected(self):
        broken=self.rewrite(extra=('../escape.txt',b'bad'));cloud=self.base/'cloud'
        with self.assertRaises(ValueError):core.import_package(broken,cloud)
        self.assertFalse((self.base/'escape.txt').exists())

    def test_duplicate_member_rejected(self):
        result=self.package();dest=self.base/'duplicate.zip'
        with zipfile.ZipFile(result['path']) as src,zipfile.ZipFile(dest,'w') as dst:
            for item in src.infolist():dst.writestr(item,src.read(item))
            with warnings.catch_warnings():
                warnings.simplefilter('ignore');dst.writestr('manifest.json',src.read('manifest.json'))
        with self.assertRaisesRegex(ValueError,'重复'):core.import_package(dest,self.base/'cloud')

    def test_symlink_member_rejected(self):
        info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
        broken=self.rewrite(extra=(info,b'../../outside'))
        with self.assertRaisesRegex(ValueError,'符号链接'):core.import_package(broken,self.base/'cloud')

    def test_invalid_manifest_slots_and_ids_rejected(self):
        m=self.manifest(self.package());m['reference_images'][0]['slot']=6
        with self.assertRaises(ValueError):core.validate_manifest(m)
        for value in ['../x','0'*31,'G'*32]:
            with self.assertRaises(ValueError):core.valid_id(value)
        with self.assertRaises(ValueError):core.profile({'images':[1]})


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'verification');self.base=Path(self.temp.name).resolve()
        assert self.base.is_relative_to((ROOT/'verification').resolve())
        self.inp,self.out,self.tmp,self.workflow=fixture(self.base)
        from aiohttp import web
        self.fakefolders=ModuleType('folder_paths')
        for name,path in [('input',self.inp),('output',self.out),('temp',self.tmp)]:
            setattr(self.fakefolders,'get_'+name+'_directory',lambda p=path:str(p))
        self.fakefolders.get_annotated_filepath=lambda name:str(self.inp/name)
        self.fakeserver=ModuleType('server');self.fakeserver.PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=web.RouteTableDef()))
        self.old={k:sys.modules.get(k) for k in ['folder_paths','server']}
        sys.modules.update(folder_paths=self.fakefolders,server=self.fakeserver)
        spec=importlib.util.spec_from_file_location('test_h3_plugin',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
        self.plugin=importlib.util.module_from_spec(spec);sys.modules['test_h3_plugin']=self.plugin;spec.loader.exec_module(self.plugin)
        self.routes=sys.modules['test_h3_plugin.routes']
    async def asyncTearDown(self):
        for key,value in self.old.items():
            if value is None:sys.modules.pop(key,None)
            else:sys.modules[key]=value
        for key in list(sys.modules):
            if key=='test_h3_plugin' or key.startswith('test_h3_plugin.'):sys.modules.pop(key,None)
        assert self.base.is_relative_to((ROOT/'verification').resolve())
        self.temp.cleanup()

    async def test_nodes_and_upload_download_round_trip(self):
        sender=self.plugin.NODE_CLASS_MAPPINGS['H3TransferSend']()
        result=sender.send((True,[str(self.out/'handoff.mp4')]),extra_pnginfo={'workflow':self.workflow})
        item=result['ui']['h3_transfer'][0];self.assertNotIn('path',item)
        download=await self.routes.download(SimpleNamespace(match_info={'package_id':item['package_id']}))
        self.assertEqual(download.status,200)
        data=(self.out/'h3_transfer'/(item['package_id']+'.h3.zip')).read_bytes()
        class Content:
            async def iter_chunked(self,size):
                for i in range(0,len(data),size):yield data[i:i+size]
        request=SimpleNamespace(headers={'Origin':'http://localhost:8188'},host='localhost:8188',content=Content())
        response=await self.routes.upload(request);self.assertEqual(response.status,200,response.text)
        receipt=json.loads(response.text)
        receiver=self.plugin.NODE_CLASS_MAPPINGS['H3TransferReceive']()
        self.assertIn('已接收',receiver.receive(receipt['import_id'])[0])
        self.assertEqual(list((self.tmp/'h3_transfer_upload').iterdir()),[])

    async def test_cross_origin_import_is_rejected(self):
        from aiohttp import web
        with self.assertRaises(web.HTTPForbidden):self.routes.same_origin(SimpleNamespace(headers={'Origin':'https://unrelated.example'},host='localhost:8188'))

    async def test_invalid_download_id_rejected(self):
        response=await self.routes.download(SimpleNamespace(match_info={'package_id':'../secret'}))
        self.assertEqual(response.status,400)


if __name__=='__main__':unittest.main()
