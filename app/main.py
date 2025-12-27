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

ADMIN_USER = os.getenv('XUI_USERNAME', 'admin')
ADMIN_PASS = os.getenv('XUI_PASSWORD', 'admin')

SERVERS_CACHE = []
SUBS_CACHE = []
NODES_DATA = {}
ADMIN_CONFIG = {}

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

def safe_base64(s): return base64.b64encode(s.encode('utf-8')).decode('utf-8')
def decode_base64_safe(s): 
    try: return base64.b64decode(s).decode('utf-8')
    except: return ""

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

@app.get('/sub/group/{group_b64}')
async def group_sub_handler(group_b64: str, request: Request):
    group_name = decode_base64_safe(group_b64)
    if not group_name: return Response("Invalid Group Name", 400)
    links = []
    target_servers = [s for s in SERVERS_CACHE if s.get('group', '默认分组') == group_name]
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
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")

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

async def copy_group_link(group_name):
    try:
        origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
        if not origin: origin = ""
        encoded_name = safe_base64(group_name)
        link = f"{origin}/sub/group/{encoded_name}"
        await safe_copy_to_clipboard(link)
        safe_notify(f"已复制 [{group_name}] 专属订阅链接", "positive")
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

class SubEditor:
    def __init__(self, data=None):
        self.data = data; self.d = data.copy() if data else {'name':'','token':str(uuid.uuid4()),'nodes':[]}
        self.sel = set(self.d['nodes'])

    def ui(self, dlg):
        with ui.card().classes('w-[95vw] max-w-[400px] flex flex-col p-4 gap-4 items-stretch'):
            with ui.element('div').classes('flex justify-between items-center w-full'):
                ui.label('订阅编辑器').classes('text-lg font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense color=grey')
            ui.input('订阅名称', value=self.d['name']).classes('w-full').on_value_change(lambda e: self.d.update({'name':e.value}))
            ui.label('选择节点:').classes('text-sm text-gray-500 mt-2')
            cont = ui.column().classes('w-full flex-grow overflow-y-auto border rounded p-2 bg-gray-50 h-[50vh]')
            async def load():
                with cont: ui.spinner('dots', size='2rem').classes('self-center mt-4')
                tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in SERVERS_CACHE]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                cont.clear()
                with cont:
                    if not SERVERS_CACHE: ui.label('暂无服务器').classes('text-center text-gray-400')
                    for i, srv in enumerate(SERVERS_CACHE):
                        res = results[i]
                        if not res or isinstance(res, Exception): res = NODES_DATA.get(srv['url'], [])
                        ui.label(srv['name']).classes('font-bold text-gray-700 mt-2 text-sm px-1')
                        if not res: ui.label('无节点').classes('text-xs text-gray-400 ml-2')
                        else:
                            for n in res:
                                k = f"{srv['url']}|{n['id']}"
                                ui.checkbox(n['remark'], value=(k in self.sel), on_change=lambda e, k=k: self.sel.add(k) if e.value else self.sel.discard(k)).classes('w-full text-sm ml-2')
            asyncio.create_task(load())
            async def save():
                self.d['nodes'] = list(self.sel)
                if self.data: 
                    for i, s in enumerate(SUBS_CACHE):
                        if s['token'] == self.data['token']: SUBS_CACHE[i] = self.d
                else: SUBS_CACHE.append(self.d)
                await save_subs(); await load_subs_view(); dlg.close()
            ui.button('保存订阅', on_click=save).classes('w-full bg-primary text-white h-10 mt-auto')

def open_sub_editor(d):
    with ui.dialog() as dlg: SubEditor(d).ui(dlg); dlg.open()

async def load_subs_view():
    show_loading(content_container)
    try: origin = await ui.run_javascript('return window.location.origin', timeout=3.0)
    except: origin = ""
    content_container.clear()
    with content_container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4')
        with ui.row().classes('w-full mb-4 justify-end'): ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_sub_editor(None))
        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes('w-full p-4 mb-2 shadow-sm hover:shadow-md transition'):
                with ui.row().classes('justify-between w-full items-center'):
                    with ui.column().classes('gap-1'):
                        ui.label(sub['name']).classes('font-bold text-lg text-slate-800'); ui.label(f"包含 {len(sub.get('nodes',[]))} 个节点").classes('text-xs text-gray-500')
                    with ui.row():
                        ui.button(icon='edit', on_click=lambda s=sub: open_sub_editor(s)).props('flat dense color=blue')
                        async def dl(i=idx): del SUBS_CACHE[i]; await save_subs(); await load_subs_view()
                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense')
                ui.separator().classes('my-2')
                path = f"/sub/{sub['token']}"; full_url = f"{origin}{path}" if origin else path
                with ui.row().classes('w-full items-center gap-2 bg-gray-50 p-2 rounded'):
                    ui.icon('link').classes('text-gray-400'); ui.input(value=full_url).props('readonly borderless dense').classes('flex-grow text-xs font-mono text-gray-600'); ui.button(icon='content_copy', on_click=lambda u=full_url: safe_copy_to_clipboard(u)).props('flat dense round size=sm color=grey')

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
    with ui.dialog() as d, ui.card().classes('w-[95vw] max-w-[400px] flex flex-col p-4 gap-4 items-stretch'):
        with ui.element('div').classes('flex justify-between items-center w-full'):
            ui.label(f'管理: {group_name}').classes('text-lg font-bold')
            ui.button(icon='close', on_click=d.close).props('flat round dense color=grey')
        new_name = ui.input('重命名组', value=group_name).classes('w-full')
        ui.label('包含的服务器:').classes('text-sm text-gray-500 mt-2')
        sel_urls = {s['url'] for s in SERVERS_CACHE if s.get('group')==group_name}
        with ui.column().classes('w-full flex-grow overflow-y-auto border rounded p-2 gap-2 h-[50vh]'):
            for s in SERVERS_CACHE:
                def toggle(e, u=s.get('url')): 
                    if e.value: sel_urls.add(u)
                    else: sel_urls.discard(u)
                ui.checkbox(s.get('name', '未命名'), value=(s.get('url') in sel_urls), on_change=toggle).classes('w-full text-sm')
        async def save():
            for s in SERVERS_CACHE:
                if s['url'] in sel_urls: s['group'] = new_name.value
                elif s.get('group') == group_name: s['group'] = '默认分组'
            await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('GROUP', new_name.value)
        ui.button('保存修改', on_click=save).classes('w-full bg-primary text-white h-10')
    d.open()

def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-full max-w-sm flex flex-col gap-4 p-6'):
        ui.label('新建分组').classes('text-lg font-bold mb-4')
        name_input = ui.input('分组名称').classes('w-full')
        server_select = ui.select({s['url']: s.get('name', '未命名') for s in SERVERS_CACHE}, label='选择服务器', multiple=True).classes('w-full').props('use-chips')
        async def save_new_group():
            if not name_input.value: return
            for s in SERVERS_CACHE:
                if s['url'] in (server_select.value or []): s['group'] = name_input.value
            await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('GROUP', name_input.value)
        ui.button('保存', on_click=save_new_group).classes('w-full bg-blue-600 text-white mt-4')
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

# [修改] 调整列布局：
TABLE_COLS_CSS = 'grid-template-columns: 150px 200px 1fr 100px 80px 80px 50px 150px; align-items: center;'
SINGLE_COLS = 'grid-template-columns: 200px 1fr 100px 80px 80px 50px 150px; align-items: center;'

async def refresh_content(scope='ALL', data=None, force_refresh=False):
    client = ui.context.client
    with client: show_loading(content_container)
    
    if force_refresh:
        count = len(SERVERS_CACHE)
        if scope == 'GROUP': count = len([s for s in SERVERS_CACHE if s.get('group', '默认分组') == data])
        elif scope == 'SINGLE': count = 1
        safe_notify(f'正在同步 {count} 个服务器 (并发: 15)...')

    async def _render():
        await asyncio.sleep(0.1)
        targets = []
        title = ""
        is_group_view = False
        
        if scope == 'ALL': targets = SERVERS_CACHE; title = f"🌍 所有节点 ({len(targets)})"
        elif scope == 'GROUP': 
            targets = [s for s in SERVERS_CACHE if s.get('group', '默认分组') == data]; title = f"📁 分组: {data} ({len(targets)})"
            is_group_view = True
        elif scope == 'SINGLE': targets = [data]; title = f"🖥️ {data['name']}"

        with client:
            content_container.clear()
            SERVER_UI_MAP.clear()
            
            with content_container:
                with ui.row().classes('items-center w-full mb-4 border-b pb-2 justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.label(title).classes('text-2xl font-bold')
                        if is_group_view:
                            ui.button('复制订阅', icon='link', on_click=lambda g=data: copy_group_link(g)).props('outline dense size=sm').classes('text-blue-600')
                    ui.button('同步最新数据', icon='sync', on_click=lambda: refresh_content(scope, data, force_refresh=True)).props('outline color=primary')
                
                if scope == 'SINGLE': await render_single_server_view(data, force_refresh)
                else: await render_aggregated_view(targets, force_refresh)
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

        with list_container:
            # [修改] 表头顺序调整：备注 -> 所在组 -> 已用流量 -> 协议...
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2').style(SINGLE_COLS):
                ui.label('备注名称').classes('text-left pl-2')
                for h in ['所在组', '已用流量', '协议', '端口', '状态', '操作']: 
                    ui.label(h).classes('text-center')
            
            if not res: ui.label('暂无节点或连接失败').classes('text-gray-400 mt-4 text-center w-full'); return
            if not force_refresh: ui.label('本地缓存模式').classes('text-xs text-gray-300 w-full text-right px-2')
            
            for n in res:
                # [修改] 计算流量
                traffic = n.get('up', 0) + n.get('down', 0)
                traffic_str = format_bytes(traffic)

                with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(SINGLE_COLS):
                    # 1. 备注
                    ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                    # 2. 所在组 (移到备注右侧)
                    ui.label(server_conf.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                    # 3. 流量 (新增)
                    ui.label(traffic_str).classes('text-xs text-gray-600 w-full text-center font-mono')
                    # 4. 协议
                    ui.label(n.get('protocol', 'unknown')).classes('uppercase text-xs font-bold w-full text-center')
                    # 5. 端口
                    ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                    # 6. 状态
                    with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle', color='green' if n.get('enable') else 'red').props('size=xs')
                    # 7. 操作
                    with ui.row().classes('gap-2 justify-center w-full'):
                        link = generate_node_link(n, raw_host)
                        if link: ui.button(icon='content_copy', on_click=lambda l=link: safe_copy_to_clipboard(l)).props('flat dense size=sm').tooltip('复制链接')
                        ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm')
                        ui.button(icon='delete', on_click=lambda i=n: delete_inbound(mgr, i['id'], lambda: refresh_content('SINGLE', server_conf, force_refresh=True))).props('flat dense size=sm color=red')
    except: pass

async def render_aggregated_view(server_list, force_refresh=False):
    list_container = ui.column().classes('w-full gap-4')
    try:
        tasks = [fetch_inbounds_safe(s, force_refresh=force_refresh) for s in server_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        list_container.clear()
        
        with list_container:
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2 bg-gray-50').style(TABLE_COLS_CSS):
                ui.label('服务器').classes('text-left pl-2')
                ui.label('备注名称').classes('text-left pl-2')
                for h in ['所在组', '已用流量', '协议', '端口', '状态', '操作']: 
                    ui.label(h).classes('text-center')
            
            for i, res in enumerate(results):
                if i % 2 == 0: await asyncio.sleep(0.01)
                srv = server_list[i]
                if res is None or isinstance(res, Exception): res = NODES_DATA.get(srv['url'], [])
                mgr = get_manager(srv)
                raw_host = srv['url']
                try:
                    if '://' not in raw_host: raw_host = f'http://{raw_host}'
                    p = urlparse(raw_host); raw_host = p.hostname or raw_host.split('://')[-1].split(':')[0]
                except: pass

                row_wrapper = ui.element('div').classes('w-full')
                SERVER_UI_MAP[srv['url']] = row_wrapper
                with row_wrapper:
                    if not res:
                        with ui.element('div').classes('grid w-full gap-4 py-3 border-b bg-red-50 px-2 items-center').style(TABLE_COLS_CSS):
                            ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                            ui.label('❌ 连接失败').classes('text-red-500 font-bold w-full text-left pl-2')
                            ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                            ui.label('-').classes('w-full text-center')
                            ui.label('-').classes('w-full text-center')
                            ui.label('-').classes('w-full text-center')
                            with ui.element('div').classes('flex justify-center w-full'): ui.icon('error', color='red').props('size=xs')
                            with ui.row().classes('gap-2 justify-center w-full'): ui.button(icon='settings', on_click=lambda s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm color=grey')
                        continue

                    for n in res:
                        try:
                            traffic = n.get('up', 0) + n.get('down', 0)
                            traffic_str = format_bytes(traffic)

                            with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(TABLE_COLS_CSS):
                                ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-left pl-2')
                                ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-2')
                                ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                                ui.label(traffic_str).classes('text-xs text-gray-600 w-full text-center font-mono')
                                ui.label(n.get('protocol', 'unk')).classes('uppercase text-xs font-bold w-full text-center')
                                ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                                with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle', color='green' if n.get('enable') else 'red').props('size=xs')
                                with ui.row().classes('gap-2 justify-center w-full'):
                                    link = generate_node_link(n, raw_host)
                                    if link: ui.button(icon='content_copy', on_click=lambda l=link: safe_copy_to_clipboard(l)).props('flat dense size=sm').tooltip('复制链接')
                                    ui.button(icon='edit', on_click=lambda m=mgr, i=n, s=srv: open_inbound_dialog(m, i, lambda: refresh_content('SINGLE', s, force_refresh=True))).props('flat dense size=sm')
                                    ui.button(icon='delete', on_click=lambda m=mgr, i=n, s=srv: delete_inbound(m, i['id'], lambda: refresh_content('SINGLE', s, force_refresh=True))).props('flat dense size=sm color=red')
                        except: continue
    except: pass

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
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0'):
        ui.label('X-UI Manager Pro').classes('text-xl font-bold mb-4 text-slate-800')
        ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props('flat align=left').classes('w-full text-slate-700')
        ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes('w-full text-slate-700')

    with ui.column().classes('w-full flex-grow overflow-y-auto p-2 gap-1'):
        ui.label('资源列表').classes('font-bold text-xs text-gray-400 mt-2 mb-1 px-2 uppercase')
        with ui.row().classes('w-full gap-2 px-1 mb-2'):
            ui.button('新建分组', icon='create_new_folder', on_click=open_create_group_dialog).props('dense unelevated').classes('flex-grow bg-blue-600 text-white text-xs')
            ui.button('添加服务器', icon='add', color='green', on_click=open_add_server_dialog).props('dense unelevated').classes('flex-grow text-xs')
        ui.button('🌍 所有节点', icon='public', on_click=lambda: refresh_content('ALL')).props('flat align=left').classes('w-full font-bold mb-2')
        
        groups = {}
        for s in SERVERS_CACHE:
            g = s.get('group', '默认分组') or '默认分组'
            groups.setdefault(g, []).append(s)

        for gname, gservers in groups.items():
            is_open = gname in EXPANDED_GROUPS
            with ui.expansion('', icon='folder', value=is_open).classes('w-full border rounded mb-1 bg-white shadow-sm').props('expand-icon-class=hidden').on_value_change(lambda e, g=gname: EXPANDED_GROUPS.add(g) if e.value else EXPANDED_GROUPS.discard(g)) as exp:
                with exp.add_slot('header'):
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        ui.label(gname).classes('flex-grow cursor-pointer font-bold truncate').on('click', lambda g=gname: refresh_content('GROUP', g))
                        ui.button(icon='edit', on_click=lambda g=gname: open_group_mgmt_dialog(g)).props('flat dense round size=xs color=grey').on('click.stop')
                        ui.label(str(len(gservers))).classes('text-xs bg-gray-200 px-2 rounded-full')
                with ui.column().classes('w-full gap-0'):
                    for srv in gservers:
                        with ui.row().classes('w-full justify-between p-2 pl-4 cursor-pointer hover:bg-blue-50 items-center border-t border-gray-100 no-wrap'):
                            ui.label(srv['name']).classes('text-sm flex-grow truncate').on('click', lambda s=srv: refresh_content('SINGLE', s))
                            ui.button(icon='edit', on_click=lambda idx=SERVERS_CACHE.index(srv): open_edit_server_dialog(idx)).props('flat dense round size=xs color=grey')
    
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

if __name__ in {"__main__", "__mp_main__"}:
    logger.info("🚀 系统正在初始化...")
    ui.run(title='X-UI Pro', host='0.0.0.0', port=8080, language='zh-CN', storage_secret='sijuly_secret_key', reload=False)
