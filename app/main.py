import json
import os
import uuid
import base64
import asyncio
import logging
import requests
import urllib3
import shutil
import re
import sys
import socket
import random
import pyotp
import qrcode
import time
import io
import paramiko
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor # ✅ 修正
from apscheduler.schedulers.asyncio import AsyncIOScheduler # ✅ 修正
from urllib.parse import urlparse, quote
from nicegui import ui, run, app, Client
from fastapi import Response, Request
from fastapi.responses import RedirectResponse

IP_GEO_CACHE = {}

# ✨✨✨ 定义全局进程池变量 ✨✨✨
PROCESS_POOL = None 

# ✨✨✨ [新增] 同步 Ping 函数 (将由独立进程执行) ✨✨✨
def sync_ping_worker(host, port):
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3) # 3秒超时
        sock.connect((host, int(port)))
        sock.close()
        return int((time.time() - start) * 1000)
    except:
        return -1

# ================= 辅助：全局 GeoIP 和 智能命名逻辑 =================

# 从 IP 获取地理信息 (全局版)
def fetch_geo_from_ip(host):
    try:
        clean_host = host.split('://')[-1].split(':')[0]
        # 跳过内网
        if clean_host.startswith('192.168.') or clean_host.startswith('10.') or clean_host == '127.0.0.1':
            return None
        if clean_host in IP_GEO_CACHE:
            return IP_GEO_CACHE[clean_host]
        
        # 请求 ip-api (lang=zh-CN)
        with requests.Session() as s:
            url = f"http://ip-api.com/json/{clean_host}?lang=zh-CN&fields=status,lat,lon,country"
            r = s.get(url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 'success':
                    result = (data['lat'], data['lon'], data['country'])
                    IP_GEO_CACHE[clean_host] = result
                    return result
    except: 
        pass
    return None


# ================= 全局辅助：超级坐标库 =================
LOCATION_COORDS = {
    '🇨🇳': (35.86, 104.19), 'China': (35.86, 104.19), '中国': (35.86, 104.19),
    '🇭🇰': (22.31, 114.16), 'HK': (22.31, 114.16), 'Hong Kong': (22.31, 114.16), '香港': (22.31, 114.16),
    '🇹🇼': (23.69, 120.96), 'TW': (23.69, 120.96), 'Taiwan': (23.69, 120.96), '台湾': (23.69, 120.96),
    '🇯🇵': (36.20, 138.25), 'JP': (36.20, 138.25), 'Japan': (36.20, 138.25), '日本': (36.20, 138.25),
    'Tokyo': (35.68, 139.76), '东京': (35.68, 139.76), 'Osaka': (34.69, 135.50), '大阪': (34.69, 135.50),
    '🇸🇬': (1.35, 103.81), 'SG': (1.35, 103.81), 'Singapore': (1.35, 103.81), '新加坡': (1.35, 103.81),
    '🇰🇷': (35.90, 127.76), 'KR': (35.90, 127.76), 'Korea': (35.90, 127.76), '韩国': (35.90, 127.76),
    'Seoul': (37.56, 126.97), '首尔': (37.56, 126.97),
    '🇮🇳': (20.59, 78.96), 'IN': (20.59, 78.96), 'India': (20.59, 78.96), '印度': (20.59, 78.96),
    '🇮🇩': (-0.78, 113.92), 'ID': (-0.78, 113.92), 'Indonesia': (-0.78, 113.92), '印尼': (-0.78, 113.92),
    '🇲🇾': (4.21, 101.97), 'MY': (4.21, 101.97), 'Malaysia': (4.21, 101.97), '马来西亚': (4.21, 101.97),
    '🇹🇭': (15.87, 100.99), 'TH': (15.87, 100.99), 'Thailand': (15.87, 100.99), '泰国': (15.87, 100.99),
    'Bangkok': (13.75, 100.50), '曼谷': (13.75, 100.50),
    '🇻🇳': (14.05, 108.27), 'VN': (14.05, 108.27), 'Vietnam': (14.05, 108.27), '越南': (14.05, 108.27),
    '🇵🇭': (12.87, 121.77), 'PH': (12.87, 121.77), 'Philippines': (12.87, 121.77), '菲律宾': (12.87, 121.77),
    '🇮🇱': (31.04, 34.85), 'IL': (31.04, 34.85), 'Israel': (31.04, 34.85), '以色列': (31.04, 34.85),
    '🇹🇷': (38.96, 35.24), 'TR': (38.96, 35.24), 'Turkey': (38.96, 35.24), '土耳其': (38.96, 35.24),
    '🇦🇪': (23.42, 53.84), 'AE': (23.42, 53.84), 'UAE': (23.42, 53.84), '阿联酋': (23.42, 53.84),
    'Dubai': (25.20, 55.27), '迪拜': (25.20, 55.27),
    '🇺🇸': (37.09, -95.71), 'US': (37.09, -95.71), 'USA': (37.09, -95.71), 'United States': (37.09, -95.71), '美国': (37.09, -95.71),
    'San Jose': (37.33, -121.88), '圣何塞': (37.33, -121.88), 'Los Angeles': (34.05, -118.24), '洛杉矶': (34.05, -118.24),
    'Phoenix': (33.44, -112.07), '凤凰城': (33.44, -112.07),
    '🇨🇦': (56.13, -106.34), 'CA': (56.13, -106.34), 'Canada': (56.13, -106.34), '加拿大': (56.13, -106.34),
    '🇧🇷': (-14.23, -51.92), 'BR': (-14.23, -51.92), 'Brazil': (-14.23, -51.92), '巴西': (-14.23, -51.92),
    '🇲🇽': (23.63, -102.55), 'MX': (23.63, -102.55), 'Mexico': (23.63, -102.55), '墨西哥': (23.63, -102.55),
    '🇨🇱': (-35.67, -71.54), 'CL': (-35.67, -71.54), 'Chile': (-35.67, -71.54), '智利': (-35.67, -71.54),
    '🇦🇷': (-38.41, -63.61), 'AR': (-38.41, -63.61), 'Argentina': (-38.41, -63.61), '阿根廷': (-38.41, -63.61),
    '🇬🇧': (55.37, -3.43), 'UK': (55.37, -3.43), 'United Kingdom': (55.37, -3.43), '英国': (55.37, -3.43),
    'London': (51.50, -0.12), '伦敦': (51.50, -0.12),
    '🇩🇪': (51.16, 10.45), 'DE': (51.16, 10.45), 'Germany': (51.16, 10.45), '德国': (51.16, 10.45),
    'Frankfurt': (50.11, 8.68), '法兰克福': (50.11, 8.68),
    '🇫🇷': (46.22, 2.21), 'FR': (46.22, 2.21), 'France': (46.22, 2.21), '法国': (46.22, 2.21),
    'Paris': (48.85, 2.35), '巴黎': (48.85, 2.35),
    '🇳🇱': (52.13, 5.29), 'NL': (52.13, 5.29), 'Netherlands': (52.13, 5.29), '荷兰': (52.13, 5.29),
    'Amsterdam': (52.36, 4.90), '阿姆斯特丹': (52.36, 4.90),
    '🇷🇺': (61.52, 105.31), 'RU': (61.52, 105.31), 'Russia': (61.52, 105.31), '俄罗斯': (61.52, 105.31),
    'Moscow': (55.75, 37.61), '莫斯科': (55.75, 37.61),
    '🇮🇹': (41.87, 12.56), 'IT': (41.87, 12.56), 'Italy': (41.87, 12.56), '意大利': (41.87, 12.56),
    'Milan': (45.46, 9.19), '米兰': (45.46, 9.19),
    '🇪🇸': (40.46, -3.74), 'ES': (40.46, -3.74), 'Spain': (40.46, -3.74), '西班牙': (40.46, -3.74),
    'Madrid': (40.41, -3.70), '马德里': (40.41, -3.70),
    '🇸🇪': (60.12, 18.64), 'SE': (60.12, 18.64), 'Sweden': (60.12, 18.64), '瑞典': (60.12, 18.64),
    'Stockholm': (59.32, 18.06), '斯德哥尔摩': (59.32, 18.06),
    '🇨🇭': (46.81, 8.22), 'CH': (46.81, 8.22), 'Switzerland': (46.81, 8.22), '瑞士': (46.81, 8.22),
    'Zurich': (47.37, 8.54), '苏黎世': (47.37, 8.54),
    '🇦🇺': (-25.27, 133.77), 'AU': (-25.27, 133.77), 'Australia': (-25.27, 133.77), '澳大利亚': (-25.27, 133.77), '澳洲': (-25.27, 133.77),
    'Sydney': (-33.86, 151.20), '悉尼': (-33.86, 151.20),
    '🇿🇦': (-30.55, 22.93), 'ZA': (-30.55, 22.93), 'South Africa': (-30.55, 22.93), '南非': (-30.55, 22.93),
    'Johannesburg': (-26.20, 28.04), '约翰内斯堡': (-26.20, 28.04),
}

def get_coords_from_name(name):
    for k in sorted(LOCATION_COORDS.keys(), key=len, reverse=True):
        if k in name: return LOCATION_COORDS[k]
    return None

# ================= 全局变量区 =================
IP_GEO_CACHE = {}
# ✨ 新增：存储仪表盘 UI 元素的引用，让后台能控制前台
DASHBOARD_REFS = {
    'servers': None, 'nodes': None, 'traffic': None, 'subs': None,
    'bar_chart': None, 'pie_chart': None, 'stat_up': None, 'stat_down': None, 'stat_avg': None,
    'map': None, 'map_info': None
}

# ================= 全局 DNS 缓存 (支持静默更新) ======================
DNS_CACHE = {}
DNS_WAITING_LABELS = {} # ✨ 新增：存储等待 DNS 结果的 UI 标签引用

async def _resolve_dns_bg(host):
    """后台线程池解析 DNS，解析完自动刷新所有绑定的 UI 标签"""
    try:
        # 放到后台线程去跑，绝对不卡主界面
        ip = await run.io_bound(socket.gethostbyname, host)
        DNS_CACHE[host] = ip
        
        # ✨✨✨ 核心逻辑：解析完成了，通知前台变身！ ✨✨✨
        if host in DNS_WAITING_LABELS:
            for label in DNS_WAITING_LABELS[host]:
                try:
                    # 检查元素是否还活着 (防止切页后报错)
                    if not label.is_deleted:
                        label.set_text(ip) # 瞬间变成 IP
                except: pass
            
            # 通知完了就清空，释放内存
            del DNS_WAITING_LABELS[host]
            
    except: 
        DNS_CACHE[host] = "failed" # 标记失败，防止反复解析

def get_real_ip_display(url):
    """
    非阻塞获取 IP：
    1. 有缓存 -> 直接返回 IP
    2. 没缓存 -> 先返回域名，同时偷偷启动后台解析任务
    """
    try:
        # 提取域名/IP
        host = url.split('://')[-1].split(':')[0]
        
        # 1. 如果本身就是 IP，直接返回
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
            return host

        # 2. 查缓存
        if host in DNS_CACHE:
            val = DNS_CACHE[host]
            return val if val != "failed" else host
        
        # 3. 没缓存？(系统刚启动)
        # 启动后台任务，并立即返回域名占位
        asyncio.create_task(_resolve_dns_bg(host))
        return host 
        
    except:
        return url

def bind_ip_label(url, label):
    """
    ✨ 新增辅助函数：将 UI Label 绑定到 DNS 监听列表
    用法：在创建 ui.label 后调用 bind_ip_label(url, label)
    """
    try:
        host = url.split('://')[-1].split(':')[0]
        # 如果已经解析过，或者本身是 IP，就不需要监听了
        import re
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host): return
        if host in DNS_CACHE: return
        
        # 加入监听列表
        if host not in DNS_WAITING_LABELS: DNS_WAITING_LABELS[host] = []
        DNS_WAITING_LABELS[host].append(label)
    except: pass

# ================= 获取国旗  =====================
def get_flag_for_country(country_name):
    for k, v in AUTO_COUNTRY_MAP.items():
        if k in country_name:
            return v 
    return f"🏳️ {country_name}"

# ✨✨✨ [逻辑修正] 自动给名称添加国旗 ✨✨✨
async def auto_prepend_flag(name, url):
    """
    检查名字是否已经包含任意已知国旗。
    - 如果包含：直接返回原名（尊重用户填写或面板自带的国旗）。
    - 如果不包含：根据 IP 归属地自动添加。
    """
    if not name: return name

    # 1. 遍历所有已知国旗，检查名称中是否已存在
    # AUTO_COUNTRY_MAP 的值格式如 "🇺🇸 美国", 我们只取空格前的 emoji
    for v in AUTO_COUNTRY_MAP.values():
        flag_icon = v.split(' ')[0] # 提取 🇺🇸
        if flag_icon in name:
            # logger.info(f"名称 '{name}' 已包含国旗 {flag_icon}，跳过自动添加")
            return name

    # 2. 如果没有国旗，则进行 GeoIP 查询
    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, url)
        if not geo_info: 
            return name # 查不到 IP 信息，原样返回
        
        country_name = geo_info[2]
        flag_group = get_flag_for_country(country_name) 
        flag_icon = flag_group.split(' ')[0] 
        
        # 再次确认（防止 GeoIP 返回的国旗就是名字里有的，虽然上面已经过滤过一次）
        if flag_icon in name:
            return name
            
        return f"{flag_icon} {name}"
    except Exception as e:
        return name

# ✨✨✨ 智能命名核心逻辑 ✨✨✨
async def generate_smart_name(server_conf):
    """尝试获取面板节点名，获取不到则用 GeoIP+序号"""
    # 1. 尝试连接面板获取节点名
    try:
        mgr = get_manager(server_conf)
        inbounds = await run_in_bg_executor(mgr.get_inbounds)
        if inbounds and len(inbounds) > 0:
            # 优先找一个有备注的节点
            for node in inbounds:
                if node.get('remark'):
                    # 注意：这里直接返回面板的 remark，不加处理
                    # 后续会交给 auto_prepend_flag 统一处理国旗
                    return node['remark'] 
    except: pass

    # 2. 尝试 GeoIP 命名 (如果面板连不上)
    try:
        geo_info = await run.io_bound(fetch_geo_from_ip, server_conf['url'])
        if geo_info:
            country_name = geo_info[2]
            flag_prefix = get_flag_for_country(country_name) # 这里自带国旗，如 "🇺🇸 美国"
            
            # 计算序号
            count = 1
            for s in SERVERS_CACHE:
                if s.get('name', '').startswith(flag_prefix):
                    count += 1
            return f"{flag_prefix}-{count}"
    except: pass

    # 3. 兜底
    return f"Server-{len(SERVERS_CACHE) + 1}"


# ================= SSH 全局配置区域  =================
GLOBAL_SSH_KEY_FILE = 'data/global_ssh_key'

def load_global_key():
    if os.path.exists(GLOBAL_SSH_KEY_FILE):
        with open(GLOBAL_SSH_KEY_FILE, 'r') as f: return f.read()
    return ""

def save_global_key(content):
    with open(GLOBAL_SSH_KEY_FILE, 'w') as f: f.write(content)

def open_global_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl'):
        ui.label('🔐 全局 SSH 密钥设置').classes('text-lg font-bold')
        ui.label('当服务器未单独配置密钥时，将默认使用此私钥连接。').classes('text-xs text-gray-400')
        key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----', value=load_global_key()).classes('w-full h-64 font-mono text-xs').props('outlined')
        ui.button('保存全局密钥', on_click=lambda: [save_global_key(key_input.value), d.close(), safe_notify('全局密钥已保存', 'positive')]).classes('w-full bg-slate-900 text-white')
    d.open()

# ================= 探针安装脚本 (引号修复版) =================
PROBE_INSTALL_SCRIPT = r"""
bash -c '
# 1. 智能判断 Root
if [ "$(id -u)" -eq 0 ]; then
    CMD_PREFIX=""
else
    if command -v sudo >/dev/null 2>&1; then
        CMD_PREFIX="sudo -i"
    else
        echo "Root required"
        exit 1
    fi
fi

$CMD_PREFIX bash -s << "EOF"
    export DEBIAN_FRONTEND=noninteractive
    
    # 2. 安装 Python3
    if ! command -v python3 >/dev/null 2>&1; then
        if [ -f /etc/debian_version ]; then
            apt-get update -y --allow-releaseinfo-change || true
            apt-get install -y python3 || true
        elif [ -f /etc/redhat-release ]; then
            yum install -y python3 || true
        elif [ -f /etc/alpine-release ]; then
            apk add python3 || true
        fi
    fi

    # 3. 写入探针 (✨关键修改：TOKEN 使用双引号，避免与外层单引号冲突✨)
    cat > /root/mini_probe.py << 'PYTHON_EOF'
import http.server,json,subprocess,sys
PORT=54322; TOKEN="sijuly_probe_token"
class H(http.server.BaseHTTPRequestHandler):
 def do_GET(s):
  if s.path!=f"/status?token={TOKEN}": s.send_response(403); s.end_headers(); return
  try:
   with open("/proc/loadavg") as f: l=f.read().split()[0]
   with open("/proc/meminfo") as f: m=f.readlines(); mt=int(m[0].split()[1]); ma=int(m[2].split()[1]); mu=round((mt-ma)/mt*100,1)
   try: d=int(subprocess.check_output(["df","-h","/"]).decode().split("\n")[1].split()[-2].strip("%"))
   except: d=0
   with open("/proc/uptime") as f: u=float(f.read().split()[0]); dy=int(u//86400); hr=int((u%86400)//3600)
   dat={"status":"online","load":l,"mem":mu,"disk":d,"uptime":f"{dy}d {hr}h"}
   s.send_response(200); s.send_header("Content-type","application/json"); s.end_headers(); s.wfile.write(json.dumps(dat).encode())
  except: s.send_response(500)
 def log_message(s,f,*a): pass
if __name__=="__main__":
 try: http.server.HTTPServer(("0.0.0.0",PORT),H).serve_forever()
 except: pass
PYTHON_EOF

    # 4. 重启进程
    pkill -f mini_probe.py || true
    nohup python3 /root/mini_probe.py >/dev/null 2>&1 &

    # 5. 防火墙
    if command -v iptables >/dev/null; then iptables -I INPUT -p tcp --dport 54322 -j ACCEPT || true; fi
    if command -v ufw >/dev/null; then ufw allow 54322/tcp || true; fi
    if command -v firewall-cmd >/dev/null; then firewall-cmd --zone=public --add-port=54322/tcp --permanent && firewall-cmd --reload || true; fi
    
    echo "Install sequence completed"
    exit 0
EOF
'
"""

# ================= 强制日志实时输出 =================
sys.stdout.reconfigure(line_buffering=True)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s', 
    datefmt='%H:%M:%S',
    force=True,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("XUI_Manager")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("nicegui").setLevel(logging.INFO)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 性能调优配置 =================
# 1. 后台专用线程池 (处理 90+ 服务器同步)
BG_EXECUTOR = ThreadPoolExecutor(max_workers=20)
# 2. 限制后台并发数
SYNC_SEMAPHORE = asyncio.Semaphore(15) 


LAST_AUTO_SYNC_TIME = 0
SYNC_COOLDOWN_SECONDS = 300  # 冷却时间：300秒（5分钟）

# ================= 配置区域 =================
CONFIG_FILE = 'data/servers.json'
SUBS_FILE = 'data/subscriptions.json'
NODES_CACHE_FILE = 'data/nodes_cache.json'
ADMIN_CONFIG_FILE = 'data/admin_config.json'

# ✨✨✨ 自动注册密钥 (优先从环境变量获取) ✨✨✨
AUTO_REGISTER_SECRET = os.getenv('XUI_SECRET_KEY', 'sijuly_secret_key_default')

ADMIN_USER = os.getenv('XUI_USERNAME', 'admin')
ADMIN_PASS = os.getenv('XUI_PASSWORD', 'admin')

SERVERS_CACHE = []
SUBS_CACHE = []
NODES_DATA = {}
ADMIN_CONFIG = {}
# ================= 智能分组配置  =================
# 移除了容易与单词冲突的2字母缩写 (如 CL 冲突 Oracle)
AUTO_COUNTRY_MAP = {
    '🇭🇰': '🇭🇰 香港', 'HK': '🇭🇰 香港', '香港': '🇭🇰 香港',
    '🇹🇼': '🇹🇼 台湾', 'TW': '🇹🇼 台湾', '台湾': '🇹🇼 台湾',
    '🇯🇵': '🇯🇵 日本', 'JP': '🇯🇵 日本', '日本': '🇯🇵 日本',
    '🇸🇬': '🇸🇬 新加坡', 'SG': '🇸🇬 新加坡', '新加坡': '🇸🇬 新加坡',
    '🇺🇸': '🇺🇸 美国', '美国': '🇺🇸 美国', # 移除 US 防止冲突
    '🇰🇷': '🇰🇷 韩国', 'KR': '🇰🇷 韩国', '首尔': '🇰🇷 韩国', '春川': '🇰🇷 韩国',
    '🇬🇧': '🇬🇧 英国', 'UK': '🇬🇧 英国', '伦敦': '🇬🇧 英国',
    '🇩🇪': '🇩🇪 德国', 'DE': '🇩🇪 德国', '法兰克福': '🇩🇪 德国',
    '🇫🇷': '🇫🇷 法国', 'FR': '🇫🇷 法国', '巴黎': '🇫🇷 法国',
    '🇦🇺': '🇦🇺 澳大利亚', 'AU': '🇦🇺 澳大利亚', '悉尼': '🇦🇺 澳大利亚',
    '🇨🇦': '🇨🇦 加拿大', '加拿大': '🇨🇦 加拿大', # 移除 CA
    '🇮🇳': '🇮🇳 印度', 'IN': '🇮🇳 印度', '海得拉巴': '🇮🇳 印度',
    '🇮🇩': '🇮🇩 印尼', 'ID': '🇮🇩 印尼', '巴淡': '🇮🇩 印尼',
    '🇧🇷': '🇧🇷 巴西', 'BR': '🇧🇷 巴西',
    '🇳🇱': '🇳🇱 荷兰', 'NL': '🇳🇱 荷兰', '阿姆斯特丹': '🇳🇱 荷兰',
    '🇸🇪': '🇸🇪 瑞典', 'SE': '🇸🇪 瑞典', '斯德哥尔摩': '🇸🇪 瑞典',
    '🇨🇭': '🇨🇭 瑞士', 'CH': '🇨🇭 瑞士', '苏黎世': '🇨🇭 瑞士',
    '🇦🇪': '🇦🇪 阿联酋', '迪拜': '🇦🇪 阿联酋', '阿布扎比': '🇦🇪 阿联酋',
    '🇹🇷': '🇹🇷 土耳其', 'TR': '🇹🇷 土耳其',
    '🇮🇹': '🇮🇹 意大利', 'IT': '🇮🇹 意大利', '米兰': '🇮🇹 意大利',
    '🇨🇱': '🇨🇱 智利', '智利': '🇨🇱 智利', # 移除 CL (冲突 Oracle)
    '🇪🇸': '🇪🇸 西班牙', 'ES': '🇪🇸 西班牙', '马德里': '🇪🇸 西班牙',
    '🇲🇽': '🇲🇽 墨西哥', 'MX': '🇲🇽 墨西哥',
    '🇮🇱': '🇮🇱 以色列', 'IL': '🇮🇱 以色列',
    '🇷🇺': '🇷🇺 俄罗斯', 'RU': '🇷🇺 俄罗斯',
}

def detect_country_group(name, server_config=None):
    # 1. ✨ 最高优先级：手动设置的分组 ✨
    if server_config:
        saved_group = server_config.get('group')
        # 只有当分组有内容，且不是那些“无意义”的默认分组时，才强制生效
        # ⚠️ 关键修改：如果手动设为 '其他地区'，我们认为这是无效分类，允许继续走下面的智能识别
        if saved_group and saved_group.strip() and saved_group not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区', '其他地区']:
            # 尝试标准化 (输入 "美国" -> "🇺🇸 美国")
            for v in AUTO_COUNTRY_MAP.values():
                if saved_group in v or v in saved_group:
                    return v 
            return saved_group

    # 2. ✨✨✨ 第二优先级：看图识字 (国旗) + 关键字 ✨✨✨
    name_upper = name.upper()
    for key, val in AUTO_COUNTRY_MAP.items():
        # A. 找关键字
        if key in name_upper:
            return val
        
        # B. 找国旗 (比如名字里有 🇺🇸)
        try:
            flag_icon = val.split(' ')[0]
            if flag_icon and flag_icon in name:
                return val
        except:
            continue

    # 3. 第三优先级：IP 检测的隐藏字段
    if server_config and server_config.get('_detected_region'):
        detected = server_config['_detected_region'].upper()
        for key, val in AUTO_COUNTRY_MAP.items():
            if key.upper() == detected or key.upper() in detected:
                return val
            
    return '🏳️ 其他地区'



# ==========================================
# 👇全局变量定义 👇
# ==========================================
FILE_LOCK = asyncio.Lock()
EXPANDED_GROUPS = set()
SERVER_UI_MAP = {}
# ==========================================


def init_data():
    if not os.path.exists('data'): os.makedirs('data')
    
    global SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG
    
    logger.info(f"正在初始化数据... (当前登录账号: {ADMIN_USER})")
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: SERVERS_CACHE = json.load(f)
            logger.info(f"✅ 加载服务器配置: {len(SERVERS_CACHE)} 个")
        except: SERVERS_CACHE = []
    
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f: SUBS_CACHE = json.load(f)
        except: SUBS_CACHE = []

    if os.path.exists(NODES_CACHE_FILE):
        try:
            with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f: NODES_DATA = json.load(f)
            # 统计一下节点数，确认真的加载进去了
            count = sum([len(v) for v in NODES_DATA.values() if isinstance(v, list)])
            logger.info(f"✅ 加载节点缓存完毕 (共 {count} 个节点)")
        except: NODES_DATA = {}
        
    if os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f: ADMIN_CONFIG = json.load(f)
        except: ADMIN_CONFIG = {}

def _save_file_sync_internal(filename, data):
    temp_file = f"{filename}.{uuid.uuid4()}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        shutil.move(temp_file, filename)
    except Exception as e:
        if os.path.exists(temp_file): os.remove(temp_file)
        raise e

async def safe_save(filename, data):
    async with FILE_LOCK:
        try: await run.io_bound(_save_file_sync_internal, filename, data)
        except Exception as e: logger.error(f"❌ 保存 {filename} 失败: {e}")

# ✨✨✨ 保存服务器后，立即通知首页刷新
async def save_servers(): 
    await safe_save(CONFIG_FILE, SERVERS_CACHE)
    # 触发静默更新 (Add/Del Server)
    await refresh_dashboard_ui()

async def save_subs(): await safe_save(SUBS_FILE, SUBS_CACHE)
async def save_admin_config(): await safe_save(ADMIN_CONFIG_FILE, ADMIN_CONFIG)

# ✨✨✨ 保存节点缓存后，也立即通知首页刷新
async def save_nodes_cache():
    try:
        # 直接保存所有内存数据，不做任何过滤
        data_snapshot = NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
        
        # 触发静默更新 (流量变化/节点增删)
        await refresh_dashboard_ui()
    except Exception as e:
        logger.error(f"❌ 保存缓存失败: {e}")

init_data()
managers = {}

def safe_notify(message, type='info', timeout=3000):
    try: ui.notify(message, type=type, timeout=timeout)
    except: logger.info(f"[Notify] {message}")

# ================= SSH 连接核心逻辑 =================
def get_ssh_client(server_data):
    """建立 SSH 连接"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # 解析 IP
    raw_url = server_data['url']
    if '://' in raw_url: host = raw_url.split('://')[-1].split(':')[0]
    else: host = raw_url.split(':')[0]
    
    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    auth_type = server_data.get('ssh_auth_type', '全局密钥')
    
    try:
        if auth_type == '独立密码':
            client.connect(host, port, username=user, password=server_data.get('ssh_password'), timeout=5)
        elif auth_type == '独立密钥':
            key_file = io.StringIO(server_data.get('ssh_key', ''))
            pkey = paramiko.RSAKey.from_private_key(key_file)
            client.connect(host, port, username=user, pkey=pkey, timeout=5)
        else: # 全局密钥
            g_key = load_global_key()
            if not g_key: raise Exception("全局密钥未配置")
            key_file = io.StringIO(g_key)
            pkey = paramiko.RSAKey.from_private_key(key_file)
            client.connect(host, port, username=user, pkey=pkey, timeout=5)
        return client, f"✅ 已连接 {user}@{host}"
    except Exception as e:
        return None, f"❌ 连接失败: {str(e)}"

# =================  交互式 WebSSH 类 =================
def get_ssh_client_sync(server_data):
    return get_ssh_client(server_data)

class WebSSH:
    def __init__(self, container, server_data):
        self.container = container
        self.server_data = server_data
        self.client = None
        self.channel = None
        self.active = False
        self.term_id = f'term_{uuid.uuid4().hex}'

    async def connect(self):
        # 显式进入容器上下文
        with self.container:
            try:
                # 1. 渲染终端 UI 容器
                # 使用 relative 和 hidden 确保布局正确
                ui.element('div').props(f'id={self.term_id}').classes('w-full h-full bg-black rounded p-2 overflow-hidden relative')
                
                # 2. 注入 JS (初始化 xterm, 增加详细错误处理)
                init_js = f"""
                try {{
                    // --- A. 安全清理旧实例 ---
                    if (window.{self.term_id}) {{
                        console.log("Cleaning up old term:", window.{self.term_id});
                        // ✨ 核心修复：只有当 dispose 是一个函数时才调用
                        if (typeof window.{self.term_id}.dispose === 'function') {{
                            window.{self.term_id}.dispose();
                        }}
                        window.{self.term_id} = null;
                    }}
                    
                    // --- B. 检查 xterm.js 库是否加载 ---
                    if (typeof Terminal === 'undefined') {{
                        throw new Error("xterm.js 库未加载！请检查 /static/xterm.js 是否正常访问");
                    }}
                    
                    // --- C. 创建新实例 ---
                    var term = new Terminal({{
                        cursorBlink: true,
                        fontSize: 14,
                        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                        theme: {{ background: '#000000', foreground: '#ffffff' }},
                        convertEol: true,
                    }});
                    
                    // --- D. 加载自适应插件 (兼容处理) ---
                    var fitAddon;
                    if (typeof FitAddon !== 'undefined') {{
                        // 兼容不同版本的导出方式: FitAddon.FitAddon 或 直接 FitAddon
                        var FitAddonClass = FitAddon.FitAddon || FitAddon;
                        fitAddon = new FitAddonClass();
                        term.loadAddon(fitAddon);
                    }} else {{
                        console.warn("FitAddon not found");
                    }}
                    
                    // --- E. 挂载到 DOM ---
                    var el = document.getElementById('{self.term_id}');
                    term.open(el);
                    
                    // 打印本地欢迎语
                    term.write('\\x1b[32m[Local] Terminal Ready. Connecting to SSH...\\x1b[0m\\r\\n');
                    
                    if (fitAddon) {{ setTimeout(() => {{ fitAddon.fit(); }}, 200); }}
                    
                    // 注册到全局变量
                    window.{self.term_id} = term;
                    term.focus();
                    
                    // --- F. 绑定事件 ---
                    term.onData(data => {{
                        emitEvent('term_input_{self.term_id}', data);
                    }});
                    
                    if (fitAddon) {{ new ResizeObserver(() => fitAddon.fit()).observe(el); }}

                }} catch(e) {{
                    console.error("Terminal Init Error:", e);
                    var el = document.getElementById('{self.term_id}');
                    if (el) {{
                        el.innerHTML = '<div style="color:red; padding:20px; font-weight:bold;">启动错误: ' + e.message + '</div>';
                    }}
                    alert("终端启动失败: " + e.message);
                }}
                """
                ui.run_javascript(init_js)

                # 3. 绑定输入事件
                ui.on(f'term_input_{self.term_id}', lambda e: self._write_to_ssh(e.args))

                # 4. 后台建立 SSH 连接
                self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)
                
                if not self.client:
                    self._print_error(msg)
                    return

                # 5. 开启 Shell
                self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
                self.channel.settimeout(0.0) 
                self.active = True


                # 6. 启动读取循环
                asyncio.create_task(self._read_loop())
                
                ui.notify(f"已连接到 {self.server_data['name']}", type='positive')

            except Exception as e:
                self._print_error(f"初始化异常: {e}")

    def _print_error(self, msg):
        try:
            js_cmd = f'if(window.{self.term_id}) window.{self.term_id}.write("\\r\\n\\x1b[31m[Error] {str(msg)}\\x1b[0m\\r\\n");'
            with self.container.client:
                ui.run_javascript(js_cmd)
        except:
            ui.notify(msg, type='negative')

    def _write_to_ssh(self, data):
        if self.channel and self.active:
            try: self.channel.send(data)
            except: pass

    async def _read_loop(self):
        while self.active:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    if not data: break 
                    
                    b64_data = base64.b64encode(data).decode('utf-8')
                    
                    with self.container.client:
                        ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_data}"))')
                
                await asyncio.sleep(0.01)
            except Exception:
                await asyncio.sleep(0.1)

    def close(self):
        self.active = False
        if self.client: 
            try: self.client.close()
            except: pass
        try:
            with self.container.client:
                # 简单的 dispose，不做复杂判断，因为 connect 里已经有强力清理了
                ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.dispose();')
        except: pass

# ================= SSH 界面入口  =================
ssh_instances = {} 

def open_ssh_interface(server_data):
    # 1. 清理内容
    content_container.clear()
    
    # h-full: 容器高度占满屏幕，为垂直居中做准备
    # p-6: 保持四周留白，不贴边
    # flex flex-col justify-center: 让内部的灰色大卡片在垂直方向居中！
    content_container.classes(remove='p-0 pl-0 block', add='h-full p-6 flex flex-col justify-center overflow-hidden')
    
    old_ssh = ssh_instances.get('current')
    if old_ssh: old_ssh.close()

    with content_container:
        # ✨ 灰色背景大容器 (Wrapper)
        # w-full: 宽度占满 (满足你的要求)
        # h-[85vh]: 高度固定为视口的 85%，这样上下就会留出空隙，实现“悬浮感”
        with ui.column().classes('w-full h-[85vh] bg-gray-100 rounded-2xl p-4 shadow-2xl border border-gray-200 gap-3 relative'):
            
            # === 1. 顶部大标题栏 (居中) ===
            # relative: 为了让关闭按钮绝对定位
            # justify-center: 让标题文字居中
            with ui.row().classes('w-full items-center justify-center relative mb-1'):
                 
                 # 居中的标题文字
                 with ui.row().classes('items-center gap-3'):
                    ui.icon('dns').classes('text-2xl text-blue-600')
                    ui.label('VPS SSH 客户端连接').classes('text-xl font-extrabold text-gray-800 tracking-wide')
                 
                 # 绝对定位在右侧的关闭按钮
                 with ui.element('div').classes('absolute right-0 top-1/2 -translate-y-1/2'):
                     ui.button(icon='close', on_click=lambda: [close_ssh(), load_dashboard_stats()]) \
                        .props('flat round dense color=grey-7').tooltip('关闭')

            # === 2. 终端卡片 ===
            # flex-grow: 自动填满灰色容器剩余的高度
            with ui.card().classes('w-full flex-grow p-0 gap-0 border border-gray-300 rounded-xl flex flex-col flex-nowrap overflow-hidden shadow-inner min-w-0 relative'):
                
                # --- 内部信息栏 (白色) ---
                with ui.row().classes('w-full h-10 bg-white items-center justify-between px-4 border-b border-gray-200 flex-shrink-0'):
                    
                    # 左侧：服务器信息
                    with ui.row().classes('items-center gap-3 overflow-hidden'):
                        ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-sm animate-pulse')
                        ui.icon('terminal').classes('text-slate-500')
                        with ui.row().classes('gap-2 items-baseline'):
                             ui.label(server_data['name']).classes('text-sm font-bold text-gray-800 truncate')
                             host_name = server_data.get('url', '').replace('http://', '').split(':')[0]
                             ui.label(f"{server_data.get('ssh_user','root')}@{host_name}").classes('text-xs font-mono text-gray-400 hidden sm:block truncate')

                    # 右侧：断开按钮
                    async def close_and_restore():
                        close_ssh()
                        await load_dashboard_stats()

                    ui.button(icon='link_off', on_click=close_and_restore) \
                        .props('round unelevated dense size=sm color=red-1 text-color=red shadow-none') \
                        .tooltip('断开连接')

                # --- 黑色终端区域 ---
                terminal_box = ui.column().classes('w-full flex-grow bg-black p-0 overflow-hidden relative min-h-0 min-w-0')
                
                # 启动 WebSSH
                ssh = WebSSH(terminal_box, server_data)
                ssh_instances['current'] = ssh
                ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

    def close_ssh():
        if ssh_instances.get('current'):
            ssh_instances['current'].close()
            ssh_instances['current'] = None
        # 关闭时恢复布局
        content_container.clear()
        content_container.classes(remove='h-full flex flex-col justify-center overflow-hidden', add='block overflow-y-auto')
            
def _exec(server_data, cmd, log_area):
    client, msg = get_ssh_client(server_data)
    if not client:
        log_area.push(msg)
        return
    try:
        # get_pty=True 模拟伪终端，能获取更好的输出格式
        # timeout=10 设置 10 秒超时，防止卡死
        stdin, stdout, stderr = client.exec_command(cmd, timeout=10, get_pty=True)
        
        # 读取输出 (二进制转字符串)
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        err = stderr.read().decode('utf-8', errors='ignore').strip()
        
        if out: log_area.push(out)
        if err: log_area.push(f"ERR: {err}")
        
        # 如果都没有输出且没有报错
        if not out and not err:
            log_area.push("✅ 命令已执行 (无返回内容)")
            
    except  paramiko.SSHException as e:
         log_area.push(f"SSH Error: {str(e)}")
    except socket.timeout:
         log_area.push("❌ 执行超时: 命令执行时间过长或正在等待交互 (如 sudo/vim)")
    except Exception as e:
        log_area.push(f"系统错误: {repr(e)}") # 使用 repr 显示详细错误类型
    finally:
        client.close()

# ================= 核心网络类 =================
class XUIManager:
    def __init__(self, url, username, password, api_prefix=None):
        self.original_url = str(url).strip().rstrip('/')
        self.url = self.original_url
        self.username = str(username).strip()
        self.password = str(password).strip()
        self.api_prefix = f"/{api_prefix.strip('/')}" if api_prefix else None
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Connection': 'close'})
        self.session.verify = False 
        self.login_path = None

    def _request(self, method, path, **kwargs):
        target_url = f"{self.url}{path}"
        for attempt in range(2):
            try:
                if method == 'POST': return self.session.post(target_url, timeout=5, allow_redirects=False, **kwargs)
                else: return self.session.get(target_url, timeout=5, allow_redirects=False, **kwargs)
            except Exception as e:
                if attempt == 1: return None

    def login(self):
        if self.login_path:
            if self._try_login_at(self.login_path): return True
            self.login_path = None 
        paths = ['/login', '/xui/login', '/panel/login']
        if self.api_prefix: paths.insert(0, f"{self.api_prefix}/login")
        protocols = [self.original_url]
        if '://' not in self.original_url: protocols = [f"http://{self.original_url}", f"https://{self.original_url}"]
        elif self.original_url.startswith('http://'): protocols.append(self.original_url.replace('http://', 'https://'))
        elif self.original_url.startswith('https://'): protocols.append(self.original_url.replace('https://', 'http://'))
        for proto_url in protocols:
            self.url = proto_url
            for path in paths:
                if self._try_login_at(path):
                    self.login_path = path
                    return True
        return False

    def _try_login_at(self, path):
        try:
            r = self._request('POST', path, data={'username': self.username, 'password': self.password})
            if r and r.status_code == 200 and r.json().get('success') == True: return True
            return False
        except: return False

    def get_inbounds(self):
        if not self.login(): return None
        candidates = []
        if self.login_path: candidates.append(self.login_path.replace('login', 'inbound/list'))
        defaults = ['/xui/inbound/list', '/panel/inbound/list', '/inbound/list']
        if self.api_prefix: defaults.insert(0, f"{self.api_prefix}/inbound/list")
        for d in defaults: 
            if d not in candidates: candidates.append(d)
        for path in candidates:
            r = self._request('POST', path)
            if r and r.status_code == 200:
                try: 
                    res = r.json()
                    if res.get('success'): return res.get('obj')
                except: pass
        return None


    def get_server_status(self):
        """获取服务器系统状态 (CPU, 内存, 硬盘, Uptime)"""
        if not self.login(): return None
        
        # 适配不同版本的 X-UI API 路径
        candidates = []
        if self.login_path: candidates.append(self.login_path.replace('login', 'server/status'))
        defaults = ['/xui/server/status', '/panel/server/status', '/server/status']
        if self.api_prefix: defaults.insert(0, f"{self.api_prefix}/server/status")
        
        for d in defaults: 
            if d not in candidates: candidates.append(d)
            
        for path in candidates:
            try:
                # server/status 通常是 POST 请求
                r = self._request('POST', path)
                if r and r.status_code == 200:
                    res = r.json()
                    if res.get('success'): return res.get('obj')
            except: pass
        return None

    def add_inbound(self, data): return self._action('/add', data)
    def update_inbound(self, iid, data): return self._action(f'/update/{iid}', data)
    def delete_inbound(self, iid): return self._action(f'/del/{iid}', {})
    
    def _action(self, suffix, data):
        if not self.login(): return False, "登录失败"
        base = self.login_path.replace('/login', '/inbound')
        path = f"{base}{suffix}"
        
        # print(f"🔵 [用户操作] 正在提交: {self.url}{path}", flush=True)
        r = self._request('POST', path, json=data)
        if r: 
            try: 
                resp = r.json()
                if resp.get('success'): return True, resp.get('msg')
                else: return False, f"后端拒绝: {resp.get('msg')}"
            except Exception as e: return False, f"解析失败 ({r.status_code})"
        return False, "请求无响应 (超时)"

def get_manager(server_conf):
    key = server_conf['url']
    if key not in managers or managers[key].username != server_conf['user']:
        managers[key] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'], server_conf.get('prefix'))
    return managers[key]

# ================= 即时存档 + 顺序修正 =================

# 1. 辅助函数：后台线程执行
async def run_in_bg_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(BG_EXECUTOR, func, *args)

# 2. 单个服务器同步逻辑 
async def fetch_inbounds_safe(server_conf, force_refresh=False):
    url = server_conf['url']
    name = server_conf.get('name', '未命名')
    
    # 如果不是强制刷新，且缓存里有数据，直接返回缓存
    if not force_refresh and url in NODES_DATA: return NODES_DATA[url]
    
    async with SYNC_SEMAPHORE:
        logger.info(f"🔄 同步: [{name}] ...")
        try:
            mgr = get_manager(server_conf)
            inbounds = await run_in_bg_executor(mgr.get_inbounds)
            if inbounds is None:
                # 登录重试逻辑
                mgr = managers[server_conf['url']] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'], server_conf.get('prefix')) 
                inbounds = await run_in_bg_executor(mgr.get_inbounds)
            
            if inbounds is not None:
                # ✅ 成功：更新缓存
                NODES_DATA[url] = inbounds
                # 标记为在线 (可选，目前通过列表非空来判断即可)
                server_conf['_status'] = 'online' 
                
                asyncio.create_task(save_nodes_cache())
                return inbounds
            
            # ❌ 失败 (登录失败/连接超时)：清空该服务器的节点缓存，确保仪表盘数据归零
            logger.error(f"❌ [{name}] 连接失败 (清除缓存)")
            NODES_DATA[url] = [] # ✨ 关键修改：连接失败则清空节点数据
            server_conf['_status'] = 'offline' # 标记离线
            return []
            
        except Exception as e: 
            logger.error(f"❌ [{name}] 异常: {e}")
            # ❌ 异常：同样清空缓存
            NODES_DATA[url] = [] 
            server_conf['_status'] = 'error'
            return []

# 3. 批量静默刷新逻辑 (防抖 + 空缓存穿透)
async def silent_refresh_all(is_auto_trigger=False):
    # 1. 读取上次时间
    last_time = ADMIN_CONFIG.get('last_sync_time', 0)
    
    if is_auto_trigger:
        current_time = time.time()
        
        # === 检查缓存节点数 ===
        total_nodes = 0
        try:
            for nodes in NODES_DATA.values():
                if isinstance(nodes, list): total_nodes += len(nodes)
        except: pass

        # 穿透条件：有服务器配置 但 缓存里完全没数据 (说明之前可能还没来得及存就崩了)
        if len(SERVERS_CACHE) > 0 and total_nodes == 0:
            logger.warning(f"⚠️ [防抖穿透] 缓存为空 (节点数0)，强制触发首次修复同步！")
            # 继续向下执行同步...
        
        # 冷却条件
        elif current_time - last_time < SYNC_COOLDOWN_SECONDS:
            remaining = int(SYNC_COOLDOWN_SECONDS - (current_time - last_time))
            logger.info(f"⏳ [防抖生效] 距离上次同步不足 {SYNC_COOLDOWN_SECONDS}秒，跳过 (剩余: {remaining}s)")
            
            # ❌❌❌ [修复] 这里不要强制刷新页面，否则会导致 UI 闪烁或死循环 ❌❌❌
            # try: 
            #     render_sidebar_content.refresh()
            #     await load_dashboard_stats()
            # except: pass
            
            return

    # 2. 执行同步流程
    safe_notify(f'🚀 开始后台静默刷新 ({len(SERVERS_CACHE)} 个服务器)...')
    
    # 只要开始跑了，就标记为"已更新"，防止重启后重复触发
    ADMIN_CONFIG['last_sync_time'] = time.time()
    await save_admin_config() 
    
    tasks = []
    for srv in SERVERS_CACHE:
        # 使用之前那个带即时保存功能的 fetch 函数
        tasks.append(fetch_inbounds_safe(srv, force_refresh=True))
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # 跑完再保存一次兜底（双保险）
    await save_nodes_cache() 
    
    safe_notify('✅ 后台刷新完成', 'positive')
    try: 
        render_sidebar_content.refresh()
        await load_dashboard_stats() 
    except: pass

    
async def install_probe_on_server(server_conf):
    """给单个服务器安装探针 (智能宽容版)"""
    name = server_conf.get('name', 'Unknown')
    
    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client(server_conf)
            if not client: return False, f"连接失败: {msg}"
            
            # 执行安装 (300秒超时)
            stdin, stdout, stderr = client.exec_command(PROBE_INSTALL_SCRIPT, timeout=300)
            
            # 获取结果
            exit_status = stdout.channel.recv_exit_status() 
            out_log = stdout.read().decode('utf-8', errors='ignore').strip()
            err_log = stderr.read().decode('utf-8', errors='ignore').strip()
            
            client.close()
            
            # ✨✨✨ 核心修改：智能判定 ✨✨✨
            # 1. 正常退出 (0) -> 成功
            # 2. 意外断开 (-1) 但看到了防火墙日志 (Skipping/rule) -> 视为成功 (说明脚本跑完了)
            if exit_status == 0:
                return True, "安装成功"
            elif exit_status == -1 and ("Skipping" in out_log or "rule" in out_log or "allow" in out_log):
                return True, "安装成功 (连接重置)"
            else:
                debug_info = f"Exit Code: {exit_status}\n[STDERR]: {err_log}\n[STDOUT]: {out_log}"
                return False, debug_info
                
        except Exception as e:
            return False, f"执行异常: {str(e)}"
        finally:
            if client: 
                try: client.close()
                except: pass

    success, msg = await run.io_bound(_do_install)
    if success:
        logger.info(f"✅ [AutoInstall] {name} 安装成功")
    else:
        logger.error(f"❌ [AutoInstall] {name} 安装失败:\n{msg}")
        
    return success

# ================= 探针核心逻辑 (强制直连版：解决 Docker 代理干扰) =================
async def get_server_status(server_conf):
    """
    仅通过 HTTP 探针获取状态。
    关键点：强制不走系统代理 (proxies=None)，防止 Docker 环境变量导致连接失败。
    """
    def _try_http_probe():
        try:
            # 提取主机名/IP
            raw = server_conf['url']
            host = raw.split('://')[-1].split(':')[0]
            
            # 构造请求 (3秒超时)
            target_url = f"http://{host}:54322/status?token=sijuly_probe_token"
            
            # ✨✨✨ 核心修复：proxies={"http": None, "https": None} ✨✨✨
            # 这句代码的意思是：无视系统代理，必须直连！
            with requests.get(target_url, timeout=3, proxies={"http": None, "https": None}) as r:
                if r.status_code == 200:
                    data = r.json()
                    return {
                        'status': 'online',
                        'load': data.get('load', 0),
                        'mem': data.get('mem', 0),
                        'disk': data.get('disk', 0),
                        'uptime': data.get('uptime', '')
                    }
        except:
            return None 

    # 在后台线程执行
    http_result = await run.io_bound(_try_http_probe)
    
    if http_result: 
        return http_result
    
    # ❌ 如果直连也失败，才报离线
    return {'status': 'offline', 'msg': '探针未连接'}

# ================= 使用 URL 安全的 Base64 =================
def safe_base64(s): 
    # 使用 urlsafe_b64encode 避免出现 + 和 /
    return base64.urlsafe_b64encode(s.encode('utf-8')).decode('utf-8')

def decode_base64_safe(s): 
    try: 
        # 兼容标准 Base64 和 URL Safe Base64
        # 补全 padding
        missing_padding = len(s) % 4
        if missing_padding: s += '=' * (4 - missing_padding)
        return base64.urlsafe_b64decode(s).decode('utf-8')
    except: 
        try: return base64.b64decode(s).decode('utf-8')
        except: return ""

# ================= 生成 SubConverter 转换链接 =================
def generate_converted_link(raw_link, target, domain_prefix):
    """
    生成经过 SubConverter 转换的订阅链接
    target: surge, clash
    """
    if not raw_link or not domain_prefix: return ""
    
    converter_base = f"{domain_prefix}/convert"
    encoded_url = quote(raw_link)
    
    # ✨✨✨ 核心修改 ✨✨✨
    # 1. 移除了 config=... (去掉了强制的分流规则模板)
    # 2. 增加了 list=true  (只输出节点部分)
    # 3. 增加了 udp=true   (默认开启 UDP 转发支持)
    # 4. 增加了 scv=true   (关闭 TLS 证书校验，防止自签证书报错)
    params = f"target={target}&url={encoded_url}&insert=false&list=true&ver=4&udp=true&scv=true"
    
    return f"{converter_base}?{params}"

def generate_node_link(node, server_host):
    try:
        p = node['protocol']; remark = node['remark']; port = node['port']
        add = node.get('listen') or server_host
        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = st.get('network', 'tcp'); tls = st.get('security', 'none'); path = ""; host = ""
        if net == 'ws': 
            path = st.get('wsSettings',{}).get('path','/')
            host = st.get('wsSettings',{}).get('headers',{}).get('Host','')
        elif net == 'grpc': path = st.get('grpcSettings',{}).get('serviceName','')
        
        if p == 'vmess':
            v = {"v":"2","ps":remark,"add":add,"port":port,"id":s['clients'][0]['id'],"aid":"0","scy":"auto","net":net,"type":"none","host":host,"path":path,"tls":tls}
            return "vmess://" + safe_base64(json.dumps(v))
        elif p == 'vless':
            params = f"type={net}&security={tls}"
            if path: params += f"&path={path}" if net != 'grpc' else f"&serviceName={path}"
            if host: params += f"&host={host}"
            return f"vless://{s['clients'][0]['id']}@{add}:{port}?{params}#{remark}"
        elif p == 'trojan': return f"trojan://{s['clients'][0]['password']}@{add}:{port}?type={net}&security={tls}#{remark}"
        elif p == 'shadowsocks': 
            cred = f"{s['method']}:{s['password']}"
            return f"ss://{safe_base64(cred)}@{add}:{port}#{remark}"
    except: return ""
    return ""

# ================= 生成 Surge/Loon 格式明文配置 =================
def generate_detail_config(node, server_host):
    try:
        p = node['protocol']
        remark = node['remark']
        port = node['port']
        add = node.get('listen') or server_host
        
        # 解析设置
        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        
        # 基础流控设置
        net = st.get('network', 'tcp')
        security = st.get('security', 'none')
        tls = (security == 'tls')
        
        # 构造基础头部
        # 格式: protocol=host:port
        base = f"{p}={add}:{port}"
        params = []

        if p == 'vmess':
            uuid = s['clients'][0]['id']
            # VMess 默认参数
            params.append("method=auto")
            params.append(f"password={uuid}")
            params.append("fast-open=false")
            params.append("udp-relay=false")
            params.append("aead=true") # 现代客户端通常开启 AEAD
            
            # 传输协议处理
            if net == 'ws':
                ws_set = st.get('wsSettings', {})
                path = ws_set.get('path', '/')
                host = ws_set.get('headers', {}).get('Host', '')
                params.append("obfs=websocket")
                params.append(f"obfs-uri={path}")
                if host: params.append(f"obfs-host={host}")
            
            if tls:
                params.append("tls=true")
                # 尝试获取 SNI
                tls_set = st.get('tlsSettings', {})
                sni = tls_set.get('serverName', '')
                if sni: params.append(f"sni={sni}")

        elif p == 'shadowsocks':
            method = s.get('method', 'aes-256-gcm')
            pwd = s.get('password', '')
            params.append(f"method={method}")
            params.append(f"password={pwd}")
            params.append("fast-open=false")
            params.append("udp-relay=true")
            
            # Simple-obfs / v2ray-plugin 处理 (X-UI通常是标准SS，这里只做基础处理)

        elif p == 'trojan':
            pwd = s['clients'][0]['password']
            params.append(f"password={pwd}")
            params.append("fast-open=false")
            params.append("udp-relay=false")
            if tls:
                params.append("tls=true")
                sni = st.get('tlsSettings', {}).get('serverName', '')
                if sni: params.append(f"sni={sni}")
        
        else:
            # VLESS 等协议 Surge 格式支持较复杂，暂返回空或标准链接
            return ""

        # 最后加上 Tag
        params.append(f"tag={remark}")
        
        # 拼接
        return f"{base}, {', '.join(params)}"

    except Exception as e:
        # logger.error(f"格式转换失败: {e}")
        return ""


# ================= 延迟测试核心逻辑 (多进程优化版) =================
PING_CACHE = {}

async def batch_ping_nodes(nodes, raw_host):
    """
    使用多进程池并行 Ping，彻底解放主线程。
    """
    # 如果进程池还没启动（比如刚开机），直接返回，防止报错
    if not PROCESS_POOL: return 

    loop = asyncio.get_running_loop()
    
    # 1. 准备任务列表
    targets = []
    for n in nodes:
        # 获取真实地址
        host = n.get('listen')
        if not host or host == '0.0.0.0': host = raw_host
        port = n.get('port')
        key = f"{host}:{port}"
        targets.append((host, port, key))

    # 2. 定义回调处理 (将子进程的结果更新到主进程缓存)
    async def run_single_ping(t_host, t_port, t_key):
        try:
            # ✨ 核心：将同步的 ping 扔给进程池执行
            # 这行代码会在另一个进程里跑，绝对不会卡住你的网页
            latency = await loop.run_in_executor(PROCESS_POOL, sync_ping_worker, t_host, t_port)
            PING_CACHE[t_key] = latency
        except:
            PING_CACHE[t_key] = -1

    # 3. 并发分发任务
    # 虽然这里用了 await gather，但这只是在等待结果，计算压力全在 ProcessPool
    tasks = [run_single_ping(h, p, k) for h, p, k in targets]
    if tasks:
        await asyncio.gather(*tasks)


# ================= 接口处理 =================
@app.get('/sub/{token}')
async def sub_handler(token: str, request: Request):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)
    links = []
    for srv in SERVERS_CACHE:
        inbounds = NODES_DATA.get(srv['url'], [])
        if not inbounds: continue
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url); host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url
        sub_nodes_set = set(sub.get('nodes', []))
        for n in inbounds:
            if f"{srv['url']}|{n['id']}" in sub_nodes_set:
                l = generate_node_link(n, host)
                if l: links.append(l)
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")

# ================= 分组订阅接口：支持 Tag 和 主分组 =================
@app.get('/sub/group/{group_b64}')
async def group_sub_handler(group_b64: str, request: Request):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)
    
    links = []
    
    # ✨✨✨ 同时筛选“主分组”和“Tags” ✨✨✨
    target_servers = [
        s for s in SERVERS_CACHE 
        if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
    ]
    
    logger.info(f"正在生成分组订阅: [{group_name}]，匹配到 {len(target_servers)} 个服务器")

    for srv in target_servers:
        inbounds = NODES_DATA.get(srv['url'], [])
        if not inbounds: continue
        
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url); host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url
        
        for n in inbounds:
            if n.get('enable'): 
                l = generate_node_link(n, host)
                if l: links.append(l)
    
    # 如果没有节点，返回一个提示注释，防止 SubConverter 报错
    if not links:
        return Response(f"// Group [{group_name}] is empty or not found", media_type="text/plain; charset=utf-8")
        
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")

# ================= 短链接接口：分组 =================
@app.get('/get/group/{target}/{group_b64}')
async def short_group_handler(target: str, group_b64: str):
    try:
        internal_api = f"http://xui-manager:8080/sub/group/{group_b64}"

        params = {
            "target": target,
            "url": internal_api,
            "insert": "false",
            "list": "true",
            "ver": "4",
            "udp": "true",
            "scv": "true"
        }
        
        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            code = response.status_code if response else 'Timeout'
            return Response(f"Backend Error: {code} (Check Docker Network)", status_code=502)
    except Exception as e: return Response(f"Error: {str(e)}", status_code=500)

# ================= 短链接接口：单个订阅 (支持重命名) =================
@app.get('/get/sub/{target}/{token}')
async def short_sub_handler(target: str, token: str):
    try:
        sub_obj = next((s for s in SUBS_CACHE if s['token'] == token), None)
        if not sub_obj: return Response("Subscription Not Found", 404)
        
        opt = sub_obj.get('options', {})
        internal_api = f"http://xui-manager:8080/sub/{token}"
        
        params = {
            "target": target,
            "url": internal_api,
            "insert": "false",
            "list": "true",
            "ver": "4",
            "emoji": str(opt.get('emoji', True)).lower(),
            "udp": str(opt.get('udp', True)).lower(),
            "tfo": str(opt.get('tfo', False)).lower(),
            "scv": str(opt.get('skip_cert', True)).lower(),
            "sort": str(opt.get('sort', False)).lower(),
        }

        # --- 正则过滤 ---
        regions = opt.get('regions', [])
        includes = []
        if opt.get('include_regex'): includes.append(opt['include_regex'])
        if regions:
            region_keywords = []
            for r in regions:
                parts = r.split(' '); k = parts[1] if len(parts)>1 else r
                region_keywords.append(k)
                for c, v in AUTO_COUNTRY_MAP.items(): 
                    if v == r and len(c) == 2: region_keywords.append(c)
            if region_keywords: includes.append(f"({'|'.join(region_keywords)})")
        
        if includes: params['include'] = "|".join(includes)
        if opt.get('exclude_regex'): params['exclude'] = opt['exclude_regex']

        ren_pat = opt.get('rename_pattern', '')
        ren_rep = opt.get('rename_replacement', '')
        
        if ren_pat:
            # SubConverter 的 rename 参数格式: pattern@replacement
            # 注意：SubConverter 默认支持正则，$1 需要写成 $1
            params['rename'] = f"{ren_pat}@{ren_rep}"

        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            return Response(f"Backend Error: {response.status_code if response else 'Timeout'}", status_code=502)
    except Exception as e: return Response(f"Error: {str(e)}", status_code=500)
    
# ================= 自动注册接口 (带鉴权) =================
@app.post('/api/auto_register_node')
async def auto_register_node(request: Request):
    try:
        # 1. 获取并解析数据
        data = await request.json()
        
        # 2. 安全验证
        secret = data.get('secret')
        if secret != AUTO_REGISTER_SECRET:
            logger.warning(f"⚠️ [自动注册] 密钥错误: {secret}")
            return Response(json.dumps({"success": False, "msg": "密钥错误"}), status_code=403, media_type="application/json")

        # 3. 提取字段
        ip = data.get('ip')
        port = data.get('port')
        username = data.get('username')
        password = data.get('password')
        alias = data.get('alias', f'Auto-{ip}')

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "参数不完整"}), status_code=400, media_type="application/json")

        target_url = f"http://{ip}:{port}"
        
        new_server_config = {
            'name': alias,
            'group': '默认分组',
            'url': target_url,
            'user': username,
            'pass': password,
            'prefix': ''
        }

        # 5. 查重逻辑
        existing_index = -1
        for idx, srv in enumerate(SERVERS_CACHE):
            cache_url = srv['url'].replace('http://', '').replace('https://', '')
            new_url_clean = target_url.replace('http://', '').replace('https://', '')
            if cache_url == new_url_clean:
                existing_index = idx
                break

        action_msg = ""
        if existing_index != -1:
            SERVERS_CACHE[existing_index].update(new_server_config)
            action_msg = f"🔄 更新节点: {alias}"
        else:
            SERVERS_CACHE.append(new_server_config)
            action_msg = f"✅ 新增节点: {alias}"

        await save_servers()
        try: render_sidebar_content.refresh()
        except: pass
        
        logger.info(f"[自动注册] {action_msg} ({ip})")
        return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200, media_type="application/json")

    except Exception as e:
        logger.error(f"❌ [自动注册] 处理异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500, media_type="application/json")

def show_loading(container):
    try:
        container.clear()
        with container:
            with ui.column().classes('w-full h-[60vh] justify-center items-center'):
                ui.spinner('dots', size='3rem', color='primary')
                ui.label('数据处理中...').classes('text-gray-500 mt-4')
    except: pass

def get_all_groups():
    groups = {'默认分组', '自动注册'}
    for s in SERVERS_CACHE:
        g = s.get('group')
        if g: groups.add(g)
    return sorted(list(groups))

async def safe_copy_to_clipboard(text):
    safe_text = json.dumps(text).replace('"', '\\"') 
    js_code = f"""
    (async () => {{
        const text = {json.dumps(text)};
        try {{
            await navigator.clipboard.writeText(text);
            return true;
        }} catch (err) {{
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {{
                document.execCommand('copy');
                document.body.removeChild(textArea);
                return true;
            }} catch (err2) {{
                document.body.removeChild(textArea);
                return false;
            }}
        }}
    }})()
    """
    try:
        result = await ui.run_javascript(js_code)
        if result: safe_notify('已复制到剪贴板', 'positive')
        else: safe_notify('复制失败', 'negative')
    except: safe_notify('复制功能不可用', 'negative')

# =================  支持格式转换的分组复制 =================
async def copy_group_link(group_name, target=None):
    try:
        origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
        if not origin: origin = "https://xui-manager.sijuly.nyc.mn"
        encoded_name = safe_base64(group_name)
        
        if target:
            # ✨路径 /get/group/...
            final_link = f"{origin}/get/group/{target}/{encoded_name}"
            msg_prefix = "Surge" if target == 'surge' else "Clash"
        else:
            final_link = f"{origin}/sub/group/{encoded_name}"
            msg_prefix = "原始"
            
        await safe_copy_to_clipboard(final_link)
        safe_notify(f"已复制 [{group_name}] {msg_prefix} 订阅", "positive")
    except Exception as e: safe_notify(f"生成失败: {e}", "negative")
    
# ================= UI 组件 =================
class InboundEditor:
    def __init__(self, mgr, data=None, on_success=None):
        self.mgr = mgr; self.cb = on_success; self.is_edit = data is not None
        if not data:
            random_port = random.randint(10000, 65000)
            self.d = {
                "enable": True, 
                "remark": "", 
                "port": random_port,
                "protocol": "vmess",
                "settings": {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}], "disableInsecureEncryption": False},
                "streamSettings": {"network": "tcp", "security": "none"},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
            }
        else: 
            self.d = data.copy()
        
        if isinstance(self.d.get('settings'), str): 
            try: self.d['settings'] = json.loads(self.d['settings'])
            except: self.d['settings'] = {}
        if isinstance(self.d.get('streamSettings'), str): 
            try: self.d['streamSettings'] = json.loads(self.d['streamSettings'])
            except: self.d['streamSettings'] = {}

    def ui(self, dlg):
        with ui.card().classes('w-full max-w-4xl p-6 flex flex-col gap-4'):
            title = '编辑节点' if self.is_edit else '新建节点'
            with ui.row().classes('justify-between items-center'):
                ui.label(title).classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')
            with ui.row().classes('w-full gap-4'):
                self.rem = ui.input('备注', value=self.d.get('remark')).classes('flex-grow')
                self.ena = ui.switch('启用', value=self.d.get('enable', True)).classes('mt-2')
            with ui.row().classes('w-full gap-4'):
                self.pro = ui.select(['vmess', 'vless', 'trojan', 'shadowsocks', 'socks'], value=self.d['protocol'], label='协议', on_change=self.on_protocol_change).classes('w-1/3')
                self.prt = ui.number('端口', value=self.d['port'], format='%.0f').classes('w-1/3')
                ui.button(icon='shuffle', on_click=lambda: self.prt.set_value(int(run.io_bound(lambda: __import__('random').randint(10000, 60000))))).props('flat dense').tooltip('随机端口')
            ui.separator().classes('my-2'); self.auth_box = ui.column().classes('w-full gap-2'); self.refresh_auth_ui(); ui.separator().classes('my-2')
            with ui.row().classes('w-full gap-4'):
                st = self.d.get('streamSettings', {})
                self.net = ui.select(['tcp', 'ws', 'grpc'], value=st.get('network', 'tcp'), label='传输协议').classes('w-1/3')
                self.sec = ui.select(['none', 'tls'], value=st.get('security', 'none'), label='安全加密').classes('w-1/3')
            with ui.row().classes('w-full justify-end mt-6'): ui.button('保存', on_click=lambda: self.save(dlg)).props('color=primary')

    def on_protocol_change(self, e):
        p = e.value; s = self.d.get('settings', {})
        if p in ['vmess', 'vless']:
            if 'clients' not in s: self.d['settings'] = {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}], "disableInsecureEncryption": False}
        elif p == 'trojan':
            if 'clients' not in s or 'password' not in s.get('clients', [{}])[0]: self.d['settings'] = {"clients": [{"password": str(uuid.uuid4().hex[:8])}]}
        elif p == 'shadowsocks':
            if 'password' not in s: self.d['settings'] = {"method": "aes-256-gcm", "password": str(uuid.uuid4().hex[:10]), "network": "tcp,udp"}
        elif p == 'socks':
            if 'accounts' not in s: self.d['settings'] = {"auth": "password", "accounts": [{"user": "admin", "pass": "admin"}], "udp": False}
        self.d['protocol'] = p; self.refresh_auth_ui()

    def refresh_auth_ui(self):
        self.auth_box.clear(); p = self.pro.value; s = self.d.get('settings', {})
        with self.auth_box:
            if p in ['vmess', 'vless']:
                clients = s.get('clients', [{}]); cid = clients[0].get('id', str(uuid.uuid4()))
                ui.label('认证 (UUID)').classes('text-sm font-bold text-gray-500')
                uuid_inp = ui.input('UUID', value=cid).classes('w-full').on_value_change(lambda e: s['clients'][0].update({'id': e.value}))
                ui.button('生成 UUID', on_click=lambda: uuid_inp.set_value(str(uuid.uuid4()))).props('flat dense size=sm')
            elif p == 'trojan':
                clients = s.get('clients', [{}]); pwd = clients[0].get('password', '')
                ui.input('密码', value=pwd).classes('w-full').on_value_change(lambda e: s['clients'][0].update({'password': e.value}))
            elif p == 'shadowsocks':
                method = s.get('method', 'aes-256-gcm'); pwd = s.get('password', '')
                with ui.row().classes('w-full gap-4'):
                    ui.select(['aes-256-gcm', 'chacha20-ietf-poly1305', 'aes-128-gcm'], value=method, label='加密').classes('flex-1').on_value_change(lambda e: s.update({'method': e.value}))
                    ui.input('密码', value=pwd).classes('flex-1').on_value_change(lambda e: s.update({'password': e.value}))
            elif p == 'socks':
                accounts = s.get('accounts', [{}]); user = accounts[0].get('user', ''); pwd = accounts[0].get('pass', '')
                with ui.row().classes('w-full gap-4'):
                    ui.input('用户名', value=user).classes('flex-1').on_value_change(lambda e: s['accounts'][0].update({'user': e.value}))
                    ui.input('密码', value=pwd).classes('flex-1').on_value_change(lambda e: s['accounts'][0].update({'pass': e.value}))

    async def save(self, dlg):
        self.d['remark'] = self.rem.value
        self.d['enable'] = self.ena.value
        try:
            port_val = int(self.prt.value)
            if port_val <= 0 or port_val > 65535: raise ValueError
            self.d['port'] = port_val
        except: safe_notify("请输入有效端口", "negative"); return
        self.d['protocol'] = self.pro.value
        
        if 'streamSettings' not in self.d: self.d['streamSettings'] = {}
        if 'sniffing' not in self.d: 
            self.d['sniffing'] = {"enabled": True, "destOverride": ["http", "tls"]}
            
        self.d['streamSettings']['network'] = self.net.value
        self.d['streamSettings']['security'] = self.sec.value
        
        def _do_save_sync():
            try:
                session = requests.Session()
                session.verify = False 
                session.headers.update({'User-Agent': 'Mozilla/5.0', 'Connection': 'close'})
                raw_base = str(self.mgr.original_url).strip()
                base_list = []
                if '://' not in raw_base:
                    base_list.append(f"http://{raw_base}")
                    base_list.append(f"https://{raw_base}")
                else:
                    base_list.append(raw_base.rstrip('/'))
                    if raw_base.startswith('http://'):
                        base_list.append(raw_base.replace('http://', 'https://'))

                login_paths = ['/login', '/xui/login', '/panel/login', '/3x-ui/login']
                if self.mgr.api_prefix:
                    clean_prefix = self.mgr.api_prefix.strip().rstrip('/')
                    if clean_prefix: login_paths.insert(0, f"{clean_prefix}/login")

                success_login_url = None
                
                for b_url in base_list:
                    if success_login_url: break
                    for path in login_paths:
                        target_login_url = f"{b_url}{path}"
                        try:
                            r = session.post(target_login_url, data={'username': self.mgr.username, 'password': self.mgr.password}, timeout=5)
                            if r.status_code == 200 and r.json().get('success'):
                                success_login_url = target_login_url
                                break
                        except Exception as e: pass

                if not success_login_url: return False, "VIP通道：无法连接到服务器"

                submit_data = self.d.copy()
                if isinstance(submit_data.get('settings'), dict):
                    submit_data['settings'] = json.dumps(submit_data['settings'], ensure_ascii=False)
                if isinstance(submit_data.get('streamSettings'), dict):
                    submit_data['streamSettings'] = json.dumps(submit_data['streamSettings'], ensure_ascii=False)
                if isinstance(submit_data.get('sniffing'), dict):
                    submit_data['sniffing'] = json.dumps(submit_data['sniffing'], ensure_ascii=False)

                action = 'update/' + str(self.d['id']) if self.is_edit else 'add'
                base_root_url = success_login_url.rsplit('/login', 1)[0]
                
                save_candidates = [f"{base_root_url}/inbound/{action}", f"{base_root_url}/xui/inbound/{action}"]
                
                final_response = None
                for save_url in dict.fromkeys(save_candidates): 
                    try:
                        r = session.post(save_url, json=submit_data, timeout=8)
                        if r.status_code != 404:
                            final_response = r
                            break
                    except Exception as e: continue
                
                if final_response:
                    try:
                        resp = final_response.json()
                        return (True, resp.get('msg')) if resp.get('success') else (False, resp.get('msg'))
                    except: return False, f"响应解析失败 (状态码 {final_response.status_code})"
                else: return False, "保存失败：未找到正确的 API 路径 (404)"

            except Exception as e: return False, f"系统异常: {str(e)}"

        success, msg = await run.io_bound(_do_save_sync)
        if success: 
            safe_notify("✅ 保存成功", "positive")
            dlg.close()
            if self.cb:
                res = self.cb()
                if asyncio.iscoroutine(res): await res
        else: safe_notify(f"❌ 失败: {msg}", "negative", timeout=6000)

async def open_inbound_dialog(mgr, data, cb):
    with ui.dialog() as d: InboundEditor(mgr, data, cb).ui(d); d.open()

async def delete_inbound(mgr, id, cb):
    def _do_delete_sync():
        try:
            session = requests.Session()
            session.verify = False
            session.headers.update({'User-Agent': 'Mozilla/5.0', 'Connection': 'close'})
            raw_base = str(mgr.original_url).strip()
            base_list = []
            if '://' not in raw_base:
                base_list.append(f"http://{raw_base}")
                base_list.append(f"https://{raw_base}")
            else:
                base_list.append(raw_base.rstrip('/'))
                if raw_base.startswith('http://'):
                    base_list.append(raw_base.replace('http://', 'https://'))
            
            login_paths = ['/login', '/xui/login', '/panel/login']
            if mgr.api_prefix:
                clean_prefix = mgr.api_prefix.strip().rstrip('/')
                if clean_prefix: login_paths.insert(0, f"{clean_prefix}/login")
            
            success_login_url = None
            for b_url in base_list:
                if success_login_url: break
                for path in login_paths:
                    try:
                        target = f"{b_url}{path}"
                        r = session.post(target, data={'username': mgr.username, 'password': mgr.password}, timeout=5)
                        if r.status_code == 200 and r.json().get('success'):
                            success_login_url = target
                            break
                    except: pass
            
            if not success_login_url: return False, "无法连接或登录失败"

            action = f"del/{id}"
            base_root = success_login_url.rsplit('/login', 1)[0]
            
            candidates = [f"{base_root}/inbound/{action}", f"{base_root}/xui/inbound/{action}", f"{base_root}/panel/inbound/{action}"]

            final_response = None
            for del_url in dict.fromkeys(candidates):
                try:
                    r = session.post(del_url, json={}, timeout=5)
                    if r.status_code != 404:
                        final_response = r
                        break
                except: continue

            if final_response:
                try:
                    resp = final_response.json()
                    if resp.get('success'): return True, resp.get('msg')
                    else: return False, resp.get('msg')
                except: return False, f"响应解析失败: {final_response.text[:30]}"
            else: return False, "删除失败：API 路径未找到 (404)"

        except Exception as e: return False, f"异常: {str(e)}"

    success, msg = await run.io_bound(_do_delete_sync)
    if success:
        safe_notify(f"✅ 删除成功", "positive")
        if cb:
            res = cb()
            if asyncio.iscoroutine(res): await res
    else: safe_notify(f"❌ 删除失败: {msg}", "negative")


# ================= 带二次确认的删除逻辑 =================
async def delete_inbound_with_confirm(mgr, inbound_id, inbound_remark, callback):
    with ui.dialog() as d, ui.card():
        ui.label('删除确认').classes('text-lg font-bold text-red-600')
        ui.label(f"您确定要永久删除节点 [{inbound_remark}] 吗？").classes('text-base mt-2')
        ui.label("此操作不可恢复。").classes('text-xs text-gray-400 mb-4')
        
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def do_delete():
                d.close()
                # 调用原有的删除逻辑
                await delete_inbound(mgr, inbound_id, callback)
                
            ui.button('确定删除', color='red', on_click=do_delete)
    d.open()

# =================订阅编辑器 (包含 Token 编辑) =================
class SubEditor:
    def __init__(self, data=None):
        self.data = data
        if data:
            self.d = data.copy()
            # 🛡️ 安全修复：如果旧数据里没有 token，自动补全一个，防止报错
            if 'token' not in self.d:
                self.d['token'] = str(uuid.uuid4())
            if 'nodes' not in self.d:
                self.d['nodes'] = []
        else:
            self.d = {'name': '', 'token': str(uuid.uuid4()), 'nodes': []}
            
        self.sel = set(self.d.get('nodes', []))
        self.groups_data = {} 
        self.all_node_keys = set()
        self.name_input = None 
        self.token_input = None 

    def ui(self, dlg):
        # 外层卡片
        with ui.card().classes('w-[90vw] max-w-4xl p-0 bg-white').style('display: flex; flex-direction: column; height: 85vh;'):
            
            # 1. 标题栏
            with ui.row().classes('w-full justify-between items-center p-4 border-b bg-gray-50'):
                ui.label('订阅编辑器').classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense')
            
            # 2. 滚动区域
            with ui.element('div').classes('w-full flex-grow overflow-y-auto p-4').style('display: flex; flex-direction: column; gap: 1rem;'):
                
                # 订阅名称
                self.name_input = ui.input('订阅名称', value=self.d.get('name', '')).classes('w-full').props('outlined')
                self.name_input.on_value_change(lambda e: self.d.update({'name': e.value}))
                
                # 订阅路径 (Token)
                with ui.row().classes('w-full items-center gap-2'):
                    self.token_input = ui.input('订阅路径 (Token)', value=self.d.get('token', ''), placeholder='例如: my-phone').classes('flex-grow').props('outlined')
                    self.token_input.on_value_change(lambda e: self.d.update({'token': e.value.strip()}))
                    
                    # 随机生成按钮
                    ui.button(icon='refresh', on_click=lambda: self.token_input.set_value(str(uuid.uuid4()))).props('flat dense').tooltip('生成随机 UUID')

                # 全选工具栏
                with ui.row().classes('w-full items-center justify-between bg-gray-100 p-2 rounded'):
                    ui.label('节点列表').classes('font-bold ml-2')
                    with ui.row().classes('gap-2'):
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm color=primary')
                        ui.button('清空', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm color=red')

                # 列表容器
                self.cont = ui.column().classes('w-full').style('display: flex; flex-direction: column; gap: 10px;')
            
            # 3. 底部保存
            with ui.row().classes('w-full p-4 border-t'):
                async def save():
                    if self.name_input: self.d['name'] = self.name_input.value
                    
                    if self.token_input: 
                        new_token = self.token_input.value.strip()
                        if not new_token:
                            safe_notify("订阅路径不能为空", "negative")
                            return
                        # 查重逻辑
                        if (not self.data) or (self.data.get('token') != new_token):
                            for s in SUBS_CACHE:
                                if s.get('token') == new_token:
                                    safe_notify(f"路径 '{new_token}' 已被占用", "negative")
                                    return
                        self.d['token'] = new_token
                        
                    self.d['nodes'] = list(self.sel)
                    
                    if self.data: 
                        # 更新现有
                        try:
                            idx = SUBS_CACHE.index(self.data)
                            SUBS_CACHE[idx] = self.d
                        except:
                            SUBS_CACHE.append(self.d)
                    else: 
                        # 新建
                        SUBS_CACHE.append(self.d)
                    
                    await save_subs()
                    await load_subs_view()
                    dlg.close()
                    ui.notify('订阅保存成功', color='positive')

                ui.button('保存', icon='save', on_click=save).classes('w-full h-12 bg-slate-900 text-white')

        asyncio.create_task(self.load_data())

    async def load_data(self):
        with self.cont: 
            ui.spinner('dots').classes('self-center mt-10')

        tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in SERVERS_CACHE]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        self.groups_data = {}
        self.all_node_keys = set()

        for i, srv in enumerate(SERVERS_CACHE):
            nodes = results[i]
            if not nodes or isinstance(nodes, Exception): nodes = NODES_DATA.get(srv['url'], [])
            if nodes:
                for n in nodes:
                    k = f"{srv['url']}|{n['id']}"
                    self.all_node_keys.add(k)
            g_name = srv.get('group', '默认分组') or '默认分组'
            if g_name not in self.groups_data: self.groups_data[g_name] = []
            self.groups_data[g_name].append({'server': srv, 'nodes': nodes})

        self.render_list()

    def render_list(self):
        self.cont.clear()
        with self.cont:
            if not self.groups_data:
                ui.label('暂无数据').classes('text-center w-full mt-4')
                return

            sorted_groups = sorted(self.groups_data.keys())

            for g_name in sorted_groups:
                with ui.expansion(g_name, icon='folder', value=True).classes('w-full border rounded mb-2').style('width: 100%;'):
                    with ui.column().classes('w-full p-0').style('display: flex; flex-direction: column; width: 100%;'):
                        servers = self.groups_data[g_name]
                        for item in servers:
                            srv = item['server']
                            nodes = item['nodes']
                            with ui.column().classes('w-full p-2 border-b').style('display: flex; flex-direction: column; align-items: flex-start; width: 100%;'):
                                with ui.row().classes('items-center gap-2 mb-2'):
                                    ui.icon('dns', size='xs')
                                    ui.label(srv['name']).classes('font-bold')
                                if nodes:
                                    with ui.column().classes('w-full pl-4 gap-1').style('display: flex; flex-direction: column; width: 100%;'):
                                        for n in nodes:
                                            key = f"{srv['url']}|{n['id']}"
                                            cb = ui.checkbox(n['remark'], value=(key in self.sel))
                                            cb.classes('w-full text-sm dense').style('display: flex; width: 100%;')
                                            cb.on('update:model-value', lambda e, k=key: self.on_check(k, e.args))

    def on_check(self, key, value):
        if value: self.sel.add(key)
        else: self.sel.discard(key)

    def toggle_all(self, select_state):
        if select_state: self.sel.update(self.all_node_keys)
        else: self.sel.clear()
        self.render_list()


def open_sub_editor(d):
    with ui.dialog() as dlg: SubEditor(d).ui(dlg); dlg.open()


# ================= 探针页面渲染 (60秒刷新版) =================
async def render_probe_page():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'PROBE'
    CURRENT_VIEW_STATE['data'] = None

    content_container.clear()
    
    # 检查是否已开启探针功能 (默认为 False)
    is_probe_enabled = ADMIN_CONFIG.get('probe_enabled', False)
    
    # 如果没开启，显示空状态 + 弹窗引导
    if not is_probe_enabled:
        with content_container:
            with ui.column().classes('w-full h-[60vh] justify-center items-center opacity-50'):
                ui.icon('monitor_heart', size='6rem', color='grey-4')
                ui.label('探针功能未初始化').classes('text-2xl font-bold text-gray-400')

            with ui.dialog() as d, ui.card().classes('w-full max-w-md p-6'):
                with ui.column().classes('w-full items-center gap-4'):
                    ui.icon('rocket_launch', size='4rem').classes('text-blue-500 animate-bounce')
                    ui.label('开启实时监控系统').classes('text-xl font-bold text-slate-800')
                    ui.label('为了实现秒级监控，系统将自动为您的服务器配置轻量级探针。').classes('text-sm text-gray-500 text-center')
                    ui.label('是否立即开始配置？').classes('text-sm font-bold text-slate-700 mt-2')
                    
                    with ui.row().classes('w-full gap-4 mt-2'):
                        ui.button('暂不开启', on_click=lambda: [d.close(), ui.navigate.to('/')]).props('flat color=grey').classes('flex-1')
                        async def confirm_enable():
                            d.close()
                            ADMIN_CONFIG['probe_enabled'] = True
                            await save_admin_config()
                            await render_probe_page()
                            await batch_install_all_probes()
                        ui.button('确认并安装', on_click=confirm_enable).props('unelevated color=blue').classes('flex-1 shadow-lg')
            d.open()
        return

    # === 正常渲染逻辑 ===
    global card_refs 
    card_refs = {}

    with content_container:
        # --- 顶部标题栏 ---
        with ui.row().classes('w-full items-center justify-between mb-4'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('dns', color='primary').classes('text-2xl')
                ui.label('服务器监控墙 (Live Status)').classes('text-2xl font-bold text-slate-800')
                ui.badge(f'{len(SERVERS_CACHE)} 台', color='blue').props('outline')
            
            # 手动刷新按钮 (is_manual=True 会有弹窗提示)
            ui.button('刷新状态', icon='refresh', on_click=lambda: update_probe_stats(card_refs, is_manual=True)).props('color=primary unelevated')

        # --- 卡片网格 ---
        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4'):
            sorted_servers = sorted(SERVERS_CACHE, key=lambda x: smart_sort_key(x))
            for s in sorted_servers:
                url = s['url']
                refs = {} 
                with ui.card().classes('w-full p-3 shadow-sm hover:shadow-md transition border border-gray-200 bg-white gap-1'):
                    # 1. 头部
                    with ui.row().classes('w-full justify-between items-center mb-2 border-b border-gray-100 pb-2'):
                        with ui.row().classes('items-center gap-2 overflow-hidden'):
                            flag = "🏳️"
                            try: flag = detect_country_group(s['name']).split(' ')[0]
                            except: pass
                            ui.label(flag).classes('text-lg')
                            ui.label(s['name']).classes('font-bold text-slate-700 truncate text-sm')
                        refs['badge'] = ui.badge('Wait', color='grey').classes('text-xs')

                    # 2. 系统
                    with ui.row().classes('w-full justify-between text-xs text-gray-400 mb-2'):
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('terminal', size='xs'); refs['os'] = ui.label('Linux')
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('schedule', size='xs'); refs['uptime'] = ui.label('--')

                    # 3. 进度条
                    with ui.row().classes('w-full items-center gap-2 text-xs mb-1'):
                        ui.label('CPU').classes('w-8 font-bold text-slate-500')
                        refs['cpu_bar'] = ui.linear_progress(0, size='6px', color='blue').classes('flex-grow rounded')
                        refs['cpu_val'] = ui.label('0%').classes('w-8 text-right font-mono')
                    with ui.row().classes('w-full items-center gap-2 text-xs mb-1'):
                        ui.label('MEM').classes('w-8 font-bold text-slate-500')
                        refs['mem_bar'] = ui.linear_progress(0, size='6px', color='green').classes('flex-grow rounded')
                        refs['mem_val'] = ui.label('0%').classes('w-8 text-right font-mono')
                    with ui.row().classes('w-full items-center gap-2 text-xs mb-1'):
                        ui.label('DSK').classes('w-8 font-bold text-slate-500')
                        refs['disk_bar'] = ui.linear_progress(0, size='6px', color='purple').classes('flex-grow rounded')
                        refs['disk_val'] = ui.label('0%').classes('w-8 text-right font-mono')

                    # 4. 负载
                    with ui.row().classes('w-full justify-between items-center mt-2 pt-2 border-t border-dashed border-gray-100'):
                        ui.label('Load Avg').classes('text-[10px] text-gray-400 font-bold')
                        refs['load'] = ui.label('- / - / -').classes('text-[10px] text-slate-600 font-mono bg-slate-100 px-1 rounded')

                card_refs[url] = refs

        # ✅✅✅ [关键修改] 设置定时器为 60.0 秒 (即 1 分钟) ✅✅✅
        ui.timer(60.0, lambda: update_probe_stats(card_refs))
        
        # 首次进入页面立即执行一次，让用户不用干等 1 分钟
        asyncio.create_task(update_probe_stats(card_refs))

        
# ================= 批量刷新卡片数据 (无闪烁/静默更新版) =================
# 全局锁，防止定时器重叠执行
PROBE_LOCK = False

async def update_probe_stats(card_refs, is_manual=False):
    global PROBE_LOCK
    
    # 1. 只有当页面还在显示时才执行
    if CURRENT_VIEW_STATE.get('scope') != 'PROBE': return

    # 2. 如果正在运行，且不是手动强制刷新，则跳过本次定时任务
    if PROBE_LOCK and not is_manual:
        # logger.info("⏳ 上次探针任务未完成，跳过本次定时刷新")
        return

    PROBE_LOCK = True
    
    # 仅手动点击时，在右上角给一个轻微提示，但不改动卡片状态
    if is_manual:
        safe_notify('正在刷新服务器状态...', 'ongoing')

    # ❌❌❌ [已删除] 不再将所有卡片重置为橙色，保持现有状态直到新数据到来 ❌❌❌
    # for refs in card_refs.values():
    #     try: refs['badge'].props('color=orange') ...
    #     except: pass

    # 3. 定义并发限制
    sema = asyncio.Semaphore(15) 

    async def check_one(srv):
        url = srv['url']
        refs = card_refs.get(url)
        if not refs: return 

        async with sema:
            # 获取数据 (优先HTTP，回退SSH)
            res = await get_server_status(srv)
            
            # --- 更新 UI ---
            try:
                # 再次检查页面元素是否存在
                if refs['badge'].is_deleted: return

                if res and res['status'] == 'online':
                    # === 在线处理 ===
                    # 只有当之前不是 Online 或者由红变绿时，这里才会产生视觉变化
                    # 如果本来就是绿的，用户感觉不到闪烁，只会看到数字跳动
                    refs['badge'].set_text('Online')
                    refs['badge'].props('color=green')
                    
                    # 更新数值
                    try:
                        load_val = float(res['load'])
                        refs['cpu_val'].set_text(f"{load_val}")
                        load_pct = min(load_val * 20, 100)
                        refs['cpu_bar'].set_value(load_pct / 100)
                        refs['cpu_bar'].props(f'color={"red" if load_val > 4 else "blue"}')
                    except: pass

                    mem_p = res['mem']
                    refs['mem_bar'].set_value(mem_p / 100)
                    refs['mem_bar'].props(f'color={"red" if mem_p > 90 else ("orange" if mem_p > 75 else "green")}')
                    refs['mem_val'].set_text(f"{int(mem_p)}%")

                    disk_p = res['disk']
                    refs['disk_bar'].set_value(disk_p / 100)
                    refs['disk_bar'].props(f'color={"red" if disk_p > 90 else "purple"}')
                    refs['disk_val'].set_text(f"{int(disk_p)}%")

                    refs['uptime'].set_text(res['uptime'])
                    refs['load'].set_text(f"Load: {res['load']}")

                else:
                    # === 离线处理 ===
                    # 只有真的检测失败了，才变红
                    refs['badge'].set_text('Offline')
                    refs['badge'].props('color=red')
                    
                    # 离线时，可以选择清空进度条，或者保持最后一次的数值
                    # 这里选择清零，直观显示断连
                    refs['cpu_bar'].set_value(0)
                    refs['mem_bar'].set_value(0)
                    refs['disk_bar'].set_value(0)
                    
            except: 
                pass

    # 4. 执行任务
    try:
        tasks = [check_one(s) for s in SERVERS_CACHE]
        await asyncio.gather(*tasks)
    finally:
        PROBE_LOCK = False # 释放锁
        if is_manual:
            safe_notify('✅ 状态刷新完毕', 'positive')


    
# ================= 订阅管理视图 (极简模式：只显在线) =================
async def load_subs_view():
    # ✨✨✨ [新增] 标记当前在订阅管理 ✨✨✨
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'SUBS'
    CURRENT_VIEW_STATE['data'] = None
    show_loading(content_container)
    try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
    except: origin = ""
    if not origin: origin = "https://xui-manager.sijuly.nyc.mn"

    content_container.clear()
    
    # 1. 预先统计所有当前"活着"的节点 Key (确保是节点粒度)
    all_active_keys = set()
    for srv in SERVERS_CACHE:
        # NODES_DATA 是实时的，如果服务器挂了，之前那个修复会让这里为空列表
        nodes = NODES_DATA.get(srv['url'], [])
        if nodes:
            for n in nodes:
                # 这里的 key 是 URL + NodeID，确保是唯一的节点标识
                key = f"{srv['url']}|{n['id']}"
                all_active_keys.add(key)

    with content_container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4')
        with ui.row().classes('w-full mb-4 justify-end'): 
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_sub_editor(None))
        
        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes('w-full p-4 mb-2 shadow-sm hover:shadow-md transition border-l-4 border-blue-500'):
                with ui.row().classes('justify-between w-full items-center'):
                    with ui.column().classes('gap-1'):
                        # 订阅标题
                        ui.label(sub['name']).classes('font-bold text-lg text-slate-800')
                        
                        # 计算在线节点数
                        saved_node_ids = set(sub.get('nodes', []))
                        # 取交集：订阅记录的 ID  VS  当前全局在线的 ID
                        valid_count = len(saved_node_ids.intersection(all_active_keys))
                        
                        # ✨ 只显示这一行动态数据
                        color_cls = 'text-green-600' if valid_count > 0 else 'text-gray-400'
                        ui.label(f"⚡ 在线节点: {valid_count}").classes(f'text-xs font-bold {color_cls}')
                    
                    with ui.row().classes('gap-2'):
                        ui.button(icon='tune', on_click=lambda s=sub: open_process_editor(s)).props('flat dense color=purple').tooltip('配置处理策略')
                        ui.button(icon='edit', on_click=lambda s=sub: open_sub_editor(s)).props('flat dense color=blue').tooltip('编辑订阅内容')
                        async def dl(i=idx): 
                            del SUBS_CACHE[i]
                            await save_subs()
                            await load_subs_view()
                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense')

                ui.separator().classes('my-2')
                
                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"
                
                with ui.row().classes('w-full items-center gap-2 bg-gray-50 p-2 rounded justify-between'):
                    with ui.row().classes('items-center gap-2 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-gray-400')
                        ui.label(raw_url).classes('text-xs font-mono text-gray-600 truncate')
                    
                    with ui.row().classes('gap-1'):
                        ui.button(icon='content_copy', on_click=lambda u=raw_url: safe_copy_to_clipboard(u)).props('flat dense round size=sm color=grey').tooltip('复制原始链接')
                        
                        surge_short = f"{origin}/get/sub/surge/{sub['token']}"
                        ui.button(icon='bolt', on_click=lambda u=surge_short: safe_copy_to_clipboard(u)).props('flat dense round size=sm text-color=orange').tooltip('复制 Surge 订阅')
                        
                        clash_short = f"{origin}/get/sub/clash/{sub['token']}"
                        ui.button(icon='cloud_queue', on_click=lambda u=clash_short: safe_copy_to_clipboard(u)).props('flat dense round size=sm text-color=green').tooltip('复制 Clash 订阅')
                        
# ================= 订阅策略编辑器  =================
class SubscriptionProcessEditor:
    def __init__(self, sub_data):
        self.sub_data = sub_data
        # 初始化默认 options
        if 'options' not in self.sub_data:
            self.sub_data['options'] = {
                'emoji': True,
                'udp': True,
                'sort': False,
                'tfo': False,
                'skip_cert': True,
                'include_regex': '',
                'exclude_regex': '',
                'rename_pattern': '',       
                'rename_replacement': '', 
                'regions': []
            }
        self.opt = self.sub_data['options']
        
        self.raw_nodes = []
        self.preview_nodes = []
        self.collect_raw_nodes()
        self.update_preview()

    def collect_raw_nodes(self):
        self.raw_nodes = []
        sub_nodes_set = set(self.sub_data.get('nodes', []))
        for srv in SERVERS_CACHE:
            nodes = NODES_DATA.get(srv['url'], [])
            for n in nodes:
                key = f"{srv['url']}|{n['id']}"
                if key in sub_nodes_set:
                    self.raw_nodes.append({
                        'name': n['remark'],
                        'original_name': n['remark'],
                        'server_name': srv['name']
                    })

    def update_preview(self):
        """核心：模拟 SubConverter 逻辑生成预览"""
        import re
        
        result = []
        selected_regions = set(self.opt.get('regions', []))
        
        for node in self.raw_nodes:
            current_node = node.copy()
            name = current_node['name']
            
            # 1. 区域过滤
            node_region = detect_country_group(name)
            if selected_regions and node_region not in selected_regions: continue
            
            # 2. 正则保留 (Include)
            inc_reg = self.opt.get('include_regex', '').strip()
            if inc_reg:
                try: 
                    if not re.search(inc_reg, name, re.IGNORECASE): continue
                except: pass
            
            # 3. 正则排除 (Exclude)
            exc_reg = self.opt.get('exclude_regex', '').strip()
            if exc_reg:
                try:
                    if re.search(exc_reg, name, re.IGNORECASE): continue
                except: pass

            # ✨✨✨ 4. 正则重命名 (Rename) ✨✨✨
            ren_pat = self.opt.get('rename_pattern', '').strip()
            ren_rep = self.opt.get('rename_replacement', '').strip()
            if ren_pat:
                try:
                    # 兼容性处理：用户习惯用 $1, $2 表示分组，但 Python re 使用 \1, \2
                    # 我们简单做一个替换，把 $ 换成 \ (仅在 \ 未被转义时)
                    py_rep = ren_rep.replace('$', '\\')
                    name = re.sub(ren_pat, py_rep, name)
                    current_node['name'] = name # 更新名字供后续使用
                except: pass

            # 5. 自动国旗
            if self.opt.get('emoji', True):
                # 重新检测区域（因为名字可能变了，或者利用旧名字检测）
                # 这里还是用原始名字检测区域比较稳妥
                flag = node_region.split(' ')[0] 
                if flag and flag not in name: # 保持“有了就不加”的逻辑
                     current_node['name'] = f"{flag} {name}"
            
            result.append(current_node)
        
        # 6. 排序
        if self.opt.get('sort', False):
            result.sort(key=lambda x: x['name'])
            
        self.preview_nodes = result
        if hasattr(self, 'preview_container'): self.render_preview_ui()

    def ui(self, dlg):
        with ui.card().classes('w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden bg-white'):
            # --- 标题栏 ---
            with ui.row().classes('w-full justify-between items-center p-4 bg-white border-b shadow-sm z-20'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', color='primary').classes('text-xl')
                    ui.label(f"订阅策略: {self.sub_data.get('name', '未命名')}").classes('text-lg font-bold text-slate-800')
                with ui.row().classes('gap-2'):
                    ui.button('取消', on_click=dlg.close).props('flat color=grey')
                    ui.button('保存配置', icon='save', on_click=lambda: [self.save(), dlg.close(), safe_notify('策略已更新', 'positive')]).classes('bg-slate-900 text-white shadow-lg')

            # --- 内容区 ---
            with ui.row().classes('w-full flex-grow overflow-hidden gap-0'):
                # 左侧预览
                with ui.column().classes('w-[350px] flex-shrink-0 h-full border-r bg-gray-50 flex flex-col'):
                    with ui.row().classes('w-full p-3 bg-white border-b justify-between items-center'):
                        ui.label('效果预览').classes('text-xs font-bold text-gray-500')
                        self.count_label = ui.badge(f'{len(self.preview_nodes)}', color='blue')
                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.render_preview_ui()

                # 右侧配置
                with ui.column().classes('flex-grow h-full overflow-y-auto bg-white'):
                    with ui.column().classes('w-full max-w-3xl mx-auto p-8 gap-6'):
                        
                        # 1. 基础开关
                        ui.label('基础处理').classes('text-sm font-bold text-gray-900')
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                            self._render_switch('自动添加国旗 (Emoji)', 'emoji', 'flag')
                            self._render_switch('节点自动排序 (A-Z)', 'sort', 'sort_by_alpha')
                            self._render_switch('强制开启 UDP 转发', 'udp', 'rocket_launch')
                            self._render_switch('跳过证书验证', 'skip_cert', 'lock_open')
                            self._render_switch('TCP Fast Open', 'tfo', 'speed')
                        ui.separator()

                        # ✨✨✨ 2. 正则重命名 (新增) ✨✨✨
                        ui.label('正则重命名 (Rename)').classes('text-sm font-bold text-gray-900')
                        with ui.card().classes('w-full p-4 border border-gray-200 shadow-none bg-blue-50'):
                            with ui.row().classes('w-full items-center gap-2 mb-2'):
                                ui.icon('edit_note').classes('text-blue-500')
                                ui.label('支持正则匹配与替换 (可以使用 $1, $2 引用分组)').classes('text-xs text-blue-600')
                            
                            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 gap-4'):
                                with ui.input('匹配正则 (Pattern)', placeholder='例如: Oracle\|(.*)', value=self.opt.get('rename_pattern', '')) \
                                    .props('outlined dense clearable bg-white').classes('w-full') as i_pat:
                                    i_pat.on_value_change(lambda e: [self.opt.update({'rename_pattern': e.value}), self.update_preview()])
                                
                                with ui.input('替换为 (Replacement)', placeholder='例如: $1', value=self.opt.get('rename_replacement', '')) \
                                    .props('outlined dense clearable bg-white').classes('w-full') as i_rep:
                                    i_rep.on_value_change(lambda e: [self.opt.update({'rename_replacement': e.value}), self.update_preview()])
                        ui.separator()

                        # 3. 正则过滤
                        ui.label('正则过滤').classes('text-sm font-bold text-gray-900')
                        with ui.column().classes('w-full gap-3'):
                            with ui.input('保留匹配 (Include)', placeholder='例如: 香港|SG', value=self.opt.get('include_regex', '')) \
                                .props('outlined dense clearable').classes('w-full') as i1:
                                i1.on_value_change(lambda e: [self.opt.update({'include_regex': e.value}), self.update_preview()])
                            with ui.input('排除匹配 (Exclude)', placeholder='例如: 过期|剩余', value=self.opt.get('exclude_regex', '')) \
                                .props('outlined dense clearable').classes('w-full') as i2:
                                i2.on_value_change(lambda e: [self.opt.update({'exclude_regex': e.value}), self.update_preview()])
                        ui.separator()

                        # 4. 区域过滤
                        with ui.row().classes('w-full justify-between items-end'):
                            ui.label('区域过滤').classes('text-sm font-bold text-gray-900')
                            with ui.row().classes('gap-1'):
                                ui.button('全选', on_click=lambda: self.toggle_regions(True)).props('flat dense size=xs color=primary')
                                ui.button('清空', on_click=lambda: self.toggle_regions(False)).props('flat dense size=xs color=grey')
                        
                        with ui.card().classes('w-full p-4 border border-gray-200 shadow-none bg-gray-50'):
                            with ui.grid().classes('w-full grid-cols-2 md:grid-cols-3 gap-2'):
                                all_regions = set()
                                for node in self.raw_nodes: all_regions.add(detect_country_group(node['original_name']))
                                self.region_checks = {}
                                current_selected = set(self.opt.get('regions', []))
                                for reg in sorted(list(all_regions)):
                                    chk = ui.checkbox(reg, value=(reg in current_selected)).classes('text-xs')
                                    chk.on_value_change(lambda e: [self.sync_regions_opt(), self.update_preview()])
                                    self.region_checks[reg] = chk
                        
                        ui.element('div').classes('h-20')

    def render_preview_ui(self):
        self.preview_container.clear()
        self.count_label.text = f'{len(self.preview_nodes)}'
        with self.preview_container:
            if not self.preview_nodes:
                ui.label('无匹配节点').classes('text-xs text-center text-gray-400 mt-4')
                return
            for i, node in enumerate(self.preview_nodes):
                if i > 100:
                    ui.label(f'... 还有 {len(self.preview_nodes)-100} 个').classes('text-xs text-center text-gray-400')
                    break
                with ui.row().classes('w-full p-2 bg-white border border-gray-100 rounded items-center gap-2 hover:border-blue-300 transition'):
                    ui.label(str(i+1)).classes('text-[10px] text-gray-300 w-4')
                    ui.label(node['name']).classes('text-xs font-bold text-gray-700 truncate flex-grow')

    def _render_switch(self, label, key, icon):
        val = self.opt.get(key, False)
        with ui.card().classes('p-3 border border-gray-200 shadow-none flex-row items-center justify-between hover:bg-gray-50 transition cursor-pointer'):
            with ui.row().classes('items-center gap-3'):
                ui.icon(icon).classes('text-lg text-blue-500')
                ui.label(label).classes('text-sm font-medium text-gray-700 select-none')
            sw = ui.switch(value=val).props('dense color=primary')
            ui.context.client.layout.on('click', lambda: sw.toggle()) 
            sw.on_value_change(lambda e: [self.opt.update({key: e.value}), self.update_preview()])

    def sync_regions_opt(self):
        self.opt['regions'] = [r for r, chk in self.region_checks.items() if chk.value]

    def toggle_regions(self, state):
        for chk in self.region_checks.values(): chk.value = state
        self.sync_regions_opt(); self.update_preview()

    def save(self): asyncio.create_task(save_subs())

# 打开策略编辑器的入口函数
def open_process_editor(sub_data):
    with ui.dialog() as d: SubscriptionProcessEditor(sub_data).ui(d); d.open()

                        
# ================= 小巧卡片式弹窗 (带切换功能 & 自动探针安装) =================
async def open_server_dialog(idx=None):
    is_edit = idx is not None
    data = SERVERS_CACHE[idx] if is_edit else {}
    
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm p-5 flex flex-col gap-4'):
        
        # 1. 标题栏
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('编辑服务器' if is_edit else '添加服务器').classes('text-lg font-bold')
            tabs = ui.tabs().classes('text-blue-600')
            with tabs:
                t_xui = ui.tab('面板', icon='settings')
                t_ssh = ui.tab('SSH', icon='terminal')

        # 2. 变量绑定
        name = ui.input(value=data.get('name',''), label='备注名称 (留空自动获取)').classes('w-full').props('outlined dense')
        group = ui.select(options=get_all_groups(), value=data.get('group','默认分组'), new_value_mode='add-unique', label='分组').classes('w-full').props('outlined dense')
        
        # 3. 内容面板区域
        with ui.tab_panels(tabs, value=t_xui).classes('w-full animated fadeIn'):
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                url = ui.input(value=data.get('url',''), label='面板 URL (http://ip:port)').classes('w-full').props('outlined dense')
                with ui.row().classes('w-full gap-2'):
                    user = ui.input(value=data.get('user',''), label='账号').classes('flex-1').props('outlined dense')
                    pwd = ui.input(value=data.get('pass',''), label='密码', password=True).classes('flex-1').props('outlined dense')
                prefix = ui.input(value=data.get('prefix',''), label='API 前缀 (选填)').classes('w-full').props('outlined dense')

            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                with ui.row().classes('w-full gap-2'):
                    ssh_user = ui.input(value=data.get('ssh_user','root'), label='SSH 用户').classes('flex-1').props('outlined dense')
                    ssh_port = ui.input(value=data.get('ssh_port','22'), label='端口').classes('w-1/3').props('outlined dense')
                
                auth_type = ui.select(['全局密钥', '独立密码', '独立密钥'], value=data.get('ssh_auth_type', '全局密钥'), label='认证方式').classes('w-full').props('outlined dense options-dense')
                ssh_pwd = ui.input(label='SSH 密码', password=True, value=data.get('ssh_password','')).classes('w-full').props('outlined dense')
                ssh_key = ui.textarea(label='SSH 私钥', value=data.get('ssh_key','')).classes('w-full').props('outlined dense rows=3 input-class=font-mono text-xs')
                
                ssh_pwd.bind_visibility_from(auth_type, 'value', value='独立密码')
                ssh_key.bind_visibility_from(auth_type, 'value', value='独立密钥')
                ui.label('✅ 将自动使用全局私钥连接').bind_visibility_from(auth_type, 'value', value='全局密钥').classes('text-green-600 text-xs text-center mt-2')

        # 4. 底部按钮
        with ui.row().classes('w-full justify-end gap-2 mt-2'):
            if is_edit:
                async def delete():
                    # 1. 先删数据
                    if idx < len(SERVERS_CACHE): del SERVERS_CACHE[idx]
                    await save_servers()
                    
                    # 2. 先关窗 (防止弹窗遮挡刷新效果)
                    d.close()
                    
                    # 3. 再刷新 UI
                    render_sidebar_content.refresh() # 刷新左侧
                    await refresh_content('ALL') # 强制右侧回到“所有服务器”列表
                    safe_notify('服务器已删除', 'positive')
                    
                ui.button('删除', on_click=delete, color='red').props('flat dense')

            async def save():
                # 1. 自动命名逻辑 (如果为空)
                final_name = name.value.strip()
                temp_conf = {'url': url.value, 'user': user.value, 'pass': pwd.value, 'prefix': prefix.value}
                
                if not final_name:
                    safe_notify("正在智能获取名称...", "ongoing")
                    final_name = await generate_smart_name(temp_conf)
                
                # 2. 自动补全国旗逻辑
                final_name = await auto_prepend_flag(final_name, url.value)

                new_data = {
                    'name': final_name, 'group': group.value,
                    'url': url.value, 'user': user.value, 'pass': pwd.value, 'prefix': prefix.value,
                    'ssh_port': ssh_port.value, 'ssh_user': ssh_user.value,
                    'ssh_auth_type': auth_type.value, 'ssh_password': ssh_pwd.value, 'ssh_key': ssh_key.value
                }
                
                # 3. 更新数据到内存
                if is_edit: SERVERS_CACHE[idx].update(new_data)
                else: SERVERS_CACHE.append(new_data)
                
                # 4. 保存并刷新界面
                await save_servers()
                render_sidebar_content.refresh()
                await refresh_content('SINGLE', SERVERS_CACHE[idx] if is_edit else SERVERS_CACHE[-1], force_refresh=True)
                d.close()
                safe_notify(f'保存成功: {final_name}', 'positive')

                # ✨✨✨ [新增] 如果已启用探针，自动为新/修改的服务器安装探针 ✨✨✨
                if ADMIN_CONFIG.get('probe_enabled', False):
                    # 异步后台执行，不阻塞 UI
                    asyncio.create_task(install_probe_on_server(new_data))
                    safe_notify(f"正在后台为 {final_name} 配置探针...", "info")
            
            ui.button('保存配置', on_click=save).classes('bg-slate-900 text-white shadow-lg')
    d.open()
    


# 辅助函数：获取所有唯一分组名（包括主分组、Tags和自定义空分组）
def get_all_groups_set():
    groups = set()
    # 1. 现有服务器的主分组和Tags
    for s in SERVERS_CACHE:
        if s.get('group'): groups.add(s['group'])
        if s.get('tags'): groups.update(s['tags'])
    # 2. 预设的自定义分组
    if 'custom_groups' in ADMIN_CONFIG:
        groups.update(ADMIN_CONFIG['custom_groups'])
    
    # 3. 保证基本分组存在
    groups.add('默认分组')
    return groups

def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('新建自定义分组').classes('text-lg font-bold mb-2')
        
        # ✨ 修改点：只保留名称输入框，去掉了 server_select 下拉框
        name_input = ui.input('分组名称', placeholder='例如: 微软云 / 生产环境').classes('w-full').props('outlined')
        
        async def save_new_group():
            new_name = name_input.value.strip()
            if not new_name:
                safe_notify("分组名称不能为空", "warning")
                return
            
            # 检查是否重名
            existing_groups = get_all_groups_set()
            if new_name in existing_groups:
                safe_notify("该分组已存在", "warning")
                return

            # ✨ 修改点：保存到 ADMIN_CONFIG，而不是去修改服务器数据
            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            d.close()
            render_sidebar_content.refresh()
            safe_notify(f"已创建分组: {new_name}", "positive")

        with ui.row().classes('w-full justify-end gap-2 mt-4'):
             ui.button('取消', on_click=d.close).props('flat color=grey')
             ui.button('保存', on_click=save_new_group).classes('bg-blue-600 text-white')
    d.open()
    
# ================= [极简导出版 - 完美居中] 数据备份/恢复 =================
async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden'):
        
        # 顶部 Tab
        with ui.tabs().classes('w-full bg-gray-50 flex-shrink-0 border-b') as tabs:
            tab_export = ui.tab('完整备份 (导出)')
            tab_import = ui.tab('恢复 / 批量添加')
            
        with ui.tab_panels(tabs, value=tab_import).classes('w-full p-6 overflow-y-auto flex-grow'):
            # --- 面板 A: 导出 ---
            with ui.tab_panel(tab_export).classes('flex flex-col gap-8 items-center justify-center h-full'):
                full_backup = {
                    "version": "3.0", "timestamp": __import__('time').time(),
                    "servers": SERVERS_CACHE, "subscriptions": SUBS_CACHE,
                    "admin_config": ADMIN_CONFIG, "global_ssh_key": load_global_key(), "cache": NODES_DATA
                }
                json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)
                
                with ui.column().classes('items-center gap-2'):
                    ui.icon('cloud_download', size='5rem', color='primary').classes('opacity-90')
                    ui.label('备份数据已准备就绪').classes('text-xl font-bold text-gray-700 tracking-wide')
                    ui.label(f'包含 {len(SERVERS_CACHE)} 个服务器配置').classes('text-xs text-gray-400')

                with ui.column().classes('w-full max-w-md gap-4'):
                    ui.button('复制到剪贴板', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).classes('w-full h-12 text-base font-bold bg-blue-600 text-white shadow-lg rounded-lg hover:scale-105 transition')
                    ui.button('下载 .json 文件', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_manager_backup_v3.json')).classes('w-full h-12 text-base font-bold bg-green-600 text-white shadow-lg rounded-lg hover:scale-105 transition')

            # --- 面板 B: 导入 & 批量添加 ---
            with ui.tab_panel(tab_import).classes('flex flex-col gap-6'):
                # === 功能区 1: 恢复备份 ===
                with ui.expansion('方式一：恢复 JSON 备份文件', icon='restore', value=False).classes('w-full border rounded bg-gray-50'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        import_text = ui.textarea(placeholder='粘贴备份 JSON...').classes('w-full h-32 font-mono text-xs bg-white')
                        with ui.row().classes('w-full gap-4 items-center'):
                            overwrite_chk = ui.checkbox('覆盖同名服务器', value=False).props('dense')
                            restore_key_chk = ui.checkbox('恢复 SSH 密钥', value=True).props('dense')
                            restore_sub_chk = ui.checkbox('恢复订阅设置', value=True).props('dense')
                        
                        async def process_json_import():
                            try:
                                raw = import_text.value.strip()
                                if not raw: safe_notify("内容不能为空", 'warning'); return
                                data = json.loads(raw)
                                new_servers = data.get('servers', []) if isinstance(data, dict) else data
                                new_subs = data.get('subscriptions', []); new_config = data.get('admin_config', {})
                                new_ssh_key = data.get('global_ssh_key', ''); new_cache = data.get('cache', {})

                                added = 0; updated = 0
                                existing_map = {s['url']: i for i, s in enumerate(SERVERS_CACHE)}
                                for item in new_servers:
                                    url = item.get('url')
                                    if url in existing_map:
                                        if overwrite_chk.value: SERVERS_CACHE[existing_map[url]] = item; updated += 1
                                    else: SERVERS_CACHE.append(item); existing_map[url] = len(SERVERS_CACHE) - 1; added += 1

                                if restore_key_chk.value and new_ssh_key: save_global_key(new_ssh_key)
                                if restore_sub_chk.value:
                                    if new_subs: global SUBS_CACHE; SUBS_CACHE = new_subs
                                    if new_config: global ADMIN_CONFIG; ADMIN_CONFIG.update(new_config)
                                if new_cache: NODES_DATA.update(new_cache); await save_nodes_cache()

                                await save_servers(); await save_subs(); await save_admin_config()
                                render_sidebar_content.refresh()
                                safe_notify(f"恢复完成: +{added} / ~{updated}", 'positive'); d.close()
                                if content_container: content_container.clear()
                            except Exception as e: safe_notify(f"错误: {e}", 'negative')
                        ui.button('执行恢复', on_click=process_json_import).classes('w-full bg-slate-800 text-white')

                # === 功能区 2: 批量添加 ===
                with ui.expansion('方式二：批量添加服务器 (支持 纯IP / SSH)', icon='playlist_add', value=True).classes('w-full border rounded bg-white shadow-sm'):
                    with ui.column().classes('p-4 gap-4 w-full'):
                        ui.label('批量输入 (每行一个，支持 IP 或 URL)').classes('text-xs font-bold text-gray-500')
                        url_area = ui.textarea(placeholder='192.168.1.10\n192.168.1.11:2202\nhttp://example.com:54321').classes('w-full h-32 font-mono text-sm bg-gray-50').props('outlined')
                        ui.separator()
                        
                        # --- 默认设置区域 ---
                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            def_ssh_user = ui.input('默认 SSH 用户', value='root').props('dense outlined')
                            def_ssh_port = ui.input('默认 SSH 端口', value='22').props('dense outlined')
                            def_ssh_pwd  = ui.input('默认 SSH 密码 (选填)').props('dense outlined placeholder="留空则用全局密钥"')
                            def_xui_port = ui.input('默认 X-UI 端口', value='54321').props('dense outlined')
                            def_xui_user = ui.input('默认 X-UI 账号', value='admin').props('dense outlined')
                            def_xui_pass = ui.input('默认 X-UI 密码', value='admin').props('dense outlined')

                        async def run_batch_import():
                            raw_text = url_area.value.strip()
                            if not raw_text: safe_notify("请输入内容", "warning"); return
                            
                            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                            count = 0
                            existing_urls = {s['url'] for s in SERVERS_CACHE}
                            
                            # 准备后台自动安装任务
                            install_tasks = []
                            
                            for line in lines:
                                target_ssh_port = def_ssh_port.value
                                target_xui_port = def_xui_port.value
                                if '://' in line:
                                    final_url = line
                                    try: parsed = urlparse(line); name = parsed.hostname or line
                                    except: name = line
                                else:
                                    if ':' in line and not line.startswith('['): 
                                        parts = line.split(':'); host_ip = parts[0]; target_ssh_port = parts[1]
                                    else: host_ip = line
                                    final_url = f"http://{host_ip}:{target_xui_port}"; name = host_ip

                                if final_url in existing_urls: continue
                                auth_type = '独立密码' if def_ssh_pwd.value.strip() else '全局密钥'
                                
                                # ✨✨✨ 核心修改：Group 留空，不设为“默认分组” ✨✨✨
                                # 这样可以让 GeoIP 任务后续自动接管并分类
                                new_server = {
                                    'name': name, 
                                    'group': '',  # <--- 关键：留空！
                                    'url': final_url,
                                    'user': def_xui_user.value, 'pass': def_xui_pass.value, 'prefix': '',
                                    'ssh_user': def_ssh_user.value, 'ssh_port': target_ssh_port,
                                    'ssh_auth_type': auth_type, 'ssh_password': def_ssh_pwd.value, 'ssh_key': ''
                                }

                                # 简单的自动命名 (IP -> Name)
                                if name == host_ip or name == final_url:
                                    # 尝试保留原始 IP 作为名字，等待后台 GeoIP 任务来修正加国旗
                                    new_server['name'] = name 

                                SERVERS_CACHE.append(new_server)
                                existing_urls.add(final_url)
                                count += 1
                                
                                # 如果开启了探针，加入安装队列
                                if ADMIN_CONFIG.get('probe_enabled', False):
                                    install_tasks.append(install_probe_on_server(new_server))

                            if count > 0:
                                await save_servers()
                                render_sidebar_content.refresh()
                                safe_notify(f"成功添加 {count} 台服务器", 'positive')
                                d.close()
                                
                                # 后台并发安装探针
                                if install_tasks:
                                    safe_notify(f"正在后台为 {len(install_tasks)} 台新服务器配置探针...", "ongoing")
                                    asyncio.create_task(asyncio.gather(*install_tasks))
                            else: safe_notify("未添加任何服务器 (可能已存在)", 'warning')

                        ui.button('确认批量添加', icon='add_box', on_click=run_batch_import).classes('w-full bg-blue-600 text-white h-10')
    d.open()
    
# ================= 渲染逻辑 =================

# 辅助函数：格式化流量
def format_bytes(size):
    if not size: return '0 B'
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

# ================= 智能排序逻辑=================
import re

CN_NUM_MAP = {'〇':0, '零':0, '一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9}

def cn_to_arabic_str(match):
    s = match.group()
    if not s: return s
    if '十' in s:
        val = 0
        parts = s.split('十')
        if parts[0]: val += CN_NUM_MAP.get(parts[0], 0) * 10
        else: val += 10
        if len(parts) > 1 and parts[1]: val += CN_NUM_MAP.get(parts[1], 0)
        return str(val)
    return "".join(str(CN_NUM_MAP.get(c, 0)) for c in s)

def to_safe_sort_list(items):
    """确保列表可排序：[(权重, 值), ...]"""
    safe_list = []
    for item in items:
        if isinstance(item, int):
            safe_list.append((1, item)) # 数字权重高
        else:
            safe_list.append((0, str(item).lower()))
    return safe_list

def smart_sort_key(server_info):
    name = server_info.get('name', '')
    if not name: return []

    # 1. 预处理：汉字转数字
    try: name_normalized = re.sub(r'[零一二三四五六七八九十]+', cn_to_arabic_str, name)
    except: name_normalized = name

    # 2. 尝试旧版特定逻辑拆分
    try:
        if '|' in name_normalized:
            parts = name_normalized.split('|', 1)
            p1 = parts[0].strip(); rest = parts[1].strip()
        else:
            p1 = name_normalized; rest = ""

        p2 = ""
        if ' ' in rest:
            parts = rest.split(' ', 1)
            p2 = parts[0].strip(); rest = parts[1].strip()
        
        sub_parts = rest.split('-')
        p3 = sub_parts[0].strip()
        
        p3_num = 0; p3_text = p3
        p3_match = re.search(r'(\d+)$', p3)
        if p3_match:
            p3_num = int(p3_match.group(1))
            p3_text = p3[:p3_match.start()]

        p4 = ""; p5 = 0
        if len(sub_parts) >= 2: p4 = sub_parts[1].strip()
        if len(sub_parts) >= 3:
            last = sub_parts[-1].strip()
            if last.isdigit(): p5 = int(last)
            else: p4 += f"-{last}"
        elif len(sub_parts) == 2 and sub_parts[1].strip().isdigit():
            p5 = int(sub_parts[1].strip())

        return to_safe_sort_list([p1, p2, p3_text, p3_num, p4, p5])

    except:
        parts = re.split(r'(\d+)', name_normalized)
        mixed_list = [int(text) if text.isdigit() else text for text in parts]
        return to_safe_sort_list(mixed_list)
    

# ================= 表格布局定义 (定义两种模式) =================

# 1. 带延迟 (用于：区域分组、单个服务器) - 包含 90px 的延迟列
# 格式: 服务器(150) 备注(200) 分组(1fr) 流量(100) 协议(80) 端口(80) 延迟(90) 状态(50) 操作(150)
COLS_WITH_PING = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 90px 50px 150px; align-items: center;'

# 2. 无延迟 (用于：所有服务器、自定义分组) - 移除了延迟列
# 格式: 服务器(150) 备注(200) 分组(1fr) 流量(100) 协议(80) 端口(80) 状态(50) 操作(150)
COLS_NO_PING   = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 50px 150px; align-items: center;'

# 单个服务器视图直接复用带延迟的样式
SINGLE_COLS = 'grid-template-columns: 200px 1fr 100px 80px 80px 90px 50px 150px; align-items: center;'

# 格式: 服务器(150) 备注(200) 在线状态(1fr) 流量(100) 协议(80) 端口(80) 操作(150)
COLS_ALL_SERVERS = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 150px; align-items: center;'

# ✨✨✨区域分组专用布局  ✨✨✨
# 格式: 服务器(150) 备注(200) 在线状态(1fr) 流量(100) 协议(80) 端口(80) 操作(150)
COLS_SPECIAL_WITH_PING = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 150px; align-items: center;'

# ✨✨✨ 新增：单服务器专用布局 (移除延迟列 90px，格式与 All Servers 一致) ✨✨✨
# 格式: 备注(200) 所在组(1fr) 流量(100) 协议(80) 端口(80) 状态(100) 操作(150)
SINGLE_COLS_NO_PING = 'grid-template-columns: 200px 1fr 100px 80px 80px 100px 150px; align-items: center;'
# =================  刷新逻辑 (防闪烁 + 静默后台更新 + 令牌防冲突) =================
async def refresh_content(scope='ALL', data=None, force_refresh=False):
    # 1. 安全检查 UI 上下文
    try: client = ui.context.client
    except: return 

    global CURRENT_VIEW_STATE
    
    # ✨✨✨ [新增] 生成本次操作的唯一令牌 (时间戳) ✨✨✨
    import time
    current_token = time.time()
    
    # 更新全局状态（包含令牌）
    if not force_refresh:
        CURRENT_VIEW_STATE['scope'] = scope
        CURRENT_VIEW_STATE['data'] = data
    
    # 无论是否强制刷新，都要更新令牌，标记这是最新的一次操作
    CURRENT_VIEW_STATE['render_token'] = current_token

    # 记录任务开始时的视图状态
    task_start_scope = scope
    task_start_data = data

    with client: 
        if (not content_container or len(list(content_container)) == 0) and not force_refresh:
            content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
            show_loading(content_container)
    
    # --- A. 执行数据同步 ---
    sync_targets = []
    if force_refresh:
        # ... (同步逻辑保持不变，为了节省篇幅省略，逻辑与之前一致) ...
        try:
            if scope == 'ALL': sync_targets = list(SERVERS_CACHE)
            elif scope == 'TAG': sync_targets = [s for s in SERVERS_CACHE if data in s.get('tags', [])]
            elif scope == 'COUNTRY':
                for s in SERVERS_CACHE:
                    saved = s.get('group')
                    real = saved if saved and saved not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区'] else detect_country_group(s.get('name', ''))
                    if real == data: sync_targets.append(s)
            elif scope == 'SINGLE':
                 if data in SERVERS_CACHE: sync_targets = [data]
        except: pass
        
        if sync_targets:
            # ✨ 检查令牌：如果在同步准备期间用户切走了，直接取消同步（节省资源）
            if CURRENT_VIEW_STATE.get('render_token') != current_token: return

            safe_notify(f'正在后台同步 {len(sync_targets)} 个服务器...')
            tasks = [fetch_inbounds_safe(s, force_refresh=True) for s in sync_targets]
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- B. 渲染界面 (核心修改) ---
    async def _render():
        # ✨✨✨ [第一道防线] 任务跑完回来，先检查令牌 ✨✨✨
        # 如果全局令牌变了，说明在这个任务等待期间，用户又点了别的地方
        # 此时必须直接退出，绝对不能去碰 UI
        if CURRENT_VIEW_STATE.get('render_token') != current_token:
            # logger.info("渲染被拦截：令牌过期")
            return

        # 1. 获取用户现在真正所在的页面
        current_real_scope = CURRENT_VIEW_STATE['scope']
        current_real_data = CURRENT_VIEW_STATE['data']

        # 2. 如果是后台任务，检查是否还在原页面 (防跳转逻辑)
        if force_refresh:
            if current_real_scope != task_start_scope or current_real_data != task_start_data:
                return

        # 3. 准备数据
        targets = []
        title = ""
        is_group_view = False
        show_ping = False
        
        try:
            # ... (数据准备逻辑保持不变) ...
            if current_real_scope == 'ALL':
                targets = list(SERVERS_CACHE)
                title = f"🌍 所有服务器 ({len(targets)})"
            elif current_real_scope == 'TAG':
                targets = [s for s in SERVERS_CACHE if current_real_data in s.get('tags', [])]
                title = f"🏷️ 自定义分组: {current_real_data} ({len(targets)})"
                is_group_view = True
            elif current_real_scope == 'COUNTRY':
                targets = []
                for s in SERVERS_CACHE:
                    saved = s.get('group')
                    real_g = saved if saved and saved not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区'] else detect_country_group(s.get('name', ''))
                    if real_g == current_real_data: targets.append(s)
                title = f"🏳️ 区域: {current_real_data} ({len(targets)})"
                is_group_view = True
                show_ping = True 
            elif current_real_scope == 'SINGLE':
                if current_real_data in SERVERS_CACHE:
                    targets = [current_real_data]
                    raw_url = current_real_data['url']; parsed = urlparse(raw_url if '://' in raw_url else f'http://{raw_url}')
                    host_display = parsed.hostname or raw_url
                    title = f"🖥️ {current_real_data['name']} ({host_display})"
                else:
                    current_real_scope = 'ALL'; targets = list(SERVERS_CACHE); title = f"🌍 所有服务器 ({len(targets)})"

            if current_real_scope != 'SINGLE':
                targets.sort(key=smart_sort_key)
                
        except Exception as e:
            logger.error(f"Render Error: {e}")
            targets = []

        # ✨✨✨ [第二道防线] 数据准备好了，准备画图前，再查一次令牌 ✨✨✨
        if CURRENT_VIEW_STATE.get('render_token') != current_token: return

        with client:
            content_container.clear()
            with content_container:
                # 顶部栏
                with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.label(title).classes('text-2xl font-bold')
                        if is_group_view and targets:
                            with ui.row().classes('gap-1'):
                                ui.button(icon='content_copy', on_click=lambda: copy_group_link(current_real_data)).props('flat dense round size=sm color=grey')
                                ui.button(icon='bolt', on_click=lambda: copy_group_link(current_real_data, target='surge')).props('flat dense round size=sm text-color=orange')
                                ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(current_real_data, target='clash')).props('flat dense round size=sm text-color=green')

                    if targets:
                        ui.button('同步最新数据', icon='sync', on_click=lambda: refresh_content(current_real_scope, current_real_data, force_refresh=True)).props('outline color=primary')
                
                # 内容渲染
                if not targets:
                    with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                        ui.icon('inbox', size='4rem'); ui.label('列表为空').classes('text-lg')
                elif current_real_scope == 'SINGLE': 
                    await render_single_server_view(targets[0], force_refresh)
                else: 
                    # ✨ 传递令牌给聚合视图，让它在循环内部也能中断
                    await render_aggregated_view(targets, show_ping=show_ping, force_refresh=False, token=current_token)

    asyncio.create_task(_render())

# ================= 状态面板辅助函数 =================

def format_uptime(seconds):
    """将秒数转换为 天/小时/分钟"""
    if not seconds: return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}天 {h}小时 {m}分"

def render_status_card(label, value_str, sub_text, color_class='text-blue-600', icon='memory'):
    """渲染单个简易状态卡片 (用于负载、连接数等)"""
    with ui.card().classes('p-3 shadow-sm border flex-grow items-center justify-between min-w-[150px]'):
        with ui.row().classes('items-center gap-3'):
            with ui.column().classes('justify-center items-center bg-gray-100 rounded-full p-2'):
                ui.icon(icon).classes(f'{color_class} text-xl')
            with ui.column().classes('gap-0'):
                ui.label(label).classes('text-xs text-gray-400 font-bold')
                ui.label(value_str).classes('text-sm font-bold text-slate-700')
                if sub_text: ui.label(sub_text).classes('text-[10px] text-gray-400')

    
# =================单个服务器视图 =========================
async def render_single_server_view(server_conf, force_refresh=False):
    mgr = get_manager(server_conf)
    
    # UI 引用字典
    ui_refs = {}

    # --- 辅助函数：环形进度条 ---
    def _create_live_ring(label, color, key_prefix):
        with ui.column().classes('items-center justify-center min-w-[100px]'):
            with ui.element('div').classes('relative flex items-center justify-center w-16 h-16 mb-2'):
                ui_refs[f'{key_prefix}_ring'] = ui.circular_progress(0, size='60px', show_value=False, color=color).props('track-color=grey-3 thickness=0.15').classes('absolute transition-all duration-500')
                ui_refs[f'{key_prefix}_pct'] = ui.label('--%').classes('text-xs font-bold text-gray-700 z-10')
            ui.label(label).classes('text-xs font-bold text-gray-600')
            ui_refs[f'{key_prefix}_detail'] = ui.label('-- / --').classes('text-[10px] text-gray-400 font-mono text-center leading-tight')

    # --- 辅助函数：网络卡片 ---
    def _create_live_net_card(title, icon, key_prefix):
        with ui.card().classes('p-3 shadow-sm border border-gray-100 flex-grow min-w-[180px] flex-row items-center gap-3 bg-white'):
            with ui.column().classes('p-2 bg-blue-50 rounded-full'):
                ui.icon(icon).classes('text-blue-600 text-lg')
            with ui.column().classes('gap-0 flex-grow'):
                ui.label(title).classes('text-xs font-bold text-gray-400 mb-1')
                with ui.row().classes('w-full justify-between items-center gap-2'):
                    with ui.row().classes('items-center gap-1'):
                        ui.icon('arrow_upward').classes('text-xs text-orange-400')
                        ui_refs[f'{key_prefix}_up'] = ui.label('--').classes('text-sm font-bold text-slate-700 font-mono')
                    with ui.row().classes('items-center gap-1'):
                        ui.icon('arrow_downward').classes('text-xs text-green-500')
                        ui_refs[f'{key_prefix}_down'] = ui.label('--').classes('text-sm font-bold text-slate-700 font-mono')

    # --- 辅助函数：状态卡片 ---
    def _create_live_stat_card(title, icon, color_cls, key_prefix):
        with ui.card().classes('p-3 shadow-sm border flex-grow items-center justify-between min-w-[150px]'):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes('justify-center items-center bg-gray-100 rounded-full p-2'):
                    ui_refs[f'{key_prefix}_icon'] = ui.icon(icon).classes(f'{color_cls} text-xl')
                with ui.column().classes('gap-0'):
                    ui.label(title).classes('text-xs text-gray-400 font-bold')
                    ui_refs[f'{key_prefix}_main'] = ui.label('--').classes('text-sm font-bold text-slate-700')
                    ui_refs[f'{key_prefix}_sub'] = ui.label('--').classes('text-[10px] text-gray-400')

    # 顶部按钮
    with ui.row().classes('w-full justify-end mb-2'):
        ui.button('新建节点', icon='add', color='green', on_click=lambda: open_inbound_dialog(mgr, None, lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('dense')

    # 布局容器
    list_container = ui.column().classes('w-full mb-6') 
    status_container = ui.column().classes('w-full') 

    # ================= 1. 渲染节点列表 =================
    try:
        res = await fetch_inbounds_safe(server_conf, force_refresh=force_refresh)
        list_container.clear()
        
        raw_host = server_conf['url']
        try:
            if '://' not in raw_host: 
                raw_host = f'http://{raw_host}'
            p = urlparse(raw_host)
            raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
        except: 
            pass

        # 单服务器视图下，我们依然可以发起 Ping 任务来检测连通性，但不需要在 UI 上显示数值
        # 这有助于更新全局的在线状态
        if res:
            asyncio.create_task(batch_ping_nodes(res, raw_host))

        with list_container:
            # ✨✨✨ 修改表头：移除延迟，调整状态列 ✨✨✨
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2').style(SINGLE_COLS_NO_PING):
                ui.label('备注名称').classes('text-left pl-2')
                # 所在组, 已用流量, 协议, 端口, 状态, 操作
                headers = ['所在组', '已用流量', '协议', '端口', '状态', '操作']
                for h in headers: ui.label(h).classes('text-center')
            
            if not res: 
                ui.label('暂无节点或连接失败').classes('text-gray-400 mt-4 text-center w-full')
            else:
                if not force_refresh: 
                    ui.label('本地缓存模式 (点击右上角同步以刷新)').classes('text-xs text-gray-300 w-full text-right px-2')
                
                for n in res:
                    traffic = format_bytes(n.get('up', 0) + n.get('down', 0))
                    
                    with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(SINGLE_COLS_NO_PING):
                        ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                        ui.label(server_conf.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                        ui.label(traffic).classes('text-xs text-gray-600 w-full text-center font-mono')
                        ui.label(n.get('protocol', 'unknown')).classes('uppercase text-xs font-bold w-full text-center')
                        ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                        
                        # ✨✨✨ 修改状态列：闪电 + 文字 ✨✨✨
                        # 这里的逻辑：如果能获取到节点列表(res存在)，说明面板就是连通的(Online)
                        # 如果 n.get('enable') 是 False，说明节点被禁用了
                        
                        is_enable = n.get('enable', True)
                        status_text = "运行中" if is_enable else "已停止"
                        status_color = "green" if is_enable else "red"
                        status_icon = "bolt"
                        
                        with ui.row().classes('w-full justify-center items-center gap-1'):
                            ui.icon(status_icon).classes(f'text-{status_color}-500 text-sm')
                            ui.label(status_text).classes(f'text-xs font-bold text-{status_color}-600')

                        # 操作按钮
                        with ui.row().classes('gap-2 justify-center w-full no-wrap'):
                            l_url = generate_node_link(n, raw_host)
                            if l_url: 
                                ui.button(icon='content_copy', on_click=lambda u=l_url: safe_copy_to_clipboard(u)).props('flat dense size=sm').tooltip('复制链接')
                            d_conf = generate_detail_config(n, raw_host)
                            if d_conf: 
                                ui.button(icon='description', on_click=lambda u=d_conf: safe_copy_to_clipboard(u)).props('flat dense size=sm text-color=orange').tooltip('复制配置')
                            ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm')
                            ui.button(icon='delete', on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'], i.get('remark','未命名'), lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm color=red')
    except Exception as e: 
        logger.error(f"Render List Error: {e}")

    # ================= 2. 渲染状态面板框架  =================
    with status_container:
        ui.separator().classes('my-4') 
        with ui.card().classes('w-full p-4 bg-white rounded-xl shadow-sm border border-gray-100'):
            # 标题栏 + 心跳
            with ui.row().classes('w-full justify-between items-center mb-2'):
                ui.label('服务器实时监控').classes('text-sm font-bold text-gray-500')
                ui_refs['heartbeat'] = ui.spinner('dots', size='1em', color='green').classes('opacity-0 transition-opacity')

            # Row 1: 资源
            with ui.row().classes('w-full justify-around items-start mb-6 border-b pb-4'):
                _create_live_ring('CPU', 'blue', 'cpu')
                _create_live_ring('内存', 'green', 'mem')
                _create_live_ring('硬盘', 'purple', 'disk')

            # Row 2: 流量
            with ui.row().classes('w-full gap-4 mb-6 flex-wrap'):
                _create_live_net_card('实时网速', 'speed', 'speed')
                _create_live_net_card('服务器总流量', 'data_usage', 'total')

            # Row 3: 详情
            with ui.row().classes('w-full gap-4 flex-wrap'):
                _create_live_stat_card('Xray 状态', 'settings_power', 'text-gray-400', 'xray')
                _create_live_stat_card('运行时间', 'schedule', 'text-cyan-600', 'uptime')
                _create_live_stat_card('系统负载', 'analytics', 'text-pink-600', 'load')

    # ================= 3. 数据更新任务 =================
    async def update_data_task():
        try:
            # 心跳显示
            if 'heartbeat' in ui_refs: 
                ui_refs['heartbeat'].classes(remove='opacity-0')
            
            status = await run.io_bound(mgr.get_server_status)
            
            if status:
                # CPU
                cpu_val = status.get('cpu', 0)
                if 'cpu_ring' in ui_refs: ui_refs['cpu_ring'].set_value(cpu_val / 100)
                if 'cpu_pct' in ui_refs: ui_refs['cpu_pct'].set_text(f"{round(cpu_val, 1)}%")
                if 'cpu_detail' in ui_refs: ui_refs['cpu_detail'].set_text(f"{status.get('cpuModel','')[:12]}..")

                # 内存
                mem = status.get('mem', {})
                mem_curr = mem.get('current', 0)
                mem_total = mem.get('total', 1)
                if mem_total > 0:
                    if 'mem_ring' in ui_refs: ui_refs['mem_ring'].set_value(mem_curr / mem_total)
                    if 'mem_pct' in ui_refs: ui_refs['mem_pct'].set_text(f"{round(mem_curr/mem_total*100, 1)}%")
                if 'mem_detail' in ui_refs: ui_refs['mem_detail'].set_text(f"{format_bytes(mem_curr)} / {format_bytes(mem_total)}")

                # 硬盘
                disk = status.get('disk', {})
                disk_curr = disk.get('current', 0)
                disk_total = disk.get('total', 1)
                if disk_total > 0:
                    if 'disk_ring' in ui_refs: ui_refs['disk_ring'].set_value(disk_curr / disk_total)
                    if 'disk_pct' in ui_refs: ui_refs['disk_pct'].set_text(f"{round(disk_curr/disk_total*100, 1)}%")
                if 'disk_detail' in ui_refs: ui_refs['disk_detail'].set_text(f"{format_bytes(disk_curr)} / {format_bytes(disk_total)}")

                # 网速
                net = status.get('netIO', {})
                if 'speed_up' in ui_refs: ui_refs['speed_up'].set_text(f"{format_bytes(net.get('up',0))}/s")
                if 'speed_down' in ui_refs: ui_refs['speed_down'].set_text(f"{format_bytes(net.get('down',0))}/s")

                # 总流量
                traf = status.get('netTraffic', {})
                if 'total_up' in ui_refs: ui_refs['total_up'].set_text(format_bytes(traf.get('sent',0)))
                if 'total_down' in ui_refs: ui_refs['total_down'].set_text(format_bytes(traf.get('recv',0)))

                # Xray
                xray = status.get('xray', {})
                state = str(xray.get('state', 'Unknown')).upper()
                if 'xray_main' in ui_refs: ui_refs['xray_main'].set_text(state)
                if 'xray_sub' in ui_refs: ui_refs['xray_sub'].set_text(f"Ver: {xray.get('version','')}")
                if 'xray_icon' in ui_refs:
                    if state == 'RUNNING': 
                        ui_refs['xray_icon'].classes(replace='text-green-600', remove='text-red-500 text-gray-400')
                    else: 
                        ui_refs['xray_icon'].classes(replace='text-red-500', remove='text-green-600 text-gray-400')

                # Uptime & Load
                if 'uptime_main' in ui_refs: ui_refs['uptime_main'].set_text(format_uptime(status.get('uptime', 0)))
                if 'uptime_sub' in ui_refs: ui_refs['uptime_sub'].set_text('System Uptime')
                
                loads = status.get('loads', [0,0,0])
                if not loads: loads = [0,0,0]
                if 'load_main' in ui_refs: ui_refs['load_main'].set_text(f"{loads[0]} | {loads[1]}")
                if 'load_sub' in ui_refs: ui_refs['load_sub'].set_text('1min | 5min')

            # 心跳隐藏
            if 'heartbeat' in ui_refs: 
                ui_refs['heartbeat'].classes(add='opacity-0')

        except Exception as e:
            pass

    # 4. 启动定时器 (每3秒一次)
    ui.timer(3.0, update_data_task)
    # 5. 立即执行一次
    ui.timer(0.1, update_data_task, once=True)
    
# ================= 聚合视图 (局部静默刷新 + 自动状态更新) =================
# 全局字典，用于存储每行 UI 元素的引用，以便局部更新
# 结构: { 'server_url': { 'row_el': row_element, 'status_icon': icon, 'status_label': label, ... } }
UI_ROW_REFS = {} 
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None}

# =================  聚合视图 =================
async def render_aggregated_view(server_list, show_ping=False, force_refresh=False, token=None):
    list_container = ui.column().classes('w-full gap-4')
    
    results = []
    if force_refresh:
        # 如果是强制刷新，去后台获取数据
        tasks = [fetch_inbounds_safe(s, force_refresh=True) for s in server_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        # 否则直接读缓存
        for s in server_list:
            results.append(NODES_DATA.get(s['url'], []))

    list_container.clear()
    
    # 模式判断
    is_all_servers = (server_list == SERVERS_CACHE) or (len(server_list) == len(SERVERS_CACHE) and not show_ping)
    use_special_mode = is_all_servers or show_ping
    current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING

    # --- 内部定义强力重连函数 ---
    async def force_retry_ping(btn, icon, host, port, key):
        if not btn: return 
        btn.props('loading') 
        icon.classes(remove='text-red-500 text-green-500', add='text-gray-300') 
        
        async def _try_connect(timeout_sec):
            try:
                start = asyncio.get_running_loop().time()
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_sec)
                writer.close()
                await writer.wait_closed()
                return int((asyncio.get_running_loop().time() - start) * 1000)
            except: return None
            
        final_latency = None
        for i in range(3):
            final_latency = await _try_connect(3.0)
            if final_latency is not None: break
            if i < 2: await asyncio.sleep(0.5)
            
        if final_latency is not None:
            PING_CACHE[key] = final_latency
            icon.classes(remove='text-gray-300 text-red-500', add='text-green-500')
            btn.set_visibility(False) 
            safe_notify(f'✅ 重连成功: {final_latency}ms', 'positive')
        else:
            PING_CACHE[key] = -1
            icon.classes(remove='text-gray-300 text-green-500', add='text-red-500')
            safe_notify('❌ 依然无法连接 (3次尝试均失败)', 'negative')
        btn.props(remove='loading')

    with list_container:
        # --- 绘制表头 ---
        with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2 bg-gray-50').style(current_css):
            ui.label('服务器').classes('text-left pl-2')
            ui.label('备注名称').classes('text-left pl-2')
            if use_special_mode: ui.label('在线状态').classes('text-center')
            else: ui.label('所在组').classes('text-center')
            ui.label('已用流量').classes('text-center')
            ui.label('协议').classes('text-center')
            ui.label('端口').classes('text-center')
            if not use_special_mode: ui.label('状态').classes('text-center')
            ui.label('操作').classes('text-center')
        
        # --- 绘制数据行 (分批渲染) ---
        for i, res in enumerate(results):
            
            # ✨✨✨ [核心防线 1] 检查令牌是否过期 ✨✨✨
            # 如果 token 传进来了，且跟全局最新 token 不一致，说明用户切走了，立即停止渲染
            if token and CURRENT_VIEW_STATE.get('render_token') != token:
                return 

            # ✨✨✨ [核心防线 2] 喘口气，防止主线程卡死 ✨✨✨
            # 每渲染 10 行，强制休息 0 秒，让出 CPU 给心跳包
            if i > 0 and i % 10 == 0:
                await asyncio.sleep(0) 
            
            srv = server_list[i]
            if isinstance(res, Exception): res = []
            if res is None: res = []
            mgr = get_manager(srv)
            
            raw_host = srv['url']
            try:
                if '://' not in raw_host: raw_host = f'http://{raw_host}'
                p = urlparse(raw_host); raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
            except: pass

            # 如果需要显示 Ping，则发起后台 Ping 任务
            if show_ping and res:
                 asyncio.create_task(batch_ping_nodes(res, raw_host))

            row_wrapper = ui.element('div').classes('w-full')
            
            with row_wrapper:
                # --- 情况 A: 无数据 (显示连接失败或暂无数据) ---
                if not res:
                    with ui.element('div').classes('grid w-full gap-4 py-3 border-b bg-gray-50 px-2 items-center').style(current_css):
                        ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                        msg = '❌ 连接失败' if force_refresh else '⏳ 暂无数据'
                        color = 'text-red-500' if force_refresh else 'text-gray-400'
                        ui.label(msg).classes(f'{color} font-bold w-full text-left pl-2')
                        
                        if use_special_mode:
                            try: ip_display = get_real_ip_display(srv['url'])
                            except: ip_display = raw_host
                            with ui.row().classes('w-full justify-center items-center gap-1'):
                                ui.icon('bolt').classes('text-red-500 text-sm')
                                # 绑定 IP 静默更新
                                ip_label = ui.label(ip_display).classes('text-xs font-mono text-gray-500')
                                bind_ip_label(srv['url'], ip_label)
                        else:
                            ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                        
                        for _ in range(3): ui.label('-').classes('w-full text-center')
                        if not use_special_mode:
                            with ui.element('div').classes('flex justify-center w-full'): ui.icon('help_outline', color='grey').props('size=xs')
                        
                        with ui.row().classes('gap-2 justify-center w-full'): 
                            # 注意：这里调用 refresh_content 最好也带上 force_refresh=True
                            ui.button(icon='sync', on_click=lambda s=srv: refresh_content('SINGLE', s, force_refresh=True)).props('flat dense size=sm color=primary').tooltip('单独同步')
                    continue

                # --- 情况 B: 有数据 (正常渲染节点) ---
                for n in res:
                    try:
                        traffic = format_bytes(n.get('up', 0) + n.get('down', 0))
                        target_host = n.get('listen') or raw_host
                        target_port = n.get('port')
                        ping_key = f"{target_host}:{target_port}"
                        
                        with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(current_css):
                            # Col 1 & 2
                            ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                            ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                            
                            # Col 3: 状态/分组
                            if use_special_mode:
                                try: ip_display = get_real_ip_display(srv['url'])
                                except: ip_display = raw_host
                                
                                with ui.row().classes('w-full justify-center items-center gap-1'):
                                    status_icon = ui.icon('bolt').classes('text-gray-300 text-sm')
                                    # 绑定 IP 显示
                                    ip_label = ui.label(ip_display).classes('text-xs font-mono text-gray-500')
                                    bind_ip_label(srv['url'], ip_label) 
                                    
                                    # 强力重连按钮
                                    retry_btn = ui.button(icon='refresh').props('flat dense round size=xs text-color=red')
                                    retry_btn.tooltip('尝试强力重连 (3次x3秒)')
                                    retry_btn.set_visibility(False)
                                    retry_btn.on_click(lambda e, b=retry_btn, i=status_icon, h=target_host, p=target_port, k=ping_key: force_retry_ping(b, i, h, p, k))

                                # 自动更新 Ping 状态逻辑
                                if show_ping:
                                    def check_ping_result(icon_ref=status_icon, key_ref=ping_key, btn_ref=retry_btn):
                                        val = PING_CACHE.get(key_ref, None)
                                        if val is not None:
                                            if val == -1: 
                                                icon_ref.classes(remove='text-gray-300 text-green-500', add='text-red-500')
                                                btn_ref.set_visibility(True)
                                            else: 
                                                icon_ref.classes(remove='text-gray-300 text-red-500', add='text-green-500')
                                                btn_ref.set_visibility(False)
                                            return False # 停止定时器
                                        return True # 继续等待
                                    ui.timer(1.0, lambda i=status_icon, k=ping_key, b=retry_btn: check_ping_result(i, k, b))
                                else:
                                    # 如果不显示 Ping，就读服务器级状态
                                    status_code = srv.get('_status', 'online')
                                    if status_code == 'online': status_icon.classes(replace='text-green-500')
                                    elif status_code == 'offline': status_icon.classes(replace='text-red-500')
                                    else: status_icon.classes(replace='text-gray-400')

                            else:
                                ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')

                            # Col 4, 5, 6
                            ui.label(traffic).classes('text-xs text-gray-600 w-full text-center font-mono')
                            ui.label(n.get('protocol', 'unk')).classes('uppercase text-xs font-bold w-full text-center')
                            ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')

                            # Col Status Dot (Circle)
                            if not use_special_mode:
                                with ui.element('div').classes('flex justify-center w-full'): 
                                    ui.icon('circle', color='green' if n.get('enable') else 'red').props('size=xs')
                            
                            # Col Actions
                            with ui.row().classes('gap-2 justify-center w-full no-wrap'):
                                link = generate_node_link(n, raw_host)
                                if link: ui.button(icon='content_copy', on_click=lambda l=link: safe_copy_to_clipboard(l)).props('flat dense size=sm').tooltip('复制链接')
                                detail_conf = generate_detail_config(n, raw_host)
                                if detail_conf: ui.button(icon='description', on_click=lambda l=detail_conf: safe_copy_to_clipboard(l)).props('flat dense size=sm text-color=orange').tooltip('复制配置')
                                ui.button(icon='edit', on_click=lambda m=mgr, i=n, s=srv: open_inbound_dialog(m, i, lambda: refresh_content('SINGLE', s, force_refresh=True))).props('flat dense size=sm')
                                ui.button(icon='delete', on_click=lambda m=mgr, i=n, s=srv: delete_inbound_with_confirm(m, i['id'], i.get('remark','未命名'), lambda: refresh_content('SINGLE', s, force_refresh=True))).props('flat dense size=sm color=red')
                    except: continue


# ================= 核心：静默刷新 UI 数据 =================
async def refresh_dashboard_ui():
    """
    不管是谁调用我，我都会把最新的 SERVERS_CACHE 和 NODES_DATA 
    推送到仪表盘的组件上，不会刷新页面。
    """
    try:
        # 如果仪表盘还没打开（引用是空的），直接跳过
        if not DASHBOARD_REFS['servers']: return

        total_servers = len(SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        total_up_bytes = 0
        total_down_bytes = 0
        
        server_traffic_map = {}
        protocol_count = {}
        map_markers = []

        # --- 1. 计算数据 (纯内存计算，极快) ---
        for s in SERVERS_CACHE:
            res = NODES_DATA.get(s['url'], [])
            name = s.get('name', '未命名')
            
            # 收集地图数据
            # 优先使用已保存的精准 IP 坐标
            if 'lat' in s and 'lon' in s:
                 map_markers.append((s['lat'], s['lon'], name))
            else:
                 # ✨✨✨ [修复] 恢复兜底逻辑：从名字猜测坐标 (例如 "🇯🇵 日本") ✨✨✨
                 coords = get_coords_from_name(name) 
                 if coords: map_markers.append((coords[0], coords[1], name))

            if res:
                online_servers += 1
                total_nodes += len(res)
                srv_traffic = 0
                for n in res: 
                    u = int(n.get('up', 0)); d = int(n.get('down', 0)); t = u + d
                    total_up_bytes += u; total_down_bytes += d; total_traffic_bytes += t; srv_traffic += t
                    proto = str(n.get('protocol', 'unknown')).upper()
                    protocol_count[proto] = protocol_count.get(proto, 0) + 1
                server_traffic_map[name] = srv_traffic
            else:
                server_traffic_map[name] = 0

        # --- 2. 更新 UI (静默更新) ---
        # 只有当 UI 元素存在时才更新
        if DASHBOARD_REFS['servers']: DASHBOARD_REFS['servers'].set_text(f"{online_servers}/{total_servers}")
        if DASHBOARD_REFS['nodes']: DASHBOARD_REFS['nodes'].set_text(str(total_nodes))
        if DASHBOARD_REFS['traffic']: DASHBOARD_REFS['traffic'].set_text(f"{total_traffic_bytes/(1024**3):.2f} GB")
        if DASHBOARD_REFS['subs']: DASHBOARD_REFS['subs'].set_text(str(len(SUBS_CACHE)))

        if DASHBOARD_REFS['bar_chart']:
            sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15] 
            names = [x[0] for x in sorted_traffic]; values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]
            DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = names
            DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = values
            DASHBOARD_REFS['bar_chart'].update()

        if DASHBOARD_REFS['pie_chart']:
            pie_data = [{'name': k, 'value': v} for k, v in protocol_count.items()]
            DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = pie_data
            DASHBOARD_REFS['pie_chart'].update()
            
            if DASHBOARD_REFS['stat_up']: DASHBOARD_REFS['stat_up'].set_text(format_bytes(total_up_bytes))
            if DASHBOARD_REFS['stat_down']: DASHBOARD_REFS['stat_down'].set_text(format_bytes(total_down_bytes))
            avg_traffic = total_traffic_bytes / total_nodes if total_nodes > 0 else 0
            if DASHBOARD_REFS['stat_avg']: DASHBOARD_REFS['stat_avg'].set_text(format_bytes(avg_traffic))

        # 更新地图
        m = DASHBOARD_REFS['map']
        if m and map_markers:
            if DASHBOARD_REFS['map_info']: DASHBOARD_REFS['map_info'].set_text(f'已定位 {len(map_markers)} 个节点')
            
            # 简单绘制逻辑：如果地图上还没有标记，或者你想强制刷新，可以在这里处理
            # 鉴于 Leaflet 的特性，我们只在首次加载时绘制，后续 update 如果坐标变了比较难处理
            # 这里保持只绘制一次的逻辑，依靠 load_dashboard_stats 初始化
            if not getattr(m, 'has_drawn_markers', False):
                for lat, lng, name in map_markers:
                    # 随机微调防止重叠
                    lat += (random.random() - 0.5) * 0.1 
                    lng += (random.random() - 0.5) * 0.1
                    m.marker(latlng=(lat, lng))
                m.has_drawn_markers = True

    except Exception as e:
        logger.error(f"UI 更新失败: {e}")


# ========================后台刷新策略======================================

async def load_dashboard_stats():
    # ✨✨✨ [新增] 标记当前在仪表盘 ✨✨✨
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None
    # 1. 缓冲
    await asyncio.sleep(0.1)
    content_container.clear()
    
    # 强制重置容器样式
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
    
    # 注意：之前的 LOCATION_COORDS 和 get_coords_from_name 已经移到全局了，这里不需要了

    # 6. 进入容器上下文
    with content_container:
        ui.label('系统概览').classes('text-3xl font-bold mb-6 text-slate-800 tracking-tight')
        
        # === A. 顶部卡片 ===
        with ui.row().classes('w-full gap-6 mb-8 items-stretch'):
            def create_stat_card(key, title, sub_text, icon, gradient):
                with ui.card().classes(f'flex-1 p-6 shadow-lg border-none text-white {gradient} rounded-xl transform hover:scale-105 transition duration-300 relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-6 -top-6 w-24 h-24 bg-white opacity-10 rounded-full')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-1'):
                            ui.label(title).classes('opacity-80 text-xs font-bold uppercase tracking-wider')
                            # ✨✨✨ 重点：把 UI 组件存入全局引用，而不是本地变量
                            DASHBOARD_REFS[key] = ui.label('Wait...').classes('text-3xl font-extrabold tracking-tight')
                            ui.label(sub_text).classes('opacity-70 text-xs font-medium')
                        ui.icon(icon).classes('text-4xl opacity-80')

            create_stat_card('servers', '在线服务器', 'Online / Total', 'dns', 'bg-gradient-to-br from-blue-500 to-indigo-600')
            create_stat_card('nodes', '节点总数', 'Active Nodes', 'hub', 'bg-gradient-to-br from-purple-500 to-pink-600')
            create_stat_card('traffic', '总流量消耗', 'Upload + Download', 'bolt', 'bg-gradient-to-br from-emerald-500 to-teal-600')
            create_stat_card('subs', '订阅配置', 'Subscriptions', 'rss_feed', 'bg-gradient-to-br from-orange-400 to-red-500')

        # === B. 图表区域 ===
        with ui.row().classes('w-full gap-6 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            with ui.card().classes('w-full xl:w-2/3 p-6 shadow-md border-none rounded-xl bg-white flex flex-col'):
                with ui.row().classes('w-full justify-between items-center mb-2'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-lg font-bold text-slate-700')
                    # 这里也可以存个 Ref，不过非必须
                    ui.badge('Live', color='indigo').props('outline') 
                
                # ✨✨✨ 重点：存入全局引用
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': [], 'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#64748b'}},
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#f1f5f9'}}},
                    'series': [{'type': 'bar', 'data': [], 'barWidth': '40%', 'itemStyle': {'borderRadius': [4, 4, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-80')

            with ui.card().classes('w-full xl:w-1/3 p-6 shadow-md border-none rounded-xl bg-white flex flex-col'):
                ui.label('🍩 协议分布').classes('text-lg font-bold text-slate-700 mb-2')
                
                # ✨✨✨ 重点：存入全局引用
                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'color': ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'],
                    'tooltip': {'trigger': 'item'}, 
                    'legend': {'bottom': '0%', 'icon': 'circle'},
                    'series': [{'name': '协议', 'type': 'pie', 'radius': ['50%', '70%'], 'center': ['50%', '45%'], 
                                'itemStyle': {'borderRadius': 5, 'borderColor': '#fff', 'borderWidth': 2},
                                'label': {'show': False}, 'emphasis': {'label': {'show': True, 'fontSize': '20', 'fontWeight': 'bold'}}, 'data': []}]
                }).classes('w-full h-56')
                
                ui.separator().classes('my-4')
                
                with ui.row().classes('w-full gap-2 items-stretch'):
                    with ui.column().classes('items-center flex-1 p-2 bg-blue-50 rounded-lg h-full justify-center'):
                        with ui.row().classes('text-xs text-blue-400 font-bold mb-1').style('gap: 2px'):
                            ui.icon('arrow_upward', size='xs')
                            ui.label('上传')
                        # ✨✨✨ 重点：存入全局引用
                        DASHBOARD_REFS['stat_up'] = ui.label('--').classes('text-sm font-extrabold text-blue-700')
                    
                    with ui.column().classes('items-center flex-1 p-2 bg-green-50 rounded-lg h-full justify-center'):
                        with ui.row().classes('text-xs text-green-500 font-bold mb-1').style('gap: 2px'):
                            ui.icon('arrow_downward', size='xs')
                            ui.label('下载')
                        # ✨✨✨ 重点：存入全局引用
                        DASHBOARD_REFS['stat_down'] = ui.label('--').classes('text-sm font-extrabold text-green-700')
                    
                    with ui.column().classes('items-center flex-1 p-2 bg-purple-50 rounded-lg h-full justify-center'):
                        with ui.row().classes('text-xs text-purple-500 font-bold mb-1').style('gap: 2px'):
                            ui.icon('data_usage', size='xs')
                            ui.label('节点均量')
                        # ✨✨✨ 重点：存入全局引用
                        DASHBOARD_REFS['stat_avg'] = ui.label('--').classes('text-sm font-extrabold text-purple-700')

        # === C. 底部地图 (Leaflet) ===
        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-white overflow-hidden'):
                with ui.row().classes('w-full px-6 py-4 bg-slate-50 border-b border-gray-100 justify-between items-center'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public', color='blue').classes('text-xl')
                        ui.label('全球节点实景分布 (Leaflet)').classes('text-lg font-bold text-slate-700')
                    # ✨✨✨ 重点：存入全局引用
                    DASHBOARD_REFS['map_info'] = ui.label('等待数据更新...').classes('text-xs text-gray-400')

                # 初始化地图 (高度 700px, 中心点 30,20)
                # ✨✨✨ 重点：存入全局引用
                DASHBOARD_REFS['map'] = ui.leaflet(center=(30, 20), zoom=2).classes('w-full h-[700px]')

        # === D. 立即填充一次数据 ===
        # 这里不再有 while True 循环，也不再有 ui.timer
        # 只是在页面打开的瞬间，调用一次 Step 2 写的刷新函数
        await refresh_dashboard_ui()
        
# ================= 全能批量编辑器 =================
class BulkEditor:
    def __init__(self, target_servers, title="批量管理"):
        self.all_servers = target_servers
        self.title = title
        self.selected_urls = set()
        self.ui_rows = {} 
        self.dialog = None

    def open(self):
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[85vh] flex flex-col p-0 overflow-hidden'):
            self.dialog = d
            
            # --- 1. 顶部标题 ---
            with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b flex-shrink-0'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('edit_note', color='primary').classes('text-xl')
                    ui.label(self.title).classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            # --- 2. 工具栏 ---
            with ui.column().classes('w-full p-4 gap-3 border-b bg-white flex-shrink-0'):
                self.search_input = ui.input(placeholder='🔍 搜索服务器名称...').props('outlined dense clearable').classes('w-full')
                self.search_input.on_value_change(self.on_search)
                
                with ui.row().classes('w-full justify-between items-center'):
                    with ui.row().classes('gap-2'):
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm color=primary')
                        ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm color=grey')
                        self.count_label = ui.label('已选: 0').classes('text-xs font-bold text-gray-500 self-center ml-2')
            
            # --- 3. 列表区域 ---
            with ui.scroll_area().classes('w-full flex-grow p-2 bg-gray-50'):
                with ui.column().classes('w-full gap-1') as self.list_container:
                    if not self.all_servers:
                        ui.label('当前组无服务器').classes('w-full text-center text-gray-400 mt-10')
                    
                    # 尝试排序
                    try: sorted_srv = sorted(self.all_servers, key=lambda x: smart_sort_key(x))
                    except: sorted_srv = self.all_servers

                    for s in sorted_srv:
                        with ui.row().classes('w-full items-center p-2 bg-white rounded border border-gray-200 hover:border-blue-400 transition') as row:
                            chk = ui.checkbox(value=False).props('dense').classes('mr-2')
                            chk.on_value_change(lambda e, u=s['url']: self.on_check(u, e.value))
                            
                            with ui.column().classes('gap-0 flex-grow overflow-hidden'):
                                # 国旗防重复判断
                                display_name = s['name']
                                try:
                                    country = detect_country_group(s['name'])
                                    flag = country.split(' ')[0]
                                    if flag not in s['name']:
                                        display_name = f"{flag} {s['name']}"
                                except: pass

                                ui.label(display_name).classes('text-sm font-bold text-gray-800 truncate')
                                ui.label(s['url']).classes('text-xs text-gray-400 font-mono truncate hidden') # 隐藏原始URL，搜索用
                            
                            # 1. 解析 IP
                            ip_addr = get_real_ip_display(s['url'])

                            # 2. 状态图标
                            status = s.get('_status')
                            if status == 'online':
                                stat_color = 'green-500'; stat_icon = 'bolt'
                            elif status == 'offline':
                                stat_color = 'red-500'; stat_icon = 'bolt'
                            else:
                                stat_color = 'grey-400'; stat_icon = 'help_outline'

                            with ui.row().classes('items-center gap-1'):
                                ui.icon(stat_icon).classes(f'text-{stat_color} text-sm')
                                # ✨ IP 静默更新
                                ip_lbl = ui.label(ip_addr).classes('text-xs font-mono text-gray-500')
                                bind_ip_label(s['url'], ip_lbl)

                        self.ui_rows[s['url']] = {
                            'el': row, 
                            'search_text': f"{s['name']} {s['url']} {ip_addr}".lower(),
                            'checkbox': chk
                        }

            # --- 4. 底部操作栏 ---
            with ui.row().classes('w-full p-4 border-t bg-white justify-between items-center flex-shrink-0'):
                with ui.row().classes('gap-2'):
                    ui.label('批量操作:').classes('text-sm font-bold text-gray-600 self-center')
                    
                    # === 移动分组 ===
                    async def move_group():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card().classes('w-80'):
                            ui.label('移动到分组').classes('font-bold mb-2')
                            groups = sorted(list(get_all_groups_set()))
                            
                            # ✨✨✨ 关键修改：new_value_mode='add-unique' 允许用户手打新分组 ✨✨✨
                            sel = ui.select(groups, label='选择或输入分组', with_input=True, new_value_mode='add-unique').classes('w-full')
                            
                            ui.button('确定移动', on_click=lambda: do_move(sel.value)).classes('w-full mt-4 bg-blue-600 text-white')
                            
                            async def do_move(target_group):
                                if not target_group: return
                                count = 0
                                for s in SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        s['group'] = target_group
                                        count += 1
                                
                                # 同时也更新一下自定义分组列表
                                if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
                                if target_group not in ADMIN_CONFIG['custom_groups']:
                                    ADMIN_CONFIG['custom_groups'].append(target_group)
                                    await save_admin_config()

                                await save_servers()
                                sub_d.close(); self.dialog.close() # 关闭所有弹窗
                                render_sidebar_content.refresh()
                                await refresh_content('ALL')
                                safe_notify(f'已移动 {count} 个服务器到 [{target_group}]', 'positive')
                        sub_d.open()

                    ui.button('移动分组', icon='folder_open', on_click=move_group).props('flat dense color=blue')

                    # =========================================================
                    # ✨✨✨ [新增] 批量修改 SSH 设置 (用户名/认证方式) ✨✨✨
                    # =========================================================
                    async def batch_ssh_config():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')

                        with ui.dialog() as d_ssh, ui.card().classes('w-96 p-5 flex flex-col gap-3'):
                            with ui.row().classes('items-center gap-2 mb-1'):
                                ui.icon('vpn_key', color='teal').classes('text-xl')
                                ui.label('批量 SSH 配置').classes('text-lg font-bold')
                            
                            ui.label(f'正在修改 {len(self.selected_urls)} 个服务器的连接信息').classes('text-xs text-gray-400')
                            
                            # 1. 用户名设置
                            ui.label('SSH 用户名').classes('text-xs font-bold text-gray-500 mt-2')
                            user_input = ui.input(placeholder='留空则保持原样 (不修改)').props('outlined dense').classes('w-full')
                            
                            # 2. 认证方式选择
                            ui.label('认证方式').classes('text-xs font-bold text-gray-500 mt-2')
                            # 对应 open_server_dialog 中的选项
                            auth_opts = ['不修改', '全局密钥', '独立密码', '独立密钥']
                            auth_sel = ui.select(auth_opts, value='不修改').props('outlined dense options-dense').classes('w-full')
                            
                            # 3. 凭证输入 (根据选择显隐)
                            # 密码输入框
                            pwd_input = ui.input('输入新密码', password=True).props('outlined dense').classes('w-full')
                            pwd_input.bind_visibility_from(auth_sel, 'value', value='独立密码')
                            
                            # 私钥输入框
                            key_input = ui.textarea('输入新私钥', placeholder='-----BEGIN OPENSSH PRIVATE KEY-----') \
                                .props('outlined dense rows=4 input-class=text-xs font-mono').classes('w-full')
                            key_input.bind_visibility_from(auth_sel, 'value', value='独立密钥')
                            
                            # 全局密钥提示
                            global_hint = ui.label('✅ 将统一使用全局 SSH 密钥连接').classes('text-xs text-green-600 bg-green-50 p-2 rounded w-full text-center')
                            global_hint.bind_visibility_from(auth_sel, 'value', value='全局密钥')

                            async def save_ssh_changes():
                                count = 0
                                target_user = user_input.value.strip()
                                target_auth = auth_sel.value
                                
                                # 遍历并修改
                                for s in SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        changed = False
                                        
                                        # 修改用户名 (仅当输入不为空时)
                                        if target_user:
                                            s['ssh_user'] = target_user
                                            changed = True
                                        
                                        # 修改认证方式
                                        if target_auth != '不修改':
                                            s['ssh_auth_type'] = target_auth
                                            changed = True
                                            
                                            # 如果选了独立密码/密钥，更新对应的字段
                                            if target_auth == '独立密码':
                                                s['ssh_password'] = pwd_input.value
                                            elif target_auth == '独立密钥':
                                                s['ssh_key'] = key_input.value
                                        
                                        if changed: count += 1

                                if count > 0:
                                    await save_servers()
                                    d_ssh.close()
                                    safe_notify(f'✅ 已更新 {count} 个服务器的 SSH 配置', 'positive')
                                else:
                                    d_ssh.close()
                                    safe_notify('未做任何修改', 'warning')

                            with ui.row().classes('w-full justify-end mt-4 gap-2'):
                                ui.button('取消', on_click=d_ssh.close).props('flat color=grey')
                                ui.button('保存配置', icon='save', on_click=save_ssh_changes).classes('bg-teal-600 text-white shadow-md')

                        d_ssh.open()

                    ui.button('SSH 设置', icon='vpn_key', on_click=batch_ssh_config).props('flat dense color=teal')

                    # === 删除服务器 ===
                    async def delete_servers():
                        if not self.selected_urls: return safe_notify('未选择服务器', 'warning')
                        with ui.dialog() as sub_d, ui.card():
                            ui.label(f'确定删除 {len(self.selected_urls)} 个服务器?').classes('font-bold text-red-600')
                            with ui.row().classes('w-full justify-end mt-4'):
                                ui.button('取消', on_click=sub_d.close).props('flat')
                                async def confirm_del():
                                    global SERVERS_CACHE
                                    SERVERS_CACHE = [s for s in SERVERS_CACHE if s['url'] not in self.selected_urls]
                                    await save_servers()
                                    sub_d.close(); d.close()
                                    render_sidebar_content.refresh()
                                    if content_container: content_container.clear()
                                    safe_notify('删除成功', 'positive')
                                ui.button('确定删除', color='red', on_click=confirm_del)
                        sub_d.open()

                    ui.button('删除', icon='delete', on_click=delete_servers).props('flat dense color=red')

                ui.button('关闭', on_click=d.close).props('outline color=grey')

        d.open()

    def on_search(self, e):
        keyword = str(e.value).lower().strip()
        for url, item in self.ui_rows.items():
            visible = keyword in item['search_text']
            item['el'].set_visibility(visible)

    def on_check(self, url, value):
        if value: self.selected_urls.add(url)
        else: self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')

    def toggle_all(self, state):
        visible_urls = [u for u, item in self.ui_rows.items() if item['el'].visible]
        for url in visible_urls:
            self.ui_rows[url]['checkbox'].value = state
        if not state:
            for url in visible_urls: self.selected_urls.discard(url)
        self.count_label.set_text(f'已选: {len(self.selected_urls)}')

def open_bulk_edit_dialog(servers, title="管理"):
    editor = BulkEditor(servers, title)
    editor.open()


# ================= 批量 SSH 执行逻辑  =================
class BatchSSH:
    def __init__(self):
        self.selected_urls = set()
        self.log_element = None
        self.is_running = False
        self.dialog = None

    def open_dialog(self):
        self.selected_urls = set()
        with ui.dialog() as d, ui.card().classes('w-full max-w-4xl h-[80vh] flex flex-col p-0 overflow-hidden'):
            self.dialog = d
            
            # --- 标题栏 ---
            with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('terminal', color='primary').classes('text-xl')
                    ui.label('批量 SSH 执行').classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

            # --- 内容容器 (用于切换视图) ---
            self.content_box = ui.column().classes('w-full flex-grow overflow-hidden p-0')
            
            # 初始渲染：选择服务器视图
            self.render_selection_view()
        d.open()

    def render_selection_view(self):
        self.content_box.clear()
        with self.content_box:
            # 工具栏
            with ui.row().classes('w-full p-2 border-b gap-2 bg-white items-center'):
                ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense color=primary')
                ui.button('全不选', on_click=lambda: self.toggle_all(False)).props('flat dense color=grey')
                self.count_label = ui.label('已选: 0').classes('ml-auto text-sm font-bold text-gray-600 mr-4')

            # 服务器列表
            with ui.scroll_area().classes('w-full flex-grow p-4'):
                with ui.column().classes('w-full gap-1'):
                    # 按分组显示，看起来更清晰
                    groups = {}
                    for s in SERVERS_CACHE:
                        g = s.get('group', '默认分组')
                        if g not in groups: groups[g] = []
                        groups[g].append(s)

                    self.checks = {}
                    for g_name, servers in groups.items():
                        ui.label(g_name).classes('text-xs font-bold text-gray-400 mt-2')
                        for s in servers:
                            with ui.row().classes('w-full items-center p-2 hover:bg-blue-50 rounded border border-transparent hover:border-blue-200 transition'):
                                chk = ui.checkbox(value=False, on_change=self.update_count).props('dense')
                                self.checks[s['url']] = chk
                                with ui.column().classes('gap-0 ml-2'):
                                    ui.label(s['name']).classes('text-sm font-bold')
                                    ui.label(s['url']).classes('text-xs text-gray-400 font-mono')

            # 底部按钮
            with ui.row().classes('w-full p-4 border-t bg-gray-50 justify-end'):
                ui.button('下一步: 输入命令', on_click=self.go_to_execution, icon='arrow_forward').classes('bg-slate-900 text-white')

    def toggle_all(self, state):
        for chk in self.checks.values():
            chk.value = state
        self.update_count()

    def update_count(self):
        count = sum(1 for c in self.checks.values() if c.value)
        self.count_label.set_text(f'已选: {count}')

    def go_to_execution(self):
        # 收集选中的服务器
        self.selected_urls = {url for url, chk in self.checks.items() if chk.value}
        if not self.selected_urls:
            safe_notify('请至少选择一个服务器', 'warning')
            return

        # 切换到执行视图
        self.render_execution_view()

    def render_execution_view(self):
        self.content_box.clear()
        with self.content_box:
            # 上半部分：命令输入
            with ui.column().classes('w-full p-4 border-b bg-white gap-2 flex-shrink-0'):
                ui.label(f'向 {len(self.selected_urls)} 台服务器发送命令:').classes('text-sm font-bold text-gray-600')
                self.cmd_input = ui.textarea(placeholder='例如: apt update -y && apt upgrade -y').classes('w-full font-mono text-sm').props('outlined rows=3')
                
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('提示: 命令将在后台并发执行，窗口关闭不影响运行。').classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2'):
                        ui.button('上一步', on_click=self.render_selection_view).props('flat dense')
                        self.run_btn = ui.button('立即执行', on_click=self.run_batch, icon='play_arrow').classes('bg-green-600 text-white')

            # 下半部分：日志输出
            self.log_container = ui.log().classes('w-full flex-grow font-mono text-xs bg-black text-white p-4 overflow-y-auto')

    async def run_batch(self):
        cmd = self.cmd_input.value.strip()
        if not cmd:
            safe_notify('请输入命令', 'warning')
            return
        
        self.run_btn.disable()
        self.cmd_input.disable()
        self.log_container.push(f"🚀 开始批量执行: {cmd}")
        self.log_container.push(f"--------------------------------------------------")

        # 启动后台任务
        asyncio.create_task(self._process_batch(cmd, list(self.selected_urls)))

    async def _process_batch(self, cmd, urls):
        # 限制并发数，防止瞬间卡死 (例如同时只连 10 台)
        sem = asyncio.Semaphore(10)

        async def _worker(url):
            async with sem:
                # 找到服务器配置
                server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
                if not server: return
                
                name = server['name']
                
                # 尝试 UI 更新 (因为此时窗口可能已关闭)
                def log_safe(msg):
                    try: 
                        if self.log_container and self.log_container.visible:
                            self.log_container.push(msg)
                    except: pass # 窗口已关闭，忽略 UI 更新

                log_safe(f"⏳ [{name}] 连接中...")
                
                try:
                    # 在线程池中执行 SSH (复用你现有的 run_in_bg_executor)
                    # 我们需要一个非阻塞的 exec 函数
                    def ssh_sync_exec():
                        client, msg = get_ssh_client_sync(server) # 复用你的 WebSSH 辅助函数
                        if not client: return False, msg
                        try:
                            # 设置超时 30秒
                            stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
                            out = stdout.read().decode().strip()
                            err = stderr.read().decode().strip()
                            client.close()
                            return True, (out, err)
                        except Exception as e:
                            return False, str(e)

                    success, result = await run.io_bound(ssh_sync_exec)
                    
                    if success:
                        out, err = result
                        if out: log_safe(f"✅ [{name}] 输出:\n{out}")
                        if err: log_safe(f"⚠️ [{name}] 警告/错误:\n{err}")
                        if not out and not err: log_safe(f"✅ [{name}] 执行完成 (无返回内容)")
                    else:
                        log_safe(f"❌ [{name}] 失败: {result}")
                        
                except Exception as e:
                    log_safe(f"❌ [{name}] 系统异常: {e}")
                
                log_safe(f"--------------------------------------------------")

        # 创建所有任务
        tasks = [_worker(u) for u in urls]
        await asyncio.gather(*tasks)
        
        try:
            self.log_container.push("🏁 所有任务执行完毕")
            self.run_btn.enable()
            self.cmd_input.enable()
        except: pass

batch_ssh_manager = BatchSSH()


# =================  全能分组管理 (防重复国旗 + 真实IP) =================
def open_combined_group_management(group_name):
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-[600px] h-[80vh] flex flex-col p-0 gap-0 overflow-hidden'):
        
        # 1. 标题栏
        with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings', color='primary').classes('text-xl')
                ui.label(f'管理分组: {group_name}').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # 2. 内容区域
        with ui.column().classes('w-full flex-grow overflow-hidden p-0'):
            # --- A. 分组名称设置 ---
            with ui.column().classes('w-full p-4 border-b bg-white gap-2 flex-shrink-0'):
                ui.label('分组名称').classes('text-xs font-bold text-gray-500')
                name_input = ui.input(value=group_name).props('outlined dense').classes('w-full')

            # --- B. 成员选择区域 ---
            with ui.column().classes('w-full flex-grow overflow-hidden relative'):
                # 工具栏
                with ui.row().classes('w-full p-2 bg-gray-100 justify-between items-center border-b flex-shrink-0'):
                    ui.label('选择属于该组的服务器:').classes('text-xs font-bold text-gray-500 ml-2')
                    with ui.row().classes('gap-1'):
                        ui.button('全选', on_click=lambda: toggle_all(True)).props('flat dense size=xs color=primary')
                        ui.button('清空', on_click=lambda: toggle_all(False)).props('flat dense size=xs color=grey')

                with ui.scroll_area().classes('w-full flex-grow p-2'):
                    with ui.column().classes('w-full gap-1'):
                        
                        selection_map = {} 
                        checkbox_refs = {} 
                        
                        try: sorted_servers = sorted(SERVERS_CACHE, key=lambda x: smart_sort_key(x))
                        except: sorted_servers = SERVERS_CACHE 

                        if not sorted_servers:
                            ui.label('暂无服务器数据').classes('w-full text-center text-gray-400 mt-4')

                        for s in sorted_servers:
                            is_in_group = group_name in s.get('tags', [])
                            selection_map[s['url']] = is_in_group
                            
                            with ui.row().classes('w-full items-center p-2 hover:bg-blue-50 rounded border border-transparent hover:border-blue-200 transition'):
                                chk = ui.checkbox(value=is_in_group).props('dense')
                                checkbox_refs[s['url']] = chk
                                chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                                
                                # 信息展示
                                with ui.column().classes('gap-0 ml-2 flex-grow overflow-hidden'):
                                    with ui.row().classes('items-center gap-2'):
                                        
                                        # ✨✨✨ 修复：国旗防重复判断 ✨✨✨
                                        name_text = s['name']
                                        try:
                                            country = detect_country_group(name_text)
                                            flag = country.split(' ')[0] # 提取国旗 Emoji
                                            # 只有当名字里不包含这个国旗时，才添加
                                            if flag not in name_text:
                                                name_text = f"{flag} {name_text}"
                                        except: pass
                                        
                                        ui.label(name_text).classes('text-sm font-bold truncate')

                                # ✨✨✨ 修复：显示真实解析 IP ✨✨✨
                                ip_addr = get_real_ip_display(s['url'])

                                status = s.get('_status')
                                if status == 'online':
                                    stat_color = 'green-500'; stat_icon = 'bolt'
                                elif status == 'offline':
                                    stat_color = 'red-500'; stat_icon = 'bolt'
                                else:
                                    stat_color = 'grey-400'; stat_icon = 'help_outline'

                                with ui.row().classes('items-center gap-1'):
                                    ui.icon(stat_icon).classes(f'text-{stat_color} text-sm')
                                    # ✨ IP 静默更新
                                    ip_lbl = ui.label(ip_addr).classes('text-xs font-mono text-gray-500')
                                    bind_ip_label(s['url'], ip_lbl)

                def toggle_all(state):
                    for url, chk in checkbox_refs.items():
                        chk.value = state
                        selection_map[url] = state

        # 3. 底部按钮栏 (保持不变)
        with ui.row().classes('w-full p-4 border-t bg-gray-50 justify-between items-center flex-shrink-0'):
            async def delete_group():
                with ui.dialog() as confirm_d, ui.card():
                    ui.label(f'确定永久删除分组 "{group_name}"?').classes('font-bold text-red-600')
                    ui.label('组内的服务器不会被删除，仅移除标签。').classes('text-xs text-gray-500')
                    with ui.row().classes('w-full justify-end mt-4 gap-2'):
                        ui.button('取消', on_click=confirm_d.close).props('flat dense')
                        async def do_del():
                            if 'custom_groups' in ADMIN_CONFIG and group_name in ADMIN_CONFIG['custom_groups']:
                                ADMIN_CONFIG['custom_groups'].remove(group_name)
                            for s in SERVERS_CACHE:
                                if group_name in s.get('tags', []):
                                    s['tags'].remove(group_name)
                            await save_admin_config()
                            await save_servers()
                            confirm_d.close(); d.close()
                            render_sidebar_content.refresh()
                            if content_container: content_container.clear()
                            safe_notify(f'分组 "{group_name}" 已删除', 'positive')
                        ui.button('确认删除', color='red', on_click=do_del)
                confirm_d.open()

            ui.button('删除分组', icon='delete', color='red', on_click=delete_group).props('flat')

            async def save_changes():
                new_name = name_input.value.strip()
                if not new_name: return safe_notify('分组名称不能为空', 'warning')
                if new_name != group_name:
                    if 'custom_groups' in ADMIN_CONFIG:
                        if group_name in ADMIN_CONFIG['custom_groups']:
                            idx = ADMIN_CONFIG['custom_groups'].index(group_name)
                            ADMIN_CONFIG['custom_groups'][idx] = new_name
                        else:
                            ADMIN_CONFIG['custom_groups'].append(new_name)
                    await save_admin_config()
                for s in SERVERS_CACHE:
                    if 'tags' not in s: s['tags'] = []
                    should_have_tag = selection_map.get(s['url'], False)
                    if should_have_tag:
                        if new_name not in s['tags']: s['tags'].append(new_name)
                        if new_name != group_name and group_name in s['tags']:
                            s['tags'].remove(group_name)
                    else:
                        if new_name in s['tags']: s['tags'].remove(new_name)
                        if group_name in s['tags']: s['tags'].remove(group_name)
                await save_servers()
                d.close()
                render_sidebar_content.refresh()
                await refresh_content('TAG', new_name)
                safe_notify('分组设置已保存', 'positive')

            ui.button('保存修改', icon='save', on_click=save_changes).classes('bg-slate-900 text-white shadow-lg')

    d.open()
        
# =================侧边栏渲染 =====================
# ================= [侧边栏渲染：修复完整版] =================
@ui.refreshable
def render_sidebar_content():
    # 1. 顶部
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0'):
        ui.label('X-Fusion Panel').classes('text-xl font-bold mb-4 text-slate-800')
        btn_cls = 'w-full text-slate-700 active:scale-95 transition-transform duration-150'
        ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes(btn_cls)
        ui.button('服务器探针', icon='monitor_heart', on_click=render_probe_page).props('flat align=left').classes(btn_cls)
        ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(btn_cls)

    # 2. 列表区域
    with ui.column().classes('w-full flex-grow overflow-y-auto p-2 gap-1'):
        with ui.row().classes('w-full gap-2 px-1 mb-4'):
            func_btn_cls = 'flex-grow text-xs active:scale-95 transition-transform duration-150'
            ui.button('新建分组', icon='create_new_folder', on_click=open_create_group_dialog).props('dense unelevated').classes(f'bg-blue-600 text-white {func_btn_cls}')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props('dense unelevated').classes(func_btn_cls)

        # --- A. 全部服务器 ---
        list_item_cls = 'w-full items-center justify-between p-3 border rounded mb-2 bg-slate-100 hover:bg-slate-200 cursor-pointer group active:scale-95 transition-transform duration-150'
        with ui.row().classes(list_item_cls).props('clickable v-ripple').on('click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-2'):
                ui.icon('dns', color='primary')
                ui.label('所有服务器').classes('font-bold')
            ui.badge(str(len(SERVERS_CACHE)), color='blue')

        # --- B. ✨✨✨ 找回：自定义分组 (Tags) ✨✨✨ ---
        if 'custom_groups' in ADMIN_CONFIG and ADMIN_CONFIG['custom_groups']:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-2 mb-1 px-2')
            for tag_group in ADMIN_CONFIG['custom_groups']:
                # 统计逻辑：包含 Tag 或者 Group 名字匹配
                tag_servers = [
                    s for s in SERVERS_CACHE 
                    if tag_group in s.get('tags', []) or s.get('group') == tag_group
                ]
                
                is_open = tag_group in EXPANDED_GROUPS
                with ui.expansion('', icon='label', value=is_open).classes('w-full border rounded mb-1 bg-white shadow-sm').props('expand-icon-toggle').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                    with exp.add_slot('header'):
                        header_cls = 'w-full h-full items-center justify-between no-wrap cursor-pointer active:scale-95 transition-transform duration-150'
                        with ui.row().classes(header_cls).props('clickable v-ripple').on('click', lambda _, g=tag_group: refresh_content('TAG', g)):
                            ui.label(tag_group).classes('flex-grow font-bold truncate')
                            # 分组设置按钮
                            ui.button(icon='settings', on_click=lambda _, g=tag_group: open_combined_group_management(g)).props('flat dense round size=xs color=grey-6').on('click.stop').tooltip('管理此分组')
                            ui.badge(str(len(tag_servers)), color='orange' if not tag_servers else 'grey')
                    
                    with ui.column().classes('w-full gap-0 bg-gray-50'):
                        if not tag_servers: ui.label('空分组').classes('text-xs text-gray-400 p-2 italic')
                        for s in tag_servers:
                            sub_row_cls = 'w-full justify-between items-center p-2 pl-4 border-b border-gray-100 hover:bg-blue-100 cursor-pointer group active:scale-95 transition-transform duration-150'
                            with ui.row().classes(sub_row_cls).props('clickable v-ripple').on('click', lambda _, s=s: refresh_content('SINGLE', s)):
                                ui.label(s['name']).classes('text-sm truncate flex-grow')
                                with ui.row().classes('gap-1 items-center'):
                                    ui.button(icon='edit', on_click=lambda _, idx=SERVERS_CACHE.index(s): open_server_dialog(idx)).props('flat dense round size=xs color=grey').on('click.stop')

        # --- C. 智能区域分组 ---
        ui.label('区域分组').classes('text-xs font-bold text-gray-400 mt-2 mb-1 px-2')
        
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s)
            # 过滤垃圾分组
            if c_group in ['默认分组', '自动注册', '自动导入', '未分组', '']:
                c_group = '🏳️ 其他地区'
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)
        
        for c_name in sorted(country_buckets.keys()):
            c_servers = country_buckets[c_name]
            c_servers.sort(key=lambda x: x.get('name',''))
            is_open = c_name in EXPANDED_GROUPS
            
            with ui.expansion('', icon='public', value=is_open).classes('w-full border rounded mb-1 bg-white shadow-sm').props('expand-icon-toggle').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                 with exp.add_slot('header'):
                    with ui.row().classes('w-full h-full items-center justify-between no-wrap cursor-pointer').props('clickable v-ripple').on('click', lambda _, g=c_name: refresh_content('COUNTRY', g)):
                        ui.label(c_name).classes('flex-grow font-bold truncate')
                        
                        # ✨✨✨ 找回：批量管理按钮 (小铅笔图标) ✨✨✨
                        ui.button(icon='edit_note', on_click=lambda _, s=c_servers, t=c_name: open_bulk_edit_dialog(s, f"区域: {t}")).props('flat dense round size=xs color=grey').on('click.stop').tooltip('批量管理此区域')
                        
                        ui.badge(str(len(c_servers)), color='green')
                 
                 with ui.column().classes('w-full gap-0 bg-gray-50'):
                    for s in c_servers:
                         with ui.row().classes('w-full justify-between items-center p-2 pl-4 border-b border-gray-100 hover:bg-blue-100 cursor-pointer').props('clickable v-ripple').on('click', lambda _, s=s: refresh_content('SINGLE', s)):
                                ui.label(s['name']).classes('text-sm truncate flex-grow')
                                with ui.row().classes('gap-1 items-center'):
                                    ui.button(icon='edit', on_click=lambda _, idx=SERVERS_CACHE.index(s): open_server_dialog(idx)).props('flat dense round size=xs color=grey').on('click.stop')

    # 3. ✨✨✨ 找回：底部功能区 (含备份按钮) ✨✨✨
    with ui.column().classes('w-full p-2 border-t mt-auto mb-15 gap-2 bg-white z-10'):
        bottom_btn_cls = 'w-full font-bold mb-1 active:scale-95 transition-transform duration-150'
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props('flat align=left').classes(f'text-slate-800 bg-blue-50 hover:bg-blue-100 {bottom_btn_cls}')
        
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props('flat align=left').classes('w-full text-slate-600 text-sm active:scale-95 transition-transform duration-150')
        
        # 备份按钮回来了！
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes('w-full text-slate-600 text-sm active:scale-95 transition-transform duration-150')
        
# ================== 登录与 MFA 逻辑 ==================
@ui.page('/login')
def login_page(request: Request): 
    # 容器：用于切换登录步骤 (账号密码 -> MFA)
    container = ui.card().classes('absolute-center w-full max-w-sm p-8 shadow-2xl rounded-xl bg-white')

    # --- 步骤 1: 账号密码验证 ---
    def render_step1():
        container.clear()
        with container:
            ui.label('X-Fusion Panel').classes('text-2xl font-extrabold mb-2 w-full text-center text-slate-800')
            ui.label('请登录以继续').classes('text-sm text-gray-400 mb-6 w-full text-center')
            
            username = ui.input('账号').props('outlined dense').classes('w-full mb-3')
            password = ui.input('密码', password=True).props('outlined dense').classes('w-full mb-6').on('keydown.enter', lambda: check_cred())
            
            def check_cred():
                if username.value == ADMIN_USER and password.value == ADMIN_PASS:
                    # 账号密码正确，进入 MFA 流程
                    check_mfa()
                else:
                    ui.notify('账号或密码错误', color='negative', position='top')

            ui.button('下一步', on_click=check_cred).classes('w-full bg-slate-900 text-white shadow-lg h-10')

            # --- ✨✨✨ 新增：底部版权信息 ✨✨✨ ---
            ui.label('© Powered by 小龙女她爸').classes('text-xs text-gray-400 mt-6 w-full text-center font-mono opacity-80')
            # ----------------------------------------

    # --- 步骤 2: MFA 验证或设置 ---
    def check_mfa():
        secret = ADMIN_CONFIG.get('mfa_secret')
        if not secret:
            # 如果没有密钥，进入初始化流程 (生成新密钥)
            new_secret = pyotp.random_base32()
            render_setup(new_secret)
        else:
            # 已有密钥，进入验证流程
            render_verify(secret)

    # 渲染 MFA 设置页面 (首次登录)
    def render_setup(secret):
        container.clear()
        
        # 生成二维码图片 Base64
        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name="X-Fusion Panel")
        qr = qrcode.make(totp_uri)
        img_buffer = io.BytesIO()
        qr.save(img_buffer, format='PNG')
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        with container:
            ui.label('绑定二次验证 (MFA)').classes('text-xl font-bold mb-2 w-full text-center')
            ui.label('请使用 Authenticator App 扫描').classes('text-xs text-gray-400 mb-2 w-full text-center')
            
            with ui.row().classes('w-full justify-center mb-2'):
                ui.image(f'data:image/png;base64,{img_b64}').style('width: 180px; height: 180px')
            
            # 点击复制密钥功能
            with ui.row().classes('w-full justify-center items-center gap-1 mb-4 bg-gray-100 p-1 rounded cursor-pointer').on('click', lambda: safe_copy_to_clipboard(secret)):
                ui.label(secret).classes('text-xs font-mono text-gray-600')
                ui.icon('content_copy').classes('text-gray-400 text-xs')

            code = ui.input('验证码', placeholder='6位数字').props('outlined dense input-class=text-center').classes('w-full mb-4')
            
            async def confirm():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    # 验证成功，保存密钥
                    ADMIN_CONFIG['mfa_secret'] = secret
                    await save_admin_config()
                    ui.notify('绑定成功', type='positive')
                    finish()
                else:
                    ui.notify('验证码错误', type='negative')

            ui.button('确认绑定', on_click=confirm).classes('w-full bg-green-600 text-white h-10')

    # 渲染 MFA 验证页面 (日常登录)
    def render_verify(secret):
        container.clear()
        with container:
            ui.label('安全验证').classes('text-xl font-bold mb-6 w-full text-center')
            
            with ui.column().classes('w-full items-center mb-6'):
                ui.icon('verified_user').classes('text-6xl text-blue-600 mb-2')
                ui.label('请输入 Authenticator 动态码').classes('text-xs text-gray-400')

            code = ui.input(placeholder='------').props('outlined input-class=text-center text-xl tracking-widest').classes('w-full mb-6')
            code.on('keydown.enter', lambda: verify())
            
            # 自动聚焦输入框 (JS)
            ui.timer(0.1, lambda: ui.run_javascript(f'document.querySelector(".q-field__native").focus()'), once=True)

            def verify():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
                    finish()
                else:
                    ui.notify('无效的验证码', type='negative', position='top')
                    code.value = ''

            ui.button('验证登录', on_click=verify).classes('w-full bg-slate-900 text-white h-10')
            ui.button('返回', on_click=render_step1).props('flat dense').classes('w-full mt-2 text-gray-400 text-xs')

    def finish():
        app.storage.user['authenticated'] = True
        
        # --- 登录成功后记录真实 IP ---
        # 优先获取 X-Forwarded-For (适配 Docker/反代)，否则获取直连 IP
        try:
            client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
            app.storage.user['login_ip'] = client_ip
        except:
            pass # 防止极端情况报错
        # --------------------------------------

        ui.navigate.to('/')

    render_step1()



# ================= [本地化版] 主页入口 =================
@ui.page('/')
def main_page(request: Request):
    # ✨✨✨ 原有的本地静态文件引用 ✨✨✨
    ui.add_head_html('<link rel="stylesheet" href="/static/xterm.css" />')
    ui.add_head_html('<script src="/static/xterm.js"></script>')
    ui.add_head_html('<script src="/static/xterm-addon-fit.js"></script>')

    # ✨✨✨ [新增] 修复 Windows 国旗显示问题 ✨✨✨
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            body { font-family: "Roboto", "Helvetica", "Arial", sans-serif, "Noto Color Emoji"; }
        </style>
    ''')

    # ================= 2. 基础认证检查 =================
    if not app.storage.user.get('authenticated', False):
        return RedirectResponse('/login')

    # ================= 3. 获取并检查 IP =================
    try:
        current_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
        recorded_ip = app.storage.user.get('login_ip')
        
        if recorded_ip and recorded_ip != current_ip:
            app.storage.user.clear()
            ui.notify('环境变动，请重新登录', type='negative')
            return RedirectResponse('/login')
            
        display_ip = recorded_ip if recorded_ip else current_ip
    except:
        display_ip = "Unknown"

    # ================= 4. UI 构建 (响应式布局改造) =================
    
    # ✨ 改动 1: 定义左侧抽屉 (Drawer)
    # value=True: 电脑端默认展开; fixed=False: 推挤模式(不遮挡内容)
    with ui.left_drawer(value=True, fixed=True).classes('bg-gray-50 border-r').props('width=360 bordered') as drawer:
        render_sidebar_content()

    # ✨ 改动 2: 顶部 Header 增加控制按钮
    with ui.header().classes('bg-slate-900 text-white h-14'):
        with ui.row().classes('w-full items-center justify-between'):
            
            # --- 左侧：菜单按钮 + 标题 + IP ---
            with ui.row().classes('items-center gap-2'):
                # 👇 这里就是你刚才问的代码，现在它能控制上面的 drawer 了
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense')
                
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2')
                ui.label(f"[{display_ip}]").classes('text-xs text-gray-400 font-mono pt-1 hidden sm:block') # 手机隐藏IP防止拥挤

            # --- 右侧：密钥 + 登出 ---
            with ui.row().classes('items-center gap-2 mr-2'):
                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round').tooltip('点击复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating')
                
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense').tooltip('退出登录')

    # ✨ 改动 3: 内容区域 (不再需要 ui.row 包裹)
    # 直接作为主容器，Drawer 会自动处理它的位置
    global content_container
    content_container = ui.column().classes('w-full h-full pl-4 pr-4 pt-4 overflow-y-auto bg-slate-50')
    
    # ================= 6. 启动后台任务 =================
    
    # 启动仪表盘数据刷新 (只运行一次)
    ui.timer(0.1, lambda: asyncio.create_task(load_dashboard_stats()), once=True)
    
    logger.info("✅ UI 已就绪")
    

# ================= 全局定时 Ping 任务 (仅启动一次 + 限流保护) =================
async def run_global_ping_task():
    # 依然需要限流！否则启动时瞬间并发几百个 Ping 还是会卡死网页
    semaphore = asyncio.Semaphore(5)

    async def protected_ping_task(nodes, host):
        async with semaphore:
            try:
                await batch_ping_nodes(nodes, host)
            except:
                pass
            # 测完休息 0.5 秒
            await asyncio.sleep(0.5)

    # ❌ 移除了 while True 循环，只执行一次
    try:
        logger.info("📡 [系统启动] 执行首次全局延迟测试...")
        tasks = []
        for srv in SERVERS_CACHE:
            raw_host = srv['url']
            try:
                if '://' not in raw_host: raw_host = f'http://{raw_host}'
                p = urlparse(raw_host); raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
            except: continue
            
            nodes = NODES_DATA.get(srv['url'], [])
            if nodes:
                tasks.append(protected_ping_task(nodes, raw_host))
        
        if tasks:
            await asyncio.gather(*tasks)
        
        logger.info("✅ 首次延迟测试完成 (后台任务已结束)")
    except Exception as e:
        logger.error(f"Ping 任务异常: {e}")


# ✨✨✨ 注册本地静态文件目录 ✨✨✨
app.add_static_files('/static', 'static')
# ================= 优雅的后台任务调度 (APScheduler) =================

# 1. 定义流量同步任务 (单次运行逻辑)
async def job_sync_all_traffic():
    logger.info("🕒 [定时任务] 开始全量同步流量...")
    tasks = [fetch_inbounds_safe(s, force_refresh=True) for s in SERVERS_CACHE]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        await refresh_dashboard_ui()
    logger.info("✅ [定时任务] 流量同步完成")

# 2.================= 定时任务：IP 地理位置检查 & 自动修正名称 =================
async def job_check_geo_ip():
    logger.info("🌍 [定时任务] 开始全量 IP 归属地检测与名称修正...")
    geo_updated = False
    
    for s in SERVERS_CACHE:
        # 如果名字里已经有 emoji 国旗了 (比如 "🇯🇵")，就跳过，避免重复请求
        # 我们检测常见的国旗 Emoji 范围，或者简单的判断
        current_name = s.get('name', '')
        if any(c in current_name for c in ['🇯🇵','🇺🇸','🇸🇬','🇭🇰','🇰🇷','🇩🇪','🇬🇧','🇹🇼','🇨🇳']):
            continue

        try:
            # 请求 API 获取地理位置
            geo = await run.io_bound(fetch_geo_from_ip, s['url'])
            if geo:
                # geo = (lat, lon, country_name)
                s['lat'] = geo[0]
                s['lon'] = geo[1]
                s['_detected_region'] = geo[2] 
                
                # ✨✨✨ 核心改变：强制重命名 (加国旗) ✨✨✨
                # 获取 "🇯🇵 日本" 这样的字符串
                flag_prefix = get_flag_for_country(geo[2]) 
                flag_icon = flag_prefix.split(' ')[0] # 只取 "🇯🇵"
                
                # 如果名字开头不是这个国旗，就强行加上去
                if not current_name.startswith(flag_icon):
                    s['name'] = f"{flag_icon} {current_name}"
                    logger.info(f"✨ [自动修正] {current_name} -> {s['name']}")
                    geo_updated = True
        except Exception as e:
            pass
            
    if geo_updated:
        await save_servers()
        await refresh_dashboard_ui()
        render_sidebar_content.refresh()
        safe_notify("已完成所有服务器的地理位置修正", "positive")

# 3. 初始化调度器
scheduler = AsyncIOScheduler()

# 4. 系统启动序列
async def startup_sequence():
    global PROCESS_POOL
    # ✨ 初始化进程池 (4核) - 专门处理 Ping 等 CPU/阻塞任务
    PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动 (ProcessPoolExecutor)")

    # ✨ 添加定时任务
    # max_instances=1 保证同一个任务永远不会叠加（防崩关键）
    scheduler.add_job(job_sync_all_traffic, 'interval', hours=3, id='traffic_sync', replace_existing=True, max_instances=1)
    scheduler.start()
    logger.info("🕒 APScheduler 定时任务已启动")

    # ✨ 开机立即执行一次 (作为初始化)
    asyncio.create_task(job_sync_all_traffic())
    asyncio.create_task(job_check_geo_ip())

# 注册启动与关闭事件
app.on_startup(startup_sequence)
app.on_shutdown(lambda: PROCESS_POOL.shutdown(wait=False) if PROCESS_POOL else None)


if __name__ in {"__main__", "__mp_main__"}:
    logger.info("🚀 系统正在初始化...")
    ui.run(title='X-Fusion Panel', host='0.0.0.0', port=8080, language='zh-CN', storage_secret='sijuly_secret_key', reload=False)

