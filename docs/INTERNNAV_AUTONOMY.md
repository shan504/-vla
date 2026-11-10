# InternNav 实机闭环状态

当前复现模型仍是 `InternVLA-N1-DualVLN`，按文字指令驱动视觉语言与轨迹双系统。当前/历史彩色图像输入 S2；S1 轨迹分支还需要配套的真实深度。终点参考照片没有直接接入当前服务的现成字段。

## 已验证与尚缺输入

- 用户 4090 已加载完整模型，合成图请求 HTTP 200，11.92 秒返回停止动作。这不证明 S1 轨迹或实机导航已完成。
- 机器狗自带相机已保存有效 1280×720 RGBA 图片。该视频没有同时提供对齐深度。
- D435i 的 USB 枚举与实时 RGB-D 尚未验证；完整轨迹测试应使用同一标定坐标下的 RGB-D，不能直接将自带相机 RGB 和未标定的其他相机深度拼接。
- SDK 状态接收、原生控制驱动和 ROS 里程计桥已准备代码，尚需机器狗端验证。

首先在狗端查询真实可用传感器：

```bash
source /opt/ros/foxy/setup.bash
ROS_DOMAIN_ID=0 timeout 12s /usr/bin/python3 /opt/ros/foxy/bin/ros2 topic list -t --no-daemon
ls -l /dev/video* 2>/dev/null
```

## Python SDK 缺失时的原生后端

`onboard/cpp` 直接链接官方 SDK2 源码自带的架构对应预编译库，固定来源为 `unitreerobotics/unitree_sdk2` 提交 `aaf69cb56c9d5ed63d8b284008ec3a8ab999bec1`。SDK/DDS 库位于项目私有目录，不安装到系统，也不修改现有 Python 环境。

将该官方源码放入 `.tools/unitree_sdk2` 后，从项目根目录构建：

```bash
cmake -S onboard/cpp -B .tools/robot-state-build \
  -DUNITREE_SDK2_DIR="$PWD/.tools/unitree_sdk2" \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++
cmake --build .tools/robot-state-build -j 2
.tools/robot-state-build/go2_state_probe --interface eth0 --seconds 10
```

探针只订阅状态。必须以实际输出核对模式、位置和连续状态接收，不能由编译成功推断控制已通。

原生驱动与已有 ROS 桥使用相同的本机 UDP 8899 `<ff>` 速度协议；默认 dry-run。它还在本机 UDP 8898 输出新鲜 SDK 状态，可作为现有客户端的短程相对里程计来源：

```bash
.tools/robot-state-build/go2_native_driver --interface eth0 --dry-run
# 另一个已加载 Foxy 的系统 Python 3.8 终端：
/usr/bin/python3 onboard/ros_bridge.py --odom-source sdk
```

`sdk_odom` 是 SDK 相对位置估计，不是经过地图定位的全局位姿。ROS 桥保留接收时间年龄，拒绝过期、重复和非法数据；不为未提供的速度坐标系或协方差虚构精度。

驱动默认运行 30 秒，包含等待输入的时间；试运行结束即退出。运动模式须显式选择 `--enable-motion --allowed-modes`，并以已验证的实际机器人就绪模式为准。驱动带有状态/遥控新鲜度检查、遥控接管锁止、0.5 秒命令看门狗、0.15 m/s 与 0.3 rad/s 限幅、3 米累计距离预算。它不自动起身、切换步态或切换服务。收到速度后的年龄保护不等同于发送端时间戳校验。

## 启动闭环的条件

真实对齐 RGB-D 与内参、连续里程计、GPU 模型服务和控制驱动必须同时可用。先运行原有 `go2.robot_client --dry-run` 检查实景 S2/S1 结果、延迟、停止与输入时效，再进入有界的实机试运行。状态探针、模拟数据或一张静态 RGB 图不能代替这些条件。
