# 独立的 InternNav 4090 作业

本项目使用自己的 `.venv`、模型目录和日志，不激活其他项目环境，也不提交或取消其他项目任务。

## 设置真实资源名称

在登录节点运行：

```bash
sinfo -h -o '%P %G' | grep -i 4090
```

把实际分区与 GPU 类型填写到 `config/slurm.env`（从 `config/slurm.env.example` 复制）。资源请求只要一张卡；sinfo 中的总卡数需要改成 `1`，括号中的插槽信息不写入 GRES。默认分区名称末尾的 `*` 也不写入配置。不要凭显卡型号猜分区名称。若查询没有输出，继续用 `sinfo -N -h -o '%N %P %G %f'` 检查节点特征，不能认为账号没有 GPU。

本次用户已确认该集群存在 `gpu_4090` 分区，模板使用 `INTERNNAV_PARTITION=gpu_4090` 和 `INTERNNAV_GRES=gpu:1`。在专用分区申请一张卡后，作业还会用 Torch 核对真实型号为 4090；尚未在该集群验证成功分配。

如集群要求 account / QOS，也在该文件填写对应的有效资源授权。此配置只是提交参数，不创建新的计费账号或改变账号权限。

## 先验证 GPU，不需要权重

```bash
cd /data_old/run01/scxk867/InternNav
git pull --ff-only
cp -n config/slurm.env.example config/slurm.env
bash scripts/go2/submit_slurm.sh --probe
JOBID=$(cat logs/slurm/last-job-id)
squeue -j "$JOBID"
cat "logs/slurm/internnav-$JOBID.out"
cat "logs/slurm/internnav-$JOBID.err"
```

只提交一次。查看状态、日志、运行时间时沿用已经生成的 JOBID，不重复运行 sbatch。任务排队时日志可能尚未生成。探测作业最多申请 10 分钟，确认 CUDA、BF16 和 GPU 型号为 4090 后自动退出。它不会加载权重或启动机器人。

## 运行真实模型服务

完成模型下载或上传后，先确认本项目的 `checkpoints/InternVLA-N1-DualVLN/` 和 `checkpoints/depth_anything_v2_metric_hypersim_vits.pth` 存在。然后提交：

```bash
bash scripts/go2/submit_slurm.sh
JOBID=$(cat logs/slurm/last-job-id)
tail -f "logs/slurm/internnav-$JOBID.out"
```

常规作业默认单节点、单张 GPU、4 CPU、32GB 内存和 2 小时。分区与 GRES 从配置明确传给 sbatch。批处理脚本再次检查实际分配的是 4090，才继续加载本项目模型；其他型号会报错退出。脚本用 Slurm 提交目录或明确传入的项目根路径定位环境，不依赖调度器复制到 spool 的脚本路径。

模型服务默认端口 `5801`、地址 `127.0.0.1`、接口 `/healthz`。日志打印实际计算节点 hostname；**登录节点的 127.0.0.1 不是计算节点的 127.0.0.1**。在作业所在计算节点执行健康检查：

```bash
curl --fail http://127.0.0.1:5801/healthz
```

也可以在支持 `--overlap` 的 Slurm 中，从登录节点进入现有作业执行检查，无需再次申请作业：

```bash
srun --jobid="$JOBID" --overlap --ntasks=1 --cpus-per-task=1 curl --fail http://127.0.0.1:5801/healthz
```

外部机器狗连接需要后续设置 SSH 转发或平台提供的访问方式；暂不假设计算节点能直接从家庭/手机热点访问。

## 查看或结束本项目作业

```bash
JOBID=$(cat logs/slurm/last-job-id)
squeue -j "$JOBID"
scontrol show job "$JOBID"
scancel "$JOBID"
```

`scancel` 只用于你确定要结束的当前 InternNav 作业。环境软件测试和模拟调度测试不能证明这台集群已分配到真实 4090，须以探测作业日志为准。
