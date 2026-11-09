# Go2 EDU 实机部署

本配置分两端：GPU 工作站运行 InternVLA-N1-DualVLN；Go2 EDU 的 ROS 2 系统 Python 运行相机、里程计桥接和速度控制。先验证文字导航，语音识别可后续接入。无需从头训练，也无需安装仿真器。

## 1. GPU 工作站

使用 Linux x86_64 和支持 CUDA 12.4 的 NVIDIA 驱动。官方模型推理参考设备为 RTX 4090 / A100；按 24GB 显存档准备。机器人和工作站应处于可互访的局域网。云环境若没有 GPU，只能运行第 5 节开发检查。

从仓库根目录执行：

```bash
bash scripts/go2/install_server.sh cu124
.venv/bin/python -m go2.doctor --gpu
.venv/bin/python -m go2.download_models
```

安装脚本建立 Python 3.10 `.venv`，严格按带哈希的锁文件安装 Torch 2.6.0 / CUDA 12.4、Transformers 4.51.0、Diffusers 0.33.1。默认使用 PyTorch SDPA，不需要编译 FlashAttention。不执行上游包含其他基线的全量 `[model]` 安装。

集群普通账号也可安装：缺少 `ensurepip` / `python3-venv` 时，引导程序直接从官方 PyPI 下载固定版本的独立 `uv` 并核验 SHA256，无需 apt 或 sudo。Python、工具和缓存位于本项目 `.tools/`，依赖仅装到本项目 `.venv/`；脚本忽略继承的 `PYTHONPATH`、`PYTHONHOME` 和用户 site-packages，不修改系统 Python、已有 Conda 环境或其他项目。此前失败留下的 `.tools/uv` 文件可保留，无需删除。

下载结果为：

```text
checkpoints/
  InternVLA-N1-DualVLN/             # 主模型、processor 与 tokenizer
  depth_anything_v2_metric_hypersim_vits.pth
  model-revisions.json             # 下载时解析的实际模型提交
```

两项均为上游公开模型。下载需要访问 `huggingface.co` 及其实际重定向的 LFS/Xet 文件域名；当前云环境的域名添加已经保存到环境配置草稿，尚不代表运行时已放行。若遇策略 403，在环境设置保存相关配置后重试，不需要为公共权重申请 GitHub token。

模型目录和输出不会提交到 Git。重复下载使用本地缓存；固定同一模型版本的方法见 [UPSTREAM.md](UPSTREAM.md)。上游 RGB 编码器通过相对路径加载 DepthAnything，**始终在仓库根目录启动**。

## 2. 启动推理服务

```bash
cp -n config/go2.env.example config/go2.env
# 编辑 config/go2.env，将 SERVER_HOST 设为 0.0.0.0，SERVER_URL 设为工作站局域网地址
.venv/bin/python -m go2.doctor --gpu --model-path checkpoints/InternVLA-N1-DualVLN
bash scripts/go2/start_server.sh
```

另一个终端执行：

```bash
curl --fail http://127.0.0.1:5801/healthz
```

`/healthz` 只有真实 agent 完成权重加载后才提供 ready；它不证明导航任务已经成功。该有状态服务一次服务一台狗、一个导航任务，通过 `reset=true` 开始新任务。此实验服务用于受信任局域网。

可选参数通过 `bash scripts/go2/start_server.sh --help` 查看，服务不接入任何机器人控制接口。相机内参由客户端从真实 CameraInfo 发送，不使用网上示例的固定内参。

## 3. Go2 EDU 软件端

先保留机器人现有的 ROS 2 和宇树软件。社区路线以 Ubuntu 20.04 / ROS 2 Foxy（Python 3.8）为参考，Ubuntu 22.04 / ROS 2 Humble（Python 3.10）也应使用其对应的系统 Python 和 ROS 包；不要在模型 `.venv` 中导入机器人端 ROS。

在机器人终端：

```bash
source /opt/ros/foxy/setup.bash       # Humble 改为 /opt/ros/humble/setup.bash
python3 -m pip install --user --require-hashes -r requirements/go2-robot.lock
python3 -c 'import rclpy, cv_bridge, message_filters, casadi, pyrealsense2'
```

若 ROS 的桥接包缺失，使用对应发行版的官方 ROS 包管理器安装 `cv-bridge`、`message-filters`、`sensor-msgs`、`geometry-msgs`、`nav-msgs`。机器人 Python 依赖锁按 Foxy 的 Python 3.8 解析；已在云端确认可解析，未在真实 ROS 系统安装验证。

宇树 SDK2 Python 尚未安装时，在仓库外安装固定来源版本：

```bash
git clone https://github.com/unitreerobotics/unitree_sdk2_python.git ~/unitree_sdk2_python
git -C ~/unitree_sdk2_python checkout 814556d15970dd2ecf1c9984e845ca02ab07e206
python3 -m pip install --user -c requirements/go2-robot.in -e ~/unitree_sdk2_python
```

如果 SDK 提示找不到 CycloneDDS，先按 [SDK 官方说明](https://github.com/unitreerobotics/unitree_sdk2_python) 安装。可使用 `eclipse-cyclonedds/cyclonedds` 的 `releases/0.10.x` 分支，已核对提交 `5041f3560c088c99e5088b2b8520b69169621196`，编译后将 `CYCLONEDDS_HOME` 指向其绝对安装目录，再重试 SDK 安装。

相机使用 D455 或教程列出的 D435/D435i，通过 USB 3 连接。用 RealSense 工具确认能取得对齐的彩色、深度图。先通过遥控器使机器人进入合适的站立/运动模式；驱动启动不会自动起身或行走。

## 4. 接通与执行

在机器人仓库根目录启动桥接：

```bash
ROBOT_IF=eth0 ROS_DISTRO=foxy bash onboard/start_robot.sh
```

将 `eth0` 改为实际连接机器狗的网卡。脚本只管理它自己启动的驱动、ROS 桥和相机子进程。若已经有相机节点，不要重复启动相机。

另一个系统 ROS 2 终端查看：

```bash
ros2 topic list
ros2 topic echo /camera/camera/color/camera_info --once
ros2 topic echo /odom_bridge --once
```

默认接口：

| 话题 | 用途 |
|---|---|
| `/camera/camera/color/image_raw` | RGB |
| `/camera/camera/aligned_depth_to_color/image_raw` | 与 RGB 对齐的毫米深度 |
| `/camera/camera/color/camera_info` | RGB 相机真实内参 |
| `/odom_bridge` | 里程计 |
| `/cmd_vel_bridge` | 客户端输出速度，由 SDK2 驱动执行 |

先只请求模型、打印预测，不发布导航速度：

```bash
python3 -m go2.robot_client \
  --server-url http://YOUR_GPU_LAN_IP:5801/eval_dual \
  --instruction 'Walk to the doorway and stop.' --dry-run
```

确认图像、内参、里程计和模型预测有效后，去掉 `--dry-run` 执行同一条短距离任务。真实运动阶段保留遥控器人工接管，先在空旷区域测试。客户端对 STOP、失效预测和过期命令停车，驱动也有独立的 UDP 命令看门狗；这些行为已做软件测试，不能替代实机验收。

`python3 -m go2.robot_client --help` 查看话题、限速和超时参数。修改导航指令后重新启动客户端，首次请求自动 reset。`config/go2.env` 是本机配置示例；运行客户端时通过 CLI 明确传入地址和指令，或按其帮助使用环境变量。

## 5. 云端开发验证

```bash
bash scripts/go2/install_server.sh cpu
.venv/bin/python -m go2.doctor
.venv/bin/python -m pytest tests/go2 -q
```

模型组件测试使用真实模型代码和小型随机权重执行多模态前向/轨迹解码；服务协议测试注入测试 agent；停车和看门狗测试不连接硬件。它们不验证预训练模型导航性能。完整模型、CUDA、ROS 2 和 Go2 实机闭环在当前云机器尚未验证，具体结果见 [VALIDATION.md](VALIDATION.md)。

每个云任务已是隔离环境，直接使用现有检出，无需创建 Git worktree。
