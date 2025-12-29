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
import random
import pyotp
import qrcode
import io
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, quote
from nicegui import ui, run, app, Client
from fastapi import Response, Request
from fastapi.responses import RedirectResponse
from urllib.parse import urlparse, quote # <--- ✨ 修改这里，增加 quote

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

# ================= 配置区域 =================
CONFIG_FILE = 'data/servers.json'
SUBS_FILE = 'data/subscriptions.json'
NODES_CACHE_FILE = 'data/nodes_cache.json'
ADMIN_CONFIG_FILE = 'data/admin_config.json'

# ✨✨✨ 自动注册密钥 (优先从环境变量获取) ✨✨✨
AUTO_REGISTER_SECRET = os.getenv('XUI_SECRET_KEY', 'sijuly_secret_key_default')

ADMIN_USER = os.getenv('XUI_USERNAME', 'sijuly')
ADMIN_PASS = os.getenv('XUI_PASSWORD', '050148Sq$')

SERVERS_CACHE = []
SUBS_CACHE = []
NODES_DATA = {}
ADMIN_CONFIG = {}
# ================= 智能分组配置 (修复版) =================
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

def detect_country_group(name):
    name_upper = name.upper()
    for key, val in AUTO_COUNTRY_MAP.items():
        if key in name_upper:
            return val
    return '🏳️ 其他地区'

FILE_LOCK = asyncio.Lock()
EXPANDED_GROUPS = set()
SERVER_UI_MAP = {}
content_container = None




def init_data():
    if not os.path.exists('data'): os.makedirs('data')
    global SERVERS_CACHE, SUBS_CACHE, NODES_DATA, ADMIN_CONFIG
    logger.info(f"正在初始化数据... (当前登录账号: {ADMIN_USER})")
    logger.info(f"通讯密钥已加载: {AUTO_REGISTER_SECRET[:4]}***")
    
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
            logger.info(f"✅ 加载节点缓存完毕")
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

async def save_servers(): await safe_save(CONFIG_FILE, SERVERS_CACHE)
async def save_subs(): await safe_save(SUBS_FILE, SUBS_CACHE)
async def save_admin_config(): await safe_save(ADMIN_CONFIG_FILE, ADMIN_CONFIG)
async def save_nodes_cache():
    try:
        data_snapshot = NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
    except: pass

init_data()
managers = {}

def safe_notify(message, type='info', timeout=3000):
    try: ui.notify(message, type=type, timeout=timeout)
    except: logger.info(f"[Notify] {message}")

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

# 辅助函数：后台线程执行
async def run_in_bg_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(BG_EXECUTOR, func, *args)

# [核心] 静默刷新逻辑
async def silent_refresh_all():
    safe_notify(f'🚀 开始后台静默刷新 ({len(SERVERS_CACHE)} 个服务器)...')
    tasks = []
    for srv in SERVERS_CACHE:
        tasks.append(fetch_inbounds_safe(srv, force_refresh=True))
    await asyncio.gather(*tasks, return_exceptions=True)
    safe_notify('✅ 后台刷新完成', 'positive')
    render_sidebar_content.refresh()

async def fetch_inbounds_safe(server_conf, force_refresh=False):
    url = server_conf['url']
    name = server_conf.get('name', '未命名')
    
    if not force_refresh and url in NODES_DATA: return NODES_DATA[url]
    
    async with SYNC_SEMAPHORE:
        logger.info(f"🔄 同步: [{name}] ...")
        try:
            mgr = get_manager(server_conf)
            inbounds = await run_in_bg_executor(mgr.get_inbounds)
            if inbounds is None:
                mgr = managers[server_conf['url']] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'], server_conf.get('prefix')) 
                inbounds = await run_in_bg_executor(mgr.get_inbounds)
            
            if inbounds is not None:
                NODES_DATA[url] = inbounds
                await save_nodes_cache()
                return inbounds
            
            logger.error(f"❌ [{name}] 连接失败")
            NODES_DATA[url] = [] 
            await save_nodes_cache()
            return []
        except Exception as e: 
            logger.error(f"❌ [{name}] 异常: {e}")
            NODES_DATA[url] = []
            return []

# ================= [修改] 使用 URL 安全的 Base64 =================
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

# ================= [新增] 生成 SubConverter 转换链接 =================
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

# ================= 新增：生成 Surge/Loon 格式明文配置 =================
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

# ================= 新增：延迟测试核心逻辑 =================
import subprocess
import platform

# 缓存延迟结果 { 'host:port': {'ping': 120, 'time': 12345678} }
PING_CACHE = {}

async def ping_host(host, port):
    """
    对指定 Host 进行 TCP Ping (更准确反映节点连通性)
    如果 host 是域名，会先解析 IP；如果 ping 失败返回 -1
    """
    key = f"{host}:{port}"
    
    # 简单的 ICMP Ping 实现 (兼容 Linux/Windows)
    # 注意：更严格的节点检测应该用 TCP Ping (连接端口)，这里为了通用性先用 ICMP
    # 如果你的服务器是在 Docker 里，确保容器安装了 iputils-ping (apt update && apt install -iputils-ping)
    
    # 更好的方式：使用 asyncio 打开 TCP 连接测试握手时间
    try:
        start_time = asyncio.get_running_loop().time()
        try:
            # 尝试建立 TCP 连接 (超时 2秒)
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), 
                timeout=2.0
            )
            writer.close()
            await writer.wait_closed()
            
            end_time = asyncio.get_running_loop().time()
            latency = int((end_time - start_time) * 1000) # 毫秒
            PING_CACHE[key] = latency
            return latency
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            PING_CACHE[key] = -1
            return -1
    except:
        return -1

# 批量测试函数
async def batch_ping_nodes(nodes, raw_host):
    tasks = []
    for n in nodes:
        # 获取节点真实地址
        add = n.get('listen')
        if not add or add == '0.0.0.0': 
            add = raw_host # 回退到服务器地址
        
        port = n.get('port')
        tasks.append(ping_host(add, port))
    
    # 并发执行所有 Ping
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

# ================= [修改] 分组订阅接口：支持 Tag 和 主分组 =================
@app.get('/sub/group/{group_b64}')
async def group_sub_handler(group_b64: str, request: Request):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)
    
    links = []
    
    # ✨✨✨ 核心修复：同时筛选“主分组”和“Tags” ✨✨✨
    # 之前的代码只筛选了 s.get('group')，导致自定义分组（Tag）无法匹配
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

# ================= [修改] 短链接接口：分组 =================
@app.get('/get/group/{target}/{group_b64}')
async def short_group_handler(target: str, group_b64: str):
    try:
        # ✨✨✨ 重点修复：必须用横杠 xui-manager，不能用下划线 ✨✨✨
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

# ================= [修改] 短链接接口：单个订阅 =================
@app.get('/get/sub/{target}/{token}')
async def short_sub_handler(target: str, token: str):
    try:
        # ✨✨✨ 重点修复：必须用横杠 xui-manager ✨✨✨
        internal_api = f"http://xui-manager:8080/sub/{token}"

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
            return Response(f"Backend Error: {code}", status_code=502)
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
            'group': '自动注册',
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

# ================= [修改] 支持格式转换的分组复制 =================
async def copy_group_link(group_name, target=None):
    try:
        origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
        if not origin: origin = "https://xui-manager.sijuly.nyc.mn"
        encoded_name = safe_base64(group_name)
        
        if target:
            # ✨ 修改：路径变为 /get/group/...
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


# ================= [新增] 带二次确认的删除逻辑 =================
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

class SubEditor:
    def __init__(self, data=None):
        self.data = data
        self.d = data.copy() if data else {'name':'','token':str(uuid.uuid4()),'nodes':[]}
        self.sel = set(self.d['nodes'])
        self.groups_data = {} 
        self.all_node_keys = set()
        self.name_input = None # ✨ 新增：用于引用输入框控件

    def ui(self, dlg):
        # 外层卡片：强制 flex-column (保留你验证过的布局)
        with ui.card().classes('w-[90vw] max-w-4xl p-0 bg-white').style('display: flex; flex-direction: column; height: 85vh;'):
            
            # 1. 标题栏
            with ui.row().classes('w-full justify-between items-center p-4 border-b bg-gray-50'):
                ui.label('订阅编辑器').classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense')
            
            # 2. 滚动区域：强制 block 或者 flex-column
            with ui.element('div').classes('w-full flex-grow overflow-y-auto p-4').style('display: flex; flex-direction: column; gap: 1rem;'):
                
                # ✨ 修复点 1：绑定输入事件
                # 将输入框赋值给 self.name_input，并添加 on_value_change
                self.name_input = ui.input('订阅名称', value=self.d['name']).classes('w-full').props('outlined')
                self.name_input.on_value_change(lambda e: self.d.update({'name': e.value}))
                
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
                    # ✨ 修复点 2：保存前强制读取输入框当前值 (防止事件延迟)
                    if self.name_input:
                        self.d['name'] = self.name_input.value
                        
                    self.d['nodes'] = list(self.sel)
                    if self.data: 
                        for i, s in enumerate(SUBS_CACHE):
                            if s['token'] == self.data['token']: SUBS_CACHE[i] = self.d
                    else: 
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
                # 一级：分组
                with ui.expansion(g_name, icon='folder', value=True).classes('w-full border rounded mb-2').style('width: 100%;'):
                    
                    # 二级：垂直容器
                    with ui.column().classes('w-full p-0').style('display: flex; flex-direction: column; width: 100%;'):
                        
                        servers = self.groups_data[g_name]
                        for item in servers:
                            srv = item['server']
                            nodes = item['nodes']
                            
                            # 三级：服务器块
                            with ui.column().classes('w-full p-2 border-b').style('display: flex; flex-direction: column; align-items: flex-start; width: 100%;'):
                                
                                # 服务器名
                                with ui.row().classes('items-center gap-2 mb-2'):
                                    ui.icon('dns', size='xs')
                                    ui.label(srv['name']).classes('font-bold')
                                
                                # 四级：节点列表
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

# ================= [修改] 订阅管理视图 (增加转换按钮) =================
async def load_subs_view():
    show_loading(content_container)
    try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
    except: origin = ""
    if not origin: origin = "https://xui-manager.sijuly.nyc.mn"

    content_container.clear()
    with content_container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4')
        with ui.row().classes('w-full mb-4 justify-end'): ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_sub_editor(None))
        
        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes('w-full p-4 mb-2 shadow-sm hover:shadow-md transition border-l-4 border-blue-500'):
                with ui.row().classes('justify-between w-full items-center'):
                    with ui.column().classes('gap-1'):
                        ui.label(sub['name']).classes('font-bold text-lg text-slate-800')
                        ui.label(f"包含 {len(sub.get('nodes',[]))} 个节点").classes('text-xs text-gray-500')
                    
                    with ui.row().classes('gap-2'):
                        ui.button(icon='edit', on_click=lambda s=sub: open_sub_editor(s)).props('flat dense color=blue')
                        async def dl(i=idx): del SUBS_CACHE[i]; await save_subs(); await load_subs_view()
                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense')

                ui.separator().classes('my-2')
                
                path = f"/sub/{sub['token']}"
                raw_url = f"{origin}{path}"
                
                with ui.row().classes('w-full items-center gap-2 bg-gray-50 p-2 rounded justify-between'):
                    with ui.row().classes('items-center gap-2 flex-grow overflow-hidden'):
                        ui.icon('link').classes('text-gray-400')
                        ui.label(raw_url).classes('text-xs font-mono text-gray-600 truncate')
                    
                    with ui.row().classes('gap-1'):
                        # 原始
                        ui.button(icon='content_copy', on_click=lambda u=raw_url: safe_copy_to_clipboard(u)).props('flat dense round size=sm color=grey').tooltip('复制原始链接')
                        
                        # ✨✨✨ 修改：使用短链接接口 /get/sub/surge/{token} ✨✨✨
                        surge_short = f"{origin}/get/sub/surge/{sub['token']}"
                        ui.button(icon='bolt', on_click=lambda u=surge_short: safe_copy_to_clipboard(u)).props('flat dense round size=sm text-color=orange').tooltip('复制 Surge 订阅')
                        
                        clash_short = f"{origin}/get/sub/clash/{sub['token']}"
                        ui.button(icon='cloud_queue', on_click=lambda u=clash_short: safe_copy_to_clipboard(u)).props('flat dense round size=sm text-color=green').tooltip('复制 Clash 订阅')
                        
async def open_add_server_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('添加服务器').classes('text-lg font-bold')
        n = ui.input('名称').classes('w-full'); g = ui.select(options=get_all_groups(), label='分组', value='默认分组').classes('w-full')
        u = ui.input('URL').classes('w-full'); us = ui.input('账号').classes('w-full')
        p = ui.input('密码', password=True).classes('w-full'); pre = ui.input('API前缀', placeholder='/xui').classes('w-full')
        async def save():
            SERVERS_CACHE.append({'name':n.value,'group':g.value,'url':u.value,'user':us.value,'pass':p.value,'prefix':pre.value})
            await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('SINGLE', SERVERS_CACHE[-1], force_refresh=True)
        ui.button('保存', on_click=save).classes('w-full bg-green-600 text-white')
    d.open()

async def open_edit_server_dialog(idx):
    data = SERVERS_CACHE[idx]
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('编辑配置').classes('text-lg font-bold')
        n = ui.input('名称', value=data['name']).classes('w-full')
        g = ui.select(options=get_all_groups(), label='分组', value=data.get('group', '默认分组')).classes('w-full')
        u = ui.input('URL', value=data['url']).classes('w-full'); us = ui.input('账号', value=data['user']).classes('w-full')
        p = ui.input('密码', value=data['pass'], password=True).classes('w-full'); pre = ui.input('API前缀', value=data.get('prefix','')).classes('w-full')
        async def save():
            SERVERS_CACHE[idx] = {'name':n.value,'group':g.value,'url':u.value,'user':us.value,'pass':p.value,'prefix':pre.value}
            await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('SINGLE', SERVERS_CACHE[idx], force_refresh=True)
        async def delete():
            deleted_url = SERVERS_CACHE[idx]['url']
            del SERVERS_CACHE[idx]
            await save_servers()
            render_sidebar_content.refresh()
            if deleted_url in SERVER_UI_MAP:
                try: SERVER_UI_MAP[deleted_url].delete(); del SERVER_UI_MAP[deleted_url]
                except: await refresh_content('ALL')
            else: await refresh_content('ALL')
            d.close()
        with ui.column().classes('w-full gap-2 mt-2'):
            ui.button('保存', on_click=save).classes('w-full bg-primary text-white')
            ui.button('删除', on_click=delete).classes('w-full bg-red-100 text-red-600')
    d.open()

def open_group_mgmt_dialog(group_name):
    # 只用于管理自定义分组 (Tags)
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-[500px] flex flex-col p-0 gap-0 overflow-hidden'):
        with ui.row().classes('w-full justify-between items-center p-4 bg-gray-50 border-b'):
            ui.label(f'管理分组: {group_name}').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')

        with ui.column().classes('w-full p-4 gap-4'):
            new_name_inp = ui.input('分组名称', value=group_name).classes('w-full').props('outlined')
            ui.label('包含的服务器 (多选):').classes('text-sm font-bold text-gray-500 mt-2')
            
            scroll_area = ui.column().classes('w-full flex-grow overflow-y-auto border rounded p-2 gap-1 h-[40vh]')
            
            # 这里的逻辑纯粹是：Tag 有没有打上
            current_sel_urls = set()
            for s in SERVERS_CACHE:
                if group_name in s.get('tags', []):
                    current_sel_urls.add(s['url'])
            
            # 列表显示时，加上自动计算的国家前缀，方便识别
            sorted_servers = sorted(SERVERS_CACHE, key=lambda x: x['name'])
            
            with scroll_area:
                for s in sorted_servers:
                    # 显示：[🇬🇧 英国] 微软云...
                    country = detect_country_group(s['name'])
                    label_text = f"[{country}] {s['name']}"
                    
                    def toggle(e, u=s['url']): 
                        if e.value: current_sel_urls.add(u)
                        else: current_sel_urls.discard(u)
                    ui.checkbox(label_text, value=(s['url'] in current_sel_urls), on_change=toggle).classes('w-full text-sm dense').style('margin-left: 0;')

        with ui.row().classes('w-full p-4 border-t gap-4 justify-end'):
            async def delete_this_group():
                with ui.dialog() as confirm_d, ui.card():
                    ui.label(f'删除 "{group_name}" ?').classes('text-lg font-bold')
                    with ui.row().classes('w-full justify-end mt-4'):
                        ui.button('取消', on_click=confirm_d.close).props('flat')
                        async def real_delete():
                            if 'custom_groups' in ADMIN_CONFIG:
                                if group_name in ADMIN_CONFIG['custom_groups']:
                                    ADMIN_CONFIG['custom_groups'].remove(group_name)
                                await save_admin_config()
                            for s in SERVERS_CACHE:
                                if group_name in s.get('tags', []):
                                    s['tags'].remove(group_name)
                            await save_servers()
                            confirm_d.close(); d.close()
                            render_sidebar_content.refresh()
                            if content_container: content_container.clear() # 清空右侧
                            safe_notify(f'分组已删除', 'positive')
                        ui.button('确定', color='red', on_click=real_delete)
                confirm_d.open()

            ui.button('删除分组', on_click=delete_this_group, color='red').props('flat').classes('mr-auto')

            async def save():
                target_name = new_name_inp.value.strip()
                if not target_name: return
                
                # 1. 更新配置列表
                if group_name != target_name:
                    if 'custom_groups' in ADMIN_CONFIG:
                        if group_name in ADMIN_CONFIG['custom_groups']:
                            idx = ADMIN_CONFIG['custom_groups'].index(group_name)
                            ADMIN_CONFIG['custom_groups'][idx] = target_name
                        else:
                            ADMIN_CONFIG['custom_groups'].append(target_name)
                    await save_admin_config()

                # 2. 更新所有服务器的 Tag
                for s in SERVERS_CACHE:
                    if 'tags' not in s: s['tags'] = []
                    
                    if s['url'] in current_sel_urls:
                        # 选中：确保有新 tag，移除旧 tag
                        if target_name not in s['tags']: s['tags'].append(target_name)
                        if group_name != target_name and group_name in s['tags']: s['tags'].remove(group_name)
                    else:
                        # 未选中：移除 tag
                        if target_name in s['tags']: s['tags'].remove(target_name)
                        if group_name in s['tags']: s['tags'].remove(group_name)

                await save_servers()
                d.close()
                render_sidebar_content.refresh()
                await refresh_content('TAG', target_name) # 刷新右侧视图
                safe_notify('分组已保存', 'positive')

            ui.button('保存修改', on_click=save).classes('bg-slate-900 text-white')
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

async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-2xl max-h-[90vh] flex flex-col gap-0 p-0 overflow-hidden'):
        with ui.tabs().classes('w-full bg-gray-50 flex-shrink-0') as tabs:
            tab_export = ui.tab('导出')
            tab_import = ui.tab('导入')
        with ui.tab_panels(tabs, value=tab_export).classes('w-full p-6 overflow-y-auto flex-grow'):
            with ui.tab_panel(tab_export).classes('flex flex-col gap-4'):
                full_backup = {"version": "2.0", "servers": SERVERS_CACHE, "cache": NODES_DATA}
                json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)
                ui.textarea('备份内容', value=json_str).props('readonly').classes('w-full h-48 font-mono text-xs')
                ui.button('复制到剪贴板', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).classes('w-full bg-blue-600 text-white')
                ui.button('下载 .json', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_backup.json')).classes('w-full bg-green-600 text-white')
            with ui.tab_panel(tab_import).classes('flex flex-col gap-4 items-stretch'):
                ui.label('方式一：粘贴 JSON 内容').classes('font-bold')
                import_text = ui.textarea(placeholder='在此粘贴备份 JSON...').classes('w-full h-32 font-mono text-xs')
                import_cache_chk = ui.checkbox('恢复节点缓存', value=True).classes('text-sm')
                async def process_json_import():
                    try:
                        raw = import_text.value.strip()
                        if not raw: safe_notify("内容不能为空", 'warning'); return
                        data = json.loads(raw)
                        new_servers = data.get('servers', []) if isinstance(data, dict) else data
                        new_cache = data.get('cache', {}) if isinstance(data, dict) else {}
                        count = 0; existing = {s['url'] for s in SERVERS_CACHE}
                        for item in new_servers:
                            if item['url'] not in existing:
                                SERVERS_CACHE.append(item); existing.add(item['url']); count += 1
                        if import_cache_chk.value and new_cache: NODES_DATA.update(new_cache); await save_nodes_cache()
                        await save_servers(); render_sidebar_content.refresh(); safe_notify(f"已恢复 {count} 个服务器", 'positive'); d.close()
                    except Exception as e: safe_notify(f"JSON 格式错误: {e}", 'negative')
                
                ui.button('恢复数据', icon='restore', on_click=process_json_import).classes('w-full bg-green-600 text-white h-12')
                ui.separator().classes('my-2')
                async def open_url_import_sub_dialog():
                    with ui.dialog() as sub_d, ui.card().classes('w-full max-w-md flex flex-col gap-4 p-6'):
                        ui.label('批量添加 URL').classes('text-lg font-bold')
                        url_area = ui.textarea(placeholder='http://1.1.1.1:54321\nhttps://example.com').classes('w-full h-32 font-mono text-sm')
                        def_user = ui.input('默认账号', value='admin').classes('w-full')
                        def_pass = ui.input('默认密码', value='admin').classes('w-full')
                        async def run_url_import():
                            raw_text = url_area.value.strip()
                            if not raw_text: safe_notify("请输入内容", "warning"); return
                            raw_urls = re.findall(r'https?://[^\s,;"\'<>]+', raw_text)
                            if not raw_urls: raw_urls = re.findall(r'(?:[0-9]{1,3}\.){3}[0-9]{1,3}:\d+', raw_text)
                            if not raw_urls: safe_notify("未找到 URL", "warning"); return
                            count = 0; existing = {s['url'] for s in SERVERS_CACHE}
                            for u in raw_urls:
                                if '://' not in u: u = f'http://{u}'
                                if u not in existing:
                                    try: name = urlparse(u).hostname or u
                                    except: name = u
                                    SERVERS_CACHE.append({'name': name, 'group': '默认分组', 'url': u, 'user': def_user.value, 'pass': def_pass.value, 'prefix': ''})
                                    existing.add(u); count += 1
                            if count > 0: await save_servers(); render_sidebar_content.refresh(); safe_notify(f"添加了 {count} 个服务器", 'positive'); sub_d.close(); d.close()
                            else: safe_notify("没有添加新服务器", 'warning')
                        ui.button('确认添加', on_click=run_url_import).classes('w-full bg-blue-600 text-white')
                    sub_d.open()
                ui.button('方式二：批量 URL 导入', on_click=open_url_import_sub_dialog).props('outline').classes('w-full text-blue-600 h-12')
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

# ================= [修改] 表格布局定义 (定义两种模式) =================

# 1. 带延迟 (用于：区域分组、单个服务器) - 包含 90px 的延迟列
# 格式: 服务器(150) 备注(200) 分组(1fr) 流量(100) 协议(80) 端口(80) 延迟(90) 状态(50) 操作(150)
COLS_WITH_PING = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 90px 50px 150px; align-items: center;'

# 2. 无延迟 (用于：所有服务器、自定义分组) - 移除了延迟列
# 格式: 服务器(150) 备注(200) 分组(1fr) 流量(100) 协议(80) 端口(80) 状态(50) 操作(150)
COLS_NO_PING   = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 50px 150px; align-items: center;'

# 单个服务器视图直接复用带延迟的样式
SINGLE_COLS = 'grid-template-columns: 200px 1fr 100px 80px 80px 90px 50px 150px; align-items: center;'

# ================= [修改] 刷新逻辑 (区分是否显示延迟) =================
async def refresh_content(scope='ALL', data=None, force_refresh=False):
    client = ui.context.client
    with client: show_loading(content_container)
    
    targets = []
    title = ""
    is_group_view = False
    show_ping = False # 默认不显示延迟 (防卡顿)
    
    # A. 所有服务器 -> 不显示延迟
    if scope == 'ALL':
        targets = list(SERVERS_CACHE)
        title = f"🌍 所有服务器 ({len(targets)})"
        show_ping = False 
    
    # B. 自定义分组 -> 不显示延迟
    elif scope == 'TAG':
        targets = [s for s in SERVERS_CACHE if data in s.get('tags', [])]
        title = f"🏷️ 自定义分组: {data} ({len(targets)})"
        is_group_view = True
        show_ping = False 
        
    # C. 国家分组 -> ✨✨✨ 保留延迟 ✨✨✨
    elif scope == 'COUNTRY':
        targets = [s for s in SERVERS_CACHE if detect_country_group(s.get('name', '')) == data]
        title = f"🏳️ 区域: {data} ({len(targets)})"
        is_group_view = True
        show_ping = True 
        
    # D. 单个服务器
    elif scope == 'SINGLE':
        targets = [data]
        title = f"🖥️ {data['name']}"

    if scope != 'SINGLE':
        targets.sort(key=lambda x: x.get('name', '').lower())

    if force_refresh:
        safe_notify(f'正在同步 {len(targets)} 个服务器...')

    async def _render():
        await asyncio.sleep(0.1)
        with client:
            content_container.clear()
            SERVER_UI_MAP.clear()
            
            with content_container:
                # 顶部栏
                with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.label(title).classes('text-2xl font-bold')
                        
                        if is_group_view:
                            with ui.row().classes('gap-1'):
                                ui.button(icon='content_copy', on_click=lambda: copy_group_link(data)).props('flat dense round size=sm color=grey').tooltip('复制原始链接')
                                ui.button(icon='bolt', on_click=lambda: copy_group_link(data, target='surge')).props('flat dense round size=sm text-color=orange').tooltip('复制 Surge 订阅')
                                ui.button(icon='cloud_queue', on_click=lambda: copy_group_link(data, target='clash')).props('flat dense round size=sm text-color=green').tooltip('复制 Clash 订阅')

                    ui.button('同步最新数据', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True)).props('outline color=primary')
                
                if scope == 'SINGLE': 
                    await render_single_server_view(data, force_refresh)
                else: 
                    # ✨ 传递 show_ping 参数
                    await render_aggregated_view(targets, show_ping=show_ping, force_refresh=force_refresh)

    asyncio.create_task(_render())
    
async def render_single_server_view(server_conf, force_refresh=False):
    mgr = get_manager(server_conf); list_container = ui.column().classes('w-full')
    with ui.row().classes('w-full justify-end mb-2'):
        ui.button('新建节点', icon='add', color='green', on_click=lambda: open_inbound_dialog(mgr, None, lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('dense')

    try:
        res = await fetch_inbounds_safe(server_conf, force_refresh=force_refresh)
        list_container.clear()
        
        raw_host = server_conf['url']
        try:
            if '://' not in raw_host: raw_host = f'http://{raw_host}'
            p = urlparse(raw_host); raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
        except: pass

        # 1. 强制清除旧缓存 & 触发新 Ping
        if res:
            for n in res:
                t_host = n.get('listen') or raw_host
                t_port = n.get('port')
                k = f"{t_host}:{t_port}"
                if k in PING_CACHE: del PING_CACHE[k]
            asyncio.create_task(batch_ping_nodes(res, raw_host))

        with list_container:
            # 表头
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2').style(SINGLE_COLS):
                ui.label('备注名称').classes('text-left pl-2')
                for h in ['所在组', '已用流量', '协议', '端口', '延迟', '状态', '操作']: 
                    ui.label(h).classes('text-center')
            
            if not res: ui.label('暂无节点或连接失败').classes('text-gray-400 mt-4 text-center w-full'); return
            if not force_refresh: ui.label('本地缓存模式').classes('text-xs text-gray-300 w-full text-right px-2')
            
            for n in res:
                traffic = format_bytes(n.get('up', 0) + n.get('down', 0))
                target_host = n.get('listen') or raw_host
                target_port = n.get('port')
                ping_key = f"{target_host}:{target_port}"

                with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(SINGLE_COLS):
                    ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                    ui.label(server_conf.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                    ui.label(traffic).classes('text-xs text-gray-600 w-full text-center font-mono')
                    ui.label(n.get('protocol', 'unknown')).classes('uppercase text-xs font-bold w-full text-center')
                    ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                    
                    # ✨✨✨ [修复对齐] 延迟列 ✨✨✨
                    with ui.row().classes('w-full justify-center items-center gap-1 no-wrap'):
                        spinner = ui.spinner('dots', size='1em', color='primary') 
                        spinner.set_visibility(False)
                        lbl_ping = ui.label('').classes('text-xs font-mono font-bold text-center')

                    def update_ping_display(l=lbl_ping, s=spinner, k=ping_key):
                        val = PING_CACHE.get(k, None)
                        if val is None:
                            s.set_visibility(True)
                            l.set_visibility(False)
                        elif val == -1: 
                            s.set_visibility(False)
                            l.set_visibility(True)
                            l.set_text('超时')
                            l.classes(replace='text-red-500')
                        else:
                            s.set_visibility(False)
                            l.set_visibility(True)
                            l.set_text(f"{val} ms")
                            l.classes(remove='text-red-500 text-green-600 text-yellow-600 text-red-400')
                            if val < 100: l.classes(add='text-green-600')
                            elif val < 200: l.classes(add='text-yellow-600')
                            else: l.classes(add='text-red-400')
                    
                    ui.timer(0.5, update_ping_display)
                    
                    with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle', color='green' if n.get('enable') else 'red').props('size=xs')
                    
                    # 操作栏
                    with ui.row().classes('gap-2 justify-center w-full no-wrap'):
                        link = generate_node_link(n, raw_host)
                        if link: ui.button(icon='content_copy', on_click=lambda l=link: safe_copy_to_clipboard(l)).props('flat dense size=sm').tooltip('复制链接')
                        
                        detail_conf = generate_detail_config(n, raw_host)
                        if detail_conf: ui.button(icon='description', on_click=lambda l=detail_conf: safe_copy_to_clipboard(l)).props('flat dense size=sm text-color=orange').tooltip('复制配置')

                        ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm')
                        ui.button(icon='delete', on_click=lambda i=n: delete_inbound_with_confirm(mgr, i['id'], i.get('remark','未命名'), lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm color=red')
    except: pass
    
# ================= [修改] 聚合视图 (修复区域分组无延迟数据的问题) =================
async def render_aggregated_view(server_list, show_ping=False, force_refresh=False):
    list_container = ui.column().classes('w-full gap-4')
    
    results = []
    if force_refresh:
        tasks = [fetch_inbounds_safe(s, force_refresh=True) for s in server_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        for s in server_list:
            results.append(NODES_DATA.get(s['url'], []))

    list_container.clear()
    
    current_css = COLS_WITH_PING if show_ping else COLS_NO_PING
    
    with list_container:
        # 表头
        with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2 bg-gray-50').style(current_css):
            ui.label('服务器').classes('text-left pl-2')
            ui.label('备注名称').classes('text-left pl-2')
            headers = ['所在组', '已用流量', '协议', '端口']
            if show_ping: headers.append('延迟') 
            headers.extend(['状态', '操作'])
            for h in headers: ui.label(h).classes('text-center')
        
        for i, res in enumerate(results):
            if i % 5 == 0: await asyncio.sleep(0.001)
            srv = server_list[i]
            if isinstance(res, Exception): res = []
            if res is None: res = []
            mgr = get_manager(srv)
            raw_host = srv['url']
            try:
                if '://' not in raw_host: raw_host = f'http://{raw_host}'
                p = urlparse(raw_host); raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
            except: pass

            # ✨✨✨ 修复点 1：如果是区域分组(show_ping=True)，主动触发测速 ✨✨✨
            if show_ping and res:
                 asyncio.create_task(batch_ping_nodes(res, raw_host))

            row_wrapper = ui.element('div').classes('w-full')
            SERVER_UI_MAP[srv['url']] = row_wrapper
            with row_wrapper:
                if not res:
                    with ui.element('div').classes('grid w-full gap-4 py-3 border-b bg-gray-50 px-2 items-center').style(current_css):
                        ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                        msg = '❌ 连接失败' if force_refresh else '⏳ 暂无数据'
                        color = 'text-red-500' if force_refresh else 'text-gray-400'
                        ui.label(msg).classes(f'{color} font-bold w-full text-left pl-2')
                        ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                        
                        placeholder_count = 3 if show_ping else 2 
                        for _ in range(placeholder_count): ui.label('-').classes('w-full text-center')
                        
                        with ui.element('div').classes('flex justify-center w-full'): ui.icon('help_outline', color='grey').props('size=xs')
                        with ui.row().classes('gap-2 justify-center w-full'): ui.button(icon='sync', on_click=lambda s=srv: refresh_content('SINGLE', s, force_refresh=True)).props('flat dense size=sm color=primary').tooltip('单独同步')
                    continue

                for n in res:
                    try:
                        traffic = format_bytes(n.get('up', 0) + n.get('down', 0))
                        target_host = n.get('listen') or raw_host
                        target_port = n.get('port')
                        ping_key = f"{target_host}:{target_port}"
                        
                        with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(current_css):
                            ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                            ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                            ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                            ui.label(traffic).classes('text-xs text-gray-600 w-full text-center font-mono')
                            ui.label(n.get('protocol', 'unk')).classes('uppercase text-xs font-bold w-full text-center')
                            ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                            
                            # ✨✨✨ 修复点 2：如果是区域分组，恢复动态刷新逻辑 ✨✨✨
                            if show_ping:
                                with ui.row().classes('w-full justify-center items-center gap-1 no-wrap'):
                                    spinner = ui.spinner('dots', size='1em', color='primary')
                                    spinner.set_visibility(False)
                                    lbl_ping = ui.label('').classes('text-xs font-mono font-bold text-center')

                                def update_ping_display(l=lbl_ping, s=spinner, k=ping_key):
                                    val = PING_CACHE.get(k, None)
                                    if val is None: 
                                        s.set_visibility(True)
                                        l.set_visibility(False)
                                    elif val == -1: 
                                        s.set_visibility(False)
                                        l.set_visibility(True)
                                        l.set_text('超时')
                                        l.classes(replace='text-red-500')
                                    else:
                                        s.set_visibility(False)
                                        l.set_visibility(True)
                                        l.set_text(f"{val} ms")
                                        l.classes(remove='text-red-500 text-green-600 text-yellow-600 text-red-400')
                                        if val < 100: l.classes(add='text-green-600')
                                        elif val < 200: l.classes(add='text-yellow-600')
                                        else: l.classes(add='text-red-400')
                                
                                # 恢复定时器，1秒刷新一次（比单个服务器的0.5秒稍慢，减轻压力）
                                ui.timer(1.0, update_ping_display)

                            with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle', color='green' if n.get('enable') else 'red').props('size=xs')
                            
                            with ui.row().classes('gap-2 justify-center w-full no-wrap'):
                                link = generate_node_link(n, raw_host)
                                if link: ui.button(icon='content_copy', on_click=lambda l=link: safe_copy_to_clipboard(l)).props('flat dense size=sm').tooltip('复制链接')
                                detail_conf = generate_detail_config(n, raw_host)
                                if detail_conf: ui.button(icon='description', on_click=lambda l=detail_conf: safe_copy_to_clipboard(l)).props('flat dense size=sm text-color=orange').tooltip('复制配置')
                                ui.button(icon='edit', on_click=lambda m=mgr, i=n, s=srv: open_inbound_dialog(m, i, lambda: refresh_content('SINGLE', s, force_refresh=True))).props('flat dense size=sm')
                                ui.button(icon='delete', on_click=lambda m=mgr, i=n, s=srv: delete_inbound_with_confirm(m, i['id'], i.get('remark','未命名'), lambda: refresh_content('SINGLE', s, force_refresh=True))).props('flat dense size=sm color=red')
                    except: continue


async def load_dashboard_stats():
    async def _render():
        await asyncio.sleep(0.1)
        total_servers = len(SERVERS_CACHE)
        online_servers = 0; total_nodes = 0; total_traffic_bytes = 0; server_traffic_map = {}; protocol_count = {} 
        for s in SERVERS_CACHE:
            res = NODES_DATA.get(s['url'], [])
            name = s.get('name', '未命名')
            if res:
                online_servers += 1; total_nodes += len(res); srv_traffic = 0
                for n in res: 
                    t = n.get('up', 0) + n.get('down', 0); total_traffic_bytes += t; srv_traffic += t
                    proto = n.get('protocol', 'unknown').upper()
                    protocol_count[proto] = protocol_count.get(proto, 0) + 1
                server_traffic_map[name] = srv_traffic
            else: server_traffic_map[name] = 0
        
        traffic_display = f"{total_traffic_bytes / (1024**3):.2f} GB"
        content_container.clear()
        with content_container:
            ui.label('系统概览').classes('text-3xl font-bold mb-6 text-slate-800 tracking-tight')
            with ui.row().classes('w-full gap-6 mb-8'):
                def stat_card(title, value, sub_text, icon, gradient):
                    with ui.card().classes(f'flex-1 p-6 shadow-lg border-none text-white {gradient} rounded-xl transform hover:scale-105 transition duration-300 relative overflow-hidden'):
                        ui.element('div').classes('absolute -right-6 -top-6 w-24 h-24 bg-white opacity-10 rounded-full')
                        with ui.row().classes('items-center justify-between w-full relative z-10'):
                            with ui.column().classes('gap-1'):
                                ui.label(title).classes('opacity-80 text-xs font-bold uppercase tracking-wider')
                                ui.label(str(value)).classes('text-3xl font-extrabold tracking-tight')
                                ui.label(sub_text).classes('opacity-70 text-xs font-medium')
                            ui.icon(icon).classes('text-4xl opacity-80')
                stat_card('在线服务器', f"{online_servers}/{total_servers}", 'Online / Total', 'dns', 'bg-gradient-to-br from-blue-500 to-indigo-600')
                stat_card('节点总数', total_nodes, 'Active Nodes', 'hub', 'bg-gradient-to-br from-purple-500 to-pink-600')
                stat_card('总流量消耗', traffic_display, 'Upload + Download', 'bolt', 'bg-gradient-to-br from-emerald-500 to-teal-600')
                stat_card('订阅配置', len(SUBS_CACHE), 'Subscriptions', 'rss_feed', 'bg-gradient-to-br from-orange-400 to-red-500')
            with ui.row().classes('w-full gap-6 mb-6'):
                with ui.card().classes('w-2/3 p-6 shadow-md border-none rounded-xl bg-white'):
                    ui.label('📊 服务器流量排行 (GB)').classes('text-lg font-bold text-slate-700 mb-4')
                    sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15] 
                    names = [x[0] for x in sorted_traffic]; values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]
                    ui.echart({
                        'color': ['#6366f1'], 'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
                        'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                        'xAxis': {'type': 'category', 'data': names, 'axisTick': {'alignWithLabel': True}, 'axisLabel': {'interval': 0, 'rotate': 30, 'color': '#64748b'}},
                        'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'type': 'dashed', 'color': '#f1f5f9'}}},
                        'series': [{'type': 'bar', 'data': values, 'barWidth': '40%', 'itemStyle': {'borderRadius': [4, 4, 0, 0], 'color': {'type': 'linear', 'x': 0, 'y': 0, 'x2': 0, 'y2': 1, 'colorStops': [{'offset': 0, 'color': '#818cf8'}, {'offset': 1, 'color': '#4f46e5'}]}}}]
                    }).classes('w-full h-80')
                with ui.card().classes('flex-grow p-6 shadow-md border-none rounded-xl bg-white'):
                    ui.label('🍩 协议分布').classes('text-lg font-bold text-slate-700 mb-4')
                    pie_data = [{'name': k, 'value': v} for k, v in protocol_count.items()]
                    ui.echart({
                        'color': ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'],
                        'tooltip': {'trigger': 'item'}, 'legend': {'bottom': '0%'},
                        'series': [{'name': '协议', 'type': 'pie', 'radius': ['50%', '70%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 10, 'borderColor': '#fff', 'borderWidth': 2}, 'label': {'show': False, 'position': 'center'}, 'emphasis': {'label': {'show': True, 'fontSize': '20', 'fontWeight': 'bold'}}, 'labelLine': {'show': False}, 'data': pie_data}]
                    }).classes('w-full h-80')
    asyncio.create_task(_render())

@ui.refreshable
def render_sidebar_content():
    # 1. 顶部区域
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0'):
        ui.label('小龙女她爸').classes('text-xl font-bold mb-4 text-slate-800')
        # 大按钮样式
        ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes('w-full text-slate-700')
        ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes('w-full text-slate-700')

    # 2. 列表区域
    with ui.column().classes('w-full flex-grow overflow-y-auto p-2 gap-1'):
        
        # 操作按钮
        with ui.row().classes('w-full gap-2 px-1 mb-4'):
            ui.button('新建分组', icon='create_new_folder', on_click=open_create_group_dialog).props('dense unelevated').classes('flex-grow bg-blue-600 text-white text-xs')
            ui.button('添加服务器', icon='add', color='green', on_click=open_add_server_dialog).props('dense unelevated').classes('flex-grow text-xs')

        # --- A. 全部节点 ---
        all_count = len(SERVERS_CACHE)
        with ui.row().classes('w-full items-center justify-between p-3 border rounded mb-2 bg-slate-100 hover:bg-slate-200 cursor-pointer').on('click', lambda _: refresh_content('ALL')):
            with ui.row().classes('items-center gap-2'):
                ui.icon('dns', color='primary')
                ui.label('所有服务器').classes('font-bold')
            ui.badge(str(all_count), color='blue')

        # --- B. 自定义分组 (Tags) ---
        if 'custom_groups' in ADMIN_CONFIG and ADMIN_CONFIG['custom_groups']:
            ui.label('自定义分组').classes('text-xs font-bold text-gray-400 mt-2 mb-1 px-2')
            for tag_group in ADMIN_CONFIG['custom_groups']:
                tag_servers = [s for s in SERVERS_CACHE if tag_group in s.get('tags', [])]
                
                is_open = tag_group in EXPANDED_GROUPS
                with ui.expansion('', icon='label', value=is_open).classes('w-full border rounded mb-1 bg-white shadow-sm').on_value_change(lambda e, g=tag_group: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                    # ✨✨✨ 优化点：分离点击事件 ✨✨✨
                    with exp.add_slot('header'):
                        # 将 click.stop 加在整个 row 上
                        # 这样点击标题栏任何空白处，只会触发 refresh_content，并且阻止展开/收起
                        with ui.row().classes('w-full items-center justify-between no-wrap cursor-pointer').on('click.stop', lambda _, g=tag_group: refresh_content('TAG', g)):
                            # 组名
                            ui.label(tag_group).classes('flex-grow font-bold truncate')
                            # 组管理按钮 (本身有 click.stop，互不影响)
                            ui.button(icon='edit', on_click=lambda _, g=tag_group: open_group_mgmt_dialog(g)).props('flat dense round size=xs color=grey').on('click.stop')
                            # 数量
                            ui.badge(str(len(tag_servers)), color='orange' if not tag_servers else 'grey')
                    
                    # 内容区
                    with ui.column().classes('w-full gap-0 bg-gray-50'):
                        if not tag_servers:
                            ui.label('空分组').classes('text-xs text-gray-400 p-2 italic')
                        for s in tag_servers:
                            with ui.row().classes('w-full justify-between items-center p-2 pl-4 border-b border-gray-100 hover:bg-blue-100 cursor-pointer').on('click', lambda _, s=s: refresh_content('SINGLE', s)):
                                ui.label(s['name']).classes('text-sm truncate flex-grow')
                                ui.button(icon='edit', on_click=lambda _, idx=SERVERS_CACHE.index(s): open_edit_server_dialog(idx)).props('flat dense round size=xs color=grey').on('click.stop')

        # --- C. 自动国家分组 (Auto) ---
        ui.label('区域分组 (自动)').classes('text-xs font-bold text-gray-400 mt-2 mb-1 px-2')
        
        country_buckets = {}
        for s in SERVERS_CACHE:
            c_group = detect_country_group(s.get('name', ''))
            if c_group not in country_buckets: country_buckets[c_group] = []
            country_buckets[c_group].append(s)
        
        for c_name in sorted(country_buckets.keys()):
            c_servers = country_buckets[c_name]
            c_servers.sort(key=lambda x: x['name'])
            
            is_open = c_name in EXPANDED_GROUPS
            with ui.expansion('', icon='public', value=is_open).classes('w-full border rounded mb-1 bg-white shadow-sm').on_value_change(lambda e, g=c_name: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                 # ✨✨✨ 优化点：分离点击事件 ✨✨✨
                 with exp.add_slot('header'):
                    # 同样给 row 加上 click.stop
                    with ui.row().classes('w-full items-center justify-between no-wrap cursor-pointer').on('click.stop', lambda _, g=c_name: refresh_content('COUNTRY', g)):
                        ui.label(c_name).classes('flex-grow font-bold truncate')
                        ui.badge(str(len(c_servers)), color='green')
                 
                 # 内容区
                 with ui.column().classes('w-full gap-0 bg-gray-50'):
                    for s in c_servers:
                         with ui.row().classes('w-full justify-between items-center p-2 pl-4 border-b border-gray-100 hover:bg-blue-100 cursor-pointer').on('click', lambda _, s=s: refresh_content('SINGLE', s)):
                                ui.label(s['name']).classes('text-sm truncate flex-grow')
                                ui.button(icon='edit', on_click=lambda _, idx=SERVERS_CACHE.index(s): open_edit_server_dialog(idx)).props('flat dense round size=xs color=grey').on('click.stop')

    # 3. 底部
    with ui.column().classes('w-full p-2 border-t mt-auto'):
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes('w-full text-slate-600 text-sm')
        
# ================== 登录与 MFA 逻辑 ==================
@ui.page('/login')
def login_page():
    # 容器：用于切换登录步骤 (账号密码 -> MFA)
    container = ui.card().classes('absolute-center w-full max-w-sm p-8 shadow-2xl rounded-xl bg-white')

    # --- 步骤 1: 账号密码验证 ---
    def render_step1():
        container.clear()
        with container:
            ui.label('X-UI Manager').classes('text-2xl font-extrabold mb-2 w-full text-center text-slate-800')
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
        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name="X-UI Manager")
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
        ui.navigate.to('/')

    render_step1()

@ui.page('/')
def main_page():
    if not app.storage.user.get('authenticated', False):
        return RedirectResponse('/login')

    with ui.header().classes('bg-slate-900 text-white h-14'):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.row().classes('items-center'):
                ui.label('X-UI Manager Pro').classes('text-lg font-bold ml-4 mr-4')
                
                # --- ✨✨✨ 新增：右上角复制密钥按钮 ✨✨✨ ---
                with ui.button(icon='vpn_key', on_click=lambda: safe_copy_to_clipboard(AUTO_REGISTER_SECRET)).props('flat dense round').tooltip('点击复制通讯密钥'):
                    ui.badge('Key', color='red').props('floating')
                # ---------------------------------------------

            ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense')

    global content_container
    with ui.row().classes('w-full h-screen gap-0'):
        with ui.column().classes('w-80 h-full border-r pr-0 overflow-hidden'):
            render_sidebar_content()
        content_container = ui.column().classes('flex-grow h-full pl-6 overflow-y-auto p-4 bg-slate-50')
    
    # [核心修复] 开机 2 秒后，执行【后台静默刷新】，不操作 UI，不跳转
    ui.timer(2.0, lambda: asyncio.create_task(silent_refresh_all()), once=True)
    
    ui.timer(0.1, lambda: asyncio.create_task(load_dashboard_stats()), once=True)
    logger.info("✅ UI 已就绪")

# ================= 全局定时 Ping 任务 =================
async def run_global_ping_task():
    while True:
        try:
            logger.info("📡 开始全局节点延迟测试...")
            tasks = []
            for srv in SERVERS_CACHE:
                raw_host = srv['url']
                try:
                    if '://' not in raw_host: raw_host = f'http://{raw_host}'
                    p = urlparse(raw_host); raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
                except: continue
                
                nodes = NODES_DATA.get(srv['url'], [])
                if nodes:
                    tasks.append(batch_ping_nodes(nodes, raw_host))
            
            if tasks:
                await asyncio.gather(*tasks)
            logger.info("✅ 全局延迟测试完成")
        except Exception as e:
            logger.error(f"Ping 任务异常: {e}")
        
        await asyncio.sleep(300) # 5分钟 = 300秒

# 在 app 启动时运行
app.on_startup(lambda: asyncio.create_task(run_global_ping_task()))

if __name__ in {"__main__", "__mp_main__"}:
    logger.info("🚀 系统正在初始化...")
    ui.run(title='X-UI Pro', host='0.0.0.0', port=8080, language='zh-CN', storage_secret='sijuly_secret_key', reload=False)
