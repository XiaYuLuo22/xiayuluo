# 夏鱼洛视觉动作学习原型

这是借鉴 Neuro「训练专门的动作控制器」思路、自行编写的实验，不是 Neuro 原版复现，也没有复制其代码、下载其权重或改用另一个桌面代理框架。

本阶段验证一件事：**同一张画面，给出不同任务目标，训练后的模型能否预测相应目标的单步鼠标动作。** 模型输入是图像像素和目标编码，不通过聊天模型临时猜坐标。生成器知道的对象位置只用于训练答案和评测，不得作为预测输入。

## 当前范围：合成基线与本人示范分开

- 独立实验模块 `experiments/visual_mouse`，尚未接入夏鱼洛 App 的真实鼠标执行器。
- 原 `train/evaluate/predict` 路线使用自行生成的 128×96 RGB 合成界面：红、绿、蓝、黄四种颜色，圆形、方形、三角形三种形状；任务是点击或移动到目标，目标不存在时应不操作。中文/英文目标只支持有限模板，例如「点击红色圆形」。
- 新 `import-demo/train-demo/evaluate-demo/predict-demo` 路线读取本人明确提供的 PNG、精确注册任务和动作标签，不再把真实示范冒标为合成数据；任务文本以有限注册 ID 编码，不理解任意中文同义表达，不表示已经学会任何 Windows 软件。
- 第一阶段是单步定位与动作学习，不代表已经学会鼠标轨迹、拖动、连续任务或高帧率控制。即使后续加入时序网络，也不能仅凭网络名称宣称已完成轨迹学习。
- 训练、评测和预测均不捕获真实桌面、不监听鼠标或键盘、不点击真实鼠标、不上传训练数据。已有 App 的鼠标、主动建议、语音和手机权限不因此改变。

0.1.17另增加限时的本机影子采样/分析运行时，见 [CONTROL_RUNTIME.md](CONTROL_RUNTIME.md)。它不执行鼠标、不改变本实验的安全标志。`OfflineShadowPolicy`适配器可在独立源码训练环境将既有示范模型用于RAM画面的影子预测；普通Windows安装包不包含PyTorch/实验训练栈，也没有模型自动安装/训练入口。不能把采样帧率当成已训练模型的实时控制能力。

## 准备 CPU 训练环境

推荐在项目和日常 App 环境之外建立独立虚拟环境。以下是 Windows PowerShell 示例，在仓库根目录运行；路径可自行选择：

```powershell
py -3.12 -m venv E:\XiayuluoVisionEnv
E:\XiayuluoVisionEnv\Scripts\python -m pip install -c requirements-vision.lock setuptools
E:\XiayuluoVisionEnv\Scripts\python -m pip install -c requirements-vision.lock --index-url https://download.pytorch.org/whl/cpu "torch==2.14.1+cpu"
E:\XiayuluoVisionEnv\Scripts\python -m pip install -c requirements-vision.lock "numpy==2.4.2" "Pillow==12.3.0"
```

PyTorch 从官方 CPU wheel 源获取，不下载 CUDA 工具链或预训练模型权重；NumPy 和 Pillow 是图像、张量及安全文件读写依赖。安装依赖需要联网，后续合成数据训练本身不需要模型 API 密钥。Linux 可使用相同依赖及训练命令，把 Python 路径换成对应虚拟环境的 `bin/python`。

先从PyPI准备锁定的setuptools，再安装CPU Torch；PyTorch专用索引并不是完整的PyPI镜像，不能要求它提供全部构建工具版本。

## 合成基线训练和评测

0.1.13 起，新训练产物保存到安装目录外的固定训练根，默认是 Windows `%LOCALAPPDATA%\Xiayuluo\training`，而不是安装到 E 盘就自动存在 E 盘。首次可以用 `ASSISTANT_TRAINING_DIR` 或 `--data-dir` 指定独立绝对目录；登记后配置冲突、原盘消失或格式损坏时停止操作，不自动切换为空目录。完整的保存、更新前备份与恢复说明见 [TRAINING_DATA.md](TRAINING_DATA.md)。

在已安装上述依赖的环境中，从仓库根目录运行。以下示例是首次明确选择 E 盘独立训练根，输出位于该根的 `models/` 下：

```text
python -m experiments.visual_mouse train --data-dir E:/XiayuluoData/training --output E:/XiayuluoData/training/models/demo-001 --train-samples 4096 --eval-samples 512 --epochs 20 --seed 42
python -m experiments.visual_mouse evaluate --model E:/XiayuluoData/training/models/demo-001 --samples 1024 --seed 314159 --report E:/XiayuluoData/training/models/demo-001/test-report.json --require-learning
python -m experiments.visual_mouse preview --model E:/XiayuluoData/training/models/demo-001 --output E:/XiayuluoData/training/models/demo-001/preview.png
```

也可以不传位置参数，使用已登记位置或首次默认位置，并让 CLI 在 `models/` 下选择新输出目录、显示实际路径：

```text
python -m experiments.visual_mouse train --train-samples 4096 --eval-samples 512 --epochs 20 --seed 42
```

输出目录必须是新目录，不覆盖已有模型。根目录下 `datasets/`、`models/`、`checkpoints/`、`backups/` 分开管理；预留检查点目录不代表已实现断点续训。旧 `dist/visual-learning/`、`.vision-learning/` 文件不会自动迁移或删除，旧模型仍能按原路径评测和预测，但不自动纳入新根的备份。先保留旧文件，不要把移动目录或重训当作更新的一部分。

训练样本和评测样本应使用互不重叠的种子范围；不能把训练集命中率当成泛化能力。还应检查「同图换目标」的预测是否随目标改变，并与未训练模型对照，避免模型只记住常见位置或忽略任务目标。

`--require-learning`启用合成回归门槛：至少256张测试，目标存在成功率≥90%、建议精确率≥95%、缺失目标放弃率≥90%、至少32对同图换目标双成功率≥80%，错目标对原任务的成功率≤20%。未通过则返回失败而不假报学习完成；这些门槛不是允许控制真实桌面的凭据。

所有成功率、误操作率和耗时必须来自实际运行报告，不能把合成界面的指标当成真实桌面或用户电脑验收。

本次模型为自行实现的小型CNN、颜色/形状/动作目标嵌入和空间位置/动作分类头，没有采用LSTM，也没有复制Neuro代码。这样能先隔离验证“像素＋目标→单步动作”是否真的学到；不重复静态帧来冒充时序训练。合成对象使用带随机扰动的3×2网格、随机颜色和尺寸，测试种子未见过不等于见过任意新布局或真实软件。

### 实际实验记录

CPU训练4096张合成画面、20轮、种子42；训练后固定模型和0.45置信度门槛，再使用此前未用于训练或调参的种子314159评测1024张画面。预测只提供建议，没有执行任何鼠标动作：

| 检查 | 本次结果 |
| --- | --- |
| 目标存在时，建议动作正确且坐标在真实形状内部 | 816 / 816 |
| 目标不存在时建议不操作 | 208 / 208 |
| 同图换目标，两次均正确 | 64 / 64 对 |
| 故意改错颜色目标，却仍命中原任务目标 | 1 / 816 |

上述是一个模型、一个简单生成器分布、一次留出测试的结果，不是“通用鼠标控制100%成功”。目标模板仅24种组合，位置分布受生成器限制，没有网页文本、窗口切换、遮挡、多屏、DPI、登录/支付或真实任务测试。未训练模型在同门槛下全部放弃动作，不能把这种保守行为算成会做任务。

## 只预测，不执行

```text
python -m experiments.visual_mouse predict --model E:/XiayuluoData/training/models/demo-001 --image synthetic-example.png --goal "点击红色圆形"
```

`--image` 是本人明确提供的本地图片，不是自动截图入口。首次验证请使用生成器产生的合成界面；真实桌面图片属于模型未验证的输入分布，预测结果不可直接用于点击。预测只返回结构化结果，不执行动作；不支持、含糊或超出有限模板的目标应拒绝，而不是猜测你的意思。

模型包由 `manifest.json` 和 `weights.npz` 组成，不使用 pickle 或 `torch.load` 加载任意对象。读取时需核对哈希、固定张量名称/形状、数值类型、有限数值和文件大小；格式检查不是模型能力或真实性认证。旧合成模型标识仍为 `domain: synthetic-only`，新示范模型有独立的模型类型和数据域；两者都固定 `live_execution_allowed: false`，不能通过改一个开关就声称适合控制真实桌面。

电脑 App「设置 → 训练数据保护」可查看实际目录、状态并手动备份，最近成功备份的界面记录仅限本次运行，不是历史备份索引。App 内更新前若已有训练文件，先取得写锁并完成 SHA-256 校验快照；失败就停止本次更新。手动运行 exe 安装器只做目录隔离检查，不会启动 Python 自动备份，覆盖安装前请先手动备份。保留全部历史备份、不自动删除；同盘明文快照不能替代独立磁盘加密备份。CLI 可校验备份及恢复到不存在的绝对新目录，不覆盖原根、不自动切换登记，详细命令和 10,000 文件/8 GiB 限制见 [TRAINING_DATA.md](TRAINING_DATA.md)。

## 明确提供的真实示范：导入、训练、留出评测

当前可以处理本人已经明确选择的本地截图和标签，不会因为打开 App、开启活动观察、允许鼠标控制或以前同意真实数据训练就自动录制桌面。还没有 App 内示范录制器、标注页面、连续轨迹采集或真实执行器接线；本轮只用生成的测试夹具验证链路，没有采集或训练你的私人数据。

数据集目录含 `dataset.json` 和它引用的相对 PNG 文件。清单精确字段如下，示例只是结构说明，**不是可以直接训练的数据集**；哈希需换成对应 PNG 文件的真实 64 位 SHA-256，且必须实际提供三个互不重叠的分区：

```json
{
  "schema": 1,
  "domain": "user-demonstrations-offline",
  "owner_provided": true,
  "goals": ["点击示例软件的保存按钮"],
  "samples": [
    {
      "episode": "session-001",
      "split": "train",
      "image": "images/frame-001.png",
      "sha256": "<此PNG的64位真实SHA256>",
      "goal": "点击示例软件的保存按钮",
      "action": "click",
      "xy": [0.72, 0.18]
    }
  ]
}
```

`goals` 注册 1–32 条不重复、精确匹配的任务文本，每条都必须有训练示范。`action` 只有 `click`、`move`、`noop`；前两者的 `xy` 是整张图片范围内的两个有限归一化坐标 `[0,1]`，`noop` 必须 `xy: null`。标签是监督答案，不作为网络推理输入；模型只读取画面像素和注册任务 ID。

必须有 `train`、`validation`、`test` 三个分区，同一 `episode` 不能跨分区；RGB 双线性缩放为 128×96 后相同的画面也不能跨分区。这样阻止直接数据泄漏，不保证识别所有近似重复或证明数据来源。所有注册任务有训练样本仍不代表模型能准确完成它。数据最多 4,096 条，单张 PNG 不超过 16 MiB/约 16 百万像素，累计读取不超过 512 MiB；仅接受有界普通文件，不跟随链接或目录联接。

先导入到持久训练根，再训练一个新模型。以下示例中的源数据必须由本人准备并核验，目录名称可以换成新的唯一名称：

```text
python -m experiments.visual_mouse import-demo --manifest E:/MyDemonstrations/dataset.json --name editor-demo-001 --data-dir E:/XiayuluoData/training
python -m experiments.visual_mouse train-demo --dataset E:/XiayuluoData/training/datasets/editor-demo-001/dataset.json --data-dir E:/XiayuluoData/training --output E:/XiayuluoData/training/models/editor-demo-001 --epochs 20 --seed 42
python -m experiments.visual_mouse evaluate-demo --model E:/XiayuluoData/training/models/editor-demo-001 --dataset E:/XiayuluoData/training/datasets/editor-demo-001/dataset.json --split test --report E:/XiayuluoData/training/models/editor-demo-001/test-report.json
python -m experiments.visual_mouse predict-demo --model E:/XiayuluoData/training/models/editor-demo-001 --image E:/MyDemonstrations/new-frame.png --goal "点击示例软件的保存按钮"
```

导入校验后复制原图和清单，不删除来源、不覆盖已有数据集；数据和模型发布共用训练写锁。`train-demo` 必须从该持久根的 `datasets/` 读取、向 `models/` 下新目录输出，也可省略 `--output` 由 CLI 选择新目录。训练只优化 `train`，在 `validation` 比较训练前后；`test` 留给单独的 `evaluate-demo`，加载时读取测试图仅用于内容隔离检查，不参与优化。评测清单哈希和任务表须与训练记录一致，不能更换清单后声称是原留出测试。新报告文件拒绝覆盖旧报告。

示范模型标记 `model_type: demonstration-visual-mouse-v1`、`domain: user-demonstrations-offline`，加载仍核对固定架构、数值权重和安全标识；输出始终 `execution_permitted: false`。图片会压缩到 128×96，不适合直接进行精细、高 DPI 的真实桌面控制。

报告中的 `label_coordinate_success_rate` 是预测动作匹配、且与人工标签归一化欧氏距离不超过 0.06，不是实际按钮边界命中或软件操作成功。还有建议精确率、`noop`、错误目标消融和可用的同图换任务比较；只有数据支持时才有对应指标。它们不是通用桌面能力认证，不能把格式校验、一个训练集或离线分数当成已会真实操作。

## 真实示范与后续执行的边界

本人已允许使用真实数据训练，但仍需明确选择实际数据或开启将来可见的采集功能。后续采集前应告知截图与鼠标动作保存位置、保留与删除方式、暂停入口及是否本地训练；现有离线导入不添加隐藏截图或鼠标/键盘监听。训练依赖保持独立，不把 PyTorch 和私人模型默认打进日常 App。

训练样本与四级记忆分开管理，不能自动把私人截图、账号信息或动作轨迹写进记忆。屏幕上出现的指令不能变成模型的新任务。未知目标、未验证画面和不确定预测应停止；任何后续真实执行仍需要独立权限、可撤销授权和停止机制，手机不能自动继承电脑读屏或鼠标权限。

## Neuro 资料及证据范围

- [osu! 官方对 Vedal 的访谈](https://github.com/ppy/osu-wiki/blob/master/news/2024/2024-07-01-the-followpoint-neuro-sama-the-ai-vtuber-that-invaded-osu.md)说明了原始 osu! AI 与后来 AI VTuber 的发展关系，没有公开完整视觉模型、训练方法、分辨率或推理频率。
- [VedalAI 官方 Among Us 训练代码](https://github.com/VedalAI/neuro-amongus/tree/86fb34543b6ea92ce171fa219e34087d192869fb/AI)可用于研究数据、神经控制器与动作执行的分离；该项目输入是游戏状态，不能拿它来证明原始 osu! 的纯视觉结构。

本原型的图像与任务条件化设计属于夏鱼洛自行验证的适配实验，不冒称已取得 Neuro 私有算法或具备其实际游戏表现。
