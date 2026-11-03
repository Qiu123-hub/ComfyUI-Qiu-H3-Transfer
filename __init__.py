from .nodes import H3TransferSend,H3TransferReceive
from . import routes  # register only within the ComfyUI server

NODE_CLASS_MAPPINGS={'H3TransferSend':H3TransferSend,'H3TransferReceive':H3TransferReceive}
NODE_DISPLAY_NAME_MAPPINGS={'H3TransferSend':'Qiu H3 发送交接包（本地一采）',
                          'H3TransferReceive':'Qiu H3 接收交接包（云端二采）'}
WEB_DIRECTORY='./web'
__all__=['NODE_CLASS_MAPPINGS','NODE_DISPLAY_NAME_MAPPINGS','WEB_DIRECTORY']
