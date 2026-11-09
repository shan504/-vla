#!/usr/bin/env python3
"""Save home/farm Wi-Fi profiles without activating or disconnecting either one."""
import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys


def nmcli(*args, missing_ok=False):
    result = subprocess.run(["nmcli", *args], text=True, capture_output=True)
    if missing_ok and result.returncode == 10:
        return None
    if result.returncode:
        if "802-11-wireless-security.psk" in args:
            raise RuntimeError("NetworkManager 未能保存热点密码；密码未写入日志。")
        raise RuntimeError(result.stderr.strip() or "NetworkManager 操作失败")
    return result.stdout.strip()


def profile_uuid(name, run=nmcli):
    value = run("-g", "connection.uuid", "connection", "show", "id", name, missing_ok=True)
    if value is None:
        return None
    uuids = value.splitlines()
    if len(uuids) != 1:
        raise RuntimeError("存在重名网络配置，停止以避免修改错误配置：" + name)
    return uuids[0]


def active_uuid(interface, run=nmcli):
    rows = run("-t", "-f", "UUID,DEVICE", "connection", "show", "--active")
    return next((row.split(":", 1)[0] for row in rows.splitlines()
                 if row.split(":", 1)[-1] == interface), None)


def configure(password, home="CU_tD9c", hotspot="13pro", interface="wlan0", run=nmcli):
    encoded = password.encode("utf-8")
    if not (8 <= len(encoded) <= 63 or
            (len(encoded) == 64 and all(c in "0123456789abcdefABCDEF" for c in password))):
        raise ValueError("热点密码应为 8–63 字节或 64 位十六进制密钥；尚未修改网络。")
    if run("-g", "GENERAL.TYPE", "device", "show", interface) != "wifi":
        raise RuntimeError(interface + " 不是 NetworkManager 管理的 Wi-Fi 网卡。")
    home_uuid = profile_uuid(home, run)
    if not home_uuid or active_uuid(interface, run) != home_uuid:
        raise RuntimeError("请在 wlan0 仍连接家庭 Wi-Fi 时运行；尚未修改网络。")
    farm_name = "go2-farm-13pro"
    farm_uuid = profile_uuid(farm_name, run)
    if farm_uuid:
        kind = run("-g", "connection.type", "connection", "show", "uuid", farm_uuid)
        ssid = run("-g", "802-11-wireless.ssid", "connection", "show", "uuid", farm_uuid)
        if kind != "802-11-wireless" or ssid != hotspot:
            raise RuntimeError("已有同名配置用途不同，停止以保留原配置。")
        run("connection", "modify", "uuid", farm_uuid, "connection.autoconnect", "no")
    else:
        run("connection", "add", "type", "wifi", "ifname", interface,
            "con-name", farm_name, "ssid", hotspot, "connection.autoconnect", "no")
        farm_uuid = profile_uuid(farm_name, run)
        if not farm_uuid:
            raise RuntimeError("未找到刚创建的热点配置。")
    # Inactive until its credentials and DHCP configuration are complete.
    run("connection", "modify", "uuid", farm_uuid,
        "connection.interface-name", interface,
        "802-11-wireless-security.key-mgmt", "wpa-psk",
        "802-11-wireless-security.psk-flags", "0",
        "802-11-wireless-security.psk", password,
        "ipv4.method", "auto", "ipv6.method", "auto",
        "connection.autoconnect-priority", "50")
    if active_uuid(interface, run) != home_uuid:
        raise RuntimeError("当前无线连接发生变化；农场配置已保存但未启用自动连接，请保持网线检查。")
    run("connection", "modify", "uuid", home_uuid,
        "connection.autoconnect", "yes", "connection.autoconnect-priority", "100")
    run("connection", "modify", "uuid", farm_uuid, "connection.autoconnect", "yes")
    return home_uuid, farm_uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="CU_tD9c")
    parser.add_argument("--hotspot", default="13pro")
    parser.add_argument("--interface", default="wlan0")
    args = parser.parse_args()
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "python3", str(Path(__file__).resolve()), *sys.argv[1:]])
    if not sys.stdin.isatty():
        parser.exit(1, "请从交互式终端运行，以便在本机隐藏输入热点密码。\n")
    password = None
    try:
        password = getpass.getpass("请输入 {} 的 Wi-Fi 热点密码（不回显）: ".format(args.hotspot))
        configure(password, args.home, args.hotspot, args.interface)
        print("已保存：家庭 {} 优先级 100；农场 {} 优先级 50；均自动连接。".format(args.home, args.hotspot))
        print("本脚本只保存配置，没有请求切换或断开网络。")
        print("下次断线/开机时，优先连接可用的家庭网络，否则尝试手机热点。")
        print("农场热点的实际连接仍需验证；换网络后请用新 DHCP 地址 SSH。")
    except (RuntimeError, ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        parser.exit(1, "配置未完成：{}\n".format(exc))
    finally:
        password = None


if __name__ == "__main__":
    main()
