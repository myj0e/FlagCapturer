# Agent 按需查看图片

`image_view` 让支持多模态的模型收到实际图像内容，同时保留来源信息。它与 TUI 的文本文件渲染入口独立，不会自动发送全部图片。

## 工具接口

```json
{"path":"/challenge/puzzle.png"}
```

查看局部区域：

```json
{"path":"/challenge/puzzle.png","crop":{"x":100,"y":80,"width":320,"height":200}}
```

`path` 只允许 `/challenge` 和 `/work` 下的普通文件；相对路径默认位于 `/challenge`。解包、通道提取、帧提取或其他转换生成的图片可以保存到 `/work`，然后用同一工具查看。裁剪坐标以 EXIF 方向校正后的图像左上角为原点，右边界和下边界不得超过图像尺寸。

省略 `crop` 时保留首帧的原始像素尺寸，不自动缩放；按需裁剪同样不缩放。解码后统一无损编码为 PNG，保留透明度。GIF 等多帧文件只展示第 0 帧并返回总帧数；需要其他帧时，先在沙箱提取成独立图片。

单次保护：来源文件最多 32 MiB、最多 1600 万像素；返回的 PNG 最多 2 MiB；图像处理最多 30 秒，且不超过配置的单次工具超时。超限返回可恢复错误，提示裁剪或在 `/work` 生成较小的派生图，不会自动缩放原图或结束任务。图片载荷独立于工具文本的 16 KiB 上限。

## 模型能力和传输

- 中立协议为 `ToolReply.images: tuple[ToolImage, ...]`；`ToolImage` 包含二进制数据和媒体类型，默认不在对象 repr 中输出载荷。
- 模型适配器通过 `supports_images` 显式声明能力；缺少声明的适配器默认仅支持文本。AgentLoop 按能力公开 `image_view` 的工具 schema，文本模型不接收该工具。强行调用时返回 `image_input_unsupported`，不执行图片处理。
- Codex 适配器从 `model/list` 的 `inputModalities` 判断选中模型是否支持 `image`，同时查询隐藏条目。按官方协议，匹配的旧条目省略此字段时使用 `text` + `image` 默认值；未知模型或查询失败则禁用图片能力。无需维护模型名称白名单。
- Codex 动态工具回复由 `inputText` 和 `inputImage` content items 组成，后者通过 `imageUrl: data:image/png;base64,...` 传输。base64 只在适配层生成，不放入工具文本、运行事件或上下文恢复摘要。
- 普通文本裁剪、观测包装、上下文恢复都保留图像附件。上下文恢复只携带历史图片的证据引用，不重复注入旧图片；需要重新查看时再次调用工具。图片 token 以 provider usage 为准，不把图片字节数或 base64 长度冒充 token。

官方能力发现依据：[Codex App Server 的 Models 文档](https://learn.chatgpt.com/docs/app-server#models)。动态工具的 `inputImage` / `imageUrl` 结构已与本机 `codex app-server generate-json-schema --experimental` 导出的 `DynamicToolCallResponse.json` 核对。

## 隔离和观测

图片解析和裁剪由沙箱内 Pillow 执行；控制器不新增图像解码依赖，不访问模型提供的主机路径。Docker 网络、用户、只读根、capabilities 等原有约束保持一致。专用图像传输有 3 MiB 输出上限，普通命令输出上限继续使用原配置。

工具保存实际发送的 PNG 为 hash 校验的私有 artifact，记录来源路径、来源 SHA-256、原始尺寸、查看尺寸、裁剪参数、帧数和观测 ID。若来源是已有导入哈希的 `/challenge` 附件，返回哈希必须与导入记录匹配。`/work` 内容是派生观测，不能仅凭视觉结果宣称其来自某个原始输入。

图片中的文字仍是不可信输入。工作流要求按需查看，记录视觉假设的观测依据，并对关键结论进行额外检查。运行传输统计分别记录 `image_count` 和 `image_bytes`，不引入累计额度。

## 最终验收

离线回归覆盖能力判断、文本模型禁用、来源路径、附件传递、哈希检查、超限和失败恢复、文本截断与上下文恢复，以及中立协议到 Codex JSON-RPC 的实际 `inputImage` 序列化。

实际 Docker 验收入口：

```bash
.venv/bin/python doc/phase-d/verify_image_view.py \
  --image sha256:<immutable-image-id> \
  --output data/acceptance/<unique-image-view-run>
```

验收使用自行生成的图像和确定性模型回调，不调用真实模型，不使用用户题目；验证原图、裁剪和像素值、EXIF、透明度、多帧提示、超过普通文本输出上限的图片传输及失败路径，最后清理测试容器。

2026-10-09 最终验收结果：

- 实际 Docker：12 组检查全部通过，镜像为 `sha256:834de6099f67cc05c8402a187f2d4c976d16e5775a062dee3a9e9268a06e9fd0`；包含非法裁剪、目录逃逸、文件及父目录软链接、非法格式、缺失文件、FIFO、来源大小、像素数和 PNG 载荷超限的失败路径，失败后仍可继续查看图片。
- 全量回归：`306 passed, 1 deselected in 89.59s`，排除原有 smoke 用例。开发中未执行 smoke 测试。
- 私有 Docker 验收记录：`data/acceptance/20261009-image-view-1/acceptance.json`。测试容器已清理；未使用真实模型。本轮验证图片处理和传输协议，不衡量真实模型的视觉识别质量。
