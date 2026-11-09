# Windows 无线开发：家中与农场

已从 Go2 本机输出确认：`eth0=192.168.123.18/24`，`wlan0` 在家庭网络 `CU_tD9c` 上取得 `192.168.1.230/24`。这两个地址描述本次设备状态，不是所有 Go2 的固定地址。无线用于 Windows 到机载计算机的 SSH 和模型 HTTP；SDK2 继续使用机器狗原内部有线网卡。

## 保存两个网络

在 Go2 的 Ubuntu 终端、仓库根目录运行：

```bash
python3 scripts/go2/setup_wifi.py
```

程序通过 sudo 获得 NetworkManager 配置权限，并在本机隐藏提示输入 **13pro 的热点 Wi-Fi 密码**。密码交给本机 NetworkManager 保存，不写入本仓库、终端输出或聊天。脚本执行时应保持 `wlan0` 连接 `CU_tD9c`，可保留外接网线。

- 家庭连接 `CU_tD9c`：自动连接，优先级 100。
- 农场配置 `go2-farm-13pro`：SSID 为 `13pro`，WPA-PSK，DHCP，自动连接，优先级 50。
- 农场配置先以禁用自动连接的状态创建，密码等属性齐全并确认当前仍是家庭连接后才启用。
- 只保存配置，不执行切换、断开、重启网络或修改 `eth0` 的命令。
- 重跑会使用同一个农场配置；若同名配置指向其他用途或存在重名，停止并保留原配置。

优先级在断线、开机或 NetworkManager 选择新连接时起作用，不会为了更高优先级强行切换已连接的网络。在农场没有家庭网络时，会尝试连接开启的 `13pro` 热点。

## 现场验证手机热点

1. iPhone 开启个人热点的“允许其他人加入”。旧无线网卡识别不了热点时开启“最大兼容性”；SSID 必须准确为 `13pro`。
2. 若当前通过家庭 Wi-Fi SSH 操作，先重新接好外接网线，并另开 `ssh unitree@192.168.123.18` 有线会话后测试。
3. 在有线会话中执行实际切换与检查：

```bash
sudo nmcli connection up id go2-farm-13pro ifname wlan0
ip -br -4 addr
nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
```

4. Windows 也加入 `13pro`，用 **上一步 wlan0 新取得的 IP** 新建 SSH 会话。热点可能分配不同网段，不要继续用家庭地址 `192.168.1.230`。
5. 新 SSH 会话正常后再拔外接网线，并运行 `hostname` 验证无线连接。

返回家中，可在已有连接中显式切回：

```bash
sudo nmcli connection up id CU_tD9c ifname wlan0
```

切换会使旧无线 SSH 会话断开，保持有线会话作为配置入口。脚本软件测试不代表已经在 iPhone 热点完成连接、DHCP 或断线重连验证。VLA 服务地址也应随 GPU 工作站在对应网络的 IP 更新。
