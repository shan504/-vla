# Go2 EDU · InternNav / InternVLA-N1

给宇树 **Go2 EDU** 运行视觉语言导航实验。基于 [InternRobotics/InternNav](https://github.com/InternRobotics/InternNav)，使用公开的 **InternVLA-N1-DualVLN** 预训练模型；GPU 工作站负责推理，机器人侧使用 RGB-D、里程计和 SDK2 速度控制。

**完整部署说明：[docs/GO2_EDU.md](docs/GO2_EDU.md)** · [上游 README](docs/UPSTREAM_README.md) · [来源与版本](docs/UPSTREAM.md)

Windows 无线开发与家庭/农场双 Wi-Fi 配置见 [docs/WIFI.md](docs/WIFI.md)。

服务器离线安装和存储迁移见 [docs/OFFLINE.md](docs/OFFLINE.md)。

## GPU 工作站快速开始

Linux x86_64、Python 3.10（脚本自动准备）、支持 CUDA 12.4 的 NVIDIA 驱动。官方推理参考配置为 RTX 4090 / A100。无需先装 Isaac Sim、Habitat 或训练数据集。

```bash
git clone https://github.com/shan504/-vla.git
cd -- -vla
bash scripts/go2/install_server.sh cu124
.venv/bin/python -m go2.download_models
cp -n config/go2.env.example config/go2.env
# 编辑 config/go2.env：GPU 工作站的监听地址和机器人可访问的 SERVER_URL
bash scripts/go2/start_server.sh --host 0.0.0.0
```

在 GPU 工作站另一个终端检查模型服务：

```bash
curl --fail http://127.0.0.1:5801/healthz
```

## Go2 端

按 [部署说明](docs/GO2_EDU.md) 准备 ROS 2、Unitree SDK2、RealSense，配置网卡和工作站地址。启动感知与控制桥接后，手动运行导航客户端：

```bash
bash onboard/start_robot.sh
# 另一个已激活系统 ROS 2 环境的终端，在仓库根目录执行：
python3 -m go2.robot_client \
  --server-url http://YOUR_GPU_LAN_IP:5801/eval_dual \
  --instruction 'Walk to the doorway and stop.'
```

首次先以 `--dry-run` 检查图像、内参、里程计和模型输出，再运行控制。输入目前是文字，语音识别需另外接入。

## 无 GPU 的开发环境

```bash
bash scripts/go2/install_server.sh cpu
.venv/bin/python -m pytest tests/go2 -q
```

CPU 模式用于依赖、模型组件与协议测试，不等于完整预训练模型推理或实机验证。运行完整模型还需要下载权重并使用 GPU；不会在安装或测试时连接机器狗。
