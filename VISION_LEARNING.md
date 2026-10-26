# 夏鱼洛视觉动作学习原型

这是借鉴 Neuro「训练专门的动作控制器」思路、自行编写的实验，不是 Neuro 原版复现，也没有复制其代码、下载其权重或改用另一个桌面代理框架。

本阶段验证一件事：**同一张画面，给出不同任务目标，训练后的模型能否预测相应目标的单步鼠标动作。** 模型输入是图像像素和目标编码，不通过聊天模型临时猜坐标。生成器知道的对象位置只用于训练答案和评测，不得作为预测输入。

## 当前范围

- 独立实验模块 `experiments/visual_mouse`，尚未接入夏鱼洛 App 的真实鼠标执行器。
- 只使用自行生成的 128×96 RGB 合成界面：红、绿、蓝、黄四种颜色，圆形、方形、三角形三种形状；任务是点击或移动到目标，目标不存在时应不操作。
- 中文、英文目标仅支持有限模板，例如「点击红色圆形」。这不是通用语言理解，也不表示模型能识别任意 Windows 按钮、网页文字或图标。
- 第一阶段是单步定位与动作学习，不代表已经学会鼠标轨迹、拖动、连续任务或高帧率控制。即使后续加入时序网络，也不能仅凭网络名称宣称已完成轨迹学习。
- 训练、评测和预测均不捕获真实桌面、不监听鼠标或键盘、不点击真实鼠标、不上传训练数据。已有 App 的鼠标、主动建议、语音和手机权限不因此改变。

## 准备 CPU 训练环境

推荐在项目和日常 App 环境之外建立独立虚拟环境。以下是 Windows PowerShell 示例，在仓库根目录运行；路径可自行选择：

```powershell
py -3.12 -m venv E:\XiayuluoVisionEnv
E:\XiayuluoVisionEnv\Scripts\python -m pip install -c requirements-vision.lock --index-url https://download.pytorch.org/whl/cpu "torch==2.14.1+cpu"
E:\XiayuluoVisionEnv\Scripts\python -m pip install -c requirements-vision.lock "numpy==2.4.2" "Pillow==12.3.0"
```

PyTorch 从官方 CPU wheel 源获取，不下载 CUDA 工具链或预训练模型权重；NumPy 和 Pillow 是图像、张量及安全文件读写依赖。安装依赖需要联网，后续合成数据训练本身不需要模型 API 密钥。Linux 可使用相同依赖及训练命令，把 Python 路径换成对应虚拟环境的 `bin/python`。

## 训练和评测

在已安装上述依赖的环境中，从仓库根目录运行：

```text
python -m experiments.visual_mouse train --output dist/visual-learning/demo --train-samples 4096 --eval-samples 512 --epochs 20 --seed 42
python -m experiments.visual_mouse evaluate --model dist/visual-learning/demo --samples 1024 --seed 314159 --report dist/visual-learning/test-report.json --require-learning
python -m experiments.visual_mouse preview --model dist/visual-learning/demo --output dist/visual-learning/preview.png
```

输出目录必须是新目录，不覆盖已有模型。训练样本和评测样本应使用互不重叠的种子范围；不能把训练集命中率当成泛化能力。还应检查「同图换目标」的预测是否随目标改变，并与未训练模型对照，避免模型只记住常见位置或忽略任务目标。

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
python -m experiments.visual_mouse predict --model dist/visual-learning/demo --image synthetic-example.png --goal "点击红色圆形"
```

`--image` 是本人明确提供的本地图片，不是自动截图入口。首次验证请使用生成器产生的合成界面；真实桌面图片属于模型未验证的输入分布，预测结果不可直接用于点击。预测只返回结构化结果，不执行动作；不支持、含糊或超出有限模板的目标应拒绝，而不是猜测你的意思。

模型包由 `manifest.json` 和 `weights.npz` 组成，不使用 pickle 或 `torch.load` 加载任意对象。读取时需核对哈希、固定张量名称/形状、数值类型、有限数值和文件大小；格式检查不是模型能力或真实性认证。包内标识固定为 `domain: synthetic-only`、`live_execution_allowed: false`，不能通过改一个开关就声称适合控制真实桌面。

## 后续真实示范数据的边界

合成界面实验通过后，仍需另外设计并确认真实桌面验证和示范采集方案。采集前应明确告知截图与鼠标动作会保存到哪里、保留多久、怎样暂停和删除、是否完全本地训练；本人明确开启后才能采集，不能因打开 App、开启主动建议或以前同意鼠标控制而偷偷录制。

训练样本与四级记忆分开管理，不能自动把私人截图、账号信息或动作轨迹写进记忆。屏幕上出现的指令不能变成模型的新任务。未知目标、未验证画面和不确定预测应停止；任何后续真实执行仍需要独立权限、可撤销授权和停止机制，手机不能自动继承电脑读屏或鼠标权限。

## Neuro 资料及证据范围

- [osu! 官方对 Vedal 的访谈](https://github.com/ppy/osu-wiki/blob/master/news/2024/2024-07-01-the-followpoint-neuro-sama-the-ai-vtuber-that-invaded-osu.md)说明了原始 osu! AI 与后来 AI VTuber 的发展关系，没有公开完整视觉模型、训练方法、分辨率或推理频率。
- [VedalAI 官方 Among Us 训练代码](https://github.com/VedalAI/neuro-amongus/tree/86fb34543b6ea92ce171fa219e34087d192869fb/AI)可用于研究数据、神经控制器与动作执行的分离；该项目输入是游戏状态，不能拿它来证明原始 osu! 的纯视觉结构。

本原型的图像与任务条件化设计属于夏鱼洛自行验证的适配实验，不冒称已取得 Neuro 私有算法或具备其实际游戏表现。
