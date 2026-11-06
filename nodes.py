import json
from pathlib import Path
import folder_paths
from .bridge_core import create_package,inside,profile,valid_id


def package_dir():return Path(folder_paths.get_output_directory())/'h3_transfer'


class H3TransferSend:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'filenames':('VHS_FILENAMES',),
                            'include_previous':('BOOLEAN',{'default':True}),
                            'mapping_json':('STRING',{'default':'{}','multiline':True})},
                'hidden':{'extra_pnginfo':'EXTRA_PNGINFO','prompt':'PROMPT'}}
    RETURN_TYPES=('STRING',)
    RETURN_NAMES=('package_id',)
    FUNCTION='send'
    CATEGORY='H3/Transfer'
    OUTPUT_NODE=True

    @classmethod
    def IS_CHANGED(cls,**kwargs):return float('nan')

    def send(self,filenames,include_previous=True,mapping_json='{}',extra_pnginfo=None,prompt=None):
        conf=profile(mapping_json)
        workflow=(extra_pnginfo or {}).get('workflow')
        input_root=Path(folder_paths.get_input_directory()).resolve()
        def resolve_input(name):
            if not isinstance(name,str):raise ValueError('素材文件名必须是文本')
            return inside(folder_paths.get_annotated_filepath(name),[input_root])
        result=create_package(workflow,prompt,filenames,resolve_input,
            [folder_paths.get_output_directory(),folder_paths.get_temp_directory()],
            package_dir(),conf,include_previous)
        public={k:v for k,v in result.items() if k!='path'}
        artifact = {'filename': Path(result['path']).name, 'subfolder': 'h3_transfer', 'type': 'output'}
        # RunningHub's output processor collects files from the standard images list.
        return {'ui': {'h3_transfer': [public], 'images': [artifact]}, 'result': (result['package_id'],)}


class H3TransferReceive:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'import_id':('STRING',{'default':''}),
                            'mapping_json':('STRING',{'default':'{}','multiline':True})}}
    RETURN_TYPES=('STRING',)
    RETURN_NAMES=('import_status',)
    FUNCTION='receive'
    CATEGORY='H3/Transfer'

    def receive(self,import_id,mapping_json='{}'):
        profile(mapping_json)
        token=valid_id(import_id)
        path=Path(folder_paths.get_input_directory())/'h3_transfer'/'imports'/token/'receipt.json'
        if not path.is_file():raise ValueError('交接素材不存在，请先在接收节点粘贴交接码或导入ZIP')
        receipt=json.loads(path.read_text(encoding='utf-8'))
        return (f'已接收：{sum(i["enabled"] for i in receipt["reference_images"])}张参考图、参考视频、一采视频及提示词',)
