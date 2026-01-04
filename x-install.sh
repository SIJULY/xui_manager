#!/bin/bash

# =========================================================
# X-Fusion Panel 探针安装脚本 (Unified Version)
# 支持：手动 SSH 安装 & 面板自动推送安装
# =========================================================

# 1. 参数定义 (支持默认占位符，用于面板自动替换)
# 如果命令行没有传参数，就使用 __TOKEN__ 这种占位符(会被Python替换)
TOKEN="${1:-__TOKEN__}"
REGISTER_API="${2:-__API_URL__}"
SERVER_URL="${3:-__SERVER_URL__}"

# 2. 预检与权限
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ 错误: 请使用 root 权限运行 (sudo -i)"
    exit 1
fi

if [[ "$TOKEN" == "__TOKEN__" ]] && [ -z "$1" ]; then
    echo "❌ 错误: 未检测到 Token，脚本无法运行。"
    echo "用法: bash x-install.sh \"TOKEN\" \"http://面板IP:端口/api/probe/register\""
    exit 1
fi

# 3. API 地址处理
# 自动将 /register 替换为 /push 以获取推送地址
PUSH_API="${REGISTER_API/\/register/\/push}"

# 如果还是占位符(说明没替换成功也没传参)，尝试兜底(很少发生)
if [[ "$PUSH_API" == *"__API_URL__"* ]]; then
    echo "❌ 错误: 无效的 API 地址"
    exit 1
fi

echo "🚀 开始安装 X-Fusion 探针..."
echo "🔑 Token: $TOKEN"
echo "📡 推送接口: $PUSH_API"

# 4. 注册流程 (仅在手动运行时触发)
# 如果 SERVER_URL 是空的 (或者占位符没被替换)，说明是手动运行
if [[ -z "$SERVER_URL" ]] || [[ "$SERVER_URL" == "__SERVER_URL__" ]]; then
    echo "📋 正在向面板注册本机..."
    # 尝试获取本机 IP 作为 Server URL 的一部分
    MY_IP=$(curl -s4 ifconfig.me || echo "127.0.0.1")
    SERVER_URL="http://${MY_IP}:54322" # 生成一个虚拟地址用于标识
    
    # 调用注册接口
    REG_RES=$(curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API")
    echo "   └─ 面板响应: $REG_RES"
else
    echo "✅ 面板自动部署模式，跳过注册。"
fi

# 5. 环境准备
if ! command -v python3 >/dev/null 2>&1; then
    echo "📦 安装 Python3..."
    if [ -f /etc/debian_version ]; then apt-get update -y && apt-get install -y python3;
    elif [ -f /etc/redhat-release ]; then yum install -y python3;
    elif [ -f /etc/alpine-release ]; then apk add python3; fi
fi

# 6. 写入探针逻辑 (这是你 main.py 里经过测试最正确的版本)
cat > /root/x_fusion_agent.py << EOF
import time, json, os, socket, sys
import urllib.request, urllib.error

# 配置参数 (由 Shell 脚本注入)
MANAGER_URL = "$PUSH_API"
TOKEN = "$TOKEN"
SERVER_URL = "$SERVER_URL"

def get_network_stats():
    # 读取 /proc/net/dev 获取总流量
    rx_bytes = 0
    tx_bytes = 0
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:] # 跳过前两行表头
            for line in lines:
                parts = line.split(":")
                if len(parts) < 2: continue
                interface = parts[0].strip()
                if interface == "lo": continue # 跳过本地回环
                
                data = parts[1].split()
                # data[0] 是接收字节(RX), data[8] 是发送字节(TX)
                rx_bytes += int(data[0])
                tx_bytes += int(data[8])
    except: pass
    return rx_bytes, tx_bytes

def get_sys_info():
    data = {"token": TOKEN, "server_url": SERVER_URL}
    try:
        # --- 1. 获取初始网络计数 ---
        net_rx1, net_tx1 = get_network_stats()

        # --- 2. CPU 计算 (利用 sleep 1秒的时间差) ---
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t1, i1 = sum(fields), fields[3]
        
        time.sleep(1) # ✨ 等待 1 秒 ✨
        
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t2, i2 = sum(fields), fields[3]
        
        # --- 3. 获取结束网络计数 & 计算网速 ---
        net_rx2, net_tx2 = get_network_stats()
        
        data["cpu_usage"] = round((1 - (i2-i1)/(t2-t1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        
        # 写入网络数据 (单位：字节)
        data["net_total_in"] = net_rx2
        data["net_total_out"] = net_tx2
        data["net_speed_in"] = net_rx2 - net_rx1 # 1秒内的差值即为速度 B/s
        data["net_speed_out"] = net_tx2 - net_tx1

        # Load
        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])

        # Memory
        with open("/proc/meminfo") as f: lines = f.readlines()
        m = {}
        for line in lines[:5]:
            parts = line.split()
            if len(parts) >= 2: m[parts[0].rstrip(":")] = int(parts[1])
        total = m.get("MemTotal", 1); avail = m.get("MemAvailable", m.get("MemFree", 0))
        data["mem_total"] = round(total / 1024 / 1024, 2)
        data["mem_usage"] = round(((total - avail) / total) * 100, 1)

        # Disk
        st = os.statvfs("/")
        total_d = st.f_blocks * st.f_frsize
        free_d = st.f_bavail * st.f_frsize
        data["disk_total"] = round(total_d / 1024 / 1024 / 1024, 2)
        data["disk_usage"] = round(((total_d - free_d) / total_d) * 100, 1)

        # Uptime
        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        dy = int(u // 86400); hr = int((u % 86400) // 3600); mn = int((u % 3600) // 60)
        data["uptime"] = f"{dy}天 {hr}时 {mn}分"
        
    except Exception as e: 
        pass
    return data

def push_data():
    while True:
        try:
            payload = json.dumps(get_sys_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r: pass
        except Exception as e:
            pass 
        time.sleep(2) # 循环间隔

if __name__ == "__main__":
    push_data()
EOF

# 7. 创建 Systemd 服务
cat > /etc/systemd/system/x-fusion-agent.service << SERVICE_EOF
[Unit]
Description=X-Fusion Probe Agent
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /root/x_fusion_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# 8. 启动服务
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent

# 清理旧进程
pkill -f mini_probe.py || true

echo "✅ 探针 Agent 已启动！正在向 $PUSH_API 推送数据..."
exit 0
