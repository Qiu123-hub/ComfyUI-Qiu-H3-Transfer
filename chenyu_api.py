"""Adapter for the published MiniMax H3 second pass on 晨羽智云."""
import hashlib
import http.client
import json
import re
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from zipfile import ZipFile

from .runninghub_api import RequestRejected, asset_form, read_handoff, save_video


HOST = "www.chenyu.cn"
BASE = "/api/open/v2"
WORKFLOW_ID = "wf_1169ddeaa4bd8726b2a1"
REVISION_ID = "wfr_8d402727f3232b923969"
DEFAULT_REFERENCE = Path(__file__).with_name("chenyu_default_reference.png")
IMAGE_INPUTS = {1: "node_215_image", 2: "node_218_image", 3: "node_221_image",
                4: "node_614_image", 5: "node_615_image", 6: "node_618_image"}
FAILED = {"failed", "error", "cancelled", "canceled", "rejected", "timed_out", "timeout"}


def detail(value, key):
    return value.replace(key, "[已隐藏]")[:250] if isinstance(value, str) else ""


def request(method, path, key, *, headers=None, body=None):
    connection = http.client.HTTPSConnection(HOST, timeout=90)
    try:
        connection.request(method, BASE + path, body=body,
                           headers={**(headers or {}), "Authorization": f"Bearer {key}"})
        with connection.getresponse() as response:
            status = response.status
            raw = response.read(4 * 1024 * 1024)
    finally:
        connection.close()
    if status in (401, 403):
        raise RequestRejected(f"晨羽智云返回 HTTP {status}，请检查 API Key 和工作流权限。")
    if status == 413:
        raise RequestRejected("晨羽智云拒绝了该文件大小（HTTP 413）。")
    if status < 200 or status >= 500:
        raise ValueError(f"晨羽智云返回 HTTP {status}。")
    try:
        payload = json.loads(raw)
    except ValueError as error:
        if 400 <= status < 500 and status != 408:
            raise RequestRejected(f"晨羽智云返回 HTTP {status}。") from error
        raise ValueError(f"晨羽智云返回了无法识别的数据（HTTP {status}）。") from error
    if not isinstance(payload, dict):
        raise ValueError("晨羽智云返回了无法识别的数据。")
    code = payload.get("code")
    if status == 408:
        raise ValueError("晨羽智云请求超时（HTTP 408）。")
    if not 200 <= status < 300 or code not in (0, "0"):
        message = detail(payload.get("msg"), key)
        code = code if isinstance(code, int) else status
        raise RequestRejected(f"晨羽智云拒绝请求（{code}）。{message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("晨羽智云未返回预期的响应数据。")
    return data


def valid_revision(value):
    if not isinstance(value, str) or not re.fullmatch(r"wfr_[A-Za-z0-9_-]{1,120}", value):
        raise ValueError("请填写晨羽智云工作流 API 页面上的版本 ID（wfr_ 开头）。")
    return value


def media_bindings(manifest):
    bindings = {"node_286_video": manifest["reference_video"]["asset"],
                "node_700_video": manifest["first_pass_video"]["asset"]}
    bindings.update((IMAGE_INPUTS[item["slot"]], item["asset"])
                    for item in manifest["reference_images"] if item["enabled"])
    return bindings


def upload_asset(key, archive, asset, kind):
    with asset_form(archive, asset, (("media_type", kind), ("purpose", "temp_input"))) as (headers, body):
        data = request("POST", "/assets", key, headers=headers, body=body)
    uri = data.get("asset_uri")
    if not isinstance(uri, str) or not re.fullmatch(r"asset://asset_[A-Za-z0-9_-]{1,128}", uri):
        raise ValueError("晨羽智云未返回有效的素材地址。")
    return uri


def run_handoff(key, path, revision, megapixels, report):
    with ZipFile(path) as archive:
        manifest, _ = read_handoff(archive)
        bindings = media_bindings(manifest)
        missing = [slot for slot, field in IMAGE_INPUTS.items() if field not in bindings]
        if missing and not DEFAULT_REFERENCE.is_file():
            raise ValueError("缺少内置默认参考图，请重新解压完整安装包。")
        assets = list(dict.fromkeys(bindings.values()))
        uris = {}
        for index, asset in enumerate(assets, 1):
            report(state="uploading", message=f"正在上传素材到晨羽智云 {index}/{len(assets)}…")
            uris[asset] = upload_asset(key, archive, asset, manifest["assets"][asset]["kind"])
    inputs = {name: uris[asset] for name, asset in bindings.items()}
    if missing:
        slots = "、".join(map(str, missing))
        report(state="uploading", message=f"正在上传一张默认图，补齐图槽 {slots}…")
        with ZipFile(BytesIO(), "w") as fallback:
            fallback.write(DEFAULT_REFERENCE, DEFAULT_REFERENCE.name)
            uri = upload_asset(key, fallback, DEFAULT_REFERENCE.name, "image")
        inputs.update((IMAGE_INPUTS[slot], uri) for slot in missing)
    inputs.update(node_247_value=megapixels, node_248_value=manifest["prompt"])
    identity = f"{WORKFLOW_ID}/{revision}/{path.stem}/{megapixels:.1f}"
    payload = {"workflow_id": WORKFLOW_ID, "revision_id": revision,
               "idempotency_key": hashlib.sha256(identity.encode()).hexdigest(),
               "accept_external_cost_risk": False, "inputs": inputs}
    report(state="submitting", message="正在提交晨羽智云二采…")
    data = request("POST", "/workflow/run/submit", key, headers={"Content-Type": "application/json"},
                   body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    order_id = data.get("run_order_id")
    if not isinstance(order_id, str) or not re.fullmatch(r"wfr[A-Za-z0-9_-]{1,125}", order_id):
        raise ValueError("晨羽智云未返回有效的运行订单 ID。")
    return order_id


def wait_for_video(key, order_id):
    deadline = monotonic() + 40 * 60
    query = urlencode({"run_order_id": order_id})
    while monotonic() < deadline:
        data = request("GET", f"/workflow/run/info?{query}", key)
        state = data.get("run_status", "")
        if state in FAILED:
            raise RequestRejected(f"晨羽智云二采失败。{detail(data.get('error_message'), key)}")
        if state == "succeeded":
            outputs = data.get("outputs") or {}
            videos = outputs.get("result_207_gifs_1") if isinstance(outputs, dict) else None
            url = videos[0] if isinstance(videos, list) and videos else None
            if isinstance(url, str) and urlsplit(url).scheme == "https" and urlsplit(url).hostname:
                return url
            raise ValueError("任务已完成，但未找到二采输出 result_207_gifs_1。")
        sleep(8)
    raise ValueError("本地查询已超时，可在晨羽智云继续查看运行订单。")


def download_video(key, order_id, output_dir):
    url = wait_for_video(key, order_id)
    source = Request(url)
    if urlsplit(url).netloc.lower() == "media.chenyu.cn":
        source.add_unredirected_header("Authorization", f"Bearer {key}")
    return save_video(source, f"ChenYu-{order_id}.mp4", output_dir, mp4=True)
