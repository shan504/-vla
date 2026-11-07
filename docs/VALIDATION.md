# 当前验证记录

本次环境：Linux x86_64、Python 3.10、Torch 2.6.0 CPU。云端无 NVIDIA GPU、ROS 2 或 Go2 连接。

| 检查 | 实际结果 |
|---|---|
| `bash scripts/go2/install_server.sh cpu` | 成功；重复运行成功，严格使用带哈希锁文件 |
| `uv pip check --python .venv/bin/python` | 已安装的 50 个包依赖一致 |
| `python -m go2.doctor` | 真实 InternVLA 模型类、NextDiT、DepthAnything 编码器导入成功 |
| 小型随机权重多模态前向计算 | 成功；真实模型代码，包含图像 patch 和文字 token |
| 小型随机权重轨迹解码 | 成功；覆盖 Diffusers checkpointing API 兼容性 |
| HTTP 服务协议测试 | 22 项通过，使用 FakeAgent，覆盖深度单位、内参、指令/reset、错误恢复和输出校验 |
| CUDA 检查 | 不通过：本云机器没有 NVIDIA GPU；真实服务按预期拒绝启动 |
| 模型权重下载 | 已执行；Hugging Face 请求被平台代理以 403 拒绝，所需域名已保存到配置草稿但尚未应用 |
| CUDA 锁文件 | 成功解析，未在 GPU 安装验证 |
| ROS Python 3.8 依赖锁文件 | 成功解析，未在机器人安装验证 |
| ROS 2 图像/里程计/速度话题 | 未执行：没有真实 ROS/机器人连接 |
| 完整预训练模型推理、导航成功率和实机运动 | 未执行；不能由随机权重或协议测试推出 |

机器人客户端与 UDP 驱动相关软件测试 16 项通过，包括 STOP 清除旧目标/速度、HTTP 连接错误、dry-run 不创建速度发布器、限速和命令时效。ROS transport shim 与测试控制器只用于检查客户端逻辑，不代表真实 ROS/DDS 或 MPC 求解验证。

最终运行 `.venv/bin/python -m pytest tests/go2 -q`：**40 passed**（含模型组件 2 项、服务协议 22 项和机器人软件 16 项），无跳过。Python 编译、Shell 语法和 Git 差异空白检查通过。

未运行上游需要私有目录、仿真器、模型或数据集的全量评测。当前已准备代码与 CPU 开发环境；完整实机就绪需按 [GO2_EDU.md](GO2_EDU.md) 在 GPU 工作站和机器人端完成验证。
