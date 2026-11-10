# D435i 单次 RGB-D 采样与实景推理

先保存一组真实相机观测，再交给已启动的 GPU 模型服务。采样工具独立运行，仅需要匹配当前 Python/ARM 架构的 `pyrealsense2`、NumPy 和 Pillow，不需要 ROS、里程计或运动控制。

## 1. 机器狗端识别设备

D435i 的 USB 数据线应连接到运行当前 SSH 系统的 Jetson USB 主机接口。先检查：

```bash
lsusb
/usr/bin/python3 -V
/usr/bin/python3 -c 'import pyrealsense2, numpy, PIL'
```

`lsusb` 应出现 Intel/RealSense 设备。没有 USB 枚举时先核对接入口、数据线和供电；Python SDK 的安装不能替代 USB 连接。必要时用 `sudo dmesg -W` 仅观察之后新增的事件，再拔插一次相机；不要清除内核日志。

本次机器狗为 aarch64、L4T R35.3.1，系统 Python 3.8.10；已有 NumPy/Pillow，尚未安装相机 SDK。官方 PyPI 提供 `pyrealsense2==2.55.1.6486` 的 `cp38-cp38-manylinux2014_aarch64` wheel，可作为该 Python 的安装候选；真实导入和取流仍需实机验证。应使用独立相机环境安装，不修改已有 Conda base。不同 Python/架构不能直接沿用这一 wheel。

USB 识别成功后，可在机器人项目根目录建立独立的 Python 3.8 相机环境：

```bash
/usr/bin/python3 -m venv .tools/camera-env
.tools/camera-env/bin/python -m pip install --require-hashes \
  -r requirements/go2-camera-py38-aarch64.lock
```

若系统缺少 venv/ensurepip，应补齐对应的 `python3.8-venv` 工具再创建环境。此锁文件只包含三个官方 ARM64 wheel：NumPy、Pillow 和 pyrealsense2。官方 wheel 使用 native V4L 后端；若 USB 已识别且有访问权限，但实际取流仍因 L4T 内核格式支持失败，再按 [RealSense 官方 Jetson 说明](https://github.com/IntelRealSense/librealsense/blob/v2.55.1/doc/installation_jetson.md) 判断是否需要用户目录内的 RSUSB 构建，不能由 wheel 可安装推断取流成功。

## 2. 采集一帧

选好已能导入上述模块的 Python 后，在机器人仓库根目录运行：

```bash
.tools/camera-env/bin/python go2/capture_realsense.py --list
.tools/camera-env/bin/python go2/capture_realsense.py --output captures/d435i-sample
```

也可只复制 `go2/capture_realsense.py` 这一个文件到机器人，再直接运行。默认 640×480、30 FPS，丢弃前 30 帧后，从同一帧组提取彩色图和对齐到彩色的深度图。多台相机可用 `--serial` 选择；输出目录已存在时拒绝覆盖。USB 传输条件和设备支持的流配置需以实机为准。

输出包括：

| 文件 | 内容 |
|---|---|
| `rgb.png` | RGB8 彩色图 |
| `depth.png` | 对齐深度，单通道 uint16；每米 10000 单位，0 为无效 |
| `camera_intrinsic.json` | 对齐彩色帧真实标定的 3×3 内参 |
| `metadata.json` | 设备、USB、分辨率、畸变、实际深度标度、时间戳、有效深度统计 |
| `depth_raw.npy` | 原始对齐 Z16 深度，结合 metadata 中的 `depth_raw_unit_m` 还原米值 |

PNG 协议最大可表达 6.5535 米。更远的读数设为无效 0 并记录数量，完整原始读数保留在 `.npy` 中；不会把远距离饱和成假的 6.5535 米。采样结束或异常后会释放本工具启动的相机流。

## 3. 在 GPU 作业内推理

将整个采样目录上传至服务器项目的 `captures/`。从服务器项目根目录执行，替换为当前仍在运行的模型服务任务号：

```bash
srun --jobid=YOUR_RUNNING_JOB_ID --overlap --ntasks=1 --cpus-per-task=1 \
  .venv/bin/python -m go2.predict_capture captures/d435i-sample \
  --instruction 'Walk to the doorway and stop.'
```

模型默认监听计算节点的 `127.0.0.1:5801`，因此客户端需在同一作业节点执行。命令先校验图片、深度单位和真实内参，再检查服务就绪，发送一次 `reset:true` 的新任务请求，打印 HTTP 状态、耗时和预测结果。

输出预测不等于实机导航成功。每次命令都会清除模型的上一段观测历史，避免此前合成图测试污染当前实景请求；本客户端只输出结果。实际取流和完整实景推理结果需要通过机器狗与服务器现场验证。
