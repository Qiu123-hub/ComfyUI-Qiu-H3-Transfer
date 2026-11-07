# ComfyUI-Qiu-H3-Transfer

## 本地一键提交 RunningHub 二采

在本机用 `localhost` 或 `127.0.0.1` 打开 ComfyUI。原发送节点会显示「二采设置」和「发送并运行二采」。第一次打开「二采设置」，粘贴 RunningHub API Key，并选择平时使用的云端显存档位。仓库和 v0.1.5 安装包已包含配套的 `runninghub_profile.json`，对应 RunningHub 工作流 `2117626984829288449`。

一采打包后，在发送节点选择「二采分辨率（百万像素）」，默认 1.5，可在 1.0 到 2.0 之间按 0.1 选择。点击「发送并运行二采」会提交云端任务，成片保存到本机 `output/H3_stage2`。同一交接包和分辨率会继续查询已有任务或打开已有成片。选择另一个分辨率会提交一笔新任务。

**本地一采 → 云端二采，一次交接输入、素材和一采成品。**

适用于 MiniMax H3 / 海螺 H3 的两阶段视频工作流。发送节点在一采视频保存后打包本次输入，支持直接提交 RunningHub 二采，也支持通过接收节点导入交接包。当前版本为 0.1.5。

## 安装

在 `ComfyUI/custom_nodes/` 中执行：

```sh
git clone https://github.com/Qiu123-hub/ComfyUI-Qiu-H3-Transfer.git
```

也可以下载 [v0.1.5 安装包](dist/ComfyUI-Qiu-H3-Transfer-v0.1.5.zip)，解压到 `ComfyUI/custom_nodes/ComfyUI-Qiu-H3-Transfer/`，确保 `__init__.py` 位于该目录，重启 ComfyUI 并刷新网页。本地 API 提交方式只需更新本地发送端，使用已经配置好的云端二采工作流。

从旧名 `ComfyUI-H3-Transfer` 升级时，先将旧插件目录移到 `custom_nodes` 外，保证只加载一份。节点类名保持兼容，已有工作流仍可使用。

Python 3.10+；运行依赖为 ComfyUI 自带的 `aiohttp`，没有额外模型。如果独立安装依赖，请在 ComfyUI 使用的 Python 环境中执行：

```sh
python -m pip install -r requirements.txt
```

安装后可搜索两个节点：

| 节点 | 用途 |
| --- | --- |
| `H3TransferSend` / Qiu H3 发送交接包（本地一采） | 连接 VHS VideoCombine 的 Filenames；保存完成后自动打包 |
| `H3TransferReceive` / Qiu H3 接收交接包（云端二采） | 粘贴交接码或导入 ZIP，将素材及参数填入当前工作流 |

## 配套工作流

- [本地一采与发送节点](examples/stage1_local_sender.json)：本地只保留 #271 一个视频输出，预览、续跑和交接共用。
- [云端二采与接收节点](examples/stage2_cloud_receiver.json)：读取一采成品，末尾补帧在云端 #702 自动完成。

示例已清空素材选择和个人提示词，首段续跑开关默认关闭。使用前选择自己的模型、原参考视频、参考图并填写提示词。模型权重与示例涉及的其他自定义节点需要另外安装；本插件不会提供或下载它们。参考图开关在操作区，下一段续跑时打开 #593 并在 #592 选择上一段成品。

## 使用

1. 导入本地示例，选择原参考视频、启用的参考图及提示词，正常运行一采。
2. #271 保存视频后，发送节点自动生成 `output/h3_transfer/<随机ID>.h3.zip`，并登记到本次任务的输出中。
3. 点击发送节点的“复制本次交接码（含素材）”。大文件点击“下载交接ZIP”。
4. 在接收端导入二采示例，点击“粘贴并填写二采输入”或“导入交接ZIP”。
5. 检查二采分辨率和采样设置，正常运行二采。

接收不会自动排队运行。二采目标分辨率、sigma 与精修参数保留；参考图槽位及开关、提示词、原参考视频、参考图短边和视频缩放限制会同步。

一采保持 **24fps 和音轨**，不再次裁掉续跑引导的22帧；配套本地输出已经去重。#901 为输出帧率，已明确连到视频保存节点。不要仅修改帧率数值，H3 的时长与音视频对齐也依赖24fps。

## 包内内容与文件大小

交接包保存实际提示词、原参考视频及读取参数、开启的参考图、六槽位开关、一采成品（含其封装音轨）和公共尺寸设置。还可用 `include_previous` 保存续跑的上一段视频。记录一采模型和采样参数供回看，但不覆盖二采采样参数。

素材文件本体包含在包中；导入后位于接收端 `input/h3_transfer/imports/<随机ID>/`，不依赖原机器路径。关闭的参考图只记录关闭状态。交接包不包含模型权重。

| 方式 | 限制 |
| --- | --- |
| 复制 / 粘贴完整交接码 | ZIP 不超过 128 MiB，Base64 文本约增大三分之一 |
| 下载 / 导入交接ZIP | ZIP 不超过 512 MiB，实际上传还受平台限制 |

浏览器无法访问剪贴板时会提供手动文本框。生成结束后改变素材或提示词不会改写已有交接包，应使用与新输入匹配的一采输出重新生成。

## RunningHub

更新发送端后重新生成交接包，ZIP 会进入本次任务结果。按钮会尝试交接接口、ComfyUI 输出路径和任务产物链接。下载失败时也可以从任务结果获取 ZIP。任务产物的登记方式参考 [RunningHub RVC 节点](https://github.com/RH-RunningHub/ComfyUI_RH_RVC/blob/main/rvc_comfy_nodes.py)。

RunningHub 的示例接收节点通过平台素材入口上传原包中启用的参考图、原参考视频和一采成品。素材校验和目标控件检查在上传前完成，全部所需素材上传后统一填写工作流。请使用发送节点生成的原始 ZIP 或交接码。标准上传入口的使用方式参照 [RunningHub 自家节点的上传实现](https://github.com/RH-RunningHub/ComfyUI_RH_RVC/blob/main/js/rh_rvc_zip_upload.js)。

在 RunningHub 通过按钮填写画布时，`import_id` 留空。需要在图中连接 `import_status` 的工作流继续使用服务器交接接口。

点击「发送并运行二采」时，素材通过 RunningHub 官方 API 上传并提交任务。使用接收节点时，素材在用户主动导入后上传到当前接收端 ComfyUI。

## 节点映射

默认映射面向上述配套 H3 工作流，其他工作流需要在 `mapping_json` 指定对应节点ID。插件不会仅凭节点标题推断目标。

```json
{
  "prompt": 248,
  "reference_video": 286,
  "handoff_video": 700,
  "images": [215, 218, 221, 614, 615, 618],
  "image_resizes": [214, 217, 220, 613, 616, 617],
  "reference_short_edge": 246,
  "reference_video_resize": 294
}
```

发送端实际输出文件取自相连的 `VHS_FILENAMES`；示例发送节点已接 #271，并在映射中声明该输出。另用 #592 与 #588 识别续跑。接收时先检查全部目标节点与控件，再填写；控件回调失败时回滚。

## 验证与开发

```sh
python -m unittest discover -s verification -p test_bridge.py
node verification/test_frontend.mjs
```

13 项 Python 测试覆盖归档校验、素材去重、续跑记录和真实回环 HTTP 接口。用 40.2 MiB 的合成交接包检查过下载与导入。前端测试覆盖交接码往返、下载回退、使用 Python 发送节点原包模拟 RunningHub 接收，以及损坏素材和错误映射的上传前校验。

配套工作流做过 ComfyUI 前端导入检查，VHS 小样已生成 24fps 视频。完整交接包通过官方 API 提交 RunningHub 二采已由用户实测出片。v0.1.5 节点按钮完成了素材传递、分辨率、任务复用及本地保存的模拟验证，节点按钮的实机验收待完成。

## 许可

插件代码采用 [MIT License](LICENSE)。第三方 ComfyUI 节点和模型遵循各自的许可；示例JSON不包含第三方节点实现或模型文件。
