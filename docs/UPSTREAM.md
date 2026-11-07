# 上游来源

- InternNav: https://github.com/InternRobotics/InternNav
- 导入版本: `7a5c62400ac45b313d9b709c740b64191556a242`（main）
- 根目录 `LICENSE` 保留 Intern Robotics 的 MIT 许可；各文件原始版权声明保持不变。
- 上游 README 保存在 [UPSTREAM_README.md](UPSTREAM_README.md)。
- 机器人侧驱动来源和固定版本见 `onboard/UPSTREAM.md`，相应许可保存在 `onboard/LICENSE`。

本仓库保留用户 GitHub 仓库为 `origin`；`upstream` 指向官方 InternNav。`go2/`、`scripts/go2/` 和 `config/` 是 Go2 EDU 的部署入口。

推理使用 DualVLN，而非需要额外 diffusion_policy 依赖的独立 NavDP 分支。原始训练、仿真和各基线保留，但不属于本次最小实机推理环境。上游两个子模块只在相关基线需要时执行 `git submodule update --init --recursive`，不是运行本次 DualVLN 服务的前提。

部署修复包括按需加载可选 agent/encoder、可选 SDPA、正确初始化/reset、可配置指令/相机内参，以及客户端停车与超时处理。未改模型权重、网络结构或训练算法。

模型下载首次将公开分支解析为具体提交，再按该提交下载；`checkpoints/model-revisions.json` 保存两个模型的实际 revision。重现同一下载时传入：

```bash
.venv/bin/python -m go2.download_models --revision MODEL_COMMIT --depth-revision DEPTH_COMMIT
```
