#!/bin/bash

TOKEN="$1"
REGISTER_API="$2"

if [ -z "$TOKEN" ] || [ -z "$REGISTER_API" ]; then
    echo "❌ 错误: 缺少参数"
    echo "用法: bash x-install.sh \"TOKEN\" \"REGISTER_API_URL\""
    exit 1
fi

PUSH_API="${REGISTER_API/\/register/\/push}"

echo "🚀 开始安装 X-Fusion 全能探针 (v3.6 网速修复版)..."
echo "🔑 Token: $TOKEN"
echo "📡 推送地址: $PUSH_API"

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null && exec sudo bash "$0" "$@" || { echo "Root required"; exit 1; }
fi

curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API"
echo ""

if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux >/dev/null 2>&1
fi

echo "📝 正在写入 Agent 脚本..."
cat > /root/x_fusion_agent.py << 'PYTHON_EOF'
import time, json, os, socket, sys, subprocess, re, platform
import urllib.request, urllib.error
import ssl

MANAGER_URL = "placeholder_url"
TOKEN = "placeholder_token"
SERVER_URL = ""

PING_TARGETS = {
"电信": "202.102.192.68",
"联通": "112.122.10.26",
"移动": "211.138.180.2"
}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def get_cpu_model():
    model = "Unknown"
    try:
        try:
            out = subprocess.check_output("lscpu", shell=True).decode()
            for line in out.split("\n"):
                if "Model name:" in line: return line.split(":")[1].strip()
        except: pass
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line: return line.split(":")[1].strip()
                if "Hardware" in line: return line.split(":")[1].strip()
    except: pass
    return model

def get_os_name():
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip('"')
    except: pass
    return platform.system() + " " + platform.release()

STATIC_CACHE = {
    "cpu_model": get_cpu_model(),
    "arch": platform.machine(),
    "os": get_os_name(),
    "virt": "Unknown"
}
try:
    v = subprocess.check_output("systemd-detect-virt", shell=True).decode().strip()
    if v and v != "none": STATIC_CACHE["virt"] = v
except: pass

def get_ping(target):
    try:
        ip = target.split("://")[-1].split(":")[0]
        cmd = "ping -c 1 -W 1 " + ip
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            match = re.search(r"time=([\d.]+)", res.stdout.decode())
            if match: return int(float(match.group(1)))
    except: pass
    return -1

def read_net():
    r, t = 0, 0
    try:
        with open("/proc/net/dev") as f:
            for l in f.readlines()[2:]:
                cols = l.split(":")
                if len(cols)<2: continue
                parts = cols[1].split()
                if len(parts)>=9 and cols[0].strip() != "lo":
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
        r1, t1 = read_net()
        with open("/proc/stat") as f: 
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]
        
        time.sleep(1)
        
        r2, t2 = read_net()
        with open("/proc/stat") as f: 
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]
            
        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        data["net_speed_in"] = r2 - r1
        data["net_speed_out"] = t2 - t1
        data["net_total_in"] = r2
        data["net_total_out"] = t2

        with open("/proc/loadavg") as f: data["load_1"] = float(f.read().split()[0])
        
        with open("/proc/meminfo") as f:
            m = {}
            for l in f:
                p = l.split()
                if len(p)>=2: m[p[0].rstrip(":")] = int(p[1])
        
        tot = m.get("MemTotal", 1)
        avail = m.get("MemAvailable", m.get("MemFree", 0))
        data["mem_total"] = round(tot/1024/1024, 2)
        data["mem_usage"] = round(((tot-avail)/tot)*100, 1)
        data["swap_total"] = round(m.get("SwapTotal", 0)/1024/1024, 2)
        data["swap_free"] = round(m.get("SwapFree", 0)/1024/1024, 2)

        st = os.statvfs("/")
        data["disk_total"] = round((st.f_blocks * st.f_frsize)/1024/1024/1024, 2)
        free = st.f_bavail * st.f_frsize
        total = st.f_blocks * st.f_frsize
        data["disk_usage"] = round(((total-free)/total)*100, 1)

        with open("/proc/uptime") as f: u = float(f.read().split()[0])
        d = int(u // 86400); h = int((u % 86400) // 3600); m = int((u % 3600) // 60)
        data["uptime"] = "%d天 %d时 %d分" % (d, h, m)

        data["pings"] = {k: get_ping(v) for k, v in PING_TARGETS.items()}

    except: pass
    return data

def push():
    while True:
        try:
            js = json.dumps(get_info()).encode("utf-8")
            req = urllib.request.Request(MANAGER_URL, data=js, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as r: pass
        except: pass
        time.sleep(1)

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
