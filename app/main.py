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

import time
GLOBAL_UI_VERSION = time.time()
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


# =================  Cloudflare 设置弹窗 =================
def open_cloudflare_settings_dialog():
    with ui.dialog() as d, ui.card().classes('w-[500px] p-6 flex flex-col gap-4'):
        with ui.row().classes('items-center gap-2 text-orange-600 mb-2'):
            ui.icon('cloud', size='md')
            ui.label('Cloudflare API 配置').classes('text-lg font-bold')
            
        ui.label('用于自动解析域名、开启 CDN 和设置 SSL (Flexible)。').classes('text-xs text-gray-500')
        
        # 读取现有配置
        cf_token = ui.input('API Token', value=ADMIN_CONFIG.get('cf_api_token', '')).props('outlined dense type=password').classes('w-full')
        ui.label('权限要求: Zone.DNS (Edit), Zone.Settings (Edit)').classes('text-[10px] text-gray-400 ml-1')
        
        cf_domain_root = ui.input('根域名 (例如: example.com)', value=ADMIN_CONFIG.get('cf_root_domain', '')).props('outlined dense').classes('w-full')
        
        async def save_cf():
            ADMIN_CONFIG['cf_api_token'] = cf_token.value.strip()
            ADMIN_CONFIG['cf_root_domain'] = cf_domain_root.value.strip()
            await save_admin_config()
            safe_notify('✅ Cloudflare 配置已保存', 'positive')
            d.close()

        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('保存配置', on_click=save_cf).classes('bg-orange-600 text-white shadow-md')
    d.open()


# ================= SSH 全局配置区域  =================
GLOBAL_SSH_KEY_FILE = 'data/global_ssh_key'

def load_global_key():
    if os.path.exists(GLOBAL_SSH_KEY_FILE):
        with open(GLOBAL_SSH_KEY_FILE, 'r') as f: return f.read()
    return ""

def save_global_key(content):
    with open(GLOBAL_SSH_KEY_FILE, 'w') as f: f.write(content)

# =================  全局SSH密钥设置弹窗  =================
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



# =================  XHTTP-Reality 部署脚本 =================
# 特性：自动检测 Caddy/Nginx，如果 443 被占，自动切换到 8443，互不冲突。
XHTTP_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
export PATH=$PATH:/usr/local/bin

# 1. 基础环境检查
if [ -f /etc/debian_version ]; then
    apt-get update -y >/dev/null 2>&1
    apt-get install -y net-tools lsof curl unzip jq uuid-runtime openssl >/dev/null 2>&1
elif [ -f /etc/redhat-release ]; then
    yum install -y net-tools lsof curl unzip jq >/dev/null 2>&1
fi

# 定义日志
log() { echo -e "\033[32m[DEBUG]\033[0m $1"; }
err() { echo -e "\033[31m[ERROR]\033[0m $1"; }

DOMAIN="$1"
if [ -z "$DOMAIN" ]; then err "域名参数缺失"; exit 1; fi

log "========== 开始智能部署 XHTTP =========="

# 2. 智能端口选择 (核心修改)
# 默认端口
PORT_REALITY=443
PORT_XHTTP=80

# 检查 443 (TCP) 是否被占用 (例如 Caddy/Nginx)
if netstat -tlpn | grep -q ":443 "; then
    log "⚠️ 检测到 TCP 443 端口被占用 (可能是 Caddy/Nginx)"
    log "🔄 自动切换 Reality 端口至: 8443"
    PORT_REALITY=8443
else
    log "✅ TCP 443 端口空闲，将使用默认端口"
fi

# 检查 80 (TCP) 是否被占用
if netstat -tlpn | grep -q ":80 "; then
    log "⚠️ 检测到 TCP 80 端口被占用"
    log "🔄 自动切换 XHTTP 监听端口至: 8080"
    PORT_XHTTP=8080
else
    log "✅ TCP 80 端口空闲，将使用默认端口"
fi

# 3. 安装/更新 Xray
log "正在下载最新版 Xray..."
xray_bin="/usr/local/bin/xray"
rm -f "$xray_bin" # 清理旧版
arch=$(uname -m); 
case "$arch" in x86_64) a="64";; aarch64) a="arm64-v8a";; esac
curl -fsSL https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${a}.zip -o /tmp/xray.zip
if [ $? -ne 0 ]; then err "Xray 下载失败"; exit 1; fi

unzip -qo /tmp/xray.zip -d /tmp/xray
install -m 755 /tmp/xray/xray "$xray_bin"

# 4. 生成密钥与配置
KEYS=$($xray_bin x25519)
PRI_KEY=$(echo "$KEYS" | grep -i "Private" | awk '{print $NF}')
PUB_KEY=$(echo "$KEYS" | grep -i "Public" | awk '{print $NF}')
# 兜底
if [ -z "$PUB_KEY" ]; then
    PRI_KEY=$(echo "$KEYS" | head -n1 | awk '{print $NF}')
    PUB_KEY=$(echo "$KEYS" | tail -n1 | awk '{print $NF}')
fi

UUID_XHTTP=$(cat /proc/sys/kernel/random/uuid)
UUID_REALITY=$(cat /proc/sys/kernel/random/uuid)
XHTTP_PATH="/$(echo "$UUID_XHTTP" | cut -d- -f1 | tr -d '\n')"
SHORT_ID=$(openssl rand -hex 4)
# 如果端口不是 443，Reality 目标也要相应调整，这里偷懒直接回环，或者偷一个公网
SNI="www.icloud.com"

mkdir -p /usr/local/etc/xray
CONFIG_FILE="/usr/local/etc/xray/config.json"

# 生成配置文件 (使用动态端口变量)
cat > $CONFIG_FILE <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "port": $PORT_XHTTP,
      "protocol": "vless",
      "settings": { "clients": [{ "id": "$UUID_XHTTP" }], "decryption": "none" },
      "streamSettings": { "network": "xhttp", "xhttpSettings": { "path": "$XHTTP_PATH", "mode": "auto" } }
    },
    {
      "port": $PORT_REALITY,
      "protocol": "vless",
      "settings": {
        "clients": [{ "id": "$UUID_REALITY", "flow": "xtls-rprx-vision" }],
        "decryption": "none",
        "fallbacks": [{ "dest": $PORT_XHTTP }]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": { "privateKey": "$PRI_KEY", "serverNames": ["$SNI"], "shortIds": ["$SHORT_ID"], "target": "$SNI:443" }
      }
    }
  ],
  "outbounds": [{ "protocol": "freedom" }]
}
EOF

# 5. 启动服务
cat > /etc/systemd/system/xray.service <<EOF
[Unit]
Description=Xray Service
After=network.target
[Service]
ExecStart=$xray_bin run -c $CONFIG_FILE
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xray >/dev/null 2>&1
systemctl restart xray
sleep 2

# 6. 生成链接 (使用实际端口)
VPS_IP=$(curl -fsSL https://api.ipify.org)
EXTRA_JSON="{\"downloadSettings\":{\"address\":\"$VPS_IP\",\"port\":$PORT_REALITY,\"network\":\"xhttp\",\"xhttpSettings\":{\"path\":\"$XHTTP_PATH\",\"mode\":\"auto\"},\"security\":\"reality\",\"realitySettings\":{\"serverName\":\"$SNI\",\"fingerprint\":\"chrome\",\"show\":false,\"publicKey\":\"$PUB_KEY\",\"shortId\":\"$SHORT_ID\",\"spiderX\":\"/\"}}}"

ENC_EXTRA=$(printf '%s' "$EXTRA_JSON" | jq -sRr @uri)
ENC_PATH=$(printf '%s' "$XHTTP_PATH" | jq -sRr @uri)

# 注意：链接中的端口变为 $PORT_REALITY
LINK="vless://${UUID_XHTTP}@${VPS_IP}:${PORT_REALITY}?encryption=none&security=tls&sni=${DOMAIN}&type=xhttp&host=${DOMAIN}&path=${ENC_PATH}&mode=auto&extra=${ENC_EXTRA}#XHTTP-Reality"

echo "DEPLOY_SUCCESS_LINK: $LINK"
"""
# ================= VLESS 链接解析器 =================
def parse_vless_link_to_node(link, remark_override=None):
    """将 vless:// 链接解析为面板节点格式的字典"""
    try:
        if not link.startswith("vless://"): return None
        
        # 局部引入依赖，防止报错
        import urllib.parse
        
        # 1. 基础解析：移除协议头
        main_part = link.replace("vless://", "")
        
        # 处理 fragment (#备注)
        remark = "XHTTP-Reality"
        if "#" in main_part:
            main_part, remark = main_part.split("#", 1)
            remark = urllib.parse.unquote(remark)
        
        # 如果传入了强制备注（用户输入的），覆盖原备注
        if remark_override: 
            remark = remark_override

        # 处理 query parameters (?)
        params = {}
        if "?" in main_part:
            main_part, query_str = main_part.split("?", 1)
            params = dict(urllib.parse.parse_qsl(query_str))
        
        # 处理 user@host:port
        if "@" in main_part:
            user_info, host_port = main_part.split("@", 1)
            uuid = user_info
        else:
            return None # 格式不正确

        if ":" in host_port:
            # 使用 rsplit 确保正确处理 host:port
            host, port = host_port.rsplit(":", 1)
        else:
            host = host_port
            port = 443

        # ================= 核心修复：更新原始链接中的备注 =================
        final_link = link
        if remark_override:
            # 1. 如果原链接里有 #，先去掉旧的
            if "#" in final_link:
                final_link = final_link.split("#")[0]
            # 2. 拼接新的备注 (进行 URL 编码)
            final_link = f"{final_link}#{urllib.parse.quote(remark)}"
        # ==========================================================

        # 2. 构建符合 Panel 格式的 Node 字典
        node = {
            "id": uuid, 
            "remark": remark,
            "port": int(port),
            "protocol": "vless",
            "settings": {
                "clients": [{"id": uuid, "flow": params.get("flow", "")}],
                "decryption": "none"
            },
            "streamSettings": {
                "network": params.get("type", "tcp"),
                "security": params.get("security", "none"),
                "xhttpSettings": {
                    "path": params.get("path", ""),
                    "mode": params.get("mode", "auto"),
                    "host": params.get("host", "")
                },
                "realitySettings": {
                    "serverName": params.get("sni", ""),
                    "shortId": params.get("sid", ""), 
                    "publicKey": params.get("pbk", "") 
                }
            },
            "enable": True,
            "_is_custom": True, 
            "_raw_link": final_link  # 使用更新后的链接
        }
        return node

    except Exception as e:
        # 必须要有 except 块来捕获潜在错误
        print(f"[Error] 解析 VLESS 链接失败: {e}")
        return None

# ================= 部署弹窗 (自定义使用 CF API 根域名) =================
async def open_deploy_xhttp_dialog(server_conf, callback):
    # 1. 获取服务器真实 IP (用于解析)
    # 无论配置里填的是域名还是IP，我们都需要解析出最终的 IPv4 地址
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    
    real_ip = target_host
    import re
    import socket
    
    # 如果填的是域名，先尝试解析出 IP
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except:
            safe_notify(f"❌ 无法解析服务器 IP: {target_host}", "negative")
            return

    # 2. 读取 Cloudflare 配置
    cf_handler = CloudflareHandler()
    has_cf_api = bool(cf_handler.token)
    root_domain = cf_handler.root_domain

    if not has_cf_api or not root_domain:
        safe_notify("❌ 自定义模式失败: 请先在左下角配置 Cloudflare API 和根域名", "negative")
        return

    # 3. 生成自定义使用的新域名
    import random, string
    rand_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    # 格式: node-1-2-3-4-abcd.aaabb.com
    sub_prefix = f"node-{real_ip.replace('.', '-')}-{rand_suffix}"
    # ✨✨✨ 核心修改：使用配置的根域名 ✨✨✨
    target_domain = f"{sub_prefix}.{root_domain}"

    # === 构建弹窗 ===
    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl'):
        
        # --- 顶部标题栏 ---
        with ui.column().classes('w-full bg-slate-900 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('rocket_launch', size='md')
                ui.label('部署 XHTTP-Reality (自定义域名模式)').classes('text-lg font-bold')
            
            ui.label(f"服务器 IP: {real_ip}").classes('text-xs text-gray-400 font-mono')
            ui.label(f"即将部署到: {target_domain}").classes('text-sm text-green-400 font-mono font-bold')
            ui.label(f"(忽略原地址，将使用 {root_domain})").classes('text-[10px] text-orange-300')

        # --- 内容输入区 ---
        with ui.column().classes('w-full p-6 gap-4'):
            ui.label('节点备注名称').classes('text-xs font-bold text-gray-500 mb-[-8px]')
            # 默认备注也改成新域名
            remark_input = ui.input(placeholder=f'默认: Reality-{target_domain}').props('outlined dense clearable').classes('w-full')
            
            log_area = ui.log().classes('w-full h-48 bg-gray-900 text-green-400 text-[11px] font-mono p-3 rounded border border-gray-700 hidden transition-all')

        # --- 底部按钮区 ---
        with ui.row().classes('w-full p-4 bg-gray-50 border-t border-gray-200 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')
                
                try:
                    # --- 阶段 1: 强制 Cloudflare 解析 ---
                    log_area.push(f"🔄 [Cloudflare] 正在强制添加解析...")
                    log_area.push(f"   域名: {target_domain} -> {real_ip}")
                    
                    # 调用 auto_configure 自动添加 A 记录 + 开启小云朵 + 设 SSL Flexible
                    success, msg = await cf_handler.auto_configure(real_ip, sub_prefix)
                    
                    if success:
                        log_area.push(f"✅ 解析成功！等待 3 秒生效...")
                        await asyncio.sleep(3)
                    else:
                        log_area.push(f"❌ 解析失败: {msg}")
                        raise Exception("CF配置失败")

                    # --- 阶段 2: SSH 部署脚本 ---
                    log_area.push(f"🚀 [SSH] 开始在服务器上部署 Xray...")
                    
                    deploy_cmd = f"""
cat > /tmp/install_xhttp.sh << 'EOF_SCRIPT'
{XHTTP_INSTALL_SCRIPT_TEMPLATE}
EOF_SCRIPT
bash /tmp/install_xhttp.sh "{target_domain}"
"""
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        import re
                        match = re.search(r'DEPLOY_SUCCESS_LINK: (vless://.*)', output)
                        if match:
                            link = match.group(1).strip()
                            log_area.push("✅ 部署脚本执行完毕！")
                            
                            custom_name = remark_input.value.strip()
                            final_remark = custom_name if custom_name else f"Reality-{target_domain}"
                            
                            node_data = parse_vless_link_to_node(link, remark_override=final_remark)
                            
                            if node_data:
                                if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                                server_conf['custom_nodes'].append(node_data)
                                await save_servers()
                                
                                safe_notify(f"✅ 节点已添加: {final_remark}", "positive")
                                await asyncio.sleep(1)
                                d.close()
                                if callback: await callback() 
                            else:
                                log_area.push("❌ 链接解析失败")
                        else:
                            log_area.push("❌ 未捕获到链接，请检查日志")
                            log_area.push(output[-500:])
                    else:
                        log_area.push(f"❌ SSH 执行出错: {output}")

                except Exception as e:
                    log_area.push(f"❌ 异常: {str(e)}")
                
                btn_cancel.enable()
                btn_deploy.props(remove='loading')

            btn_deploy = ui.button('自定义部署', on_click=start_process).classes('bg-red-600 text-white shadow-lg')

    d.open()

# SSH 执行辅助函数 (放在外面避免闭包问题)
def _ssh_exec_wrapper(server_conf, cmd):
    client, msg = get_ssh_client_sync(server_conf)
    if not client: return False, msg
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        client.close()
        return True, out + "\n" + err
    except Exception as e:
        return False, str(e)


# ================= XHTTP 卸载脚本 =================
# 修正：只停止服务和删除配置，保留 xray 二进制文件，防止误杀 X-UI
XHTTP_UNINSTALL_SCRIPT = r"""
#!/bin/bash
# 1. 停止服务
systemctl stop xray
systemctl disable xray

# 2. 删除服务文件
rm -f /etc/systemd/system/xray.service
systemctl daemon-reload

# 3. 删除配置文件 (保留 bin 文件以防 X-UI 共用)
rm -rf /usr/local/etc/xray

echo "Xray Service Uninstalled (Binary kept safe)"
"""


# ================= Hysteria 2 安装脚本 =================
# 特性：自动检测 UDP 443 占用，如果 Caddy 在运行，Hy2 自动退避到 8443，
# 并自动将端口跳跃流量转发到 8443。Caddy 和 Hy2 完美共存。
HYSTERIA_INSTALL_SCRIPT_TEMPLATE = r"""
#!/bin/bash
# 1. 接收参数
PASSWORD="{password}"
OBFS_PASSWORD="{obfs_password}"
SNI="{sni}"
ENABLE_PORT_HOPPING="{enable_hopping}"
PORT_RANGE_START="{port_range_start}"
PORT_RANGE_END="{port_range_end}"

# 2. 环境清理与安装
systemctl stop hysteria-server.service 2>/dev/null
rm -rf /etc/hysteria
bash <(curl -fsSL https://get.hy2.sh/)

# 3. 证书生成
mkdir -p /etc/hysteria
openssl req -x509 -nodes -newkey ec:<(openssl ecparam -name prime256v1) \
  -keyout /etc/hysteria/server.key \
  -out /etc/hysteria/server.crt \
  -subj "/CN=$SNI" \
  -days 3650
chown hysteria /etc/hysteria/server.key
chown hysteria /etc/hysteria/server.crt

# 4. 智能端口选择 (核心逻辑)
# 默认监听 443
HY2_PORT=443

# 检测 UDP 443 是否被占用 (通常是 Caddy/Nginx 的 HTTP/3)
if netstat -ulpn | grep -q ":443 "; then
    echo "⚠️  检测到 UDP 443 端口忙 (Caddy/HTTP3?)，Hy2 自动切换至 8443"
    HY2_PORT=8443
else
    echo "✅ UDP 443 端口空闲，Hy2 将使用标准端口"
fi

# 5. 写入配置 (使用动态端口)
cat << EOF > /etc/hysteria/config.yaml
listen: :$HY2_PORT
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: password
  password: $PASSWORD
obfs:
  type: salamander
  salamander:
    password: $OBFS_PASSWORD
masquerade:
  type: proxy
  proxy:
    url: https://$SNI
    rewriteHost: true
EOF

# 6. 端口跳跃设置 (动态转发到实际端口)
# 注意：awk 的花括号在 Python 中需要双写 {{ }}
if [ "$ENABLE_PORT_HOPPING" == "true" ]; then
    IFACE=$(ip route get 8.8.8.8 | awk '{{print $5; exit}}')
    
    # 清理旧规则
    iptables -t nat -D PREROUTING -i $IFACE -p udp --dport $PORT_RANGE_START:$PORT_RANGE_END -j REDIRECT --to-ports $HY2_PORT 2>/dev/null || true
    
    # 添加新规则：将 跳跃范围 转发给 -> Hy2 实际端口 ($HY2_PORT)
    iptables -t nat -A PREROUTING -i $IFACE -p udp --dport $PORT_RANGE_START:$PORT_RANGE_END -j REDIRECT --to-ports $HY2_PORT
    
    # 持久化 (简单处理)
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4
fi

# 7. 启动
systemctl enable --now hysteria-server.service
sleep 2

# 8. 输出链接
# 无论内部用 443 还是 8443，我们生成的链接可以用 443 (如果没被占) 或者直接用跳跃端口
# 为了稳妥，如果开启了端口跳跃，建议直接给出一个跳跃范围内的端口，或者依然给 443 (靠 iptables 转发)
# 这里我们输出实际监听端口，确保最稳
if systemctl is-active --quiet hysteria-server.service; then
    PUBLIC_IP=$(curl -s https://api.ipify.org)
    
    # 如果开启了跳跃，链接里的端口其实可以是范围内任意一个，
    # 但为了兼容性，我们还是写实际端口。
    # 用户可以在客户端自己改成 20000-50000。
    LINK="hy2://$PASSWORD@$PUBLIC_IP:$HY2_PORT?peer=$SNI&insecure=1&obfs=salamander&obfs-password=$OBFS_PASSWORD&sni=$SNI#Hy2-Nodes"
    
    echo "HYSTERIA_DEPLOY_SUCCESS_LINK: $LINK"
else
    echo "HYSTERIA_DEPLOY_FAILED"
fi
"""
# ================= 一键部署 Hysteria 2  =================
async def open_deploy_hysteria_dialog(server_conf, callback):
    # --- 1. IP 获取逻辑 (保持不变) ---
    target_host = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
    
    real_ip = target_host
    import re
    import socket
    
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target_host):
        try:
            real_ip = await run.io_bound(socket.gethostbyname, target_host)
        except:
            safe_notify(f"❌ 无法解析服务器 IP: {target_host}", "negative")
            return

    # --- 2. 构建 UI ---
    with ui.dialog() as d, ui.card().classes('w-[500px] p-0 gap-0 overflow-hidden rounded-xl'):
        
        # 顶部标题栏
        with ui.column().classes('w-full bg-slate-900 p-6 gap-2'):
            with ui.row().classes('items-center gap-2 text-white'):
                ui.icon('bolt', size='md')
                ui.label('部署 Hysteria 2 (直连模式)').classes('text-lg font-bold')
            
            ui.label(f"服务器 IP: {real_ip}").classes('text-xs text-gray-400 font-mono')

        # 内容输入区
        with ui.column().classes('w-full p-6 gap-4'):
            # === 新增：自定义节点名称 ===
            name_input = ui.input('节点名称 (可选)', placeholder='留空将自动生成').props('outlined dense').classes('w-full')

            # 伪装域名
            sni_input = ui.input('伪装域名 (SNI)', value='www.bing.com').props('outlined dense').classes('w-full')
            # 混淆密码
            obfs_input = ui.input('混淆密码', value=str(uuid.uuid4())[:8]).props('outlined dense').classes('w-full')
            
            # 端口跳跃
            enable_hopping = ui.checkbox('启用端口跳跃 (Port Hopping)', value=True).classes('text-sm font-bold text-gray-600')
            with ui.row().classes('w-full items-center gap-2'):
                hop_start = ui.number('起始端口', value=20000, format='%.0f').classes('flex-1').bind_visibility_from(enable_hopping, 'value')
                ui.label('-').bind_visibility_from(enable_hopping, 'value')
                hop_end = ui.number('结束端口', value=50000, format='%.0f').classes('flex-1').bind_visibility_from(enable_hopping, 'value')

            # 日志区域
            log_area = ui.log().classes('w-full h-48 bg-gray-900 text-green-400 text-[11px] font-mono p-3 rounded border border-gray-700 hidden transition-all')

        # 底部按钮区
        with ui.row().classes('w-full p-4 bg-gray-50 border-t border-gray-200 justify-end gap-3'):
            btn_cancel = ui.button('取消', on_click=d.close).props('flat color=grey')
            
            async def start_process():
                btn_cancel.disable()
                btn_deploy.props('loading')
                log_area.classes(remove='hidden')
                
                try:
                    hy2_password = str(uuid.uuid4()).replace('-', '')[:16]
                    
                    params = {
                        "password": hy2_password,
                        "obfs_password": obfs_input.value,
                        "sni": sni_input.value,
                        "enable_hopping": "true" if enable_hopping.value else "false",
                        "port_range_start": int(hop_start.value),
                        "port_range_end": int(hop_end.value)
                    }
                    
                    script_content = HYSTERIA_INSTALL_SCRIPT_TEMPLATE.format(**params)
                    
                    deploy_cmd = f"""
cat > /tmp/install_hy2.sh << 'EOF_SCRIPT'
{script_content}
EOF_SCRIPT
bash /tmp/install_hy2.sh
"""
                    log_area.push(f"🚀 [SSH] 连接到 {real_ip} 开始安装...")
                    
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, deploy_cmd))
                    
                    if success:
                        log_area.push("✅ 脚本执行完毕，正在解析输出...")
                        
                        import re
                        match = re.search(r'HYSTERIA_DEPLOY_SUCCESS_LINK: (hy2://.*)', output)
                        
                        if match:
                            link = match.group(1).strip()
                            log_area.push("🎉 部署成功！")
                            
                            # === 修改：使用自定义名称逻辑 ===
                            custom_name = name_input.value.strip()
                            if custom_name:
                                node_name = custom_name
                            else:
                                node_name = f"Hy2-{real_ip[-3:]}-{sni_input.value}"

                            # 构建节点对象
                            new_node = {
                                "id": str(uuid.uuid4()),
                                "remark": node_name,
                                "port": 443,
                                "protocol": "hysteria2",
                                "settings": {},
                                "streamSettings": {},
                                "enable": True,
                                "_is_custom": True, 
                                "_raw_link": link 
                            }
                            
                            if 'custom_nodes' not in server_conf: server_conf['custom_nodes'] = []
                            server_conf['custom_nodes'].append(new_node)
                            await save_servers()
                            
                            safe_notify(f"✅ 节点 {node_name} 已添加", "positive")
                            await asyncio.sleep(1)
                            d.close()
                            if callback: await callback() 
                        else:
                            log_area.push("❌ 未捕获到链接，请检查日志")
                            log_area.push(output[-500:]) 
                    else:
                        log_area.push(f"❌ SSH 连接或执行失败: {output}")

                except Exception as e:
                    log_area.push(f"❌ 异常: {str(e)}")
                
                btn_cancel.enable()
                btn_deploy.props(remove='loading')

            # 这里按钮也顺便统一了风格
            btn_deploy = ui.button('开始部署', on_click=start_process).props('unelevated').classes('bg-purple-600 text-white')

    d.open()
 
# ================= 全局变量区 (缓存) =================
PROBE_DATA_CACHE = {} 
PING_TREND_CACHE = {} 

# ================= 全局记录历史数据的函数 (V61：强制每分钟只记录一次) =================
def record_ping_history(url, pings_dict):
    """
    后台收到数据调用此函数记录历史。
    ✨ 新增逻辑：同一服务器，至少间隔 60 秒才记录一次数据 (防抖)。
    """
    if not url or not pings_dict: return
    
    current_ts = time.time()
    
    # 1. 初始化
    if url not in PING_TREND_CACHE: 
        PING_TREND_CACHE[url] = []
    
    # 2. ✨✨✨ 核心防抖逻辑 ✨✨✨
    # 如果该服务器已有数据，且最后一条数据的时间距离现在不足 60 秒，则跳过不录
    if PING_TREND_CACHE[url]:
        last_record = PING_TREND_CACHE[url][-1]
        if current_ts - last_record['ts'] < 60: 
            return # <--- 没到1分钟，直接忽略，不记录

    # 3. 只有超过 60 秒才执行下面的追加逻辑
    import datetime
    time_str = datetime.datetime.fromtimestamp(current_ts).strftime('%m/%d %H:%M') # 格式化为 "01/06 19:46"
    
    ct = pings_dict.get('电信', 0); ct = ct if ct > 0 else 0
    cu = pings_dict.get('联通', 0); cu = cu if cu > 0 else 0
    cm = pings_dict.get('移动', 0); cm = cm if cm > 0 else 0
    
    PING_TREND_CACHE[url].append({
        'ts': current_ts, 
        'time_str': time_str, 
        'ct': ct, 
        'cu': cu, 
        'cm': cm
    })
    
    # 限制长度：保留最近 1000 条 (足够存放 6小时 甚至 24小时 的分钟级数据)
    # 6小时 * 60分 = 360条，设置 1000 很安全
    if len(PING_TREND_CACHE[url]) > 1000:
        PING_TREND_CACHE[url] = PING_TREND_CACHE[url][-1000:]

        
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
SYNC_SEMAPHORE = asyncio.Semaphore(50) 


LAST_AUTO_SYNC_TIME = 0
SYNC_COOLDOWN_SECONDS = 300  # 冷却时间：300秒（5分钟）

# ================= 配置区域 (Docker 强制版) =================
import os
import sys

# 🛑 强制指定数据路径为 Docker 挂载点
# 不要改动这里，直接指向容器内的挂载目录
DATA_DIR = '/app/data'

# 打印调试信息，确保它真的在读这里
print(f"🔒 [System] 强制锁定数据目录: {DATA_DIR}")

# 定义文件路径
CONFIG_FILE = os.path.join(DATA_DIR, 'servers.json')
SUBS_FILE = os.path.join(DATA_DIR, 'subscriptions.json')
NODES_CACHE_FILE = os.path.join(DATA_DIR, 'nodes_cache.json')
ADMIN_CONFIG_FILE = os.path.join(DATA_DIR, 'admin_config.json')
GLOBAL_SSH_KEY_FILE = os.path.join(DATA_DIR, 'global_ssh_key')

# 环境变量
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

# ================= 2D 平面地图：JS 逻辑 (仪表盘专用 - 已修复 Win 国旗显示) =================
GLOBE_JS_LOGIC = r"""
(function() {
    // 1. 获取仪表盘专用容器
    var container = document.getElementById('earth-render-area');
    if (!container) return;
    
    // 2. 初始化数据
    var serverData = window.DASHBOARD_DATA || [];
    
    // 3. 定义默认坐标 (北京)，如果定位成功会被覆盖
    var myLat = 39.9;
    var myLon = 116.4;

    // ✨✨✨ 修复核心：定义国旗字体 ✨✨✨
    var emojiFont = '"Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif';

    // 更新统计数字
    var nodeCountEl = document.getElementById('node-count');
    var regionCountEl = document.getElementById('region-count');
    function updateStats(data) {
        if(nodeCountEl) nodeCountEl.textContent = data.length;
        const uniqueRegions = new Set(data.map(s => s.name));
        if(regionCountEl) regionCountEl.textContent = uniqueRegions.size;
    }
    updateStats(serverData);

    // 初始化 ECharts
    var existing = echarts.getInstanceByDom(container);
    if (existing) existing.dispose();
    var myChart = echarts.init(container);

    // 4. 获取浏览器定位
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(function(position) {
            myLat = position.coords.latitude;
            myLon = position.coords.longitude;
            var option = buildOption(window.cachedWorldJson, serverData, myLat, myLon);
            myChart.setOption(option);
        });
    }

    // 5. 定义仪表盘专用的更新函数
    window.updateDashboardMap = function(newData) {
        if (!window.cachedWorldJson || !myChart) return;
        serverData = newData;
        updateStats(newData);
        var option = buildOption(window.cachedWorldJson, newData, myLat, myLon);
        myChart.setOption(option);
    };

    // 定义高亮区域
    const searchKeys = {
        '🇺🇸': 'United States', '🇨🇳': 'China', '🇭🇰': 'China', '🇹🇼': 'China', '🇯🇵': 'Japan', '🇰🇷': 'Korea',
        '🇸🇬': 'Singapore', '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇷🇺': 'Russia',
        '🇨🇦': 'Canada', '🇦🇺': 'Australia', '🇮🇳': 'India', '🇧🇷': 'Brazil'
    };

    function buildOption(mapGeoJSON, data, userLat, userLon) {
        const mapFeatureNames = mapGeoJSON.features.map(f => f.properties.name);
        const activeMapNames = new Set();

        data.forEach(s => {
            let keyword = null;
            for (let key in searchKeys) {
                if ((s.name && s.name.includes(key))) {
                    keyword = searchKeys[key];
                    break;
                }
            }
            if (keyword && mapFeatureNames.includes(keyword)) {
                activeMapNames.add(keyword);
            }
        });

        const highlightRegions = Array.from(activeMapNames).map(name => ({
            name: name,
            itemStyle: { areaColor: '#0055ff', borderColor: '#00ffff', borderWidth: 1.5, opacity: 0.9 }
        }));

        const scatterData = data.map(s => ({
            name: s.name, value: [s.lon, s.lat], itemStyle: { color: '#00ffff' }
        }));
        
        scatterData.push({
            name: "ME", value: [userLon, userLat], itemStyle: { color: '#FFD700' },
            symbolSize: 15, label: { show: true, position: 'top', formatter: 'My PC', color: '#FFD700' }
        });

        const linesData = data.map(s => ({
            coords: [[s.lon, s.lat], [userLon, userLat]]
        }));

        return {
            backgroundColor: '#100C2A', 
            geo: {
                map: 'world', roam: false, zoom: 1.2, center: [15, 10],
                label: { show: false },
                itemStyle: { areaColor: '#1B2631', borderColor: '#404a59', borderWidth: 1 },
                emphasis: { itemStyle: { areaColor: '#2a333d' }, label: { show: false } },
                regions: highlightRegions 
            },
            series: [
                {
                    type: 'lines', coordinateSystem: 'geo', zlevel: 2,
                    effect: { show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 },
                    lineStyle: { color: '#00ffff', width: 1, opacity: 0, curveness: 0.2 },
                    data: linesData
                },
                {
                    type: 'scatter', coordinateSystem: 'geo', zlevel: 3, symbol: 'circle', symbolSize: 12,
                    itemStyle: { color: '#00ffff', shadowBlur: 10, shadowColor: '#333' },
                    
                    // ✨✨✨ 重点：在这里应用了字体 ✨✨✨
                    label: { 
                        show: true, 
                        position: 'right', 
                        formatter: '{b}', 
                        color: '#fff', 
                        fontSize: 16, 
                        fontWeight: 'bold',
                        fontFamily: emojiFont  // <--- 修复这一行
                    },
                    
                    data: scatterData
                }
            ]
        };
    }

    fetch('/static/world.json')
        .then(response => response.json())
        .then(worldJson => {
            echarts.registerMap('world', worldJson);
            window.cachedWorldJson = worldJson;
            var option = buildOption(worldJson, serverData, myLat, myLon);
            myChart.setOption(option);
            
            window.addEventListener('resize', () => myChart.resize());
            new ResizeObserver(() => myChart.resize()).observe(container);
        });
})();
"""

# ================= 全局地图名称映射表 (用于 Status 页面) =================
MATCH_MAP = {
    # --- 南美 ---
    '🇨🇱': 'Chile', 'CHILE': 'Chile',
    '🇧🇷': 'Brazil', 'BRAZIL': 'Brazil', 'BRA': 'Brazil', 'SAO PAULO': 'Brazil',
    '🇦🇷': 'Argentina', 'ARGENTINA': 'Argentina', 'ARG': 'Argentina',
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

def get_echarts_region_name(name_raw):
    if not name_raw: return None
    name = name_raw.upper()
    sorted_keys = sorted(MATCH_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in name: return MATCH_MAP[key]
    return None

# ================= 全局地图数据准备 (修复：包含详细服务器列表) =================
def prepare_map_data():
    try:
        city_points_map = {} 
        flag_points_map = {} 
        unique_deployed_countries = set() 
        region_stats = {} 
        active_regions_for_highlight = set()

        # 1. 国旗 -> 标准地图名映射 (ECharts World Map 英文名)
        FLAG_TO_MAP_NAME = {
            '🇨🇳': 'China', '🇭🇰': 'China', '🇲🇴': 'China', '🇹🇼': 'China',
            '🇺🇸': 'United States', '🇨🇦': 'Canada', '🇲🇽': 'Mexico',
            '🇬🇧': 'United Kingdom', '🇩🇪': 'Germany', '🇫🇷': 'France', '🇳🇱': 'Netherlands',
            '🇷🇺': 'Russia', '🇯🇵': 'Japan', '🇰🇷': 'South Korea', '🇸🇬': 'Singapore',
            '🇮🇳': 'India', '🇦🇺': 'Australia', '🇧🇷': 'Brazil', '🇦🇷': 'Argentina',
            '🇹🇷': 'Turkey', '🇮🇹': 'Italy', '🇪🇸': 'Spain', '🇵🇹': 'Portugal',
            '🇨🇭': 'Switzerland', '🇸🇪': 'Sweden', '🇳🇴': 'Norway', '🇫🇮': 'Finland',
            '🇵🇱': 'Poland', '🇺🇦': 'Ukraine', '🇮🇪': 'Ireland', '🇦🇹': 'Austria',
            '🇧🇪': 'Belgium', '🇩🇰': 'Denmark', '🇨🇿': 'Czech Republic', '🇬🇷': 'Greece',
            '🇿🇦': 'South Africa', '🇪🇬': 'Egypt', '🇸🇦': 'Saudi Arabia', '🇦🇪': 'United Arab Emirates',
            '🇮🇱': 'Israel', '🇮🇷': 'Iran', '🇮🇩': 'Indonesia', '🇲🇾': 'Malaysia',
            '🇹🇭': 'Thailand', '🇻🇳': 'Vietnam', '🇵🇭': 'Philippines', '🇨🇱': 'Chile',
            '🇨🇴': 'Colombia', '🇵🇪': 'Peru'
        }

        # 2. 地图名别名库 (用于高亮区域)
        MAP_NAME_ALIASES = {
            'United States': ['United States of America', 'USA'],
            'United Kingdom': ['United Kingdom', 'UK', 'Great Britain'],
            'China': ['People\'s Republic of China'],
            'Russia': ['Russian Federation'],
            'South Korea': ['Korea', 'Republic of Korea'],
            'Vietnam': ['Viet Nam']
        }

        # 3. 中心点坐标库 (用于点击聚焦)
        COUNTRY_CENTROIDS = {
            'China': [104.19, 35.86], 'United States': [-95.71, 37.09], 'United Kingdom': [-3.43, 55.37],
            'Germany': [10.45, 51.16], 'France': [2.21, 46.22], 'Netherlands': [5.29, 52.13],
            'Russia': [105.31, 61.52], 'Canada': [-106.34, 56.13], 'Brazil': [-51.92, -14.23],
            'Australia': [133.77, -25.27], 'India': [78.96, 20.59], 'Japan': [138.25, 36.20],
            'South Korea': [127.76, 35.90], 'Singapore': [103.81, 1.35], 'Turkey': [35.24, 38.96]
        }
        
        # 特殊城市坐标修正
        CITY_COORDS_FIX = { 
            'Dubai': (25.20, 55.27), 'Frankfurt': (50.11, 8.68), 'Amsterdam': (52.36, 4.90), 
            'San Jose': (37.33, -121.88), 'Phoenix': (33.44, -112.07), 'Tokyo': (35.68, 139.76),
            'Seoul': (37.56, 126.97), 'London': (51.50, -0.12), 'Singapore': (1.35, 103.81)
        }
        
        from collections import Counter
        country_counter = Counter()
        snapshot = list(SERVERS_CACHE)
        
        # 临时存储结构: { 'United States': { flag, cn, total, online, servers: [] } }
        temp_stats_storage = {}

        for s in snapshot:
            s_name = s.get('name', '')
            
            # --- A. 确定国旗与标准名 ---
            # 优先从名字里提取国旗
            flag_icon = "📍"
            map_name_standard = None
            
            # 1. 尝试直接从名字匹配国旗
            for f, m_name in FLAG_TO_MAP_NAME.items():
                if f in s_name:
                    flag_icon = f
                    map_name_standard = m_name
                    break
            
            # 2. 如果没找到，尝试通过分组或检测函数
            if not map_name_standard:
                try:
                    # 这是一个智能检测函数，会返回如 "🇺🇸 美国"
                    group_str = detect_country_group(s_name, s)
                    if group_str:
                        flag_part = group_str.split(' ')[0]
                        # 再次查表
                        if flag_part in FLAG_TO_MAP_NAME:
                            flag_icon = flag_part
                            map_name_standard = FLAG_TO_MAP_NAME[flag_part]
                except: pass

            # 3. 统计饼图数据
            try: country_counter[detect_country_group(s_name, s)] += 1
            except: pass

            # --- B. 确定坐标 (用于飞线) ---
            lat, lon = None, None
            # 优先用修正库
            for city_key, (c_lat, c_lon) in CITY_COORDS_FIX.items():
                if city_key.lower() in s_name.lower(): lat, lon = c_lat, c_lon; break
            # 其次用保存的坐标
            if not lat:
                if 'lat' in s and 'lon' in s: lat, lon = s['lat'], s['lon']
                else: 
                    coords = get_coords_from_name(s_name)
                    if coords: lat, lon = coords[0], coords[1]
            
            # --- C. 生成数据点 ---
            if lat and lon and map_name_standard:
                # 城市点 (圆点)
                coord_key = f"{lat},{lon}"
                if coord_key not in city_points_map: 
                    city_points_map[coord_key] = {'name': s_name, 'value': [lon, lat], 'country_key': map_name_standard}
                
                # 国旗点 (显示在地图上的Emoji)
                if flag_icon != "📍" and flag_icon not in flag_points_map:
                    flag_points_map[flag_icon] = {'name': flag_icon, 'value': [lon, lat], 'country_key': map_name_standard}

            # --- D. 聚合统计数据 (关键修改：填充 servers 列表) ---
            if map_name_standard:
                unique_deployed_countries.add(map_name_standard)
                
                if map_name_standard not in temp_stats_storage:
                    # 获取中文名用于显示 (例如 "美国")
                    cn_name = map_name_standard
                    try: 
                        full_g = detect_country_group(s_name, s)
                        if full_g and ' ' in full_g: cn_name = full_g.split(' ')[1]
                    except: pass

                    temp_stats_storage[map_name_standard] = {
                        'flag': flag_icon, 
                        'cn': cn_name,
                        'total': 0, 'online': 0, 
                        'servers': [] # ✨✨✨ 初始化列表 ✨✨✨
                    }
                
                rs = temp_stats_storage[map_name_standard]
                rs['total'] += 1
                
                # 判断在线状态
                is_on = s.get('_status') == 'online' or (s.get('cpu_usage') is not None)
                if is_on: rs['online'] += 1
                
                # ✨✨✨ 将服务器信息加入列表 ✨✨✨
                rs['servers'].append({
                    'name': s_name,
                    'status': 'online' if is_on else 'offline'
                })

                # 更新中心点 (如果有坐标的话)
                if map_name_standard not in COUNTRY_CENTROIDS and lat and lon:
                    COUNTRY_CENTROIDS[map_name_standard] = [lon, lat]

        # --- E. 数据后处理 ---
        for std_name, stats in temp_stats_storage.items():
            # 1. 对服务器列表进行排序：在线的在前，离线的在后
            stats['servers'].sort(key=lambda x: 0 if x['status'] == 'online' else 1)
            
            region_stats[std_name] = stats
            active_regions_for_highlight.add(std_name)
            
            # 添加别名索引 (让 UK 和 United Kingdom 都能查到数据)
            if std_name in MAP_NAME_ALIASES:
                for alias in MAP_NAME_ALIASES[std_name]:
                    region_stats[alias] = stats
                    active_regions_for_highlight.add(alias)

        # --- F. 生成饼图数据 ---
        pie_data = []
        if country_counter:
            sorted_counts = country_counter.most_common(5)
            for k, v in sorted_counts: pie_data.append({'name': f"{k} ({v})", 'value': v})
            others = sum(country_counter.values()) - sum(x[1] for x in sorted_counts)
            if others > 0: pie_data.append({'name': f"🏳️ 其他 ({others})", 'value': others})
        else: pie_data.append({'name': '暂无数据', 'value': 0})

        city_list = list(city_points_map.values())
        flag_list = list(flag_points_map.values())
        
        return (
            json.dumps({'cities': city_list, 'flags': flag_list, 'regions': list(active_regions_for_highlight)}, ensure_ascii=False), 
            pie_data, 
            len(unique_deployed_countries), 
            json.dumps(region_stats, ensure_ascii=False), # 这里现在包含了 servers 列表
            json.dumps(COUNTRY_CENTROIDS, ensure_ascii=False)
        )
    except Exception as e:
        print(f"[ERROR] prepare_map_data failed: {e}")
        import traceback; traceback.print_exc()
        return (json.dumps({'cities': [], 'flags': [], 'regions': []}), [], 0, "{}", "{}")


# ==========================================
# 👇全局变量定义 👇
# ==========================================
FILE_LOCK = asyncio.Lock()
EXPANDED_GROUPS = set()
SERVER_UI_MAP = {}
# ==========================================

def init_data():
    # 如果强制路径不存在，说明 Docker 挂载失败，必须报错提醒
    if not os.path.exists(DATA_DIR):
        logger.error(f"❌ 严重错误: 找不到数据目录 {DATA_DIR}！请检查 docker-compose volumes 挂载！")
        # 尝试创建以免程序崩溃，但大概率读不到旧数据
        os.makedirs(DATA_DIR)
    
    global SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG
    
    logger.info(f"正在读取数据... (目标: {DATA_DIR})")
    
    # 1. 加载服务器
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: 
                raw_data = json.load(f)
                SERVERS_CACHE = [s for s in raw_data if isinstance(s, dict)]
            logger.info(f"✅ 成功加载服务器: {len(SERVERS_CACHE)} 台")
        except Exception as e:
            logger.error(f"❌ 读取 servers.json 失败: {e}")
            SERVERS_CACHE = []
    else:
        logger.warning(f"⚠️ 未找到服务器配置文件: {CONFIG_FILE}")

    # 2. 加载订阅
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f: SUBS_CACHE = json.load(f)
        except: SUBS_CACHE = []

    # 3. 加载缓存
    if os.path.exists(NODES_CACHE_FILE):
        # 处理之前误生成的文件夹
        if os.path.isdir(NODES_CACHE_FILE):
             try: 
                import shutil
                shutil.rmtree(NODES_CACHE_FILE)
                logger.info("♻️ 已自动删除错误的缓存文件夹")
             except: pass
             NODES_DATA = {}
        else:
            try:
                with open(NODES_CACHE_FILE, 'r', encoding='utf-8') as f: NODES_DATA = json.load(f)
                count = sum([len(v) for v in NODES_DATA.values() if isinstance(v, list)])
                logger.info(f"✅ 加载缓存节点: {count} 个")
            except: NODES_DATA = {}
    else:
        NODES_DATA = {}
        
    # 4. 加载配置
    if os.path.exists(ADMIN_CONFIG_FILE):
        try:
            with open(ADMIN_CONFIG_FILE, 'r', encoding='utf-8') as f: ADMIN_CONFIG = json.load(f)
        except: ADMIN_CONFIG = {}

    # 初始化设置
    if 'probe_enabled' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_enabled'] = True
    if 'probe_token' not in ADMIN_CONFIG:
        ADMIN_CONFIG['probe_token'] = uuid.uuid4().hex

    # 保存一次配置确保持久化
    try:
        with open(ADMIN_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(ADMIN_CONFIG, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ 配置保存失败: {e}")
    # ==========================================================

def _save_file_sync_internal(filename, data):
    # 使用绝对路径生成临时文件
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

# ================= 数据保存函数 =================

# 1. 保存服务器列表
async def save_servers(): 
    global GLOBAL_UI_VERSION # ✨ 关键：引入全局版本变量
    
    # 执行保存
    await safe_save(CONFIG_FILE, SERVERS_CACHE)
    
    # ✨ 关键：更新版本号，通知前台 /status 页面进行结构重绘
    GLOBAL_UI_VERSION = time.time() 
    
    # 触发后台仪表盘数据的静默刷新
    await refresh_dashboard_ui()

# 2. 保存管理配置 (分组/设置)
async def save_admin_config(): 
    global GLOBAL_UI_VERSION # ✨ 关键：引入全局版本变量
    
    # 执行保存
    await safe_save(ADMIN_CONFIG_FILE, ADMIN_CONFIG)
    
    # ✨ 关键：更新版本号，通知前台 /status 页面进行结构重绘 (例如分组变化)
    GLOBAL_UI_VERSION = time.time()

async def save_subs(): await safe_save(SUBS_FILE, SUBS_CACHE)

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

# ================= SSH 连接核心逻辑 (完全隔离版) =================
def get_ssh_client(server_data):
    """建立 SSH 连接"""
    import paramiko # 确保导入
    
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # 解析 IP
    raw_url = server_data['url']
    if '://' in raw_url: host = raw_url.split('://')[-1].split(':')[0]
    else: host = raw_url.split(':')[0]
    
    # 优先使用 ssh_host
    if server_data.get('ssh_host'): host = server_data['ssh_host']
    
    port = int(server_data.get('ssh_port') or 22)
    user = server_data.get('ssh_user') or 'root'
    
    # 获取认证类型
    auth_type = server_data.get('ssh_auth_type', '全局密钥').strip()
    
    print(f"🔌 [SSH Debug] 连接目标: {host}, 用户: {user}, 认证方式: [{auth_type}]", flush=True)
    
    try:
        if auth_type == '独立密码':
            pwd = server_data.get('ssh_password', '')
            if not pwd: raise Exception("选择了独立密码，但密码为空")
            
            # ✨ 强制只用密码，不找密钥，不找Agent
            client.connect(host, port, username=user, password=pwd, timeout=5, 
                           look_for_keys=False, allow_agent=False)
                           
        elif auth_type == '独立密钥':
            key_content = server_data.get('ssh_key', '')
            if not key_content: raise Exception("选择了独立密钥，但密钥为空")
            
            key_file = io.StringIO(key_content)
            try: pkey = paramiko.RSAKey.from_private_key(key_file)
            except: 
                key_file.seek(0)
                try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except: raise Exception("无法识别的私钥格式")
            
            # ✨✨✨ [此处已修改] 同样强制禁止 Agent 和本地其他密钥 ✨✨✨
            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)
            
        else: # 默认：全局密钥
            g_key = load_global_key()
            if not g_key: raise Exception("全局密钥未配置")
            
            key_file = io.StringIO(g_key)
            try: pkey = paramiko.RSAKey.from_private_key(key_file)
            except: 
                key_file.seek(0)
                try: pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except: raise Exception("全局密钥格式无法识别")
            
            # 全局密钥也加上限制，防止它私自去读你电脑本身的 id_rsa
            client.connect(host, port, username=user, pkey=pkey, timeout=5,
                           look_for_keys=False, allow_agent=False)
            
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
                
                # 2. 注入 JS (xterm.js 初始化)
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

                # 3. 建立基础连接 (此时还不启动 Shell)
                self.client, msg = await run.io_bound(get_ssh_client_sync, self.server_data)
                
                if not self.client:
                    self._print_error(msg)
                    return

                # ================= ✨✨✨ 预处理阶段：定制信息格式 ✨✨✨ =================
                
                def pre_login_tasks():
                    last_login_msg = ""
                    try:
                        # 1. 屏蔽广告
                        self.client.exec_command("touch ~/.hushlogin")
                        
                        # 2. 获取原始日志
                        # raw_log 类似: root pts/0 Wed Jan 9 16:30 still logged in 167.234.xx.xx
                        stdin, stdout, stderr = self.client.exec_command("last -n 2 -a | head -n 2 | tail -n 1")
                        raw_log = stdout.read().decode().strip()
                        
                        if raw_log and "wtmp" not in raw_log:
                            # 3. ✂️ Python 字符串切割重组 ✂️
                            parts = raw_log.split()
                            # 确保长度足够防止报错
                            # parts[2:6] 是日期时间 (Wed Jan 9 16:30)
                            # parts[-1] 是 IP 地址 (167.234.xx.xx)
                            if len(parts) >= 7:
                                date_time = " ".join(parts[2:6])
                                ip_addr = parts[-1]
                                # 拼凑最终格式
                                last_login_msg = f"Last login:  {date_time}   {ip_addr}"
                    except: pass
                    return last_login_msg

                # 在后台线程执行
                login_info = await run.io_bound(pre_login_tasks)

                # 3.1 打印定制后的绿色信息
                if login_info:
                    # \x1b[32m 是绿色
                    formatted_msg = f"\r\n\x1b[32m{login_info}\x1b[0m\r\n"
                    b64_msg = base64.b64encode(formatted_msg.encode('utf-8')).decode('utf-8')
                    ui.run_javascript(f'if(window.{self.term_id}) window.{self.term_id}.write(atob("{b64_msg}"));')

                # =========================================================================

                # 4. 启动交互式 Shell
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
                    # 读取原始字节流
                    data = self.channel.recv(4096)
                    if not data: break 
                    
                    # 转为 Base64 以便在 JS 中传输
                    b64_data = base64.b64encode(data).decode('utf-8')
                    
                    # ✨✨✨ [修复核心]：JS 端使用 TextDecoder 正确解码 UTF-8 中文 ✨✨✨
                    js_cmd = f"""
                    if(window.{self.term_id}) {{
                        try {{
                            // 1. 解码 Base64 为二进制字符串
                            var binaryStr = atob("{b64_data}");
                            // 2. 转换为 Uint8Array 字节数组
                            var bytes = new Uint8Array(binaryStr.length);
                            for (var i = 0; i < binaryStr.length; i++) {{
                                bytes[i] = binaryStr.charCodeAt(i);
                            }}
                            // 3. 使用 TextDecoder 按 UTF-8 解码为正确字符
                            var decodedStr = new TextDecoder("utf-8").decode(bytes);
                            
                            // 4. 写入终端
                            window.{self.term_id}.write(decodedStr);
                        }} catch(e) {{
                            console.error("Term Decode Error", e);
                        }}
                    }}
                    """
                    with self.container.client:
                        ui.run_javascript(js_cmd)
                        
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

                # --- 黑色终端区域 + 底部命令栏区域 ---
                terminal_box = ui.column().classes('w-full flex-grow p-0 overflow-hidden relative min-h-0 min-w-0 flex flex-col')
                
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
# =================  Cloudflare API 工具类 =================
class CloudflareHandler:
    def __init__(self):
        self.token = ADMIN_CONFIG.get('cf_api_token', '')
        self.email = ADMIN_CONFIG.get('cf_email', '')
        self.root_domain = ADMIN_CONFIG.get('cf_root_domain', '')
        self.base_url = "https://api.cloudflare.com/client/v4"
        
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.email and "global" in self.token.lower():
            h["X-Auth-Email"] = self.email
            h["X-Auth-Key"] = self.token
        else:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get_zone_id(self, domain_name=None):
        # 如果没有指定域名，用配置的根域名；如果指定了，尝试匹配
        target = self.root_domain
        if domain_name:
            # 简单尝试：如果域名以配置的根域名结尾，就用根域名去查 Zone
            if self.root_domain and domain_name.endswith(self.root_domain):
                target = self.root_domain
            else:
                # 否则尝试推断：取域名的后两段作为 Zone (如 a.b.com -> b.com)
                parts = domain_name.split('.')
                if len(parts) >= 2: target = f"{parts[-2]}.{parts[-1]}"

        url = f"{self.base_url}/zones?name={target}"
        try:
            r = requests.get(url, headers=self._headers(), timeout=10)
            data = r.json()
            if data.get('success') and len(data['result']) > 0:
                return data['result'][0]['id'], None
            return None, f"未找到 Zone: {target}"
        except Exception as e: return None, str(e)

    def set_ssl_flexible(self, zone_id):
        url = f"{self.base_url}/zones/{zone_id}/settings/ssl"
        try:
            payload = {"value": "flexible"}
            r = requests.patch(url, headers=self._headers(), json=payload, timeout=10)
            if r.json().get('success'): return True, "SSL 已强制设为 Flexible"
            # 如果已经是 flexible 可能会报错或者返回成功，视情况忽略错误
            return True, "SSL 设置指令已发送" 
        except Exception as e: return False, str(e)

    def force_enable_proxy(self, zone_id, full_domain):
        """查找现有 DNS 记录并强制开启代理 (小云朵)"""
        url = f"{self.base_url}/zones/{zone_id}/dns_records?name={full_domain}"
        try:
            # 1. 查找记录
            r = requests.get(url, headers=self._headers(), timeout=10)
            data = r.json()
            if not data.get('success') or len(data['result']) == 0:
                return False, "Cloudflare 中未找到此域名的 DNS 记录"
            
            record = data['result'][0]
            record_id = record['id']
            current_ip = record['content']
            current_proxy = record['proxied']
            
            # 2. 如果已经是 Proxied，直接返回
            if current_proxy:
                return True, "代理 (小云朵) 已处于开启状态"
            
            # 3. 更新记录
            update_url = f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}"
            payload = {
                "type": record['type'],
                "name": full_domain,
                "content": current_ip,
                "proxied": True  # ✨ 强制开启
            }
            r_up = requests.put(update_url, headers=self._headers(), json=payload, timeout=10)
            if r_up.json().get('success'): return True, "已自动开启代理 (点亮小云朵)"
            return False, f"开启代理失败: {r_up.text}"
            
        except Exception as e: return False, str(e)

    async def auto_configure(self, ip, sub_prefix):
        """(IP 模式) 全自动流程：新建解析 + 设置 SSL"""
        if not self.token: return False, "未配置 API Token"
        def _task():
            zone_id, err = self.get_zone_id()
            if not zone_id: return False, err
            
            ok, msg_ssl = self.set_ssl_flexible(zone_id)
            if not ok: return False, msg_ssl
            
            full_domain = f"{sub_prefix}.{self.root_domain}"
            # 这里复用之前的 add_dns_record 逻辑 (此处略去重复代码，假设你有 add_dns_record)
            # 为保证完整性，简写一下 add_dns_record 逻辑:
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            payload = {"type": "A", "name": full_domain, "content": ip, "ttl": 1, "proxied": True}
            try: requests.post(url, headers=self._headers(), json=payload, timeout=10)
            except: pass
            
            return True, f"成功! 域名: {full_domain}"
        return await run.io_bound(_task)

    async def fix_existing_domain(self, domain):
        """(域名模式) 修复流程：强制 SSL Flexible + 强制 Proxy"""
        if not self.token: return False, "未配置 API Token"
        def _task():
            # 1. 获取 Zone
            zone_id, err = self.get_zone_id(domain)
            if not zone_id: return False, err
            
            logs = []
            # 2. 设置 SSL
            ok, msg = self.set_ssl_flexible(zone_id)
            logs.append(msg if ok else f"SSL设置失败: {msg}")
            
            # 3. 开启 Proxy
            ok, msg = self.force_enable_proxy(zone_id, domain)
            logs.append(msg if ok else f"代理设置失败: {msg}")
            
            return True, " | ".join(logs)
        return await run.io_bound(_task)


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

# 2. 单个服务器同步逻辑 (修改版：增加 sync_name 开关)
async def fetch_inbounds_safe(server_conf, force_refresh=False, sync_name=False):
    url = server_conf['url']
    name = server_conf.get('name', '未命名')
    
    # 如果不是强制刷新，且缓存里有数据，直接返回缓存
    if not force_refresh and url in NODES_DATA: return NODES_DATA[url]
    
    async with SYNC_SEMAPHORE:
        try:
            mgr = get_manager(server_conf)
            inbounds = await run_in_bg_executor(mgr.get_inbounds)
            if inbounds is None:
                # 登录重试逻辑
                mgr = managers[server_conf['url']] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'], server_conf.get('prefix')) 
                inbounds = await run_in_bg_executor(mgr.get_inbounds)
            
            if inbounds is not None:
                # ✅ 成功：更新内存缓存
                NODES_DATA[url] = inbounds
                server_conf['_status'] = 'online' 
                
                # ================= ✨✨✨ [逻辑修改]：仅当 sync_name=True 时才同步名称 ✨✨✨ =================
                if sync_name: 
                    try:
                        if len(inbounds) > 0:
                            remote_name = inbounds[0].get('remark', '').strip()
                            if remote_name:
                                current_full_name = server_conf.get('name', '')
                                
                                # 分离国旗
                                if ' ' in current_full_name:
                                    parts = current_full_name.split(' ', 1)
                                    current_flag = parts[0]
                                    current_text = parts[1].strip()
                                else:
                                    current_flag = ""
                                    current_text = current_full_name
                                
                                # 比对并更新
                                if current_text != remote_name:
                                    logger.info(f"🔄 [名称同步] (主动触发) 发现变更: {current_text} -> {remote_name}")
                                    if current_flag:
                                        new_name = f"{current_flag} {remote_name}"
                                    else:
                                        new_name = await auto_prepend_flag(remote_name, url)
                                    
                                    server_conf['name'] = new_name
                                    asyncio.create_task(save_servers())
                    except Exception as e:
                        logger.warning(f"⚠️ [名称同步] 异常: {e}")
                # =========================================================================================
                
                return inbounds
            
            # ❌ 失败
            NODES_DATA[url] = [] 
            server_conf['_status'] = 'offline'
            return []
            
        except Exception as e: 
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

 
# =================  单台安装探针 =================
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

# ================= 生成节点链接 (已修复：自动清洗 IP 和 端口) =================
def generate_node_link(node, server_host):
    try:
        # ✨✨✨ 核心修复：清洗 server_host，只保留纯 IP/域名 ✨✨✨
        clean_host = server_host
        # 1. 去掉协议头 (http:// 或 https://)
        if '://' in clean_host:
            clean_host = clean_host.split('://')[-1]
        # 2. 去掉端口 (例如 :54321)
        # 注意：排除 IPv6 ([...]) 的情况，这里简单处理 IPv4 和域名
        if ':' in clean_host and not clean_host.startswith('['):
            clean_host = clean_host.split(':')[0]

        p = node['protocol']; remark = node['remark']; port = node['port']
        # 使用清洗后的 clean_host 作为默认地址
        add = node.get('listen') or clean_host
        
        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = st.get('network', 'tcp'); tls = st.get('security', 'none'); path = ""; host = ""
        
        if net == 'ws': 
            path = st.get('wsSettings',{}).get('path','/')
            host = st.get('wsSettings',{}).get('headers',{}).get('Host','')
        elif net == 'grpc': 
            path = st.get('grpcSettings',{}).get('serviceName','')
        
        if p == 'vmess':
            # 构建标准的 v2 VMess json
            v = {
                "v": "2",
                "ps": remark,
                "add": add,      # 这里现在是纯 IP 了
                "port": port,    # 这里的端口才是节点端口 (如 14789)
                "id": s['clients'][0]['id'],
                "aid": "0",
                "scy": "auto",
                "net": net,
                "type": "none",
                "host": host,
                "path": path,
                "tls": tls
            }
            return "vmess://" + safe_base64(json.dumps(v))
            
        elif p == 'vless':
            params = f"type={net}&security={tls}"
            if path: params += f"&path={path}" if net != 'grpc' else f"&serviceName={path}"
            if host: params += f"&host={host}"
            return f"vless://{s['clients'][0]['id']}@{add}:{port}?{params}#{remark}"
            
        elif p == 'trojan': 
            return f"trojan://{s['clients'][0]['password']}@{add}:{port}?type={net}&security={tls}#{remark}"
            
        elif p == 'shadowsocks': 
            cred = f"{s['method']}:{s['password']}"
            return f"ss://{safe_base64(cred)}@{add}:{port}#{remark}"
            
    except Exception as e: 
        # print(f"Generate Link Error: {e}")
        return ""
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
        # 1. 获取面板节点 (缓存中)
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        
        # 2. 获取自定义节点 (一键部署的 Hy2/XHTTP)
        custom_nodes = srv.get('custom_nodes', []) or []
        
        # === 合并节点列表 ===
        all_nodes = panel_nodes + custom_nodes
        
        if not all_nodes: continue
        
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url); host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url
        
        sub_nodes_set = set(sub.get('nodes', []))
        
        for n in all_nodes:
            # 检查节点 ID 是否在订阅的选择列表中
            # 注意：一键部署的节点在保存时也生成了 uuid 作为 id，所以逻辑通用
            if f"{srv['url']}|{n['id']}" in sub_nodes_set:
                
                # A. 优先使用原始链接 (Hy2/XHTTP 部署时存的完整链接)
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                
                # B. 或者是面板节点，需要生成链接
                else:
                    l = generate_node_link(n, host)
                    if l: links.append(l)
                    
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")

# ================= 分组订阅接口：支持 Tag 和 主分组 =================
@app.get('/sub/group/{group_b64}')
async def group_sub_handler(group_b64: str, request: Request):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)
    
    links = []
    
    # 筛选符合分组的服务器
    target_servers = [
        s for s in SERVERS_CACHE 
        if s.get('group', '默认分组') == group_name or group_name in s.get('tags', [])
    ]
    
    logger.info(f"正在生成分组订阅: [{group_name}]，匹配到 {len(target_servers)} 个服务器")

    for srv in target_servers:
        # 1. 获取面板节点
        panel_nodes = NODES_DATA.get(srv['url'], []) or []
        # 2. 获取自定义节点
        custom_nodes = srv.get('custom_nodes', []) or []
        # === 合并 ===
        all_nodes = panel_nodes + custom_nodes
        
        if not all_nodes: continue
        
        raw_url = srv['url']
        try:
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            parsed = urlparse(raw_url); host = parsed.hostname or raw_url.split('://')[-1].split(':')[0]
        except: host = raw_url
        
        for n in all_nodes:
            if n.get('enable'): 
                # A. 优先使用原始链接
                if n.get('_raw_link'):
                    links.append(n['_raw_link'])
                # B. 生成面板节点链接
                else:
                    l = generate_node_link(n, host)
                    if l: links.append(l)
    
    if not links:
        return Response(f"// Group [{group_name}] is empty or not found", media_type="text/plain; charset=utf-8")
        
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")

# ================= 短链接接口：分组 (智能跟随版) =================
@app.get('/get/group/{target}/{group_b64}')
async def short_group_handler(target: str, group_b64: str, request: Request): # ✨ 1. 注入 request
    try:
        # ✨ 2. 智能获取当前访问的 协议://域名:端口
        # 优先读取用户在"探针设置"里填写的地址，如果没有填，则自动识别当前浏览器地址
        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        
        if custom_base:
            base_url = custom_base
        else:
            # 自动识别：获取当前请求的 Host 头 (例如 example.com 或 1.2.3.4:8080)
            host = request.headers.get('host')
            scheme = request.url.scheme # http 或 https
            base_url = f"{scheme}://{host}"

        # 拼接出让 SubConverter 抓取的地址
        internal_api = f"{base_url}/sub/group/{group_b64}"

        params = {
            "target": target,
            "url": internal_api,
            "insert": "false",
            "list": "true",
            "ver": "4",
            "udp": "true",
            "scv": "true"
        }
        
        # 注意：如果 SubConverter 也是容器，且和面板不在一个网络，这里用 127.0.0.1 可能会失败
        # 建议保持 subconverter:25500 (容器名) 或改为你的真实 IP:25500
        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            # 增加错误提示，方便排查
            err_msg = f"SubConverter Error. Backend: {converter_api}, Target: {internal_api}"
            return Response(err_msg, status_code=502)
    except Exception as e: return Response(f"Error: {str(e)}", status_code=500)
    
# ================= 短链接接口：单个订阅 (智能跟随版) =================
@app.get('/get/sub/{target}/{token}')
async def short_sub_handler(target: str, token: str, request: Request): # ✨ 1. 注入 request
    try:
        sub_obj = next((s for s in SUBS_CACHE if s['token'] == token), None)
        if not sub_obj: return Response("Subscription Not Found", 404)
        
        # ✨ 2. 智能获取当前访问的 协议://域名:端口
        custom_base = ADMIN_CONFIG.get('manager_base_url', '').strip().rstrip('/')
        
        if custom_base:
            base_url = custom_base
        else:
            # 自动识别
            host = request.headers.get('host')
            scheme = request.url.scheme
            base_url = f"{scheme}://{host}"
            
        internal_api = f"{base_url}/sub/{token}"
        
        opt = sub_obj.get('options', {})
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
            params['rename'] = f"{ren_pat}@{ren_rep}"

        converter_api = "http://subconverter:25500/sub"

        def _fetch_sync():
            try: return requests.get(converter_api, params=params, timeout=10)
            except: return None

        response = await run.io_bound(_fetch_sync)
        if response and response.status_code == 200:
            return Response(content=response.content, media_type="text/plain; charset=utf-8")
        else:
            err_msg = f"SubConverter Error. Backend: {converter_api}, Target: {internal_api}"
            return Response(err_msg, status_code=502)
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
        
        # ================= ✨✨✨ 后台任务启动区 ✨✨✨ =================
        
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
# ================= 订阅编辑器 (已增加搜索功能) =================
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
        
        # ✨ 新增：搜索相关状态
        self.search_term = "" 
        self.visible_node_keys = set() # 用于存储当前搜索结果显示的节点Key

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

                # ✨ 修改：操作栏增加搜索框
                with ui.column().classes('w-full gap-2 bg-gray-100 p-3 rounded'):
                    # 第一行：标题和搜索框
                    with ui.row().classes('w-full items-center gap-4'):
                        ui.label('节点列表').classes('font-bold ml-2 flex-shrink-0')
                        # 搜索输入框
                        ui.input(placeholder='🔍 搜索节点或服务器...', on_change=self.on_search_change).props('outlined dense bg-white').classes('flex-grow')

                    # 第二行：全选/清空按钮 (针对当前搜索结果)
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.label('操作当前列表:').classes('text-xs text-gray-500 self-center')
                        ui.button('全选', on_click=lambda: self.toggle_all(True)).props('flat dense size=sm color=primary bg-white')
                        ui.button('清空', on_click=lambda: self.toggle_all(False)).props('flat dense size=sm color=red bg-white')

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

    # ✨ 新增：搜索处理函数
    def on_search_change(self, e):
        self.search_term = str(e.value).lower().strip()
        self.render_list()

    async def load_data(self):
        with self.cont: 
            ui.spinner('dots').classes('self-center mt-10')

        current_servers_snapshot = list(SERVERS_CACHE)
        
        # 并发获取面板节点
        tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in current_servers_snapshot]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        self.groups_data = {}
        self.all_node_keys = set()
        
        for i, srv in enumerate(current_servers_snapshot):
            # 1. 获取面板数据
            nodes = results[i]
            if not nodes or isinstance(nodes, Exception): 
                nodes = NODES_DATA.get(srv['url'], []) or []
            
            # 2. 获取自定义数据 (Hy2/XHTTP)
            custom = srv.get('custom_nodes', []) or []
            
            # === 合并显示 ===
            all_server_nodes = nodes + custom
            
            if all_server_nodes:
                for n in all_server_nodes:
                    # 注册 Key 用于全选功能
                    k = f"{srv['url']}|{n['id']}"
                    self.all_node_keys.add(k)
            
            g_name = srv.get('group', '默认分组') or '默认分组'
            if g_name not in self.groups_data: self.groups_data[g_name] = []
            
            # 将合并后的列表传给 UI 渲染
            self.groups_data[g_name].append({'server': srv, 'nodes': all_server_nodes})

        self.render_list()

    def render_list(self):
        self.cont.clear()
        self.visible_node_keys = set() # 重置可见节点集合

        with self.cont:
            if not self.groups_data:
                ui.label('暂无数据').classes('text-center w-full mt-4')
                return

            sorted_groups = sorted(self.groups_data.keys())
            has_match = False # 标记是否有匹配项

            for g_name in sorted_groups:
                # 预先筛选：检查该分组下是否有符合搜索条件的节点
                servers_in_group = self.groups_data[g_name]
                visible_servers_ui = []
                
                for item in servers_in_group:
                    srv = item['server']
                    nodes = item['nodes']
                    
                    # 筛选符合条件的节点
                    matched_nodes = []
                    for n in nodes:
                        # 搜索匹配逻辑：匹配 节点备注 或 服务器名称
                        if (not self.search_term) or \
                           (self.search_term in n['remark'].lower()) or \
                           (self.search_term in srv['name'].lower()):
                            matched_nodes.append(n)
                            self.visible_node_keys.add(f"{srv['url']}|{n['id']}")

                    if matched_nodes:
                        visible_servers_ui.append({'server': srv, 'nodes': matched_nodes})

                # 如果该分组下有匹配的节点，才渲染该分组
                if visible_servers_ui:
                    has_match = True
                    # 默认展开，如果是搜索状态
                    expand_value = True if self.search_term else True 
                    
                    with ui.expansion(g_name, icon='folder', value=expand_value).classes('w-full border rounded mb-2').style('width: 100%;'):
                        with ui.column().classes('w-full p-0').style('display: flex; flex-direction: column; width: 100%;'):
                            for item in visible_servers_ui:
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
            
            if not has_match:
                ui.label('未找到匹配的节点').classes('text-center w-full mt-4 text-gray-400')

    def on_check(self, key, value):
        if value: self.sel.add(key)
        else: self.sel.discard(key)

    # ✨ 修改：全选逻辑改为只选中/取消选中“当前可见”的节点
    def toggle_all(self, select_state):
        if select_state:
            # 全选：将所有可见节点加入选中集合
            self.sel.update(self.visible_node_keys)
        else:
            # 清空：从选中集合中移除所有可见节点
            self.sel.difference_update(self.visible_node_keys)
        self.render_list()

def open_sub_editor(d):
    with ui.dialog() as dlg: SubEditor(d).ui(dlg); dlg.open()
    
# ================= 全局变量 =================
# 用于记录当前探针页面选中的标签，防止刷新重置
CURRENT_PROBE_TAB = 'ALL' 

# ================= 快捷创建分组弹窗 (升级版：带搜索筛选) =================
def open_quick_group_create_dialog(callback=None):
    # 准备选择状态字典
    selection_map = {s['url']: False for s in SERVERS_CACHE}
    
    # ✨ 新增：存储每一行的 UI 引用，用于控制显隐
    # 结构: { 'url': { 'row': ui_row_element, 'chk': checkbox_element, 'search_text': 'name+ip' } }
    ui_rows = {} 

    with ui.dialog() as d, ui.card().classes('w-full max-w-lg h-[85vh] flex flex-col p-0'):
        
        # 1. 顶部区域：名称 + 搜索
        with ui.column().classes('w-full p-4 border-b bg-gray-50 gap-3 flex-shrink-0'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('新建分组 (标签模式)').classes('text-lg font-bold')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
            
            # 分组名称输入
            name_input = ui.input('分组名称', placeholder='例如: 甲骨文云').props('outlined dense autofocus').classes('w-full bg-white')
            
            # ✨✨✨ 新增：搜索过滤框 ✨✨✨
            search_input = ui.input(placeholder='🔍 搜索筛选服务器 (名称/IP)...').props('outlined dense clearable').classes('w-full bg-white')
            
            # 绑定搜索事件
            def on_search(e):
                keyword = str(e.value).lower().strip()
                for url, item in ui_rows.items():
                    # 匹配逻辑：如果关键字在 (名称 + IP) 里，就显示，否则隐藏
                    is_match = keyword in item['search_text']
                    item['row'].set_visibility(is_match)
            
            search_input.on_value_change(on_search)

        # 2. 中间：选择服务器列表
        with ui.column().classes('w-full flex-grow overflow-hidden relative'):
            # 工具栏
            with ui.row().classes('w-full p-2 bg-gray-100 justify-between items-center border-b flex-shrink-0'):
                ui.label('勾选加入该组:').classes('text-xs font-bold text-gray-500 ml-2')
                with ui.row().classes('gap-1'):
                    # ✨ 逻辑升级：全选只针对【当前可见】的项
                    ui.button('全选 (当前)', on_click=lambda: toggle_visible(True)).props('flat dense size=xs color=primary')
                    ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

            scroll_area = ui.scroll_area().classes('w-full flex-grow p-2')
            with scroll_area:
                with ui.column().classes('w-full gap-1'):
                    # 按名称排序
                    try: sorted_srv = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                    except: sorted_srv = SERVERS_CACHE
                    
                    for s in sorted_srv:
                        # 准备搜索文本 (名称 + IP)
                        search_key = f"{s['name']} {s['url']}".lower()
                        
                        # 渲染每一行
                        with ui.row().classes('w-full items-center p-2 hover:bg-blue-50 rounded border border-transparent hover:border-blue-200 transition cursor-pointer') as row:
                            chk = ui.checkbox(value=False).props('dense')
                            
                            # ✨ [新增] 阻止复选框自身的点击事件冒泡给 row，防止双重触发
                            chk.on('click.stop', lambda: None)

                            # 绑定勾选事件 (保持数据同步)
                            chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                            
                            # ✨ [修改] 点击整行也能勾选 (已修复 .c 错误，并将监听绑定在 row 上)
                            row.on('click', lambda: chk.set_value(not chk.value))

                            # 显示名称
                            ui.label(s['name']).classes('text-sm font-bold text-gray-700 ml-2 truncate flex-grow select-none')
                            
                            # 显示原区域
                            detected = "未知"
                            try: detected = detect_country_group(s['name'], s)
                            except: pass
                            ui.label(detected).classes('text-xs text-gray-400 font-mono')
                        
                        # ✨ 存入字典，供搜索和全选使用
                        ui_rows[s['url']] = {
                            'row': row, 
                            'chk': chk, 
                            'search_text': search_key
                        }

            # ✨ 升级版全选函数
            def toggle_visible(state):
                count = 0
                for item in ui_rows.values():
                    # 只操作当前可见的行
                    if item['row'].visible:
                        item['chk'].value = state # 这会自动触发上面的 on_value_change 更新 selection_map
                        count += 1
                if state and count > 0:
                    safe_notify(f"已选中当前显示的 {count} 个服务器", "positive")

        # 3. 底部：保存 (逻辑保持不变)
        async def save():
            new_name = name_input.value.strip()
            if not new_name: return safe_notify('名称不能为空', 'warning')
            
            existing = set(ADMIN_CONFIG.get('custom_groups', []))
            if new_name in existing: return safe_notify('分组已存在', 'warning')
            
            if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
            ADMIN_CONFIG['custom_groups'].append(new_name)
            await save_admin_config()
            
            count = 0
            for s in SERVERS_CACHE:
                if selection_map.get(s['url'], False):
                    if 'tags' not in s or not isinstance(s['tags'], list): s['tags'] = []
                    if new_name not in s['tags']:
                        s['tags'].append(new_name)
                        count += 1
                    
                    if s.get('group') == new_name:
                        geo_group = "默认分组"
                        try: geo_group = detect_country_group(s['name'], None) 
                        except: pass
                        s['group'] = geo_group

            if count > 0:
                await save_servers()
            
            render_sidebar_content.refresh()
            safe_notify(f'✅ 分组 "{new_name}" 创建成功，{count} 台服务器已打标签', 'positive')
            d.close()
            if callback and callable(callback): 
                try: await callback(new_name)
                except: pass

        with ui.row().classes('w-full p-4 border-t bg-white justify-end gap-2 flex-shrink-0'):
            ui.button('取消', on_click=d.close).props('flat color=grey')
            ui.button('创建并保存', on_click=save).classes('bg-blue-600 text-white shadow-md')

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
            safe_notify("✅ 分组顺序已更新", "positive")
            d.close()

        with ui.row().classes('w-full p-4 border-t bg-white'):
            ui.button('保存顺序', icon='save', on_click=save).classes('w-full bg-slate-900 text-white shadow-lg')
    
    d.open()
import traceback # 引入用于打印报错堆栈

# ================= 探针自定义分组一体化管理器 (修复版：全选/新建逻辑重构) =================
def open_unified_group_manager(mode='manage'):
    # 1. 数据准备
    if 'probe_custom_groups' not in ADMIN_CONFIG: 
        ADMIN_CONFIG['probe_custom_groups'] = []
    
    # 状态字典
    state = {
        'current_group': None,
        'selected_urls': set(), # ✨ 核心：使用一个集合统一管理当前选中的服务器URL
        'checkboxes': {},       # 存储当前页 checkbox 引用
        'page': 1,
        'search_text': ''
    }

    # UI 引用
    view_list_container = None
    server_list_container = None
    title_input = None
    pagination_ref = None 

    # ================= 界面构建 =================
    with ui.dialog() as d, ui.card().classes('w-full max-w-5xl h-[90vh] flex flex-col p-0 gap-0'):
        
        # --- 1. 顶部：视图切换区 ---
        with ui.row().classes('w-full p-3 bg-slate-100 border-b items-center gap-2 overflow-x-auto flex-shrink-0'):
            ui.label('视图列表:').classes('font-bold text-gray-500 mr-2 text-xs')
            ui.button('➕ 新建分组', on_click=lambda: load_group_data(None)).props('unelevated color=green text-color=white size=sm')
            ui.separator().props('vertical').classes('mx-2 h-6')
            view_list_container = ui.row().classes('gap-2 items-center flex-nowrap')
            ui.space()
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # --- 2. 编辑区头部 ---
        with ui.row().classes('w-full p-4 bg-white border-b items-center gap-4 flex-shrink-0 wrap'):
            title_input = ui.input('视图名称', placeholder='请输入分组名称...').props('outlined dense').classes('min-w-[200px] flex-grow font-bold')
            
            # 搜索框
            ui.input(placeholder='🔍 搜索服务器...', on_change=lambda e: update_search(e.value)).props('outlined dense dense').classes('w-48')

            with ui.row().classes('gap-2'):
                ui.button('全选本页', on_click=lambda: toggle_page_all(True)).props('flat dense size=sm color=blue')
                ui.button('清空本页', on_click=lambda: toggle_page_all(False)).props('flat dense size=sm color=grey')

        # --- 3. 服务器列表 ---
        with ui.scroll_area().classes('w-full flex-grow p-4 bg-gray-50'):
            server_list_container = ui.column().classes('w-full gap-2')
            
        # --- 3.5 分页 ---
        with ui.row().classes('w-full p-2 justify-center bg-gray-50 border-t border-gray-200'):
            pagination_ref = ui.row() 

        # --- 4. 底部保存 ---
        with ui.row().classes('w-full p-4 bg-white border-t justify-between items-center flex-shrink-0'):
            ui.button('删除此视图', icon='delete', color='red', on_click=lambda: delete_current_group()).props('flat')
            ui.button('保存当前配置', icon='save', on_click=lambda: save_current_group()).classes('bg-slate-900 text-white shadow-lg')

    # ================= 逻辑定义 =================

    def update_search(val):
        state['search_text'] = str(val).lower().strip()
        state['page'] = 1 
        render_servers()

    def render_views():
        view_list_container.clear()
        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        with view_list_container:
            for g in groups:
                is_active = (g == state['current_group'])
                btn_props = 'unelevated color=blue' if is_active else 'outline color=grey text-color=grey-8'
                ui.button(g, on_click=lambda _, name=g: load_group_data(name)).props(f'{btn_props} size=sm')

    def load_group_data(group_name):
        state['current_group'] = group_name
        state['page'] = 1
        state['selected_urls'] = set() # 清空选中状态
        
        # 如果是编辑模式，预加载已有的服务器到集合中
        if group_name:
            for s in SERVERS_CACHE:
                # 兼容 tags 和 old group 字段
                if (group_name in s.get('tags', [])) or (s.get('group') == group_name):
                    state['selected_urls'].add(s['url'])
                    
        render_views()
        title_input.value = group_name if group_name else ''
        if not group_name: title_input.run_method('focus')
        render_servers()

    def render_servers():
        server_list_container.clear()
        pagination_ref.clear()
        state['checkboxes'] = {} 
        
        if not SERVERS_CACHE:
            with server_list_container: ui.label('暂无服务器').classes('text-center text-gray-400 mt-10 w-full')
            return

        # 1. 过滤
        all_srv = SERVERS_CACHE
        if state['search_text']:
            all_srv = [s for s in all_srv if state['search_text'] in s.get('name', '').lower() or state['search_text'] in s.get('url', '').lower()]
        
        try: sorted_servers = sorted(all_srv, key=lambda x: str(x.get('name', '')))
        except: sorted_servers = all_srv

        # 2. 分页
        PAGE_SIZE = 48 
        total_items = len(sorted_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if state['page'] > total_pages: state['page'] = 1
        if state['page'] < 1: state['page'] = 1
        
        start_idx = (state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = sorted_servers[start_idx:end_idx]

        # 3. 渲染
        with server_list_container:
            ui.label(f"共 {total_items} 台 (第 {state['page']}/{total_pages} 页)").classes('text-xs text-gray-400 mb-2')

            with ui.grid().classes('w-full grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2'):
                for s in current_page_items:
                    url = s.get('url')
                    if not url: continue
                    
                    # ✨ 核心：状态只看 state['selected_urls'] 集合
                    is_checked = url in state['selected_urls']
                    
                    bg_cls = 'bg-blue-50 border-blue-300' if is_checked else 'bg-white border-gray-200'
                    
                    with ui.row().classes(f'items-center p-2 border rounded cursor-pointer hover:border-blue-400 transition {bg_cls}') as row:
                        chk = ui.checkbox(value=is_checked).props('dense')
                        state['checkboxes'][url] = chk
                        
                        # 单行点击逻辑
                        def toggle_row(c=chk, r=row, u=url): 
                            c.value = not c.value
                            update_selection(u, c.value)
                            # 样式手动更新，避免重绘整个列表
                            if c.value: r.classes(add='bg-blue-50 border-blue-300', remove='bg-white border-gray-200')
                            else: r.classes(remove='bg-blue-50 border-blue-300', add='bg-white border-gray-200')

                        row.on('click', toggle_row)
                        chk.on('click.stop', lambda _, c=chk, r=row, u=url: [update_selection(u, c.value), 
                            r.classes(add='bg-blue-50 border-blue-300', remove='bg-white border-gray-200') if c.value else r.classes(remove='bg-blue-50 border-blue-300', add='bg-white border-gray-200')])

                        with ui.column().classes('gap-0 ml-2 overflow-hidden'):
                            ui.label(s.get('name', 'Unknown')).classes('text-sm font-bold truncate text-gray-700')
                            # 仅提示当前状态，不做逻辑判断
                            if is_checked: ui.label('已选中').classes('text-[10px] text-blue-500 font-bold')
                            else: ui.label(s.get('group','')).classes('text-[10px] text-gray-300')

        # 4. 分页器
        if total_pages > 1:
            with pagination_ref:
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue')
                p.value = state['page']
                p.on('update:model-value', lambda e: [state.update({'page': e.args}), render_servers()])

    def update_selection(url, checked):
        if checked: state['selected_urls'].add(url)
        else: state['selected_urls'].discard(url)

    # ✨ 修复后的全选逻辑：遍历当前页 checkbox，更新集合 + 刷新 UI
    def toggle_page_all(val):
        for url in state['checkboxes'].keys():
            if val: state['selected_urls'].add(url)
            else: state['selected_urls'].discard(url)
        render_servers() # 重新渲染以更新 checkbox 状态和样式

    async def save_current_group():
        old_name = state['current_group']
        new_name = title_input.value.strip()
        if not new_name: return safe_notify("名称不能为空", "warning")

        groups = ADMIN_CONFIG.get('probe_custom_groups', [])
        
        # 1. 维护分组名列表
        if not old_name: # 新建
            if new_name in groups: return safe_notify("名称已存在", "negative")
            groups.append(new_name)
        elif new_name != old_name: # 改名
            if new_name in groups: return safe_notify("名称已存在", "negative")
            idx = groups.index(old_name)
            groups[idx] = new_name
            
            # 顺便把所有机器上的旧 tag 换成新 tag
            for s in SERVERS_CACHE:
                if 'tags' in s and old_name in s['tags']:
                    s['tags'].remove(old_name)
                    s['tags'].append(new_name)

        # 2. 应用选中状态到 tags
        # 遍历所有服务器，如果在 selected_urls 里 -> 加 tag，不在 -> 删 tag
        for s in SERVERS_CACHE:
            if 'tags' not in s: s['tags'] = []
            
            if s['url'] in state['selected_urls']:
                if new_name not in s['tags']: s['tags'].append(new_name)
            else:
                # 只有当这是编辑现有分组，或者改名后的分组时，才需要移除
                # 如果是新建分组，原本就没有这个 tag，这里 remove 会抛错吗？不会，list.remove 需要 try
                if new_name in s['tags']: s['tags'].remove(new_name)
                # 如果改名了，旧名字上面已经处理过了

        ADMIN_CONFIG['probe_custom_groups'] = groups
        await save_admin_config()
        await save_servers()
        
        safe_notify(f"✅ 保存成功", "positive")
        load_group_data(new_name)
        
        # ✨ 修复报错：加上 await
        try: await render_probe_page()
        except: pass

    async def delete_current_group():
        target = state['current_group']
        if not target: return
        
        if target in ADMIN_CONFIG.get('probe_custom_groups', []):
            ADMIN_CONFIG['probe_custom_groups'].remove(target)
            await save_admin_config()
        
        for s in SERVERS_CACHE:
            if 'tags' in s and target in s['tags']: s['tags'].remove(target)
        await save_servers()
        
        safe_notify("🗑️ 已删除", "positive")
        load_group_data(None)
        
        # ✨ 修复报错：加上 await
        try: await render_probe_page()
        except: pass

    # --- 初始化 ---
    def init():
        render_views()
        load_group_data(None)
    
    ui.timer(0.1, init, once=True)
    d.open()
# ================= ✨✨✨ 详情弹窗逻辑✨✨✨ =================
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

# ================= 探针设置页 =================
async def render_probe_page():
    # 1. 标记当前视图状态
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'PROBE'
    
    # 2. 清理并初始化容器 (垂直居中)
    content_container.clear()
    content_container.classes(replace='w-full h-full overflow-y-auto p-6 bg-slate-50 relative flex flex-col justify-center items-center')
    
    if not ADMIN_CONFIG.get('probe_enabled'):
        ADMIN_CONFIG['probe_enabled'] = True
        await save_admin_config()

    # 3. 渲染布局 (直接开始渲染正式页面)
    with content_container:
        with ui.column().classes('w-full max-w-7xl gap-6'):
            
            # --- 标题栏 ---
            with ui.row().classes('w-full items-center gap-3'):
                 with ui.element('div').classes('p-2 bg-blue-600 rounded-lg shadow-sm'):
                     ui.icon('tune', color='white').classes('text-2xl')
                 with ui.column().classes('gap-0'):
                    ui.label('探针管理与设置').classes('text-2xl font-extrabold text-slate-800 tracking-tight')
                    ui.label('Probe Configuration & Management').classes('text-xs font-bold text-gray-400 uppercase tracking-widest')

            # --- 核心网格布局 (左右 4:3 比例) ---
            # lg:grid-cols-7 将网格分为 7 份
            with ui.grid().classes('w-full grid-cols-1 lg:grid-cols-7 gap-6 items-stretch'):
                
                # ======================= 左侧：参数设置区 (占 4/7) =======================
                with ui.column().classes('lg:col-span-4 w-full gap-6'):
                    
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

                # ======================= 右侧：快捷操作区 (占 3/7) =======================
                with ui.column().classes('lg:col-span-3 w-full gap-6 h-full'):
                    
                    # --- 卡片 A: 快捷操作 ---
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
                                # ✨ 分组管理
                                ui.button('分组管理', icon='settings', on_click=lambda: open_unified_group_manager('manage')) \
                                    .classes('flex-1 bg-blue-50 text-blue-700 border border-blue-200 shadow-sm hover:bg-blue-100 font-bold')

                                # 排序视图
                                ui.button('排序', icon='sort', on_click=open_group_sort_dialog) \
                                    .classes('flex-1 bg-gray-50 text-gray-700 border border-gray-200 shadow-sm hover:bg-gray-100 font-bold')
                            
                            # 3. 更新所有探针
                            async def reinstall_all():
                                safe_notify("正在后台更新所有探针脚本...", "ongoing")
                                await batch_install_all_probes()
                            
                            ui.button('更新所有探针', icon='system_update_alt', on_click=reinstall_all) \
                                .classes('w-full bg-orange-50 text-orange-700 border border-orange-200 shadow-sm hover:bg-orange-100 font-bold align-left')

                    # --- 卡片 B: 公开监控页入口 ---
                    with ui.card().classes('w-full p-6 bg-gradient-to-br from-slate-800 to-slate-900 text-white rounded-xl shadow-lg relative overflow-hidden group cursor-pointer flex-grow flex flex-col justify-center') \
                        .on('click', lambda: ui.navigate.to('/status', new_tab=True)):
                        
                        ui.icon('public', size='10rem').classes('absolute -right-8 -bottom-8 text-white opacity-10 group-hover:rotate-12 transition transform duration-500')
                        
                        ui.label('公开监控墙').classes('text-2xl font-bold mb-2')
                        ui.label('点击前往查看实时状态地图').classes('text-sm text-gray-400 mb-6')
                        
                        with ui.row().classes('items-center gap-2 text-blue-400 font-bold text-base group-hover:gap-3 transition-all'):
                            ui.label('立即前往')
                            ui.icon('arrow_forward')

                    # --- 卡片 C: 数据统计 ---
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
                           
    
# ================= 订阅管理视图 (极简模式：只显在线) =================
async def load_subs_view():
    # 标记当前视图
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'SUBS'
    CURRENT_VIEW_STATE['data'] = None
    show_loading(content_container)
    
    try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
    except: origin = ""
    if not origin: origin = "https://xui-manager.sijuly.nyc.mn"

    content_container.clear()
    
    # === 1. 预先统计所有当前存在的节点 Key (面板 + 自定义) ===
    all_active_keys = set()
    for srv in SERVERS_CACHE:
        # 面板节点
        panel = NODES_DATA.get(srv['url'], []) or []
        # 自定义节点
        custom = srv.get('custom_nodes', []) or []
        
        for n in (panel + custom):
            key = f"{srv['url']}|{n['id']}"
            all_active_keys.add(key)
    # =======================================================

    with content_container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4')
        with ui.row().classes('w-full mb-4 justify-end'): 
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_sub_editor(None))
        
        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes('w-full p-4 mb-2 shadow-sm hover:shadow-md transition border-l-4 border-blue-500'):
                with ui.row().classes('justify-between w-full items-center'):
                    with ui.column().classes('gap-1'):
                        ui.label(sub['name']).classes('font-bold text-lg text-slate-800')
                        
                        # 计算有效节点数
                        saved_node_ids = set(sub.get('nodes', []))
                        valid_count = len(saved_node_ids.intersection(all_active_keys))
                        
                        color_cls = 'text-green-600' if valid_count > 0 else 'text-gray-400'
                        ui.label(f"⚡ 在线节点: {valid_count}").classes(f'text-xs font-bold {color_cls}')
                    
                    with ui.row().classes('gap-2'):
                        # ✨ 修改点：这里删除了 'tune' (配置处理策略) 按钮
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


# ================= 通用服务器保存函数 (UI 操控版：彻底消除闪烁 + 列表同步) =================
async def save_server_config(server_data, is_add=True, idx=None):
    # 1. 基础校验
    if not server_data.get('name') or not server_data.get('url'):
        safe_notify("名称和地址不能为空", "negative"); return False

    # 记录旧信息
    old_group = None
    if not is_add and idx is not None and 0 <= idx < len(SERVERS_CACHE):
        old_group = SERVERS_CACHE[idx].get('group')

    # 2. 逻辑处理
    if is_add:
        for s in SERVERS_CACHE:
            if s['url'] == server_data['url']: safe_notify(f"已存在！", "warning"); return False
        
        has_flag = False
        for v in AUTO_COUNTRY_MAP.values():
            if v.split(' ')[0] in server_data['name']: has_flag = True; break
        if not has_flag and '🏳️' not in server_data['name']: server_data['name'] = f"🏳️ {server_data['name']}"

        SERVERS_CACHE.append(server_data)
        safe_notify(f"已添加: {server_data['name']}", "positive")
    else:
        if idx is not None and 0 <= idx < len(SERVERS_CACHE):
            # 直接更新字典，UI 会自动响应（因为有 bind_text_from）
            SERVERS_CACHE[idx].update(server_data)
            safe_notify(f"已更新: {server_data['name']}", "positive")
        else:
            safe_notify("目标不存在", "negative"); return False

    # 3. 保存到硬盘
    await save_servers()

    # ================= ✨✨✨ UI 零闪烁操作区 ✨✨✨ =================
    # 获取新分组名称
    new_group = server_data.get('group', '默认分组')
    # 计算新分组对应的区域
    if new_group in ['默认分组', '自动注册', '未分组', '自动导入']:
        try: new_group = detect_country_group(server_data.get('name', ''), server_data)
        except: pass
        if not new_group: new_group = '🏳️ 其他地区'

    need_full_refresh = False

    try:
        if is_add:
            # === 新增 ===
            if new_group in SIDEBAR_UI_REFS['groups']:
                with SIDEBAR_UI_REFS['groups'][new_group]:
                    render_single_sidebar_row(server_data)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True
                
        elif old_group != new_group:
            # === 移动分组 ===
            row_el = SIDEBAR_UI_REFS['rows'].get(server_data['url'])
            target_col = SIDEBAR_UI_REFS['groups'].get(new_group)
            
            if row_el and target_col:
                row_el.move(target_col)
                EXPANDED_GROUPS.add(new_group)
            else:
                need_full_refresh = True
        
    except Exception as e:
        logger.error(f"UI Move Error: {e}")
        need_full_refresh = True

    if need_full_refresh:
        try: render_sidebar_content.refresh()
        except: pass

    # ================= ✨✨✨ 右侧主视图同步逻辑 (修正版) ✨✨✨ =================
    current_scope = CURRENT_VIEW_STATE.get('scope')
    current_data = CURRENT_VIEW_STATE.get('data')
    
    # 情况1: 如果当前正在查看这台服务器的详情页
    if current_scope == 'SINGLE' and (current_data == server_data or (is_add and server_data == SERVERS_CACHE[-1])):
        try: await refresh_content('SINGLE', server_data, force_refresh=True)
        except: pass
        
    # 情况2: 如果当前在列表视图 (全部/分组/区域)，立即静默重绘列表
    elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
        # ⚠️ 关键修改：强制置空 scope 以绕过 refresh_content 内部的防抖判断
        # 这样可以触发 UI 重绘 (增/删行)，但 force_refresh=False 不会触发 API 重新请求
        CURRENT_VIEW_STATE['scope'] = None 
        try: await refresh_content(current_scope, current_data, force_refresh=False)
        except: pass
        
    elif current_scope == 'DASHBOARD':
        try: await refresh_dashboard_ui()
        except: pass

    # ================= ✨ 后台任务 ✨ =================
    asyncio.create_task(fast_resolve_single_server(server_data))
    if ADMIN_CONFIG.get('probe_enabled', False) and server_data.get('probe_installed', False):
        async def delayed_install():
            await asyncio.sleep(1)
            await install_probe_on_server(server_data)
        asyncio.create_task(delayed_install())
        
    return True


                        
# ================= 小巧卡片式弹窗 (修复版：删除同步优化) =================
async def open_server_dialog(idx=None):
    is_edit = idx is not None
    original_data = SERVERS_CACHE[idx] if is_edit else {}
    data = original_data.copy()
    
    # --- 1. 智能检测初始状态 ---
    if is_edit:
        has_xui_conf = bool(data.get('url') and data.get('user') and data.get('pass'))
        raw_ssh_host = data.get('ssh_host')
        if not raw_ssh_host and not has_xui_conf: 
            raw_ssh_host = data.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
        
        has_ssh_conf = bool(
            raw_ssh_host or 
            data.get('ssh_user') or 
            data.get('ssh_key') or 
            data.get('ssh_password') or 
            data.get('probe_installed')
        )
        if not has_ssh_conf and not has_xui_conf: has_ssh_conf = True
    else:
        has_xui_conf = True; has_ssh_conf = True

    state = {'ssh_active': has_ssh_conf, 'xui_active': has_xui_conf}

    with ui.dialog() as d, ui.card().classes('w-full max-w-sm p-5 flex flex-col gap-4'):
        
        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('编辑服务器' if is_edit else '添加服务器').classes('text-lg font-bold')
            tabs = ui.tabs().classes('text-blue-600')
            with tabs:
                t_ssh = ui.tab('SSH / 探针', icon='terminal')
                t_xui = ui.tab('X-UI面板', icon='settings')

        # ================= 独立的基础信息保存逻辑 =================
        async def save_basic_info_only():
            if not is_edit: 
                safe_notify("新增服务器请使用下方的保存按钮", "warning")
                return

            new_name = name_input.value.strip()
            new_group = group_input.value
            
            if not new_name: new_name = await generate_smart_name(data)
            
            SERVERS_CACHE[idx]['name'] = new_name
            SERVERS_CACHE[idx]['group'] = new_group
            
            await save_servers()
            render_sidebar_content.refresh()
            
            # ✨ 基础信息修改同步刷新右侧
            current_scope = CURRENT_VIEW_STATE.get('scope')
            if current_scope == 'SINGLE' and CURRENT_VIEW_STATE.get('data') == SERVERS_CACHE[idx]:
                try: await refresh_content('SINGLE', SERVERS_CACHE[idx])
                except: pass
            elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                # ⚠️ 关键修改：强制重绘
                CURRENT_VIEW_STATE['scope'] = None
                try: await refresh_content(current_scope, CURRENT_VIEW_STATE.get('data'), force_refresh=False)
                except: pass
            
            safe_notify("✅ 基础信息已更新", "positive")
            d.close()
            
        # --- 通用字段区域 ---
        with ui.column().classes('w-full gap-2'):
            name_input = ui.input(value=data.get('name',''), label='备注名称 (留空自动获取)').classes('w-full').props('outlined dense')
            
            with ui.row().classes('w-full items-center gap-2 no-wrap'):
                group_input = ui.select(options=get_all_groups(), value=data.get('group','默认分组'), new_value_mode='add-unique', label='分组').classes('flex-grow').props('outlined dense')
                
                if is_edit:
                    ui.button(icon='save', on_click=save_basic_info_only) \
                        .props('flat dense round color=primary') \
                        .tooltip('仅保存名称和分组 (不重新部署)')

        inputs = {}
        btn_keycap_blue = 'bg-white rounded-lg font-bold tracking-wide border-t border-x border-gray-100 border-b-4 border-blue-100 text-blue-600 px-4 py-1 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-blue-50'
        btn_keycap_delete = 'bg-white rounded-xl font-bold tracking-wide w-full border-t border-x border-gray-100 border-b-4 border-red-100 text-red-500 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1 hover:bg-red-50'
        btn_keycap_red_confirm = 'rounded-lg font-bold tracking-wide text-white border-b-4 border-red-900 transition-all duration-100 active:border-b-0 active:border-t-4 active:translate-y-1'

        # ==================== 保存逻辑 (完整保存) ====================
        async def save_panel_data(panel_type):
            final_name = name_input.value.strip()
            final_group = group_input.value
            new_server_data = data.copy()
            new_server_data['group'] = final_group

            if panel_type == 'ssh':
                if not inputs.get('ssh_host'): return
                s_host = inputs['ssh_host'].value.strip()
                if not s_host: safe_notify("SSH 主机 IP 不能为空", "negative"); return

                new_server_data.update({
                    'ssh_host': s_host,
                    'ssh_port': inputs['ssh_port'].value.strip(),
                    'ssh_user': inputs['ssh_user'].value.strip(),
                    'ssh_auth_type': inputs['auth_type'].value,
                    'ssh_password': inputs['ssh_pwd'].value if inputs['ssh_pwd'] else '',
                    'ssh_key': inputs['ssh_key'].value if inputs['ssh_key'] else '',
                    'probe_installed': data.get('probe_installed', True)
                })
                if not new_server_data.get('url'): new_server_data['url'] = f"http://{s_host}:22"

            elif panel_type == 'xui':
                if not inputs.get('xui_url'): return
                x_url_raw = inputs['xui_url'].value.strip()
                x_user = inputs['xui_user'].value.strip()
                x_pass = inputs['xui_pass'].value.strip()
                
                if not (x_url_raw and x_user and x_pass): 
                    safe_notify("必填项不能为空", "negative"); return

                if '://' not in x_url_raw: x_url_raw = f"http://{x_url_raw}"
                try:
                    parts = x_url_raw.split('://')
                    body = parts[1]
                    if ':' not in body:
                        x_url_raw = f"{x_url_raw}:54321"
                        safe_notify(f"已自动添加默认端口: {x_url_raw}", "positive")
                except: pass

                probe_val = inputs['probe_chk'].value
                new_server_data.update({
                    'url': x_url_raw, 'user': x_user, 'pass': x_pass,
                    'prefix': inputs['xui_prefix'].value.strip(),
                    'probe_installed': probe_val
                })
                
                if probe_val:
                    if not new_server_data.get('ssh_host'):
                        if '://' in x_url_raw: new_server_data['ssh_host'] = x_url_raw.split('://')[-1].split(':')[0]
                        else: new_server_data['ssh_host'] = x_url_raw.split(':')[0]
                    if not new_server_data.get('ssh_port'): new_server_data['ssh_port'] = '22'
                    if not new_server_data.get('ssh_user'): new_server_data['ssh_user'] = 'root'
                    if not new_server_data.get('ssh_auth_type'): new_server_data['ssh_auth_type'] = '全局密钥'

            if not final_name:
                safe_notify("正在生成名称...", "ongoing")
                final_name = await generate_smart_name(new_server_data)
            new_server_data['name'] = final_name

            success = await save_server_config(new_server_data, is_add=not is_edit, idx=idx)
            
            if success:
                data.update(new_server_data)
                if panel_type == 'ssh': state['ssh_active'] = True
                if panel_type == 'xui': state['xui_active'] = True
                
                if panel_type == 'xui' and new_server_data.get('probe_installed'):
                    state['ssh_active'] = True

                if (panel_type == 'ssh' or panel_type == 'xui') and new_server_data.get('probe_installed'):
                     safe_notify(f"🚀 配置已保存，后台推送 Agent...", "ongoing")
                     asyncio.create_task(install_probe_on_server(new_server_data))
                else:
                     safe_notify(f"✅ {panel_type.upper()} 已保存", "positive")

        # ==================== SSH 面板渲染 ====================
        @ui.refreshable
        def render_ssh_panel():
            if not state['ssh_active']:
                with ui.column().classes('w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('terminal', color='grey').classes('text-4xl mb-2')
                    ui.label('SSH 功能未启用').classes('text-gray-500 font-bold mb-2')
                    ui.button('启用 SSH 配置', icon='add', on_click=lambda: _activate_panel('ssh')).props('flat bg-blue-50 text-blue-600')
            else:
                init_host = data.get('ssh_host')
                if not init_host and is_edit:
                     if '://' in data.get('url', ''): init_host = data.get('url', '').split('://')[-1].split(':')[0]
                     else: init_host = data.get('url', '').split(':')[0]

                inputs['ssh_host'] = ui.input(label='SSH 主机 IP', value=init_host).classes('w-full').props('outlined dense')
                
                with ui.column().classes('w-full gap-3'):
                    with ui.row().classes('w-full gap-2'):
                        inputs['ssh_user'] = ui.input(value=data.get('ssh_user','root'), label='SSH 用户').classes('flex-1').props('outlined dense')
                        inputs['ssh_port'] = ui.input(value=data.get('ssh_port','22'), label='端口').classes('w-1/3').props('outlined dense')
                    
                    valid_auth_options = ['全局密钥', '独立密码', '独立密钥']
                    current_auth = data.get('ssh_auth_type', '全局密钥')
                    if current_auth not in valid_auth_options: current_auth = '全局密钥'
                    
                    inputs['auth_type'] = ui.select(valid_auth_options, value=current_auth, label='认证方式').classes('w-full').props('outlined dense options-dense')

                    inputs['ssh_pwd'] = ui.input(label='SSH 密码', password=True, value=data.get('ssh_password','')).classes('w-full').props('outlined dense')
                    inputs['ssh_pwd'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密码')
                    
                    inputs['ssh_key'] = ui.textarea(label='SSH 私钥', value=data.get('ssh_key','')).classes('w-full').props('outlined dense rows=3 input-class=font-mono text-xs')
                    inputs['ssh_key'].bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥')
                
                ui.separator().classes('my-1')
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('✅ 自动使用全局私钥').bind_visibility_from(inputs['auth_type'], 'value', value='全局密钥').classes('text-green-600 text-xs font-bold')
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密码') 
                    ui.element('div').bind_visibility_from(inputs['auth_type'], 'value', value='独立密钥') 
                    
                    ui.button('保存 SSH', icon='save', on_click=lambda: save_panel_data('ssh')).props('flat').classes(btn_keycap_blue)

        # ==================== X-UI 面板渲染 ====================
        @ui.refreshable
        def render_xui_panel():
            if not state['xui_active']:
                with ui.column().classes('w-full h-48 justify-center items-center bg-gray-50 rounded border border-dashed border-gray-300'):
                    ui.icon('settings_applications', color='grey').classes('text-4xl mb-2')
                    ui.label('X-UI 面板未配置').classes('text-gray-500 font-bold mb-2')
                    ui.button('配置 X-UI 信息', icon='add', on_click=lambda: _activate_panel('xui')).props('flat bg-purple-50 text-purple-600')
            else:
                inputs['xui_url'] = ui.input(value=data.get('url',''), label='面板 URL (http://ip:port)').classes('w-full').props('outlined dense')
                ui.label('默认端口 54321，如不填写将自动补全').classes('text-[10px] text-gray-400 ml-1 -mt-1 mb-1')
                
                with ui.row().classes('w-full gap-2'):
                    inputs['xui_user'] = ui.input(value=data.get('user',''), label='账号').classes('flex-1').props('outlined dense')
                    inputs['xui_pass'] = ui.input(value=data.get('pass',''), label='密码', password=True).classes('flex-1').props('outlined dense')
                inputs['xui_prefix'] = ui.input(value=data.get('prefix',''), label='API 前缀 (选填)').classes('w-full').props('outlined dense')

                ui.separator().classes('my-1')
                
                with ui.row().classes('w-full justify-between items-center'):
                    inputs['probe_chk'] = ui.checkbox('启用 Root 探针', value=data.get('probe_installed', False))
                    inputs['probe_chk'].classes('text-sm font-bold text-slate-700')
                    
                    ui.button('保存 X-UI', icon='save', on_click=lambda: save_panel_data('xui')).props('flat').classes(btn_keycap_blue)

                ui.label('提示: 启用探针需先配置 SSH 登录信息').classes('text-[10px] text-red-500 ml-8 -mt-2')

                def auto_fill_ssh():
                    if inputs['probe_chk'].value and state['ssh_active'] and inputs.get('ssh_host') and not inputs['ssh_host'].value:
                        p_url = inputs['xui_url'].value
                        if p_url:
                            clean_ip = p_url.split('://')[-1].split(':')[0]
                            if ':' in clean_ip: clean_ip = clean_ip.split(':')[0]
                            inputs['ssh_host'].set_value(clean_ip)
                inputs['probe_chk'].on_value_change(auto_fill_ssh)

        def _activate_panel(panel_type):
            state[f'{panel_type}_active'] = True
            if panel_type == 'ssh': render_ssh_panel.refresh()
            elif panel_type == 'xui': render_xui_panel.refresh()

        default_tab = t_ssh
        if is_edit and not state['ssh_active'] and state['xui_active']: default_tab = t_xui

        with ui.tab_panels(tabs, value=default_tab).classes('w-full animated fadeIn'):
            with ui.tab_panel(t_ssh).classes('p-0 flex flex-col gap-3'):
                render_ssh_panel()
            with ui.tab_panel(t_xui).classes('p-0 flex flex-col gap-3'):
                render_xui_panel()

        # ================= 5. 全局删除逻辑 (已修复：删除后立即重绘右侧列表) =================
        if is_edit:
            with ui.row().classes('w-full justify-start mt-4 pt-2 border-t border-gray-100'):
                async def open_delete_confirm():
                    with ui.dialog() as del_d, ui.card().classes('w-80 p-4'):
                        ui.label('删除确认').classes('text-lg font-bold text-red-600')
                        ui.label('请选择要删除的内容：').classes('text-sm text-gray-600 mb-2')
                        
                        real_ssh_exists = bool(data.get('ssh_host') or data.get('ssh_user'))
                        real_xui_exists = bool(data.get('url') and data.get('user') and data.get('pass'))

                        if not real_ssh_exists and not real_xui_exists:
                            real_ssh_exists = True; real_xui_exists = True

                        chk_ssh = ui.checkbox('SSH 连接信息', value=real_ssh_exists).classes('text-sm font-bold')
                        chk_xui = ui.checkbox('X-UI 面板信息', value=real_xui_exists).classes('text-sm font-bold')
                        
                        if not real_ssh_exists: chk_ssh.value = False; chk_ssh.disable()
                        if not real_xui_exists: chk_xui.value = False; chk_xui.disable()
                        if real_ssh_exists and not real_xui_exists: chk_ssh.disable()
                        if real_xui_exists and not real_ssh_exists: chk_xui.disable()

                        async def confirm_execution():
                            if idx >= len(SERVERS_CACHE): return
                            target_srv = SERVERS_CACHE[idx]
                            
                            will_delete_ssh = chk_ssh.value
                            will_delete_xui = chk_xui.value
                            
                            remaining_ssh = real_ssh_exists and not will_delete_ssh
                            remaining_xui = real_xui_exists and not will_delete_xui
                            
                            is_full_delete = False

                            if not remaining_ssh and not remaining_xui:
                                SERVERS_CACHE.pop(idx)
                                u = target_srv.get('url'); p_u = target_srv.get('ssh_host') or u
                                for k in [u, p_u]:
                                    if k in PROBE_DATA_CACHE: del PROBE_DATA_CACHE[k]
                                    if k in NODES_DATA: del NODES_DATA[k]
                                safe_notify('✅ 服务器已彻底删除', 'positive')
                                is_full_delete = True
                            else:
                                if will_delete_ssh:
                                    for k in ['ssh_host', 'ssh_port', 'ssh_user', 'ssh_password', 'ssh_key', 'ssh_auth_type']: target_srv[k] = ''
                                    target_srv['probe_installed'] = False
                                    state['ssh_active'] = False
                                    data['ssh_host'] = ''
                                    safe_notify('✅ SSH 信息已清除', 'positive')
                                
                                if will_delete_xui:
                                    for k in ['url', 'user', 'pass', 'prefix']: target_srv[k] = ''
                                    state['xui_active'] = False
                                    data['url'] = '' 
                                    safe_notify('✅ X-UI 信息已清除', 'positive')

                            await save_servers()
                            del_d.close()
                            d.close()
                            
                            render_sidebar_content.refresh()
                            current_scope = CURRENT_VIEW_STATE.get('scope')
                            current_data = CURRENT_VIEW_STATE.get('data')

                            if is_full_delete:
                                # 如果正在查看当前单服务器详情
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    content_container.clear()
                                    with content_container:
                                        ui.label('该服务器已删除').classes('text-gray-400 text-lg w-full text-center mt-20')
                                # ✨✨✨ 关键修改：如果正在查看列表，立即静默刷新 ✨✨✨
                                elif current_scope in ['ALL', 'TAG', 'COUNTRY']:
                                    # 强制打破防抖，触发 refresh_content 重绘
                                    CURRENT_VIEW_STATE['scope'] = None
                                    await refresh_content(current_scope, current_data, force_refresh=False)
                            else:
                                if current_scope == 'SINGLE' and current_data == target_srv:
                                    await refresh_content('SINGLE', target_srv)

                        with ui.row().classes('w-full justify-end mt-4 gap-2'):
                            ui.button('取消', on_click=del_d.close).props('flat dense color=grey')
                            ui.button('确认执行', color='red', on_click=confirm_execution).props('unelevated').classes(btn_keycap_red_confirm)
                    del_d.open()

                ui.button('删除 / 卸载配置', icon='delete', on_click=open_delete_confirm).props('flat').classes(btn_keycap_delete)
    d.open()
    
# =================  数据备份/恢复 (已修复记忆功能)  =================
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
                        
                        # --- 默认设置区域 (记忆功能已修复) ---
                        with ui.grid().classes('w-full gap-2 grid-cols-2'):
                            # 从配置读取上次的值，如果没有则使用默认
                            last_ssh_user = ADMIN_CONFIG.get('pref_ssh_user', 'root')
                            last_ssh_port = ADMIN_CONFIG.get('pref_ssh_port', '22')
                            
                            def_ssh_user = ui.input('默认 SSH 用户', value=last_ssh_user).props('dense outlined')
                            def_ssh_port = ui.input('默认 SSH 端口', value=last_ssh_port).props('dense outlined')
                            
                            # SSH 认证方式 & 密码 & 私钥
                            def_auth_type = ui.select(['全局密钥', '独立密码', '独立密钥'], value='全局密钥', label='默认 SSH 认证').classes('col-span-2').props('dense outlined options-dense')
                            
                            def_ssh_pwd = ui.input('默认 SSH 密码').props('dense outlined').classes('col-span-2')
                            def_ssh_pwd.bind_visibility_from(def_auth_type, 'value', value='独立密码')
                            
                            def_ssh_key = ui.textarea('默认 SSH 私钥').props('dense outlined rows=2 input-class=text-xs font-mono').classes('col-span-2')
                            def_ssh_key.bind_visibility_from(def_auth_type, 'value', value='独立密钥')

                            # X-UI 默认值记忆
                            last_xui_port = ADMIN_CONFIG.get('pref_xui_port', '54321')
                            last_xui_user = ADMIN_CONFIG.get('pref_xui_user', 'admin')
                            last_xui_pass = ADMIN_CONFIG.get('pref_xui_pass', 'admin')

                            def_xui_port = ui.input('默认 X-UI 端口', value=last_xui_port).props('dense outlined')
                            def_xui_user = ui.input('默认 X-UI 账号', value=last_xui_user).props('dense outlined')
                            def_xui_pass = ui.input('默认 X-UI 密码', value=last_xui_pass).props('dense outlined')
                        
                        ui.separator()

                        # 双独立开关
                        with ui.row().classes('w-full justify-between items-center bg-gray-50 p-2 rounded border border-gray-200'):
                            chk_xui = ui.checkbox('添加 X-UI 面板', value=True).classes('font-bold text-blue-700')
                            chk_probe = ui.checkbox('启用 Root 探针 (自动安装)', value=False).classes('font-bold text-slate-700')

                        # ✨✨✨ 主处理函数 (合并了保存逻辑) ✨✨✨
                        async def run_batch_import():
                            # 1. 先保存用户的偏好设置到 ADMIN_CONFIG
                            ADMIN_CONFIG['pref_ssh_user'] = def_ssh_user.value
                            ADMIN_CONFIG['pref_ssh_port'] = def_ssh_port.value
                            ADMIN_CONFIG['pref_xui_port'] = def_xui_port.value
                            ADMIN_CONFIG['pref_xui_user'] = def_xui_user.value
                            ADMIN_CONFIG['pref_xui_pass'] = def_xui_pass.value
                            await save_admin_config() # 立即写入磁盘
                            
                            # 2. 开始处理添加逻辑
                            raw_text = url_area.value.strip()
                            if not raw_text: safe_notify("请输入内容", "warning"); return
                            
                            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                            count = 0
                            existing_urls = {s['url'] for s in SERVERS_CACHE}
                            post_tasks = []
                            
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
                                
                                # 根据开关决定是否填入账号密码
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
                                    'ssh_auth_type': def_auth_type.value,
                                    'ssh_password': def_ssh_pwd.value, 
                                    'ssh_key': def_ssh_key.value,
                                    'probe_installed': should_add_probe
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
    


# ================= 全局布局定义区域 (全响应式版) =================

# 1. 带延迟 (9列) - 用于: 区域分组(如显示Ping时)
# 布局: 服务器(2fr) 备注(2fr) 分组/IP(1.5fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 延迟(0.8fr) 状态(0.5fr) 操作(1.5fr)
COLS_WITH_PING = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'

# 2. 无延迟 (8列) - 用于: 所有服务器列表(默认), 自定义分组
# 布局: 服务器(2fr) 备注(2fr) 分组(1.5fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 状态(0.5fr) 操作(1.5fr)
COLS_NO_PING   = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'

# 3. 单机视图带延迟 (8列) - 用于: 单台服务器详情页 (如果显示延迟的话)
# 布局: 节点名称(3fr) 类型(1fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 延迟(0.8fr) 状态(0.5fr) 操作(1.5fr)
# 注：这里给“节点名称”分配 3fr，因为它只有一列长文字，可以宽一点
SINGLE_COLS = 'grid-template-columns: 3fr 1fr 1fr 0.8fr 0.8fr 0.8fr 0.5fr 1.5fr; align-items: center;'

# 4. 所有服务器简略版 (7列) - 某些特殊视图使用
# 布局: 服务器(2fr) 备注(2fr) 在线状态(1.5fr) 流量(1fr) 协议(0.8fr) 端口(0.8fr) 操作(1.5fr)
COLS_ALL_SERVERS = 'grid-template-columns: 2fr 2fr 1.5fr 1fr 0.8fr 0.8fr 1.5fr; align-items: center;'

# 5. 区域分组专用布局  ✨✨✨
# 格式: 服务器(150) 备注(200) 在线状态(1fr) 流量(100) 协议(80) 端口(80) 操作(150)
COLS_SPECIAL_WITH_PING = 'grid-template-columns: 2.5fr 1.5fr 1.5fr 1fr 0.8fr 0.8fr 1.5fr; align-items: center;'

# 6. 单服务器专用布局 (移除延迟列 90px，格式与 All Servers 一致) ✨✨✨
# 格式: 备注(200) 所在组(1fr) 流量(100) 协议(80) 端口(80) 状态(100) 操作(150)
SINGLE_COLS_NO_PING = 'grid-template-columns: 3fr 1fr 1.5fr 1fr 1fr 1fr 1.5fr; align-items: center;'


# ================= 刷新逻辑 (修改版：同步完成后刷新侧边栏) =================
async def refresh_content(scope='ALL', data=None, force_refresh=False):
    try: client = ui.context.client
    except: return 

    global CURRENT_VIEW_STATE
    
    # 防抖判断 -> 修改为：如果是重复点击，自动转为“强制刷新”模式
    if not force_refresh and CURRENT_VIEW_STATE.get('scope') == scope and CURRENT_VIEW_STATE.get('data') == data:
        force_refresh = True # <--- 关键修改：不再 return，而是标记为强制刷新

    import time
    current_token = time.time()
    
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

    # 2. UI 绘制逻辑
    async def _render_ui():
        if CURRENT_VIEW_STATE.get('render_token') != current_token: return
        with client:
            if not content_container: return
            content_container.clear()
            content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
            
            with content_container:
                if scope == 'SINGLE': 
                    if targets: await render_single_server_view(targets[0])
                    else: ui.label('服务器未找到').classes('text-gray-400')
                    return 
                
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

                with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.label(title).classes('text-2xl font-bold')

                    with ui.row().classes('items-center gap-2'):
                        if is_group_view and targets:
                            with ui.row().classes('gap-1'):
                                ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey').tooltip('复制原始链接')
                                ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange').tooltip('复制 Surge 订阅')
                                ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green').tooltip('复制 Clash 订阅')
                        
                        if targets:
                             # 点击这里的按钮是“主动操作”，所以 force_refresh=True
                             ui.button('同步最新数据', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True)).props('outline color=primary')

                if not targets:
                    with ui.column().classes('w-full h-64 justify-center items-center text-gray-400'):
                        ui.icon('inbox', size='4rem'); ui.label('列表为空').classes('text-lg')
                else: 
                    try: targets.sort(key=smart_sort_key)
                    except: pass
                    await render_aggregated_view(targets, show_ping=show_ping, token=current_token)

    if not force_refresh:
        await _render_ui()

    # 4. 后台数据同步逻辑
    panel_only_servers = [s for s in targets if not s.get('probe_installed', False)]
    if force_refresh: panel_only_servers = targets

    if panel_only_servers:
        async def _background_fetch():
            if not panel_only_servers: return
            if scope != 'SINGLE': safe_notify(f"正在后台更新 {len(panel_only_servers)} 台面板数据...", "ongoing", timeout=2000)
            
            # 执行同步 (含改名逻辑)
            tasks = [fetch_inbounds_safe(s, force_refresh=True, sync_name=force_refresh) for s in panel_only_servers]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # ✨✨✨ [核心修改点]：数据更新完了，强制刷新左侧侧边栏！ ✨✨✨
            try:
                render_sidebar_content.refresh()
            except: 
                pass 
            
            # 👇👇👇 [新增] 如果是强制刷新，且当前不是单机详情页，则静默重绘主列表 👇👇👇
            if force_refresh and scope != 'SINGLE':
                await _render_ui()
            # 👆👆👆 [新增结束]
            
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

# 用于外部调用的刷新句柄 (例如给右上角"新建节点"按钮使用)
REFRESH_CURRENT_NODES = lambda: None


# =================  单服务器视图 (已修复：补回明文复制按钮)  =================
async def render_single_server_view(server_conf, force_refresh=False):
    global REFRESH_CURRENT_NODES
    
    # 1. 布局初始化
    if content_container:
        content_container.clear()
        content_container.classes(remove='overflow-y-auto block', add='h-full overflow-hidden flex flex-col p-4')
    
    with content_container:
        has_xui_config = (server_conf.get('url') and server_conf.get('user') and server_conf.get('pass'))
        mgr = None
        if has_xui_config:
            try: mgr = get_manager(server_conf)
            except: pass

        @ui.refreshable
        async def render_node_list(): pass

        async def reload_and_refresh_ui():
            if has_xui_config:
                try: await fetch_inbounds_safe(server_conf, force_refresh=True)
                except: pass
            render_node_list.refresh()

        REFRESH_CURRENT_NODES = reload_and_refresh_ui

        # --- 辅助功能 ---
        def open_edit_custom_node(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-4'):
                ui.label('编辑节点备注').classes('text-lg font-bold mb-4')
                name_input = ui.input('节点名称', value=node_data.get('remark', '')).classes('w-full')
                async def save():
                    node_data['remark'] = name_input.value.strip()
                    await save_servers()
                    safe_notify('修改已保存', 'positive')
                    d.close()
                    render_node_list.refresh()
                with ui.row().classes('w-full justify-end mt-4'):
                    ui.button('取消', on_click=d.close).props('flat')
                    ui.button('保存', on_click=save).classes('bg-blue-600 text-white')
            d.open()

        async def uninstall_and_delete(node_data):
            with ui.dialog() as d, ui.card().classes('w-96 p-6'):
                with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                    ui.icon('warning', size='md'); ui.label('确认卸载节点？').classes('font-bold text-lg')
                ui.label(f"将停止服务并删除节点 [{node_data.get('remark')}]").classes('text-sm text-gray-600')
                async def start_uninstall():
                    d.close()
                    notification = ui.notification(message='正在执行卸载...', timeout=0, spinner=True)
                    success, output = await run.io_bound(lambda: _ssh_exec_wrapper(server_conf, XHTTP_UNINSTALL_SCRIPT))
                    notification.dismiss()
                    if success: safe_notify('✅ 服务已停止，配置已清理', 'positive')
                    else: safe_notify(f'⚠️ 卸载异常: {output}', 'warning')
                    if 'custom_nodes' in server_conf and node_data in server_conf['custom_nodes']:
                        server_conf['custom_nodes'].remove(node_data)
                        await save_servers()
                    await reload_and_refresh_ui()
                with ui.row().classes('w-full justify-end mt-6 gap-2'):
                    ui.button('取消', on_click=d.close).props('flat')
                    ui.button('确认卸载', on_click=start_uninstall).classes('bg-red-600 text-white')
            d.open()

        # ================= 布局构建 =================

        # --- 顶部 ---
        btn_3d_base = 'text-xs font-bold text-white rounded-lg px-4 py-2 border-b-4 active:border-b-0 active:translate-y-[4px] transition-all duration-150 shadow-sm'
        btn_blue = f'bg-blue-600 border-blue-800 hover:bg-blue-500 {btn_3d_base}'
        btn_green = f'bg-green-600 border-green-800 hover:bg-green-500 {btn_3d_base}'

        with ui.row().classes('w-full justify-between items-center bg-white p-4 rounded-xl border border-gray-200 border-b-[4px] border-b-gray-300 shadow-sm flex-shrink-0'):
            with ui.row().classes('items-center gap-4'):
                sys_icon = 'computer' if 'Oracle' in server_conf.get('name', '') else 'dns'
                with ui.element('div').classes('p-3 bg-slate-100 rounded-lg border border-slate-200'):
                    ui.icon(sys_icon, size='md').classes('text-slate-700')
                with ui.column().classes('gap-1'):
                    ui.label(server_conf.get('name', '未命名服务器')).classes('text-xl font-black text-slate-800 leading-tight tracking-tight')
                    with ui.row().classes('items-center gap-2'):
                        ip_addr = server_conf.get('ssh_host') or server_conf.get('url', '').replace('http://', '').split(':')[0]
                        ui.label(ip_addr).classes('text-xs font-mono font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded')
                        if server_conf.get('_status') == 'online': ui.badge('Online', color='green').props('rounded outline size=xs')
                        else: ui.badge('Offline', color='grey').props('rounded outline size=xs')
            with ui.row().classes('gap-3'):
                ui.button('一键部署 XHTTP', icon='rocket_launch', on_click=lambda: open_deploy_xhttp_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                ui.button('一键部署 Hy2', icon='bolt', on_click=lambda: open_deploy_hysteria_dialog(server_conf, reload_and_refresh_ui)).props('unelevated').classes(btn_blue)
                if has_xui_config:
                    async def on_add_success(): ui.notify('添加节点成功'); await reload_and_refresh_ui()
                    ui.button('新建 XUI 节点', icon='add', on_click=lambda: open_inbound_dialog(mgr, None, on_add_success)).props('unelevated').classes(btn_green)

        ui.element('div').classes('h-4 flex-shrink-0')

        # --- 中间 ---
        with ui.card().classes('w-full flex-grow flex flex-col p-0 rounded-xl border border-gray-200 border-b-[4px] border-b-gray-300 shadow-sm overflow-hidden'):
            with ui.row().classes('w-full items-center justify-between p-3 bg-gray-50 border-b border-gray-200'):
                 ui.label('节点列表').classes('text-sm font-black text-gray-600 uppercase tracking-wide ml-1')
                 if has_xui_config: ui.badge('X-UI 面板已连接', color='green').props('outline rounded size=xs')

            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-400 border-b border-gray-200 pb-2 pt-2 px-2 text-xs uppercase tracking-wider bg-white').style(SINGLE_COLS_NO_PING):
                ui.label('节点名称').classes('text-left pl-2')
                for h in ['类型', '流量', '协议', '端口', '状态', '操作']: ui.label(h).classes('text-center')

            with ui.scroll_area().classes('w-full flex-grow bg-gray-50 p-1'): 
                @ui.refreshable
                async def render_node_list():
                    xui_nodes = await fetch_inbounds_safe(server_conf, force_refresh=False) if has_xui_config else []
                    custom_nodes = server_conf.get('custom_nodes', [])
                    all_nodes = xui_nodes + custom_nodes
                    if not all_nodes:
                        with ui.column().classes('w-full py-12 items-center justify-center opacity-50'):
                            ui.icon('inbox', size='4rem').classes('text-gray-300 mb-2'); ui.label('暂无节点数据').classes('text-gray-400 text-sm')
                    else:
                        for n in all_nodes:
                            is_custom = n.get('_is_custom', False)
                            row_3d_cls = 'grid w-full gap-4 py-3 px-2 mb-2 items-center group bg-white rounded-xl border border-gray-200 border-b-[3px] shadow-sm transition-all duration-150 ease-out hover:shadow-md hover:border-blue-300 hover:-translate-y-[2px] active:border-b active:translate-y-[2px] active:shadow-none cursor-default'
                            with ui.element('div').classes(row_3d_cls).style(SINGLE_COLS_NO_PING):
                                ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left text-slate-700 text-sm')
                                source_tag = "独立" if is_custom else "面板"; source_cls = "bg-purple-100 text-purple-700" if is_custom else "bg-gray-100 text-gray-600"
                                ui.label(source_tag).classes(f'text-[10px] {source_cls} font-bold px-2 py-0.5 rounded-full w-fit mx-auto shadow-sm')
                                traffic = format_bytes(n.get('up', 0) + n.get('down', 0)) if not is_custom else "--"
                                ui.label(traffic).classes('text-xs text-gray-500 w-full text-center font-mono font-bold')
                                proto = n.get('protocol', 'unk').upper()
                                ui.label(proto).classes('text-[10px] font-black bg-slate-100 text-slate-500 px-1 rounded w-fit mx-auto')
                                ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center font-bold text-xs')
                                is_enable = n.get('enable', True)
                                with ui.row().classes('w-full justify-center items-center gap-1'):
                                    color = "green" if (is_custom or is_enable) else "red"; text = "已安装" if is_custom else ("运行中" if is_enable else "已停止")
                                    ui.element('div').classes(f'w-2 h-2 rounded-full bg-{color}-500 shadow-[0_0_5px_rgba(0,0,0,0.2)]'); ui.label(text).classes(f'text-[10px] font-bold text-{color}-600')
                                
                                # --- 按钮操作区 ---
                                with ui.row().classes('gap-2 justify-center w-full no-wrap opacity-60 group-hover:opacity-100 transition'):
                                    link = n.get('_raw_link', '') if is_custom else generate_node_link(n, server_conf['url'])
                                    btn_props = 'flat dense size=sm round'
                                    
                                    # 1. 复制链接
                                    if link: ui.button(icon='content_copy', on_click=lambda u=link: safe_copy_to_clipboard(u)).props(btn_props).tooltip('复制链接').classes('text-gray-600 hover:bg-blue-50 hover:text-blue-600')
                                    
                                    # 2. ✨✨✨ 补回：复制明文配置 (Surge/Loon) ✨✨✨
                                    async def copy_detail_action(node_item=n):
                                        host = server_conf.get('url', '').replace('http://', '').replace('https://', '').split(':')[0]
                                        # 调用全局辅助函数生成
                                        text = generate_detail_config(node_item, host)
                                        if text: await safe_copy_to_clipboard(text)
                                        else: ui.notify('该协议不支持生成明文配置', type='warning')

                                    ui.button(icon='description', on_click=copy_detail_action).props(btn_props).tooltip('复制明文配置').classes('text-gray-600 hover:bg-orange-50 hover:text-orange-600')

                                    # 3. 编辑/删除按钮
                                    if is_custom:
                                        ui.button(icon='edit', on_click=lambda node=n: open_edit_custom_node(node)).props(btn_props).tooltip('编辑备注').classes('text-blue-600 hover:bg-blue-50')
                                        ui.button(icon='delete', on_click=lambda node=n: uninstall_and_delete(node)).props(btn_props).tooltip('卸载并删除').classes('text-red-500 hover:bg-red-50')
                                    else:
                                        async def on_edit_success(): ui.notify('修改成功'); await reload_and_refresh_ui()
                                        ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, on_edit_success)).props(btn_props).classes('text-blue-600 hover:bg-blue-50')
                                        async def on_del_success(): ui.notify('删除成功'); await reload_and_refresh_ui()
                                        ui.button(icon='delete', on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'], i.get('remark',''), on_del_success)).props(btn_props).classes('text-red-500 hover:bg-red-50')
                await render_node_list()
                if has_xui_config: asyncio.create_task(reload_and_refresh_ui())

        ui.element('div').classes('h-6 flex-shrink-0') 

        # --- 第三段：SSH 窗口 ---
        with ui.card().classes('w-full h-[750px] flex-shrink-0 p-0 rounded-xl border border-gray-300 border-b-[4px] border-b-gray-400 shadow-lg overflow-hidden bg-slate-900 flex flex-col'):
            ssh_state = {'active': False, 'instance': None}

            def render_ssh_area():
                with ui.row().classes('w-full h-10 bg-slate-800 items-center justify-between px-4 flex-shrink-0 border-b border-slate-700'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal').classes('text-white text-sm')
                        ui.label(f"SSH Console: {server_conf.get('ssh_user','root')}@{server_conf.get('ssh_host') or 'IP'}").classes('text-gray-300 text-xs font-mono font-bold')
                    if ssh_state['active']: ui.button(icon='link_off', on_click=stop_ssh).props('flat dense round color=red size=sm').tooltip('断开连接')
                    else: ui.label('Disconnected').classes('text-[10px] text-gray-500')

                box_cls = 'w-full flex-grow bg-[#0f0f0f] overflow-hidden'
                if not ssh_state['active']: box_cls += ' flex justify-center items-center'
                else: box_cls += ' relative block'

                terminal_box = ui.element('div').classes(box_cls)
                with terminal_box:
                    if not ssh_state['active']:
                        with ui.column().classes('items-center gap-4'):
                            ui.icon('dns', size='4rem').classes('text-gray-800')
                            ui.label('安全终端已就绪').classes('text-gray-600 text-sm font-bold')
                            ui.button('立即连接 SSH', icon='login', on_click=start_ssh).classes('bg-blue-600 text-white font-bold px-6 py-2 rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px] transition-all')
                    else:
                        ssh = WebSSH(terminal_box, server_conf)
                        ssh_state['instance'] = ssh
                        ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

                # --- 快捷命令区 ---
                with ui.row().classes('w-full min-h-[60px] bg-slate-800 border-t border-slate-700 px-4 py-4 gap-3 items-center flex-wrap'):
                    ui.label('快捷命令:').classes('text-xs font-bold text-gray-400 mr-2')
                    
                    commands = ADMIN_CONFIG.get('quick_commands', [])
                    for cmd_obj in commands:
                        cmd_name = cmd_obj.get('name', '未命名')
                        cmd_text = cmd_obj.get('cmd', '')
                        
                        # 容器背景：bg-slate-700 (深灰)
                        with ui.element('div').classes('flex items-center bg-slate-700 rounded overflow-hidden border-b-2 border-slate-900 transition-all active:border-b-0 active:translate-y-[2px] hover:bg-slate-600'):
                            # 左侧按钮：unelevated (去阴影/颜色), bg-transparent (透出容器色), text-slate-300
                            ui.button(cmd_name, on_click=lambda c=cmd_text: exec_quick_cmd(c)) \
                                .props('unelevated') \
                                .classes('bg-transparent text-[11px] font-bold text-slate-300 px-3 py-1.5 hover:text-white rounded-none')
                            
                            # 分割线
                            ui.element('div').classes('w-[1px] h-4 bg-slate-500 opacity-50')
                            
                            # 右侧按钮：齿轮
                            ui.button(icon='settings', on_click=lambda c=cmd_obj: open_cmd_editor(c)) \
                                .props('flat dense size=xs') \
                                .classes('text-slate-400 hover:text-white px-1 py-1.5 rounded-none')

                    ui.button(icon='add', on_click=lambda: open_cmd_editor(None)).props('flat dense round size=sm color=green').tooltip('添加常用命令')

            async def start_ssh():
                ssh_state['active'] = True
                render_card_content()

            async def stop_ssh():
                if ssh_state['instance']:
                    ssh_state['instance'].close()
                    ssh_state['instance'] = None
                ssh_state['active'] = False
                render_card_content()

            def exec_quick_cmd(cmd_text):
                if ssh_state['instance'] and ssh_state['instance'].active:
                    ssh_state['instance'].channel.send(cmd_text + "\n")
                    ui.notify(f"已发送: {cmd_text[:20]}...", type='positive', position='bottom')
                else:
                    ui.notify("请先连接 SSH", type='warning', position='bottom')

            def open_cmd_editor(existing_cmd=None):
                with ui.dialog() as d, ui.card().classes('w-96 p-5 bg-[#1e293b] border border-slate-600 shadow-2xl'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        ui.label('管理快捷命令').classes('text-lg font-bold text-white')
                        ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

                    name_input = ui.input('按钮名称', value=existing_cmd['name'] if existing_cmd else '') \
                        .classes('w-full mb-3').props('outlined dense dark bg-color="slate-800"')
                    cmd_input = ui.textarea('执行命令', value=existing_cmd['cmd'] if existing_cmd else '') \
                        .classes('w-full mb-4').props('outlined dense dark bg-color="slate-800" rows=4')
                    
                    async def save():
                        name = name_input.value.strip(); cmd = cmd_input.value.strip()
                        if not name or not cmd: return ui.notify("内容不能为空", type='negative')
                        if 'quick_commands' not in ADMIN_CONFIG: ADMIN_CONFIG['quick_commands'] = []
                        if existing_cmd: existing_cmd['name'] = name; existing_cmd['cmd'] = cmd
                        else: ADMIN_CONFIG['quick_commands'].append({'name': name, 'cmd': cmd, 'id': str(uuid.uuid4())[:8]})
                        await save_admin_config()
                        d.close()
                        render_card_content()
                        ui.notify("命令已保存", type='positive')

                    async def delete_current():
                        if existing_cmd and 'quick_commands' in ADMIN_CONFIG:
                            ADMIN_CONFIG['quick_commands'].remove(existing_cmd)
                            await save_admin_config()
                            d.close()
                            render_card_content()
                            ui.notify("命令已删除", type='positive')

                    with ui.row().classes('w-full justify-between mt-2'):
                        if existing_cmd: ui.button('删除', icon='delete', color='red', on_click=delete_current).props('flat dense')
                        else: ui.element('div')
                        ui.button('保存', icon='save', on_click=save).classes('bg-blue-600 text-white font-bold rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px]')

                d.open()

            def render_card_content():
                ssh_wrapper.clear()
                with ssh_wrapper:
                    render_ssh_area()

            ssh_wrapper = ui.column().classes('w-full h-full p-0 gap-0')
            render_card_content()

# ================= SSH 窗口 (修复 SyntaxError) =================
def render_ssh_window_full(server_conf):
    with ui.card().classes('w-full h-[750px] flex-shrink-0 p-0 rounded-xl border border-gray-300 border-b-[4px] border-b-gray-400 shadow-lg overflow-hidden bg-slate-900 flex flex-col'):
        ssh_state = {'active': False, 'instance': None}

        def render_ssh_area():
            with ui.row().classes('w-full h-10 bg-slate-800 items-center justify-between px-4 flex-shrink-0 border-b border-slate-700'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('terminal').classes('text-white text-sm')
                    ui.label(f"SSH Console: {server_conf.get('ssh_user','root')}@{server_conf.get('ssh_host') or 'IP'}").classes('text-gray-300 text-xs font-mono font-bold')
                if ssh_state['active']: 
                    ui.button(icon='link_off', on_click=stop_ssh).props('flat dense round color=red size=sm').tooltip('断开连接')
                else: 
                    ui.label('Disconnected').classes('text-[10px] text-gray-500')

            box_cls = 'w-full flex-grow bg-[#0f0f0f] overflow-hidden'
            if not ssh_state['active']: box_cls += ' flex justify-center items-center'
            else: box_cls += ' relative block'

            terminal_box = ui.element('div').classes(box_cls)
            with terminal_box:
                if not ssh_state['active']:
                    with ui.column().classes('items-center gap-4'):
                        ui.icon('dns', size='4rem').classes('text-gray-800')
                        ui.label('安全终端已就绪').classes('text-gray-600 text-sm font-bold')
                        ui.button('立即连接 SSH', icon='login', on_click=start_ssh).classes('bg-blue-600 text-white font-bold px-6 py-2 rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px] transition-all')
                else:
                    ssh = WebSSH(terminal_box, server_conf)
                    ssh_state['instance'] = ssh
                    ui.timer(0.1, lambda: asyncio.create_task(ssh.connect()), once=True)

            # 快捷命令栏
            with ui.row().classes('w-full min-h-[60px] bg-slate-800 border-t border-slate-700 px-4 py-4 gap-3 items-center flex-wrap'):
                ui.label('快捷命令:').classes('text-xs font-bold text-gray-400 mr-2')
                commands = ADMIN_CONFIG.get('quick_commands', [])
                for cmd_obj in commands:
                    cmd_name = cmd_obj.get('name', '未命名'); cmd_text = cmd_obj.get('cmd', '')
                    with ui.element('div').classes('flex items-center bg-slate-700 rounded overflow-hidden border-b-2 border-slate-900 transition-all active:border-b-0 active:translate-y-[2px] hover:bg-slate-600'):
                        ui.button(cmd_name, on_click=lambda c=cmd_text: exec_quick_cmd(c)).props('unelevated').classes('bg-transparent text-[11px] font-bold text-slate-300 px-3 py-1.5 hover:text-white rounded-none')
                        ui.element('div').classes('w-[1px] h-4 bg-slate-500 opacity-50')
                        ui.button(icon='settings', on_click=lambda c=cmd_obj: open_cmd_editor(c)).props('flat dense size=xs').classes('text-slate-400 hover:text-white px-1 py-1.5 rounded-none')
                ui.button(icon='add', on_click=lambda: open_cmd_editor(None)).props('flat dense round size=sm color=green').tooltip('添加常用命令')

        async def start_ssh():
            ssh_state['active'] = True
            ssh_wrapper.clear()
            with ssh_wrapper:
                render_ssh_area()

        async def stop_ssh():
            if ssh_state['instance']: 
                ssh_state['instance'].close()
                ssh_state['instance'] = None
            ssh_state['active'] = False
            ssh_wrapper.clear()
            with ssh_wrapper:
                render_ssh_area()

        def exec_quick_cmd(cmd_text):
            if ssh_state['instance'] and ssh_state['instance'].active:
                ssh_state['instance'].channel.send(cmd_text + "\n")
                ui.notify(f"已发送: {cmd_text[:20]}...", type='positive', position='bottom')
            else: ui.notify("请先连接 SSH", type='warning', position='bottom')

        def open_cmd_editor(existing_cmd=None):
            with ui.dialog() as d, ui.card().classes('w-96 p-5 bg-[#1e293b] border border-slate-600 shadow-2xl'):
                with ui.row().classes('w-full justify-between items-center mb-4'):
                    ui.label('管理快捷命令').classes('text-lg font-bold text-white')
                    ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
                name_input = ui.input('按钮名称', value=existing_cmd['name'] if existing_cmd else '').classes('w-full mb-3').props('outlined dense dark bg-color="slate-800"')
                cmd_input = ui.textarea('执行命令', value=existing_cmd['cmd'] if existing_cmd else '').classes('w-full mb-4').props('outlined dense dark bg-color="slate-800" rows=4')
                
                async def save():
                    name = name_input.value.strip(); cmd = cmd_input.value.strip()
                    if not name or not cmd: return ui.notify("内容不能为空", type='negative')
                    if 'quick_commands' not in ADMIN_CONFIG: ADMIN_CONFIG['quick_commands'] = []
                    if existing_cmd: existing_cmd['name'] = name; existing_cmd['cmd'] = cmd
                    else: ADMIN_CONFIG['quick_commands'].append({'name': name, 'cmd': cmd, 'id': str(uuid.uuid4())[:8]})
                    await save_admin_config()
                    d.close()
                    ssh_wrapper.clear()
                    with ssh_wrapper:
                        render_ssh_area()
                    ui.notify("命令已保存", type='positive')

                async def delete_current():
                    if existing_cmd and 'quick_commands' in ADMIN_CONFIG:
                        ADMIN_CONFIG['quick_commands'].remove(existing_cmd)
                        await save_admin_config()
                        d.close()
                        ssh_wrapper.clear()
                        with ssh_wrapper:
                            render_ssh_area()
                        ui.notify("命令已删除", type='positive')

                with ui.row().classes('w-full justify-between mt-2'):
                    if existing_cmd: ui.button('删除', icon='delete', color='red', on_click=delete_current).props('flat dense')
                    else: ui.element('div')
                    ui.button('保存', icon='save', on_click=save).classes('bg-blue-600 text-white font-bold rounded-lg border-b-4 border-blue-800 active:border-b-0 active:translate-y-[2px]')
            d.open()

        ssh_wrapper = ui.column().classes('w-full h-full p-0 gap-0')
        with ssh_wrapper:
            render_ssh_area()
            
# ================= 聚合视图 (局部静默刷新 + 自动状态更新) =================
# 全局字典，用于存储每行 UI 元素的引用，以便局部更新
# 结构: { 'server_url': { 'row_el': row_element, 'status_icon': icon, 'status_label': label, ... } }
UI_ROW_REFS = {} 
CURRENT_VIEW_STATE = {'scope': 'DASHBOARD', 'data': None}
# ================= 点击自定义节点显示详情 =================
def show_custom_node_info(node):
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm'):
        ui.label(node.get('remark', '节点详情')).classes('text-lg font-bold mb-2')
        
        # 获取链接
        link = node.get('_raw_link') or node.get('link') or "无法获取链接"
        
        # 显示链接区域
        with ui.row().classes('w-full bg-gray-100 p-3 rounded break-all font-mono text-xs mb-4'):
            ui.label(link)
            
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('复制', icon='content_copy', on_click=lambda: [safe_copy_to_clipboard(link), d.close()])
            ui.button('关闭', on_click=d.close).props('flat')
    d.open()

# ================= 聚合视图渲染 (修复版：移除定时器，防止页面卡死) =================
async def render_aggregated_view(server_list, show_ping=False, force_refresh=False, token=None):
    # 1. 触发后台数据更新 (限制并发，分批处理)
    if force_refresh:
        chunk_size = 50
        for i in range(0, len(server_list), chunk_size):
            chunk = server_list[i:i + chunk_size]
            asyncio.create_task(asyncio.gather(*[fetch_inbounds_safe(s, force_refresh=True) for s in chunk], return_exceptions=True))
            
    list_container = ui.column().classes('w-full gap-3 p-1')
    
    # 2. 定义列宽
    cols_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 1.5fr' 
    cols_no_ping = 'grid-template-columns: 2fr 2fr 1.5fr 1.5fr 1fr 1fr 0.5fr 1.5fr'
    
    try:
        is_all_servers = (len(server_list) == len(SERVERS_CACHE) and not show_ping)
        use_special_mode = is_all_servers or show_ping
        current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING
    except:
        current_css = cols_ping if show_ping else cols_no_ping

    # ================= ✨✨✨ 分页逻辑核心 ✨✨✨ =================
    PAGE_SIZE = 30  # 每页显示 30 台
    total_items = len(server_list)
    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
    
    # 记录当前页码
    if not hasattr(render_aggregated_view, 'current_page'):
        render_aggregated_view.current_page = 1
    
    if render_aggregated_view.current_page > total_pages: render_aggregated_view.current_page = 1
    if render_aggregated_view.current_page < 1: render_aggregated_view.current_page = 1

    # 渲染页面的函数
    def render_page(page_num):
        list_container.clear()
        with list_container:
            # === A. 顶部统计与翻页器 ===
            with ui.row().classes('w-full justify-between items-center px-2 mb-2'):
                ui.label(f'共 {total_items} 台服务器 (第 {page_num}/{total_pages} 页)').classes('text-xs text-gray-400 font-bold')
                
                if total_pages > 1:
                    pagination = ui.pagination(1, total_pages, direction_links=True).props('dense flat color=blue')
                    pagination.value = page_num
                    pagination.on('update:model-value', lambda e: switch_page(e.args))

            # === B. 绘制静态表头 ===
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-400 border-b pb-2 px-6 mb-1 uppercase tracking-wider text-xs').style(current_css):
                ui.label('服务器').classes('text-left pl-1')
                ui.label('节点名称').classes('text-left pl-1')
                if use_special_mode: ui.label('在线状态 / IP').classes('text-center')
                else: ui.label('所在组').classes('text-center')
                ui.label('已用流量').classes('text-center')
                ui.label('协议').classes('text-center')
                ui.label('端口').classes('text-center')
                if not use_special_mode: ui.label('状态').classes('text-center')
                ui.label('操作').classes('text-center')
            
            # === C. 切片获取当前页数据 ===
            start_idx = (page_num - 1) * PAGE_SIZE
            end_idx = start_idx + PAGE_SIZE
            current_page_data = server_list[start_idx:end_idx]

            # === D. 遍历渲染 (修复：移除内部定时器和 refreshable) ===
            for srv in current_page_data:
                panel_n = NODES_DATA.get(srv['url'], []) or []
                custom_n = srv.get('custom_nodes', []) or []
                # 标记自定义节点
                for cn in custom_n: cn['_is_custom'] = True
                all_nodes = panel_n + custom_n
                
                if not all_nodes:
                    draw_row(srv, None, current_css, use_special_mode, is_first=True)
                    continue

                for index, node in enumerate(all_nodes):
                    # 直接绘制行，不再包裹定时器
                    draw_row(srv, node, current_css, use_special_mode, is_first=(index==0))
            
            # === E. 底部翻页器 ===
            if total_pages > 1:
                with ui.row().classes('w-full justify-center mt-4'):
                    pag_bottom = ui.pagination(1, total_pages, direction_links=True).props('dense flat color=blue')
                    pag_bottom.value = page_num
                    pag_bottom.on('update:model-value', lambda e: switch_page(e.args))

    def switch_page(new_page):
        render_aggregated_view.current_page = new_page
        render_page(new_page)

    # 初始渲染
    render_page(render_aggregated_view.current_page)

# --- 辅助函数：绘制单行 (保持原样，含复制修复) ---
def draw_row(srv, node, css_style, use_special_mode, is_first=True):
    card_cls = 'grid w-full gap-4 py-3 px-4 items-center group relative bg-white rounded-xl border border-gray-200 border-b-[3px] shadow-sm transition-all duration-150 ease-out hover:shadow-md hover:border-blue-300 hover:-translate-y-[1px] mb-2'
    
    with ui.element('div').classes(card_cls).style(css_style):
        # 1. 服务器名
        srv_name = srv.get('name', '未命名')
        if not is_first: ui.label(srv_name).classes('text-xs text-gray-300 truncate w-full text-left pl-2 font-mono')
        else: ui.label(srv_name).classes('text-xs text-gray-500 font-bold truncate w-full text-left pl-2 font-mono')

        # 无节点情况
        if not node:
            is_probe = srv.get('probe_installed', False)
            msg = '同步中...' if not is_probe else '无节点配置'
            ui.label(msg).classes('font-bold truncate text-gray-400 text-xs italic')
            ui.label('--').classes('text-center text-gray-300')
            ui.label('--').classes('text-center text-gray-300')
            ui.label('UNK').classes('text-center text-gray-300 font-bold text-[10px]')
            ui.label('--').classes('text-center text-gray-300')
            if not use_special_mode: ui.element('div')
            with ui.row().classes('gap-1 justify-center w-full no-wrap'):
                 ui.button(icon='settings', on_click=lambda _, s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm round color=grey')
            return

        # 2. 备注
        remark = node.get('ps') or node.get('remark') or '未命名节点'
        ui.label(remark).classes('font-bold truncate w-full text-left pl-2 text-slate-700 text-sm')

        # 3. 分组/IP
        if use_special_mode:
            with ui.row().classes('w-full justify-center items-center gap-1.5 no-wrap'):
                is_online = srv.get('_status') == 'online'
                color = 'text-green-500' if is_online else 'text-red-500'
                if not srv.get('probe_installed') and not node.get('_is_custom'): color = 'text-orange-400'
                ui.icon('bolt').classes(f'{color} text-sm')
                display_ip = get_real_ip_display(srv['url'])
                ip_lbl = ui.label(display_ip).classes('text-[10px] font-mono text-gray-500 font-bold bg-gray-100 px-1.5 py-0.5 rounded select-all')
                bind_ip_label(srv['url'], ip_lbl)
        else:
            group_display = srv.get('group', '默认分组')
            if group_display in ['默认分组', '自动注册', '未分组', '自动导入']:
                try:
                    detected = detect_country_group(srv.get('name', ''), None)
                    if detected: group_display = detected
                except: pass
            ui.label(group_display).classes('text-xs font-bold text-gray-500 w-full text-center truncate bg-gray-50 px-2 py-0.5 rounded-full')

        # 4. 流量
        if node.get('_is_custom'): ui.label('-').classes('text-xs text-gray-400 w-full text-center font-mono')
        else:
            traffic = sum([node.get('up', 0), node.get('down', 0)])
            ui.label(format_bytes(traffic)).classes('text-xs text-blue-600 w-full text-center font-mono font-bold')

        # 5. 协议
        proto = str(node.get('protocol', 'unk')).upper()
        if 'HYSTERIA' in proto: proto = 'HY2'
        if 'SHADOWSOCKS' in proto: proto = 'SS'
        proto_color = 'text-slate-500'
        if 'HY2' in proto: proto_color = 'text-purple-600'
        elif 'VLESS' in proto: proto_color = 'text-blue-600'
        elif 'VMESS' in proto: proto_color = 'text-green-600'
        elif 'TROJAN' in proto: proto_color = 'text-orange-600'
        ui.label(proto).classes(f'text-[11px] font-extrabold w-full text-center {proto_color} tracking-wide')

        # 6. 端口
        port_val = str(node.get('port', 0))
        ui.label(port_val).classes('text-slate-600 font-mono w-full text-center font-bold text-xs')

        # 7. 状态
        if not use_special_mode:
            with ui.element('div').classes('flex justify-center w-full'):
                is_enable = node.get('enable', True)
                dot_cls = "bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.6)]" if is_enable else "bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.6)]"
                ui.element('div').classes(f'w-2 h-2 rounded-full {dot_cls}')

        # 8. 操作按钮 (含修复逻辑)
        with ui.row().classes('gap-1 justify-center w-full no-wrap'):
            # 复制链接 (修复版)
            async def copy_link(n=node, s=srv):
                link = n.get('_raw_link') or n.get('link')
                if not link: link = generate_node_link(n, s['url'])
                await safe_copy_to_clipboard(link)

            ui.button(icon='content_copy', on_click=copy_link).props('flat dense size=sm round').tooltip('复制链接').classes('text-gray-500 hover:text-blue-600 hover:bg-blue-50')

            # 明文配置
            async def copy_detail():
                host = srv['url'].split('://')[-1].split(':')[0]
                text = generate_detail_config(node, host)
                if text: await safe_copy_to_clipboard(text)
                else: ui.notify('该协议不支持生成明文配置', type='warning')

            ui.button(icon='description', on_click=copy_detail).props('flat dense size=sm round').tooltip('复制明文配置').classes('text-gray-500 hover:text-orange-600 hover:bg-orange-50')

            # 设置按钮
            ui.button(icon='settings', on_click=lambda _, s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm round').tooltip('管理服务器').classes('text-gray-500 hover:text-slate-800 hover:bg-slate-100')

# ================= 核心：静默刷新 UI 数据 (已修复：统一为区域分布) =================
async def refresh_dashboard_ui():
    try:
        # 如果仪表盘还没打开（引用是空的），直接跳过
        if not DASHBOARD_REFS.get('servers'): return

        total_servers = len(SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        
        server_traffic_map = {}
        
        # ✨✨✨ [修复] 使用 country_counter 替代 protocol_count ✨✨✨
        from collections import Counter
        country_counter = Counter()
        
        # --- 1. 计算基础统计数据 ---
        for s in SERVERS_CACHE:
            res = NODES_DATA.get(s['url'], []) or []
            custom = s.get('custom_nodes', []) or []
            name = s.get('name', '未命名')
            srv_traffic = 0
            
            # 统计区域 (与主页逻辑保持一致)
            try:
                # 优先读取 group，如果是默认分组则探测
                g_name = s.get('group')
                if not g_name or g_name in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区']:
                    g_name = detect_country_group(name, s)
                if not g_name: g_name = "🏳️ 其他"
                country_counter[g_name] += 1
            except: 
                country_counter["🏳️ 其他"] += 1

            if res:
                online_servers += 1
                total_nodes += len(res)
                for n in res: 
                    t = int(n.get('up', 0)) + int(n.get('down', 0))
                    total_traffic_bytes += t; srv_traffic += t
                    # 移除协议统计
            
            if custom:
                total_nodes += len(custom)
                # 移除自定义节点协议统计

            server_traffic_map[name] = srv_traffic

        # --- 2. 更新 UI 文字和图表 ---
        if DASHBOARD_REFS.get('servers'): DASHBOARD_REFS['servers'].set_text(f"{online_servers}/{total_servers}")
        if DASHBOARD_REFS.get('nodes'): DASHBOARD_REFS['nodes'].set_text(str(total_nodes))
        if DASHBOARD_REFS.get('traffic'): DASHBOARD_REFS['traffic'].set_text(f"{total_traffic_bytes/(1024**3):.2f} GB")
        if DASHBOARD_REFS.get('subs'): DASHBOARD_REFS['subs'].set_text(str(len(SUBS_CACHE)))

        # 更新柱状图 (保持不变)
        if DASHBOARD_REFS.get('bar_chart'):
            sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15] 
            names = [x[0] for x in sorted_traffic]
            values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]
            DASHBOARD_REFS['bar_chart'].options['xAxis']['data'] = names
            DASHBOARD_REFS['bar_chart'].options['series'][0]['data'] = values
            DASHBOARD_REFS['bar_chart'].update()

        # ✨✨✨ [修复] 更新饼图为区域分布 (Top 5 + Others) ✨✨✨
        if DASHBOARD_REFS.get('pie_chart'):
            # 处理数据：排序并合并 Top 5 以外的
            sorted_regions = country_counter.most_common()
            
            pie_data = []
            if len(sorted_regions) > 5:
                top_5 = sorted_regions[:5]
                others_count = sum(item[1] for item in sorted_regions[5:])
                for k, v in top_5: pie_data.append({'name': f"{k} ({v})", 'value': v})
                if others_count > 0: pie_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})
            else:
                for k, v in sorted_regions: pie_data.append({'name': f"{k} ({v})", 'value': v})

            if not pie_data: pie_data = [{'name': '暂无数据', 'value': 0}]
            
            DASHBOARD_REFS['pie_chart'].options['series'][0]['data'] = pie_data
            DASHBOARD_REFS['pie_chart'].update()

        # --- 3. 同步刷新地图数据 (保持不变) ---
        globe_data_list = []
        seen_locations = set()
        for s in SERVERS_CACHE:
            lat, lon = None, None
            if 'lat' in s and 'lon' in s: lat, lon = s['lat'], s['lon']
            else:
                coords = get_coords_from_name(s.get('name', ''))
                if coords: lat, lon = coords[0], coords[1]
            
            if lat is not None and lon is not None:
                coord_key = (round(lat, 2), round(lon, 2))
                if coord_key not in seen_locations:
                    seen_locations.add(coord_key)
                    flag_only = "📍"
                    try:
                        full_group = detect_country_group(s.get('name', ''), s)
                        flag_only = full_group.split(' ')[0]
                    except: pass
                    globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag_only})
        
        if CURRENT_VIEW_STATE.get('scope') == 'DASHBOARD':
            import json
            json_data = json.dumps(globe_data_list, ensure_ascii=False)
            ui.run_javascript(f'if(window.updateDashboardMap) window.updateDashboardMap({json_data});')

    except Exception as e:
        logger.error(f"UI 更新失败: {e}")


# ================= 核心：前端轮询用的纯数据接口 (API) =================
@app.get('/api/dashboard/live_data')
def get_dashboard_live_data():
    data = calculate_dashboard_data()
    return data if data else {"error": "Calculation failed"}


# ================= 辅助：统一数据计算逻辑 =================
def calculate_dashboard_data():
    """
    计算并返回当前所有面板数据。
    供 API 和 页面初始化 共用。
    """
    try:
        total_servers = len(SERVERS_CACHE)
        online_servers = 0
        total_nodes = 0
        total_traffic_bytes = 0
        
        server_traffic_map = {}
        from collections import Counter
        protocol_count = Counter()
        country_counter = Counter()

        for s in SERVERS_CACHE:
            # 1. 获取面板节点 (X-UI API 数据)
            res = NODES_DATA.get(s['url'], []) or []
            # 2. 获取自定义节点 (一键部署的数据)
            custom = s.get('custom_nodes', []) or []
            
            name = s.get('name', '未命名')
            
            # 统计区域
            try:
                region_str = detect_country_group(name, s)
                if not region_str or region_str.strip() == "🏳️": region_str = "🏳️ 未知区域"
            except: region_str = "🏳️ 未知区域"
            country_counter[region_str] += 1

            # === A. 处理面板节点 (统计流量、协议、在线状态) ===
            srv_traffic = 0
            if res:
                online_servers += 1
                total_nodes += len(res) # 累加面板节点
                for n in res: 
                    t = int(n.get('up', 0)) + int(n.get('down', 0))
                    total_traffic_bytes += t
                    srv_traffic += t
                    proto = str(n.get('protocol', 'unknown')).upper()
                    protocol_count[proto] += 1
            
            # === B. 处理自定义节点 (累加数量和协议) ===
            if custom:
                # 注意：如果面板掉线(res为空)但有自定义节点，是否算在线服务器？
                # 这里暂且保持原逻辑：只根据API连通性判断 online_servers
                # 但节点数量必须加上去：
                total_nodes += len(custom)
                
                for cn in custom:
                    # 自定义节点通常没有流量反馈，只统计协议
                    c_proto = str(cn.get('protocol', 'custom')).upper()
                    protocol_count[c_proto] += 1

            # 记录该服务器总流量
            server_traffic_map[name] = srv_traffic

        # 构建图表数据
        sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15]
        bar_names = [x[0] for x in sorted_traffic]
        bar_values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]

        chart_data = []
        sorted_regions = country_counter.most_common()
        top_5 = sorted_regions[:5]
        for region, count in top_5: chart_data.append({'name': f"{region} ({count})", 'value': count})
        others_count = sum(count for _, count in sorted_regions[5:])
        if others_count > 0: chart_data.append({'name': f"🏳️ 其他 ({others_count})", 'value': others_count})
        if not chart_data: chart_data = [{'name': '暂无数据', 'value': 0}]

        return {
            "servers": f"{online_servers}/{total_servers}",
            "nodes": str(total_nodes), # 这里的 total_nodes 现在包含了 Hy2 和 XHTTP
            "traffic": f"{total_traffic_bytes/(1024**3):.2f} GB",
            "subs": str(len(SUBS_CACHE)),
            "bar_chart": {"names": bar_names, "values": bar_values},
            "pie_chart": chart_data
        }
    except Exception as e:
        # 建议把报错打印出来方便调试，生产环境可以去掉 print
        print(f"Error calculating dashboard data: {e}")
        return None
# ================= 核心：仪表盘主视图渲染 (最终稳定版：切断 JS 关联) =================
async def load_dashboard_stats():
    global CURRENT_VIEW_STATE
    CURRENT_VIEW_STATE['scope'] = 'DASHBOARD'
    CURRENT_VIEW_STATE['data'] = None
    
    await asyncio.sleep(0.1)
    content_container.clear()
    content_container.classes(remove='justify-center items-center overflow-hidden p-6', add='overflow-y-auto p-4 pl-6 justify-start')
    
    # 1. 计算初始统计数据
    # 这里就算 calculate_dashboard_data 返回的是协议数据也没关系
    # 因为我们在下一步会马上覆盖它
    init_data = calculate_dashboard_data()
    if not init_data:
        init_data = {
            "servers": "0/0", "nodes": "0", "traffic": "0 GB", "subs": "0",
            "bar_chart": {"names": [], "values": []}, "pie_chart": []
        }

    # ✨✨✨ [Python端]：强制重算区域数据 (Top 5 + 其他) ✨✨✨
    # 这是页面加载时显示的正确数据
    group_buckets = {}
    for s in SERVERS_CACHE:
        # 优先使用保存的分组，如果是特殊分组则重新检测
        g_name = s.get('group')
        if not g_name or g_name in ['默认分组', '自动注册', '未分组', '自动导入', '🏳️ 其他地区']:
            g_name = detect_country_group(s.get('name', ''))
        
        if g_name not in group_buckets: group_buckets[g_name] = 0
        group_buckets[g_name] += 1
    
    # 转为列表并排序
    all_regions = [{'name': k, 'value': v} for k, v in group_buckets.items()]
    all_regions.sort(key=lambda x: x['value'], reverse=True)
    
    # 只取前 5 名，剩下的合并为 "🏳️ 其他地区"
    if len(all_regions) > 5:
        top_5 = all_regions[:5]
        others_count = sum(item['value'] for item in all_regions[5:])
        top_5.append({'name': '🏳️ 其他地区', 'value': others_count})
        pie_data_final = top_5
    else:
        pie_data_final = all_regions

    # 覆盖 init_data，确保初始显示正确
    init_data['pie_chart'] = pie_data_final

    with content_container:
        # ✨✨✨ [关键修改]：JS 脚本中删除了更新 Pie Chart 的代码 ✨✨✨
        # 这样即使后台 API 返回了旧的协议数据，前端也不会接收，从而彻底阻断“变身”
        ui.run_javascript("""
        if (window.dashInterval) clearInterval(window.dashInterval);
        window.dashInterval = setInterval(async () => {
            if (document.hidden) return;
            try {
                const res = await fetch('/api/dashboard/live_data');
                if (!res.ok) return;
                const data = await res.json();
                if (data.error) return;

                // 1. 刷新顶部数字 (保留)
                const ids = ['stat-servers', 'stat-nodes', 'stat-traffic', 'stat-subs'];
                const keys = ['servers', 'nodes', 'traffic', 'subs'];
                ids.forEach((id, i) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = data[keys[i]];
                });

                // 2. 刷新柱状图 (流量是实时变的，必须保留)
                const barDom = document.getElementById('chart-bar');
                if (barDom) {
                    const chart = echarts.getInstanceByDom(barDom);
                    if (chart) {
                        chart.setOption({
                            xAxis: { data: data.bar_chart.names },
                            series: [{ data: data.bar_chart.values }]
                        });
                    }
                }
                
                // ✂️ [已彻底删除] 饼图更新逻辑
                // 这里原本有 update chart-pie 的代码，现在删掉了。
                // 无论后台发来什么数据，饼图永远保持 Python 刚开始画的样子。
                
            } catch (e) {}
        }, 3000);
        """)

        ui.label('系统概览').classes('text-3xl font-bold mb-4 text-slate-800 tracking-tight')
        
        # === A. 顶部统计卡片 ===
        with ui.row().classes('w-full gap-4 mb-6 items-stretch'):
            def create_stat_card(ref_key, dom_id, title, sub_text, icon, gradient, init_val):
                with ui.card().classes(f'flex-1 p-3 shadow border-none text-white {gradient} rounded-xl relative overflow-hidden'):
                    ui.element('div').classes('absolute -right-4 -top-4 w-20 h-20 bg-white opacity-10 rounded-full')
                    with ui.row().classes('items-center justify-between w-full relative z-10'):
                        with ui.column().classes('gap-0'):
                            ui.label(title).classes('opacity-90 text-[10px] font-bold uppercase tracking-wider')
                            DASHBOARD_REFS[ref_key] = ui.label(init_val).props(f'id={dom_id}').classes('text-2xl font-extrabold tracking-tight my-0.5')
                            ui.label(sub_text).classes('opacity-70 text-[10px] font-medium')
                        ui.icon(icon).classes('text-3xl opacity-80')

            create_stat_card('servers', 'stat-servers', '在线服务器', 'Online / Total', 'dns', 'bg-gradient-to-br from-blue-500 to-indigo-600', init_data['servers'])
            create_stat_card('nodes', 'stat-nodes', '节点总数', 'Active Nodes', 'hub', 'bg-gradient-to-br from-purple-500 to-pink-600', init_data['nodes'])
            create_stat_card('traffic', 'stat-traffic', '总流量消耗', 'Upload + Download', 'bolt', 'bg-gradient-to-br from-emerald-500 to-teal-600', init_data['traffic'])
            create_stat_card('subs', 'stat-subs', '订阅配置', 'Subscriptions', 'rss_feed', 'bg-gradient-to-br from-orange-400 to-red-500', init_data['subs'])

        # === B. 图表区域 ===
        with ui.row().classes('w-full gap-4 mb-6 flex-wrap xl:flex-nowrap items-stretch'):
            # 流量排行 (保持原样)
            with ui.card().classes('w-full xl:w-2/3 p-4 shadow-md border-none rounded-xl bg-white flex flex-col'):
                with ui.row().classes('w-full justify-between items-center mb-2'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-base font-bold text-slate-700')
                    with ui.row().classes('items-center gap-1 px-2 py-0.5 bg-green-50 rounded-full border border-green-200'):
                        ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse')
                        ui.label('Live').classes('text-[10px] font-bold text-green-700')
                
                DASHBOARD_REFS['bar_chart'] = ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '2%', 'right': '3%', 'bottom': '2%', 'top': '10%', 'containLabel': True},
                    'xAxis': {'type': 'category', 'data': init_data['bar_chart']['names'], 'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#64748b', 'fontSize': 10}},
                    'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#f1f5f9'}}},
                    'series': [{'type': 'bar', 'data': init_data['bar_chart']['values'], 'barWidth': '40%', 'itemStyle': {'borderRadius': [3, 3, 0, 0], 'color': '#6366f1'}}]
                }).classes('w-full h-56').props('id=chart-bar')

            # 区域分布 (饼图)
            with ui.card().classes('w-full xl:w-1/3 p-4 shadow-md border-none rounded-xl bg-white flex flex-col'):
                ui.label('🌏 服务器分布').classes('text-base font-bold text-slate-700 mb-1')
                color_palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#6366f1', '#ec4899', '#14b8a6', '#f97316']
                
                DASHBOARD_REFS['pie_chart'] = ui.echart({
                    'tooltip': {'trigger': 'item', 'formatter': '{b}: <br/><b>{c} 台</b> ({d}%)'},
                    'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 10, 'textStyle': {'color': '#64748b', 'fontSize': 11}},
                    'color': color_palette,
                    'series': [{
                        'name': '服务器分布', 
                        'type': 'pie', 
                        'radius': ['40%', '70%'],
                        'center': ['50%', '42%'],
                        'avoidLabelOverlap': False,
                        'itemStyle': {'borderRadius': 4, 'borderColor': '#fff', 'borderWidth': 1},
                        'label': { 'show': False, 'position': 'center' },
                        'emphasis': {'label': {'show': True, 'fontSize': 14, 'fontWeight': 'bold', 'color': '#334155'}, 'scale': True, 'scaleSize': 5},
                        'labelLine': { 'show': False },
                        'data': init_data['pie_chart'] # ✨ 这里是 Python 计算好的区域数据
                    }]
                }).classes('w-full h-56').props('id=chart-pie') # ⚠️ 注意：ID 还在，但 JS 不会再操作它了

        # === C. 底部地图区域 (保持原样) ===
        with ui.row().classes('w-full gap-6 mb-6'):
            with ui.card().classes('w-full p-0 shadow-md border-none rounded-xl bg-slate-900 overflow-hidden relative'):
                with ui.row().classes('w-full px-6 py-3 bg-slate-800/50 border-b border-gray-700 justify-between items-center z-10 relative'):
                    with ui.row().classes('gap-2 items-center'):
                        ui.icon('public', color='blue-4').classes('text-xl')
                        ui.label('全球节点实景 (Global View)').classes('text-base font-bold text-white')
                    DASHBOARD_REFS['map_info'] = ui.label('Live Rendering').classes('text-[10px] text-gray-400')

                # 1. 准备旧版简单数据
                globe_data_list = []
                seen_locations = set()
                total_server_count = len(SERVERS_CACHE)
                
                for s in SERVERS_CACHE:
                    lat, lon = None, None
                    if 'lat' in s: lat, lon = s['lat'], s['lon']
                    else:
                        c = get_coords_from_name(s.get('name', ''))
                        if c: lat, lon = c[0], c[1]
                    if lat:
                        k = (round(lat,2), round(lon,2))
                        if k not in seen_locations:
                            seen_locations.add(k)
                            flag = "📍"
                            try: flag = detect_country_group(s['name']).split(' ')[0]
                            except: pass
                            globe_data_list.append({'lat': lat, 'lon': lon, 'name': flag})

                import json
                json_data = json.dumps(globe_data_list, ensure_ascii=False)
                
                # 2. 渲染容器
                ui.html(GLOBE_STRUCTURE, sanitize=False).classes('w-full h-[650px] overflow-hidden')
                
                # 3. 注入数据和 JS
                ui.run_javascript(f'window.DASHBOARD_DATA = {json_data};')
                ui.run_javascript(GLOBE_JS_LOGIC)
                
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
                            
                            # ✨✨✨ 修复点：函数名修正 get_all_groups_set -> get_all_groups ✨✨✨
                            groups = get_all_groups()
                            
                            # 允许用户手打新分组
                            sel = ui.select(groups, label='选择或输入分组', with_input=True, new_value_mode='add-unique').classes('w-full')
                            
                            ui.button('确定移动', on_click=lambda: do_move(sel.value)).classes('w-full mt-4 bg-blue-600 text-white')
                            
                            async def do_move(target_group):
                                if not target_group: return
                                count = 0
                                for s in SERVERS_CACHE:
                                    if s['url'] in self.selected_urls:
                                        s['group'] = target_group
                                        count += 1
                                
                                # 同时也更新一下自定义分组列表，防止新输入的分组消失
                                if 'custom_groups' not in ADMIN_CONFIG: ADMIN_CONFIG['custom_groups'] = []
                                if target_group not in ADMIN_CONFIG['custom_groups'] and target_group != '默认分组':
                                    ADMIN_CONFIG['custom_groups'].append(target_group)
                                    await save_admin_config()

                                await save_servers()
                                sub_d.close(); self.dialog.close() # 关闭所有弹窗
                                
                                # 刷新侧边栏和主内容
                                render_sidebar_content.refresh()
                                try: await refresh_content('ALL') 
                                except: pass
                                
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


# =================  全能分组管理 (升级版：带搜索、智能全选、防闪白) =================
def open_combined_group_management(group_name):
    # ✨ 1. 准备数据结构：用于存储每一行的 UI 引用，以便控制显隐
    ui_rows = {}
    
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-[600px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden'):
        
        # --- 标题栏 ---
        with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b flex-shrink-0'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('settings', color='primary').classes('text-xl')
                ui.label(f'管理分组: {group_name}').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        # --- 内容区域 ---
        with ui.column().classes('w-full flex-grow overflow-hidden p-0'):
            
            # --- A. 顶部设置区 (名称 + 搜索) ---
            with ui.column().classes('w-full p-4 border-b bg-white gap-3 flex-shrink-0'):
                # 分组名称修改
                ui.label('分组名称').classes('text-xs font-bold text-gray-500 mb-[-5px]')
                name_input = ui.input(value=group_name).props('outlined dense').classes('w-full')
                
                # ✨✨✨ 新增：搜索框 ✨✨✨
                ui.label('搜索筛选').classes('text-xs font-bold text-gray-500 mb-[-5px]')
                search_input = ui.input(placeholder='🔍 搜名称 / IP...').props('outlined dense clearable').classes('w-full')
                
                # 搜索逻辑
                def on_search(e):
                    keyword = str(e.value).lower().strip()
                    for url, item in ui_rows.items():
                        # 控制行的可见性
                        is_match = keyword in item['search_text']
                        item['row'].set_visibility(is_match)
                
                search_input.on_value_change(on_search)

            # --- B. 成员选择区域 ---
            with ui.column().classes('w-full flex-grow overflow-hidden relative'):
                # 工具栏
                with ui.row().classes('w-full p-2 bg-gray-100 justify-between items-center border-b flex-shrink-0'):
                    ui.label('成员选择:').classes('text-xs font-bold text-gray-500 ml-2')
                    with ui.row().classes('gap-1'):
                        # ✨ 绑定新的全选逻辑
                        ui.button('全选 (当前)', on_click=lambda: toggle_visible(True)).props('flat dense size=xs color=primary')
                        ui.button('清空', on_click=lambda: toggle_visible(False)).props('flat dense size=xs color=grey')

                with ui.scroll_area().classes('w-full flex-grow p-2'):
                    with ui.column().classes('w-full gap-1'):
                        
                        selection_map = {} 
                        
                        try: sorted_servers = sorted(SERVERS_CACHE, key=lambda x: str(x.get('name', '')))
                        except: sorted_servers = SERVERS_CACHE 

                        if not sorted_servers:
                            ui.label('暂无服务器数据').classes('w-full text-center text-gray-400 mt-4')

                        for s in sorted_servers:
                            # 判断逻辑：只要 tags 里有这个组名，就算选中
                            tags = s.get('tags', [])
                            if not isinstance(tags, list): tags = []
                            is_in_group = group_name in tags
                            
                            # 兼容旧数据：如果 group 字段也是这个名字，也算选中
                            if s.get('group') == group_name: is_in_group = True
                            
                            selection_map[s['url']] = is_in_group
                            
                            # 准备搜索文本
                            ip_addr = s['url'].split('//')[-1].split(':')[0]
                            search_key = f"{s['name']} {ip_addr}".lower()

                            # 渲染行
                            # ✨ 修改：这里要把 Checkbox 和 Row 点击事件分离，防止冒泡
                            with ui.row().classes('w-full items-center p-2 hover:bg-blue-50 rounded border border-transparent hover:border-blue-200 transition cursor-pointer') as row:
                                chk = ui.checkbox(value=is_in_group).props('dense')
                                
                                # 点击行也可以勾选 (更稳健的写法)
                                row.on('click', lambda _, c=chk: c.set_value(not c.value))
                                chk.on('click.stop', lambda: None) # 阻止 checkbox 点击穿透
                                
                                chk.on_value_change(lambda e, u=s['url']: selection_map.update({u: e.value}))
                                
                                # 信息展示
                                with ui.column().classes('gap-0 ml-2 flex-grow overflow-hidden'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.label(s['name']).classes('text-sm font-bold truncate text-gray-700')
                                        
                                # 真实区域展示
                                try:
                                    real_region = detect_country_group(s['name'], None)
                                    ui.label(real_region).classes('text-xs font-mono text-gray-400')
                                except: pass
                            
                            # ✨ 存入 UI 字典供搜索使用
                            ui_rows[s['url']] = {
                                'row': row,
                                'chk': chk,
                                'search_text': search_key
                            }

                # ✨✨✨ 智能全选/清空函数 ✨✨✨
                def toggle_visible(state):
                    count = 0
                    for item in ui_rows.values():
                        # 只操作当前可见的行
                        if item['row'].visible:
                            item['chk'].value = state
                            count += 1
                    if state and count > 0:
                        safe_notify(f"已选中当前显示的 {count} 个服务器", "positive")

        # 3. 底部按钮栏
        with ui.row().classes('w-full p-4 border-t bg-gray-50 justify-between items-center flex-shrink-0'):
            
            # === 删除分组 (核心修改：防闪白) ===
            async def delete_group():
                with ui.dialog() as confirm_d, ui.card():
                    ui.label(f'确定永久删除分组 "{group_name}"?').classes('font-bold text-red-600')
                    ui.label('服务器将保留，仅移除此标签，并恢复回原区域分组。').classes('text-xs text-gray-500')
                    with ui.row().classes('w-full justify-end mt-4 gap-2'):
                        ui.button('取消', on_click=confirm_d.close).props('flat dense')
                        async def do_del():
                            if 'custom_groups' in ADMIN_CONFIG and group_name in ADMIN_CONFIG['custom_groups']:
                                ADMIN_CONFIG['custom_groups'].remove(group_name)
                            
                            for s in SERVERS_CACHE:
                                if 'tags' in s and group_name in s['tags']: s['tags'].remove(group_name)
                                # 兼容处理
                                if s.get('group') == group_name:
                                    try: s['group'] = detect_country_group(s['name'], None)
                                    except: s['group'] = '默认分组'

                            await save_admin_config()
                            await save_servers()
                            confirm_d.close(); d.close()
                            
                            # ✨✨✨ [关键修改] 只刷新侧边栏，不刷新内容 ✨✨✨
                            render_sidebar_content.refresh()
                            
                            # 只有当前正在看这个组时，才跳回首页
                            if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                                await refresh_content('ALL')
                            else:
                                safe_notify(f'分组 "{group_name}" 已删除', 'positive')
                                
                        ui.button('确认删除', color='red', on_click=do_del)
                confirm_d.open()

            ui.button('删除分组', icon='delete', color='red', on_click=delete_group).props('flat')

            # === 保存修改 (重命名逻辑) ===
            async def save_changes():
                new_name = name_input.value.strip()
                if not new_name: return safe_notify('分组名称不能为空', 'warning')
                
                # 1. 更新分组名列表
                if new_name != group_name:
                    if 'custom_groups' in ADMIN_CONFIG:
                        if group_name in ADMIN_CONFIG['custom_groups']:
                            idx = ADMIN_CONFIG['custom_groups'].index(group_name)
                            ADMIN_CONFIG['custom_groups'][idx] = new_name
                        else:
                            ADMIN_CONFIG['custom_groups'].append(new_name)
                    await save_admin_config()

                # 2. 更新服务器 Tags
                for s in SERVERS_CACHE:
                    if 'tags' not in s or not isinstance(s['tags'], list): s['tags'] = []
                    
                    should_have_tag = selection_map.get(s['url'], False)
                    
                    if should_have_tag:
                        if new_name not in s['tags']: s['tags'].append(new_name)
                        if new_name != group_name and group_name in s['tags']: s['tags'].remove(group_name)
                    else:
                        if new_name in s['tags']: s['tags'].remove(new_name)
                        if group_name in s['tags']: s['tags'].remove(group_name)

                await save_servers()
                d.close()
                
                # 刷新 UI
                render_sidebar_content.refresh()
                
                # 如果改了名，且正好在看旧分组，刷新内容到新分组
                if CURRENT_VIEW_STATE.get('scope') == 'TAG' and CURRENT_VIEW_STATE.get('data') == group_name:
                    await refresh_content('TAG', new_name, force_refresh=True)
                
                safe_notify('分组设置已保存', 'positive')

            ui.button('保存修改', icon='save', on_click=save_changes).classes('bg-slate-900 text-white shadow-lg')

    d.open()
    
# ================= 快捷创建分组弹窗 =================
def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('新建自定义分组').classes('text-lg font-bold mb-2')
        
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


# ================= 全局 UI 索引 (用于实现 0 闪烁移动) =================
SIDEBAR_UI_REFS = {
    'groups': {},      # 存储格式: {'默认分组': ui_column_element, ...}
    'rows': {}         # 存储格式: {'http://1.2.3.4': ui_row_element, ...}
}

# 提取出来的单行渲染函数 (保持与之前一样的样式)
def render_single_sidebar_row(s):
    # 样式定义 (与之前保持一致)
    btn_keycap_base = 'bg-white border-t border-x border-gray-200 border-b-[3px] border-b-gray-300 rounded-lg transition-all duration-100 active:border-b-0 active:border-t-[3px] active:translate-y-[3px]'
    btn_name_cls = f'{btn_keycap_base} flex-grow text-xs font-bold text-gray-700 truncate px-3 py-2.5 hover:bg-gray-50 hover:text-black hover:border-gray-400'
    btn_settings_cls = f'{btn_keycap_base} w-10 py-2.5 px-0 flex items-center justify-center text-gray-400 hover:text-gray-700 hover:bg-gray-50 hover:border-gray-400'

    # 创建行容器
    with ui.row().classes('w-full gap-2 no-wrap items-stretch') as row:
        # 1. 服务器名字按钮 (带绑定)
        ui.button(on_click=lambda _, s=s: refresh_content('SINGLE', s)) \
            .bind_text_from(s, 'name') \
            .props('no-caps align=left flat text-color=grey-8') \
            .classes(btn_name_cls)
        
        # 2. 设置按钮
        ui.button(icon='settings', on_click=lambda _, s=s: open_server_dialog(SERVERS_CACHE.index(s))) \
            .props('flat square size=sm text-color=grey-5') \
            .classes(btn_settings_cls).tooltip('配置 / 删除')
    
    # 注册到全局索引
    SIDEBAR_UI_REFS['rows'][s['url']] = row
    return row

# ================= 侧边栏渲染 (最终版：绑定模式，修改名字0闪烁) =================
_current_dragged_group = None 

@ui.refreshable
def render_sidebar_content():
    global _current_dragged_group
    
    # 每次重绘前清空索引，防止引用死对象
    SIDEBAR_UI_REFS['groups'].clear()
    SIDEBAR_UI_REFS['rows'].clear()

    # --- 1. 顶部固定区域 (保持不变) ---
    btn_top_style = 'w-full bg-white border border-gray-200 rounded-lg shadow-sm text-gray-600 font-medium px-3 py-2 transition-all duration-200 ease-out hover:shadow-md hover:-translate-y-0.5 hover:border-gray-300 hover:text-gray-900 active:translate-y-0 active:shadow-none active:bg-gray-50 active:scale-[0.98]'
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0 relative overflow-hidden'):
        ui.label('X-Fusion').classes('absolute top-2 right-6 text-[3rem] font-black text-gray-200 opacity-30 pointer-events-none -rotate-12 select-none z-0 tracking-tighter leading-tight')
        ui.label('小龙女她爸').classes('text-2xl font-black mb-4 z-10 relative bg-gradient-to-r from-gray-700 to-black bg-clip-text text-transparent tracking-wide drop-shadow-sm')
        with ui.column().classes('w-full gap-2 z-10 relative'):
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes(btn_top_style)
            ui.button('探针设置', icon='tune', on_click=render_probe_page).props('flat align=left').classes(btn_top_style)
            ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(btn_top_style)
            
    # --- 2. 列表区域 ---
    with ui.column().props('id=sidebar-scroll-box').classes('w-full flex-grow overflow-y-auto p-2 gap-2 bg-slate-50'):
        # 功能按钮
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            func_btn_base = 'flex-grow text-xs font-bold text-white rounded-lg border-b-4 active:border-b-0 active:translate-y-[4px] transition-all'
            ui.button('新建分组', icon='create_new_folder', on_click=open_quick_group_create_dialog).props('dense unelevated').classes(f'bg-blue-500 border-blue-700 hover:bg-blue-400 {func_btn_base}')
            ui.button('添加服务器', icon='add', color='green', on_click=lambda: open_server_dialog(None)).props('dense unelevated').classes(f'bg-green-500 border-green-700 hover:bg-green-400 {func_btn_base}')
                
        # --- A. 全部服务器 ---
        list_item_3d = 'w-full items-center justify-between p-3 border border-gray-200 rounded-xl mb-1 bg-white shadow-sm cursor-pointer group transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 hover:border-gray-300 active:translate-y-0 active:shadow-none active:bg-gray-50 active:scale-[0.98]'
        with ui.row().classes(list_item_3d).on('click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-3'):
                with ui.column().classes('p-1.5 bg-gray-100 rounded-lg group-hover:bg-gray-200 transition-colors'):
                    ui.icon('dns', color='grey-8').classes('text-sm')
                ui.label('所有服务器').classes('font-bold text-gray-700')
            ui.badge(str(len(SERVERS_CACHE)), color='blue').props('rounded outline')

        def on_drag_start(e, name): global _current_dragged_group; _current_dragged_group = name

        # --- B. 自定义分组 ---
        final_tags = ADMIN_CONFIG.get('custom_groups', [])
        async def on_tag_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(final_tags)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group); item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name); current_list.insert(new_idx, item)
                    ADMIN_CONFIG['custom_groups'] = current_list; await save_admin_config()
                    _current_dragged_group = None; render_sidebar_content.refresh()
            except: pass

        if final_tags:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
            for tag_group in final_tags:
                tag_servers = [s for s in SERVERS_CACHE if isinstance(s, dict) and (tag_group in s.get('tags', []) or s.get('group') == tag_group)]
                try: tag_servers.sort(key=smart_sort_key)
                except: tag_servers.sort(key=lambda x: x.get('name', ''))
                is_open = tag_group in EXPANDED_GROUPS
                
                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=tag_group: on_tag_drop(e, n)):
                    with ui.expansion('', icon=None, value=is_open).classes('w-full border border-gray-200 rounded-xl mb-2 bg-white shadow-sm transition-all duration-300 hover:border-gray-300 hover:shadow-md').props('expand-icon-toggle').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap cursor-pointer py-1 group/header transition-all duration-200 active:bg-gray-100 active:scale-[0.98]').on('click', lambda _, g=tag_group: refresh_content('TAG', g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                                    ui.icon('drag_indicator').props('draggable="true"').classes('cursor-move text-gray-300 hover:text-gray-500 p-1 rounded transition-colors group-hover/header:text-gray-400').on('dragstart', lambda e, n=tag_group: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    ui.icon('folder', color='primary').classes('opacity-70')
                                    ui.label(tag_group).classes('flex-grow font-bold text-gray-700 truncate')
                                with ui.row().classes('items-center gap-2 pr-2').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='settings', on_click=lambda _, g=tag_group: open_combined_group_management(g)).props('flat dense round size=xs color=grey-4').classes('hover:text-gray-700').tooltip('管理分组')
                                    ui.badge(str(len(tag_servers)), color='orange' if not tag_servers else 'grey').props('rounded outline')
                        
                        # ✨✨✨ 注册分组容器 ✨✨✨
                        with ui.column().classes('w-full gap-2 p-2 bg-gray-50/50') as col:
                            SIDEBAR_UI_REFS['groups'][tag_group] = col
                            for s in tag_servers:
                                render_single_sidebar_row(s) # 使用提取的函数

        # --- C. 区域分组 ---
        ui.label('区域分组').classes('text-xs font-bold text-gray-400 mt-4 mb-2 px-2 uppercase tracking-wider')
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''), s)
            if c_group in ['默认分组', '自动注册', '自动导入', '未分组', '', None]: c_group = '🏳️ 其他地区'
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)
        
        saved_order = ADMIN_CONFIG.get('group_order', [])
        def region_sort_key(name): return saved_order.index(name) if name in saved_order else 9999
        sorted_regions = sorted(country_buckets.keys(), key=region_sort_key)

        async def on_region_drop(e, target_name):
            global _current_dragged_group
            if not _current_dragged_group or _current_dragged_group == target_name: return
            try:
                current_list = list(sorted_regions)
                if _current_dragged_group in current_list and target_name in current_list:
                    old_idx = current_list.index(_current_dragged_group); item = current_list.pop(old_idx)
                    new_idx = current_list.index(target_name); current_list.insert(new_idx, item)
                    ADMIN_CONFIG['group_order'] = current_list; await save_admin_config()
                    _current_dragged_group = None; render_sidebar_content.refresh()
            except: pass

        with ui.column().classes('w-full gap-2 pb-4'):
            for c_name in sorted_regions:
                c_servers = country_buckets[c_name]
                try: c_servers.sort(key=smart_sort_key)
                except: c_servers.sort(key=lambda x: x.get('name', ''))
                is_open = c_name in EXPANDED_GROUPS

                with ui.element('div').classes('w-full').on('dragover.prevent', lambda _: None).on('drop', lambda e, n=c_name: on_region_drop(e, n)):
                    with ui.expansion('', icon=None, value=is_open).classes('w-full border border-gray-200 rounded-xl bg-white shadow-sm transition-all duration-300 hover:border-gray-300 hover:shadow-md').props('expand-icon-toggle').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                        with exp.add_slot('header'):
                            with ui.row().classes('w-full h-full items-center justify-between no-wrap py-2 cursor-pointer group/header transition-all duration-200 active:bg-gray-50 active:scale-[0.98]').on('click', lambda _, g=c_name: refresh_content('COUNTRY', g)):
                                with ui.row().classes('items-center gap-3 flex-grow overflow-hidden'):
                                    ui.icon('drag_indicator').props('draggable="true"').classes('cursor-move text-gray-300 hover:text-gray-500 p-1 rounded transition-colors group-hover/header:text-gray-400').on('dragstart', lambda e, n=c_name: on_drag_start(e, n)).on('click.stop').tooltip('按住拖拽')
                                    with ui.row().classes('items-center gap-2 flex-grow'):
                                        flag = c_name.split(' ')[0] if ' ' in c_name else '🏳️'
                                        ui.label(flag).classes('text-lg filter drop-shadow-sm')
                                        display_name = c_name.split(' ')[1] if ' ' in c_name else c_name
                                        ui.label(display_name).classes('font-bold text-gray-700 truncate')
                                with ui.row().classes('items-center gap-2 pr-2').on('mousedown.stop').on('click.stop'):
                                    ui.button(icon='edit_note', on_click=lambda _, s=c_servers, t=c_name: open_bulk_edit_dialog(s, f"区域: {t}")).props('flat dense round size=xs color=grey-4').classes('hover:text-gray-600').tooltip('批量管理')
                                    ui.badge(str(len(c_servers)), color='green').props('rounded outline').classes('font-mono font-bold')

                        # ✨✨✨ 注册区域容器 ✨✨✨
                        with ui.column().classes('w-full gap-2 p-2 bg-slate-50/80 border-t border-gray-100') as col:
                            SIDEBAR_UI_REFS['groups'][c_name] = col
                            for s in c_servers:
                                render_single_sidebar_row(s) # 使用提取的函数

    # JS 滚动记忆
    ui.run_javascript('''
        (function() {
            var el = document.getElementById("sidebar-scroll-box");
            if (el) {
                if (window.sidebarScroll) el.scrollTop = window.sidebarScroll;
                el.addEventListener("scroll", function() { window.sidebarScroll = el.scrollTop; });
            }
        })();
    ''')
    
    # 底部
    with ui.column().classes('w-full p-2 border-t mt-auto mb-4 gap-2 bg-white z-10 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]'):
        bottom_btn_3d = 'w-full text-gray-600 text-xs font-bold bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 transition-all duration-200 hover:bg-white hover:shadow-md hover:border-slate-300 hover:text-slate-900 active:translate-y-[1px] active:bg-slate-100 active:shadow-none'
        ui.button('批量 SSH 执行', icon='playlist_play', on_click=batch_ssh_manager.open_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('Cloudflare 设置', icon='cloud', on_click=open_cloudflare_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('全局 SSH 设置', icon='vpn_key', on_click=open_global_settings_dialog).props('flat align=left').classes(bottom_btn_3d)
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes(bottom_btn_3d)
        
# ================== 登录与 MFA 逻辑 (修正版) ==================
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

            ui.label('© Powered by 小龙女她爸').classes('text-xs text-gray-400 mt-6 w-full text-center font-mono opacity-80')

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
            
            with ui.row().classes('w-full justify-center items-center gap-1 mb-4 bg-gray-100 p-1 rounded cursor-pointer').on('click', lambda: safe_copy_to_clipboard(secret)):
                ui.label(secret).classes('text-xs font-mono text-gray-600')
                ui.icon('content_copy').classes('text-gray-400 text-xs')

            code = ui.input('验证码', placeholder='6位数字').props('outlined dense input-class=text-center').classes('w-full mb-4')
            
            async def confirm():
                totp = pyotp.TOTP(secret)
                if totp.verify(code.value):
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

    # ✨✨✨ [核心修复] finish 函数 ✨✨✨
    def finish():
        # 1. 基础认证标记
        app.storage.user['authenticated'] = True
        
        # 2. 写入全局版本号 (防止被踢出)
        if 'session_version' not in ADMIN_CONFIG:
            ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        app.storage.user['session_version'] = ADMIN_CONFIG['session_version']
        
        # 3. 记录 IP (用于主页的变动检测弹窗)
        try:
            client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
            # 变量名必须是 last_known_ip，与主页对应
            app.storage.user['last_known_ip'] = client_ip
        except: pass

        ui.navigate.to('/')

    render_step1()

# ================= 0. 认证检查辅助函数 (升级版：支持版本控制) =================
def check_auth(request: Request):
    """
    检查用户是否已登录，且会话版本是否有效
    """
    # 1. 基础认证：检查 Cookie 里有没有 authenticated 标记
    if not app.storage.user.get('authenticated', False):
        return False
    
    # 2. 全局会话版本校验 (实现一键踢人核心逻辑)
    # 获取当前系统要求的全局版本号 (如 v1)
    current_global_ver = ADMIN_CONFIG.get('session_version', 'init')
    # 获取用户 Cookie 里的版本号
    user_ver = app.storage.user.get('session_version', '')
    
    # 如果版本不匹配 (比如管理员刚刚重置了密钥)，视为未登录
    if current_global_ver != user_ver:
        return False
        
    return True

# ================= [本地化版] 主页入口 (含 IP 检测与强制下线) =================
@ui.page('/')
def main_page(request: Request):
    # ================= 1. 注入全局资源与样式 (修复国旗显示) =================
    ui.add_head_html('<link rel="stylesheet" href="/static/xterm.css" />')
    ui.add_head_html('<script src="/static/xterm.js"></script>')
    ui.add_head_html('<script src="/static/xterm-addon-fit.js"></script>')
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
    
    # ✨✨✨ 核心修复：引入 Twemoji 字体 polyfill ✨✨✨
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            /* 1. 定义国旗专用字体 */
            @font-face {
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }
            
            /* 2. 全局应用字体 */
            body { 
                font-family: 'Twemoji Country Flags', 'Noto Sans SC', "Roboto", "Helvetica", "Arial", sans-serif, "Noto Color Emoji"; 
                background-color: #f8fafc; 
            }
            .nicegui-connection-lost { display: none !important; }
        </style>
    ''')
    # ================= 2. 认证检查 =================
    if not check_auth(request): 
        return RedirectResponse('/login')

    # ================= 3. IP 变动检测与处理 =================
    try:
        # 获取当前真实 IP
        current_ip = request.headers.get("X-Forwarded-For", request.client.host).split(',')[0].strip()
    except:
        current_ip = "Unknown"
        
    display_ip = current_ip # 用于右上角显示

    # 获取上次记录的 IP (从 Cookie 读取)
    last_ip = app.storage.user.get('last_known_ip', '')
    
    # 立即更新存储为当前 IP (为下一次检测做准备)
    app.storage.user['last_known_ip'] = current_ip
    
    # ✨✨✨ 核心逻辑：强制下线 (重置密钥) ✨✨✨
    async def reset_global_session(dialog_ref=None):
        # 1. 生成新的随机版本号 (例如 "v2")
        new_ver = str(uuid.uuid4())[:8]
        ADMIN_CONFIG['session_version'] = new_ver
        await save_admin_config()
        
        if dialog_ref: dialog_ref.close()
        
        # 2. 弹出提示并等待
        ui.notify('🔒 安全密钥已重置，正在强制所有设备下线...', type='warning', close_button=False)
        await asyncio.sleep(1.5)
        
        # 3. 清除当前用户的 Session 并跳转登录页
        app.storage.user.clear()
        ui.navigate.to('/login')

    # ✨✨✨ 弹窗逻辑：如果 IP 变了，弹出提示框 ✨✨✨
    if last_ip and last_ip != current_ip:
        def open_ip_alert():
            with ui.dialog() as d, ui.card().classes('w-96 p-5 border-t-4 border-red-500 shadow-2xl'):
                with ui.row().classes('items-center gap-2 text-red-600 mb-2'):
                    ui.icon('security', size='md')
                    ui.label('安全警告：登录 IP 变动').classes('font-bold text-lg')
                
                ui.label('检测到您的登录 IP 发生了变化：').classes('text-sm text-gray-600')
                
                with ui.grid().classes('grid-cols-2 gap-2 my-4 bg-red-50 p-3 rounded border border-red-100'):
                    ui.label('上次 IP:').classes('text-xs font-bold text-gray-500')
                    ui.label(last_ip).classes('text-xs font-mono font-bold text-gray-800')
                    ui.label('本次 IP:').classes('text-xs font-bold text-gray-500')
                    ui.label(current_ip).classes('text-xs font-mono font-bold text-blue-600')
                
                ui.label('如果是您切换了网络 (如 Wi-Fi 转 4G)，请忽略。').classes('text-xs text-gray-400')
                ui.label('若非本人操作，请立即重置密钥！').classes('text-xs text-red-500 font-bold mt-1')

                with ui.row().classes('w-full justify-end gap-2 mt-4'):
                    ui.button('我知道了', on_click=d.close).props('flat dense color=grey')
                    # 点击此按钮触发强制下线
                    ui.button('强制所有设备下线', color='red', icon='gpp_bad', on_click=lambda: reset_global_session(d)).props('unelevated dense')
            d.open()
        
        # 延迟 0.5 秒弹出，确保页面加载完毕
        ui.timer(0.5, open_ip_alert, once=True)

    # ================= 4. UI 构建 =================
    
    # 左侧抽屉
    with ui.left_drawer(value=True, fixed=True).classes('bg-gray-50 border-r').props('width=400 bordered') as drawer:
        render_sidebar_content()

    # 顶部导航栏
    with ui.header().classes('bg-slate-900 text-white h-14 shadow-md'):
        with ui.row().classes('w-full items-center justify-between'):
            
            # 左侧
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense color=white')
                
                ui.label('X-Fusion Panel').classes('text-lg font-bold ml-2 tracking-wide')
                ui.label(f"[{display_ip}]").classes('text-xs text-gray-400 font-mono pt-1 hidden sm:block')

            # 右侧按钮区
            with ui.row().classes('items-center gap-2 mr-2'):
                
                # ✨✨✨ [新增] 主动重置密钥按钮 (盾牌图标) ✨✨✨
                with ui.button(icon='gpp_bad', color='red', on_click=lambda: reset_global_session(None)).props('flat dense round').tooltip('安全重置：强制所有已登录用户下线'):
                     ui.badge('Reset', color='orange').props('floating rounded')

                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round').tooltip('点击复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating rounded')
                
                ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense').tooltip('退出登录')

    # 主内容区域
    global content_container
    content_container = ui.column().classes('w-full h-full pl-4 pr-4 pt-4 overflow-y-auto bg-slate-50')
    
    # ================= 5. 后台任务 (自动初始化) =================
    async def auto_init_system_settings():
        try:
            current_origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
            if not current_origin: return

            stored_url = ADMIN_CONFIG.get('manager_base_url', '')
            need_save = False
            
            # 初始化会话版本 (防止第一次登录报错)
            if 'session_version' not in ADMIN_CONFIG:
                ADMIN_CONFIG['session_version'] = 'init_v1'
                need_save = True

            if not stored_url or 'xui-manager' in stored_url or '127.0.0.1' in stored_url:
                ADMIN_CONFIG['manager_base_url'] = current_origin
                need_save = True

            if not ADMIN_CONFIG.get('probe_enabled'):
                ADMIN_CONFIG['probe_enabled'] = True
                need_save = True

            if need_save: await save_admin_config()
        except: pass

    ui.timer(1.0, auto_init_system_settings, once=True)

    # 视图恢复逻辑
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
FAILURE_COUNTS = {}  # 记录连续失败次数

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
    
# ================= 优化后的监控任务 (高性能版：高并发 + 防误报 + 历史记录) =================
async def job_monitor_status():
    """
    监控任务：每分钟检查一次服务器状态
    优化：将并发数从 5 提升至 50，以支持 1000 台服务器在 30-40秒内完成轮询
    """
    # ✨✨✨ 核心修改：将并发数提高到 50 ✨✨✨
    # 解释：对于 1000 台服务器，50 并发意味着同时处理 50 台，大约 20 轮即可跑完。
    # 假设每轮耗时 1.5s，总耗时约 30s，完全可以在 60s 或 120s 的间隔内完成。
    sema = asyncio.Semaphore(50) 
    
    # 定义报警阈值：连续失败 3 次才报警
    FAILURE_THRESHOLD = 3 
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _check_single_server(srv):
        async with sema:
            # 稍微让出一点 CPU 时间片，避免高并发瞬间卡顿 UI
            await asyncio.sleep(0.01) 
            
            res = await get_server_status(srv)
            name = srv.get('name', 'Unknown')
            url = srv['url']
            
            # ✨✨✨ [新增] 如果不是探针机器(探针已经在push接口记过了)，则在这里补录历史 ✨✨✨
            if not srv.get('probe_installed'):
                 if res and 'pings' in res:
                     record_ping_history(url, res['pings'])

            # 如果没配 TG，后面的报警逻辑就跳过
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
                            f"⚠️ **提示**: 连续监测，无法连接"
                        )
                        logger.warning(f"🔔 [报警] {name} 确认离线 (重试{current_count}次)")
                        asyncio.create_task(send_telegram_message(msg))
                        ALERT_CACHE[url] = 'offline'

    # 创建所有任务并执行
    tasks = [_check_single_server(s) for s in SERVERS_CACHE]
    await asyncio.gather(*tasks)

# ✨✨✨ 注册本地静态文件目录 ✨✨✨
app.add_static_files('/static', 'static')

# ================= 定义流量同步任务 =================
async def job_sync_all_traffic():
    logger.info("🕒 [定时任务] 开始全量同步流量...")
    # ✨✨✨ [修改点]：显式传入 sync_name=False，确保定时任务只拉流量，不改名字 ✨✨✨
    tasks = [fetch_inbounds_safe(s, force_refresh=True, sync_name=False) for s in SERVERS_CACHE]
    
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        await save_nodes_cache()
        await refresh_dashboard_ui()
        
    logger.info("✅ [定时任务] 流量同步完成 (已落盘，跳过名称同步)")

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
    
    # 2. 服务器状态监控与报警 (120秒一次) ✨✨✨
    scheduler.add_job(job_monitor_status, 'interval', seconds=120, id='status_monitor', replace_existing=True, max_instances=1)
    
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

# 1. 全局地图名称映射表 (✨严格清洗版：移除 AR/US 等易误判短词✨)
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
    
# ================= [手机端] 详情弹窗  =================
def open_mobile_server_detail(server_conf):
    # 注入 CSS
    ui.add_head_html('''
        <style>
            .full-height-dialog { height: 85vh !important; max-height: 95vh !important; }
            @media (orientation: landscape) { .full-height-dialog { height: 95vh !important; } }
            .q-tabs__arrow { display: none !important; }
            .q-tabs__content { overflow: hidden !important; flex-wrap: nowrap !important; }
            .q-tab { cursor: pointer !important; min-height: 32px !important; }
            .q-tab__content { padding: 0 8px !important; }
            .detail-scroll-area, .detail-scroll-area .q-scrollarea__container, 
            .detail-scroll-area .q-scrollarea__content { width: 100% !important; max-width: 100% !important; }
            .q-dialog__inner--minimized > div { max-width: 95vw !important; }
            
            /* ✨ 修复：卡片样式 (无缩放，仅变色) */
            .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
            .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
        </style>
    ''')

    try:
        LABEL_STYLE = 'text-gray-500 font-bold text-[9px] md:text-[10px] uppercase tracking-wider' 
        VALUE_STYLE = 'text-gray-200 font-mono text-xs md:text-sm truncate font-bold'
        BORDER_STYLE = 'border border-white/10'
        CARD_BG = 'bg-[#1e293b]/50'
        
        # 状态管理
        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        with ui.dialog() as d, ui.card().classes(
            'p-0 overflow-hidden flex flex-col bg-[#0f172a] border border-slate-700 shadow-2xl full-height-dialog'
        ).style('width: 95vw; max-width: 900px; border-radius: 20px;'): 
            d.props('backdrop-filter="blur(10px)"')
            
            # --- 1. 顶部标题栏 ---
            with ui.row().classes('w-full items-center justify-between p-3 md:p-6 bg-[#1e293b] border-b border-slate-700 flex-shrink-0 flex-nowrap'):
                with ui.row().classes('items-center gap-3 overflow-hidden flex-nowrap'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-xl md:text-3xl flex-shrink-0') 
                    ui.label(server_conf['name']).classes('text-base md:text-lg font-black text-white truncate flex-grow')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=white')

            # --- 2. 内容滚动区 ---
            with ui.scroll_area().classes('w-full flex-grow detail-scroll-area'):
                with ui.column().classes('p-4 md:p-8 gap-4 w-full'):
                    refs = {} 
                    
                    # A. 系统信息模块
                    with ui.card().classes(f'w-full p-0 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        ui.label('系统信息').classes('text-[10px] font-black text-blue-500 m-3 mb-1 tracking-widest')
                        with ui.row().classes('w-full flex-wrap md:flex-nowrap items-stretch p-0'):
                            def info_row(label, key, value_cls=VALUE_STYLE):
                                with ui.row().classes('w-full items-center justify-between border-b border-white/5 pb-1.5 mb-1.5 last:border-0 last:mb-0'):
                                    ui.label(label).classes(LABEL_STYLE)
                                    refs[key] = ui.label('Loading...').classes(value_cls)
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 border-b md:border-b-0 md:border-r border-white/10 gap-1'):
                                info_row('CPU 型号', 'cpu_model'); info_row('操作系统', 'os')
                                info_row('内存', 'mem_detail'); info_row('总流量', 'traffic_detail')
                            with ui.column().classes('w-full md:w-1/2 p-3 md:p-6 gap-1'):
                                info_row('架构/虚拟', 'arch_virt')
                                info_row('硬盘', 'disk_detail')
                                info_row('实时网速', 'speed_detail', value_cls='text-blue-400 font-mono text-xs font-bold text-right')
                                info_row('系统负载', 'load')

                    # B. 三网延迟模块 (修复：点击仅变色，不移位)
                    with ui.card().classes(f'w-full p-3 rounded-xl {CARD_BG} {BORDER_STYLE}'):
                        ui.label('三网延迟 (点击切换)').classes('text-[10px] font-black text-purple-500 mb-2 tracking-widest')
                        with ui.grid().classes('w-full grid-cols-3 gap-2'):
                            
                            def toggle_series(idx, card_el, color_cls):
                                visible_series[idx] = not visible_series[idx]
                                if visible_series[idx]:
                                    # 选中：恢复颜色边框，移除透明边框和灰色滤镜
                                    card_el.classes(add=color_cls, remove='ping-card-inactive')
                                else:
                                    # 取消：添加透明边框和灰色滤镜，移除颜色边框
                                    card_el.classes(add='ping-card-inactive', remove=color_cls)
                                
                            def ping_box(name, color, key, idx):
                                color_border_cls = f'border-{color}-500' # 激活时的边框颜色
                                # 默认状态：激活
                                base_cls = f'bg-[#0f172a]/60 ping-card-base rounded-xl p-1.5 items-center flex flex-col cursor-pointer {color_border_cls}'
                                
                                with ui.element('div').classes(base_cls) as card:
                                    card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                                    ui.label(name).classes(f'text-{color}-400 font-bold text-[8px] whitespace-nowrap')
                                    refs[key] = ui.label('--').classes('text-white font-bold text-xs font-mono tracking-tighter')
                            
                            ping_box('电信', 'blue', 'ping_ct', 0)
                            ping_box('联通', 'orange', 'ping_cu', 1)
                            ping_box('移动', 'green', 'ping_cm', 2)

                    # C. 网络趋势模块
                    with ui.card().classes(f'w-full p-0 mb-2 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                        
                        # 工具栏
                        with ui.row().classes('w-full justify-between items-center p-3 border-b border-white/5'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label('网络趋势').classes('text-[10px] font-black text-teal-500 tracking-widest')
                                # 平滑开关
                                with ui.row().classes('items-center gap-1 cursor-pointer bg-white/5 px-2 py-0.5 rounded-full').on('click', lambda: [smooth_sw.set_value(not smooth_sw.value)]):
                                    smooth_sw = ui.switch().props('dense size=xs color=teal').classes('scale-75')
                                    ui.label('平滑').classes('text-[9px] text-gray-400 select-none')
                                    smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))

                            with ui.tabs().props('dense no-caps hide-arrows active-color=blue-400 indicator-color=transparent').classes('bg-white/5 rounded-lg p-0.5') as chart_tabs:
                                t_1h = ui.tab('1h', label='1小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                t_3h = ui.tab('3h', label='3小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                                t_6h = ui.tab('6h', label='6小时').classes('text-[9px] min-h-0 h-7 px-3 rounded-md')
                            chart_tabs.set_value('1h')

                        # EWMA 算法
                        def calculate_ewma(data, alpha=0.3):
                            if not data: return []
                            result = [data[0]]
                            for i in range(1, len(data)):
                                result.append(alpha * data[i] + (1 - alpha) * result[-1])
                            return [int(x) for x in result]

                        chart = ui.echart({
                            'backgroundColor': 'transparent',
                            'color': ['#3b82f6', '#f97316', '#22c55e'], 
                            'legend': { 'show': False },
                            'tooltip': {
                                'trigger': 'axis',
                                'backgroundColor': 'rgba(15, 23, 42, 0.9)',
                                'borderColor': '#334155',
                                'textStyle': {'color': '#f1f5f9', 'fontSize': 10},
                                'axisPointer': {'type': 'line', 'lineStyle': {'color': '#94a3b8', 'width': 1, 'type': 'dashed'}},
                                'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                            },
                            'dataZoom': [
                                {'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}
                            ],
                            'grid': { 'left': '2%', 'right': '4%', 'bottom': '5%', 'top': '10%', 'containLabel': True },
                            'xAxis': { 'type': 'category', 'boundaryGap': False, 'data': [], 'axisLabel': { 'fontSize': 8, 'color': '#64748b' } },
                            'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': 'rgba(255,255,255,0.05)' } }, 'axisLabel': { 'fontSize': 8, 'color': '#64748b' } },
                            'series': [
                                {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}},
                                {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'lineStyle': {'width': 1.5}}
                            ]
                        }).classes('w-full h-64 md:h-72')

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        if not status: return
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})
                        
                        refs['cpu_model'].set_text(status.get('cpu_model', static.get('cpu_model', 'Generic CPU')))
                        refs['os'].set_text(static.get('os', 'Linux'))
                        refs['mem_detail'].set_text(f"{int(status.get('mem_usage', 0))}% / {status.get('mem_total', 0)}G")
                        refs['arch_virt'].set_text(f"{static.get('arch', 'x64')} / {static.get('virt', 'kvm')}")
                        refs['disk_detail'].set_text(f"{int(status.get('disk_usage', 0))}% / {status.get('disk_total', 0)}G")
                        
                        def fmt_b(b): return format_bytes(b)
                        refs['traffic_detail'].set_text(f"↑{fmt_b(status.get('net_total_out', 0))} ↓{fmt_b(status.get('net_total_in', 0))}")
                        refs['speed_detail'].set_text(f"↑{fmt_b(status.get('net_speed_out', 0))}/s ↓{fmt_b(status.get('net_speed_in', 0))}/s")
                        refs['load'].set_text(str(status.get('load_1', 0)))
                        
                        pings = status.get('pings', {})
                        def fmt_p(v): return str(v) if v > 0 else "N/A"
                        refs['ping_ct'].set_text(fmt_p(pings.get('电信', -1)))
                        refs['ping_cu'].set_text(fmt_p(pings.get('联通', -1)))
                        refs['ping_cm'].set_text(fmt_p(pings.get('移动', -1)))

                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            if current_mode == '1h': duration = 3600
                            elif current_mode == '3h': duration = 10800
                            elif current_mode == '6h': duration = 21600 
                            else: duration = 3600
                            
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            
                            if sliced:
                                raw_ct = [p['ct'] for p in sliced]
                                raw_cu = [p['cu'] for p in sliced]
                                raw_cm = [p['cm'] for p in sliced]
                                times = [p['time_str'] for p in sliced]

                                if is_smooth['value']:
                                    final_ct = calculate_ewma(raw_ct)
                                    final_cu = calculate_ewma(raw_cu)
                                    final_cm = calculate_ewma(raw_cm)
                                else:
                                    final_ct, final_cu, final_cm = raw_ct, raw_cu, raw_cm

                                chart.options['xAxis']['data'] = times
                                chart.options['series'][0]['data'] = final_ct if visible_series[0] else []
                                chart.options['series'][1]['data'] = final_cu if visible_series[1] else []
                                chart.options['series'][2]['data'] = final_cm if visible_series[2] else []
                                
                                chart.update()
                    except: pass

                chart_tabs.on_value_change(update_dark_detail)

            # 3. 底部状态栏
            with ui.row().classes('w-full justify-center p-2 bg-[#0f172a] border-t border-white/5 flex-shrink-0'):
                ui.label(f"已运行: {PROBE_DATA_CACHE.get(server_conf['url'], {}).get('uptime', '-') or '-'}").classes('text-[10px] text-gray-500 font-mono')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())

    except Exception as e:
        print(f"Mobile Detail error: {e}")
        
# ================= [电脑端] 详情弹窗 (完美修复CPU数值显示) =================
def open_pc_server_detail(server_conf):
    try:
        # 1. 获取当前主题状态
        is_dark = app.storage.user.get('is_dark', True)
        
        # 2. 定义双模样式 
        LABEL_STYLE = 'text-slate-500 dark:text-gray-400 text-sm font-medium'
        VALUE_STYLE = 'text-[#1e293b] dark:text-gray-200 font-mono text-sm font-bold'
        SECTION_TITLE = 'text-[#1e293b] dark:text-gray-200 text-base font-black mb-4 flex items-center gap-2'
        DIALOG_BG = 'bg-white/85 backdrop-blur-xl dark:bg-[#0d1117] dark:backdrop-blur-none'
        CARD_BG   = 'bg-white/60 dark:bg-[#161b22]' 
        BORDER_STYLE = 'border border-white/50 dark:border-[#30363d]'
        SHADOW_STYLE = 'shadow-[0_8px_32px_0_rgba(31,38,135,0.15)] dark:shadow-2xl'
        TRACK_COLOR = 'blue-1' if not is_dark else 'grey-9'

        visible_series = {0: True, 1: True, 2: True}
        is_smooth = {'value': False}

        # 智能容量格式化
        def fmt_capacity(b):
            if b is None: return "0 B"
            try:
                if isinstance(b, str):
                    import re
                    nums = re.findall(r"[-+]?\d*\.\d+|\d+", b)
                    val = float(nums[0]) if nums else 0
                else:
                    val = float(b)
                if val > 1024 * 1024:
                    if val < 1024**3: return f"{val/1024**2:.1f} MB"
                    return f"{val/1024**3:.1f} GB"
                if val > 0: return f"{val:.1f} GB"
                return "0 B"
            except:
                return str(b)

        ui.add_head_html('''
            <style>
                .ping-card-base { border-width: 2px; border-style: solid; transition: all 0.3s; }
                .ping-card-inactive { border-color: transparent !important; opacity: 0.4; filter: grayscale(100%); }
            </style>
        ''')
        
        with ui.dialog() as d, ui.card().classes(f'p-0 overflow-hidden flex flex-col {DIALOG_BG} {SHADOW_STYLE}').style('width: 1000px; max-width: 95vw; border-radius: 12px;'):
            
            # --- 标题栏 ---
            with ui.row().classes(f'w-full items-center justify-between p-4 {CARD_BG} border-b border-white/50 dark:border-[#30363d] flex-shrink-0'):
                with ui.row().classes('items-center gap-3'):
                    flag = "🏳️"
                    try: flag = detect_country_group(server_conf['name'], server_conf).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl')
                    ui.label(server_conf['name']).classes(f'text-lg font-bold text-[#1e293b] dark:text-white')
                ui.button(icon='close', on_click=d.close).props('flat round dense color=grey-5')

            # --- 内容区 ---
            with ui.scroll_area().classes('w-full flex-grow p-6').style('height: 65vh;'):
                refs = {}
                
                # 第一行：左右分栏
                with ui.row().classes('w-full gap-6 no-wrap items-stretch'):
                    # 左侧：资源
                    with ui.column().classes(f'flex-1 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('资源使用情况').classes(SECTION_TITLE)
                        
                        def progress_block(label, key, icon, color_class):
                            with ui.column().classes('w-full gap-1'):
                                with ui.row().classes('w-full justify-between items-end'):
                                    with ui.row().classes('items-center gap-2'):
                                        ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-xs'); ui.label(label).classes(LABEL_STYLE)
                                    refs[f'{key}_pct'] = ui.label('0.0%').classes('text-gray-500 dark:text-gray-400 text-xs font-mono')
                                refs[f'{key}_bar'] = ui.linear_progress(value=0, show_value=False).props(f'color={color_class} track-color={TRACK_COLOR}').classes('h-1.5 rounded-full')
                                with ui.row().classes('w-full justify-end'):
                                    # ✨ 修改默认占位符，不再显示 "-- / --"
                                    refs[f'{key}_val'] = ui.label('--').classes('text-[11px] text-gray-500 font-mono mt-1')
                        
                        progress_block('CPU', 'cpu', 'settings_suggest', 'blue-5')
                        progress_block('RAM', 'mem', 'memory', 'green-5')
                        progress_block('DISK', 'disk', 'storage', 'purple-5')

                    # 右侧：系统
                    with ui.column().classes(f'w-[400px] p-5 rounded-xl {CARD_BG} {BORDER_STYLE} justify-between'):
                        ui.label('系统资讯').classes(SECTION_TITLE)
                        def info_line(label, icon, key):
                            with ui.row().classes('w-full items-center justify-between py-3 border-b border-white/50 dark:border-[#30363d] last:border-0'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.icon(icon).classes('text-gray-400 dark:text-gray-500 text-sm'); ui.label(label).classes(LABEL_STYLE)
                                refs[key] = ui.label('Loading...').classes(VALUE_STYLE)
                        info_line('作业系统', 'laptop_windows', 'os')
                        info_line('架构', 'developer_board', 'arch')
                        info_line('虚拟化', 'cloud_queue', 'virt')
                        info_line('在线时长', 'timer', 'uptime')

                # 第二行：延迟卡片
                with ui.row().classes('w-full gap-4 mt-6'):
                    def toggle_series(idx, card_el, color_cls):
                        visible_series[idx] = not visible_series[idx]
                        if visible_series[idx]:
                            card_el.classes(add=color_cls, remove='ping-card-inactive')
                        else:
                            card_el.classes(add='ping-card-inactive', remove=color_cls)

                    def ping_card(name, color, key, idx):
                        color_border_cls = f'border-{color}-500'
                        base_cls = f'flex-1 p-4 rounded-xl {CARD_BG} ping-card-base cursor-pointer {color_border_cls}'
                        with ui.element('div').classes(base_cls) as card:
                            card.on('click', lambda _, i=idx, c=card, col=color_border_cls: toggle_series(i, c, col))
                            with ui.row().classes('w-full justify-between items-center mb-1'):
                                ui.label(name).classes(f'text-{color}-500 text-xs font-bold')
                            with ui.row().classes('items-baseline gap-1'):
                                refs[f'{key}_cur'] = ui.label('--').classes(f'text-2xl font-black font-mono text-[#1e293b] dark:text-white')
                                ui.label('ms').classes('text-gray-500 text-[10px]')
                    ping_card('电信', 'blue', 'ping_ct', 0)
                    ping_card('联通', 'orange', 'ping_cu', 1)
                    ping_card('移动', 'green', 'ping_cm', 2)

                # 第三行：趋势图
                with ui.column().classes(f'w-full mt-6 p-5 rounded-xl {CARD_BG} {BORDER_STYLE} overflow-hidden'):
                    with ui.row().classes('w-full justify-between items-center mb-4'):
                        with ui.row().classes('items-center gap-4'):
                            ui.label('网络质量趋势').classes(f'text-sm font-bold text-[#1e293b] dark:text-gray-200')
                            switch_bg = 'bg-blue-50/50 dark:bg-[#0d1117]'
                            with ui.row().classes(f'items-center gap-2 cursor-pointer {switch_bg} px-3 py-1 rounded-full border border-white/50 dark:border-[#30363d]').on('click', lambda: smooth_sw.set_value(not smooth_sw.value)):
                                smooth_sw = ui.switch().props('dense size=sm color=blue')
                                ui.label('平滑曲线').classes('text-xs text-slate-500 dark:text-gray-400 select-none')
                                smooth_sw.on_value_change(lambda e: is_smooth.update({'value': e.value}))
                        tab_bg = 'bg-blue-50/50 dark:bg-[#0d1117]'
                        with ui.tabs().props('dense no-caps indicator-color=blue active-color=blue').classes(f'{tab_bg} rounded-lg p-1') as chart_tabs:
                            tab_cls = 'px-4 text-xs text-slate-500 dark:text-gray-400'
                            ui.tab('1h', label='1小时').classes(tab_cls)
                            ui.tab('3h', label='3小时').classes(tab_cls)
                            ui.tab('6h', label='6小时').classes(tab_cls)
                        chart_tabs.set_value('1h')

                    def calculate_ewma(data, alpha=0.3):
                        if not data: return []
                        result = [data[0]]
                        for i in range(1, len(data)):
                            result.append(alpha * data[i] + (1 - alpha) * result[-1])
                        return [int(x) for x in result]

                    chart_text = '#64748b' if not is_dark else '#94a3b8'
                    split_line = '#e2e8f0' if not is_dark else '#30363d'
                    tooltip_bg = 'rgba(255, 255, 255, 0.95)' if not is_dark else 'rgba(13, 17, 23, 0.95)'
                    tooltip_border = '#cbd5e1' if not is_dark else '#30363d'
                    tooltip_text = '#334155' if not is_dark else '#e6edf3'

                    chart = ui.echart({
                        'backgroundColor': 'transparent', 
                        'color': ['#3b82f6', '#f97316', '#22c55e'], 
                        'legend': { 'show': False },
                        'tooltip': {
                            'trigger': 'axis', 'backgroundColor': tooltip_bg, 'borderColor': tooltip_border, 'textStyle': {'color': tooltip_text},
                            'axisPointer': {'type': 'line', 'lineStyle': {'color': '#8b949e', 'type': 'dashed'}},
                            'formatter': '{b}<br/>{a0}: {c0}ms<br/>{a1}: {c1}ms<br/>{a2}: {c2}ms'
                        },
                        'dataZoom': [{'type': 'inside', 'xAxisIndex': 0, 'zoomLock': False}],
                        'grid': { 'left': '1%', 'right': '1%', 'bottom': '5%', 'top': '15%', 'containLabel': True },
                        'xAxis': { 'type': 'category', 'boundaryGap': False, 'axisLabel': { 'color': chart_text } },
                        'yAxis': { 'type': 'value', 'splitLine': { 'lineStyle': { 'color': split_line } }, 'axisLabel': { 'color': chart_text } },
                        'series': [
                            {'name': '电信', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '联通', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}},
                            {'name': '移动', 'type': 'line', 'smooth': True, 'showSymbol': False, 'data': [], 'areaStyle': {'opacity': 0.05}}
                        ]
                    }).classes('w-full h-64')

                async def update_dark_detail():
                    if not d.value: return
                    try:
                        status = await get_server_status(server_conf)
                        raw_cache = PROBE_DATA_CACHE.get(server_conf['url'], {})
                        static = raw_cache.get('static', {})

                        # ✨✨✨ CPU 更新逻辑：百分比 + 核心数 ✨✨✨
                        cpu_val = float(status.get('cpu_usage', 0))
                        refs['cpu_pct'].set_text(f"{cpu_val:.1f}%") 
                        refs['cpu_bar'].set_value(cpu_val / 100)
                        
                        # ✨ 核心修复：强制获取并显示核心数 (格式如 "2 C")
                        c_cores = status.get('cpu_cores')
                        if not c_cores:
                            c_cores = static.get('cpu_cores') # 备用：从静态缓存读取
                        
                        if c_cores:
                            refs['cpu_val'].set_text(f"{c_cores} C")
                        else:
                            refs['cpu_val'].set_text("--")

                        # ✨✨✨ 内存 百分比 + 容量 ✨✨✨
                        mem_p = float(status.get('mem_usage', 0))
                        refs['mem_pct'].set_text(f"{mem_p:.1f}%") 
                        refs['mem_bar'].set_value(mem_p / 100)
                        
                        mem_t_raw = status.get('mem_total', 0)
                        total_str = fmt_capacity(mem_t_raw)
                        used_str = "--"
                        if status.get('mem_used'):
                            used_str = fmt_capacity(status.get('mem_used'))
                        else:
                            # 估算已用
                            try:
                                val_t = float(re.findall(r"[-+]?\d*\.\d+|\d+", str(mem_t_raw))[0]) if isinstance(mem_t_raw, str) else float(mem_t_raw)
                                numeric_used = val_t * (mem_p / 100.0)
                                used_str = fmt_capacity(numeric_used)
                            except: pass
                        refs['mem_val'].set_text(f"{used_str} / {total_str}")

                        # ✨✨✨ 硬盘 百分比 + 容量 ✨✨✨
                        disk_p = float(status.get('disk_usage', 0))
                        refs['disk_pct'].set_text(f"{disk_p:.1f}%")
                        refs['disk_bar'].set_value(disk_p / 100)
                        
                        disk_t_raw = status.get('disk_total', 0)
                        disk_total_str = fmt_capacity(disk_t_raw)
                        disk_used_str = "--"
                        if status.get('disk_used'):
                            disk_used_str = fmt_capacity(status.get('disk_used'))
                        else:
                            # 估算已用
                            try:
                                val_d = float(re.findall(r"[-+]?\d*\.\d+|\d+", str(disk_t_raw))[0]) if isinstance(disk_t_raw, str) else float(disk_t_raw)
                                numeric_disk_used = val_d * (disk_p / 100.0)
                                disk_used_str = fmt_capacity(numeric_disk_used)
                            except: pass
                        refs['disk_val'].set_text(f"{disk_used_str} / {disk_total_str}")

                        # 系统信息
                        raw_arch = static.get('arch', '').lower()
                        display_arch = "AMD" if "x86" in raw_arch or "amd" in raw_arch else "ARM" if "arm" in raw_arch or "aarch" in raw_arch else raw_arch.upper()
                        refs['os'].set_text(static.get('os', 'Linux')); refs['arch'].set_text(display_arch); refs['virt'].set_text(static.get('virt', 'kvm'))
                        
                        uptime_str = str(status.get('uptime', '-')).replace('up ', '').replace('days', '天').replace('hours', '时').replace('minutes', '分')
                        refs['uptime'].set_text(uptime_str); refs['uptime'].classes('text-green-500')

                        # 延迟
                        pings = status.get('pings', {})
                        refs['ping_ct_cur'].set_text(str(pings.get('电信', 'N/A')))
                        refs['ping_cu_cur'].set_text(str(pings.get('联通', 'N/A')))
                        refs['ping_cm_cur'].set_text(str(pings.get('移动', 'N/A')))

                        # 图表
                        history_data = PING_TREND_CACHE.get(server_conf['url'], [])
                        if history_data:
                            import time
                            current_mode = chart_tabs.value
                            duration = 3600
                            if current_mode == '3h': duration = 10800
                            elif current_mode == '6h': duration = 21600 
                            
                            cutoff = time.time() - duration
                            sliced = [p for p in history_data if p['ts'] > cutoff]
                            if sliced:
                                raw_ct = [p['ct'] for p in sliced]
                                raw_cu = [p['cu'] for p in sliced]
                                raw_cm = [p['cm'] for p in sliced]
                                times = [p['time_str'] for p in sliced]
                                
                                final_ct = calculate_ewma(raw_ct) if is_smooth['value'] else raw_ct
                                final_cu = calculate_ewma(raw_cu) if is_smooth['value'] else raw_cu
                                final_cm = calculate_ewma(raw_cm) if is_smooth['value'] else raw_cm
                                
                                chart.options['xAxis']['data'] = times
                                chart.options['series'][0]['data'] = final_ct if visible_series[0] else []
                                chart.options['series'][1]['data'] = final_cu if visible_series[1] else []
                                chart.options['series'][2]['data'] = final_cm if visible_series[2] else []
                                chart.update()
                    except: pass

                chart_tabs.on_value_change(update_dark_detail)

            # --- 底部 ---
            with ui.row().classes(f'w-full justify-center p-2 {CARD_BG} border-t border-white/50 dark:border-[#30363d]'):
                ui.label('Powered by X-Fusion Monitor').classes('text-[10px] text-gray-500 dark:text-gray-600 font-mono italic')

        d.open()
        asyncio.create_task(update_dark_detail())
        timer = ui.timer(2.0, update_dark_detail)
        d.on('hide', lambda: timer.cancel())
    except Exception as e:
        print(f"PC Detail Error: {e}")

# ================= 自动判断路由函数 =================
def open_dark_server_detail(server_conf):
    # 简单的 JS 判断：如果屏幕宽度 > 768px (iPad竖屏宽度)，认为是电脑，否则是手机
    ui.run_javascript(f'''
        if (window.innerWidth > 768) {{
            window.location.href = "javascript:void(0)"; // 占位
        }}
    ''')
    
    # 由于 NiceGUI 服务端渲染的特性，要在 Python 里即时知道客户端宽度比较困难。
    # 为了最稳妥，建议直接在调用处区分（例如 render_mobile_status_page 调用 mobile 版，render_desktop 调用 PC 版）。
    # 或者，我们利用一个折中方案：默认调用 PC 版，但在手机页面入口调用 Mobile 版。
    
    # 但为了方便您直接替换，这里做一个简单的假设：
    # 如果当前处于 Mobile 渲染函数中（render_mobile_status_page），直接调 mobile 版。
    # 否则默认调 PC 版。
    
    # ⚠️ 既然您有两个完全不同的渲染函数 (render_mobile_status_page 和 render_desktop_status_page)
    # 请手动去 render_mobile_status_page 里把调用改成 open_mobile_server_detail(s)
    # 去 render_desktop_status_page 里把调用改成 open_pc_server_detail(s)
    
    # 既然函数名没变，我就默认打开 PC 版 (因为您刚才是在 PC 调试)，
    # **请务必去您的 render_mobile_status_page 函数里，把调用的函数名改为 open_mobile_server_detail**
    open_pc_server_detail(server_conf)
        
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


# ================= 电脑端大屏辅助全局变量  =================        
import asyncio 
import traceback

# ================= 核心：/status 电脑端大屏显示 (最终完美版：分页+缓存回显+Win修复+详细地图悬浮窗) =================
async def render_desktop_status_page():
    global CURRENT_PROBE_TAB
    
    # 1. 启用 Dark Mode
    dark_mode = ui.dark_mode()
    if app.storage.user.get('is_dark') is None:
        app.storage.user['is_dark'] = True
    dark_mode.value = app.storage.user.get('is_dark')

    # 2. 资源注入
    ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>')
    ui.add_head_html('<link href="https://use.fontawesome.com/releases/v6.4.0/css/all.css" rel="stylesheet">')
    
    # ✨✨✨ [CSS 样式注入] 集成 Twemoji 字体修复 Win 系统国旗显示 ✨✨✨
    ui.add_head_html('''
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
        <style>
            @font-face {
                font-family: 'Twemoji Country Flags';
                src: url('https://cdn.jsdelivr.net/npm/country-flag-emoji-polyfill@0.1/dist/TwemojiCountryFlags.woff2') format('woff2');
                unicode-range: U+1F1E6-1F1FF, U+1F3F4, U+E0062-E007F;
            }
            body { 
                margin: 0; 
                font-family: "Twemoji Country Flags", "Noto Color Emoji", "Segoe UI Emoji", "Noto Sans SC", sans-serif; 
                transition: background-color 0.3s ease; 
            }
            body:not(.body--dark) { background: linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%); }
            body.body--dark { background-color: #0b1121; }
            .status-card { transition: all 0.3s ease; border-radius: 16px; }
            body:not(.body--dark) .status-card { background: rgba(255, 255, 255, 0.95); border: 1px solid rgba(255, 255, 255, 0.8); box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1); color: #1e293b; }
            body.body--dark .status-card { background: #1e293b; border: 1px solid rgba(255,255,255,0.05); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); color: #e2e8f0; }
            .status-card:hover { transform: translateY(-3px); }
            .offline-card { border-color: rgba(239, 68, 68, 0.6) !important; }
            body.body--dark .offline-card { background-image: repeating-linear-gradient(45deg, rgba(239, 68, 68, 0.05) 0px, rgba(239, 68, 68, 0.05) 10px, transparent 10px, transparent 20px) !important; }
            body:not(.body--dark) .offline-card { background: rgba(254, 226, 226, 0.95) !important; }
            .scrollbar-hide::-webkit-scrollbar { display: none; }
            .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            .prog-bar { transition: width 0.5s ease-out; }
            #public-map-container { contain: strict; transform: translateZ(0); will-change: transform; z-index: 0; }
        </style>
    ''')
    
    RENDERED_CARDS = {} 
    tab_container = None
    grid_container = None
    header_refs = {}
    pie_chart_ref = None
    pagination_ref = None 
    local_ui_version = GLOBAL_UI_VERSION
    
    # 状态管理
    page_state = {
        'page': 1,
        'group': 'ALL'
    }

    def get_probe_groups():
        groups_list = ['ALL']
        customs = ADMIN_CONFIG.get('probe_custom_groups', [])
        groups_list.extend(customs) 
        return groups_list
    
    def fmt_capacity(b):
        if b is None: return "0 B"
        try:
            if isinstance(b, str):
                import re
                nums = re.findall(r"[-+]?\d*\.\d+|\d+", b)
                val = float(nums[0]) if nums else 0
            else:
                val = float(b)
            if val > 1024 * 1024:
                if val < 1024**3: return f"{val/1024**2:.1f} MB"
                return f"{val/1024**3:.1f} GB"
            if val > 0: return f"{val:.1f} GB"
            return "0 B"
        except: return str(b)

    def fmt_traffic(b): return f"{round(b/1024**3, 1)}G" if b > 1024**3 else f"{int(b/1024**2)}M"
    def fmt_speed(b): return f"{int(b)} B" if b < 1024 else (f"{int(b/1024)} K" if b < 1024**2 else f"{int(b/1024**2)} M")

    try:
        chart_data, pie_data, region_count, region_stats_json, centroids_json = prepare_map_data()
    except Exception as e:
        chart_data = '{"cities": [], "flags": [], "regions": []}'
        pie_data = []; region_count = 0; region_stats_json = "{}"; centroids_json = "{}"

    # ================= UI 布局 =================
    with ui.element('div').classes('fixed top-0 left-0 w-full h-[35vh] min-h-[300px] max-h-[500px] z-0').style('z-index: 0; contain: size layout paint;'): 
        ui.html('<div id="public-map-container" style="width:100%; height:100%;"></div>', sanitize=False).classes('w-full h-full')

    with ui.column().classes('w-full h-screen p-0 gap-0 overflow-hidden flex flex-col absolute top-0 left-0 pointer-events-none'):
        with ui.element('div').classes('w-full h-[35vh] min-h-[300px] max-h-[500px] relative p-0 shrink-0 pointer-events-none'):
            with ui.row().classes('absolute top-6 left-8 right-8 z-50 justify-between items-start pointer-events-auto'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('public', color='blue').classes('text-3xl drop-shadow-[0_0_10px_rgba(59,130,246,0.8)]')
                        ui.label('X-Fusion Status').classes('text-2xl font-black text-slate-800 dark:text-white drop-shadow-md')
                    with ui.row().classes('gap-4 text-sm font-bold font-mono pl-1'):
                        with ui.row().classes('items-center gap-1'):
                            ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-[0_0_5px_rgba(34,197,94,0.8)]')
                            header_refs['online_count'] = ui.label('在线: --').classes('text-slate-600 dark:text-slate-300 drop-shadow-sm')
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('language').classes('text-blue-500 dark:text-blue-400 text-xs drop-shadow-sm')
                            header_refs['region_count'] = ui.label(f'分布区域: {region_count}').classes('text-slate-600 dark:text-slate-300 drop-shadow-sm')
                with ui.row().classes('items-center gap-2'):
                    def toggle_dark():
                        dark_mode.value = not dark_mode.value
                        app.storage.user['is_dark'] = dark_mode.value
                        if pie_chart_ref:
                            color = '#e2e8f0' if dark_mode.value else '#334155'
                            pie_chart_ref.options['legend']['textStyle']['color'] = color
                            pie_chart_ref.update()
                        ui.run_javascript(f'if(window.changeTheme) window.changeTheme({str(dark_mode.value).lower()});')
                    ui.button(icon='dark_mode', on_click=toggle_dark).props('flat round dense').classes('text-slate-700 dark:text-yellow-400 bg-white/50')
                    ui.button('后台管理', icon='login', on_click=lambda: ui.navigate.to('/login')).props('flat dense').classes('font-bold text-xs text-slate-700 dark:text-slate-300 bg-white/50 rounded px-2')
            with ui.element('div').classes('absolute left-4 bottom-4 z-40 pointer-events-auto'):
                text_color = '#e2e8f0' if dark_mode.value else '#334155'
                pie_chart_ref = ui.echart({'backgroundColor': 'transparent', 'tooltip': {'trigger': 'item'}, 'legend': {'bottom': '0%', 'left': 'center', 'icon': 'circle', 'itemGap': 15, 'textStyle': {'color': text_color, 'fontSize': 11}}, 'series': [{'type': 'pie', 'radius': ['35%', '60%'], 'center': ['50%', '35%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 4, 'borderColor': 'transparent', 'borderWidth': 2}, 'label': {'show': False}, 'emphasis': {'scale': True, 'scaleSize': 10, 'label': {'show': True, 'color': 'auto', 'fontWeight': 'bold'}, 'itemStyle': {'shadowBlur': 10, 'shadowOffsetX': 0, 'shadowColor': 'rgba(0, 0, 0, 0.5)'}}, 'data': pie_data}]}).classes('w-64 h-72')

        with ui.column().classes('w-full flex-grow relative gap-0 overflow-hidden flex flex-col bg-white/80 dark:bg-[#0f172a]/90 backdrop-blur-xl pointer-events-auto border-t border-white/10').style('z-index: 10; contain: content;'): 
            with ui.row().classes('w-full px-6 py-2 border-b border-gray-200/50 dark:border-gray-800 items-center shrink-0 justify-between'):
                with ui.element('div').classes('flex-grow overflow-x-auto whitespace-nowrap scrollbar-hide mr-4') as tab_container: pass 
                pagination_ref = ui.row().classes('items-center')

            with ui.scroll_area().classes('w-full flex-grow p-4 md:p-6'):
                grid_container = ui.grid().classes('w-full gap-4 md:gap-5 pb-20').style('grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))')

    # ================= 渲染逻辑 (含分页) =================
    
    def render_tabs():
        tab_container.clear()
        groups = get_probe_groups(); global CURRENT_PROBE_TAB 
        if CURRENT_PROBE_TAB not in groups: CURRENT_PROBE_TAB = 'ALL'
        page_state['group'] = CURRENT_PROBE_TAB
        
        with tab_container:
            with ui.tabs().props('dense no-caps align=left active-color=blue indicator-color=blue').classes('text-slate-600 dark:text-gray-500 bg-transparent') as tabs:
                ui.tab('ALL', label='全部').on('click', lambda: apply_filter('ALL'))
                for g in groups:
                    if g == 'ALL': continue
                    ui.tab(g).on('click', lambda _, g=g: apply_filter(g))
                tabs.set_value(CURRENT_PROBE_TAB)

    # ================= ✨✨✨ 优化后的卡片渲染与更新逻辑 ✨✨✨ =================

    # 1. 抽离出的通用 UI 更新函数 (用于：1.创建时立即回显缓存 2.定时任务更新)
    def update_card_ui(refs, status, static):
        if not status: return
        
        is_probe_online = (status.get('status') == 'online')
        
        if is_probe_online:
            refs['status_icon'].set_name('bolt'); refs['status_icon'].classes(replace='text-green-500', remove='text-gray-400 text-red-500 text-purple-400')
            refs['online_dot'].classes(replace='bg-green-500', remove='bg-gray-500 bg-red-500 bg-purple-500')
        else:
            if status.get('cpu_usage') is not None:
                refs['status_icon'].set_name('api'); refs['status_icon'].classes(replace='text-purple-400', remove='text-gray-400 text-red-500 text-green-500')
                refs['online_dot'].classes(replace='bg-purple-500', remove='bg-gray-500 bg-red-500 bg-green-500')
            else:
                refs['status_icon'].set_name('flash_off'); refs['status_icon'].classes(replace='text-red-500', remove='text-green-500 text-gray-400 text-purple-400')
                refs['online_dot'].classes(replace='bg-red-500', remove='bg-green-500 bg-orange-500 bg-purple-500')

        os_str = static.get('os', 'Linux')
        import re
        simple_os = re.sub(r' GNU/Linux', '', os_str, flags=re.I)
        refs['os_info'].set_text(f"{simple_os}")
        
        cores = status.get('cpu_cores')
        refs['summary_cores'].set_text(f"{cores} C" if cores else "N/A")
        refs['summary_ram'].set_text(fmt_capacity(status.get('mem_total', 0)))
        refs['summary_disk'].set_text(fmt_capacity(status.get('disk_total', 0)))
        
        refs['traf_up'].set_text(f"↑ {fmt_traffic(status.get('net_total_out', 0))}")
        refs['traf_down'].set_text(f"↓ {fmt_traffic(status.get('net_total_in', 0))}")

        cpu = float(status.get('cpu_usage', 0))
        refs['cpu_bar'].style(f'width: {cpu}%'); refs['cpu_pct'].set_text(f'{cpu:.1f}%')
        c_num = status.get('cpu_cores', 1); refs['cpu_sub'].set_text(f"{c_num} Cores")
        
        mem = float(status.get('mem_usage', 0))
        refs['mem_bar'].style(f'width: {mem}%'); refs['mem_pct'].set_text(f'{mem:.1f}%')
        mem_total = float(status.get('mem_total', 0))
        if mem_total > 0:
            mem_val_used = mem_total * (mem / 100.0)
            refs['mem_sub'].set_text(f"{fmt_capacity(mem_val_used)} / {fmt_capacity(mem_total)}")
        else: refs['mem_sub'].set_text(f"{mem:.1f}%")

        disk = float(status.get('disk_usage', 0))
        refs['disk_bar'].style(f'width: {disk}%'); refs['disk_pct'].set_text(f'{disk:.1f}%')
        disk_total = float(status.get('disk_total', 0))
        if disk_total > 0:
            disk_val_used = disk_total * (disk / 100.0)
            refs['disk_sub'].set_text(f"{fmt_capacity(disk_val_used)} / {fmt_capacity(disk_total)}")
        else: refs['disk_sub'].set_text(f"{disk:.1f}%")

        n_up = status.get('net_speed_out', 0); n_down = status.get('net_speed_in', 0)
        refs['net_up'].set_text(f"↑ {fmt_speed(n_up)}/s"); refs['net_down'].set_text(f"↓ {fmt_speed(n_down)}/s")

        up = str(status.get('uptime', '-'))
        colored_up = re.sub(r'(\d+)(\s*(?:days?|天))', r'<span class="text-green-500 font-bold text-sm">\1</span>\2', up, flags=re.IGNORECASE)
        refs['uptime'].set_content(colored_up)

    # 2. 自动更新循环 (混合策略：探针实时，API 节能)
    async def card_autoupdate_loop(url):
        # 获取服务器配置
        current_server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
        if not current_server: return

        # 判断是否安装了探针
        is_probe = current_server.get('probe_installed', False)

        # --- 阶段 1: 首次启动延迟 ---
        if is_probe:
            # 探针机器：只随机延迟 0.5~3秒，让它尽快显示
            await asyncio.sleep(random.uniform(0.5, 3.0))
        else:
            # X-UI API机器：随机延迟 4~60秒，彻底错峰
            await asyncio.sleep(random.uniform(4, 60.0))
        
        while True:
            # --- 基础检查 ---
            if url not in RENDERED_CARDS: break 
            if url not in [s['url'] for s in SERVERS_CACHE]: break
            
            item = RENDERED_CARDS.get(url)
            if not item: break 
            
            # 如果浏览器标签页切到了后台，停止刷新以省流
            if not item['card'].visible: 
                await asyncio.sleep(5.0) 
                continue 
            
            # --- 执行获取数据 ---
            # 重新获取最新的配置引用
            current_server = next((s for s in SERVERS_CACHE if s['url'] == url), None)
            
            if current_server:
                res = None
                try: 
                    # 获取状态
                    # 这里的 timeout 对于 API 请求很重要，对探针读取则是瞬时的
                    res = await asyncio.wait_for(get_server_status(current_server), timeout=5.0)
                except: res = None
                
                if res:
                    raw_cache = PROBE_DATA_CACHE.get(url, {})
                    static = raw_cache.get('static', {})
                    update_card_ui(item['refs'], res, static)
                    
                    is_online = (res.get('status') == 'online') or (res.get('cpu_usage') is not None)
                    if is_online: item['card'].classes(remove='offline-card')
                    else: item['card'].classes(add='offline-card')

            # --- 阶段 2: 下一轮刷新的等待时间 (核心差异) ---
            if is_probe:
                # 探针：保持 2~3 秒的实时刷新 (读内存不费资源)
                await asyncio.sleep(random.uniform(2.0, 3.0))
            else:
                # X-UI API：休眠 60 秒 (55~65随机) (省流节能)
                await asyncio.sleep(random.uniform(55.0, 65.0))

    # 3. 创建卡片 (✨✨✨ 创建时立即回显 ✨✨✨)
    def create_server_card(s):
        url = s['url']; refs = {}
        
        cached_data = PROBE_DATA_CACHE.get(url, {})
        initial_status = None
        if cached_data:
            initial_status = cached_data.copy()
            if 'pings' not in initial_status: initial_status['pings'] = {}
        
        with grid_container:
            with ui.card().classes('status-card w-full p-4 md:p-5 flex flex-col gap-2 md:gap-3 relative overflow-hidden group').style('contain: content;') as card:
                refs['card'] = card
                with ui.row().classes('w-full items-center mb-1 gap-2 flex-nowrap'):
                    flag = "🏳️"; 
                    try: flag = detect_country_group(s['name'], s).split(' ')[0]
                    except: pass
                    ui.label(flag).classes('text-2xl md:text-3xl flex-shrink-0 leading-none') 
                    ui.label(s['name']).classes('text-base md:text-lg font-bold text-slate-800 dark:text-gray-100 truncate flex-grow min-w-0 cursor-pointer hover:text-blue-500 transition leading-tight').on('click', lambda _, s=s: open_pc_server_detail(s))
                    refs['status_icon'] = ui.icon('bolt').props('size=32px').classes('text-gray-400 flex-shrink-0')
                with ui.row().classes('w-full justify-between items-center px-1 mb-2'):
                    with ui.row().classes('items-center gap-1.5'):
                        ui.icon('dns').classes('text-xs text-gray-400'); ui.label('OS').classes('text-xs text-slate-500 dark:text-gray-400 font-bold')
                    with ui.row().classes('items-center gap-1.5'):
                        refs['os_icon'] = ui.icon('computer').classes('text-xs text-slate-400'); refs['os_info'] = ui.label('Loading...').classes('text-xs font-mono font-bold text-slate-700 dark:text-gray-300 whitespace-nowrap')
                ui.separator().classes('mb-3 opacity-50 dark:opacity-30')
                with ui.row().classes('w-full justify-between px-1 mb-1 md:mb-2'):
                    label_cls = 'text-xs font-mono text-slate-500 dark:text-gray-400 font-bold'
                    with ui.row().classes('items-center gap-1'): ui.icon('grid_view').classes('text-blue-500 dark:text-blue-400 text-xs'); refs['summary_cores'] = ui.label('--').classes(label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('memory').classes('text-green-500 dark:text-green-400 text-xs'); refs['summary_ram'] = ui.label('--').classes(label_cls)
                    with ui.row().classes('items-center gap-1'): ui.icon('storage').classes('text-purple-500 dark:text-purple-400 text-xs'); refs['summary_disk'] = ui.label('--').classes(label_cls)
                with ui.column().classes('w-full gap-2 md:gap-3'):
                    def stat_row(label, color_cls, light_track_color):
                        with ui.column().classes('w-full gap-1'):
                            with ui.row().classes('w-full items-center justify-between'):
                                ui.label(label).classes('text-xs text-slate-500 dark:text-gray-500 font-bold w-8')
                                with ui.element('div').classes(f'flex-grow h-2 md:h-2.5 bg-{light_track_color} dark:bg-gray-700/50 rounded-full overflow-hidden mx-2 transition-colors'):
                                    bar = ui.element('div').classes(f'h-full {color_cls} prog-bar').style('width: 0%')
                                pct = ui.label('0%').classes('text-xs font-mono font-bold text-slate-700 dark:text-white w-8 text-right')
                            sub = ui.label('').classes('text-[10px] text-slate-400 dark:text-gray-500 font-mono text-right w-full pr-1')
                        return bar, pct, sub
                    refs['cpu_bar'], refs['cpu_pct'], refs['cpu_sub'] = stat_row('CPU', 'bg-blue-500', 'blue-100')
                    refs['mem_bar'], refs['mem_pct'], refs['mem_sub'] = stat_row('内存', 'bg-green-500', 'green-100')
                    refs['disk_bar'], refs['disk_pct'], refs['disk_sub'] = stat_row('硬盘', 'bg-purple-500', 'purple-100')
                ui.separator().classes('bg-slate-200 dark:bg-white/5 my-1')
                with ui.column().classes('w-full gap-1'):
                    label_sub_cls = 'text-xs text-slate-400 dark:text-gray-500'
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('网络').classes(label_sub_cls); 
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap'): refs['net_up'] = ui.label('↑ 0B').classes('text-xs text-orange-500 dark:text-orange-400 font-bold'); refs['net_down'] = ui.label('↓ 0B').classes('text-xs text-green-600 dark:text-green-400 font-bold')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('流量').classes(label_sub_cls)
                        with ui.row().classes('gap-2 font-mono whitespace-nowrap text-xs text-slate-600 dark:text-gray-300'): refs['traf_up'] = ui.label('↑ 0B'); refs['traf_down'] = ui.label('↓ 0B')
                    with ui.row().classes('w-full justify-between items-center no-wrap'):
                        ui.label('在线').classes(label_sub_cls)
                        with ui.row().classes('items-center gap-1'): refs['uptime'] = ui.html('--', sanitize=False).classes('text-xs font-mono text-slate-600 dark:text-gray-300 text-right'); refs['online_dot'] = ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-gray-400')

        # ✨✨✨ 立即应用缓存数据 (防止页面白屏闪烁) ✨✨✨
        if initial_status:
            static = cached_data.get('static', {})
            update_card_ui(refs, initial_status, static)
            is_cached_online = (initial_status.get('status') == 'online') or (initial_status.get('cpu_usage') is not None)
            if is_cached_online: card.classes(remove='offline-card')
            else: card.classes(add='offline-card')

        RENDERED_CARDS[url] = {'card': card, 'refs': refs, 'data': s}
        asyncio.create_task(card_autoupdate_loop(url))

    def apply_filter(group_name):
        global CURRENT_PROBE_TAB; CURRENT_PROBE_TAB = group_name
        page_state['group'] = group_name
        page_state['page'] = 1 
        render_grid_page()

    def change_page(new_page):
        page_state['page'] = new_page
        render_grid_page()

    # ================= ✨✨✨ 核心：分页渲染逻辑 ✨✨✨ =================
    def render_grid_page():
        grid_container.clear()
        pagination_ref.clear()
        RENDERED_CARDS.clear()

        group_name = page_state['group']
        filtered_servers = []
        try: sorted_all = sorted(SERVERS_CACHE, key=lambda x: x.get('name', ''))
        except: sorted_all = SERVERS_CACHE
        
        for s in sorted_all:
            if group_name == 'ALL' or (group_name in s.get('tags', [])):
                filtered_servers.append(s)

        PAGE_SIZE = 32
        total_items = len(filtered_servers)
        total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
        if page_state['page'] > total_pages: page_state['page'] = 1
        if page_state['page'] < 1: page_state['page'] = 1
        
        start_idx = (page_state['page'] - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = filtered_servers[start_idx:end_idx]

        if not current_page_items:
            with grid_container:
                ui.label('暂无服务器').classes('text-gray-500 dark:text-gray-400 col-span-full text-center mt-10')
        else:
            for s in current_page_items:
                create_server_card(s)

        if total_pages > 1:
            with pagination_ref:
                # ✨✨✨ 修改：max-pages=7 ✨✨✨
                p = ui.pagination(1, total_pages, direction_links=True).props('dense color=blue outline rounded text-color=white active-color=blue active-text-color=white max-pages=7')
                p.value = page_state['page']
                p.on('update:model-value', lambda e: change_page(e.args))
                ui.label(f'共 {total_items} 台').classes('text-xs text-gray-400 ml-4 self-center')

    render_tabs()
    render_grid_page()
    
    # ✨✨✨ [JS 逻辑注入] 地图渲染 + 修复字体样式 + 调整悬浮窗宽度 ✨✨✨
    ui.run_javascript(f'''
    (function() {{
        var mapData = {chart_data}; 
        window.regionStats = {region_stats_json}; 
        window.countryCentroids = {centroids_json}; 
        
        var defaultPt = [116.40, 39.90]; 
        var defaultZoom = 1.35; 
        var focusedZoom = 4.0; 
        var isZoomed = false; 
        var myChart = null;

        function tryIpLocation() {{
            fetch('https://ipapi.co/json/')
                .then(response => response.json())
                .then(data => {{
                    if(data.latitude && data.longitude) {{
                        console.log("Using HTTPS IP Location:", data.latitude, data.longitude);
                        defaultPt = [data.longitude, data.latitude];
                        if(!isZoomed && myChart) renderMap();
                    }}
                }})
                .catch(e => {{}});
        }}

        function checkAndRender() {{
            var chartDom = document.getElementById('public-map-container');
            if (!chartDom || typeof echarts === 'undefined') {{ setTimeout(checkAndRender, 100); return; }}
            
            fetch('/static/world.json').then(r => r.json()).then(w => {{
                echarts.registerMap('world', w); 
                myChart = echarts.init(chartDom); 
                window.publicMapChart = myChart; 
                
                if (navigator.geolocation) {{ 
                    navigator.geolocation.getCurrentPosition(
                        p => {{ 
                            defaultPt = [p.coords.longitude, p.coords.latitude]; 
                            if(!isZoomed) renderMap(); 
                        }},
                        e => {{ tryIpLocation(); }}
                    ); 
                }} else {{ tryIpLocation(); }}
                
                renderMap();
                
                function renderMap(center, zoomLevel, roamState) {{
                    var viewCenter = center || defaultPt;
                    var viewZoom = zoomLevel || defaultZoom;
                    var viewRoam = roamState !== undefined ? roamState : false;
                    var mapLeft = isZoomed ? 'center' : '55%'; 
                    var mapTop = '1%';

                    var lines = mapData.cities.map(pt => ({{ coords: [pt.value, defaultPt] }}));
                    
                    var isDark = document.body.classList.contains('body--dark');
                    var areaColor = isDark ? '#1B2631' : '#e0e7ff'; 
                    var borderColor = isDark ? '#404a59' : '#a5b4fc'; 
                    
                    // 双色主题定义
                    var ttBg = isDark ? 'rgba(23, 23, 23, 0.95)' : 'rgba(255, 255, 255, 0.95)'; // 更深的黑色背景
                    var ttTextMain = isDark ? '#fff' : '#1e293b';
                    var ttTextSub = isDark ? 'rgba(255, 255, 255, 0.6)' : 'rgba(30, 41, 59, 0.6)';
                    var ttBorder = isDark ? '1px solid rgba(255,255,255,0.1)' : '1px solid #e2e8f0';

                    // ✅ 字体优化：优先使用 Google Noto Sans SC (黑体)，确保中文显示现代、清晰
                    var emojiFont = "'Twemoji Country Flags', 'Noto Sans SC', 'Roboto', 'Helvetica Neue', 'Arial', sans-serif";

                    var option = {{
                        backgroundColor: 'transparent',
                        tooltip: {{
                            show: true, trigger: 'item', padding: 0, backgroundColor: 'transparent', borderColor: 'transparent',
                            formatter: function(params) {{
                                var searchKey = params.name;
                                if (params.data && params.data.country_key) searchKey = params.data.country_key;
                                var stats = window.regionStats[searchKey];
                                if (!stats) return;
                                
                                var serverListHtml = '';
                                var displayLimit = 5; 
                                var servers = stats.servers || []; 
                                
                                for (var i = 0; i < Math.min(servers.length, displayLimit); i++) {{
                                    var s = servers[i];
                                    var isOnline = s.status === 'online';
                                    var statusColor = isOnline ? '#22c55e' : '#ef4444'; 
                                    var statusText = isOnline ? '在线' : '离线';
                                    
                                    // 列表项样式调整：行高紧凑，字体清晰
                                    serverListHtml += `
                                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; line-height: 1.2;">
                                            <div style="display: flex; align-items: center; max-width: 170px;">
                                                <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background-color: ${{statusColor}}; margin-right: 8px; flex-shrink: 0;"></span>
                                                <span style="font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${{s.name}}</span>
                                            </div>
                                            <span style="font-size: 12px; color: ${{ttTextSub}}; flex-shrink: 0; margin-left: 8px;">${{statusText}}</span>
                                        </div>
                                    `;
                                }}
                                
                                if (servers.length > displayLimit) {{
                                    serverListHtml += `<div style="font-size: 11px; color: ${{ttTextSub}}; margin-top: 8px; text-align: right; opacity: 0.8;">+${{servers.length - displayLimit}} 更多...</div>`;
                                }}
                                
                                // ✅ 外框样式调整：宽度缩窄，圆角适中
                                return `<div style="background:${{ttBg}}; border:${{ttBorder}}; padding: 14px 16px; border-radius: 10px; color:${{ttTextMain}}; font-family: ${{emojiFont}}; box-shadow: 0 4px 16px rgba(0,0,0,0.3); min-width: 240px; max-width: 260px;">
                                    <div style="font-size: 16px; font-weight: 700; margin-bottom: 2px; display: flex; align-items: center; letter-spacing: 0.5px;">
                                        <span style="margin-right: 8px; font-size: 20px;">${{stats.flag}}</span>${{stats.cn}}
                                    </div>
                                    <div style="font-size: 12px; color: ${{ttTextSub}}; margin-bottom: 12px; font-weight: 400;">
                                        共 ${{stats.total}} 台服务器, ${{stats.online}} 台在线
                                    </div>
                                    <div style="border-top: 1px solid ${{isDark ? 'rgba(255,255,255,0.08)' : '#f1f5f9'}}; padding-top: 10px; margin-top: 4px;">
                                        ${{serverListHtml}}
                                    </div>
                                </div>`;
                            }}
                        }},
                        geo: {{
                            map: 'world', left: mapLeft, top: mapTop, roam: viewRoam, zoom: viewZoom, center: viewCenter,
                            aspectScale: 0.85, label: {{ show: false }},
                            itemStyle: {{ areaColor: areaColor, borderColor: borderColor, borderWidth: 1 }},
                            emphasis: {{ itemStyle: {{ areaColor: isDark ? '#1e3a8a' : '#bfdbfe' }} }}
                        }},
                        series: [
                            {{ type: 'lines', zlevel: 2, effect: {{ show: true, period: 4, trailLength: 0.5, color: '#00ffff', symbol: 'arrow', symbolSize: 6 }}, lineStyle: {{ color: '#00ffff', width: 0, curveness: 0.2, opacity: 0 }}, data: lines, silent: true }},
                            {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 3, rippleEffect: {{ brushType: 'stroke', scale: 2.5 }}, itemStyle: {{ color: '#00ffff' }}, data: mapData.cities }},
                            
                            {{ 
                                type: 'scatter', coordinateSystem: 'geo', zlevel: 6, symbolSize: 0, 
                                label: {{ 
                                    show: true, position: 'top', formatter: '{{b}}', 
                                    color: isDark?'#fff':'#1e293b', fontSize: 16, offset: [0, -5],
                                    fontFamily: emojiFont 
                                }}, 
                                data: mapData.flags 
                            }},
                            
                            {{ type: 'effectScatter', coordinateSystem: 'geo', zlevel: 5, itemStyle: {{ color: '#f59e0b' }}, label: {{ show: true, position: 'bottom', formatter: 'My PC', color: '#f59e0b', fontWeight: 'bold' }}, data: [{{ value: defaultPt }}] }}
                        ]
                    }};
                    myChart.setOption(option, true);
                }}
                
                window.updatePublicMap = function(newData) {{ 
                    if (!newData) return; mapData = newData; 
                    renderMap(isZoomed ? myChart.getOption().geo[0].center : defaultPt, isZoomed ? myChart.getOption().geo[0].zoom : defaultZoom, isZoomed ? 'move' : false); 
                }};
                
                myChart.on('click', function(params) {{
                    var searchKey = params.name;
                    if (params.data && params.data.country_key) searchKey = params.data.country_key;
                    var targetCoord = window.countryCentroids[searchKey];
                    if (targetCoord) {{ isZoomed = true; renderMap(targetCoord, focusedZoom, 'move'); }}
                }});
                
                myChart.getZr().on('mousewheel', function() {{ if(isZoomed) {{ isZoomed = false; renderMap(defaultPt, defaultZoom, false); }} }});
                window.changeTheme = function(isDark) {{ renderMap(undefined, undefined, undefined); }}; 
                window.addEventListener('resize', () => myChart.resize());
            }});
        }}
        checkAndRender();
    }})();
    ''')
    async def loop_update():
        nonlocal local_ui_version
        try:
            if GLOBAL_UI_VERSION != local_ui_version:
                local_ui_version = GLOBAL_UI_VERSION
                render_tabs(); render_grid_page() 
                try: new_map, _, new_cnt, new_stats, new_centroids = prepare_map_data()
                except: new_map = "{}"; new_cnt = 0; new_stats = "{}"; new_centroids = "{}"
                if header_refs.get('region_count'): header_refs['region_count'].set_text(f'分布区域: {new_cnt}')
                ui.run_javascript(f'''if(window.updatePublicMap){{ window.regionStats = {new_stats}; window.countryCentroids = {new_centroids}; window.updatePublicMap({new_map}); }}''')
            real_online_count = len([s for s in SERVERS_CACHE if s.get('_status') == 'online'])
            if header_refs.get('online_count'): header_refs['online_count'].set_text(f'在线: {real_online_count}')
        except: pass
        ui.timer(5.0, loop_update, once=True)

    ui.timer(0.1, loop_update, once=True)
    
# ================= 手机端专用：实时动效 Dashboard 最终完整版 =================
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
                
                with ui.column().classes('mobile-card').on('click', lambda _, srv=s: open_mobile_server_detail(srv)):
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
                        # 修改点：左侧显示绿色加粗的在线时长
                        srv_ref['uptime'] = ui.label("在线时长：--").classes('text-[10px] font-bold text-green-500 font-mono')
                        with ui.row().classes('items-center gap-2'):
                            # 修改点：闪电图标引用 srv_ref['load']，动态展示 load_1 数据
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
            
            # Load 更新：显示实时负载数据
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
