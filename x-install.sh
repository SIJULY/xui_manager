#!/bin/bash

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
# 例如: https://.../api/probe/register -> https://.../api/probe/push
PUSH_API="${REGISTER_API/\/register/\/push}"

echo "🚀 开始安装 X-Fusion 推送探针..."
echo "🔑 Token: $TOKEN"
echo "📡 推送地址: $PUSH_API"

# 1. 向面板注册 (这一步是为了让面板知道这台机器存在，如果已存在会忽略)
curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API"
echo ""

# 2. 安装 Python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "📦 安装 Python3..."
    if [ -f /etc/debian_version ]; then apt-get update -y && apt-get install -y python3;
    elif [ -f /etc/redhat-release ]; then yum install -y python3;
    elif [ -f /etc/alpine-release ]; then apk add python3; fi
fi

# 3. 写入 Python 推送脚本
cat > /root/x_fusion_agent.py << EOF
import time, json, os, socket, sys
import urllib.request, urllib.error

# 配置参数
MANAGER_URL = "$PUSH_API"
TOKEN = "$TOKEN"
# 获取本机 IP/域名作为标识
SERVER_URL = "" 

def get_sys_info():
    data = {"token": TOKEN}
    
    # 获取系统信息
    try:
        # CPU
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t1, i1 = sum(fields), fields[3]
        time.sleep(1)
        with open("/proc/stat") as f: fields = [float(x) for x in f.readline().split()[1:5]]
        t2, i2 = sum(fields), fields[3]
        data["cpu_usage"] = round((1 - (i2-i1)/(t2-t1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        
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
        
        # 尝试获取本机公网 IP 用于匹配缓存
        try:
            with urllib.request.urlopen("http://ifconfig.me", timeout=3) as r:
                my_ip = r.read().decode().strip()
                data["server_url"] = f"http://{my_ip}:54322" # 模拟旧格式URL以匹配缓存键
        except:
            pass

    except: pass
    return data

def push_data():
    while True:
        try:
            payload = json.dumps(get_sys_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r: pass
        except Exception as e:
            pass 
        time.sleep(3) 

if __name__ == "__main__":
    push_data()
EOF

# 4. 创建 Systemd 服务
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

# 清理旧的监听进程 (如果存在)
pkill -f mini_probe.py || true

echo "✅ 探针 Agent 已启动！正在向 $PUSH_API 推送数据..."
