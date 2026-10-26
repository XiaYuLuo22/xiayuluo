# 训练数据保留与恢复（0.1.13）

训练数据与程序、安装包及四级记忆分开保存。更新程序不应替换训练目录；位置无法确认时停止操作，而不是新建一个空目录后假称数据还在。此保护需要先安装支持它的新版，不能追溯保护旧版、手动删除、磁盘损坏或未登记的任意目录。

本次范围是 Windows 训练数据保护和离线训练 CLI，不新增真实桌面采集或真实鼠标学习执行，也不代表有新的 Android APK。双端版本元数据同步到 0.1.13 / versionCode 14，仅同步元数据不等于已完成双端构建或正式发布；仓库可见性和签名发布前提不变。

## 数据在哪里

Windows 默认根目录是 `%LOCALAPPDATA%\Xiayuluo\training`，**不是**安装目录 `E:\Xiayuluo`。首次需要存到 E 盘时，可以明确指定一个独立绝对路径，例如 `E:/XiayuluoData/training`。不要放在程序、源码或其打包输出目录中。

| 根目录下的区域 | 用途 |
| --- | --- |
| `datasets/` | 单独管理的训练数据集；本机制不会自动采集桌面 |
| `models/` | 每次训练的新模型及相关报告，不覆盖旧模型 |
| `checkpoints/` | 单独保存的阶段性检查点；设有目录不代表已实现断点续训 |
| `backups/` | 本机训练文件快照；不会自动清理历史备份 |

Windows 在当前用户 `HKCU\Software\Xiayuluo` 的 `TrainingDataDir` 登记训练根目录，位置独立于安装版本。Linux/macOS 使用用户数据目录内的 `training-location.json`；默认根目录分别为 `$XDG_DATA_HOME/Xiayuluo/training`（未设置时使用 `~/.local/share/Xiayuluo/training`）和 `~/Library/Application Support/Xiayuluo/training`。

查看状态不启动训练、不采集屏幕，也不会因为目录尚不存在就立即生成训练数据。首次实际初始化时建立分区和格式标记，并登记位置。

## 首次选择位置与训练

App 可在本机配置中首次指定 `ASSISTANT_TRAINING_DIR`，值必须是绝对路径，例如：

```dotenv
ASSISTANT_TRAINING_DIR=E:/XiayuluoData/training
```

训练 CLI 也可以首次明确指定根目录和它的 `models/` 下一个不存在的新输出目录：

```text
python -m experiments.visual_mouse train --data-dir E:/XiayuluoData/training --output E:/XiayuluoData/training/models/demo-001 --train-samples 4096 --eval-samples 512 --epochs 20 --seed 42
```

不传 `--data-dir` 时，使用进程环境中的 `ASSISTANT_TRAINING_DIR`、已登记位置或首次默认位置；这些配置必须一致。不传 `--output` 时，在该根目录的 `models/` 下选择新的模型目录并输出实际路径。若只在 App 的 `.env` 中写了自定义位置，独立训练进程仍应明确传 `--data-dir` 或设置进程环境，不能假设所有工具都会自动读取该文件。

新 `train` 输出不能继续指向相对的 `dist/visual-learning/...` 或安装目录。旧的 `dist/`、`.vision-learning/` 模型和数据不会自动移动、覆盖或删除；旧模型仍可按原路径执行 `evaluate`、`predict`。这些旧目录不自动纳入新训练根的备份，清理或替换旧源码目录前应由本人另行备份。

## 位置错误时停止，不切换为空数据

以下情况会阻止相关训练、备份或更新：

- 新配置与已登记路径不同；不能通过改一个环境变量静默迁移。
- 已登记目录消失、原磁盘未接入、位置登记损坏、训练格式标记无效或不兼容。
- 训练根与程序安装/源码目录重叠，或使用链接、Windows 目录联接等无法确认的路径。
- 目录已有未管理的文件，无法作为新的空训练存储初始化。

原文件不会因此删除或覆盖。先接回原磁盘、核对路径或恢复副本；不要删除登记记录、建立空目录来掩盖错误。当前没有一键更改登记路径或自动迁移工具。

## App 里的状态和备份

电脑 App「设置 → 训练数据保护」显示实际根目录、保护状态，并提供「手动备份训练数据」。这里不提供数据删除、覆盖恢复或更改存储路径；手机不能远程管理电脑训练目录或触发备份。

界面“最近成功备份”只记录本次运行观察到的成功结果，不是持久的历史索引。重启后不显示上一次记录不等于备份文件被删除，请到实际 `backups/` 目录核对。保留的所有历史备份都不会被自动清理。

App 内下载更新、导入本地更新包之前，如果训练区已有文件，会先取得跨进程写锁，创建只读数据快照并检查文件清单和 SHA-256。一致性核验失败、文件正在变化、写锁不可用、空间不足或超出限制时，停止这次更新，不把部分备份当作成功。请先完成或停止训练及其他写入程序，再更新。

快照包括 `datasets/`、`models/`、`checkpoints/`，不递归打包 `backups/`，不替代配置、签名密钥和聊天数据库的独立备份。快照不装载模型、不执行训练内容；校验用于验证文件一致性，不是模型能力或来源认证。

手动运行 `Xiayuluo-Setup.exe` 时会检查程序目录与训练目录是否隔离，**但安装器不会调用 Python 自动做训练快照**。手动覆盖安装前应先在旧 App 的保护页或下方 CLI 备份并校验。更新 helper、程序回滚和卸载流程不操作或清理用户训练目录；程序回滚不是训练数据回滚。

## CLI 备份、校验与恢复副本

在支持本机制的源码和 Python 环境中运行：

```text
python -m assistant.training_data status
python -m assistant.training_data backup
python -m assistant.training_data --root E:/XiayuluoData/training status
```

`--root` 是全局参数，应写在子命令之前；它仍必须符合已登记位置，不是绕过位置冲突的迁移开关。备份命令输出实际备份路径、文件数、大小及校验信息。把以下示例中的备份路径替换成实际生成的文件：

```text
python -m assistant.training_data verify E:/XiayuluoData/training/backups/实际备份文件.zip
python -m assistant.training_data restore E:/XiayuluoData/training/backups/实际备份文件.zip --to F:/XiayuluoRecovery/restore-001
```

`verify`、`restore` 直接核对指定备份，不依赖当前训练根的登记记录，可在登记损坏时先恢复副本。恢复目标必须是**不存在的绝对新目录**，不能指向原训练目录或已有目录；恢复不覆盖原文件，也不自动把新副本登记为正在使用的训练根。恢复完成后仍需核对内容，并另外确认迁移/接管步骤，不能把恢复成功当成已切换训练位置。

当前单次快照和恢复资源上限为 **10,000 个文件、8 GiB 数据**。超过上限、校验错误或不支持的文件类型时拒绝操作，不部分成功、不删除原数据。数据库、密钥或特殊文件不应混入训练文件区，应使用相应的独立导出/备份方式。

## 不能保证的事情

- 默认备份是同盘的**明文副本**，可能包含私人训练内容，不能防止原磁盘损坏、丢失或同盘勒索软件破坏。请由本人另做独立磁盘、适当加密的备份，并保管好解密材料。
- 应用写锁只能协调遵守该锁的写入者；外部直接编辑、同步工具或其他程序仍可能修改文件。一致性检查发现变化会停止，不保证替代这些程序的数据库事务或备份机制。
- 不能防止本人手动删除、清空磁盘、破坏注册表/位置文件、卸载工具的自定义清理，不能保证“任何情况下永不丢失”。
- 首次安装支持保护的新版之前，旧数据仍需自行保留；云端代码更新不会替旧 App 或任意旧模型目录补做备份。

视觉模型的有限能力、离线实验用法与真实示范采集授权边界见 [VISION_LEARNING.md](VISION_LEARNING.md)。
