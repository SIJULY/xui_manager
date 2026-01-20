#!/bin/bash

# ==========================================
# X-Fusion Panel 单机安装脚本 (v3.8 独立版)
# ==========================================

# 1. 获取参数
TOKEN="$1"
REGISTER_API="$2"

# 可选参数：自定义测速点 (如果未提供，使用默认值)
PING_CT="${3:-202.102.192.68}"  # 电信
PING_CU="${4:-112.122.10.26}"   # 联通
PING_CM="${5:-211.138.180.2}"   # 移动

# 2. 参数校验
if [ -z "$TOKEN" ] || [ -z "$REGISTER_API" ]; then
    echo "❌ 错误: 缺少必要参数"
    echo "用法: curl ... | bash -s -- \"TOKEN\" \"REGISTER_API_URL\" [CT_IP] [CU_IP] [CM_IP]"
    exit 1
fi

# 3. 计算 Push API 地址 (将 /register 替换为 /push)
# 例如: https://.../api/probe/register -> https://.../api/probe/push
PUSH_API="${REGISTER_API/register/push}"

echo "🚀 开始安装 X-Fusion 全能探针 (v3.8)..."
echo "🔑 Token: $TOKEN"
echo "📡 注册地址: $REGISTER_API"
echo "📡 推送地址: $PUSH_API"
echo "🎯 测速目标: $PING_CT / $PING_CU / $PING_CM"

# 4. 向面板发起注册请求 (主动注册)
echo "☁️ 正在向面板注册服务器..."
REG_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" -d "{\"token\":\"$TOKEN\"}" "$REGISTER_API")
echo "   服务端响应: $REG_RESPONSE"
echo ""

# 5. 安装系统依赖
echo "📦 检查并安装依赖..."
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux >/dev/null 2>&1
fi

# 6. 写入 Python 探针脚本 (v3.8 核心逻辑)
# 使用 "PYTHON_EOF" (双引号) 防止 Shell 变量在 cat 阶段被提前展开
cat > /root/x_fusion_agent.py << "PYTHON_EOF"
import time, json, os, socket, sys, subprocess, re, platform
import urllib.request, urllib.error
import ssl

# 占位符，稍后由 sed 替换
MANAGER_URL = "__MANAGER_URL__"
TOKEN = "__TOKEN__"
SERVER_URL = "__SERVER_URL__"

PING_TARGETS = {
    "电信": "__PING_CT__",
    "联通": "__PING_CU__",
    "移动": "__PING_CM__"
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

def get_os_distro():
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip("\"")
    except: pass
    try: return platform.platform()
    except: return "Linux (Unknown)"

STATIC_CACHE = {
    "cpu_model": get_cpu_model(),
    "arch": platform.machine(),
    "os": get_os_distro(),
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

# 获取网卡流量辅助函数
def get_network_bytes():
    r, t = 0, 0
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
            for l in lines:
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
        # 第一次采样
        net_in_1, net_out_1 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]
        
        time.sleep(1)
        
        # 第二次采样
        net_in_2, net_out_2 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]
            
        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        
        # 实时网速计算 (差值)
        data["net_speed_in"] = net_in_2 - net_in_1
        data["net_speed_out"] = net_out_2 - net_out_1
        data["net_total_in"] = net_in_2
        data["net_total_out"] = net_out_2

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

# 7. 配置替换 (使用 sed 注入真实参数)
echo "🔧 配置 Agent 参数..."
sed -i "s|__MANAGER_URL__|$PUSH_API|g" /root/x_fusion_agent.py
sed -i "s|__TOKEN__|$TOKEN|g" /root/x_fusion_agent.py
# 注入测速点
sed -i "s|__PING_CT__|$PING_CT|g" /root/x_fusion_agent.py
sed -i "s|__PING_CU__|$PING_CU|g" /root/x_fusion_agent.py
sed -i "s|__PING_CM__|$PING_CM|g" /root/x_fusion_agent.py

# 8. 创建并启动服务
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

echo "✅ 探针安装完成，服务已启动！"
echo "📊 请回到面板查看数据 (约10秒内上线)"
