"""RunningHub task API for the local H3 sender."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import mimetypes
import os
from pathlib import Path
import shutil
from time import monotonic, sleep
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4
from zipfile import ZipFile

from .bridge_core import MAX_ASSETS, MAX_MANIFEST, MAX_BYTES, validate_manifest

HOST = "www.runninghub.ai"
IMAGE_IDS = [("215", "214"), ("218", "217"), ("221", "220"), ("614", "613"), ("615", "616"), ("618", "617")]
API_ERRORS = {
    301: "接口拒绝了请求参数",
    380: "RunningHub 找不到指定工作流",
    416: "RunningHub 账户余额不足",
    433: "RunningHub 工作流校验失败",
    801: "账号当前没有 API 调用权益",
    802: "API Key 未授权或已失效",
    805: "二采任务运行失败或已取消",
    808: "RunningHub 文件上传失败",
    809: "文件超过 RunningHub 的单文件上限",
    811: "企业版 API Key 无效",
    1002: "API Key 无效",
    1008: "文件超过 RunningHub 的单文件上限",
}


class RequestRejected(ValueError):
    pass


def request(path, headers, body, pending=()):
    connection = http.client.HTTPSConnection(HOST, timeout=90)
    try:
        connection.request("POST", path, body=body, headers=headers)
        with connection.getresponse() as response:
            if response.status == 413:
                raise RequestRejected("HTTP 413，RunningHub 拒绝了这个文件的大小。")
            if response.status in (401, 403):
                raise RequestRejected(f"HTTP {response.status}，请检查 API Key 及账号权限。")
            if response.status != 200:
                raise ValueError(f"RunningHub 返回 HTTP {response.status}。")
            payload = json.loads(response.read(4 * 1024 * 1024))
    finally:
        connection.close()
    if not isinstance(payload, dict):
        raise ValueError("RunningHub 返回了无法识别的数据。")
    code = payload.get("code")
    if code not in (0, 200, *pending):
        code = code if type(code) is int else "未知"
        reason = API_ERRORS.get(code, "RunningHub 拒绝了请求")
        error = RequestRejected if code in API_ERRORS else ValueError
        raise error(f"{reason}（业务码 {code}）。")
    return payload


def read_handoff(archive):
    members = archive.infolist()
    names = {member.filename for member in members}
    if len(names) != len(members) or len(members) > MAX_ASSETS + 1:
        raise ValueError("交接包内包含重复或过多文件。")
    if sum(member.file_size for member in members) > MAX_BYTES:
        raise ValueError("交接包解压后超过512MiB。")
    if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MAX_MANIFEST:
        raise ValueError("请选择一采结束后生成的 .h3.zip 交接包。")
    manifest = json.loads(archive.read("manifest.json"))
    validate_manifest(manifest)
    images = [image for image in manifest["reference_images"] if image["enabled"]]
    selected = dict.fromkeys([manifest["first_pass_video"]["asset"], manifest["reference_video"]["asset"],
                              *(image["asset"] for image in images)])
    for name in selected:
        info = manifest["assets"][name]
        if name not in names or archive.getinfo(name).file_size != info["size"] or not info["size"]:
            raise ValueError("交接包内的素材缺失或大小不符。")
        with archive.open(name) as asset:
            digest = hashlib.sha256()
            for block in iter(lambda: asset.read(1024 * 1024), b""):
                digest.update(block)
            if digest.hexdigest() != info["sha256"]:
                raise ValueError("交接包内的素材校验失败。")
    return manifest, selected


@contextmanager
def asset_form(archive, name, fields=()):
    boundary = uuid4().hex
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    prefix = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'
                     for field, value in fields)
    prefix = (prefix + f"--{boundary}\r\n"
              f'Content-Disposition: form-data; name="file"; filename="{Path(name).name}"\r\n'
              f"Content-Type: {content_type}\r\n\r\n").encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    size = archive.getinfo(name).file_size
    with archive.open(name) as asset:

        def parts():
            yield prefix
            yield from iter(lambda: asset.read(1024 * 1024), b"")
            yield suffix

        yield {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(prefix) + size + len(suffix)),
        }, parts()


def upload_asset(key, archive, name):
    with asset_form(archive, name) as (headers, body):
        data = request("/openapi/v2/media/upload/binary", {
            **headers, "Authorization": f"Bearer {key}",
        }, body).get("data")
    filename = (data.get("filename") or data.get("fileName")) if isinstance(data, dict) else None
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("上传接口没有返回可用于加载视频的文件名。")
    return filename


def apply_handoff(graph, manifest):
    source = manifest["reference_video"]
    video = graph["286"]["inputs"]
    video.update(source["loader"])
    graph["248"]["inputs"]["value"] = manifest["prompt"]
    common = manifest["common_settings"]
    graph["246"]["inputs"]["value"] = common["reference_short_edge"]
    graph["294"]["inputs"].update(common["reference_video_resize"])
    bindings = [(video, "video", source["asset"]),
                (graph["700"]["inputs"], "video", manifest["first_pass_video"]["asset"])]
    conditioning = graph["208"]["inputs"]
    for field in tuple(conditioning):
        if field.startswith("ref_images.ref_image_"):
            del conditioning[field]
    for index, (image, (load_id, resize_id)) in enumerate(zip(manifest["reference_images"], IMAGE_IDS)):
        if not image["enabled"]:
            graph.pop(load_id, None)
            graph.pop(resize_id, None)
            continue
        loader = graph.setdefault(load_id, {"class_type": "LoadImage", "inputs": {}})
        graph.setdefault(resize_id, {"class_type": "ResizeImagesByShorterEdge", "inputs": {
            "shorter_edge": ["246", 0], "images": [load_id, 0],
        }})
        conditioning[f"ref_images.ref_image_{index}"] = [resize_id, 0]
        bindings.append((loader["inputs"], "image", image["asset"]))
    return bindings


def workflow_request(path, key, pending=(), **params):
    body = json.dumps({"apiKey": key, **params}, ensure_ascii=False).encode("utf-8")
    return request(path, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, body, pending)


def submit_task(key, graph, workflow_id, instance_type):
    data = workflow_request("/task/openapi/create", key, workflowId=workflow_id,
                            workflow=json.dumps(graph, ensure_ascii=False), instanceType=instance_type).get("data")
    task_id = data.get("taskId") if isinstance(data, dict) else None
    if not isinstance(task_id, str) or not task_id.isascii() or not task_id.isdecimal():
        raise ValueError("平台未返回任务 ID，请先检查 RunningHub 任务记录。")
    return task_id


def wait_for_video(key, task_id):
    deadline = monotonic() + 40 * 60
    while monotonic() < deadline:
        result = workflow_request("/task/openapi/outputs", key, (804, 813), taskId=task_id)
        if result["code"] in (804, 813):
            sleep(8)
            continue
        files = result.get("data")
        if isinstance(files, list):
            for entry in files:
                url = entry.get("fileUrl") if isinstance(entry, dict) else None
                if (isinstance(url, str) and urlsplit(url).scheme == "https"
                        and urlsplit(url).hostname and str(entry.get("nodeId")) == "207"
                        and str(entry.get("fileType")).lower() in {"mp4", "mov", "webm", "mkv"}):
                    return url, entry["fileType"].lower()
        raise ValueError("平台返回的结果中没有找到二采视频，请查看任务记录。")
    raise ValueError("本地查询已超时，可在 RunningHub 继续查看任务。")


def download_video(key, task_id, output_dir):
    url, extension = wait_for_video(key, task_id)
    return save_video(url, f"RunningHub-{task_id}.{extension}", output_dir)


def save_video(url, filename, output_dir, *, mp4=False):
    target = output_dir / filename
    output_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(f".{uuid4().hex}.part")
    try:
        with urlopen(url, timeout=90) as response, partial.open("xb") as destination:
            if mp4:
                header = response.read(12)
                if header[4:8] != b"ftyp":
                    raise ValueError("平台返回的文件无法识别为 MP4。")
                destination.write(header)
            shutil.copyfileobj(response, destination, 1024 * 1024)
        if not partial.stat().st_size:
            raise ValueError("平台返回的成片文件为空。")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return filename


def run_handoff(key, path, profile, instance_type, megapixels, report):
    graph = profile["workflow"]
    graph["247"]["inputs"]["value"] = megapixels
    with ZipFile(path) as archive:
        manifest, assets = read_handoff(archive)
        bindings = apply_handoff(graph, manifest)
        filenames = {}
        for index, asset in enumerate(assets, 1):
            report(state="uploading", message=f"正在上传素材 {index}/{len(assets)}…")
            filenames[asset] = upload_asset(key, archive, asset)
    for inputs, field, asset in bindings:
        inputs[field] = filenames[asset]
    report(state="submitting", message="正在提交云端二采…")
    return submit_task(key, graph, profile["workflowId"], instance_type)


