"""Local-only UI endpoints and a receipt for each submitted handoff."""
import asyncio
import base64
import ctypes
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import BadZipFile

import folder_paths
from aiohttp import web
from server import PromptServer

from . import chenyu_api
from .bridge_core import valid_id
from .nodes import package_dir
from .runninghub_api import RequestRejected, download_video, run_handoff


PROFILE = Path(__file__).with_name("runninghub_profile.json")
SETTINGS = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / ".qiu-h3-transfer" / "runninghub.json"
PLATFORM_ROUTE = "/h3_transfer/{platform:runninghub|chenyu}"
PLATFORM_NAMES = {"runninghub": "RunningHub", "chenyu": "晨羽智云"}
SUBFOLDER = "H3_stage2"
INSTANCES = ("default", "plus", "ultra")
MEGAPIXELS = tuple(index / 10 for index in range(10, 21))
LOOPBACK = ("localhost", "127.0.0.1", "::1")
active = {}
routes = PromptServer.instance.routes


def local_only(request):
    origin = request.headers.get("Origin")
    host = urlsplit(f"//{request.host}").hostname
    if host not in LOOPBACK or request.remote not in LOOPBACK or (origin and urlsplit(origin).netloc != request.host):
        raise web.HTTPForbidden(text="请从本机 localhost 或 127.0.0.1 打开 ComfyUI。")


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.part")
    try:
        with os.fdopen(os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def windows_dpapi(data, encrypt):
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("无法访问 Windows 本机密钥，请重新配置 API Key。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        free(output.data)


def save_key(key):
    return (base64.b64encode(windows_dpapi(key.encode(), True)).decode() if os.name == "nt" else key)


def api_key(settings):
    saved = settings.get("key")
    if not saved:
        return ""
    return windows_dpapi(base64.b64decode(saved), False).decode() if os.name == "nt" else saved


def settings_path(platform):
    return SETTINGS if platform == "runninghub" else SETTINGS.with_name("chenyu.json")


def chenyu_revision(settings):
    if settings.get("workflowId") == chenyu_api.WORKFLOW_ID:
        return settings.get("revisionId", chenyu_api.REVISION_ID)
    return chenyu_api.REVISION_ID


def public_settings(settings, platform):
    result = {"configured": bool(settings.get("key"))}
    if platform == "runninghub":
        result["instanceType"] = settings.get("instanceType", "default")
    else:
        result.update(workflowId=chenyu_api.WORKFLOW_ID, revisionId=chenyu_revision(settings))
    return result


def response(value, status=200):
    return web.json_response(value, status=status, headers={"Cache-Control": "no-store"})


@routes.get(PLATFORM_ROUTE + "/settings")
async def get_settings(request):
    local_only(request)
    platform = request.match_info["platform"]
    return response(public_settings(read_json(settings_path(platform), {}), platform))


@routes.post(PLATFORM_ROUTE + "/settings")
async def update_settings(request):
    local_only(request)
    platform = request.match_info["platform"]
    path = settings_path(platform)
    try:
        data = await request.json()
        key = data.get("apiKey", "").strip()
        settings = read_json(path, {})
        if platform == "runninghub":
            instance = data["instanceType"]
            if instance not in INSTANCES:
                raise ValueError("请选择有效的显存档位。")
            settings["instanceType"] = instance
        else:
            revision = chenyu_api.valid_revision(data.get("revisionId", "").strip())
            settings.update(workflowId=chenyu_api.WORKFLOW_ID, revisionId=revision)
        if key:
            if len(key) > 256 or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
                raise ValueError(f"请输入有效的 {PLATFORM_NAMES[platform]} API Key。")
            settings["key"] = save_key(key)
        if not settings.get("key"):
            raise ValueError(f"请先粘贴 {PLATFORM_NAMES[platform]} API Key。")
        save_json(path, settings)
        return response(public_settings(settings, platform))
    except ValueError as error:
        return response({"error": str(error)}, 400)
    except (OSError, AttributeError, KeyError, TypeError):
        return response({"error": "无法保存本机配置。"}, 400)


def job_identity(token, megapixels, platform, revision=None):
    if type(megapixels) not in (int, float) or megapixels not in MEGAPIXELS:
        raise ValueError("二采分辨率需在 1.0 到 2.0 百万像素之间，间隔 0.1。")
    revision = chenyu_api.valid_revision(revision) if platform == "chenyu" else None
    return valid_id(token), f"{megapixels:.1f}", platform, revision


def receipt_path(identity):
    token, resolution, platform, revision = identity
    version = f".{revision}" if revision else ""
    return package_dir() / f"{token}.{resolution}.{platform}{version}.json"


def read_job(identity):
    job = read_json(receipt_path(identity), {"state": "idle"})
    if identity not in active:
        if job["state"] == "submitting":
            job.update(state="unknown", message=f"上次提交未确认，请先查看 {PLATFORM_NAMES[identity[2]]} 的任务记录。")
        elif job["state"] in ("preparing", "uploading", "running"):
            job.update(state="paused", message="本机处理已暂停。再点一次发送即可继续。")
    return job


def execute(path, receipt, key, profile, instance_type, megapixels, job, platform):
    platform_name = PLATFORM_NAMES[platform]
    def report(**fields):
        job.update(fields)
        save_json(receipt, job)

    try:
        task_id = job.get("taskId")
        if not task_id:
            if platform == "chenyu":
                task_id = chenyu_api.run_handoff(key, path, job["revisionId"], megapixels, report)
            else:
                task_id = run_handoff(key, path, profile, instance_type, megapixels, report)
        report(state="running", taskId=task_id, message="二采已提交，正在等待成片…")
        download = chenyu_api.download_video if platform == "chenyu" else download_video
        filename = download(key, task_id, Path(folder_paths.get_output_directory()) / SUBFOLDER)
        report(state="done", filename=filename, subfolder=SUBFOLDER, message="二采成片已保存到本地。")
    except RequestRejected as error:
        report(state="error", message=f"二采交接失败。{error}")
    except (ValueError, BadZipFile, OSError) as error:
        if job["state"] == "submitting":
            report(state="unknown", message=f"提交未确认。{error} 请先查看 {platform_name} 的任务记录。")
        else:
            report(state="error", message=f"二采交接失败。{error}")
    except Exception:
        if job["state"] == "submitting":
            report(state="unknown", message=f"提交未确认，请先查看 {platform_name} 的任务记录。")
        else:
            report(state="error", message="二采交接中断，请检查本地连接和任务记录。")


@routes.get(PLATFORM_ROUTE + "/job/{package_id}")
async def get_job(request):
    local_only(request)
    try:
        platform = request.match_info["platform"]
        identity = job_identity(request.match_info["package_id"], float(request.query.get("megapixels", "")),
                                platform, request.query.get("revisionId"))
        return response(read_job(identity))
    except (ValueError, OSError):
        return response({"error": "无法读取二采任务状态。"}, 400)


@routes.post(PLATFORM_ROUTE + "/job/{package_id}")
async def start_job(request):
    local_only(request)
    platform = request.match_info["platform"]
    try:
        data = await request.json()
        megapixels = data["megapixels"]
        settings = read_json(settings_path(platform), {})
        revision = chenyu_revision(settings) if platform == "chenyu" else None
        identity = job_identity(request.match_info["package_id"], megapixels, platform, revision)
        receipt = receipt_path(identity)
        job = read_job(identity)
        if identity in active or job["state"] in ("done", "unknown"):
            return response(job)
        key = api_key(settings)
        if not key:
            raise ValueError("请先点击「二采设置」粘贴 API Key。")
        profile = None
        if platform == "runninghub":
            profile = read_json(PROFILE, None)
            if not profile:
                raise ValueError("本地插件缺少二采 API 模板，请安装此工作流对应的本地包。")
        path = package_dir() / f"{identity[0]}.h3.zip"
        if not job.get("taskId") and not path.is_file():
            raise ValueError("找不到本次交接包，请先运行一采。")
        job.update(state="preparing", message="正在准备本次一采素材…")
        if revision:
            job["revisionId"] = revision
        save_json(receipt, job)
        task = asyncio.create_task(asyncio.to_thread(execute, path, receipt, key, profile,
                                                    settings.get("instanceType", "default"), megapixels, job, platform))
        active[identity] = task
        task.add_done_callback(lambda _: active.pop(identity, None))
        return response(job)
    except (ValueError, OSError, AttributeError, KeyError, TypeError) as error:
        return response({"error": str(error)}, 400)
