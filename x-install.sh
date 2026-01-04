#!/bin/bash

# =========================================================
# X-Fusion Panel 探针安装脚本 (手动安装适配版)
# =========================================================

# 获取参数
TOKEN="$1"
REGISTER_API="$2"

# 参数校验
if [ -z "$TOKEN" ] || [ -z "$REGISTER_API" ]; then
    echo "❌ 错误: 缺少参数"
    echo "用法: bash x-install.sh \"TOKEN\" \"REGISTER_API_URL\""
    exit 1
fi

# 从注册 API 提取 推送 API (将 /register 替换为 /push)
PUSH_API="${REGISTER_API/\/register/\/push}"

echo "🚀 开始安装 X-Fusion 推送探针..."
echo "🔑 Token: $TOKEN"
echo "📡 推送地址: $PUSH_API"

# 1. 向面板注册 (这一步让面板知道这台机器上线了)
echo "📋 正在注册..."
curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API"
echo ""

# 2. 环境检测与安装 Python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "📦 安装 Python3..."
    if [ -f /etc/debian_version ]; then apt-get update -y && apt-get install -y python3;
    elif [ -f /etc/redhat-release ]; then yum install -y python3;
    elif [ -f /etc/alpine-release ]; then apk add python3; fi
fi

# 3. 写入 Python 探针脚本
# (这里的内容已同步为你 main.py 中最正确的逻辑)
cat > /root/x_fusion_agent.py << EOF
import time, json, os, socket, sys
import urllib.request, urllib.error

# 配置参数 (由 Shell 传入)
MANAGER_URL = "$PUSH_API"
TOKEN = "$TOKEN"

# 尝试获取本机 IP 用于生成 server_url 标识 (与面板逻辑保持一致)
try:
    with urllib.request.urlopen("http://ifconfig.me", timeout=5) as r:
        my_ip = r.read().decode().strip()
        SERVER_URL = "http://" + my_ip + ":54322"
except:
    SERVER_URL = "http://127.0.0.1:54322"

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
                rx_bytes += int(data[0])
                tx_bytes += int(data[8])
    except: pass
    return rx_bytes, tx_bytes

def get_sys_info():
    data = {"token": TOKEN, "server_url": SERVER_URL}
    try:
        # --- 1. 获取初始网络计数 ---
        net_rx1, net_tx1 = get_network_stats()

        # --- 2. CPU 计算 (利用 sleep 1秒的时间差，精确计算) ---
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t1, i1 = sum(fields), fields[3]
        
        time.sleep(1) # ✨ 等待 1 秒 ✨
        
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t2, i2 = sum(fields), fields[3]
        
        # --- 3. 获取结束网络计数 & 计算网速 ---
        net_rx2, net_tx2 = get_network_stats()
        
        # CPU 使用率
        data["cpu_usage"] = round((1 - (i2-i1)/(t2-t1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        
        # 写入网络数据 (累计值 + 瞬时速度)
        data["net_total_in"] = net_rx2
        data["net_total_out"] = net_tx2
        data["net_speed_in"] = net_rx2 - net_rx1 
        data["net_speed_out"] = net_tx2 - net_tx1

        # Load Average
        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])

        # Memory (使用 MemAvailable 获取真实可用内存)
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
            pass # 忽略网络错误，等待下一次循环
        time.sleep(2) # 这里的间隔可以根据需要调整

if __name__ == "__main__":
    push_data()
EOF

# 4. 创建 Systemd 服务 (开机自启)
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

# 5. 启动服务
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent

# 清理可能存在的旧进程
pkill -f mini_probe.py || true

echo "✅ 探针安装完成！数据已开始推送。"
exit 0
