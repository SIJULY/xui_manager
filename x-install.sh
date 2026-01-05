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
PUSH_API="${REGISTER_API/\/register/\/push}"

echo "🚀 开始安装 X-Fusion 全能探针 (v3.4 ARM修复版)..."
echo "🔑 Token: $TOKEN"
echo "📡 推送地址: $PUSH_API"

# 1. 向面板注册
curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API"
echo ""

# 2. 安装必要依赖 (Python3 和 Ping)
echo "📦 检查并安装依赖..."
if [ -f /etc/debian_version ]; then
    apt-get update -y
    command -v python3 >/dev/null 2>&1 || apt-get install -y python3
    command -v ping >/dev/null 2>&1 || apt-get install -y iputils-ping
    # ✨ 确保安装 util-linux 以获得 lscpu 命令
    command -v lscpu >/dev/null 2>&1 || apt-get install -y util-linux
elif [ -f /etc/redhat-release ]; then
    command -v python3 >/dev/null 2>&1 || yum install -y python3
    command -v ping >/dev/null 2>&1 || yum install -y iputils
    command -v lscpu >/dev/null 2>&1 || yum install -y util-linux
elif [ -f /etc/alpine-release ]; then
    command -v python3 >/dev/null 2>&1 || apk add python3
    command -v ping >/dev/null 2>&1 || apk add iputils
    command -v lscpu >/dev/null 2>&1 || apk add util-linux
fi

# 3. 写入 Python 推送脚本
cat > /root/x_fusion_agent.py << 'PYTHON_EOF'
import time, json, os, socket, sys, subprocess, re, platform
import urllib.request, urllib.error
import ssl

MANAGER_URL = "placeholder_url"
TOKEN = "placeholder_token"
SERVER_URL = "" 

# 测速目标
PING_TARGETS = {
    "电信": "202.102.192.68",
    "联通": "112.122.10.26",
    "移动": "211.138.180.2"
}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_cmd_output(cmd):
    try:
        return subprocess.check_output(cmd, shell=True).decode().strip()
    except:
        return "Unknown"

# --- 核心修复：ARM 架构 CPU 识别 ---
def get_static_info():
    info = {"cpu_model": "Unknown", "virt": "Unknown", "arch": "Unknown", "os": "Unknown"}
    try:
        info["arch"] = platform.machine()
        info["os"] = platform.platform()
        
        # 1. 优先尝试读取文件 (x86/AMD 常用)
        found_model = False
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        info["cpu_model"] = line.split(":")[1].strip()
                        found_model = True
                        break
                    if "Hardware" in line: # 部分旧款 ARM
                         info["cpu_model"] = line.split(":")[1].strip()
                         found_model = True
                         break
        
        # 2. ✨✨✨ 重点：如果没找到或者是 Unknown (常见于 ARM/Oracle)，尝试 lscpu 命令
        if not found_model or info["cpu_model"] == "Unknown":
            try:
                lscpu_out = get_cmd_output("lscpu")
                for line in lscpu_out.split('\n'):
                    if "Model name:" in line:
                        info["cpu_model"] = line.split(":")[1].strip()
                        break
            except: pass

        # 3. 获取虚拟化类型
        virt = get_cmd_output("systemd-detect-virt")
        if virt and virt != "none": info["virt"] = virt
        
    except: pass
    return info

STATIC_CACHE = get_static_info()

def get_ping(target):
    try:
        target = target.split("://")[-1].split(":")[0]
        cmd = "ping -c 1 -W 1 " + target
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            out = res.stdout.decode()
            match = re.search(r"time=([\d.]+)", out)
            if match: return int(float(match.group(1)))
    except: pass
    return -1

def get_net():
    r, t = 0, 0
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                cols = line.split(":")
                if len(cols)<2: continue
                if cols[0].strip() == "lo": continue
                parts = cols[1].split()
                if len(parts) >= 9:
                    r += int(parts[0])
                    t += int(parts[8])
    except: pass
    return r, t

def get_info():
    global SERVER_URL
    data = {"token": TOKEN, "static": STATIC_CACHE}
    
    if not SERVER_URL:
        try:
            with urllib.request.urlopen("http://checkip.amazonaws.com", timeout=5, context=ssl_ctx) as r:
                my_ip = r.read().decode().strip()
                SERVER_URL = "http://" + my_ip + ":54322"
        except: pass
    data["server_url"] = SERVER_URL

    try:
        r1, t1 = get_net()
        with open("/proc/stat") as f: 
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]
        
        time.sleep(1)
        
        r2, t2 = get_net()
        with open("/proc/stat") as f: 
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]

        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        data["net_total_in"] = r2
        data["net_total_out"] = t2
        data["net_speed_in"] = r2 - r1
        data["net_speed_out"] = t2 - t1

        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])
        
        with open("/proc/meminfo") as f:
            m = {}
            for l in f:
                p = l.split()
                if len(p) >= 2: m[p[0].rstrip(":")] = int(p[1])
        
        tot = m.get("MemTotal", 1)
        avail = m.get("MemAvailable", m.get("MemFree", 0))
        data["mem_total"] = round(tot / 1024 / 1024, 2)
        data["mem_usage"] = round(((tot - avail) / tot) * 100, 1)
        
        sw_tot = m.get("SwapTotal", 0)
        sw_free = m.get("SwapFree", 0)
        data["swap_total"] = round(sw_tot / 1024 / 1024, 2)
        data["swap_free"] = round(sw_free / 1024 / 1024, 2)

        st = os.statvfs("/")
        dt = st.f_blocks * st.f_frsize
        df = st.f_bavail * st.f_frsize
        data["disk_total"] = round(dt / 1024 / 1024 / 1024, 2)
        data["disk_usage"] = round(((dt - df) / dt) * 100, 1)

        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        d = int(u // 86400)
        h = int((u % 86400) // 3600)
        m = int((u % 3600) // 60)
        data["uptime"] = "%d天 %d时 %d分" % (d, h, m)

        pings = {}
        for k, v in PING_TARGETS.items(): pings[k] = get_ping(v)
        data["pings"] = pings

    except: pass
    return data

def push():
    while True:
        try:
            js = json.dumps(get_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=js, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass
        time.sleep(2)

if __name__ == "__main__":
    push()
PYTHON_EOF

sed -i "s|placeholder_url|$PUSH_API|g" /root/x_fusion_agent.py
sed -i "s|placeholder_token|$TOKEN|g" /root/x_fusion_agent.py

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

systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent

echo "✅ 探针 Agent 安装完成"
