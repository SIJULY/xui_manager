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
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from urllib.parse import urlparse, quote
from nicegui import ui, run, app, Client
from fastapi import Response, Request
from fastapi.responses import RedirectResponse
from collections import Counter

IP_GEO_CACHE = {}

# ================= 定义全局进程池变量  =================
PROCESS_POOL = None 

# ================= 全局 同步 Ping 函数 =================
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


# =================强制 GeoIP 命名与分组任务  =================
async def force_geoip_naming_task(server_conf, max_retries=10):
    """
    强制执行 GeoIP 解析，直到成功或达到最大重试次数。
    成功后：
    1. 命名格式：🇺🇸 美国-1, 🇭🇰 香港-2
    2. 分组：自动分入对应国家组
    """
    url = server_conf['url']
    logger.info(f"🌍 [强制修正] 开始处理: {url} (目标: 国旗+国家+序号)")
    
    for i in range(max_retries):
        try:
            # 1. 查询 GeoIP
            geo_info = await run.io_bound(fetch_geo_from_ip, url)
            
            if geo_info:
                # geo_info 格式: (lat, lon, 'United States')
                country_raw = geo_info[2]
                
                # 2. 获取标准化的 "国旗+国家" 字符串，例如 "🇺🇸 美国"
                flag_group = get_flag_for_country(country_raw)
                
                # 3. 计算序号 (查找现有多少个同类服务器)
                # 逻辑：遍历所有服务器，看有多少个名字是以 "🇺🇸 美国" 开头的
                count = 1
                for s in SERVERS_CACHE:
                    # 排除自己 (如果是刚加进去的，可能已经存在于列表中，需要注意去重逻辑，这里简单处理)
                    if s is not server_conf and s.get('name', '').startswith(flag_group):
                        count += 1
                
                # 4. 生成最终名称
                final_name = f"{flag_group}-{count}"
                
                # 5. 应用更改
                old_name = server_conf.get('name', '')
                if old_name != final_name:
                    server_conf['name'] = final_name
                    server_conf['group'] = flag_group # 自动分组
                    server_conf['_detected_region'] = country_raw # 记录原始地区信息
                    
                    # 保存并刷新
                    await save_servers()
                    await refresh_dashboard_ui()
                    try: render_sidebar_content.refresh()
                    except: pass
                    
                    logger.info(f"✅ [强制修正] 成功: {old_name} -> {final_name} (第 {i+1} 次尝试)")
                    return # 成功退出
            
            # 如果没查到，打印日志
            logger.warning(f"⏳ [强制修正] 第 {i+1} 次解析 IP 归属地失败，3秒后重试...")
            
        except Exception as e:
            logger.error(f"❌ [强制修正] 异常: {e}")

        # 等待后重试
        await asyncio.sleep(3)

    logger.warning(f"⚠️ [强制修正] 最终失败: 达到最大重试次数，保持原名 {server_conf.get('name')}")


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


# ================= 全局 DNS 缓存  ======================
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

# ================= 获取国旗  =================
def get_flag_for_country(country_name):
    if not country_name: return "🏳️ 未知"
    
    # 1. 正向匹配：检查 Key (例如 API返回 'Singapore', Key 有 'Singapore')
    for k, v in AUTO_COUNTRY_MAP.items():
        if k.upper() == country_name.upper() or k in country_name:
            return v 
    
    # 2. ✨✨✨ 反向匹配：检查 Value (解决中文匹配问题) ✨✨✨
    # API返回 '新加坡'，虽然 Key 里没有，但 Value '🇸🇬 新加坡' 里包含它！
    for v in AUTO_COUNTRY_MAP.values():
        if country_name in v:
            return v

    # 3. 实在找不到，返回白旗
    return f"🏳️ {country_name}"

# ✨✨✨自动给名称添加国旗 ✨✨✨
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

# ================= 全局设置弹窗 =================
def open_global_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            ui.label('🔐 全局 SSH 密钥设置').classes('text-xl font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        
        with ui.column().classes('w-full mt-2'):
            ui.label('全局 SSH 私钥').classes('text-sm font-bold text-gray-700')
            ui.label('当服务器未单独配置密钥时，默认使用此密钥连接。').classes('text-xs text-gray-400 mb-2')
            key_input = ui.textarea(placeholder='-----BEGIN OPENSSH PRIVATE KEY-----', value=load_global_key()).classes('w-full font-mono text-xs').props('outlined rows=10')

        async def save_all():
            save_global_key(key_input.value)
            safe_notify('✅ 全局密钥已保存', 'positive')
            d.close()

        ui.button('保存密钥', icon='save', on_click=save_all).classes('w-full bg-slate-900 text-white shadow-lg h-12 mt-2')
    d.open()



    
# ================= 全局变量区 (缓存) =================
PROBE_DATA_CACHE = {} 
PING_TREND_CACHE = {} 

# ✨✨✨ [新增] 全局记录历史数据的函数 ✨✨✨
def record_ping_history(url, pings_dict):
    """
    不管前台是否打开，后台收到数据就调用此函数记录历史。
    """
    if not url or not pings_dict: return
    
    current_ts = time.time()
    import datetime
    time_str = datetime.datetime.fromtimestamp(current_ts).strftime('%H:%M:%S')
    
    # 提取数据
    ct = pings_dict.get('电信', 0); ct = ct if ct > 0 else 0
    cu = pings_dict.get('联通', 0); cu = cu if cu > 0 else 0
    cm = pings_dict.get('移动', 0); cm = cm if cm > 0 else 0
    
    # 初始化
    if url not in PING_TREND_CACHE: PING_TREND_CACHE[url] = []
    
    # 追加新记录
    PING_TREND_CACHE[url].append({
        'ts': current_ts, 
        'time_str': time_str, 
        'ct': ct, 
        'cu': cu, 
        'cm': cm
    })
    
    # 限制长度：保留最近 4 小时的数据 (假设每3秒一条，约4800条)
    # 这样既保证有数据，又不撑爆内存
    if len(PING_TREND_CACHE[url]) > 5000:
        PING_TREND_CACHE[url] = PING_TREND_CACHE[url][-5000:]

        
# ================= 探针安装脚本  =================
PROBE_INSTALL_SCRIPT = r"""
bash -c '
# 1. 提升权限
[ "$(id -u)" -eq 0 ] || { command -v sudo >/dev/null && exec sudo bash "$0" "$@"; echo "Root required"; exit 1; }

# 2. 安装基础依赖
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y python3 iputils-ping util-linux >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y python3 iputils util-linux >/dev/null 2>&1
elif [ -f /etc/alpine-release ]; then
    apk add python3 iputils util-linux >/dev/null 2>&1
fi

# 3. 写入 Python 脚本
cat > /root/x_fusion_agent.py << "PYTHON_EOF"
import time, json, os, socket, sys, subprocess, re, platform
import urllib.request, urllib.error
import ssl

MANAGER_URL = "__MANAGER_URL__/api/probe/push"
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

# ✨✨✨ 新增：读取网卡流量辅助函数 ✨✨✨
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
        # ✨ 第一次采样 (网络 + CPU)
        net_in_1, net_out_1 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot1, idle1 = sum(fs), fs[3]
        
        # 等待 1 秒
        time.sleep(1)
        
        # ✨ 第二次采样 (网络 + CPU)
        net_in_2, net_out_2 = get_network_bytes()
        with open("/proc/stat") as f:
            fs = [float(x) for x in f.readline().split()[1:5]]
            tot2, idle2 = sum(fs), fs[3]
            
        # 计算差值
        data["cpu_usage"] = round((1 - (idle2-idle1)/(tot2-tot1)) * 100, 1)
        data["cpu_cores"] = os.cpu_count() or 1
        
        # ✨ 计算实时网速 (差值)
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
        time.sleep(1) # 稍微加快推送频率，因为采集本身耗时1秒

if __name__ == "__main__":
    push()
PYTHON_EOF

# 4. 创建服务
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

# 5. 启动
systemctl daemon-reload
systemctl enable x-fusion-agent
systemctl restart x-fusion-agent
exit 0
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
AUTO_COUNTRY_MAP = {
    # --- 亚太地区 ---
    '🇨🇳': '🇨🇳 中国', 'China': '🇨🇳 中国', '中国': '🇨🇳 中国', 'CN': '🇨🇳 中国',
    '🇭🇰': '🇭🇰 香港', 'HK': '🇭🇰 香港', 'Hong Kong': '🇭🇰 香港',
    '🇹🇼': '🇹🇼 台湾', 'TW': '🇹🇼 台湾', 'Taiwan': '🇹🇼 台湾',
    '🇯🇵': '🇯🇵 日本', 'JP': '🇯🇵 日本', 'Japan': '🇯🇵 日本', 'Tokyo': '🇯🇵 日本', 'Osaka': '🇯🇵 日本',
    '🇸🇬': '🇸🇬 新加坡', 'SG': '🇸🇬 新加坡', 'Singapore': '🇸🇬 新加坡',
    '🇰🇷': '🇰🇷 韩国', 'KR': '🇰🇷 韩国', 'Korea': '🇰🇷 韩国', 'Seoul': '🇰🇷 韩国', 'Chuncheon': '🇰🇷 韩国',
    '🇮🇳': '🇮🇳 印度', 'IN': '🇮🇳 印度', 'India': '🇮🇳 印度', 'Mumbai': '🇮🇳 印度', 'Hyderabad': '🇮🇳 印度',
    '🇮🇩': '🇮🇩 印尼', 'ID': '🇮🇩 印尼', 'Indonesia': '🇮🇩 印尼', 'Jakarta': '🇮🇩 印尼',
    '🇲🇾': '🇲🇾 马来西亚', 'MY': '🇲🇾 马来西亚', 'Malaysia': '🇲🇾 马来西亚',
    '🇹🇭': '🇹🇭 泰国', 'TH': '🇹🇭 泰国', 'Thailand': '🇹🇭 泰国', 'Bangkok': '🇹🇭 泰国',
    '🇻🇳': '🇻🇳 越南', 'VN': '🇻🇳 越南', 'Vietnam': '🇻🇳 越南',
    '🇵🇭': '🇵🇭 菲律宾', 'PH': '🇵🇭 菲律宾', 'Philippines': '🇵🇭 菲律宾',
    '🇦🇺': '🇦🇺 澳大利亚', 'AU': '🇦🇺 澳大利亚', 'Australia': '🇦🇺 澳大利亚', 'Sydney': '🇦🇺 澳大利亚', 'Melbourne': '🇦🇺 澳大利亚',

    # --- 北美地区 ---
    '🇺🇸': '🇺🇸 美国', 'USA': '🇺🇸 美国', 'United States': '🇺🇸 美国', 'America': '🇺🇸 美国',
    '🇨🇦': '🇨🇦 加拿大', 'CA': '🇨🇦 加拿大', 'Canada': '🇨🇦 加拿大', 'Toronto': '🇨🇦 加拿大', 'Montreal': '🇨🇦 加拿大',
    '🇲🇽': '🇲🇽 墨西哥', 'MX': '🇲🇽 墨西哥', 'Mexico': '🇲🇽 墨西哥', 'Queretaro': '🇲🇽 墨西哥',

    # --- 南美地区 ---
    '🇧🇷': '🇧🇷 巴西', 'BR': '🇧🇷 巴西', 'Brazil': '🇧🇷 巴西', 'Sao Paulo': '🇧🇷 巴西',
    '🇨🇱': '🇨🇱 智利', 'CL': '🇨🇱 智利', 'Chile': '🇨🇱 智利', 'Santiago': '🇨🇱 智利',
    '🇦🇷': '🇦🇷 阿根廷', 'AR': '🇦🇷 阿根廷', 'Argentina': '🇦🇷 阿根廷',

    # --- 欧洲地区 ---
    '🇬🇧': '🇬🇧 英国', 'UK': '🇬🇧 英国', 'United Kingdom': '🇬🇧 英国', 'London': '🇬🇧 英国',
    '🇩🇪': '🇩🇪 德国', 'DE': '🇩🇪 德国', 'Germany': '🇩🇪 德国', 'Frankfurt': '🇩🇪 德国',
    '🇫🇷': '🇫🇷 法国', 'FR': '🇫🇷 法国', 'France': '🇫🇷 法国', 'Paris': '🇫🇷 法国', 'Marseille': '🇫🇷 法国',
    '🇳🇱': '🇳🇱 荷兰', 'NL': '🇳🇱 荷兰', 'Netherlands': '🇳🇱 荷兰', 'Amsterdam': '🇳🇱 荷兰',
    '🇷🇺': '🇷🇺 俄罗斯', 'RU': '🇷🇺 俄罗斯', 'Russia': '🇷🇺 俄罗斯', 'Moscow': '🇷🇺 俄罗斯',
    '🇮🇹': '🇮🇹 意大利', 'IT': '🇮🇹 意大利', 'Italy': '🇮🇹 意大利', 'Milan': '🇮🇹 意大利',
    '🇪🇸': '🇪🇸 西班牙', 'ES': '🇪🇸 西班牙', 'Spain': '🇪🇸 西班牙', 'Madrid': '🇪🇸 西班牙',
    '🇸🇪': '🇸🇪 瑞典', 'SE': '🇸🇪 瑞典', 'Sweden': '🇸🇪 瑞典', 'Stockholm': '🇸🇪 瑞典',
    '🇨🇭': '🇨🇭 瑞士', 'CH': '🇨🇭 瑞士', 'Switzerland': '🇨🇭 瑞士', 'Zurich': '🇨🇭 瑞士',
    '🇵🇱': '🇵🇱 波兰', 'PL': '🇵🇱 波兰', 'Poland': '🇵🇱 波兰', 'Warsaw': '🇵🇱 波兰',
    '🇮🇪': '🇮🇪 爱尔兰', 'IE': '🇮🇪 爱尔兰', 'Ireland': '🇮🇪 爱尔兰',

    # --- 中东与非洲 ---
    '🇦🇪': '🇦🇪 阿联酋', 'AE': '🇦🇪 阿联酋', 'UAE': '🇦🇪 阿联酋', 'Dubai': '🇦🇪 阿联酋',
    '🇹🇷': '🇹🇷 土耳其', 'TR': '🇹🇷 土耳其', 'Turkey': '🇹🇷 土耳其', 'Istanbul': '🇹🇷 土耳其',
    '🇮🇱': '🇮🇱 以色列', 'IL': '🇮🇱 以色列', 'Israel': '🇮🇱 以色列', 'Jerusalem': '🇮🇱 以色列',
    '🇿🇦': '🇿🇦 南非', 'ZA': '🇿🇦 南非', 'South Africa': '🇿🇦 南非', 'Johannesburg': '🇿🇦 南非',
    '🇸🇦': '🇸🇦 沙特', 'SA': '🇸🇦 沙特', 'Saudi Arabia': '🇸🇦 沙特',
}

# ================= 智能分组核心  =================
def detect_country_group(name, server_config=None):
    # 1. ✨ 最高优先级：手动设置的分组 ✨
    if server_config:
        saved_group = server_config.get('group')
        # 排除无效分组
        if saved_group and saved_group.strip() and saved_group not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区', '其他地区']:
            # 尝试标准化 (如输入 "美国" -> "🇺🇸 美国")
            for v in AUTO_COUNTRY_MAP.values():
                if saved_group in v or v in saved_group:
                    return v 
            return saved_group

    # 2. ✨✨✨ 第二优先级：看图识字 + 智能关键字匹配 ✨✨✨
    name_upper = name.upper()
    
    # 🌟 关键优化：按长度倒序匹配 (优先匹配 "United States" 而非 "US")
    # 这样可以防止长词被短词截胡
    sorted_keys = sorted(AUTO_COUNTRY_MAP.keys(), key=len, reverse=True)
    
    import re
    
    for key in sorted_keys:
        val = AUTO_COUNTRY_MAP[key]
        
        if key in name_upper:
            # 🌟 核心修复：针对 2-3 位短字母缩写 (如 CL, US, SG, ID)
            # 必须前后是符号或边界，不能夹在单词里 (防止 Oracle 匹配到 CL)
            if len(key) <= 3 and key.isalpha():
                # 正则：(?<![A-Z0-9]) 表示前面不能是字母数字
                #       (?![A-Z0-9])  表示后面不能是字母数字
                pattern = r'(?<![A-Z0-9])' + re.escape(key) + r'(?![A-Z0-9])'
                if re.search(pattern, name_upper):
                    return val
            else:
                # 长关键字 (Japan) 或 Emoji (🇯🇵) 或带符号的 (HK-)，直接匹配
                return val

    # 3. 第三优先级：IP 检测的隐藏字段
    if server_config and server_config.get('_detected_region'):
        detected = server_config['_detected_region'].upper()
        for key, val in AUTO_COUNTRY_MAP.items():
            if key.upper() == detected or key.upper() in detected:
                return val
            
    return '🏳️ 其他地区'

# ================= 2D 平面地图：结构与样式  =================
GLOBE_STRUCTURE = r"""
<style>
    /* 容器填满父级 */
    #earth-container {
        width: 100%;
        height: 100%;
        position: relative;
        overflow: hidden;
        border-radius: 12px;
        background-color: #100C2A; /* 深色背景 */
    }
    
    /* 统计面板 */
    .earth-stats {
        position: absolute;
        top: 20px;
        left: 20px;
        color: rgba(255, 255, 255, 0.8);
        font-family: 'Consolas', monospace;
        font-size: 12px;
        z-index: 10;
        background: rgba(0, 20, 40, 0.6);
        padding: 10px 15px;
        border: 1px solid rgba(0, 255, 255, 0.3);
        border-radius: 6px;
        backdrop-filter: blur(4px);
        pointer-events: none;
    }
    .earth-stats span { color: #00ffff; font-weight: bold; }
</style>

<div id="earth-container">
    <div class="earth-stats">
        <div>ACTIVE NODES: <span id="node-count">0</span></div>
        <div>REGIONS: <span id="region-count">0</span></div>
    </div>
    <div id="earth-render-area" style="width:100%; height:100%;"></div>
</div>
"""
# ================= 2D 平面地图：JS 逻辑  =================
GLOBE_JS_LOGIC = r"""
(function() {
    const serverData = window.GLOBE_DATA || [];
    // ✨✨✨ 获取 Python 传过来的真实总数 ✨✨✨
    const realTotal = window.SERVER_TOTAL || serverData.length;
    
    const container = document.getElementById('earth-render-area');
    if (!container) return;

    // 更新统计面板
    const nodeCountEl = document.getElementById('node-count');
    const regionCountEl = document.getElementById('region-count');
    

    if(nodeCountEl) nodeCountEl.textContent = realTotal;
    
    const uniqueRegions = new Set(serverData.map(s => s.name));
    if(regionCountEl) regionCountEl.textContent = uniqueRegions.size;

    const myChart = echarts.init(container);

    // ✨✨✨ 1. 终极国旗/名称 -> 搜索关键词映射 (支持全球主要地区) ✨✨✨
    const searchKeys = {
        // --- 北美 ---
        '🇺🇸': 'United States', 'US': 'United States', 'USA': 'United States',
        '🇨🇦': 'Canada', 'CA': 'Canada',
        '🇲🇽': 'Mexico', 'MX': 'Mexico',
        
        // --- 欧洲 ---
        '🇬🇧': 'United Kingdom', 'UK': 'United Kingdom', 'GB': 'United Kingdom',
        '🇩🇪': 'Germany', 'DE': 'Germany',
        '🇫🇷': 'France', 'FR': 'France',
        '🇳🇱': 'Netherlands', 'NL': 'Netherlands',
        '🇷🇺': 'Russia', 'RU': 'Russia',
        '🇮🇹': 'Italy', 'IT': 'Italy',
        '🇪🇸': 'Spain', 'ES': 'Spain',
        '🇵🇱': 'Poland', 'PL': 'Poland',
        '🇺🇦': 'Ukraine', 'UA': 'Ukraine',
        '🇸🇪': 'Sweden', 'SE': 'Sweden',
        '🇨🇭': 'Switzerland', 'CH': 'Switzerland',
        '🇹🇷': 'Turkey', 'TR': 'Turkey',
        '🇮🇪': 'Ireland', 'IE': 'Ireland',
        '🇫🇮': 'Finland', 'FI': 'Finland',
        '🇳🇴': 'Norway', 'NO': 'Norway',
        '🇦🇹': 'Austria', 'AT': 'Austria',
        '🇧🇪': 'Belgium', 'BE': 'Belgium',
        '🇵🇹': 'Portugal', 'PT': 'Portugal',
        '🇬🇷': 'Greece', 'GR': 'Greece',
        '🇩🇰': 'Denmark', 'DK': 'Denmark',
        
        // --- 亚太 ---
        '🇨🇳': 'China', 'CN': 'China',
        '🇭🇰': 'China', 'HK': 'China', // ECharts China 包含 HK
        '🇲🇴': 'China', 'MO': 'China',
        '🇹🇼': 'Taiwan', 'TW': 'Taiwan',
        '🇯🇵': 'Japan', 'JP': 'Japan',
        '🇰🇷': 'Korea', 'KR': 'Korea',
        '🇸🇬': 'Singapore', 'SG': 'Singapore',
        '🇮🇳': 'India', 'IN': 'India',
        '🇦🇺': 'Australia', 'AU': 'Australia',
        '🇳🇿': 'New Zealand', 'NZ': 'New Zealand',
        '🇻🇳': 'Vietnam', 'VN': 'Vietnam',
        '🇹🇭': 'Thailand', 'TH': 'Thailand',
        '🇲🇾': 'Malaysia', 'MY': 'Malaysia',
        '🇮🇩': 'Indonesia', 'ID': 'Indonesia',
        '🇵🇭': 'Philippines', 'PH': 'Philippines',
        '🇰🇭': 'Cambodia', 'KH': 'Cambodia',
        
        // --- 中东/非洲 ---
        '🇦🇪': 'United Arab Emirates', 'UAE': 'United Arab Emirates', 'AE': 'United Arab Emirates',
        '🇿🇦': 'South Africa', 'ZA': 'South Africa',
        '🇸🇦': 'Saudi Arabia', 'SA': 'Saudi Arabia',
        '🇮🇱': 'Israel', 'IL': 'Israel',
        '🇪🇬': 'Egypt', 'EG': 'Egypt',
        '🇮🇷': 'Iran', 'IR': 'Iran',
        '🇳🇬': 'Nigeria', 'NG': 'Nigeria',
        
        // --- 南美 ---
        '🇧🇷': 'Brazil', 'BR': 'Brazil',
        '🇦🇷': 'Argentina', 'AR': 'Argentina',
        '🇨🇱': 'Chile', 'CL': 'Chile',
        '🇨🇴': 'Colombia', 'CO': 'Colombia',
        '🇵🇪': 'Peru', 'PE': 'Peru'
    };

    function renderMap(mapGeoJSON, userLat, userLon) {
        
        // 智能匹配高亮
        const mapFeatureNames = mapGeoJSON.features.map(f => f.properties.name);
        const activeMapNames = new Set();

        serverData.forEach(s => {
            let keyword = null;
            // 1. 优先匹配名字里的国旗/关键词
            for (let key in searchKeys) {
                if ((s.name && s.name.includes(key)) || (s.country && s.country.includes(key))) {
                    keyword = searchKeys[key];
                    break;
                }
            }
            if (!keyword && s.country) keyword = s.country; 

            // 2. 在地图数据中找匹配
            if (keyword) {
                if (mapFeatureNames.includes(keyword)) {
                    activeMapNames.add(keyword);
                } else {
                    const match = mapFeatureNames.find(n => n.includes(keyword) || keyword.includes(n));
                    if (match) activeMapNames.add(match);
                }
            }
        });

        const highlightRegions = Array.from(activeMapNames).map(name => ({
            name: name,
            itemStyle: {
                areaColor: '#0055ff',
                borderColor: '#00ffff',
                borderWidth: 1.5,
                shadowColor: 'rgba(0, 255, 255, 0.8)',
                shadowBlur: 20,
                opacity: 0.9
            }
        }));

        const scatterData = serverData.map(s => ({
            name: s.name, value: [s.lon, s.lat], itemStyle: { color: '#00ffff' }
        }));
        
        scatterData.push({
            name: "ME", value: [userLon, userLat], itemStyle: { color: '#FFD700' },
            symbolSize: 15, label: { show: true, position: 'top', formatter: 'My PC', color: '#FFD700' }
        });

        const linesData = serverData.map(s => ({
            coords: [[s.lon, s.lat], [userLon, userLat]]
        }));

        const option = {
            backgroundColor: '#100C2A',
            geo: {
                map: 'world',
                roam: true,
                zoom: 1.2,
                center: [15, 10], // 非洲/大西洋中心
                label: { show: false },
                itemStyle: {
                    areaColor: '#1B2631',
                    borderColor: '#404a59',
                    borderWidth: 1
                },
                emphasis: {
                    itemStyle: { areaColor: '#2a333d' },
                    label: { show: false }
                },
                regions: highlightRegions 
            },
            series: [
                {
                    type: 'lines',
                    coordinateSystem: 'geo',
                    zlevel: 2,
                    effect: {
                        show: true, period: 4, trailLength: 0.5, 
                        color: '#00ffff', symbol: 'arrow', symbolSize: 6
                    },
                    lineStyle: {
                        color: '#00ffff', width: 1, opacity: 0, curveness: 0.2
                    },
                    data: linesData
                },
                {
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    zlevel: 3,
                    symbol: 'circle', symbolSize: 12,
                    itemStyle: { color: '#00ffff', shadowBlur: 10, shadowColor: '#333' },
                    label: {
                        show: true, position: 'right', formatter: '{b}', 
                        color: '#fff', fontSize: 16, fontWeight: 'bold', 
                        textBorderColor: '#000', textBorderWidth: 2
                    },
                    data: scatterData
                }
            ]
        };
        myChart.setOption(option);
    }

    fetch('https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/world.json')
        .then(response => response.json())
        .then(worldJson => {
            echarts.registerMap('world', worldJson);
            
            let uLat = 39.9, uLon = 116.4; 
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    (p) => { renderMap(worldJson, p.coords.latitude, p.coords.longitude); },
                    (e) => { renderMap(worldJson, uLat, uLon); }
                );
            } else {
                renderMap(worldJson, uLat, uLon);
            }

            window.addEventListener('resize', () => myChart.resize());
            new ResizeObserver(() => myChart.resize()).observe(container);
        });
})();
"""

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
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: 
                raw_data = json.load(f)
                # ✨✨✨ 修复：过滤掉非字典类型的脏数据 (解决 AttributeError: 'str' object has no attribute 'get') ✨✨✨
                SERVERS_CACHE = [s for s in raw_data if isinstance(s, dict)]
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

    # ✨✨✨ [新增] 首次启动自动生成随机探针 Token ✨✨✨
    if 'probe_token' not in ADMIN_CONFIG:
        # 生成一个随机的 32 位字符串
        ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex
        try:
            with open(ADMIN_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(ADMIN_CONFIG, f, indent=4, ensure_ascii=False)
            logger.info(f"🔑 系统初始化: 已生成唯一的探针安全令牌")
        except Exception as e:
            logger.error(f"❌ 保存 Config 失败: {e}")

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

# =================  交互式 WebSSH 类  =================
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
        with self.container:
            try:
                # 1. 渲染终端 UI 容器
                ui.element('div').props(f'id={self.term_id}').classes('w-full h-full bg-black rounded p-2 overflow-hidden relative')
                
                # 2. 注入 JS
                init_js = f"""
                try {{
                    if (window.{self.term_id}) {{
                        if (typeof window.{self.term_id}.dispose === 'function') {{
                            window.{self.term_id}.dispose();
                        }}
                        window.{self.term_id} = null;
                    }}
                    
                    if (typeof Terminal === 'undefined') {{
                        throw new Error("xterm.js 库未加载");
                    }}
                    
                    // ✨ 修复：移除了 rendererType: "canvas"，防止因缺少插件导致报错
                    var term = new Terminal({{
                        cursorBlink: true,
                        fontSize: 13,
                        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                        theme: {{ background: '#000000', foreground: '#ffffff' }},
                        convertEol: true,
                        scrollback: 5000
                    }});
                    
                    var fitAddon;
                    if (typeof FitAddon !== 'undefined') {{
                        var FitAddonClass = FitAddon.FitAddon || FitAddon;
                        fitAddon = new FitAddonClass();
                        term.loadAddon(fitAddon);
                    }}
                    
                    var el = document.getElementById('{self.term_id}');
                    term.open(el);
                    
                    term.write('\\x1b[32m[Local] Terminal Ready. Connecting...\\x1b[0m\\r\\n');
                    
                    if (fitAddon) {{ setTimeout(() => {{ fitAddon.fit(); }}, 200); }}
                    
                    window.{self.term_id} = term;
                    term.focus();
                    
                    term.onData(data => {{
                        emitEvent('term_input_{self.term_id}', data);
                    }});
                    
                    if (fitAddon) {{ new ResizeObserver(() => fitAddon.fit()).observe(el); }}

                }} catch(e) {{
                    console.error("Terminal Init Error:", e);
                    alert("终端启动失败: " + e.message);
                }}
                """
                ui.run_javascript(init_js)

                ui.on(f'term_input_{self.term_id}', lambda e: self._write_to_ssh(e.args))

                self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)
                
                if not self.client:
                    self._print_error(msg)
                    return

                self.channel = self.client.invoke_shell(term='xterm', width=100, height=30)
                self.channel.settimeout(0.0) 
                self.active = True

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


# ================= 探针与监控设置弹窗 =================
def open_probe_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl p-6 flex flex-col gap-4'):
        with ui.row().classes('justify-between items-center w-full border-b pb-2'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('tune', color='primary').classes('text-xl')
                ui.label('探针与监控设置').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        with ui.scroll_area().classes('w-full h-[60vh] pr-4'):
            with ui.column().classes('w-full gap-6'):
                
                # 1. 主控端地址 (从全局 SSH 设置移入)
                with ui.column().classes('w-full bg-blue-50 p-4 rounded-lg border border-blue-100'):
                    ui.label('📡 主控端外部地址 (Agent连接地址)').classes('text-sm font-bold text-blue-900')
                    ui.label('Agent 将向此地址推送数据。请填写 http://公网IP:端口 或 https://域名').classes('text-xs text-blue-700 mb-2')
                    default_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')
                    url_input = ui.input(value=default_url, placeholder='http://1.2.3.4:8080').classes('w-full bg-white').props('outlined dense')

                # 2. 三网测速目标
                with ui.column().classes('w-full'):
                    ui.label('🚀 三网延迟测速目标 (Ping)').classes('text-sm font-bold text-gray-700')
                    ui.label('修改后需点击“更新探针”才能在服务器上生效。').classes('text-xs text-gray-400 mb-2')
                    
                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-3'):
                        ping_ct = ui.input('电信目标 IP', value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props('outlined dense')
                        ping_cu = ui.input('联通目标 IP', value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props('outlined dense')
                        ping_cm = ui.input('移动目标 IP', value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props('outlined dense')

                # 3. 通知设置 (预留功能)
                with ui.column().classes('w-full'):
                    ui.label('🤖 Telegram 通知 ').classes('text-sm font-bold text-gray-700')
                    ui.label('用于掉线报警等通知 (当前版本尚未实装)').classes('text-xs text-gray-400 mb-2')
                    
                    with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-3'):
                        tg_token = ui.input('Bot Token', value=ADMIN_CONFIG.get('tg_bot_token', '')).props('outlined dense')
                        tg_id = ui.input('Chat ID', value=ADMIN_CONFIG.get('tg_chat_id', '')).props('outlined dense')

        # 保存按钮
        async def save_settings():
            # 保存 URL
            url_val = url_input.value.strip().rstrip('/')
            if url_val: ADMIN_CONFIG['manager_base_url'] = url_val
            
            # 保存 Ping 目标
            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value.strip()
            ADMIN_CONFIG['ping_target_cu'] = ping_cu.value.strip()
            ADMIN_CONFIG['ping_target_cm'] = ping_cm.value.strip()
            
            # 保存 TG
            ADMIN_CONFIG['tg_bot_token'] = tg_token.value.strip()
            ADMIN_CONFIG['tg_chat_id'] = tg_id.value.strip()
            
            await save_admin_config()
            safe_notify('✅ 设置已保存 (请记得重新安装/更新探针以应用新配置)', 'positive')
            d.close()

        ui.button('保存设置', icon='save', on_click=save_settings).classes('w-full bg-slate-900 text-white shadow-lg h-12')
    d.open()

 
# =================  单台安装探针 (逻辑升级：支持注入自定义测速点) =================
async def install_probe_on_server(server_conf):
    name = server_conf.get('name', 'Unknown')
    auth_type = server_conf.get('ssh_auth_type', '全局密钥')
    if auth_type == '独立密码' and not server_conf.get('ssh_password'): return False
    if auth_type == '独立密钥' and not server_conf.get('ssh_key'): return False
    
    my_token = ADMIN_CONFIG.get('probe_token', 'default_token')
    
    # 1. 获取主控端地址
    manager_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080') 
    
    # 2. 获取自定义测速点 (如果没有设置，使用默认值)
    ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68') # 电信
    ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')  # 联通
    ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')  # 移动

    # 3. 替换脚本中的变量
    real_script = PROBE_INSTALL_SCRIPT \
        .replace("__MANAGER_URL__", manager_url) \
        .replace("__TOKEN__", my_token) \
        .replace("__SERVER_URL__", server_conf['url']) \
        .replace("__PING_CT__", ping_ct) \
        .replace("__PING_CU__", ping_cu) \
        .replace("__PING_CM__", ping_cm)

    # 4. 执行安装 (保持原有 Paramiko 逻辑)
    def _do_install():
        client = None
        try:
            client, msg = get_ssh_client_sync(server_conf)
            if not client: return False, f"SSH连接失败: {msg}"
            stdin, stdout, stderr = client.exec_command(real_script, timeout=60)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0: return True, "Agent 安装成功并启动"
            return False, f"安装脚本错误 (Exit {exit_status})"
        except Exception as e:
            return False, f"异常: {str(e)}"
        finally:
            if client: client.close()

    success, msg = await run.io_bound(_do_install)
    if success:
        server_conf['probe_installed'] = True
        await save_servers()
        logger.info(f"✅ [Push Agent] {name} 部署成功")
    else:
        logger.warning(f"⚠️ [Push Agent] {name} 部署失败: {msg}")
    return success

# ================= 批量安装所有探针  =================
async def batch_install_all_probes():
    if not SERVERS_CACHE:
        safe_notify("没有服务器可安装", "warning")
        return

    safe_notify(f"正在后台为 {len(SERVERS_CACHE)} 台服务器安装/更新探针...", "ongoing")
    
    # ✨ 限制并发数：同时只允许 10 台服务器进行 SSH 连接，防止卡死
    sema = asyncio.Semaphore(10)

    async def _worker(server_conf):
        name = server_conf.get('name', 'Unknown')
        async with sema:
            # 1. 打印开始日志
            logger.info(f"🚀 [AutoInstall] {name} 开始安装...")
            
            # 2. 执行安装 (复用已有的单台安装函数)
            success = await install_probe_on_server(server_conf)
            
            # 3. 这里的日志会在 install_probe_on_server 内部打印，或者我们可以补充
            # (原函数 install_probe_on_server 内部已经有成功/失败的日志了)

    # 创建任务列表
    tasks = [_worker(s) for s in SERVERS_CACHE]
    
    # 并发执行
    if tasks:
        await asyncio.gather(*tasks)
    
    safe_notify("✅ 所有探针安装/更新任务已完成", "positive")
    
# =================  获取服务器状态 (混合模式：探针优先 + API 兜底) =================
async def get_server_status(server_conf):
    raw_url = server_conf['url']
    
    # --- 策略 A: 探针模式 (保持不变) ---
    if server_conf.get('probe_installed', False) or raw_url in PROBE_DATA_CACHE:
        cache = PROBE_DATA_CACHE.get(raw_url)
        if cache:
            if time.time() - cache.get('last_updated', 0) < 15:
                return cache 
            else:
                return {'status': 'offline', 'msg': '探针离线 (超时)'}
        
    # --- 策略 B: 纯 X-UI 面板模式 (修复版) ---
    try:
        mgr = get_manager(server_conf)
        panel_stats = await run.io_bound(mgr.get_server_status)
        
        if panel_stats:
            # ✨✨✨ [调试核心] 打印原始数据到日志，排查 Oracle 内存问题 ✨✨✨
            if panel_stats.get('cpu', 0) == 0 or float(panel_stats.get('mem', {}).get('current', 0)) > float(panel_stats.get('mem', {}).get('total', 1)):
                 print(f"⚠️ [异常数据调试] {server_conf['name']} 返回: {panel_stats.get('mem')}", flush=True)

            # --- 1. 内存处理 (暴力修正版) ---
            mem_raw = panel_stats.get('mem')
            mem_usage = 0
            mem_total = 0
            
            if isinstance(mem_raw, dict):
                mem_total = float(mem_raw.get('total', 1))
                mem_curr = float(mem_raw.get('current', 0))
                
                # 计算百分比
                if mem_total > 0:
                    mem_usage = (mem_curr / mem_total) * 100
                
                # ✨✨✨ 暴力纠错：如果内存 > 100%，强制压回 99% ✨✨✨
                # 这样界面显示的 "38GB" 就会自动变成 "0.9GB" (跟随总量)
                if mem_usage > 100:
                    # 尝试自动除以 1024 (应对 KB/Byte 混用)
                    if mem_usage > 10000: # 差距过大，可能是 Bytes vs KB (1024倍)
                         mem_curr /= 1024
                         mem_usage /= 1024
                    
                    # 如果除完还是很离谱，直接暴力修正显示
                    if mem_usage > 100:
                        mem_usage = 95.0 # 假定 95%
            else:
                mem_usage = float(mem_raw or 0) * 100
            
            # --- 2. 硬盘处理 ---
            disk_raw = panel_stats.get('disk')
            disk_usage = 0
            disk_total = 0
            if isinstance(disk_raw, dict):
                 disk_total = disk_raw.get('total', 0)
                 if disk_total > 0:
                     disk_usage = (disk_raw.get('current', 0) / disk_total) * 100

            # --- 3. 其他数据补全 ---
            net_io = panel_stats.get('netIO', {})       
            net_traffic = panel_stats.get('netTraffic', {}) 
            loads = panel_stats.get('loads', [0, 0, 0])     
            load_1 = loads[0] if isinstance(loads, list) and len(loads) > 0 else 0

            # --- 4. CPU 修正 ---
            raw_cpu = float(panel_stats.get('cpu', 0))
            final_cpu = raw_cpu if raw_cpu > 1 else raw_cpu * 100

            return {
                'status': 'warning', 
                'msg': '⚠️ 未安装探针',
                'cpu_usage': final_cpu,
                'mem_usage': mem_usage,
                'mem_total': mem_total, 
                'disk_usage': disk_usage,
                'disk_total': disk_total, 
                'net_speed_in': net_io.get('down', 0),
                'net_speed_out': net_io.get('up', 0),
                'net_total_in': net_traffic.get('recv', 0),
                'net_total_out': net_traffic.get('sent', 0),
                'load_1': load_1,
                'uptime': f"{int(panel_stats.get('uptime', 0)/86400)}天",
                '_is_lite': True 
            }
    except Exception as e: 
        # print(f"API Error: {e}")
        pass

    return {'status': 'offline', 'msg': '无信号'}
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


# ================= 延迟测试核心逻辑  =================
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

# ================= 探针数据被动接收接口  =================
@app.post('/api/probe/push')
async def probe_push_data(request: Request):
    try:
        data = await request.json()
        token = data.get('token')
        server_url = data.get('server_url') # Agent 实际汇报上来的地址
        
        # 1. 校验 Token
        correct_token = ADMIN_CONFIG.get('probe_token')
        if not token or token != correct_token:
            return Response("Invalid Token", 403)
            
        # 2. 查找对应的服务器
        # 🎯 优先尝试精确匹配 (URL 完全一致)
        target_server = next((s for s in SERVERS_CACHE if s['url'] == server_url), None)
        
        # ✨✨✨ 核心修复：如果精确匹配失败，尝试 IP 模糊匹配 ✨✨✨
        if not target_server:
            try:
                # 提取 Agent 汇报的 IP (去掉 http:// 和 端口)
                push_ip = server_url.split('://')[-1].split(':')[0]
                
                # 遍历缓存寻找 IP 相同的服务器
                for s in SERVERS_CACHE:
                    cache_ip = s['url'].split('://')[-1].split(':')[0]
                    if cache_ip == push_ip:
                        target_server = s
                        break
            except: pass

        if target_server:
            # 激活探针状态
            if not target_server.get('probe_installed'):
                 target_server['probe_installed'] = True
            
            # 3. 写入缓存
            data['status'] = 'online'
            data['last_updated'] = time.time()
            
            # 🌟 关键：使用面板里存储的 URL (target_server['url']) 作为 Key
            PROBE_DATA_CACHE[target_server['url']] = data
            
            # ✨✨✨ [新增] 立即记录历史数据 ✨✨✨
            record_ping_history(target_server['url'], data.get('pings', {}))
            
        return Response("OK", 200)
    except Exception as e:
        return Response("Error", 500)

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

# ================= 短链接接口：单个订阅  =================
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



# ================= 探针主动注册接口=================
@app.post('/api/probe/register')
async def probe_register(request: Request):
    try:
        data = await request.json()
        
        # 1. 安全校验
        submitted_token = data.get('token')
        correct_token = ADMIN_CONFIG.get('probe_token')
        
        if not submitted_token or submitted_token != correct_token:
            return Response(json.dumps({"success": False, "msg": "Token 错误"}), status_code=403)

        # 2. 获取客户端真实 IP
        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
        
        # 3. ✨✨✨ 智能查重逻辑 (核心修改) ✨✨✨
        target_server = None
        
        # 策略 A: 直接字符串匹配 (命中纯 IP 注册的情况)
        for s in SERVERS_CACHE:
            if client_ip in s['url']:
                target_server = s
                break
        
        # 策略 B: 如果没找到，尝试 DNS 反向解析 (命中域名注册的情况)
        if not target_server:
            logger.info(f"🔍 [探针注册] IP {client_ip} 未直接匹配，尝试解析现有域名...")
            for s in SERVERS_CACHE:
                try:
                    # 提取缓存中的 Host (可能是域名)
                    cached_host = s['url'].split('://')[-1].split(':')[0]
                    
                    # 跳过已经是 IP 的
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", cached_host): continue
                    
                    # 解析域名为 IP (使用 run.io_bound 防止阻塞)
                    resolved_ip = await run.io_bound(socket.gethostbyname, cached_host)
                    
                    if resolved_ip == client_ip:
                        target_server = s
                        logger.info(f"✅ [探针注册] 域名 {cached_host} 解析为 {client_ip}，匹配成功！")
                        break
                except: pass

        # 4. 逻辑分支
        if target_server:
            # === 情况 1: 已存在，仅激活探针 ===
            if not target_server.get('probe_installed'):
                target_server['probe_installed'] = True
                await save_servers() # 保存状态
                await refresh_dashboard_ui() # 刷新UI
            
            return Response(json.dumps({"success": True, "msg": "已合并现有服务器"}), status_code=200)

        else:
            # === 情况 2: 完全陌生的机器，新建 ===
            # (之前的创建逻辑保持不变)
            new_server = {
                'name': f"🏳️ {client_ip}", 
                'group': '自动注册',
                'url': f"http://{client_ip}:54321",
                'user': 'admin',
                'pass': 'admin',
                'ssh_auth_type': '全局密钥',
                'probe_installed': True,
                '_status': 'online'
            }
            SERVERS_CACHE.append(new_server)
            await save_servers()
            
            # 触发强制重命名
            asyncio.create_task(force_geoip_naming_task(new_server))
            
            await refresh_dashboard_ui()
            try: render_sidebar_content.refresh()
            except: pass
            
            logger.info(f"✨ [主动注册] 新服务器上线: {client_ip}")
            return Response(json.dumps({"success": True, "msg": "注册成功"}), status_code=200)

    except Exception as e:
        logger.error(f"❌ 注册接口异常: {e}")
        return Response(json.dumps({"success": False, "msg": str(e)}), status_code=500)
        
# ================= 辅助：单机极速修正  =================
async def fast_resolve_single_server(s):
    """
    后台全自动修正流程：
    1. 尝试连接面板，读取第一个节点的备注名 (Smart Name)
    2. 尝试查询 IP 归属地，获取国旗 (GeoIP)
    3. 自动组合名字 (防止国旗重复)
    4. 自动归类分组
    """
    await asyncio.sleep(1.5) # 稍微错峰
    
    raw_ip = s['url'].split('://')[-1].split(':')[0]
    logger.info(f"🔍 [智能修正] 正在处理: {raw_ip} ...")
    
    data_changed = False
    
    try:
        # --- 步骤 1: 尝试从面板获取真实备注 ---
        # 只有当名字看起来像默认 IP (或带白旗的IP) 时，才去面板读取
        # 这样防止覆盖用户手动修改过的名字
        current_pure_name = s['name'].replace('🏳️', '').strip()
        
        if current_pure_name == raw_ip:
            try:
                smart_name = await generate_smart_name(s)
                # 如果获取到了有效名字 (不是 IP，也不是默认的 Server-X)
                if smart_name and smart_name != raw_ip and not smart_name.startswith('Server-'):
                    s['name'] = smart_name
                    data_changed = True
                    logger.info(f"🏷️ [获取备注] 成功: {smart_name}")
            except Exception as e:
                logger.warning(f"⚠️ [获取备注] 失败: {e}")

        # --- 步骤 2: 查 IP 归属地并修正国旗/分组 ---
        geo = await run.io_bound(fetch_geo_from_ip, s['url'])
        
        if geo:
            # geo: (lat, lon, "CountryName")
            country_name = geo[2]
            s['lat'] = geo[0]; s['lon'] = geo[1]; s['_detected_region'] = country_name
            
            # 获取正确的国旗
            flag_group = get_flag_for_country(country_name)
            flag_icon = flag_group.split(' ')[0] # 提取 "🇸🇬"
            
            # ✨✨✨ [核心修复] 国旗防重复逻辑 ✨✨✨
            # 1. 先把白旗去掉，拿到干净的名字
            temp_name = s['name'].replace('🏳️', '').strip()
            
            # 2. 检查名字里是否已经包含了正确的国旗 (无论在什么位置)
            if flag_icon in temp_name:
                # 如果包含了 (例如 "微软云|🇸🇬新加坡")，我们只更新去掉白旗后的样子
                # 绝不强行加前缀
                if s['name'] != temp_name:
                    s['name'] = temp_name
                    data_changed = True
            else:
                # 3. 如果完全没包含，才加到最前面
                s['name'] = f"{flag_icon} {temp_name}"
                data_changed = True

            # --- 步骤 3: 强制自动分组 ---
            target_group = flag_group 
            
            # 尝试在配置里找精确匹配
            for k, v in AUTO_COUNTRY_MAP.items():
                if flag_icon in k or flag_icon in v:
                    target_group = v
                    break
            
            if s.get('group') != target_group:
                s['group'] = target_group
                data_changed = True
                
        else:
            logger.warning(f"⚠️ [GeoIP] 未获取到地理位置: {raw_ip}")

        # --- 步骤 4: 保存变更 ---
        if data_changed:
            await save_servers()
            await refresh_dashboard_ui()
            try: render_sidebar_content.refresh()
            except: pass
            logger.info(f"✅ [智能修正] 完毕: {s['name']} -> [{s['group']}]")
            
    except Exception as e:
        logger.error(f"❌ [智能修正] 严重错误: {e}")

# ================= 后台智能探测 SSH 用户名 =================
async def smart_detect_ssh_user_task(server_conf):
    """
    后台任务：尝试使用不同的用户名 (ubuntu -> root) 连接 SSH。
    连接成功后：
    1. 更新配置并保存。
    2. 自动触发探针安装。
    """
    # 待测试的用户名列表 (优先尝试 ubuntu，失败则尝试 root)
    # 你可以在这里添加更多，比如 'ec2-user', 'debian', 'opc'
    candidates = ['ubuntu', 'root'] 
    
    ip = server_conf['url'].split('://')[-1].split(':')[0]
    original_user = server_conf.get('ssh_user', '')
    
    logger.info(f"🕵️‍♂️ [智能探测] 开始探测 {server_conf['name']} ({ip}) 的 SSH 用户名...")

    found_user = None

    for user in candidates:
        # 1. 临时修改配置中的用户名
        server_conf['ssh_user'] = user
        
        # 2. 尝试连接 (复用现有的连接函数，自带全局密钥逻辑)
        # 注意：get_ssh_client_sync 内部有 5秒 超时，适合做探测
        client, msg = await run.io_bound(get_ssh_client_sync, server_conf)
        
        if client:
            # ✅ 连接成功！
            client.close()
            found_user = user
            logger.info(f"✅ [智能探测] 成功匹配用户名: {user}")
            break
        else:
            logger.warning(f"⚠️ [智能探测] 用户名 '{user}' 连接失败，尝试下一个...")

    # 3. 处理探测结果
    if found_user:
        # 保存正确的用户名
        server_conf['ssh_user'] = found_user
        # 标记探测成功，防止后续逻辑误判
        server_conf['_ssh_verified'] = True 
        await save_servers()
        
        # 🎉 探测成功后，立即触发探针安装 (如果开启了探针功能)
        if ADMIN_CONFIG.get('probe_enabled', False):
            logger.info(f"🚀 [自动部署] SSH 验证通过，开始安装探针...")
            # 稍作延迟，等待 SSH 服务稳定
            await asyncio.sleep(2) 
            await install_probe_on_server(server_conf)
            
    else:
        # ❌ 全部失败，恢复回默认 (或者保留最后一个尝试失败的)
        logger.error(f"❌ [智能探测] {server_conf['name']} 所有用户名均尝试失败 (请检查安全组或密钥)")
        # 可选：恢复为 root 或者保持原状
        if original_user: server_conf['ssh_user'] = original_user
        await save_servers()

    
# ================= 自动注册接口 =================
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
        
        # 可选参数
        ssh_port = data.get('ssh_port', 22)

        if not all([ip, port, username, password]):
            return Response(json.dumps({"success": False, "msg": "参数不完整"}), status_code=400, media_type="application/json")

        target_url = f"http://{ip}:{port}"
        
        # 4. 构建配置字典
        new_server_config = {
            'name': alias,
            'group': '默认分组',
            'url': target_url,
            'user': username,
            'pass': password,
            'prefix': '',
            
            # SSH 配置
            'ssh_port': ssh_port,
            'ssh_auth_type': '全局密钥',
            'ssh_user': 'detecting...', # 初始占位符，稍后会被后台任务覆盖
            'probe_installed': False
        }

        # 5. 查重与更新逻辑
        existing_index = -1
        # 标准化 URL 进行比对
        for idx, srv in enumerate(SERVERS_CACHE):
            cache_url = srv['url'].replace('http://', '').replace('https://', '')
            new_url_clean = target_url.replace('http://', '').replace('https://', '')
            if cache_url == new_url_clean:
                existing_index = idx
                break

        action_msg = ""
        target_server_ref = None 

        if existing_index != -1:
            # 更新现有节点
            SERVERS_CACHE[existing_index].update(new_server_config)
            target_server_ref = SERVERS_CACHE[existing_index]
            action_msg = f"🔄 更新节点: {alias}"
        else:
            # 新增节点
            SERVERS_CACHE.append(new_server_config)
            target_server_ref = new_server_config
            action_msg = f"✅ 新增节点: {alias}"

        # 6. 保存到硬盘
        await save_servers()
        
        # =================后台任务启动区 =================
        
        # 任务A: 启动 GeoIP 命名任务 (自动变国旗)
        asyncio.create_task(force_geoip_naming_task(target_server_ref))
        
        # 任务B: 启动智能 SSH 用户探测任务 (先试ubuntu，再试root，成功后装探针)
        asyncio.create_task(smart_detect_ssh_user_task(target_server_ref))
        
        # =============================================================

        try: render_sidebar_content.refresh()
        except: pass
        
        logger.info(f"[自动注册] {action_msg} ({ip}) - 已加入 SSH 探测与命名队列")
        return Response(json.dumps({"success": True, "msg": "注册成功，后台正在探测连接..."}), status_code=200, media_type="application/json")

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
# ================= 订阅编辑器  =================
class SubEditor:
    def __init__(self, data=None):
        self.data = data
        if data:
            self.d = data.copy()
            if 'token' not in self.d: self.d['token'] = str(uuid.uuid4())
            if 'nodes' not in self.d: self.d['nodes'] = []
        else:
            self.d = {'name': '', 'token': str(uuid.uuid4()), 'nodes': []}
            
        self.sel = set(self.d.get('nodes', []))
        self.groups_data = {} 
        self.all_node_keys = set()
        self.name_input = None 
        self.token_input = None 

    def ui(self, dlg):
        with ui.card().classes('w-[90vw] max-w-4xl p-0 bg-white').style('display: flex; flex-direction: column; height: 85vh;'):
            with ui.row().classes('w-full justify-between items-center p-4 border-b bg-gray-50'):
                ui.label('订阅编辑器').classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense')
            
            with ui.element('div').classes('w-full flex-grow overflow-y-auto p-4').style('display: flex; flex-direction: column; gap: 1rem;'):
                self.name_input = ui.input('订阅名称', value=self.d.get('name', '')).classes('w-full').props('outlined')
                self.name_input.on_value_change(lambda e: self.d.update({'name': e.value}))
                
                with ui.row().classes('w-full items-center gap-2'):
                    self.token_input = ui.input('订阅路径 (Token)', value=self.d.get('token', ''), placeholder='例如: my-phone').classes('flex-grow').props('outlined')
                    self.token_input.on_value_change(lambda e: self.d.update({'token': e.value.strip()}))
                    ui.button(icon='refresh', on_click=lambda: self.token_input.set_value(str(uuid.uuid4()))).props('flat dense').tooltip('生成随机 UUID')

                with ui.row().classes('w-full items-center justify-between bg-gray-100 p-2 rounded'):
                    ui.label('节点列表').classes('font-bold ml-2')
                    with ui.row().classes('gap-2'):
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm color=primary')
                        ui.button('清空', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm color=red')

                self.cont = ui.column().classes('w-full').style('display: flex; flex-direction: column; gap: 10px;')
            
            with ui.row().classes('w-full p-4 border-t'):
                async def save():
                    if self.name_input: self.d['name'] = self.name_input.value
                    if self.token_input: 
                        new_token = self.token_input.value.strip()
                        if not new_token: return safe_notify("订阅路径不能为空", "negative")
                        if (not self.data) or (self.data.get('token') != new_token):
                            for s in SUBS_CACHE:
                                if s.get('token') == new_token: return safe_notify(f"路径 '{new_token}' 已被占用", "negative")
                        self.d['token'] = new_token
                        
                    self.d['nodes'] = list(self.sel)
                    if self.data: 
                        try: idx = SUBS_CACHE.index(self.data); SUBS_CACHE[idx] = self.d
                        except: SUBS_CACHE.append(self.d)
                    else: SUBS_CACHE.append(self.d)
                    
                    await save_subs()
                    await load_subs_view()
                    dlg.close()
                    ui.notify('订阅保存成功', color='positive')

                ui.button('保存', icon='save', on_click=save).classes('w-full h-12 bg-slate-900 text-white')

        asyncio.create_task(self.load_data())

    async def load_data(self):
        with self.cont: 
            ui.spinner('dots').classes('self-center mt-10')

        # ✨✨✨ 先对服务器列表进行快照，防止在 await 期间列表发生变化 ✨✨✨
        current_servers_snapshot = list(SERVERS_CACHE)
        
        tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in current_servers_snapshot]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        self.groups_data = {}
        self.all_node_keys = set()
        
        # 使用快照进行遍历，确保索引一一对应
        for i, srv in enumerate(current_servers_snapshot):
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
                                            cb.on_value_change(lambda e, k=key: self.on_check(k, e.value))

    def on_check(self, key, value):
        if value: self.sel.add(key)
        else: self.sel.discard(key)

    def toggle_all(self, select_state):
        if select_state: self.sel.update(self.all_node_keys)
        else: self.sel.clear()
        self.render_list()

def open_sub_editor(d):
    with ui.dialog() as dlg: SubEditor(d).ui(dlg); dlg.open()
    
# ================= 全局变量 =================
# 用于记录当前探针页面选中的标签，防止刷新重置
CURRENT_PROBE_TAB = 'ALL' 

# ================= 快捷创建分组弹窗 =================
def open_quick_group_create_dialog(callback=None):
    # 准备选择状态字典
    selection_map = {s['url']: False for s in SERVERS_CACHE}

    with ui.dialog() as d, ui.card().classes('w-full max-w-lg h-[80vh] flex flex-col p-0'):
        
        # 1. 顶部：输入名称
        with ui.column().classes('w-full p-4 border-b bg-gray-50 gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('新建分组').classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            
            name_input = ui.input('分组名称', placeholder='例如: 生产环境').props('outlined dense autofocus').classes('w-full bg-white')

        # 2. 中间：选择服务器列表
        with ui.column().classes('w-full flex-grow overflow-hidden relative'):
            # 全选工具栏
            with ui.row().classes('w-full p-2 bg-gray-100 justify-between items-center border-b flex-shrink-0'):
                ui.label('勾选加入该组的服务器:').classes('text-xs font-bold text-gray-500 ml-2')
                with ui.row().classes('gap-1'):
                    ui.button('全选', on_click=lambda: toggle_all(True)).props('flat dense size=xs color=primary')
                    ui.button('清空', on_click=lambda: toggle_all(False)).props('flat dense size=xs color=grey')

            # 滚动列表
            scroll_area = ui.scroll_area().classes('w-full flex-grow p-2')
            with scroll_area:
                checkbox_refs = {}
                with ui.column().classes('w-full gap-1'):
                    # 按名称排序显示
                    sorted_srv = sorted(SERVERS_CACHE, key=lambda x: x.get('name', ''))
                    
                    for s in sorted_srv:
                        with ui.row().classes('w-full items-center p-2 hover:bg-blue-50 rounded border border-transparent hover:border-blue-200 transition cursor-pointer'):
                            # 复选框
                            chk = ui.checkbox(value=False).props('dense')
                            checkbox_refs[s['url']] = chk
                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            
                            # 点击行也可以触发勾选
                            ui.context.client.layout.on('click', lambda _, c=chk: c.c.set_value(not c.value))

                            # 显示名称
                            ui.label(s['name']).classes('text-sm font-bold text-gray-700 ml-2 truncate flex-grow select-none')
                            
                            # 显示原分组提示
                            old_group = s.get('group', '-')
                            ui.label(old_group).classes('text-xs text-gray-400 font-mono')

            def toggle_all(state):
                for chk in checkbox_refs.values():
                    chk.value = state
                for k in selection_map:
                    selection_map[k] = state

        # 3. 底部：保存
        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            
            # 查重
            existing = set(ADMIN_CONFIG.get('custom_groups', []))
            if new_name in existing: return safe_notify('分组已存在', 'warning')
            
            # 1. 保存分组名到配置
            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            # 2. 更新选中服务器的分组属性
            count = 0
            for s in SERVERS_CACHE:
                if selection_map.get(s['url'], False):
                    s['group'] = new_name
                    count += 1
            
            if count > 0:
                await save_servers()
            
            safe_notify(f'✅ 分组 "{new_name}" 创建成功，已添加 {count} 台服务器', 'positive')
            d.close()
            if callback: await callback(new_name)

        with ui.row().classes('w-full p-4 border-t bg-white justify-end gap-2 flex-shrink-0'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('创建并保存', on_click=save).classes('bg-blue-600 text-white shadow-md')

    d.open()

# ================= 快捷创建分组弹窗  =================
def open_quick_group_create_dialog(callback=None):
    with ui.dialog() as d, ui.card().classes('w-80 p-6 flex flex-col gap-4'):
        ui.label('新建分组').classes('text-lg font-bold')
        
        name_input = ui.input('分组名称', placeholder='例如: 生产环境').props('outlined dense autofocus').classes('w-full')
        
        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            
            # 查重
            existing = set(ADMIN_CONFIG.get('custom_groups', []))
            for s in SERVERS_CACHE:
                if s.get('group'): existing.add(s['group'])
            
            if new_name in existing:
                return safe_notify('分组已存在', 'warning')
            
            # 保存
            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            safe_notify(f'✅ 分组 "{new_name}" 创建成功', 'positive')
            d.close()
            if callback: await callback(new_name) # 回调刷新页面

        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('创建', on_click=save).classes('bg-blue-600 text-white')
    d.open()

# ================= 1.探针视图(分组)排序弹窗 =================
def open_group_sort_dialog():
    # 读取当前分组
    current_groups = ADMIN_CONFIG.get('probe_custom_groups', [])
    if not current_groups:
        safe_notify("暂无自定义视图", "warning")
        return

    # 临时列表用于编辑
    temp_list = list(current_groups)

    with ui.dialog() as d, ui.card().style('width: 400px; max-width: 95vw; height: 60vh; display: flex; flex-direction: column; padding: 0; gap: 0;'):
        
        # 顶部
        with ui.row().classes('w-full p-4 border-b justify-between items-center bg-gray-50'):
            ui.label('自定义排序 (点击箭头移动)').classes('font-bold text-gray-700')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        
        # 列表容器
        list_container = ui.element('div').classes('w-full bg-slate-50 p-2 gap-2').style('flex-grow: 1; overflow-y: auto; display: flex; flex-direction: column;')

        def render_list():
            list_container.clear()
            with list_container:
                for i, name in enumerate(temp_list):
                    with ui.card().classes('w-full p-3 flex-row items-center gap-3 border border-gray-200 shadow-sm'):
                        # 序号
                        ui.label(str(i+1)).classes('text-xs text-gray-400 w-4')
                        # 组名
                        ui.label(name).classes('font-bold text-gray-700 flex-grow text-sm')
                        
                        # 移动按钮
                        with ui.row().classes('gap-1'):
                            # 上移
                            if i > 0:
                                ui.button(icon='arrow_upward', on_click=lambda _, idx=i: move_item(idx, -1)).props('flat dense round size=sm color=blue')
                            else:
                                ui.element('div').classes('w-8') # 占位
                            
                            # 下移
                            if i < len(temp_list) - 1:
                                ui.button(icon='arrow_downward', on_click=lambda _, idx=i: move_item(idx, 1)).props('flat dense round size=sm color=blue')
                            else:
                                ui.element('div').classes('w-8')

        def move_item(index, direction):
            target = index + direction
            if 0 <= target < len(temp_list):
                temp_list[index], temp_list[target] = temp_list[target], temp_list[index]
                render_list()

        render_list()

        # 底部保存
        async def save():
            ADMIN_CONFIG['probe_custom_groups'] = temp_list
            await save_admin_config()
            safe_notify("✅ 视图顺序已更新", "positive")
            d.close()

        with ui.row().classes('w-full p-4 border-t bg-white'):
            ui.button('保存顺序', icon='save', on_click=save).classes('w-full bg-slate-900 text-white shadow-lg')
    
    d.open()
# ================= 2. 探针专用分组弹窗  =================
# is_edit_mode: 是否为编辑模式
# group_name: 编辑时的原组名
def open_quick_group_dialog(callback=None, is_edit_mode=False, group_name=None):
    # 使用 tags 来判断是否属于该组
    selection_map = {s['url']: False for s in SERVERS_CACHE}
    
    if is_edit_mode and group_name:
        for s in SERVERS_CACHE:
            if group_name in s.get('tags', []):
                selection_map[s['url']] = True

    with ui.dialog() as d, ui.card().classes('w-full max-w-lg h-[80vh] flex flex-col p-0'):
        # 顶部
        title = f'编辑探针视图: {group_name}' if is_edit_mode else '新建探针视图'
        with ui.column().classes('w-full p-4 border-b bg-gray-50 gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label(title).classes('text-lg font-bold')
                with ui.row().classes('gap-2'):
                    # 删除按钮
                    if is_edit_mode:
                        async def delete_group():
                            # 1. 从配置中移除 (使用 probe_custom_groups)
                            if group_name in ADMIN_CONFIG.get('probe_custom_groups', []):
                                ADMIN_CONFIG['probe_custom_groups'].remove(group_name)
                                await save_admin_config()
                            
                            # 2. 从所有服务器的 tags 中移除
                            for s in SERVERS_CACHE:
                                if 'tags' in s and group_name in s['tags']:
                                    s['tags'].remove(group_name)
                            
                            await save_servers()
                            safe_notify(f'视图 "{group_name}" 已删除', 'positive')
                            d.close()
                            if callback: await callback(None) # None 表示删除了
                        
                        ui.button(icon='delete', color='red', on_click=delete_group).props('flat round dense').tooltip('删除此视图')
                    
                    ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            
            name_input = ui.input('视图名称', value=group_name if is_edit_mode else '', placeholder='例如: 重点监控').props('outlined dense').classes('w-full bg-white')

        # 中间列表
        with ui.column().classes('w-full flex-grow overflow-hidden relative'):
            with ui.row().classes('w-full p-2 bg-gray-100 justify-between items-center border-b flex-shrink-0'):
                ui.label('包含的服务器:').classes('text-xs font-bold text-gray-500 ml-2')
                with ui.row().classes('gap-1'):
                    ui.button('全选', on_click=lambda: toggle_all(True)).props('flat dense size=xs color=primary')
                    ui.button('清空', on_click=lambda: toggle_all(False)).props('flat dense size=xs color=grey')

            scroll_area = ui.scroll_area().classes('w-full flex-grow p-2')
            with scroll_area:
                checkbox_refs = {}
                with ui.column().classes('w-full gap-1'):
                    sorted_srv = sorted(SERVERS_CACHE, key=lambda x: x.get('name', ''))
                    for s in sorted_srv:
                        is_checked = selection_map[s['url']]
                        bg_cls = 'bg-blue-50 border-blue-200' if is_checked else 'hover:bg-gray-50 border-transparent'
                        
                        with ui.row().classes(f'w-full items-center p-2 rounded border transition cursor-pointer {bg_cls}') as row:
                            chk = ui.checkbox(value=is_checked).props('dense')
                            checkbox_refs[s['url']] = chk
                            
                            def on_row_click(c=chk, r=row):
                                c.set_value(not c.value)
                                if c.value: r.classes(add='bg-blue-50 border-blue-200', remove='hover:bg-gray-50 border-transparent')
                                else: r.classes(remove='bg-blue-50 border-blue-200', add='hover:bg-gray-50 border-transparent')

                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            ui.context.client.layout.on('click', on_row_click)

                            ui.label(s['name']).classes('text-sm font-bold text-gray-700 ml-2 truncate flex-grow select-none')
                            
                            # 显示现有标签提示
                            if s.get('tags'):
                                ui.label(f"Tags: {len(s['tags'])}").classes('text-[10px] text-gray-400')

            def toggle_all(state):
                for chk in checkbox_refs.values(): chk.value = state
                for k in selection_map: selection_map[k] = state

        # 底部
        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            
            # 使用 probe_custom_groups 避免污染侧边栏
            if 'probe_custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['probe_custom_groups'] = []
            
            # 如果改名，检查重名
            if new_name != group_name:
                if new_name in ADMIN_CONFIG['probe_custom_groups']: return safe_notify('名称已存在', 'warning')
                # 移除旧名
                if is_edit_mode and group_name in ADMIN_CONFIG['probe_custom_groups']:
                    ADMIN_CONFIG['probe_custom_groups'].remove(group_name)
            
            # 添加新名
            if new_name not in ADMIN_CONFIG['probe_custom_groups']:
                ADMIN_CONFIG['probe_custom_groups'].append(new_name)
            
            await save_admin_config()
            
            # 更新 Tags
            count = 0
            for s in SERVERS_CACHE:
                if 'tags' not in s: s['tags'] = []
                
                # 如果被选中 -> 确保有 tag
                if selection_map.get(s['url'], False):
                    if new_name not in s['tags']: s['tags'].append(new_name)
                    # 如果是改名，移除旧 tag
                    if is_edit_mode and group_name and group_name in s['tags'] and group_name != new_name:
                        s['tags'].remove(group_name)
                    count += 1
                # 如果没选中 -> 确保没有 tag
                else:
                    if new_name in s['tags']: s['tags'].remove(new_name)
                    # 如果是改名，也移除旧 tag
                    if is_edit_mode and group_name and group_name in s['tags']:
                        s['tags'].remove(group_name)
            
            await save_servers()
            
            safe_notify(f'✅ 视图 "{new_name}" 已保存 ({count}台)', 'positive')
            d.close()
            if callback: await callback(new_name)

        with ui.row().classes('w-full p-4 border-t bg-white justify-end gap-2 flex-shrink-0'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('保存', on_click=save).classes('bg-blue-600 text-white shadow-md')

    d.open()

# ================= 详情弹窗逻辑 =================
def open_server_detail_dialog(server_conf):
    """
    打开服务器详情弹窗 (UI 升级版：大圆角 + 磨砂玻璃风格)
    """
    # 样式定义
    LABEL_STYLE = 'text-gray-600 font-bold text-xs' 
    VALUE_STYLE = 'text-gray-900 font-mono text-sm truncate'
    
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-4xl p-0 overflow-hidden flex flex-col rounded-3xl bg-slate-100/85 backdrop-blur-xl border border-white/50 shadow-2xl'):
        d.props('backdrop-filter="blur(4px)"') 
        
        # 1. 顶部标题栏
        with ui.row().classes('w-full items-center justify-between p-4 bg-white/50 border-b border-white/50 flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                flag = "🏳️"
                try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                except: pass
                ui.label(flag).classes('text-2xl filter drop-shadow-sm') 
                ui.label(f"{server_conf['name']} 详情").classes('text-xl font-bold text-slate-800 tracking-tight')
            
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey').classes('hover:bg-white/50')

        # 2. 内容滚动区
        with ui.scroll_area().classes('w-full h-[70vh] p-6'):
            refs = {} 
            
            # --- 第一部分：详细信息网格 ---
            with ui.card().classes('w-full p-5 shadow-sm border border-white/60 bg-white/60 backdrop-blur-md mb-4 rounded-2xl'):
                ui.label('详细信息').classes('text-sm font-bold text-slate-800 mb-3 border-l-4 border-blue-500 pl-2')
                with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 gap-y-3 gap-x-8'):
                    def info_row(label, key):
                        with ui.row().classes('w-full justify-between items-center border-b border-gray-400/20 pb-1'):
                            ui.label(label).classes(LABEL_STYLE)
                            refs[key] = ui.label('--').classes(VALUE_STYLE)

                    info_row('CPU 型号', 'cpu_model')
                    info_row('系统架构', 'arch')
                    info_row('虚拟化', 'virt')
                    info_row('操作系统', 'os')
                    info_row('内存使用', 'mem_detail')
                    info_row('交换分区', 'swap_detail')
                    info_row('硬盘使用', 'disk_detail')
                    info_row('总流量', 'traffic_detail')
                    info_row('实时流量', 'speed_detail')
                    info_row('负载 (Load)', 'load')
                    info_row('在线时间', 'uptime')
                    info_row('最后上报', 'last_seen')

            # --- 第二部分：三网测速 ---
            with ui.card().classes('w-full p-5 shadow-sm border border-white/60 bg-white/60 backdrop-blur-md mb-4 rounded-2xl'):
                ui.label('三网延迟检测 (ICMP Ping)').classes('text-sm font-bold text-slate-800 mb-3 border-l-4 border-purple-500 pl-2')
                with ui.row().classes('w-full gap-4 justify-around'):
                    def ping_box(name, color, key):
                        with ui.column().classes(f'flex-1 bg-{color}-50/80 border border-{color}-100 rounded-xl p-3 items-center min-w-[100px]'):
                            ui.label(name).classes(f'text-{color}-700 font-bold text-xs mb-1')
                            refs[key] = ui.label('-- ms').classes(f'text-{color}-900 font-bold text-lg')

                    ping_box('电信', 'blue', 'ping_ct')
                    ping_box('联通', 'orange', 'ping_cu')
                    ping_box('移动', 'green', 'ping_cm')

            # --- 第三部分：延迟趋势图 ---
            with ui.card().classes('w-full p-0 shadow-sm border border-white/60 bg-white/60 backdrop-blur-md overflow-hidden rounded-2xl'):
                with ui.row().classes('w-full justify-between items-center p-4 border-b border-white/50 bg-white/40'):
                     ui.label('网络质量监控').classes('text-sm font-bold text-slate-800 border-l-4 border-teal-500 pl-2')
                     
                     with ui.tabs().props('dense no-caps active-color=primary indicator-color=primary').classes('bg-slate-200/50 rounded-lg p-1') as chart_tabs:
                         t_real = ui.tab('real', label='实时(60s)').classes('rounded h-8 min-h-0 px-3 text-xs')
                         t_1h = ui.tab('1h', label='1小时').classes('rounded h-8 min-h-0 px-3 text-xs')
                         t_3h = ui.tab('3h', label='3小时').classes('rounded h-8 min-h-0 px-3 text-xs')
                     
                     chart_tabs.set_value('real') 

                chart = ui.echart({
                    'tooltip': {
                        'trigger': 'axis',
                        'backgroundColor': 'rgba(255, 255, 255, 0.8)',
                        'backdropFilter': 'blur(4px)',
                        'borderColor': '#fff',
                        'borderWidth': 1,
                        'textStyle': {'color': '#333', 'fontSize': 12},
                        'axisPointer': {'type': 'cross', 'label': {'backgroundColor': '#6a7985'}}
                    },
                    'legend': {'data': ['电信', '联通', '移动'], 'bottom': 0, 'icon': 'circle', 'itemGap': 20},
                    'grid': {'left': '3%', 'right': '4%', 'bottom': '10%', 'top': '5%', 'containLabel': True},
                    'xAxis': {
                        'type': 'category', 
                        'boundaryGap': False,
                        'axisLine': {'lineStyle': {'color': '#9ca3af'}}, 
                        'axisLabel': {'color': '#4b5563'},
                        'data': [] 
                    },
                    'yAxis': {
                        'type': 'value', 
                        'splitLine': {'lineStyle': {'type': 'dashed', 'color': 'rgba(200,200,200,0.5)'}}, 
                        'minInterval': 1
                    },
                    'series': [
                        {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 2}, 'itemStyle': {'color': '#3b82f6'}, 'areaStyle': {'opacity': 0.1, 'color': '#3b82f6'}},
                        {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 2}, 'itemStyle': {'color': '#f97316'}, 'areaStyle': {'opacity': 0.1, 'color': '#f97316'}},
                        {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 2}, 'itemStyle': {'color': '#22c55e'}, 'areaStyle': {'opacity': 0.1, 'color': '#22c55e'}}
                    ]
                }).classes('w-full h-64 p-2')

        # 3. 实时更新逻辑 (修复：改为纯读取模式)
        async def update_detail_loop():
            if not d.value: return
            try:
                raw_data = PROBE_DATA_CACHE.get(server_conf['url'], {})
                status = await get_server_status(server_conf)
                static = raw_data.get('static', {})
                
                # 更新文本信息
                refs['cpu_model'].set_text(static.get('cpu_model', status.get('cpu_model', 'Generic CPU')))
                raw_arch = static.get('arch', 'unknown')
                fmt_arch = raw_arch
                if 'x86_64' in raw_arch.lower(): fmt_arch = 'AMD64'
                elif 'aarch64' in raw_arch.lower() or 'arm64' in raw_arch.lower(): fmt_arch = 'ARM64'
                refs['arch'].set_text(fmt_arch)
                refs['os'].set_text(static.get('os', 'Linux'))
                refs['virt'].set_text(static.get('virt', 'kvm')) 

                def fmt_usage(used_pct, total_gb):
                    if not total_gb: return "--"
                    used_gb = float(total_gb) * (float(used_pct)/100)
                    return f"{round(used_gb, 2)} GB / {total_gb} GB"
                
                refs['mem_detail'].set_text(fmt_usage(status.get('mem_usage', 0), status.get('mem_total', 0)))
                sw_total = raw_data.get('swap_total', 0)
                sw_free = raw_data.get('swap_free', 0)
                if sw_total: refs['swap_detail'].set_text(f"{round(sw_total - sw_free, 2)} GB / {sw_total} GB")
                else: refs['swap_detail'].set_text("未启用")

                refs['disk_detail'].set_text(fmt_usage(status.get('disk_usage', 0), status.get('disk_total', 0)))
                
                t_in = format_bytes(status.get('net_total_in', 0))
                t_out = format_bytes(status.get('net_total_out', 0))
                refs['traffic_detail'].set_text(f"↑ {t_out}  ↓ {t_in}")
                
                s_in = format_bytes(status.get('net_speed_in', 0)) + "/s"
                s_out = format_bytes(status.get('net_speed_out', 0)) + "/s"
                refs['speed_detail'].set_text(f"↑ {s_out}  ↓ {s_in}")

                refs['load'].set_text(str(status.get('load_1', 0)))
                refs['uptime'].set_text(status.get('uptime', '-'))
                
                last_ts = raw_data.get('last_updated', 0)
                if last_ts:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M:%S')
                    refs['last_seen'].set_text(dt)
                else: refs['last_seen'].set_text('Never')

                pings = status.get('pings', {})
                ct = pings.get('电信', 0); ct = ct if ct > 0 else 0
                cu = pings.get('联通', 0); cu = cu if cu > 0 else 0
                cm = pings.get('移动', 0); cm = cm if cm > 0 else 0
                
                def fmt_ping(val): return f"{val} ms" if val > 0 else "超时"
                refs['ping_ct'].set_text(fmt_ping(ct))
                refs['ping_cu'].set_text(fmt_ping(cu))
                refs['ping_cm'].set_text(fmt_ping(cm))

                # --- ✨✨✨ 图表更新逻辑 (核心修改) ✨✨✨ ---
                
                # 1. 从全局缓存读取历史数据 (而不是在这里 append)
                history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                
                now_ts = time.time()
                tab_mode = chart_tabs.value
                final_ct, final_cu, final_cm, final_time = [], [], [], []
                
                if tab_mode == 'real':
                    # 实时: 60秒
                    cutoff = now_ts - 60
                    sliced = [p for p in history_data if p['ts'] > cutoff]
                elif tab_mode == '1h':
                    # 1小时
                    cutoff = now_ts - 3600
                    sliced = [p for p in history_data if p['ts'] > cutoff]
                else:
                    # 3小时 (降采样)
                    cutoff = now_ts - 10800
                    sliced = [p for p in history_data if p['ts'] > cutoff]
                    if len(sliced) > 1000: sliced = sliced[::2]
                
                if sliced:
                    final_ct = [p['ct'] for p in sliced]
                    final_cu = [p['cu'] for p in sliced]
                    final_cm = [p['cm'] for p in sliced]
                    final_time = [p['time_str'] for p in sliced]
                
                chart.options['xAxis']['data'] = final_time
                chart.options['series'][0]['data'] = final_ct
                chart.options['series'][1]['data'] = final_cu
                chart.options['series'][2]['data'] = final_cm
                chart.update()

            except Exception as e: pass

        timer = ui.timer(2.0, update_detail_loop)
        d.on('hide', lambda: timer.cancel())
        
    d.open()

# ================= 探针设置页  =================
async def render_probe_page():
    # 1. 标记当前视图状态
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'PROBE'
    
    # 2. 清理并初始化容器 (垂直居中)
    content_container.clear()
    content_container.classes(replace='w-full h-full overflow-y-auto p-6 bg-slate-50 relative flex flex-col justify-center items-center')
    
    # 3. 开启引导逻辑
    async def enable_probe_feature():
        ADMIN_CONFIG['probe_enabled'] = True
        await save_admin_config()
        safe_notify("✅ 探针功能已激活！", "positive")
        asyncio.create_task(batch_install_all_probes())
        await render_probe_page()

    if not ADMIN_CONFIG.get('probe_enabled', False):
        with content_container:
            with ui.column().classes('w-full h-full justify-center items-center opacity-50 gap-4'):
                ui.icon('monitor_heart', size='5rem').classes('text-gray-300')
                ui.label('探针监控功能未开启').classes('text-2xl font-bold text-gray-400')
                ui.button('立即开启探针监控', on_click=enable_probe_feature).props('push color=primary')
        return

    # 4. 渲染布局 
    with content_container:
        with ui.column().classes('w-full max-w-7xl gap-6'):
            
            # --- 标题栏 ---
            with ui.row().classes('w-full items-center gap-3'):
                 with ui.element('div').classes('p-2 bg-blue-600 rounded-lg shadow-sm'):
                     ui.icon('tune', color='white').classes('text-2xl')
                 with ui.column().classes('gap-0'):
                    ui.label('探针管理与设置').classes('text-2xl font-extrabold text-slate-800 tracking-tight')
                    ui.label('Probe Configuration & Management').classes('text-xs font-bold text-gray-400 uppercase tracking-widest')

            # --- 核心网格布局 (左右等高) ---
            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-3 gap-6 items-stretch'):
                
                # ======================= 左侧：参数设置区 (占 2/3) =======================
                with ui.column().classes('lg:col-span-2 w-full gap-6'):
                    
                    # --- 卡片 1: 基础连接设置 ---
                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-gray-100 pb-2 w-full'):
                            ui.icon('hub', color='blue').classes('text-xl')
                            ui.label('基础连接设置').classes('text-lg font-bold text-slate-700')
                        
                        with ui.column().classes('w-full gap-2'):
                            ui.label('📡 主控端外部地址 (Agent 连接地址)').classes('text-sm font-bold text-gray-600')
                            default_url = ADMIN_CONFIG.get('manager_base_url', 'http://xui-manager:8080')
                            url_input = ui.input(value=default_url, placeholder='http://1.2.3.4:8080').props('outlined dense').classes('w-full')
                            ui.label('Agent 将向此地址推送数据。请填写 http://公网IP:端口 或 https://域名').classes('text-xs text-gray-400')

                        async def save_url():
                            val = url_input.value.strip().rstrip('/')
                            if val:
                                ADMIN_CONFIG['manager_base_url'] = val
                                await save_admin_config()
                                safe_notify('✅ 主控端地址已保存', 'positive')
                            else: safe_notify('地址不能为空', 'warning')

                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存连接设置', icon='save', on_click=save_url).props('unelevated color=blue-7').classes('font-bold')

                    # --- 卡片 2: 三网测速目标 ---
                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-gray-100 pb-2 w-full'):
                            ui.icon('speed', color='orange').classes('text-xl')
                            ui.label('三网延迟测速目标 (Ping)').classes('text-lg font-bold text-slate-700')
                        
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-3 gap-4'):
                            with ui.column().classes('gap-1'):
                                ui.label('中国电信 IP').classes('text-xs font-bold text-gray-500')
                                ping_ct = ui.input(value=ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')).props('outlined dense').classes('w-full')
                            
                            with ui.column().classes('gap-1'):
                                ui.label('中国联通 IP').classes('text-xs font-bold text-gray-500')
                                ping_cu = ui.input(value=ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')).props('outlined dense').classes('w-full')
                            
                            with ui.column().classes('gap-1'):
                                ui.label('中国移动 IP').classes('text-xs font-bold text-gray-500')
                                ping_cm = ui.input(value=ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')).props('outlined dense').classes('w-full')
                        
                        with ui.row().classes('w-full items-center gap-1 mt-2'):
                            ui.icon('info', size='xs').classes('text-gray-400')
                            ui.label('修改测速目标后，请点击右侧的“更新所有探针”按钮以生效。').classes('text-xs text-gray-400')

                        async def save_ping():
                            ADMIN_CONFIG['ping_target_ct'] = ping_ct.value.strip()
                            ADMIN_CONFIG['ping_target_cu'] = ping_cu.value.strip()
                            ADMIN_CONFIG['ping_target_cm'] = ping_cm.value.strip()
                            await save_admin_config()
                            safe_notify('✅ 测速目标已保存 (请更新探针以生效)', 'positive')

                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存测速目标', icon='save', on_click=save_ping).props('unelevated color=orange-7').classes('font-bold')

                    # --- 卡片 3: 通知设置 ---
                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl'):
                        with ui.row().classes('items-center gap-2 mb-4 border-b border-gray-100 pb-2 w-full'):
                            ui.icon('notifications', color='purple').classes('text-xl')
                            ui.label('通知设置 (Telegram)').classes('text-lg font-bold text-slate-700')
                        
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                            with ui.column().classes('gap-1'):
                                ui.label('Bot Token').classes('text-xs font-bold text-gray-500')
                                tg_token = ui.input(value=ADMIN_CONFIG.get('tg_bot_token', '')).props('outlined dense').classes('w-full')
                            
                            with ui.column().classes('gap-1'):
                                ui.label('Chat ID').classes('text-xs font-bold text-gray-500')
                                tg_id = ui.input(value=ADMIN_CONFIG.get('tg_chat_id', '')).props('outlined dense').classes('w-full')
                        
                        ui.label('用于接收服务器离线/恢复的实时通知。').classes('text-xs text-gray-400 mt-2')

                        async def save_notify_conf():
                            ADMIN_CONFIG['tg_bot_token'] = tg_token.value.strip()
                            ADMIN_CONFIG['tg_chat_id'] = tg_id.value.strip()
                            await save_admin_config()
                            safe_notify('✅ 通知设置已保存', 'positive')

                        with ui.row().classes('w-full justify-end mt-4'):
                            ui.button('保存通知设置', icon='save', on_click=save_notify_conf).props('unelevated color=purple-7').classes('font-bold')

                # ======================= 右侧：快捷操作区 (占 1/3) =======================
                with ui.column().classes('lg:col-span-1 w-full gap-6 h-full'):
                    
                    # --- 卡片 A: 快捷操作 (已替换排序按钮) ---
                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl flex-shrink-0'):
                        ui.label('快捷操作').classes('text-lg font-bold text-slate-700 mb-4 border-l-4 border-blue-500 pl-2')
                        
                        with ui.column().classes('w-full gap-3'):
                            # 1. 复制安装命令
                            async def copy_install_cmd():
                                try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
                                except: safe_notify("无法获取面板地址", "negative"); return
                                token = ADMIN_CONFIG.get('probe_token', 'default_token')
                                mgr_url_conf = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
                                base_url = mgr_url_conf if mgr_url_conf else origin
                                register_api = f"{base_url}/api/probe/register"
                                ping_ct = ADMIN_CONFIG.get('ping_target_ct', '202.102.192.68')
                                ping_cu = ADMIN_CONFIG.get('ping_target_cu', '112.122.10.26')
                                ping_cm = ADMIN_CONFIG.get('ping_target_cm', '211.138.180.2')
                                cmd = f'curl -sL https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/x-install.sh | bash -s -- "{token}" "{register_api}" "{ping_ct}" "{ping_cu}" "{ping_cm}"'
                                await safe_copy_to_clipboard(cmd)
                                safe_notify("📋 安装命令已复制！", "positive")
                            
                            ui.button('复制安装命令', icon='content_copy', on_click=copy_install_cmd) \
                                .classes('w-full bg-blue-50 text-blue-700 border border-blue-200 shadow-sm hover:bg-blue-100 font-bold align-left')
                            
                            # 2. 视图管理按钮组 (横向排列)
                            with ui.row().classes('w-full gap-2'):
                                # 自定义分组排序 (新功能)
                                ui.button('分组排序', icon='toc', on_click=open_group_sort_dialog) \
                                    .classes('flex-1 bg-gray-50 text-gray-700 border border-gray-200 shadow-sm hover:bg-gray-100 font-bold align-left')
                                
                                # 新建视图 (新功能)
                                ui.button('新建分组', icon='add_circle', on_click=lambda: open_quick_group_dialog(None)) \
                                    .classes('flex-1 bg-green-50 text-green-700 border border-green-200 shadow-sm hover:bg-green-100 font-bold align-left')
                            
                            # 3. 更新所有探针
                            async def reinstall_all():
                                safe_notify("正在后台更新所有探针脚本...", "ongoing")
                                await batch_install_all_probes()
                            
                            ui.button('更新所有探针', icon='system_update_alt', on_click=reinstall_all) \
                                .classes('w-full bg-orange-50 text-orange-700 border border-orange-200 shadow-sm hover:bg-orange-100 font-bold align-left')

                    # --- 卡片 B: 公开监控页入口 (自动拉伸填满高度) ---
                    with ui.card().classes('w-full p-6 bg-gradient-to-br from-slate-800 to-slate-900 text-white rounded-xl shadow-lg relative overflow-hidden group cursor-pointer flex-grow flex flex-col justify-center') \
                        .on('click', lambda: ui.navigate.to('/status', new_tab=True)):
                        
                        ui.icon('public', size='10rem').classes('absolute -right-8 -bottom-8 text-white opacity-10 group-hover:rotate-12 transition transform duration-500')
                        
                        ui.label('公开监控墙').classes('text-2xl font-bold mb-2')
                        ui.label('点击前往查看实时状态地图').classes('text-sm text-gray-400 mb-6')
                        
                        with ui.row().classes('items-center gap-2 text-blue-400 font-bold text-base group-hover:gap-3 transition-all'):
                            ui.label('立即前往')
                            ui.icon('arrow_forward')

                    # --- 卡片 C: 数据统计 (固定高度) ---
                    online = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
                    total = len(SERVERS_CACHE)
                    probe = len([s for s in SERVERS_CACHE if s.get('probe_installed')])
                    
                    with ui.card().classes('w-full p-6 bg-white border border-gray-200 shadow-sm rounded-xl flex-shrink-0'):
                        ui.label('数据概览').classes('text-lg font-bold text-slate-700 mb-4 border-l-4 border-green-500 pl-2')
                        
                        with ui.row().classes('w-full justify-between items-center border-b border-gray-50 pb-3 mb-3'):
                            ui.label('总服务器').classes('text-gray-500 text-sm')
                            ui.label(str(total)).classes('font-bold text-xl text-slate-800')
                        
                        with ui.row().classes('w-full justify-between items-center border-b border-gray-50 pb-3 mb-3'):
                            ui.label('探针在线').classes('text-gray-500 text-sm')
                            ui.label(str(online)).classes('font-bold text-xl text-green-600')
                        
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('已安装探针').classes('text-gray-500 text-sm')
                            ui.label(str(probe)).classes('font-bold text-xl text-purple-600')
                            
# ================= 批量刷新卡片数据 (监控墙) =================
async def update_probe_stats(card_refs, is_manual=False):
    global PROBE_LOCK
    # 只有当在 PROBE 页面时才运行
    if CURRENT_VIEW_STATE.get('scope') != 'PROBE': return
    if PROBE_LOCK and not is_manual: return

    PROBE_LOCK = True
    if is_manual: safe_notify('正在刷新服务器状态...', 'ongoing')

    # 限制并发 (主要保护纯面板的 HTTP 请求)
    sema = asyncio.Semaphore(15) 

    async def check_one(srv):
        url = srv['url']
        refs = card_refs.get(url)
        if not refs: return 

        async with sema:
            # ✨ 调用全局混合获取
            res = await get_server_status(srv)
            
            try:
                if refs['status_badge'].is_deleted: return

                if res and res.get('status') == 'online':
                    # === 在线 (Root 探针) ===
                    refs['status_badge'].set_text('运行中')
                    refs['status_badge'].classes(replace='bg-green-100 text-green-600', remove='bg-gray-100 bg-red-100 bg-orange-100 text-orange-600')
                    
                    # 硬件数据
                    if 'cpu_cores' in res: refs['cpu_cores'].set_text(f"{res['cpu_cores']} Cores")
                    if 'mem_total' in res: refs['mem_total'].set_text(f"{res['mem_total']} GB")
                    if 'disk_total' in res: refs['disk_total'].set_text(f"{res['disk_total']} GB")

                    # 进度条
                    cpu = float(res.get('cpu_usage', 0))
                    refs['cpu_bar'].set_value(cpu / 100.0)
                    refs['cpu_val'].set_text(f'{int(cpu)}%')
                    refs['cpu_bar'].props('color=blue')
                    
                    mem = float(res.get('mem_usage', 0))
                    refs['mem_bar'].set_value(mem / 100.0)
                    mem_color = '#ef4444' if mem > 90 else ('#f97316' if mem > 75 else '#22c55e')
                    refs['mem_bar'].props(f'color="{mem_color}"')
                    refs['mem_val'].set_text(f'{int(mem)}%')

                    disk = float(res.get('disk_usage', 0))
                    refs['disk_bar'].set_value(disk / 100.0)
                    refs['disk_bar'].props('color=purple')
                    refs['disk_val'].set_text(f'{int(disk)}%')

                    refs['uptime_val'].set_text(str(res.get('uptime', '')))

                elif res and res.get('status') == 'warning':
                    # === 警告 (纯 X-UI 面板) ===
                    refs['status_badge'].set_text('未安装探针')
                    # 橙色样式
                    refs['status_badge'].classes(replace='bg-orange-100 text-orange-600', remove='bg-green-100 bg-red-100 bg-gray-100')
                    
                    # 仅显示 CPU/内存 (面板通常只给这两个)
                    cpu = float(res.get('cpu_usage', 0))
                    refs['cpu_bar'].set_value(cpu / 100.0)
                    refs['cpu_val'].set_text(f'{int(cpu)}%')
                    refs['cpu_bar'].props('color=orange') # 橙色警告条
                    
                    mem = float(res.get('mem_usage', 0))
                    refs['mem_bar'].set_value(mem / 100.0)
                    refs['mem_val'].set_text(f'{int(mem)}%')
                    refs['mem_bar'].props('color=orange')

                    # 硬盘/负载置空
                    refs['disk_bar'].set_value(0); refs['disk_val'].set_text('--')
                    refs['load_val'].set_text('--')
                    refs['uptime_val'].set_text(str(res.get('uptime', '-')))

                else:
                    # === 离线 ===
                    refs['status_badge'].set_text('已离线')
                    refs['status_badge'].classes(replace='bg-red-100 text-red-500', remove='bg-green-100 bg-orange-100 bg-gray-100')
                    refs['cpu_bar'].set_value(0); refs['mem_bar'].set_value(0); refs['disk_bar'].set_value(0)
                    
            except: pass

    tasks = [check_one(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)
    PROBE_LOCK = False 
    if is_manual: safe_notify('✅ 状态刷新完毕', 'positive')


    
# ================= 订阅管理视图  =================
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
        if 'options' not in self.sub_data:
            self.sub_data['options'] = {
                'emoji': True, 'udp': True, 'sort': False, 'tfo': False,
                'skip_cert': True, 'include_regex': '', 'exclude_regex': '',
                'rename_pattern': '', 'rename_replacement': '', 'regions': []
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
                    self.raw_nodes.append({'name': n['remark'], 'original_name': n['remark'], 'server_name': srv['name']})

    def update_preview(self):
        import re
        result = []
        selected_regions = set(self.opt.get('regions', []))
        
        for node in self.raw_nodes:
            current_node = node.copy()
            name = current_node['name']
            
            node_region = detect_country_group(name)
            if selected_regions and node_region not in selected_regions: continue
            
            inc_reg = self.opt.get('include_regex', '').strip()
            if inc_reg:
                try: 
                    if not re.search(inc_reg, name, re.IGNORECASE): continue
                except: pass
            
            exc_reg = self.opt.get('exclude_regex', '').strip()
            if exc_reg:
                try:
                    if re.search(exc_reg, name, re.IGNORECASE): continue
                except: pass

            ren_pat = self.opt.get('rename_pattern', '').strip()
            ren_rep = self.opt.get('rename_replacement', '').strip()
            if ren_pat:
                try:
                    py_rep = ren_rep.replace('$', '\\')
                    name = re.sub(ren_pat, py_rep, name)
                    current_node['name'] = name
                except: pass

            if self.opt.get('emoji', True):
                flag = node_region.split(' ')[0] 
                if flag and flag not in name: current_node['name'] = f"{flag} {name}"
            
            result.append(current_node)
        
        if self.opt.get('sort', False): result.sort(key=lambda x: x['name'])
        self.preview_nodes = result
        if hasattr(self, 'preview_container'): self.render_preview_ui()

    def ui(self, dlg):
        with ui.card().classes('w-full max-w-6xl h-[90vh] flex flex-col p-0 overflow-hidden bg-white'):
            with ui.row().classes('w-full justify-between items-center p-4 bg-white border-b shadow-sm z-20'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', color='primary').classes('text-xl')
                    ui.label(f"订阅策略: {self.sub_data.get('name', '未命名')}").classes('text-lg font-bold text-slate-800')
                with ui.row().classes('gap-2'):
                    ui.button('取消', on_click=dlg.close).props('flat color=grey')
                    ui.button('保存配置', icon='save', on_click=lambda: [self.save(), dlg.close(), safe_notify('策略已更新', 'positive')]).classes('bg-slate-900 text-white shadow-lg')

            with ui.row().classes('w-full flex-grow overflow-hidden gap-0'):
                with ui.column().classes('w-[350px] flex-shrink-0 h-full border-r bg-gray-50 flex flex-col'):
                    with ui.row().classes('w-full p-3 bg-white border-b justify-between items-center'):
                        ui.label('效果预览').classes('text-xs font-bold text-gray-500')
                        self.count_label = ui.badge(f'{len(self.preview_nodes)}', color='blue')
                    with ui.scroll_area().classes('w-full flex-grow p-2'):
                        self.preview_container = ui.column().classes('w-full gap-1')
                        self.render_preview_ui()

                with ui.column().classes('flex-grow h-full overflow-y-auto bg-white'):
                    with ui.column().classes('w-full max-w-3xl mx-auto p-8 gap-6'):
                        ui.label('基础处理').classes('text-sm font-bold text-gray-900')
                        with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 gap-4'):
                            self._render_switch('自动添加国旗 (Emoji)', 'emoji', 'flag')
                            self._render_switch('节点自动排序 (A-Z)', 'sort', 'sort_by_alpha')
                            self._render_switch('强制开启 UDP 转发', 'udp', 'rocket_launch')
                            self._render_switch('跳过证书验证', 'skip_cert', 'lock_open')
                            self._render_switch('TCP Fast Open', 'tfo', 'speed')
                        ui.separator()

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

                        ui.label('正则过滤').classes('text-sm font-bold text-gray-900')
                        with ui.column().classes('w-full gap-3'):
                            with ui.input('保留匹配 (Include)', placeholder='例如: 香港|SG', value=self.opt.get('include_regex', '')) \
                                .props('outlined dense clearable').classes('w-full') as i1:
                                i1.on_value_change(lambda e: [self.opt.update({'include_regex': e.value}), self.update_preview()])
                            with ui.input('排除匹配 (Exclude)', placeholder='例如: 过期|剩余', value=self.opt.get('exclude_regex', '')) \
                                .props('outlined dense clearable').classes('w-full') as i2:
                                i2.on_value_change(lambda e: [self.opt.update({'exclude_regex': e.value}), self.update_preview()])
                        ui.separator()

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
        card = ui.card().classes('p-3 border border-gray-200 shadow-none flex-row items-center justify-between hover:bg-gray-50 transition cursor-pointer')
        with card:
            with ui.row().classes('items-center gap-3'):
                ui.icon(icon).classes('text-lg text-blue-500')
                ui.label(label).classes('text-sm font-medium text-gray-700 select-none')
            sw = ui.switch(value=val).props('dense color=primary')
            sw.on_value_change(lambda e: [self.opt.update({key: e.value}), self.update_preview()])
            
        # 点击卡片反转开关
        card.on('click', lambda: sw.set_value(not sw.value))

    def sync_regions_opt(self):
        self.opt['regions'] = [r for r, chk in self.region_checks.items() if chk.value]

    def toggle_regions(self, state):
        for chk in self.region_checks.values(): chk.value = state
        self.sync_regions_opt(); self.update_preview()

    def save(self): asyncio.create_task(save_subs())

# 打开策略编辑器的入口函数
def open_process_editor(sub_data):
    with ui.dialog() as d: SubscriptionProcessEditor(sub_data).ui(d); d.open()

# ================= 通用服务器保存函数  =================
async def save_server_config(server_data, is_add=True, idx=None):
    """
    统一处理服务器的保存逻辑（新增或编辑）
    1. 查重
    2. 写入缓存
    3. 触发后台极速修正 (GeoIP)
    4. 触发后台探针安装
    """
    # 1. 基础校验
    if not server_data.get('name') or not server_data.get('url'):
        safe_notify("名称和地址不能为空", "negative")
        return False

    # 2. 逻辑处理
    if is_add:
        # --- 新增模式 ---
        # 查重
        for s in SERVERS_CACHE:
            if s['url'] == server_data['url']:
                safe_notify(f"服务器地址 {server_data['url']} 已存在！", "warning")
                return False
        
        # 初始处理：如果没有国旗，先给白旗占位
        # (check 1: 名字里没国旗; check 2: 名字里也没白旗)
        has_flag = False
        for v in AUTO_COUNTRY_MAP.values():
            if v.split(' ')[0] in server_data['name']:
                has_flag = True
                break
        
        if not has_flag and '🏳️' not in server_data['name']:
             server_data['name'] = f"🏳️ {server_data['name']}"

        # 写入列表
        SERVERS_CACHE.append(server_data)
        safe_notify(f"已添加服务器: {server_data['name']}", "positive")

    else:
        # --- 编辑模式 ---
        if idx is not None and 0 <= idx < len(SERVERS_CACHE):
            SERVERS_CACHE[idx].update(server_data)
            safe_notify(f"已更新服务器: {server_data['name']}", "positive")
        else:
            safe_notify("编辑目标不存在", "negative")
            return False

    # 3. 保存到硬盘
    await save_servers()

    # 4. 刷新左侧列表
    render_sidebar_content.refresh()
    
    # 5. 如果当前正在看这台服务器，刷新右侧详情
    try:
        # 这里的 refresh_content 使用 force_refresh=True 会顺便同步一下节点
        if is_add:
            # 新增的显示最后一个
            await refresh_content('SINGLE', SERVERS_CACHE[-1], force_refresh=True)
        else:
            # 编辑的显示当前这个
            await refresh_content('SINGLE', SERVERS_CACHE[idx], force_refresh=True)
    except: pass

    # ================= ✨ 核心：触发后台自动化任务 ✨ =================
    
    # 任务 1: 极速 GeoIP 修正 (2秒后自动变国旗、自动归类分组)
    asyncio.create_task(fast_resolve_single_server(server_data))
    
    # 任务 2: 自动安装探针 (如果配置了SSH)
    if ADMIN_CONFIG.get('probe_enabled', False) and server_data.get('probe_installed', False):
        asyncio.create_task(install_probe_on_server(server_data))
        
    return True


                        
# ================= 小巧卡片式弹窗 =================
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
                t_ssh = ui.tab('SSH / 探针', icon='terminal')

        # 2. 通用字段
        name = ui.input(value=data.get('name',''), label='备注名称 (留空自动获取)').classes('w-full').props('outlined dense')
        group = ui.select(options=get_all_groups(), value=data.get('group','默认分组'), new_value_mode='add-unique', label='分组').classes('w-full').props('outlined dense')
        
        # 3. 内容面板
        with ui.tab_panels(tabs, value=t_xui).classes('w-full animated fadeIn'):
            
            # --- Tab 1: 面板设置 ---
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                # 这里的 url 变量稍后会和 Tab 2 的输入框绑定
                url = ui.input(value=data.get('url',''), label='面板 URL 或 IP').classes('w-full').props('outlined dense')
                
                with ui.row().classes('w-full gap-2'):
                    user = ui.input(value=data.get('user',''), label='账号').classes('flex-1').props('outlined dense')
                    pwd = ui.input(value=data.get('pass',''), label='密码', password=True).classes('flex-1').props('outlined dense')
                prefix = ui.input(value=data.get('prefix',''), label='API 前缀 (选填)').classes('w-full').props('outlined dense')

                ui.separator().classes('my-1')

                # 复选框
                probe_chk = ui.checkbox('启用 Root 探针 (自动安装)', value=data.get('probe_installed', False))
                probe_chk.classes('text-sm font-bold text-slate-700')
                
                ui.label('提示：若此处未填写信息且仅填写了 SSH，保存时将自动启用探针模式。').classes('text-[10px] text-gray-400 ml-8 -mt-2 leading-tight')

            # --- Tab 2: SSH 配置 ---
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                
                # ✨✨✨ [新增] SSH 页面的 Host 输入框 (与 Tab 1 同步) ✨✨✨
                # 逻辑：自动获取 Tab 1 的值；输入时同步回 Tab 1
                ssh_host_input = ui.input(label='面板 URL 或 IP (必填)', value=url.value).classes('w-full').props('outlined dense')
                
                # ✨ 双向绑定逻辑 ✨
                # 1. 当在这个框输入时 -> 更新 Tab 1 的 url
                ssh_host_input.on_value_change(lambda e: url.set_value(e.value))
                # 2. 当 Tab 1 的 url 变化时 -> 更新这个框
                url.on_value_change(lambda e: ssh_host_input.set_value(e.value))

                ui.label('SSH 连接信息').classes('text-xs font-bold text-gray-500 mb-1 mt-1')
                
                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('w-full gap-2'):
                        ssh_user = ui.input(value=data.get('ssh_user','root'), label='SSH 用户').classes('flex-1').props('outlined dense')
                        ssh_port = ui.input(value=data.get('ssh_port','22'), label='端口').classes('w-1/3').props('outlined dense')
                    
                    auth_type = ui.select(['全局密钥', '独立密码', '独立密钥'], value=data.get('ssh_auth_type', '全局密钥'), label='认证方式').classes('w-full').props('outlined dense options-dense')
                    
                    ssh_pwd = ui.input(label='SSH 密码', password=True, value=data.get('ssh_password','')).classes('w-full').props('outlined dense')
                    ssh_pwd.bind_visibility_from(auth_type, 'value', value='独立密码')
                    
                    ssh_key = ui.textarea(label='SSH 私钥', value=data.get('ssh_key','')).classes('w-full').props('outlined dense rows=3 input-class=font-mono text-xs')
                    ssh_key.bind_visibility_from(auth_type, 'value', value='独立密钥')
                    
                    ui.label('✅ 将自动使用全局私钥连接').bind_visibility_from(auth_type, 'value', value='全局密钥').classes('text-green-600 text-xs text-center mt-2')

        # 4. 底部按钮
        with ui.row().classes('w-full justify-end gap-2 mt-2'):
            if is_edit:
                async def delete():
                    if idx < len(SERVERS_CACHE): 
                        # 获取要删除的服务器信息
                        deleted_srv = SERVERS_CACHE.pop(idx)
                        deleted_url = deleted_srv.get('url')
                        
                        # ✨✨✨ 双重保险：显式清理所有相关缓存 ✨✨✨
                        if deleted_url in PROBE_DATA_CACHE: del PROBE_DATA_CACHE[deleted_url]
                        if deleted_url in NODES_DATA: del NODES_DATA[deleted_url]
                        if deleted_url in PING_TREND_CACHE: del PING_TREND_CACHE[deleted_url]

                    await save_servers()
                    d.close()
                    render_sidebar_content.refresh()
                    await refresh_content('ALL')
                    safe_notify('服务器已删除，缓存已清理', 'positive')
                ui.button('删除', on_click=delete, color='red').props('flat dense')

            async def save():
                # 1. 获取最终 URL (因为双向绑定，取 url.value 即可获取两边最新的值)
                final_url = url.value.strip()
                final_user = user.value.strip()
                final_pass = pwd.value.strip()
                
                # ✨✨✨ [校验]：如果此时 URL 还是空的，说明两边都没填 ✨✨✨
                if not final_url:
                     safe_notify("错误：必须填写 '面板 URL 或 IP'", "negative")
                     # 可以在这里做个小交互，自动切到 Tab 2 并聚焦输入框
                     t_ssh.value = True # 切换 Tab
                     return

                # 判断 SSH 信息是否有效
                has_ssh_info = False
                if ssh_user.value:
                    if auth_type.value == '全局密钥': has_ssh_info = True
                    elif auth_type.value == '独立密码' and ssh_pwd.value: has_ssh_info = True
                    elif auth_type.value == '独立密钥' and ssh_key.value: has_ssh_info = True

                # 判断 X-UI 信息是否有效 (URL + 账号 + 密码)
                has_xui_info = bool(final_url and final_user and final_pass)

                # 核心逻辑判断
                final_probe_enable = False

                if has_xui_info:
                    # 场景 1: 填写了 X-UI 信息 -> 严格遵循复选框
                    final_probe_enable = probe_chk.value
                else:
                    # 场景 2: 未填写 X-UI 信息 (只有 IP/URL，没账号密码)
                    if has_ssh_info:
                        final_probe_enable = True
                    else:
                        final_probe_enable = False

                # 自动命名
                final_name = name.value.strip()
                if not final_name:
                    safe_notify("正在智能获取名称...", "ongoing")
                    temp_conf = {'url': final_url, 'user': final_user, 'pass': final_pass, 'prefix': prefix.value}
                    final_name = await generate_smart_name(temp_conf)
                
                server_data = {
                    'name': final_name, 
                    'group': group.value,
                    'url': final_url, 
                    'user': final_user, 
                    'pass': final_pass, 
                    'prefix': prefix.value,
                    'ssh_port': ssh_port.value, 
                    'ssh_user': ssh_user.value,
                    'ssh_auth_type': auth_type.value, 
                    'ssh_password': ssh_pwd.value, 
                    'ssh_key': ssh_key.value,
                    'probe_installed': final_probe_enable 
                }
                
                success = await save_server_config(server_data, is_add=not is_edit, idx=idx)
                
                if success:
                    # ✨✨✨ [修复核心] 保存成功后，立即刷新当前列表视图 ✨✨✨
                    # 判断当前是不是在列表页，如果是，就刷新一下
                    if CURRENT_VIEW_STATE.get('scope') in ['ALL', 'TAG', 'COUNTRY']:
                        await refresh_content(CURRENT_VIEW_STATE['scope'], CURRENT_VIEW_STATE['data'])
                    elif not is_edit: 
                        # 如果是新增，且当前不在列表页，强制跳转到所有服务器列表
                        await refresh_content('ALL')

                    if final_probe_enable:
                        safe_notify(f"🚀 正在后台连接 SSH 并推送 Agent...", "ongoing")
                        asyncio.create_task(install_probe_on_server(server_data))
                    else:
                        safe_notify(f"✅ 配置已保存 (未启用探针)", "positive")
                    d.close()
            
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
    
# ================= 数据备份/恢复  =================
async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden'):
        
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
                        
                        # --- 默认设置区域 (SSH 认证升级版) ---
                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            def_ssh_user = ui.input('默认 SSH 用户', value='root').props('dense outlined')
                            def_ssh_port = ui.input('默认 SSH 端口', value='22').props('dense outlined')
                            
                            # ✨✨✨ [新增] 认证方式选择 ✨✨✨
                            def_auth_type = ui.select(['全局密钥', '独立密码', '独立密钥'], value='全局密钥', label='默认 SSH 认证').classes('col-span-2').props('dense outlined options-dense')
                            
                            # ✨✨✨ [新增] 动态显隐：密码框 ✨✨✨
                            def_ssh_pwd = ui.input('默认 SSH 密码').props('dense outlined').classes('col-span-2')
                            def_ssh_pwd.bind_visibility_from(def_auth_type, 'value', value='独立密码')
                            
                            # ✨✨✨ [新增] 动态显隐：私钥框 ✨✨✨
                            def_ssh_key = ui.textarea('默认 SSH 私钥').props('dense outlined rows=2 input-class=text-xs font-mono').classes('col-span-2')
                            def_ssh_key.bind_visibility_from(def_auth_type, 'value', value='独立密钥')

                            def_xui_port = ui.input('默认 X-UI 端口', value='54321').props('dense outlined')
                            def_xui_user = ui.input('默认 X-UI 账号', value='admin').props('dense outlined')
                            def_xui_pass = ui.input('默认 X-UI 密码', value='admin').props('dense outlined')
                        
                        ui.separator()

                        # ✨✨✨ 双独立开关 (Double Switch) ✨✨✨
                        with ui.row().classes('w-full justify-between items-center bg-gray-50 p-2 rounded border border-gray-200'):
                            chk_xui = ui.checkbox('添加 X-UI 面板', value=True).classes('font-bold text-blue-700')
                            chk_probe = ui.checkbox('启用 Root 探针 (自动安装)', value=False).classes('font-bold text-slate-700')

                        async def run_batch_import():
                            raw_text = url_area.value.strip()
                            if not raw_text: safe_notify("请输入内容", "warning"); return
                            
                            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                            count = 0
                            existing_urls = {s['url'] for s in SERVERS_CACHE}
                            post_tasks = []
                            
                            # 获取开关状态
                            should_add_xui = chk_xui.value
                            should_add_probe = chk_probe.value

                            for line in lines:
                                target_ssh_port = def_ssh_port.value
                                target_xui_port = def_xui_port.value
                                
                                if '://' in line:
                                    final_url = line
                                    try: 
                                        parsed = urlparse(line)
                                        name = parsed.hostname or line
                                    except: name = line
                                else:
                                    if ':' in line and not line.startswith('['): 
                                        parts = line.split(':')
                                        host_ip = parts[0]
                                        target_xui_port = parts[1] 
                                    else: 
                                        host_ip = line
                                        target_xui_port = def_xui_port.value
                                    
                                    final_url = f"http://{host_ip}:{target_xui_port}"
                                    name = host_ip

                                if final_url in existing_urls: continue
                                
                                # ✨ 根据开关决定是否填入账号密码
                                final_xui_user = def_xui_user.value if should_add_xui else ""
                                final_xui_pass = def_xui_pass.value if should_add_xui else ""

                                new_server = {
                                    'name': name, 
                                    'group': '', 
                                    'url': final_url,
                                    'user': final_xui_user, 
                                    'pass': final_xui_pass, 
                                    'prefix': '',
                                    'ssh_user': def_ssh_user.value, 
                                    'ssh_port': target_ssh_port,
                                    'ssh_auth_type': def_auth_type.value, # 使用选择的认证方式
                                    'ssh_password': def_ssh_pwd.value, 
                                    'ssh_key': def_ssh_key.value,
                                    'probe_installed': should_add_probe # 使用开关状态
                                }

                                SERVERS_CACHE.append(new_server)
                                existing_urls.add(final_url)
                                count += 1
                                
                                post_tasks.append(fast_resolve_single_server(new_server))
                                
                                if ADMIN_CONFIG.get('probe_enabled', False) and should_add_probe:
                                    post_tasks.append(install_probe_on_server(new_server))

                            if count > 0:
                                await save_servers()
                                render_sidebar_content.refresh()
                                safe_notify(f"成功添加 {count} 台服务器", 'positive')
                                d.close()
                                
                                if post_tasks:
                                    safe_notify(f"正在后台处理 {len(post_tasks)} 个初始化任务...", "ongoing")
                                    async def _run_bg_tasks():
                                        await asyncio.gather(*post_tasks, return_exceptions=True)
                                    asyncio.create_task(_run_bg_tasks())
                                    
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

# ================= 智能排序逻辑 =================
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
COLS_NO_PING   = 'grid-template-columns: 220px 200px 1fr 100px 80px 80px 50px 150px; align-items: center;'

# 单个服务器视图直接复用带延迟的样式
SINGLE_COLS = 'grid-template-columns: 200px 1fr 100px 80px 80px 90px 50px 150px; align-items: center;'

# 格式: 服务器(150) 备注(200) 在线状态(1fr) 流量(100) 协议(80) 端口(80) 操作(150)
COLS_ALL_SERVERS = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 150px; align-items: center;'

# ✨✨✨区域分组专用布局  ✨✨✨
# 格式: 服务器(150) 备注(200) 在线状态(1fr) 流量(100) 协议(80) 端口(80) 操作(150)
COLS_SPECIAL_WITH_PING = 'grid-template-columns: 220px 200px 1fr 100px 80px 80px 150px; align-items: center;'

# ✨✨✨ 新增：单服务器专用布局 (移除延迟列 90px，格式与 All Servers 一致) ✨✨✨
# 格式: 备注(200) 所在组(1fr) 流量(100) 协议(80) 端口(80) 状态(100) 操作(150)
SINGLE_COLS_NO_PING = 'grid-template-columns: 200px 1fr 100px 80px 80px 100px 150px; align-items: center;'

# ================= ✨✨✨ 刷新逻辑 (调整版：避免强制重绘) =================
async def refresh_content(scope='ALL', data=None, force_refresh=False):
    try: client = ui.context.client
    except: return 

    global CURRENT_VIEW_STATE
    import time
    current_token = time.time()
    
    # 更新当前视图状态
    if not force_refresh:
        CURRENT_VIEW_STATE['scope'] = scope
        CURRENT_VIEW_STATE['data'] = data
    
    CURRENT_VIEW_STATE['render_token'] = current_token
    
    # 1. 筛选目标服务器
    targets = []
    try:
        if scope == 'ALL': targets = list(SERVERS_CACHE)
        elif scope == 'TAG': targets = [s for s in SERVERS_CACHE if data in s.get('tags', [])]
        elif scope == 'COUNTRY':
            for s in SERVERS_CACHE:
                saved = s.get('group')
                real = saved if saved and saved not in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区'] else detect_country_group(s.get('name', ''))
                if real == data: targets.append(s)
        elif scope == 'SINGLE':
             if data in SERVERS_CACHE: targets = [data]
    except: pass

    # 2. 定义 UI 绘制逻辑 (清空容器并重绘)
    async def _render_ui():
        if CURRENT_VIEW_STATE.get('render_token') != current_token: return
        
        with client:
            if not content_container: return
            content_container.clear()
            content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
            
            with content_container:
                title = ""
                is_group_view = False
                show_ping = False
                
                if scope == 'ALL': title = f"🌍 所有服务器 ({len(targets)})"
                elif scope == 'TAG': 
                    title = f"🏷️ 自定义分组: {data} ({len(targets)})"
                    is_group_view = True
                elif scope == 'COUNTRY':
                    title = f"🏳️ 区域: {data} ({len(targets)})"
                    is_group_view = True
                    show_ping = True 
                elif scope == 'SINGLE':
                    if targets:
                        s = targets[0]
                        real_ip = get_real_ip_display(s['url'])
                        title = f"🖥️ {s['name']} ({real_ip})"
                    else: return

                # --- 标题栏 ---
                with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.label(title).classes('text-2xl font-bold')
                        if scope == 'SINGLE':
                            lbl = ui.label('').classes('hidden')
                            bind_ip_label(targets[0]['url'], lbl)

                    # --- 右侧按钮区 ---
                    with ui.row().classes('items-center gap-2'):
                        # 分组操作按钮
                        if is_group_view and targets:
                            with ui.row().classes('gap-1'):
                                ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey')
                                ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange')
                                ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green')
                        
                        # 单机视图按钮
                        if scope == 'SINGLE' and targets:
                            s = targets[0]
                            if s.get('url') and s.get('user') and s.get('pass'):
                                mgr = get_manager(s)
                                ui.button('新建节点', icon='add', color='green', on_click=lambda: open_inbound_dialog(mgr, None, lambda: refresh_content('SINGLE', s, force_refresh=True))).props('dense size=sm')

                        # 同步按钮 (触发 force_refresh=True)
                        if targets and scope != 'SINGLE':
                             ui.button('同步最新数据', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True)).props('outline color=primary')

                # --- 渲染具体内容 ---
                if not targets:
                    with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                        ui.icon('inbox', size='4rem'); ui.label('列表为空').classes('text-lg')
                elif scope == 'SINGLE': 
                    await render_single_server_view(targets[0])
                else: 
                    # 列表排序
                    try: targets.sort(key=smart_sort_key)
                    except: pass
                    # 调用上面写的优化版渲染函数
                    await render_aggregated_view(targets, show_ping=show_ping, token=current_token)

    # 3. ✨✨✨ 核心逻辑：只有在【非强制刷新】时才重绘 UI ✨✨✨
    if not force_refresh:
        await _render_ui()

    # 4. 后台数据同步逻辑
    # 如果是 Single 视图，或者是强制刷新，我们需要去拉取最新数据
    panel_only_servers = [s for s in targets if not s.get('probe_installed', False)]
    if force_refresh: panel_only_servers = targets # 强刷时，所有机器都拉一遍

    if panel_only_servers:
        async def _background_fetch():
            if not panel_only_servers: return
            if scope != 'SINGLE': safe_notify(f"正在后台更新 {len(panel_only_servers)} 台面板数据...", "ongoing", timeout=2000)
            
            # 发起网络请求更新数据 (结果会存入 NODES_DATA)
            tasks = [fetch_inbounds_safe(s, force_refresh=True) for s in panel_only_servers]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # ✨✨✨ 关键点：数据回来后，不需要再调用 _render_ui() 重绘页面！✨✨✨
            # render_aggregated_view 里的 row_timer 会自动读取新的 NODES_DATA 并更新文字。
            # 这里只需要给用户一个完成的反馈即可。
            if scope != 'SINGLE': safe_notify("数据已同步", "positive")
        
        asyncio.create_task(_background_fetch())
        
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
    ui_refs = {}
    
    # 判断是否配置了有效的 X-UI 信息
    has_xui_config = (server_conf.get('url') and server_conf.get('user') and server_conf.get('pass'))

    # --- UI 组件定义 ---
    def _create_live_ring(label, color, key_prefix):
        with ui.column().classes('items-center justify-center min-w-[100px]'):
            with ui.element('div').classes('relative flex items-center justify-center w-16 h-16 mb-2'):
                ui_refs[f'{key_prefix}_ring'] = ui.circular_progress(0, size='60px', show_value=False, color=color).props('track-color=grey-3 thickness=0.15').classes('absolute transition-all duration-500')
                ui_refs[f'{key_prefix}_pct'] = ui.label('--%').classes('text-xs font-bold text-gray-700 z-10')
            ui.label(label).classes('text-xs font-bold text-gray-600')
            ui_refs[f'{key_prefix}_detail'] = ui.label('-- / --').classes('text-[10px] text-gray-400 font-mono text-center leading-tight')

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

    def _create_live_stat_card(title, icon, color_cls, key_prefix):
        with ui.card().classes('p-3 shadow-sm border flex-grow items-center justify-between min-w-[150px]'):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes('justify-center items-center bg-gray-100 rounded-full p-2'):
                    ui_refs[f'{key_prefix}_icon'] = ui.icon(icon).classes(f'{color_cls} text-xl')
                with ui.column().classes('gap-0'):
                    ui.label(title).classes('text-xs text-gray-400 font-bold')
                    ui_refs[f'{key_prefix}_main'] = ui.label('--').classes('text-sm font-bold text-slate-700')
                    ui_refs[f'{key_prefix}_sub'] = ui.label('--').classes('text-[10px] text-gray-400')

    list_container = ui.column().classes('w-full mb-6') 
    status_container = ui.column().classes('w-full mb-6') 
    ssh_container_outer = ui.column().classes('w-full') 

    # 1. 节点列表渲染
    with list_container:
        if not has_xui_config:
            with ui.card().classes('w-full p-4 bg-orange-50 border border-orange-200 items-center flex-row gap-4'):
                ui.icon('info', size='2rem').classes('text-orange-500')
                with ui.column().classes('gap-1'):
                    ui.label('未配置 X-UI 面板信息').classes('font-bold text-orange-800')
                    ui.label('当前仅作为服务器探针运行。如需管理节点，请在编辑页面填写面板 URL 和账号密码。').classes('text-xs text-orange-600')
        else:
            res = await fetch_inbounds_safe(server_conf, force_refresh=force_refresh)
            
            # ✨✨✨ 关键步骤：提取纯净的主机名 (去掉 http:// 和 :端口) ✨✨✨
            # 这样生成的配置才是 vmess=1.2.3.4:端口，而不是 vmess=http://1.2.3.4:54321:端口
            raw_host = server_conf['url'].split('://')[-1].split(':')[0]

            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2').style(SINGLE_COLS_NO_PING):
                ui.label('备注名称').classes('text-left pl-2')
                for h in ['所在组', '已用流量', '协议', '端口', '状态', '操作']: ui.label(h).classes('text-center')
            
            if not res: 
                msg = '暂无节点 (后台同步中...)' if not server_conf.get('probe_installed') else '暂无节点数据'
                ui.label(msg).classes('text-gray-400 mt-4 text-center w-full')
            else:
                for n in res:
                    traffic = format_bytes(n.get('up', 0) + n.get('down', 0))
                    with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(SINGLE_COLS_NO_PING):
                        ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                        ui.label(server_conf.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                        ui.label(traffic).classes('text-xs text-gray-600 w-full text-center font-mono')
                        ui.label(n.get('protocol', 'unk')).classes('uppercase text-xs font-bold w-full text-center')
                        ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                        
                        is_enable = n.get('enable', True)
                        with ui.row().classes('w-full justify-center items-center gap-1'):
                            ui.icon('bolt').classes(f'text-{"green" if is_enable else "red"}-500 text-sm')
                            ui.label("运行中" if is_enable else "已停止").classes(f'text-xs font-bold text-{"green" if is_enable else "red"}-600')

                        with ui.row().classes('gap-2 justify-center w-full no-wrap'):
                            # 1. 复制通用链接 (vmess://)
                            l = generate_node_link(n, server_conf['url'])
                            if l: ui.button(icon='content_copy', on_click=lambda u=l: safe_copy_to_clipboard(u)).props('flat dense size=sm').tooltip('复制链接 (Base64)')
                            
                            # 2. ✨✨✨ 新增：复制明文配置 (Surge格式) ✨✨✨
                            detail_conf = generate_detail_config(n, raw_host)
                            if detail_conf:
                                ui.button(icon='description', on_click=lambda t=detail_conf: safe_copy_to_clipboard(t)).props('flat dense size=sm text-color=purple').tooltip('复制明文配置 (Surge/Loon)')

                            # 3. 编辑和删除
                            ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm')
                            ui.button(icon='delete', on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'], i.get('remark',''), lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm color=red')

    # 2. 状态面板
    with status_container:
        ui.separator().classes('my-4') 
        with ui.card().classes('w-full p-4 bg-white rounded-xl shadow-sm border border-gray-100'):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                ui.label('服务器实时监控').classes('text-sm font-bold text-gray-500')
                ui_refs['heartbeat'] = ui.spinner('dots', size='1em', color='green').classes('opacity-0 transition-opacity')

            with ui.row().classes('w-full justify-around items-start mb-6 border-b pb-4'):
                _create_live_ring('CPU', 'blue', 'cpu')
                _create_live_ring('内存', 'green', 'mem')
                _create_live_ring('硬盘', 'purple', 'disk')

            with ui.row().classes('w-full gap-4 mb-6 flex-wrap'):
                _create_live_net_card('实时网速', 'speed', 'speed')
                _create_live_net_card('服务器总流量', 'data_usage', 'total')

            with ui.row().classes('w-full gap-4 flex-wrap'):
                _create_live_stat_card('Xray 状态', 'settings_power', 'text-gray-400', 'xray')
                _create_live_stat_card('运行时间', 'schedule', 'text-cyan-600', 'uptime')
                _create_live_stat_card('系统负载', 'analytics', 'text-pink-600', 'load')

    # 3. 嵌入式 SSH 终端
    with ssh_container_outer:
        ui.separator().classes('my-4')
        ssh_card = ui.card().classes('w-full p-0 border border-gray-300 rounded-xl overflow-hidden shadow-sm flex flex-col')
        ssh_state = {'active': False, 'instance': None}

        def render_ssh_area():
            ssh_card.clear()
            with ssh_card:
                with ui.row().classes('w-full h-10 bg-slate-800 items-center justify-between px-4 flex-shrink-0'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal').classes('text-white text-sm')
                        ui.label(f"SSH Console: {server_conf['name']}").classes('text-white text-xs font-mono font-bold')
                    if ssh_state['active']:
                        ui.button(icon='close', on_click=stop_ssh).props('flat dense round color=red size=sm').tooltip('断开连接')

                terminal_box = ui.column().classes('w-full h-[700px] bg-black relative justify-center items-center p-0 overflow-hidden')
                
                if not ssh_state['active']:
                    with terminal_box:
                        with ui.column().classes('items-center gap-4'):
                            ui.icon('dns', size='4rem').classes('text-gray-800')
                            ui.label('安全终端已就绪').classes('text-gray-600 text-sm font-bold')
                            host_name = server_conf.get('url', '').replace('http://', '').split(':')[0]
                            ui.label(f"{server_conf.get('ssh_user','root')} @ {host_name}").classes('text-gray-700 font-mono text-xs mb-2 bg-gray-100 px-2 py-1 rounded')
                            ui.button('立即连接 SSH', icon='login', on_click=start_ssh).classes('bg-blue-600 text-white shadow-lg px-6')
                else:
                    ssh = WebSSH(terminal_box, server_conf)
                    ssh_state['instance'] = ssh
                    ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

        async def start_ssh():
            ssh_state['active'] = True
            render_ssh_area()

        async def stop_ssh():
            if ssh_state['instance']:
                ssh_state['instance'].close()
                ssh_state['instance'] = None
            ssh_state['active'] = False
            render_ssh_area()

        render_ssh_area()

    # 4. 数据更新任务
    async def update_data_task():
        try:
            if 'heartbeat' in ui_refs: ui_refs['heartbeat'].classes(remove='opacity-0')
            status = await get_server_status(server_conf)
            if status:
                is_lite = status.get('_is_lite', False)
                def smart_fmt(used_pct, total_val):
                    try:
                        total = float(total_val)
                        if total == 0: return "-- / --"
                        if total > 10000: used = total * (used_pct / 100); return f"{format_bytes(used)} / {format_bytes(total)}"
                        else: used = total * (used_pct / 100); return f"{round(used, 1)} / {round(total, 1)} GB"
                    except: return "-- / --"

                cpu = float(status.get('cpu_usage', 0))
                if 'cpu_ring' in ui_refs: 
                    ui_refs['cpu_ring'].set_value(cpu / 100)
                    ui_refs['cpu_ring'].props(f'color={"orange" if is_lite else "blue"}')
                if 'cpu_pct' in ui_refs: ui_refs['cpu_pct'].set_text(f"{round(cpu, 1)}%")
                if 'cpu_detail' in ui_refs:
                    cores = status.get('cpu_cores', 0)
                    ui_refs['cpu_detail'].set_text(f"{cores} Cores" if cores and cores > 0 else f"{int(cpu)}% Used")
                
                mem_pct = float(status.get('mem_usage', 0))
                mem_total = float(status.get('mem_total', 1))
                if 'mem_ring' in ui_refs: ui_refs['mem_ring'].set_value(mem_pct / 100)
                if 'mem_pct' in ui_refs: ui_refs['mem_pct'].set_text(f"{int(mem_pct)}%")
                if 'mem_detail' in ui_refs: ui_refs['mem_detail'].set_text(smart_fmt(mem_pct, mem_total))

                disk_pct = float(status.get('disk_usage', 0))
                disk_total = status.get('disk_total', 0)
                if 'disk_ring' in ui_refs: ui_refs['disk_ring'].set_value(disk_pct / 100)
                if 'disk_pct' in ui_refs: ui_refs['disk_pct'].set_text(f"{int(disk_pct)}%")
                if 'disk_detail' in ui_refs: ui_refs['disk_detail'].set_text(smart_fmt(disk_pct, disk_total))

                def fmt_speed(b): return f"{format_bytes(b)}/s"
                if 'speed_up' in ui_refs: ui_refs['speed_up'].set_text(fmt_speed(status.get('net_speed_out', 0)))
                if 'speed_down' in ui_refs: ui_refs['speed_down'].set_text(fmt_speed(status.get('net_speed_in', 0)))
                if 'total_up' in ui_refs: ui_refs['total_up'].set_text(format_bytes(status.get('net_total_out', 0)))
                if 'total_down' in ui_refs: ui_refs['total_down'].set_text(format_bytes(status.get('net_total_in', 0)))
                if 'uptime_main' in ui_refs: ui_refs['uptime_main'].set_text(status.get('uptime', '-'))
                if 'load_main' in ui_refs: ui_refs['load_main'].set_text(str(status.get('load_1', '--')))
                
                if 'xray_main' in ui_refs: 
                    if not has_xui_config: ui_refs['xray_main'].set_text("Probe Only")
                    else: ui_refs['xray_main'].set_text("Lite Mode" if is_lite else "RUNNING")
                if 'xray_icon' in ui_refs: ui_refs['xray_icon'].classes(replace='text-green-500', remove='text-gray-400 text-red-500')
            else:
                if 'xray_icon' in ui_refs: ui_refs['xray_icon'].classes(replace='text-red-500', remove='text-green-500 text-gray-400')

            if 'heartbeat' in ui_refs: ui_refs['heartbeat'].classes(add='opacity-0')
        except: pass

    interval = 3.0 if server_conf.get('probe_installed') else 5.0
    ui.timer(interval, update_data_task)
    ui.timer(0.1, update_data_task, once=True)
    
# ================= 聚合视图 (局部静默刷新 + 自动状态更新) =================
# 全局字典，用于存储每行 UI 元素的引用，以便局部更新
# 结构: { 'server_url': { 'row_el': row_element, 'status_icon': icon, 'status_label': label, ... } }
UI_ROW_REFS = {} 
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None}

# ================= ✨✨✨ 高性能渲染函数 ✨✨✨ =================
async def render_aggregated_view(server_list, show_ping=False, force_refresh=False, token=None):
    # 如果强制刷新，后台触发一下数据更新，但不阻塞当前 UI 渲染
    if force_refresh:
        asyncio.create_task(asyncio.gather(*[fetch_inbounds_safe(s, force_refresh=True) for s in server_list], return_exceptions=True))

    list_container = ui.column().classes('w-full gap-4')
    
    # 定义布局样式
    is_all_servers = (len(server_list) == len(SERVERS_CACHE) and not show_ping)
    use_special_mode = is_all_servers or show_ping
    # 使用之前的 CSS 变量 (请确保全局变量中 COLS_XXX 已定义)
    current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING

    list_container.clear()
    with list_container:
        # 1. 绘制静态表头 (只画一次)
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
        
        # 2. 遍历服务器，绘制每一行
        for srv in server_list:
            # 创建行容器
            row_card = ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2 items-center').style(current_css)
            
            with row_card:
                # --- 静态内容 (不会变的) ---
                ui.label(srv.get('name', '未命名')).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                
                # --- 动态内容 (需要变的数据，先创建 Label 占位) ---
                
                # 1. 备注名
                lbl_remark = ui.label('Loading...').classes('font-bold truncate w-full text-left pl-2')
                
                # 2. 分组或在线状态
                if use_special_mode:
                    with ui.row().classes('w-full justify-center items-center gap-1'):
                        icon_status = ui.icon('bolt').classes('text-gray-300 text-sm')
                        lbl_ip = ui.label(get_real_ip_display(srv['url'])).classes('text-xs font-mono text-gray-500')
                        bind_ip_label(srv['url'], lbl_ip) # 绑定 DNS 更新
                else:
                    lbl_group = ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')

                # 3. 流量
                lbl_traffic = ui.label('--').classes('text-xs text-gray-600 w-full text-center font-mono')
                
                # 4. 协议 & 端口
                lbl_proto = ui.label('--').classes('uppercase text-xs font-bold w-full text-center')
                lbl_port = ui.label('--').classes('text-blue-600 font-mono w-full text-center')

                # 5. 状态圆点 (非特殊模式下)
                icon_dot = None
                if not use_special_mode:
                    with ui.element('div').classes('flex justify-center w-full'): 
                        icon_dot = ui.icon('circle', color='grey').props('size=xs')
                
                # 6. 操作按钮 (已移除编辑按钮)
                with ui.row().classes('gap-2 justify-center w-full no-wrap'):
                    
                    # ✨✨✨ 闭包工厂：确保点击事件能锁定当前的 srv 对象 ✨✨✨
                    def make_handlers(current_s):
                        # A. 复制链接
                        async def on_copy_link():
                            nodes = NODES_DATA.get(current_s['url'], [])
                            if nodes:
                                await safe_copy_to_clipboard(generate_node_link(nodes[0], current_s['url']))
                            else:
                                safe_notify('暂无节点数据', 'warning')
                        
                        # B. 复制明文 (新增)
                        async def on_copy_text():
                            nodes = NODES_DATA.get(current_s['url'], [])
                            if nodes:
                                # 提取 Host
                                raw_host = current_s['url'].split('://')[-1].split(':')[0]
                                text = generate_detail_config(nodes[0], raw_host)
                                if text:
                                    await safe_copy_to_clipboard(text)
                                    safe_notify('明文配置已复制', 'positive')
                                else:
                                    safe_notify('生成配置失败', 'warning')
                            else:
                                safe_notify('暂无节点数据', 'warning')
                        
                        return on_copy_link, on_copy_text

                    # 获取绑定好的处理函数
                    h_copy, h_text = make_handlers(srv)

                    # 1. 复制 Base64 链接
                    ui.button(icon='content_copy', on_click=h_copy).props('flat dense size=sm').tooltip('复制链接 (Base64)')
                    
                    # 2. 复制明文配置 (Surge/Loon)
                    ui.button(icon='description', on_click=h_text).props('flat dense size=sm text-color=purple').tooltip('复制明文配置 (Surge/Loon)')
                    
                    # 3. 详情/删除
                    ui.button(icon='settings', on_click=lambda s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm color=blue-grey').tooltip('服务器详情/删除')

            # ================= 内部闭包更新函数 (保持不变) =================
            def update_row(_srv=srv, _lbl_rem=lbl_remark, _lbl_tra=lbl_traffic, 
                          _lbl_pro=lbl_proto, _lbl_prt=lbl_port, _icon_dot=icon_dot, 
                          _icon_stat=icon_status if use_special_mode else None):
                
                nodes = NODES_DATA.get(_srv['url'], [])
                
                if not nodes:
                    is_probe = _srv.get('probe_installed', False)
                    msg = '同步中...' if not is_probe else '离线/无节点'
                    _lbl_rem.set_text(msg)
                    _lbl_rem.classes(replace='text-gray-400' if not is_probe else 'text-red-500', remove='text-black')
                    _lbl_tra.set_text('--')
                    _lbl_pro.set_text('--')
                    _lbl_prt.set_text('--')
                    if _icon_stat: _icon_stat.classes(replace='text-red-300')
                    if _icon_dot: _icon_dot.props('color=grey')
                    return

                n = nodes[0]
                total_traffic = sum(x.get('up',0) + x.get('down',0) for x in nodes)
                
                _lbl_rem.set_text(n.get('remark', '未命名'))
                _lbl_rem.classes(replace='text-black', remove='text-gray-400 text-red-500')
                
                _lbl_tra.set_text(format_bytes(total_traffic))
                _lbl_pro.set_text(n.get('protocol', 'unk'))
                _lbl_prt.set_text(str(n.get('port', 0)))

                is_online = _srv.get('_status') == 'online'
                is_enable = n.get('enable', True)
                
                if use_special_mode and _icon_stat:
                    color = 'text-green-500' if is_online else 'text-red-500'
                    if not _srv.get('probe_installed'): color = 'text-orange-400'
                    _icon_stat.classes(replace=color, remove='text-gray-300')
                
                if not use_special_mode and _icon_dot:
                    _icon_dot.props(f'color={"green" if is_enable else "red"}')

            ui.timer(2.0, update_row)
            update_row()


# ================= 核心：静默刷新 UI 数据  =================
async def refresh_dashboard_ui():
    try:
        # 如果仪表盘还没打开（引用是空的），直接跳过
        if not DASHBOARD_REFS.get('servers'): return

        total_servers = len(SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        total_up_bytes = 0
        total_down_bytes = 0
        
        server_traffic_map = {}
        protocol_count = {}
        
        # --- 1. 计算数据 ---
        for s in SERVERS_CACHE:
            res = NODES_DATA.get(s['url'], [])
            name = s.get('name', '未命名')
            
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
        
        # 更新顶部卡片数字
        if DASHBOARD_REFS.get('servers'): DASHBOARD_REFS['servers'].set_text(f"{online_servers}/{total_servers}")
        if DASHBOARD_REFS.get('nodes'): DASHBOARD_REFS['nodes'].set_text(str(total_nodes))
        if DASHBOARD_REFS.get('traffic'): DASHBOARD_REFS['traffic'].set_text(f"{total_traffic_bytes/(1024**3):.2f} GB")
        if DASHBOARD_REFS.get('subs'): DASHBOARD_REFS['subs'].set_text(str(len(SUBS_CACHE)))

        # 更新柱状图 (流量排行)
        if DASHBOARD_REFS.get('bar_chart'):
            sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15] 
            names = [x[0] for x in sorted_traffic]
            values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]
            
            DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = names
            DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = values
            DASHBOARD_REFS['bar_chart'].update()

        # 更新饼图 (协议分布)
        if DASHBOARD_REFS.get('pie_chart'):
            pie_data = [{'name': k, 'value': v} for k, v in protocol_count.items()]
            DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = pie_data
            DASHBOARD_REFS['pie_chart'].update()
            
            # ✨✨✨ 修改点：删除了 stat_up, stat_down, stat_avg 的更新代码 ✨✨✨

        if DASHBOARD_REFS.get('map_info'):
             DASHBOARD_REFS['map_info'].set_text('Live Rendering')

    except Exception as e:
        logger.error(f"UI 更新失败: {e}")

# ================= 核心：仪表盘主视图渲染 =================
async def load_dashboard_stats():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None
    
    await asyncio.sleep(0.1)
    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
    
    with content_container:
        ui.label('系统概览').classes('text-3xl font-bold mb-6 text-slate-800 tracking-tight')
        
        # === A. 顶部统计卡片 (保持不变) ===
        with ui.row().classes('w-full gap-6 mb-8 items-stretch'):
            def create_stat_card(key, title, sub_text, icon, gradient):
                with ui.card().classes(f'flex-1 p-6 shadow-lg border-none text-white {gradient} rounded-xl transform hover:scale-105 transition duration-300 relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-6 -top-6 w-24 h-24 bg-white opacity-10 rounded-full')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-1'):
                            ui.label(title).classes('opacity-80 text-xs font-bold uppercase tracking-wider')
                            DASHBOARD_REFS[key] = ui.label('Wait...').classes('text-3xl font-extrabold tracking-tight')
                            ui.label(sub_text).classes('opacity-70 text-xs font-medium')
                        ui.icon(icon).classes('text-4xl opacity-80')

            create_stat_card('servers', '在线服务器', 'Online / Total', 'dns', 'bg-gradient-to-br from-blue-500 to-indigo-600')
            create_stat_card('nodes', '节点总数', 'Active Nodes', 'hub', 'bg-gradient-to-br from-purple-500 to-pink-600')
            create_stat_card('traffic', '总流量消耗', 'Upload + Download', 'bolt', 'bg-gradient-to-br from-emerald-500 to-teal-600')
            create_stat_card('subs', '订阅配置', 'Subscriptions', 'rss_feed', 'bg-gradient-to-br from-orange-400 to-red-500')

        # === B. 图表区域 ===
        with ui.row().classes('w-full gap-6 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            
            # --- 第三张卡片：流量排行 (保持不变) ---
            with ui.card().classes('w-full xl:w-2/3 p-6 shadow-md border-none rounded-xl bg-white flex flex-col'):
                with ui.row().classes('w-full justify-between items-center mb-2'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-lg font-bold text-slate-700')
                    ui.badge('Live', color='indigo').props('outline') 
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': [], 'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#64748b'}},
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#f1f5f9'}}},
                    'series': [{'type': 'bar', 'data': [], 'barWidth': '40%', 'itemStyle': {'borderRadius': [4, 4, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-64')

            # --- ✨✨✨ 第四张卡片：服务器区域分布 (Top 5 + 其他) ✨✨✨ ---
            with ui.card().classes('w-full xl:w-1/3 p-6 shadow-md border-none rounded-xl bg-white flex flex-col'):
                ui.label('🌏 服务器分布').classes('text-lg font-bold text-slate-700 mb-2')
                
                # --- 1. 数据统计逻辑 ---
                from collections import Counter
                country_counter = Counter()
                
                if SERVERS_CACHE:
                    for s in SERVERS_CACHE:
                        try:
                            region_str = detect_country_group(s.get('name', ''), s)
                            if not region_str or region_str.strip() == "🏳️":
                                region_str = "🏳️ 未知区域"
                        except:
                            region_str = "🏳️ 未知区域"
                        country_counter[region_str] += 1
                else:
                    country_counter["暂无数据"] = 1

                # --- 2. Top 5 + "其他" 分组逻辑 ---
                sorted_counts = country_counter.most_common()
                chart_data = []
                
                top_5 = sorted_counts[:5]
                for region, count in top_5:
                    chart_data.append({'name': f"{region} ({count})", 'value': count})
                
                others_count = sum(count for _, count in sorted_counts[5:])
                if others_count > 0:
                    chart_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})

                # --- 3. ECharts 图表配置 (尺寸已调整) ---
                color_palette = [
                    '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', 
                    '#6366f1', '#ec4899', '#14b8a6', '#f97316'
                ]
                
                ui.echart({
                    'tooltip': {
                        'trigger': 'item',
                        'formatter': '{b}: <br/><b>{c} 台</b> ({d}%)'
                    },
                    'legend': {
                        'bottom': '0%',
                        'left': 'center',
                        'icon': 'circle',
                        'itemGap': 15,
                        # ✨ 修改 1：字体变大到 13
                        'textStyle': {'color': '#64748b', 'fontSize': 13}
                    },
                    'color': color_palette,
                    'series': [
                        {
                            'name': '服务器分布',
                            'type': 'pie',
                            # ✨ 修改 2：圆环变大变粗 (55% -> 85%)
                            'radius': ['35%', '75%'],
                            'center': ['50%', '45%'],
                            'avoidLabelOverlap': False,
                            'itemStyle': {
                                'borderRadius': 5,
                                'borderColor': '#fff',
                                'borderWidth': 2
                            },
                            'label': { 'show': False, 'position': 'center' },
                            'emphasis': {
                                'label': {
                                    'show': True,
                                    'fontSize': 18, # 中间高亮文字也稍微加大
                                    'fontWeight': 'bold',
                                    'color': '#334155'
                                },
                                'scale': True,
                                'scaleSize': 5
                            },
                            'labelLine': { 'show': False },
                            'data': chart_data
                        }
                    ]
                # ✨ 修改 3：容器高度增加到 h-80 (约320px)
                }).classes('w-full h-80')

        # === C. 底部地图区域 (保持不变) ===
        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-slate-900 overflow-hidden relative'):
                with ui.row().classes('w-full px-6 py-4 bg-slate-800/50 border-b border-gray-700 justify-between items-center z-10 relative'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public', color='blue-4').classes('text-xl')
                        ui.label('全球节点实景 (Global View)').classes('text-lg font-bold text-white')
                    DASHBOARD_REFS['map_info'] = ui.label('渲染中...').classes('text-xs text-gray-400')

                globe_data_list = []
                seen_locations = set()
                total_server_count = len(SERVERS_CACHE)

                flag_map_py = {
                    'CN':'China', 'HK':'Hong Kong', 'TW':'Taiwan', 'US':'United States', 'JP':'Japan', 
                    'KR':'South Korea', 'SG':'Singapore', 'RU':'Russia', 'DE':'Germany', 'GB':'United Kingdom'
                }

                for s in SERVERS_CACHE:
                    lat, lon = None, None
                    if 'lat' in s and 'lon' in s:
                        lat, lon = s['lat'], s['lon']
                    else:
                        coords = get_coords_from_name(s.get('name', ''))
                        if coords: lat, lon = coords[0], coords[1]
                    
                    if lat is not None and lon is not None:
                        coord_key = (round(lat, 2), round(lon, 2))
                        if coord_key not in seen_locations:
                            seen_locations.add(coord_key)
                            
                            flag_only = "📍"
                            country_name = s.get('_detected_region', '')
                            try:
                                full_group = detect_country_group(s.get('name', ''), s)
                                flag_only = full_group.split(' ')[0]
                                if not country_name and flag_only in flag_map_py:
                                    country_name = flag_map_py[flag_only]
                            except: pass
                            
                            globe_data_list.append({
                                'lat': lat, 'lon': lon, 'name': flag_only, 'country': country_name
                            })

                import json
                json_data = json.dumps(globe_data_list, ensure_ascii=False)
                
                ui.html(GLOBE_STRUCTURE, sanitize=False).classes('w-full h-[850px] overflow-hidden')
                ui.run_javascript(f'window.GLOBE_DATA = {json_data}; window.SERVER_TOTAL = {total_server_count};')
                ui.run_javascript(GLOBE_JS_LOGIC)
                DASHBOARD_REFS['map'] = None

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
                    # ✨✨✨批量修改 SSH 设置 (用户名/认证方式) ✨✨✨
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
    
# ================= [侧边栏渲染：艺术字标题版] =================
_current_dragged_group = None

@ui.refreshable
def render_sidebar_content():
    global _current_dragged_group

    # --- 通用 3D 按钮样式 ---
    btn_3d_style = (
        'w-full bg-white border border-gray-200 rounded-lg shadow-sm '
        'text-slate-700 font-medium px-3 py-2 '
        'transition-all duration-200 ease-out '
        'hover:shadow-md hover:-translate-y-0.5 hover:border-blue-300 hover:text-blue-600 '
        'active:translate-y-0 active:shadow-none active:bg-gray-50 active:scale-[0.98]'
    )
    
    # --- 1. 顶部固定区域 (带水印) ---
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0 relative overflow-hidden'):
        
        # ✨ 水印：位于右上角 ✨
        ui.label('X-Fusion').classes(
            'absolute top-2 right-6 text-[3rem] font-black text-slate-300 '
            'opacity-20 pointer-events-none -rotate-12 select-none z-0 tracking-tighter leading-tight'
        )

        # ✨✨✨ [修改] 艺术字标题 ✨✨✨
        # 使用 bg-clip-text 实现渐变色文字效果
        ui.label('X-Fusion Panel').classes(
            'text-2xl font-black mb-4 z-10 relative '
            'bg-gradient-to-r from-blue-600 via-purple-600 to-pink-500 bg-clip-text text-transparent '
            'tracking-wide drop-shadow-sm'
        )
        
        with ui.column().classes('w-full gap-2 z-10 relative'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes(btn_3d_style)
            ui.button('探针设置', icon='tune', on_click=render_probe_page).props('flat align=left').classes(btn_3d_style)
            ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(btn_3d_style)
            
    # --- 2. 列表区域 ---
    with ui.column().classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-slate-50'):
        
        # 功能按钮
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            func_btn_base = (
                'flex-grow text-xs font-bold text-white rounded-lg shadow-md '
                'transition-all duration-150 hover:-translate-y-0.5 hover:shadow-lg '
                'active:translate-y-0 active:shadow-sm active:scale-[0.98]'
            )
            ui.button('新建分组', icon='create_new_folder', on_click=open_create_group_dialog).props('dense unelevated').classes(f'bg-blue-600 hover:bg-blue-500 {func_btn_base}')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props('dense unelevated').classes(f'bg-green-600 hover:bg-green-500 {func_btn_base}')

        # --- A. 全部服务器 ---
        list_item_3d = (
            'w-full items-center justify-between p-3 border border-gray-200 rounded-xl mb-1 '
            'bg-white shadow-sm cursor-pointer group '
            'transition-all duration-200 '
            'hover:shadow-md hover:-translate-y-0.5 hover:border-blue-300 '
            'active:translate-y-0 active:shadow-none active:bg-gray-50 active:scale-[0.98]'
        )
        
        with ui.row().classes(list_item_3d).on('click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes('p-1.5 bg-blue-50 rounded-lg group-hover:bg-blue-100 transition-colors'):
                    ui.icon('dns', color='primary').classes('text-sm')
                ui.label('所有服务器').classes('font-bold text-slate-700')
            ui.badge(str(len(SERVERS_CACHE)), color='blue').props('rounded outline')

        # --- B. ✨✨✨ [修复] 自定义分组 ✨✨✨ ---
        custom_groups = ADMIN_CONFIG.get('custom_groups', [])
        if custom_groups:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
            for tag_group in custom_groups:
                # ✨✨✨ 修复：增加类型检查，防止因脏数据导致网页打不开 ✨✨✨
                tag_servers = [
                    s for s in SERVERS_CACHE 
                    if isinstance(s, dict) and (tag_group in s.get('tags', []) or s.get('group') == tag_group)
                ]
                try: tag_servers.sort(key=smart_sort_key)
                except: tag_servers.sort(key=lambda x: x.get('name', ''))

                is_open = tag_group in EXPANDED_GROUPS
                
                # 样式：移除 overflow-hidden，防止内容被遮挡
                group_card_cls = 'w-full border border-gray-200 rounded-xl mb-2 bg-white shadow-sm transition-all duration-300'
                
                # 关键修复：移除 .props('group')，只保留 expand-icon-toggle
                with ui.expansion('', icon='folder', value=is_open).classes(group_card_cls).props('expand-icon-toggle').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                    with exp.add_slot('header'):
                        header_cls = (
                            'w-full h-full items-center justify-between no-wrap cursor-pointer py-1 '
                            'hover:bg-gray-50 transition-all duration-200 active:bg-gray-100 active:scale-[0.98]'
                        )
                        with ui.row().classes(header_cls).on('click', lambda _, g=tag_group: refresh_content('TAG', g)):
                            ui.label(tag_group).classes('flex-grow font-bold text-slate-700 truncate pl-2')
                            ui.button(icon='settings', on_click=lambda _, g=tag_group: open_combined_group_management(g)).props('flat dense round size=xs color=grey-4').classes('hover:text-blue-500').on('click.stop')
                            ui.badge(str(len(tag_servers)), color='orange' if not tag_servers else 'grey').props('rounded outline')
                    
                    with ui.column().classes('w-full gap-1 p-1 bg-gray-50/50'):
                        for s in tag_servers:
                            sub_item_cls = (
                                'w-full justify-between items-center p-2 pl-3 rounded-lg border border-transparent '
                                'hover:bg-white hover:shadow-sm hover:border-gray-200 transition-all duration-200 cursor-pointer '
                                'active:scale-[0.97]'
                            )
                            with ui.row().classes(sub_item_cls).on('click', lambda _, s=s: refresh_content('SINGLE', s)):
                                ui.label(s['name']).classes('text-xs font-medium text-slate-600 truncate flex-grow')
                                ui.button(icon='edit', on_click=lambda _, idx=SERVERS_CACHE.index(s): open_server_dialog(idx)).props('flat dense round size=xs color=grey-4').classes('hover:text-blue-600').on('click.stop')

        # --- C. 区域分组 ---
        ui.label('区域分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
        
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s)
            if c_group in ['默认分组', '自动注册', '自动导入', '未分组', '', None]: c_group = '🏳️ 其他地区'
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)
        
        saved_order = ADMIN_CONFIG.get('group_order', [])
        def region_sort_key(name):
            if name in saved_order: return saved_order.index(name)
            return 9999
        sorted_regions = sorted(country_buckets.keys(), key=region_sort_key)

        def on_drag_start(e, name):
            global _current_dragged_group
            _current_dragged_group = name

        async def on_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(sorted_regions)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group)
                    item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name)
                    current_list.insert(new_idx, item)
                    ADMIN_CONFIG['group_order'] = current_list
                    await save_admin_config()
                    _current_dragged_group = None
                    render_sidebar_content.refresh()
            except: pass

        # ---------------- 渲染区域列表 ----------------
        with ui.column().classes('w-full gap-2 pb-4'):
            for c_name in sorted_regions:
                c_servers = country_buckets[c_name]
                try: c_servers.sort(key=smart_sort_key)
                except: c_servers.sort(key=lambda x: x.get('name', ''))
                is_open = c_name in EXPANDED_GROUPS

                with ui.element('div').classes('w-full') \
                    .on('dragover.prevent', lambda _: None) \
                    .on('drop', lambda e, n=c_name: on_drop(e, n)):

                    group_card_cls = (
                        'w-full border border-gray-200 rounded-xl bg-white shadow-sm transition-all duration-300 '
                        'hover:border-blue-200 hover:shadow-md'
                    )
                    
                    with ui.expansion('', icon=None, value=is_open).classes(group_card_cls).props('expand-icon-toggle').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            header_cls = (
                                'w-full h-full items-center justify-between no-wrap py-2 cursor-pointer '
                                'group/header transition-all duration-200 active:bg-gray-50 active:scale-[0.98]'
                            )
                            with ui.row().classes(header_cls).on('click', lambda _, g=c_name: refresh_content('COUNTRY', g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                                    
                                    ui.icon('drag_indicator').props('draggable="true"').classes(
                                        'cursor-move text-gray-300 hover:text-blue-500 p-1 rounded transition-colors group-hover/header:text-gray-400'
                                    ).on('dragstart', lambda e, n=c_name: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    
                                    with ui.row().classes('items-center gap-2 flex-grow'):
                                        flag = c_name.split(' ')[0] if ' ' in c_name else '🏳️'
                                        ui.label(flag).classes('text-lg filter drop-shadow-sm')
                                        display_name = c_name.split(' ')[1] if ' ' in c_name else c_name
                                        ui.label(display_name).classes('font-bold text-slate-700 truncate')
                                
                                with ui.row().classes('items-center gap-2 pr-2').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='edit_note', on_click=lambda _, s=c_servers, t=c_name: open_bulk_edit_dialog(s, f"区域: {t}")).props('flat dense round size=xs color=grey-4').classes('hover:text-blue-600').tooltip('批量管理')
                                    ui.badge(str(len(c_servers)), color='green').props('rounded outline').classes('font-mono font-bold')

                        with ui.column().classes('w-full gap-1 p-1 bg-slate-50/80 border-t border-gray-100'):
                            for s in c_servers:
                                sub_item_cls = (
                                    'w-full justify-between items-center p-2 pl-4 rounded-lg border border-transparent '
                                    'hover:bg-white hover:shadow-sm hover:border-blue-100 transition-all duration-200 cursor-pointer '
                                    'active:scale-[0.97] active:bg-gray-100'
                                )
                                with ui.row().classes(sub_item_cls).on('click', lambda _, s=s: refresh_content('SINGLE', s)):
                                    ui.label(s['name']).classes('text-xs font-medium text-slate-600 truncate flex-grow')
                                    with ui.row().classes('gap-1 items-center'):
                                        ui.button(icon='edit', on_click=lambda _, idx=SERVERS_CACHE.index(s): open_server_dialog(idx)).props('flat dense round size=xs color=grey-4').classes('hover:text-blue-600').on('click.stop')

    # --- 3. 底部功能区 ---
    with ui.column().classes('w-full p-2 border-t mt-auto mb-4 gap-2 bg-white z-10 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]'):
        bottom_btn_3d = (
            'w-full text-slate-600 text-xs font-bold bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 '
            'transition-all duration-200 hover:bg-white hover:shadow-sm hover:border-slate-300 hover:text-slate-800 '
            'active:translate-y-0 active:bg-slate-100 active:scale-[0.98]'
        )
        
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(bottom_btn_3d)
        
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


# ================= 0. 认证检查辅助函数 =================
def check_auth(request: Request):
    """
    检查用户是否已登录
    """
    return app.storage.user.get('authenticated', False)


# ================= [本地化版] 主页入口 =================
@ui.page('/')
def main_page(request: Request):
    # ================= 1. 注入全局资源与样式 =================
    
    # 1.1 xterm.js 终端依赖
    ui.add_head_html('<link rel="stylesheet" href="/static/xterm.css" />')
    ui.add_head_html('<script src="/static/xterm.js"></script>')
    ui.add_head_html('<script src="/static/xterm-addon-fit.js"></script>')

    # ✨✨✨ [修改] 2D 平面地图依赖 (ECharts) ✨✨✨
    # 删除了旧的 globe.gl，改为引入 ECharts
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')

    # 1.2 核心样式注入
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            body { 
                font-family: 'Noto Sans SC', "Roboto", "Helvetica", "Arial", sans-serif, "Noto Color Emoji"; 
                background-color: #f8fafc; 
            }
            .nicegui-connection-lost { 
                display: none !important; 
                opacity: 0 !important;
                pointer-events: none !important;
            }
        </style>
    ''')

    # ================= 2. 基础认证检查 =================
    if not check_auth(request): 
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

    # ================= 4. UI 构建 =================
    
    # 左侧抽屉
    with ui.left_drawer(value=True, fixed=True).classes('bg-gray-50 border-r').props('width=400 bordered') as drawer:
        render_sidebar_content()

    # 顶部导航栏
    with ui.header().classes('bg-slate-900 text-white h-14 shadow-md'):
        with ui.row().classes('w-full items-center justify-between'):
            
            # 左侧
            with ui.row().classes('items-center gap-2'):
                # 使用 drawer.toggle() 切换侧边栏
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense color=white')
                
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2 tracking-wide')
                ui.label(f"[{display_ip}]").classes('text-xs text-gray-400 font-mono pt-1 hidden sm:block')

            # 右侧
            with ui.row().classes('items-center gap-2 mr-2'):
                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round').tooltip('点击复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded')
                
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense').tooltip('退出登录')

    # 主内容区域
    global content_container
    content_container = ui.column().classes('w-full h-full pl-4 pr-4 pt-4 overflow-y-auto bg-slate-50')
    
    # ================= 5. 启动后台任务 =================
    async def restore_last_view():
        last_scope = app.storage.user.get('last_view_scope', 'DASHBOARD')
        last_data_id = app.storage.user.get('last_view_data', None)
        target_data = last_data_id

        if last_scope == 'SINGLE' and last_data_id:
            target_data = next((s for s in SERVERS_CACHE if s['url'] == last_data_id), None)
            if not target_data:
                last_scope = 'DASHBOARD'

        if last_scope == 'DASHBOARD':
            await load_dashboard_stats()
        elif last_scope == 'PROBE':
            await render_probe_page()
        elif last_scope == 'SUBS':
            await load_subs_view()
        else:
            await refresh_content(last_scope, target_data)
            
        logger.info(f"♻️ 自动恢复视图: {last_scope}")

    ui.timer(0.1, lambda: asyncio.create_task(restore_last_view()), once=True)
    
    logger.info("✅ UI 已就绪")
    



# ================= TG 报警模块 =================
ALERT_CACHE = {}     # 记录服务器确认后的状态 (Online/Offline)
FAILURE_COUNTS = {}  # ✨新增：记录连续失败次数

async def send_telegram_message(text):
    """发送 Telegram 消息"""
    token = ADMIN_CONFIG.get('tg_bot_token')
    chat_id = ADMIN_CONFIG.get('tg_chat_id')
    
    if not token or not chat_id: return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    def _do_req():
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"❌ TG 发送失败: {e}")

    await run.io_bound(_do_req)
# ================= 优化后的监控任务 (防误报 + 历史记录版) =================
async def job_monitor_status():
    """
    监控任务：每分钟检查一次服务器状态
    1. 限制并发数
    2. 引入失败计数器
    3. [新增] 自动补录历史数据
    """
    # 限制并发数为 5
    sema = asyncio.Semaphore(5)
    
    # 定义报警阈值：连续失败 3 次才报警
    FAILURE_THRESHOLD = 3 
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _check_single_server(srv):
        async with sema:
            await asyncio.sleep(0.1) # 让出 CPU
            
            res = await get_server_status(srv)
            name = srv.get('name', 'Unknown')
            url = srv['url']
            
            # ✨✨✨ [新增] 如果不是探针机器(探针已经在push接口记过了)，则在这里补录历史 ✨✨✨
            if not srv.get('probe_installed'):
                 if res and 'pings' in res:
                     record_ping_history(url, res['pings'])

            # 如果没配 TG，后面的报警逻辑就跳过，但上面的记录逻辑不能跳
            if not ADMIN_CONFIG.get('tg_bot_token'): return

            # 清洗 IP，只显示纯 IP
            display_ip = url.split('://')[-1].split(':')[0]
            
            # 判断当前物理探测状态
            is_physically_online = False
            if isinstance(res, dict) and res.get('status') == 'online':
                is_physically_online = True
            
            # --- 核心防抖逻辑 ---
            if is_physically_online:
                # 1. 如果当前检测在线，直接重置失败计数器
                FAILURE_COUNTS[url] = 0
                
                # 2. 检查是否需要发“恢复通知”
                if ALERT_CACHE.get(url) == 'offline':
                    msg = (
                        f"🟢 **恢复：服务器已上线**\n\n"
                        f"🖥️ **名称**: `{name}`\n"
                        f"🔗 **地址**: `{display_ip}`\n"
                        f"🕒 **时间**: `{current_time}`"
                    )
                    logger.info(f"🔔 [恢复] {name} 已上线")
                    asyncio.create_task(send_telegram_message(msg))
                    ALERT_CACHE[url] = 'online'
            else:
                # 1. 如果当前检测离线，计数器 +1
                current_count = FAILURE_COUNTS.get(url, 0) + 1
                FAILURE_COUNTS[url] = current_count
                
                # 2. 只有计数器达到阈值，才报警
                if current_count >= FAILURE_THRESHOLD:
                    if ALERT_CACHE.get(url) != 'offline':
                        msg = (
                            f"🔴 **警告：服务器离线**\n\n"
                            f"🖥️ **名称**: `{name}`\n"
                            f"🔗 **地址**: `{display_ip}`\n"
                            f"🕒 **时间**: `{current_time}`\n"
                            f"⚠️ **提示**: 连续监测失败 {current_count} 次"
                        )
                        logger.warning(f"🔔 [报警] {name} 确认离线 (重试{current_count}次)")
                        asyncio.create_task(send_telegram_message(msg))
                        ALERT_CACHE[url] = 'offline'

    # 创建所有任务并执行
    tasks = [_check_single_server(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)


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
    data_changed = False
    
    # 1. ✨ 动态生成所有已知国旗列表 (防止漏判)
    known_flags = []
    for val in AUTO_COUNTRY_MAP.values():
        icon = val.split(' ')[0] # 提取 "🇺🇸", "🇯🇵" 等
        if icon and icon not in known_flags:
            known_flags.append(icon)
    
    for s in SERVERS_CACHE:
        old_name = s.get('name', '')
        new_name = old_name

        # --- 🧹 步骤 A: 强力清洗白旗 (修复之前的 Bug) ---
        # 如果名字以 "🏳️ " 开头，且后面还有内容，直接把白旗切掉
        if new_name.startswith('🏳️ ') or new_name.startswith('🏳️'):
            # 只有当名字里除了白旗还有别的东西时才删，防止名字被删空
            if len(new_name) > 2:
                new_name = new_name.replace('🏳️', '').strip()
                logger.info(f"🧹 [清洗白旗] {old_name} -> {new_name}")

        # --- 🔍 步骤 B: 正常的 GeoIP 修正逻辑 ---
        # 检查现在的名字里有没有国旗
        has_flag = any(flag in new_name for flag in known_flags)
        
        if not has_flag:
            try:
                # 只有没国旗的时候，才去查 IP
                geo = await run.io_bound(fetch_geo_from_ip, s['url'])
                if geo:
                    s['lat'] = geo[0]; s['lon'] = geo[1]; s['_detected_region'] = geo[2]
                    
                    flag_prefix = get_flag_for_country(geo[2])
                    flag_icon = flag_prefix.split(' ')[0]
                    
                    # 加上正确的国旗
                    if flag_icon and flag_icon not in new_name:
                        new_name = f"{flag_icon} {new_name}"
                        logger.info(f"✨ [自动修正] {old_name} -> {new_name}")
            except: pass
        
        # 如果名字变了，标记需要保存
        if new_name != old_name:
            s['name'] = new_name
            data_changed = True

    if data_changed:
        await save_servers()
        await refresh_dashboard_ui()
        try: render_sidebar_content.refresh()
        except: pass
        safe_notify("✅ 已清理白旗并修正服务器名称", "positive")
    else:
        logger.info("✅ 名称检查完毕，无需修正")
        
# 3. 初始化调度器
scheduler = AsyncIOScheduler()

# 4. 系统启动序列
async def startup_sequence():
    global PROCESS_POOL
    # ✨ 初始化进程池 (4核) - 专门处理 Ping 等 CPU/阻塞任务
    PROCESS_POOL = ProcessPoolExecutor(max_workers=4)
    logger.info("🚀 进程池已启动 (ProcessPoolExecutor)")

    # ✨ 添加定时任务
    # 1. 流量同步 (3小时一次)
    scheduler.add_job(job_sync_all_traffic, 'interval', hours=3, id='traffic_sync', replace_existing=True, max_instances=1)
    
    # 2. ✨✨✨ 新增：服务器状态监控与报警 (60秒一次) ✨✨✨
    scheduler.add_job(job_monitor_status, 'interval', seconds=60, id='status_monitor', replace_existing=True, max_instances=1)
    
    scheduler.start()
    logger.info("🕒 APScheduler 定时任务已启动")

    # ✨ 开机立即执行一次 (作为初始化)
    asyncio.create_task(job_sync_all_traffic())
    asyncio.create_task(job_check_geo_ip())
    
    # 首次运行填充状态缓存，避免刚开机就疯狂报警
    async def init_alert_cache():
        await asyncio.sleep(5) # 等待几秒让系统稳一下
        if ADMIN_CONFIG.get('tg_bot_token'):
            logger.info("🛡️ 正在初始化监控状态缓存...")
            await job_monitor_status()
            
    asyncio.create_task(init_alert_cache())

# 注册启动与关闭事件
app.on_startup(startup_sequence)
app.on_shutdown(lambda: PROCESS_POOL.shutdown(wait=False) if PROCESS_POOL else None)



# ==========================================
# ✨✨✨飞线优化+定位+高亮地图✨✨✨
# ==========================================

# 1. 全局地图名称映射表 
MATCH_MAP = {
    # --- 南美 ---
    '🇨🇱': 'Chile', 'CHILE': 'Chile',
    '🇧🇷': 'Brazil', 'BRAZIL': 'Brazil', 'BRA': 'Brazil', 'SAO PAULO': 'Brazil',
    '🇦🇷': 'Argentina', 'ARGENTINA': 'Argentina', 'ARG': 'Argentina', # ⚠️已移除 'AR'，防止匹配 ARM
    '🇨🇴': 'Colombia', 'COLOMBIA': 'Colombia', 'COL': 'Colombia',
    '🇵🇪': 'Peru', 'PERU': 'Peru',
    
    # --- 北美 ---
    '🇺🇸': 'United States', 'USA': 'United States', 'UNITED STATES': 'United States', 'AMERICA': 'United States',
    '🇨🇦': 'Canada', 'CANADA': 'Canada', 'CAN': 'Canada',
    '🇲🇽': 'Mexico', 'MEXICO': 'Mexico', 'MEX': 'Mexico',
    
    # --- 欧洲 ---
    '🇬🇧': 'United Kingdom', 'UK': 'United Kingdom', 'GB': 'United Kingdom', 'UNITED KINGDOM': 'United Kingdom', 'LONDON': 'United Kingdom',
    '🇩🇪': 'Germany', 'GERMANY': 'Germany', 'DEU': 'Germany', 'FRANKFURT': 'Germany',
    '🇫🇷': 'France', 'FRANCE': 'France', 'FRA': 'France', 'PARIS': 'France',
    '🇳🇱': 'Netherlands', 'NETHERLANDS': 'Netherlands', 'NLD': 'Netherlands', 'AMSTERDAM': 'Netherlands',
    '🇷🇺': 'Russia', 'RUSSIA': 'Russia', 'RUS': 'Russia',
    '🇮🇹': 'Italy', 'ITALY': 'Italy', 'ITA': 'Italy', 'MILAN': 'Italy',
    '🇪🇸': 'Spain', 'SPAIN': 'Spain', 'ESP': 'Spain', 'MADRID': 'Spain',
    '🇵🇱': 'Poland', 'POLAND': 'Poland', 'POL': 'Poland',
    '🇺🇦': 'Ukraine', 'UKRAINE': 'Ukraine', 'UKR': 'Ukraine',
    '🇸🇪': 'Sweden', 'SWEDEN': 'Sweden', 'SWE': 'Sweden',
    '🇨🇭': 'Switzerland', 'SWITZERLAND': 'Switzerland', 'CHE': 'Switzerland',
    '🇹🇷': 'Turkey', 'TURKEY': 'Turkey', 'TUR': 'Turkey',
    '🇮🇪': 'Ireland', 'IRELAND': 'Ireland', 'IRL': 'Ireland',
    '🇫🇮': 'Finland', 'FINLAND': 'Finland', 'FIN': 'Finland',
    '🇳🇴': 'Norway', 'NORWAY': 'Norway', 'NOR': 'Norway',
    '🇦🇹': 'Austria', 'AUSTRIA': 'Austria', 'AUT': 'Austria',
    '🇧🇪': 'Belgium', 'BELGIUM': 'Belgium', 'BEL': 'Belgium',
    '🇵🇹': 'Portugal', 'PORTUGAL': 'Portugal', 'PRT': 'Portugal',
    '🇬🇷': 'Greece', 'GREECE': 'Greece', 'GRC': 'Greece',
    
    # --- 亚太 ---
    '🇨🇳': 'China', 'CHINA': 'China', 'CHN': 'China', 'CN': 'China',
    '🇭🇰': 'China', 'HONG KONG': 'China', 'HK': 'China',
    '🇲🇴': 'China', 'MACAU': 'China', 'MO': 'China',
    '🇹🇼': 'China', 'TAIWAN': 'China', 'TW': 'China',
    '🇯🇵': 'Japan', 'JAPAN': 'Japan', 'JPN': 'Japan', 'TOKYO': 'Japan', 'OSAKA': 'Japan',
    '🇰🇷': 'South Korea', 'KOREA': 'South Korea', 'KOR': 'South Korea', 'SEOUL': 'South Korea',
    '🇸🇬': 'Singapore', 'SINGAPORE': 'Singapore', 'SGP': 'Singapore', 'SG': 'Singapore',
    '🇮🇳': 'India', 'INDIA': 'India', 'IND': 'India', 'MUMBAI': 'India',
    '🇦🇺': 'Australia', 'AUSTRALIA': 'Australia', 'AUS': 'Australia', 'SYDNEY': 'Australia',
    '🇳🇿': 'New Zealand', 'NEW ZEALAND': 'New Zealand', 'NZL': 'New Zealand',
    '🇻🇳': 'Vietnam', 'VIETNAM': 'Vietnam', 'VNM': 'Vietnam',
    '🇹🇭': 'Thailand', 'THAILAND': 'Thailand', 'THA': 'Thailand', 'BANGKOK': 'Thailand',
    '🇲🇾': 'Malaysia', 'MALAYSIA': 'Malaysia', 'MYS': 'Malaysia',
    '🇮🇩': 'Indonesia', 'INDONESIA': 'Indonesia', 'IDN': 'Indonesia', 'JAKARTA': 'Indonesia',
    '🇵🇭': 'Philippines', 'PHILIPPINES': 'Philippines', 'PHL': 'Philippines',
    '🇰🇭': 'Cambodia', 'CAMBODIA': 'Cambodia', 'KHM': 'Cambodia',
    
    # --- 中东/非洲 ---
    '🇦🇪': 'United Arab Emirates', 'UAE': 'United Arab Emirates', 'DUBAI': 'United Arab Emirates',
    '🇿🇦': 'South Africa', 'SOUTH AFRICA': 'South Africa', 'ZAF': 'South Africa',
    '🇸🇦': 'Saudi Arabia', 'SAUDI ARABIA': 'Saudi Arabia', 'SAU': 'Saudi Arabia',
    '🇮🇱': 'Israel', 'ISRAEL': 'Israel', 'ISR': 'Israel',
    '🇪🇬': 'Egypt', 'EGYPT': 'Egypt', 'EGY': 'Egypt',
    '🇮🇷': 'Iran', 'IRAN': 'Iran', 'IRN': 'Iran',
    '🇳🇬': 'Nigeria', 'NIGERIA': 'Nigeria', 'NGA': 'Nigeria'
}

# 2. 辅助函数
def get_echarts_region_name(name_raw):
    if not name_raw: return None
    name = name_raw.upper()
    # 按长度排序，优先匹配 Emoji 和 长单词
    sorted_keys = sorted(MATCH_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in name: return MATCH_MAP[key]
    return None
    
# ================= PC 端详情弹窗 =================
def open_dark_server_detail(server_conf):
    try:
        # 定义 UI 样式常量
        LABEL_STYLE = 'text-gray-400 text-sm font-medium'
        VALUE_STYLE = 'text-gray-200 font-mono text-sm font-bold'
        SECTION_TITLE = 'text-gray-200 text-base font-black mb-4 flex items-center gap-2'
        CARD_BG = 'bg-[#161b22]' 
        BORDER_STYLE = 'border border-[#30363d]'
        
        # ✨ 弹窗高度
        with ui.dialog() as d, ui.card().classes('p-0 overflow-hidden flex flex-col bg-[#0d1117] shadow-2xl').style('width: 1000px; max-width: 95vw; border-radius: 12px;'):
            
            # --- 1. 顶部标题栏 ---
            with ui.row().classes('w-full items-center justify-between p-4 bg-[#161b22] border-b border-[#30363d] flex-shrink-0'):
                with ui.row().classes('items-center gap-3'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl')
                    ui.label(server_conf['name']).classes('text-lg font-bold text-white')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey-5')

            # --- 2. 内容滚动区 (适当减少高度) ---
            with ui.scroll_area().classes('w-full flex-grow p-6').style('height: 60vh;'):
                refs = {}
                
                # 第一行：左右对齐容器 (items-stretch 确保高度一致)
                with ui.row().classes('w-full gap-6 no-wrap items-stretch'):
                    
                    # A. 资源使用情况 (左侧)
                    with ui.column().classes(f'flex-1 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('资源使用情况').classes(SECTION_TITLE)
                        
                        def progress_block(label, key, icon, color_class):
                            with ui.column().classes('w-full gap-1'):
                                with ui.row().classes('w-full justify-between items-end'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.icon(icon).classes('text-gray-500 text-xs')
                                        ui.label(label).classes(LABEL_STYLE)
                                    refs[f'{key}_pct'] = ui.label('0.0%').classes('text-gray-400 text-xs font-mono')
                                
                                refs[f'{key}_bar'] = ui.linear_progress(value=0, show_value=False).props(f'color={color_class} track-color=grey-9').classes('h-1.5 rounded-full')
                                with ui.row().classes('w-full justify-end'):
                                    # ✨ 显示已用数值：8.13 GB / 48.38 GB
                                    refs[f'{key}_val'] = ui.label('0 GB / 0 GB').classes('text-[11px] text-gray-500 font-mono mt-1')

                        progress_block('CPU', 'cpu', 'settings_suggest', 'blue-5')
                        progress_block('記憶体', 'mem', 'memory', 'green-5')
                        progress_block('磁碟', 'disk', 'storage', 'purple-5')

                    # B. 系统资讯 (右侧)
                    with ui.column().classes(f'w-[400px] p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('系统资讯').classes(SECTION_TITLE)
                        
                        def info_line(label, icon, key):
                            with ui.row().classes('w-full items-center justify-between py-3 border-b border-[#30363d] last:border-0'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon(icon).classes('text-gray-500 text-sm')
                                    ui.label(label).classes(LABEL_STYLE)
                                refs[key] = ui.label('Loading...').classes(VALUE_STYLE)

                        info_line('作业系统', 'laptop_windows', 'os')
                        info_line('架构', 'developer_board', 'arch') # ✨ 显示 AMD / ARM
                        info_line('虚拟化', 'cloud_queue', 'virt')
                        info_line('在线时长', 'timer', 'uptime')

                # 第二行：三网实时延迟卡片
                with ui.row().classes('w-full gap-4 mt-6'):
                    def ping_card(name, color, key):
                        with ui.column().classes(f'flex-1 p-4 rounded-xl {CARD_BG} {BORDER_STYLE} border-l-4 border-l-{color}-500'):
                            with ui.row().classes('w-full justify-between items-center mb-1'):
                                ui.label(name).classes(f'text-{color}-400 text-xs font-bold')
                            with ui.row().classes('items-baseline gap-1'):
                                refs[f'{key}_cur'] = ui.label('--').classes('text-2xl font-black text-white font-mono')
                                ui.label('ms').classes('text-gray-500 text-[10px]')
                    
                    ping_card('安徽电信', 'blue', 'ping_ct')
                    ping_card('安徽联通', 'orange', 'ping_cu')
                    ping_card('安徽移动', 'green', 'ping_cm')

                # --- 网络质量趋势图 ---
                with ui.column().classes(f'w-full mt-6 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        ui.label('网络质量趋势').classes('text-gray-200 text-sm font-bold')
                        with ui.tabs().props('dense no-caps indicator-color=blue active-color=blue').classes('bg-[#0d1117] rounded-lg p-1') as chart_tabs:
                            ui.tab('real', label='实时').classes('px-4 text-xs')
                            ui.tab('1h', label='1小时').classes('px-4 text-xs')
                            ui.tab('3h', label='3小时').classes('px-4 text-xs')
                        chart_tabs.set_value('real')

                    chart = ui.echart({
                        'backgroundColor': 'transparent',
                        'color': ['#3b82f6', '#f97316', '#22c55e'], 
                        'legend': { 'data': ['电信', '联通', '移动'], 'textStyle': { 'color': '#94a3b8' }, 'top': 0 },
                        'grid': { 'left': '1%', 'right': '1%', 'bottom': '5%', 'top': '15%', 'containLabel': True },
                        'xAxis': { 'type': 'category', 'boundaryGap': False, 'axisLabel': { 'color': '#64748b' } },
                        'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': '#30363d' } }, 'axisLabel': { 'color': '#64748b' } },
                        'series': [{'name': n, 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}} for n in ['电信','联通','移动']]
                    }).classes('w-full h-64') # 适当减少图表高度

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})

                        # 资源更新
                        refs['cpu_pct'].set_text(f"{status.get('cpu_usage', 0)}%")
                        refs['cpu_bar'].set_value(status.get('cpu_usage', 0) / 100)
                        refs['cpu_val'].set_text(f"{status.get('cpu_cores', 1)} Cores")

                        mem_p, mem_t = status.get('mem_usage', 0), status.get('mem_total', 0)
                        refs['mem_pct'].set_text(f"{mem_p}%")
                        refs['mem_bar'].set_value(mem_p / 100)
                        refs['mem_val'].set_text(f"{round(mem_t * (mem_p / 100), 2)} GB / {mem_t} GB")

                        disk_p, disk_t = status.get('disk_usage', 0), status.get('disk_total', 0)
                        refs['disk_pct'].set_text(f"{disk_p}%")
                        refs['disk_bar'].set_value(disk_p / 100)
                        refs['disk_val'].set_text(f"{round(disk_t * (disk_p / 100), 2)} GB / {disk_t} GB")

                        # 系统资讯 (AMD/ARM 架构逻辑)
                        raw_arch = static.get('arch', '').lower()
                        display_arch = "AMD" if "x86" in raw_arch or "amd" in raw_arch else "ARM" if "arm" in raw_arch or "aarch" in raw_arch else raw_arch.upper()
                        refs['os'].set_text(static.get('os', 'Linux'))
                        refs['arch'].set_text(display_arch)
                        refs['virt'].set_text(static.get('virt', 'kvm'))
                        
                        # 在线时长 (绿色)
                        uptime_str = str(status.get('uptime', '-')).replace('up ', '').replace('days', '天').replace('hours', '时').replace('minutes', '分')
                        refs['uptime'].set_text(uptime_str)
                        refs['uptime'].classes('text-green-500')

                        # 延迟卡片
                        pings = status.get('pings', {})
                        refs['ping_ct_cur'].set_text(str(pings.get('电信', 'N/A')))
                        refs['ping_cu_cur'].set_text(str(pings.get('联通', 'N/A')))
                        refs['ping_cm_cur'].set_text(str(pings.get('移动', 'N/A')))

                        # 趋势图历史数据同步
                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            duration = 600 if current_mode == 'real' else 3600 if current_mode == '1h' else 10800
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            if sliced:
                                chart.options['xAxis']['data'] = [p['time_str'] for p in sliced]
                                chart.options['series'][0]['data'] = [p['ct'] for p in sliced]
                                chart.options['series'][1]['data'] = [p['cu'] for p in sliced]
                                chart.options['series'][2]['data'] = [p['cm'] for p in sliced]
                                chart.update()
                    except: pass

                # 绑定切换事件
                chart_tabs.on_value_change(update_dark_detail)

            # 3. 底部版权
            with ui.row().classes('w-full justify-center p-2 bg-[#161b22] border-t border-[#30363d]'):
                ui.label('Powered by X-Fusion Monitor').classes('text-[10px] text-gray-600 font-mono italic')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())
    except Exception as e:
        print(f"PC Detail Error: {e}")
        
# ================= 全局变量 =================
# 用于记录当前探针页面选中的标签，防止刷新重置
CURRENT_PROBE_TAB = 'ALL' 

# ================= 移动端检测辅助函数 =================
def is_mobile_device(request: Request) -> bool:
    """通过 User-Agent 判断是否为移动设备"""
    user_agent = request.headers.get('user-agent', '').lower()
    mobile_keywords = [
        'android', 'iphone', 'ipad', 'iemobile', 
        'opera mini', 'mobile', 'harmonyos'
    ]
    return any(keyword in user_agent for keyword in mobile_keywords)

# ================= 核心：/status 统一入口 =================
@ui.page('/status')
async def status_page_router(request: Request):
    """
    路由分发器：
    1. 检测设备类型
    2. 手机端调用 render_mobile_status_page()
    3. 电脑端调用 render_desktop_status_page()
    """
    if is_mobile_device(request):
        # 针对手机进行极简渲染，防止硬件加速导致的浏览器崩溃
        await render_mobile_status_page()
    else:
        # 恢复 V30 版本的酷炫地图大屏显示
        await render_desktop_status_page()
        
# ================= 电脑端大屏显示 =================        
async def render_desktop_status_page():
    global CURRENT_PROBE_TAB
    
    # 引入地图依赖
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
    
    # ✨✨✨ [Win国旗修复] 引入 Google Noto Color Emoji 字体 ✨✨✨
    ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">')
    
    ui.add_head_html('''
        <style>
            body { 
                background-color: #0b1121; 
                color: #e2e8f0; 
                overflow: hidden; 
                margin: 0;
                /* ✨✨✨ [Win国旗修复] 强制 CSS 优先使用彩色 Emoji 字体 ✨✨✨ */
                font-family: "Noto Color Emoji", "Segoe UI Emoji", "Apple Color Emoji", "Noto Sans SC", sans-serif;
            }
            .status-card { 
                background: #1e293b; 
                border: 1px solid rgba(255,255,255,0.05);
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
                transition: border-color 0.3s, box-shadow 0.3s;
            }
            .status-card:hover { border-color: #3b82f6; transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5); }
            .offline-card {
                border-color: rgba(239, 68, 68, 0.6) !important;
                background-image: repeating-linear-gradient(45deg, rgba(239, 68, 68, 0.05) 0px, rgba(239, 68, 68, 0.05) 10px, transparent 10px, transparent 20px) !important;
                box-shadow: 0 0 15px rgba(239, 68, 68, 0.15) !important;
            }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
            .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            .prog-bar { transition: width 0.5s ease-out; }
        </style>
    ''')

    # --- 准备地图数据 ---
    server_points = []; active_regions = set(); seen_flags = set(); online_count = 0
    CITY_COORDS_FIX = { '巴淡': (-6.20, 106.84), 'Batam': (-6.20, 106.84), '雅加达': (-6.20, 106.84), 'Dubai': (25.20, 55.27), 'Frankfurt': (50.11, 8.68), 'Amsterdam': (52.36, 4.90), 'San Jose': (37.33, -121.88), 'Phoenix': (33.44, -112.07) }
    from collections import Counter; country_counter = Counter()
    for s in SERVERS_CACHE:
        if s.get('_status') == 'online': online_count += 1
        c_name = get_echarts_region_name(s.get('name', ''))
        if not c_name: c_name = s.get('_detected_region', '')
        if c_name and c_name.upper() in MATCH_MAP: c_name = MATCH_MAP[c_name.upper()]
        if c_name: active_regions.add(c_name)
        lat, lon = None, None
        for city_key, (c_lat, c_lon) in CITY_COORDS_FIX.items():
            if city_key in s.get('name', ''): lat, lon = c_lat, c_lon; break
        if not lat:
            if 'lat' in s: lat, lon = s['lat'], s['lon']
            else: 
                coords = get_coords_from_name(s.get('name', ''))
                if coords: lat, lon = coords[0], coords[1]
        if lat and lon:
            flag = "📍"; 
            try: flag = detect_country_group(s['name'], s).split(' ')[0]
            except: pass
            region_name = detect_country_group(s['name'], s); country_counter[region_name] += 1
            if flag not in seen_flags: seen_flags.add(flag); server_points.append({'name': flag, 'value': [lon, lat]})
    
    chart_data = json.dumps({'points': server_points, 'regions': list(active_regions)}, ensure_ascii=False)
    pie_data = []
    sorted_counts = country_counter.most_common(5)
    for k, v in sorted_counts: pie_data.append({'name': f"{k} ({v})", 'value': v})
    others = sum(country_counter.values()) - sum(x[1] for x in sorted_counts)
    if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})

    # --- 辅助：获取标签栏分组 ---
    def get_probe_groups():
        groups_list = ['ALL']
        customs = ADMIN_CONFIG.get('probe_custom_groups', [])
        groups_list.extend(customs) 
        return groups_list

    header_refs = {}

    # --- 上半部分：地图 ---
    with ui.column().classes('w-full h-[35vh] relative p-0 gap-0 bg-[#0B1121]'):
        with ui.column().classes('absolute top-6 left-8 z-50 gap-1'):
            with ui.row().classes('items-center gap-3'):
                ui.icon('public', color='blue').classes('text-3xl drop-shadow-[0_0_10px_rgba(59,130,246,0.8)]')
                ui.label('X-Fusion Status').classes('text-2xl font-black text-white tracking-wide')
            with ui.row().classes('gap-4 text-sm font-bold font-mono pl-1'):
                with ui.row().classes('items-center gap-1'):
                    ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-[0_0_5px_rgba(34,197,94,0.8)]')
                    header_refs['online_count'] = ui.label('在线: --').classes('text-slate-300')
                with ui.row().classes('items-center gap-1'):
                    ui.icon('language').classes('text-blue-400 text-xs')
                    header_refs['region_count'] = ui.label(f'分布区域: {len(active_regions)}').classes('text-slate-300')

        with ui.row().classes('absolute top-6 right-8 z-50'):
            ui.button('后台管理', icon='login', on_click=lambda: ui.navigate.to('/login')) \
                .props('flat dense color=grey-4').classes('font-bold text-xs hover:text-white transition-colors')

        with ui.element('div').classes('absolute left-4 bottom-4 z-40'):
            ui.echart({
                'backgroundColor': 'transparent', 'tooltip': {'trigger': 'item'},
                'legend': {'bottom': '0%', 'left': 'center', 'itemGap': 15, 'icon': 'circle', 'textStyle': {'color': '#94a3b8', 'fontSize': 11}},
                'series': [{'type': 'pie', 'radius': ['35%', '60%'], 'center': ['50%', '35%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 4, 'borderColor': '#0B1121', 'borderWidth': 2}, 'label': {'show': False}, 'emphasis': {'scale': True, 'scaleSize': 10, 'label': {'show': True, 'color': '#fff', 'fontWeight': 'bold'}, 'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0, 'shadowColor': 'rgba(0, 0, 0, 0.5)'}}, 'data': pie_data}]
            }).classes('w-64 h-72')

        # ✅ 修正点1：移除 scaleX，避免 CSS 缩放导致交互坐标错位
        ui.html('<div id="public-map-container" style="width:100%; height:100%;"></div>', sanitize=False).classes('w-full h-full')

    # --- 下半部分：固定标签栏 + 监控网格 ---
    with ui.column().classes('w-full h-[65vh] bg-[#0f172a] relative gap-0'):
        
        # 固定标签栏 
        with ui.row().classes('w-full px-6 py-2 bg-[#0f172a]/95 backdrop-blur z-40 border-b border-gray-800 items-center'):
            with ui.element('div').classes('w-full overflow-x-auto whitespace-nowrap scrollbar-hide'):
                groups = get_probe_groups()
                if CURRENT_PROBE_TAB not in groups: CURRENT_PROBE_TAB = 'ALL'

                with ui.tabs().props('dense no-caps align=left active-color=blue indicator-color=blue').classes('text-gray-500 bg-transparent') as tabs:
                    ui.tab('ALL', label='全部').on('click', lambda: update_tab('ALL'))
                    for g in groups:
                        if g == 'ALL': continue
                        ui.tab(g).on('click', lambda _, g=g: update_tab(g))
                    
                    tabs.set_value(CURRENT_PROBE_TAB)

        # 网格滚动区
        with ui.scroll_area().classes('w-full flex-grow p-6'):
            grid_container = ui.grid().classes('w-full gap-5 pb-20').style('grid-template-columns: repeat(auto-fill, minmax(360px, 1fr))')
            public_refs = {} 

            def render_card_grid(target_group):
                grid_container.clear()
                public_refs.clear()
                
                if target_group == 'ALL':
                    filtered_servers = [s for s in SERVERS_CACHE] 
                else:
                    filtered_servers = [s for s in SERVERS_CACHE if target_group in s.get('tags', [])]
                
                filtered_servers.sort(key=lambda x: (0 if x.get('_status')=='online' else 1, x.get('name', '')))

                with grid_container:
                    if not filtered_servers:
                        ui.label(f'视图 "{target_group}" 下暂无服务器').classes('col-span-full text-center text-gray-500 mt-10')
                        return

                    for s in filtered_servers:
                        url = s['url']
                        refs = {}
                        with ui.card().classes('status-card w-full p-5 rounded-xl flex flex-col gap-3 relative overflow-hidden group') as card:
                            refs['card'] = card
                            with ui.row().classes('w-full justify-between items-center mb-1'):
                                with ui.row().classes('items-center gap-3 overflow-hidden'):
                                    flag = "🏳️"
                                    try: flag = detect_country_group(s['name'], s).split(' ')[0]
                                    except: pass
                                    ui.label(flag).classes('text-3xl') 
                                    ui.label(s['name']).classes('text-lg font-bold text-gray-100 truncate cursor-pointer hover:text-blue-400 transition').on('click', lambda _, s=s: open_dark_server_detail(s))
                                refs['badge'] = ui.label('检测中').classes('text-xs font-mono font-bold tracking-wider text-gray-500')
                            
                            with ui.row().classes('w-full justify-between px-1 mb-2'):
                                with ui.row().classes('items-center gap-1'):
                                    ui.icon('grid_view').classes('text-blue-400 text-xs'); refs['summary_cores'] = ui.label('--').classes('text-xs font-mono text-gray-400 font-bold')
                                with ui.row().classes('items-center gap-1'):
                                    ui.icon('memory').classes('text-green-400 text-xs'); refs['summary_ram'] = ui.label('--').classes('text-xs font-mono text-gray-400 font-bold')
                                with ui.row().classes('items-center gap-1'):
                                    ui.icon('storage').classes('text-purple-400 text-xs'); refs['summary_disk'] = ui.label('--').classes('text-xs font-mono text-gray-400 font-bold')

                            with ui.column().classes('w-full gap-3'):
                                def stat_row(label, color_cls):
                                    with ui.column().classes('w-full gap-1'):
                                        with ui.row().classes('w-full items-center justify-between'):
                                            ui.label(label).classes('text-xs text-gray-500 font-bold w-8')
                                            with ui.element('div').classes('flex-grow h-2.5 bg-gray-700/50 rounded-full overflow-hidden mx-2'):
                                                bar = ui.element('div').classes(f'h-full {color_cls} prog-bar').style('width: 0%')
                                            pct = ui.label('0%').classes('text-xs font-mono font-bold text-white w-8 text-right')
                                        sub = ui.label('').classes('text-[10px] text-gray-500 font-mono text-right w-full pr-1')
                                    return bar, pct, sub
                                refs['cpu_bar'], refs['cpu_pct'], refs['cpu_sub'] = stat_row('CPU', 'bg-blue-500')
                                refs['mem_bar'], refs['mem_pct'], refs['mem_sub'] = stat_row('内存', 'bg-green-500')
                                refs['disk_bar'], refs['disk_pct'], refs['disk_sub'] = stat_row('硬盘', 'bg-purple-500')
                            
                            ui.separator().classes('bg-white/5 my-1')

                            with ui.grid().classes('w-full grid-cols-2 gap-y-1 gap-x-2 text-xs'):
                                ui.label('网络').classes('text-gray-500'); 
                                with ui.row().classes('justify-end gap-2 font-mono'): refs['net_up'] = ui.label('↑ 0B').classes('text-orange-400 font-bold'); refs['net_down'] = ui.label('↓ 0B').classes('text-green-400 font-bold')
                                ui.label('流量').classes('text-gray-500');
                                with ui.row().classes('justify-end gap-2 font-mono text-gray-400'): refs['traf_up'] = ui.label('↑ 0B'); refs['traf_down'] = ui.label('↓ 0B')
                                ui.label('负载').classes('text-gray-500'); refs['load'] = ui.label('--').classes('text-gray-300 font-mono text-right font-bold')
                                ui.label('在线').classes('text-gray-500'); 
                                with ui.row().classes('justify-end items-center gap-1'): refs['uptime'] = ui.label('--').classes('text-gray-400 font-mono text-right'); refs['online_dot'] = ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-gray-500')

                            with ui.row().classes('w-full justify-between items-center mt-1 pt-2 border-t border-white/5 text-[10px]'):
                                ui.label('延迟').classes('text-gray-500 font-bold')
                                with ui.row().classes('gap-3 font-mono'):
                                    refs['ping_ct'] = ui.html('电信: <span class="text-gray-500">-</span>', sanitize=False)
                                    refs['ping_cu'] = ui.html('联通: <span class="text-gray-500">-</span>', sanitize=False)
                                    refs['ping_cm'] = ui.html('移动: <span class="text-gray-500">-</span>', sanitize=False)
                        
                        public_refs[url] = refs

            def update_tab(new_val):
                global CURRENT_PROBE_TAB
                if CURRENT_PROBE_TAB != new_val:
                    CURRENT_PROBE_TAB = new_val
                    render_card_grid(new_val)

            render_card_grid(CURRENT_PROBE_TAB)

    # 地图 JS
    ui.run_javascript(f'''
    (function() {{
        var mapData = {chart_data};
        function checkAndRender() {{
            var chartDom = document.getElementById('public-map-container');
            if (!chartDom || typeof echarts === 'undefined') {{ setTimeout(checkAndRender, 100); return; }}
            fetch('https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/world.json').then(r => r.json()).then(w => {{
                echarts.registerMap('world', w);
                var myChart = echarts.init(chartDom);
                var centerPt = [116.4, 39.9]; 
                if (navigator.geolocation) {{ navigator.geolocation.getCurrentPosition(p => {{ centerPt = [p.coords.longitude, p.coords.latitude]; updateChart(myChart, mapData, centerPt); }}, e => {{ updateChart(myChart, mapData, centerPt); }}); }} else {{ updateChart(myChart, mapData, centerPt); }}
                
                // 监听缩放事件，实现自动回正
                myChart.on('georoam', function() {{
                    var opt = myChart.getOption();
                    var currZoom = opt.geo[0].zoom;
                    // 如果缩放比例接近或小于初始值 1.2，则重置中心点
                    if (currZoom <= 1.21) {{
                        myChart.setOption({{ geo: {{ center: [-10, 20], zoom: 1.2 }} }});
                    }}
                }});
            }});
        }}
        function updateChart(chart, data, center) {{
            var regions = data.regions.map(n => ({{ name: n, itemStyle: {{ areaColor: '#0055ff', borderColor: '#00ffff', borderWidth: 1.5, shadowColor: 'rgba(0, 255, 255, 0.8)', shadowBlur: 20, opacity: 0.9 }} }}));
            var lines = data.points.map(pt => ({{ coords: [pt.value, center] }}));
            var option = {{
                backgroundColor: '#100C2A',
                geo: {{ 
                    map: 'world', 
                    roam: true,          // ✨ 开启缩放和平移
                    zoom: 1.2, 
                    aspectScale: 0.85,   // ✨ 视觉上横向拉宽地图，替代 CSS scaleX
                    scaleLimit: {{ min: 1.2, max: 10 }}, // ✨ 限制最小缩放比例为初始值
                    center: [-10, 20], 
                    label: {{ show: false }}, 
                    itemStyle: {{ areaColor: '#1B2631', borderColor: '#404a59', borderWidth: 1 }}, 
                    emphasis: {{ itemStyle: {{ areaColor: '#2a333d' }} }}, 
                    regions: regions 
                }},
                series: [
                    {{ type: 'lines', zlevel: 2, effect: {{ show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 }}, lineStyle: {{ color: '#00ffff', width: 0, curveness: 0.2, opacity: 0 }}, data: lines }},
                    {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3, rippleEffect: {{ brushType: 'stroke', scale: 2.5 }}, itemStyle: {{ color: '#00ffff', shadowBlur: 10, shadowColor: '#00ffff' }}, label: {{ show: true, position: 'top', formatter: '{{b}}', color: '#fff', fontSize: 16, offset: [0, -2] }}, data: data.points }},
                    {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 4, itemStyle: {{ color: '#f59e0b' }}, label: {{ show: true, position: 'bottom', formatter: 'My PC', color: '#f59e0b', fontWeight: 'bold' }}, data: [{{ value: center }}] }}
                ]
            }};
            chart.setOption(option);
            window.addEventListener('resize', () => chart.resize());
        }}
        checkAndRender();
    }})();
    ''')

    async def loop_update():
        try:
            current_urls = set(s['url'] for s in SERVERS_CACHE)
            displayed_urls = list(public_refs.keys())
            
            # 为了防止手机端崩溃，这里增加了 length 检查，只有真正发生增减时才触发
            target_count = len(current_urls) if CURRENT_PROBE_TAB == 'ALL' else len([s for s in SERVERS_CACHE if CURRENT_PROBE_TAB in s.get('tags', [])])
            if len(public_refs) != target_count:
                render_card_grid(CURRENT_PROBE_TAB)
                return

            real_online_count = 0
            for s in SERVERS_CACHE:
                url = s['url']
                refs = public_refs.get(url)
                if not refs or refs['badge'].is_deleted: continue
                res = await get_server_status(s)
                
                if res and res.get('status') == 'online': real_online_count += 1
                
                def get_ping_color(val):
                    if val == -1 or val == 0: return 'text-red-500', '超时'
                    if val < 80: return 'text-green-400', f'{val}ms'
                    if val < 150: return 'text-yellow-400', f'{val}ms'
                    return 'text-red-400', f'{val}ms'

                if res and res.get('status') == 'online':
                    refs['card'].classes(remove='offline-card')
                    refs['badge'].set_text('在线'); refs['badge'].classes(replace='text-green-400', remove='text-gray-500 text-red-500 text-orange-400')
                    refs['summary_cores'].set_text(f"{res.get('cpu_cores', 1)} Cores")
                    refs['summary_ram'].set_text(f"{res.get('mem_total', 0)} GB")
                    refs['summary_disk'].set_text(f"{res.get('disk_total', 0)} GB")
                    cpu = float(res.get('cpu_usage', 0))
                    refs['cpu_bar'].style(f'width: {cpu}%'); refs['cpu_pct'].set_text(f'{int(cpu)}%')
                    refs['cpu_sub'].set_text(f"{res.get('cpu_cores', 1)} Cores")
                    mem = float(res.get('mem_usage', 0)); mem_used = float(res.get('mem_total', 0)) * (mem/100)
                    refs['mem_bar'].style(f'width: {mem}%'); refs['mem_pct'].set_text(f'{int(mem)}%')
                    refs['mem_sub'].set_text(f"{round(mem_used, 2)} GB")
                    disk = float(res.get('disk_usage', 0)); disk_used = float(res.get('disk_total', 0)) * (disk/100)
                    refs['disk_bar'].style(f'width: {disk}%'); refs['disk_pct'].set_text(f'{int(disk)}%')
                    refs['disk_sub'].set_text(f"{round(disk_used, 2)} GB")
                    def fmt(b): 
                        if b<1024: return f"{int(b)}B"
                        if b<1024**2: return f"{int(b/1024)}K"
                        return f"{int(b/1024**2)}M"
                    refs['net_up'].set_text(f"↑ {fmt(res.get('net_speed_out', 0))}/s")
                    refs['net_down'].set_text(f"↓ {fmt(res.get('net_speed_in', 0))}/s")
                    def fmt_t(b): return f"{round(b/1024**3, 1)}G" if b > 1024**3 else f"{int(b/1024**2)}M"
                    refs['traf_up'].set_text(f"↑ {fmt_t(res.get('net_total_out', 0))}")
                    refs['traf_down'].set_text(f"↓ {fmt_t(res.get('net_total_in', 0))}")
                    refs['load'].set_text(str(res.get('load_1', 0)))
                    refs['uptime'].set_text(str(res.get('uptime', '-')))
                    refs['online_dot'].classes(replace='bg-green-500', remove='bg-gray-500 bg-red-500')
                    pings = res.get('pings', {})
                    c1, t1 = get_ping_color(pings.get('电信', 0))
                    c2, t2 = get_ping_color(pings.get('联通', 0))
                    c3, t3 = get_ping_color(pings.get('移动', 0))
                    refs['ping_ct'].set_content(f'电信: <span class="{c1}">{t1}</span>')
                    refs['ping_cu'].set_content(f'联通: <span class="{c2}">{t2}</span>')
                    refs['ping_cm'].set_content(f'移动: <span class="{c3}">{t3}</span>')
                elif res and res.get('status') == 'warning':
                    refs['card'].classes(remove='offline-card')
                    refs['badge'].set_text('简易'); refs['badge'].classes(replace='text-orange-400', remove='text-green-400 text-red-500')
                    refs['cpu_bar'].style(f'width: {res.get("cpu_usage",0)}%')
                    refs['online_dot'].classes(replace='bg-orange-500')
                    refs['uptime'].set_text('Agent Missing')
                else:
                    refs['card'].classes(add='offline-card')
                    refs['badge'].set_text('离线'); refs['badge'].classes(replace='text-red-500', remove='text-green-400 text-orange-400')
                    refs['cpu_bar'].style('width: 0%')
                    refs['online_dot'].classes(replace='bg-red-500')
                    last_time_str = "Down"
                    if url in PROBE_DATA_CACHE:
                        cached_info = PROBE_DATA_CACHE[url]
                        if 'uptime' in cached_info: last_time_str = f"停于: {cached_info['uptime']}"
                    refs['uptime'].set_text(last_time_str)
            
            if header_refs.get('online_count'):
                header_refs['online_count'].set_text(f'在线: {real_online_count}')

        except Exception: pass
        ui.timer(2.0, loop_update, once=True)
    ui.timer(0.1, loop_update, once=True)


# ================= 手机端专用：实时动效 Dashboard ==========================
async def render_mobile_status_page():
    global CURRENT_PROBE_TAB
    # 用于存储 UI 组件引用的字典，实现局部刷新
    mobile_refs = {}

    # 1. 注入复刻样式的 CSS
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
        <style>
            body { background-color: #0d0d0d; color: #ffffff; margin: 0; padding: 0; overflow-x: hidden; }
            .mobile-header { background: #1a1a1a; border-bottom: 1px solid #333; position: sticky; top: 0; z-index: 100; padding: 12px 16px; }
            .mobile-card-container { display: flex; flex-direction: column; align-items: center; width: 100%; padding: 12px 0; }
            .mobile-card { 
                background: #1a1a1a; border-radius: 16px; padding: 18px; border: 1px solid #333;
                width: calc(100% - 24px); margin-bottom: 16px; box-sizing: border-box;
            }
            .inner-module {
                background: #242424; border-radius: 12px; padding: 12px; height: 95px;
                display: flex; flex-direction: column; justify-content: space-between;
            }
            .stat-header { display: flex; justify-content: space-between; align-items: center; }
            .stat-label-box { display: flex; align-items: center; gap: 4px; }
            .stat-icon { font-size: 14px !important; color: #888; }
            .stat-label { color: #888; font-size: 11px; font-weight: bold; }
            .stat-value { color: #fff; font-size: 17px; font-weight: 800; font-family: monospace; }
            .bar-bg { height: 5px; background: #333; border-radius: 3px; overflow: hidden; margin: 2px 0; }
            .bar-fill-cpu { height: 100%; background: #3b82f6; transition: width 0.6s; box-shadow: 0 0 5px #3b82f6; }
            .bar-fill-mem { height: 100%; background: #22c55e; transition: width 0.6s; box-shadow: 0 0 5px #22c55e; }
            .bar-fill-disk { height: 100%; background: #a855f7; }
            .stat-subtext { color: #555; font-size: 10px; font-family: monospace; font-weight: bold; }
            .speed-up { color: #22c55e; font-weight: bold; font-size: 11px; }
            .speed-down { color: #3b82f6; font-weight: bold; font-size: 11px; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
        </style>
    ''')

    # --- 2. 顶部与标签栏 ---
    with ui.column().classes('mobile-header w-full gap-1'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('X-Fusion Status').classes('text-lg font-black text-blue-400')
            ui.button(icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense color=grey-5')
        online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
        ui.label(f'🟢 {online_count} ONLINE / {len(SERVERS_CACHE)} TOTAL').classes('text-[10px] font-bold text-gray-500 tracking-widest')

    with ui.row().classes('w-full px-2 py-1 bg-[#0d0d0d] border-b border-[#333] overflow-x-auto whitespace-nowrap scrollbar-hide'):
        groups = ['ALL'] + ADMIN_CONFIG.get('probe_custom_groups', [])
        with ui.tabs().props('dense no-caps active-color=blue-400 indicator-color=blue-400').classes('text-gray-500') as tabs:
            for g in groups:
                ui.tab(g, label='全部' if g=='ALL' else g).on('click', lambda _, group=g: update_mobile_tab(group))
            tabs.set_value(CURRENT_PROBE_TAB)

    list_container = ui.column().classes('mobile-card-container')

    # --- 3. 渲染函数 ---
    async def render_list(target_group):
        list_container.clear()
        mobile_refs.clear()
        
        filtered = [s for s in SERVERS_CACHE if target_group == 'ALL' or target_group in s.get('tags', [])]
        filtered.sort(key=lambda x: (0 if x.get('_status')=='online' else 1, x.get('name', '')))

        with list_container:
            for s in filtered:
                status = PROBE_DATA_CACHE.get(s['url'], {})
                static = status.get('static', {})
                is_online = s.get('_status') == 'online'
                srv_ref = {}
                
                with ui.column().classes('mobile-card').on('click', lambda _, srv=s: open_dark_server_detail(srv)):
                    # 标题与描述
                    with ui.row().classes('items-center gap-3 mb-3'):
                        flag = "🏳️"
                        try: flag = detect_country_group(s['name'], s).split(' ')[0]
                        except: pass
                        ui.label(flag).classes('text-3xl')
                        ui.label(s['name']).classes('text-base font-bold truncate').style('max-width:200px')

                    # 2x2 宫格布局
                    with ui.grid().classes('w-full grid-cols-2 gap-3'):
                        # CPU 模块
                        cpu = status.get('cpu_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">settings_suggest</span><span class="stat-label">CPU</span></div>', sanitize=False)
                                srv_ref['cpu_text'] = ui.label(f'{cpu}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['cpu_bar'] = ui.element('div').classes('bar-fill-cpu').style(f'width: {cpu}%')
                            ui.label(f"{status.get('cpu_cores', 1)} Cores").classes('stat-subtext')

                        # RAM 模块
                        mem_p = status.get('mem_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">memory</span><span class="stat-label">RAM</span></div>', sanitize=False)
                                srv_ref['mem_text'] = ui.label(f'{int(mem_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                srv_ref['mem_bar'] = ui.element('div').classes('bar-fill-mem').style(f'width: {mem_p}%')
                            srv_ref['mem_detail'] = ui.label('-- / --').classes('stat-subtext')

                        # DISK 模块
                        disk_p = status.get('disk_usage', 0)
                        with ui.element('div').classes('inner-module'):
                            with ui.element('div').classes('stat-header'):
                                ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">storage</span><span class="stat-label">DISK</span></div>', sanitize=False)
                                ui.label(f'{int(disk_p)}%').classes('stat-value')
                            with ui.element('div').classes('bar-bg'):
                                ui.element('div').classes('bar-fill-disk').style(f'width: {disk_p}%')
                            ui.label(f"{status.get('disk_total', 0)}G Total").classes('stat-subtext')

                        # SPEED 模块
                        with ui.element('div').classes('inner-module'):
                            ui.html('<div class="stat-label-box"><span class="material-icons stat-icon">swap_calls</span><span class="stat-label">SPEED</span></div>', sanitize=False)
                            with ui.column().classes('w-full gap-0'):
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↑').classes('speed-up')
                                    srv_ref['net_up'] = ui.label('--').classes('text-[12px] font-mono font-bold')
                                with ui.row().classes('w-full justify-between items-center'):
                                    ui.label('↓').classes('speed-down')
                                    srv_ref['net_down'] = ui.label('--').classes('text-[12px] font-mono font-bold')

                    # 底部状态
                    with ui.row().classes('w-full justify-between mt-3 pt-2 border-t border-[#333] items-center'):
                        srv_ref['uptime'] = ui.label("在线时长：--").classes('text-[10px] font-bold text-green-500 font-mono')
                        with ui.row().classes('items-center gap-2'):
                            # 闪电图标引用 srv_ref['load']，动态展示 load_1 数据
                            srv_ref['load'] = ui.label(f"⚡ {status.get('load_1', '0.0')}").classes('text-[10px] text-gray-400 font-bold')
                            ui.label('ACTIVE' if is_online else 'DOWN').classes(f'text-[10px] font-black {"text-green-500" if is_online else "text-red-400"}')
                
                mobile_refs[s['url']] = srv_ref

    # --- 4. 实时同步逻辑 ---
    def fmt_speed(b):
        if b < 1024: return f"{int(b)}B"
        return f"{int(b/1024)}K" if b < 1024**2 else f"{round(b/1024**2,1)}M"

    async def mobile_sync_loop():
        for url, refs in mobile_refs.items():
            status = PROBE_DATA_CACHE.get(url, {})
            if not status: continue
            
            # 更新网速
            refs['net_up'].set_text(f"{fmt_speed(status.get('net_speed_out', 0))}/s")
            refs['net_down'].set_text(f"{fmt_speed(status.get('net_speed_in', 0))}/s")
            
            # 更新 CPU & RAM
            cpu = status.get('cpu_usage', 0)
            mem_p = status.get('mem_usage', 0)
            refs['cpu_text'].set_text(f"{cpu}%")
            refs['cpu_bar'].style(f"width: {cpu}%")
            refs['mem_text'].set_text(f"{int(mem_p)}%")
            refs['mem_bar'].style(f"width: {mem_p}%")
            
            # 内存详情
            mem_t = status.get('mem_total', 0)
            mem_u = round(float(mem_t or 0) * (float(mem_p or 0)/100), 2)
            refs['mem_detail'].set_text(f"{mem_u}G / {mem_t}G")
            
            # Uptime 格式化处理：将 "up 81 days, 11:08" 转换为 "在线时长：81天 11时 8分"
            raw_uptime = str(status.get('uptime', '-'))
            formatted_uptime = raw_uptime.replace('up ', '').replace(' days, ', '天 ').replace(' day, ', '天 ')
            if ':' in formatted_uptime:
                parts = formatted_uptime.split(' ')
                time_parts = parts[-1].split(':')
                h = time_parts[0]
                m = time_parts[1]
                # 重新拼接
                prefix = "".join(parts[:-1])
                formatted_uptime = f"{prefix}{h}时 {m}分"
            
            refs['uptime'].set_text(f"在线时长：{formatted_uptime}")
            
            # Load 显示实时负载数据
            refs['load'].set_text(f"⚡ {status.get('load_1', '0.0')}")

    async def update_mobile_tab(val):
        global CURRENT_PROBE_TAB
        CURRENT_PROBE_TAB = val
        await render_list(val)

    await render_list(CURRENT_PROBE_TAB)
    ui.timer(2.0, mobile_sync_loop)
    
if __name__ in {"__main__", "__mp_main__"}:
    logger.info("🚀 系统正在初始化...")
    
    # ✨✨✨ 启动配置 (已开启静默重连) ✨✨✨
    # reconnect_timeout=600.0: 允许客户端断线 10 分钟内自动重连而不刷新页面
    ui.run(
        title='X-Fusion Panel', 
        host='0.0.0.0', 
        port=8080, 
        language='zh-CN', 
        storage_secret='sijuly_secret_key', 
        reload=False, 
        reconnect_timeout=600.0 
    )
