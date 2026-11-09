# 本地下载、服务器离线安装

离线安装仍需要服务器有足够存储额度。`Disk quota exceeded (os error 122)` 是服务端存储限制，不能通过先在电脑下载来绕过。当前用户指定项目存放于 `/data_old/run01/scxk867/InternNav`，与之前受限的 `/data/home/scxk867/InternNav` 区分；既有 `envs/`、`models/` 和其他实验不变。

## 1. 迁移已有项目

从服务器终端执行，目标不存在时才迁移，以避免覆盖旧项目：

```bash
cd /data/home/scxk867 &&
test -d /data_old/run01/scxk867 &&
test ! -e /data_old/run01/scxk867/InternNav &&
mv -T -- /data/home/scxk867/InternNav /data_old/run01/scxk867/InternNav &&
cd /data_old/run01/scxk867/InternNav &&
rm -rf -- .venv
```

这会保留代码、已下载的 uv/独立 Python 与缓存；旧位置的项目目录随成功迁移消失。仅删除本次失败安装的 `.venv`，后续在新路径重新创建独立环境，避免虚拟环境的绝对路径失效。不删除任何其他项目环境。

确认新路径空间、架构和 glibc：

```bash
df -h /data_old/run01/scxk867/InternNav
uname -m
getconf GNU_LIBC_VERSION
```

以下 wheel 包针对 Linux x86_64、Python 3.10 和 glibc >= 2.28；服务器不满足时先停止。存储还要容纳 wheel、安装后的环境、模型权重及临时文件，建议预留至少 50GB，较完整的实验目录按 100GB 准备。

## 2. 在本地 Windows 的 WSL2 下载

打开 WSL2 Ubuntu，不使用 Windows 原生 Python 下载服务端 Linux 包。克隆或更新本仓库，然后在仓库根目录执行：

```bash
bash scripts/go2/download_wheels.sh /mnt/d/InternNav-offline-cu124
```

`/mnt/d` 对应 Windows D 盘；如不存在或空间不足，换成实际本地目录。脚本需要输出盘至少 15GiB 可用空间，用于 wheel 和传输归档。下载工具使用本项目 `.tools/wheel-download-env`，不会给电脑安装 CUDA/PyTorch 环境或修改既有 Conda。

完成后得到 `D:\InternNav-offline-cu124.tar`，内有 Linux wheel、对应依赖锁和 SHA256 校验表。将它通过已使用的 SFTP 客户端上传到服务器的 `/data_old/run01/scxk867/`。不要上传到之前触发配额的 home 根目录，也不要将本地已经安装的 `.venv` 跨机器复制。

## 3. 服务器离线安装

```bash
cd /data_old/run01/scxk867
tar -xf InternNav-offline-cu124.tar
cd InternNav
git pull --ff-only
bash scripts/go2/install_offline.sh /data_old/run01/scxk867/InternNav-offline-cu124
```

服务器使用前一次安装已经准备的 `.tools/uv/bin/uv` 和 `.tools/python/`，不重新在线下载 Python。安装时禁用软件包索引，核验全部校验和与锁文件，创建本项目 `.venv`，不使用系统或其他项目的 Python 包。项目迁移后不能保留原路径创建的 `.venv`。

这只是依赖离线包；InternVLA-N1-DualVLN 和 DepthAnything 权重需要另行下载或上传。模型启动仍应在调度分配的 GPU 节点上执行，不能把登录节点上的依赖检查当作 GPU 推理成功。

本次验证：54 项软件测试通过；64 个锁定依赖均核实存在匹配目标平台和锁内哈希的官方 wheel；另用真实小型 wheel 验证了离线安装、导入，以及项目迁移后 managed Python 创建新环境。完整 CUDA 包安装和预训练模型推理尚未在该服务器验证。
