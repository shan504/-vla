# 当前验证记录

本次环境：Linux x86_64、Python 3.10、Torch 2.6.0 CPU。云端无 NVIDIA GPU、ROS 2 或 Go2 连接。

| 检查 | 实际结果 |
|---|---|
| `bash scripts/go2/install_server.sh cpu` | 成功；重复运行成功，严格使用带哈希锁文件 |
| `uv pip check --python .venv/bin/python` | 已安装的 50 个包依赖一致 |
| `python -m go2.doctor` | 真实 InternVLA 模型类、NextDiT、DepthAnything 编码器导入成功 |
| 小型随机权重多模态前向计算 | 成功；真实模型代码，包含图像 patch 和文字 token |
| 小型随机权重轨迹解码 | 成功；覆盖 Diffusers checkpointing API 兼容性 |
| NextDiT 检查点维度回归 | 成功；复现 Diffusers 0.33.1 下 1024/1536 维不匹配，恢复训练时 SwiGLU 宽度后，严格加载、保存/重载及实际前向结果一致 |
| DepthAnything meta 初始化加载 | 成功；两种 Accelerate meta 上下文中物化并严格加载真实小网络权重，参数、持久 buffer 和前向结果一致；损坏权重仍被拒绝 |
| HTTP 服务协议测试 | 22 项通过，使用 FakeAgent，覆盖深度单位、内参、指令/reset、错误恢复和输出校验 |
| CUDA 检查 | 不通过：本云机器没有 NVIDIA GPU；真实服务按预期拒绝启动 |
| 模型权重下载 | 已执行；Hugging Face 请求被平台代理以 403 拒绝，所需域名已保存到配置草稿但尚未应用 |
| CUDA 锁文件 | 用户集群已安装；4090 上 Torch 2.6.0+cu124、模型类导入、CUDA 和 BF16 自检成功 |
| 用户端模型权重下载与上传 | 用户本地下载完成并上传到集群；DualVLN revision `a698a9e898b4001621a319e1bc89f02ec715cc86` |
| 用户 4090 完整权重加载 | 首次因上述 1024/1536 维差异失败；代码回归已修复，完整 GPU 加载与推理待重试，不能由自检成功推出 |
| ROS Python 3.8 依赖锁文件 | 成功解析，未在机器人安装验证 |
| ROS 2 图像/里程计/速度话题 | 未执行：没有真实 ROS/机器人连接 |
| 完整预训练模型推理、导航成功率和实机运动 | 未执行；不能由随机权重或协议测试推出 |

机器人客户端与 UDP 驱动相关软件测试 16 项通过，包括 STOP 清除旧目标/速度、HTTP 连接错误、dry-run 不创建速度发布器、限速和命令时效。ROS transport shim 与测试控制器只用于检查客户端逻辑，不代表真实 ROS/DDS 或 MPC 求解验证。

当前运行 `.venv/bin/python -m pytest tests/go2 -q`：**85 passed**，无跳过，包含模型组件、检查点加载、服务协议、机器人软件及安装/调度脚本检查。完整 GPU 推理尚未通过。

未运行上游需要私有目录、仿真器、模型或数据集的全量评测。当前已准备代码与 CPU 开发环境；完整实机就绪需按 [GO2_EDU.md](GO2_EDU.md) 在 GPU 工作站和机器人端完成验证。
