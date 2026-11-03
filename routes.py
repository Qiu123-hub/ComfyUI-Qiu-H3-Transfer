"""Same-instance upload/download endpoints; no external service or credentials."""
import asyncio
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from aiohttp import web
import folder_paths
from server import PromptServer
from .bridge_core import import_package,valid_id,MAX_BYTES
from .nodes import package_dir


def same_origin(request):
    origin=request.headers.get('Origin')
    if origin:
        expected=request.headers.get('X-Forwarded-Host',request.host).split(',')[0].strip()
        if urlparse(origin).netloc!=expected:raise web.HTTPForbidden(text='只允许当前 ComfyUI 页面导入')


@PromptServer.instance.routes.get('/h3_transfer/package/{package_id}')
async def download(request):
    try:token=valid_id(request.match_info['package_id'])
    except ValueError as exc:return web.json_response({'error':str(exc)},status=400)
    path=package_dir()/(token+'.h3.zip')
    if not path.is_file():return web.json_response({'error':'交接包已不存在，请重新运行发送节点'},status=404)
    return web.FileResponse(path,headers={'Content-Disposition':f'attachment; filename="h3-transfer-{token}.h3.zip"',
                                         'Cache-Control':'no-store'})


@PromptServer.instance.routes.post('/h3_transfer/import')
async def upload(request):
    same_origin(request)
    base=Path(folder_paths.get_temp_directory())/'h3_transfer_upload'
    base.mkdir(parents=True,exist_ok=True)
    handle=tempfile.NamedTemporaryFile(dir=base,suffix='.h3.zip',delete=False)
    path=Path(handle.name)
    try:
        total=0
        with handle:
            async for chunk in request.content.iter_chunked(1024*1024):
                total+=len(chunk)
                if total>MAX_BYTES:raise ValueError('交接包超过512MiB')
                handle.write(chunk)
        receipt=await asyncio.to_thread(import_package,path,folder_paths.get_input_directory())
        return web.json_response(receipt,headers={'Cache-Control':'no-store'})
    except (ValueError,OSError,KeyError,TypeError) as exc:
        return web.json_response({'error':str(exc)},status=400)
    except Exception:
        return web.json_response({'error':'交接包读取失败；请使用完整ZIP或重新复制交接码'},status=400)
    finally:
        if path.exists():path.unlink()
