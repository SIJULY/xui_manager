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

echo "🚀 开始安装 X-Fusion 全能探针 (v3.5 极简兼容版)..."
echo "🔑 Token: $TOKEN"
echo "📡 推送地址: $PUSH_API"

# 1. 提升权限 (Root Check)
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    echo "⚠️  当前非 Root 用户，尝试提权..."
    exec sudo bash "$0" "$@"
  else
    echo "❌ 错误: 必须使用 Root 权限运行此脚本"
    exit 1
  fi
fi

# 2. 向面板注册
curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API"
echo ""

# 3. 安装基础依赖 (尽力而为，不强制报错)
echo "📦 正在安装依赖 (python3, ping, lscpu)..."
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux >/dev/null 2>&1
fi

# 4. 写入 Python 推送脚本 (去除所有缩进，防止 EOF 解析错误)
echo "📝 正在写入 Agent 脚本..."
cat > /root/x_fusion_agent.py << 'PYTHON_EOF'
import time, json, os, socket, sys, subprocess, re, platform
import urllib.request, urllib.error
import ssl

# 这些变量会在下面被 sed 替换
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
        # 优先读取 lscpu (Shell命令最准)
        try:
            out = subprocess.check_output("lscpu", shell=True).decode()
            for line in out.split("\n"):
                if "Model name:" in line:
                    return line.split(":")[1].strip()
        except: pass
        
        # 其次读取文件
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line: return line.split(":")[1].strip()
                if "Hardware" in line: return line.split(":")[1].strip()
    except: pass
    return model

# 缓存静态信息
STATIC_CACHE = {
    "cpu_model": get_cpu_model(),
    "arch": platform.machine(),
    "os": platform.platform(),
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
        # 读取流量和负载
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
            r, t = 0, 0
            for l in lines:
                cols = l.split(":")
                if len(cols)<2: continue
                parts = cols[1].split()
                if len(parts)>=9 and cols[0].strip() != "lo":
                    r += int(parts[0])
                    t += int(parts[8])
        
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]
        
        time.sleep(1)
        
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]
            
        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        data["net_speed_in"] = 0 # 暂不计算瞬时速度以简化
        data["net_total_in"] = r
        data["net_total_out"] = t

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
        d = int(u // 86400)
        h = int((u % 86400) // 3600)
        m = int((u % 3600) // 60)
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
        time.sleep(2)

if __name__ == "__main__":
    push()
PYTHON_EOF

# 5. 替换脚本中的变量 (配置你的面板地址和Token)
echo "⚙️  配置参数..."
sed -i "s|placeholder_url|$PUSH_API|g" /root/x_fusion_agent.py
sed -i "s|placeholder_token|$TOKEN|g" /root/x_fusion_agent.py

# 6. 创建 Systemd 服务
echo "🔧 创建系统服务..."
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

# 7. 启动服务
echo "✅ 启动服务..."
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent

echo "🎉 探针 Agent 安装完成，服务已启动！"
echo "   日志查看: systemctl status x-fusion-agent"
