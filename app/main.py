import json
import os
import uuid
import base64
import asyncio
import logging
import requests
import urllib3
import shutil
from urllib.parse import urlparse
from nicegui import ui, run, app, Client
from fastapi import Response, Request
from fastapi.responses import RedirectResponse

# ================= 日志配置 =================
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s', 
    datefmt='%H:%M:%S',
    force=True
)
logger = logging.getLogger("XUI_Manager")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("nicegui").setLevel(logging.INFO)

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 配置区域 =================
CONFIG_FILE = 'servers.json'
SUBS_FILE = 'subscriptions.json'
NODES_CACHE_FILE = 'nodes_cache.json'

# --- [修复] 优先读取环境变量，没有则默认为 admin ---
ADMIN_USER = os.getenv('XUI_USERNAME', 'admin')
ADMIN_PASS = os.getenv('XUI_PASSWORD', 'admin')

SERVERS_CACHE = []
SUBS_CACHE = []
NODES_DATA = {}

# 全局锁
FILE_LOCK = asyncio.Lock()
# 展开状态记忆
EXPANDED_GROUPS = set()
# UI 映射表
SERVER_UI_MAP = {}

# 容器引用
content_container = None

def init_data():
    global SERVERS_CACHE, SUBS_CACHE, NODES_DATA
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
            logger.info(f"✅ 加载节点缓存完毕")
        except: NODES_DATA = {}

# 线程安全保存
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
async def save_nodes_cache():
    try:
        data_snapshot = NODES_DATA.copy()
        await safe_save(NODES_CACHE_FILE, data_snapshot)
    except: pass

init_data()
managers = {}

# 安全通知 (防崩)
def safe_notify(message, type='info'):
    try: ui.notify(message, type=type)
    except: logger.info(f"[Notify] {message}")

# ================= 核心逻辑 =================
class XUIManager:
    def __init__(self, url, username, password, api_prefix=None):
        self.original_url = str(url).strip().rstrip('/')
        self.url = self.original_url
        self.username = str(username).strip()
        self.password = str(password).strip()
        self.api_prefix = f"/{api_prefix.strip('/')}" if api_prefix else None
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36'})
        self.session.verify = False 
        self.login_path = None

    def _request(self, method, path, **kwargs):
        target_url = f"{self.url}{path}"
        try:
            if method == 'POST': return self.session.post(target_url, timeout=5, allow_redirects=False, **kwargs)
            else: return self.session.get(target_url, timeout=5, allow_redirects=False, **kwargs)
        except: return None

    def login(self):
        if self.login_path: return self._try_login_at(self.login_path)
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
        r = self._request('POST', f"{base}{suffix}", json=data)
        if r: 
            try: return r.json().get('success'), r.json().get('msg')
            except: return False, "解析失败"
        return False, "请求失败"

def get_manager(server_conf):
    key = server_conf['url']
    if key not in managers or managers[key].username != server_conf['user']:
        managers[key] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'], server_conf.get('prefix'))
    return managers[key]

async def fetch_inbounds_safe(server_conf, force_refresh=False):
    url = server_conf['url']
    name = server_conf.get('name', '未命名')
    
    if not force_refresh and url in NODES_DATA: return NODES_DATA[url]
    logger.info(f"🔄 同步: [{name}] ...")
    try:
        mgr = get_manager(server_conf)
        inbounds = await run.io_bound(mgr.get_inbounds)
        if inbounds is None:
            mgr = managers[server_conf['url']] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'], server_conf.get('prefix')) 
            inbounds = await run.io_bound(mgr.get_inbounds)
        
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

@app.get('/sub/{token}')
async def sub_handler(token: str, request: Request):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)
    links = []
    tasks = [fetch_inbounds_safe(srv, force_refresh=False) for srv in SERVERS_CACHE]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, srv in enumerate(SERVERS_CACHE):
        inbounds = results[i]
        if not inbounds or isinstance(inbounds, Exception): continue
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

# ================= UI 辅助 =================
def show_loading(container):
    try:
        container.clear()
        with container:
            with ui.column().classes('w-full h-[60vh] justify-center items-center'):
                ui.spinner('dots', size='3rem', color='primary')
                ui.label('数据处理中...').classes('text-gray-500 mt-4')
    except: pass

def get_all_groups():
    groups = {'默认分组'}
    for s in SERVERS_CACHE:
        g = s.get('group')
        if g: groups.add(g)
    return sorted(list(groups))

# [修复] 增强版复制功能，兼容 HTTP
async def safe_copy_to_clipboard(text):
    # 处理特殊字符防止 JS 报错
    safe_text = json.dumps(text).replace('"', '\\"') # 简单转义
    
    # 注入增强版 JS：先尝试 API，失败则回退到 execCommand
    js_code = f"""
    (async () => {{
        const text = {json.dumps(text)};
        try {{
            await navigator.clipboard.writeText(text);
            return true;
        }} catch (err) {{
            // 回退方案
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
        if result:
            safe_notify('已复制到剪贴板', 'positive')
        else:
            safe_notify('复制失败，请使用下载按钮 (浏览器安全限制)', 'negative')
    except:
        safe_notify('复制功能不可用，建议使用下载按钮', 'negative')

async def open_add_server_dialog():
    with ui.dialog() as d, ui.card().classes('w-2/3 max-w-5xl h-auto flex flex-col gap-4'):
        ui.label('添加服务器').classes('text-lg font-bold')
        with ui.column().classes('w-full gap-2'):
            n = ui.input('名称').classes('w-full'); g = ui.select(options=get_all_groups(), label='分组', value='默认分组').classes('w-full')
            u = ui.input('URL').classes('w-full'); us = ui.input('账号').classes('w-full')
            p = ui.input('密码', password=True).classes('w-full'); pre = ui.input('API前缀', placeholder='/xui').classes('w-full')
        async def save():
            SERVERS_CACHE.append({'name':n.value,'group':g.value,'url':u.value,'user':us.value,'pass':p.value,'prefix':pre.value})
            await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('SINGLE', SERVERS_CACHE[-1], force_refresh=True)
        ui.button('保存', on_click=save).classes('w-full mt-4 color-green')
    d.open()

async def open_edit_server_dialog(idx):
    data = SERVERS_CACHE[idx]
    with ui.dialog() as d, ui.card().classes('w-2/3 max-w-5xl h-auto flex flex-col gap-4'):
        ui.label('编辑配置').classes('text-lg font-bold')
        with ui.column().classes('w-full gap-2'):
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
                try:
                    SERVER_UI_MAP[deleted_url].delete()
                    del SERVER_UI_MAP[deleted_url]
                    safe_notify("已删除服务器", "info")
                except: await refresh_content('ALL')
            else: await refresh_content('ALL')
            d.close()
        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('删除', on_click=delete, color='red').props('flat'); ui.button('保存', on_click=save)
    d.open()

def open_group_mgmt_dialog(group_name):
    with ui.dialog() as d, ui.card().classes('w-2/3 max-w-5xl h-[70vh] flex flex-col p-0 gap-0'):
        with ui.row().classes('w-full p-4 border-b bg-gray-50 justify-between items-center'):
            ui.label(f'管理分组: {group_name}').classes('text-lg font-bold')
            new_name = ui.input('重命名组', value=group_name).props('dense').classes('w-48')
        sel_urls = {s['url'] for s in SERVERS_CACHE if s.get('group')==group_name}
        with ui.column().classes('w-full flex-grow overflow-y-auto p-0 gap-0'):
            for i, s in enumerate(SERVERS_CACHE):
                def toggle(e, u=s.get('url')): 
                    if e.value: sel_urls.add(u)
                    else: sel_urls.discard(u)
                with ui.row().classes(f'w-full items-center px-4 py-2 hover:bg-blue-50 border-b border-gray-100 no-wrap'):
                    ui.checkbox(s.get('name', '未命名'), value=(s.get('url') in sel_urls), on_change=toggle).classes('w-full text-sm truncate')
        with ui.row().classes('w-full p-4 border-t bg-gray-50 justify-end'):
            async def save():
                for s in SERVERS_CACHE:
                    if s['url'] in sel_urls: s['group'] = new_name.value
                    elif s.get('group') == group_name: s['group'] = '默认分组'
                await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('GROUP', new_name.value)
            ui.button('保存', on_click=save).classes('bg-primary text-white')
    d.open()

def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-2/3 max-w-5xl h-auto flex flex-col gap-4'):
        ui.label('新建分组').classes('text-lg font-bold mb-4')
        with ui.column().classes('w-full gap-2'):
            name_input = ui.input('分组名称').classes('w-full mb-4')
            server_select = ui.select({s['url']: s.get('name', '未命名') for s in SERVERS_CACHE}, label='选择服务器', multiple=True).classes('w-full mb-6').props('use-chips')
        async def save_new_group():
            if not name_input.value: return
            for s in SERVERS_CACHE:
                if s['url'] in (server_select.value or []): s['group'] = name_input.value
            await save_servers(); d.close(); render_sidebar_content.refresh(); await refresh_content('GROUP', name_input.value)
        ui.button('保存', on_click=save_new_group).props('color=primary')
    d.open()

# 导入导出数据弹窗
async def open_data_mgmt_dialog():
    with ui.dialog() as d, ui.card().classes('w-2/3 max-w-5xl h-auto flex flex-col gap-4'):
        with ui.tabs().classes('w-full') as tabs:
            tab_export = ui.tab('导出备份')
            tab_import = ui.tab('导入恢复')
        
        with ui.tab_panels(tabs, value=tab_export).classes('w-full flex-grow'):
            # 导出面板
            with ui.tab_panel(tab_export).classes('flex flex-col gap-6 p-4'):
                ui.label('包含服务器配置和节点缓存数据').classes('text-xs text-gray-400')
                
                full_backup = {
                    "version": "2.0",
                    "servers": SERVERS_CACHE,
                    "cache": NODES_DATA
                }
                json_str = json.dumps(full_backup, indent=2, ensure_ascii=False)
                
                ui.textarea('全量备份数据', value=json_str).props('readonly').classes('w-full h-64 font-mono text-xs')
                
                with ui.row().classes('w-full gap-4'):
                    ui.button('复制文本', icon='content_copy', on_click=lambda: safe_copy_to_clipboard(json_str)).classes('flex-grow bg-blue-600 text-white')
                    ui.button('下载 .json 文件', icon='download', on_click=lambda: ui.download(json_str.encode('utf-8'), 'xui_backup.json')).classes('flex-grow bg-green-600 text-white')

            # 导入面板
            with ui.tab_panel(tab_import).classes('flex flex-col gap-6 p-4'):
                
                # 方式一：完整备份恢复
                with ui.column().classes('w-full gap-4'):
                    ui.label('方式一：完整备份恢复 (.json)').classes('font-bold text-gray-700 text-lg')
                    
                    async def handle_json_upload(e):
                        content = e.content.read().decode('utf-8')
                        import_text.set_value(content)
                        safe_notify("文件已读取，请点击下方恢复按钮", "positive")

                    ui.upload(on_upload=handle_json_upload, auto_upload=True, label='拖拽或点击上传 JSON 备份文件').classes('w-full')
                    
                    import_text = ui.textarea(placeholder='或直接在此粘贴 JSON 内容').classes('w-full h-24 font-mono text-xs')
                    import_cache_chk = ui.checkbox('同时恢复节点缓存 (无需重新同步)', value=True).classes('text-sm text-gray-600 mt-2')
                    
                    async def process_json_import():
                        try:
                            raw = import_text.value.strip()
                            if not raw: return
                            data = json.loads(raw)
                            new_servers = []
                            new_cache = {}
                            if isinstance(data, list): new_servers = data
                            elif isinstance(data, dict): new_servers = data.get('servers', []); new_cache = data.get('cache', {})
                            
                            count = 0; existing = {s['url'] for s in SERVERS_CACHE}
                            for item in new_servers:
                                if 'url' in item and item['url'] not in existing:
                                    SERVERS_CACHE.append(item); existing.add(item['url']); count += 1
                            
                            if import_cache_chk.value and new_cache:
                                NODES_DATA.update(new_cache); await save_nodes_cache(); safe_notify("缓存已恢复", 'positive')
                            
                            if count > 0 or (import_cache_chk.value and new_cache):
                                await save_servers(); render_sidebar_content.refresh(); safe_notify(f"操作完成，恢复 {count} 个服务器", 'positive'); d.close()
                            else: safe_notify("未发现新数据", 'warning')
                        except Exception as e: safe_notify(f"错误: {e}", 'negative')

                    ui.button('执行恢复', icon='restore', on_click=process_json_import).classes('w-full bg-green-600 text-white')
                
                ui.separator().classes('my-4')
                
                # 方式二：批量 URL 添加
                with ui.column().classes('w-full gap-4'):
                    ui.label('方式二：批量 URL 添加 (.txt)').classes('font-bold text-gray-700 text-lg')
                    
                    async def open_url_import_sub_dialog():
                        with ui.dialog() as sub_d, ui.card().classes('w-2/3 max-w-2xl p-6 flex flex-col gap-4'):
                            ui.label('批量添加服务器').classes('text-lg font-bold')
                            ui.label('请粘贴面板地址，或上传包含地址的 TXT 文件 (每行一个)').classes('text-xs text-gray-400')
                            
                            url_area = ui.textarea(placeholder='http://1.1.1.1:54321\nhttps://example.com:2053').classes('w-full h-48 font-mono text-sm')
                            
                            async def handle_txt_upload(e):
                                content = e.content.read().decode('utf-8')
                                url_area.set_value(content)
                                safe_notify("TXT 文件已读取", "positive")
                                
                            ui.upload(on_upload=handle_txt_upload, auto_upload=True, label='上传 TXT 文件').classes('w-full')

                            with ui.row().classes('w-full gap-4'):
                                def_user = ui.input('统一账号', value='admin').classes('flex-grow')
                                def_pass = ui.input('统一密码', value='admin').classes('flex-grow')
                            
                            async def run_url_import():
                                raw_urls = url_area.value.strip().split('\n')
                                if not raw_urls: return
                                count = 0; existing = {s['url'] for s in SERVERS_CACHE}
                                for u in raw_urls:
                                    u = u.strip()
                                    if not u: continue
                                    if '://' not in u: u = f'http://{u}'
                                    if u not in existing:
                                        try: name = urlparse(u).hostname or u
                                        except: name = u
                                        SERVERS_CACHE.append({'name': name, 'group': '默认分组', 'url': u, 'user': def_user.value, 'pass': def_pass.value, 'prefix': ''})
                                        existing.add(u); count += 1
                                if count > 0: await save_servers(); render_sidebar_content.refresh(); safe_notify(f"成功添加 {count} 个服务器", 'positive'); sub_d.close(); d.close()
                                else: safe_notify("未添加任何服务器", 'warning')
                            ui.button('确认添加', on_click=run_url_import).classes('w-full bg-blue-600 text-white')
                        sub_d.open()
                    ui.button('打开批量添加窗口', icon='playlist_add', on_click=open_url_import_sub_dialog).classes('w-full outline-blue-600 text-blue-600')
    d.open()

class InboundEditor:
    def __init__(self, mgr, data=None, on_success=None):
        self.mgr=mgr; self.cb=on_success; self.is_edit=data is not None
        if not data: self.d={"enable":True,"remark":"","port":50000,"protocol":"vmess","settings":{"clients":[{"id":str(uuid.uuid4()),"alterId":0}],"disableInsecureEncryption":False},"streamSettings":{"network":"tcp","security":"none"}}
        else: self.d=data.copy()
        if isinstance(self.d.get('settings'), str): self.d['settings']=json.loads(self.d['settings'])
        if isinstance(self.d.get('streamSettings'), str): self.d['streamSettings']=json.loads(self.d['streamSettings'])
    def ui(self, d):
        with ui.card().classes('w-2/3 max-w-5xl p-6 flex flex-col gap-4'):
            ui.label('编辑节点').classes('text-xl font-bold mb-4')
            with ui.row().classes('w-full gap-4'):
                self.rem=ui.input('备注', value=self.d.get('remark')).classes('flex-grow'); self.ena=ui.switch('启用', value=self.d.get('enable',True)).classes('mt-2')
            with ui.row().classes('w-full gap-4'):
                self.pro=ui.select(['vmess','vless','trojan','shadowsocks'], value=self.d['protocol'], on_change=self.refresh_auth).classes('w-1/3')
                self.prt=ui.number('端口', value=self.d['port'], format='%.0f').classes('w-1/3')
            ui.separator().classes('my-4'); self.auth_box=ui.column().classes('w-full'); self.refresh_auth(); ui.separator().classes('my-4')
            with ui.row().classes('w-full gap-4'):
                st=self.d.get('streamSettings',{})
                self.net=ui.select(['tcp','ws','grpc'], value=st.get('network','tcp'), label='传输').classes('w-1/3')
                self.sec=ui.select(['none','tls'], value=st.get('security','none'), label='安全').classes('w-1/3')
            with ui.row().classes('w-full justify-end mt-6'): ui.button('保存', on_click=lambda: self.save(d)).props('color=primary')
    def refresh_auth(self, e=None):
        self.auth_box.clear()
        with self.auth_box:
            p=self.pro.value; s=self.d.get('settings',{})
            if p in ['vmess','vless']: 
                cid=s.get('clients',[{}])[0].get('id', str(uuid.uuid4()))
                ui.input('UUID', value=cid).classes('w-full').on_value_change(lambda e: s['clients'][0].update({'id':e.value}))
            elif p=='trojan': 
                pwd=s.get('clients',[{}])[0].get('password', '')
                ui.input('密码', value=pwd).classes('w-full').on_value_change(lambda e: s['clients'][0].update({'password':e.value}))
    def save(self, d):
        self.d['remark']=self.rem.value; self.d['enable']=self.ena.value; self.d['port']=int(self.prt.value); self.d['protocol']=self.pro.value
        if 'streamSettings' not in self.d: self.d['streamSettings']={}
        self.d['streamSettings']['network']=self.net.value; self.d['streamSettings']['security']=self.sec.value
        if self.is_edit: self.mgr.update_inbound(self.d['id'], self.d)
        else: self.mgr.add_inbound(self.d)
        d.close(); self.cb()

async def open_inbound_dialog(mgr, data, cb):
    with ui.dialog() as d: InboundEditor(mgr, data, cb).ui(d); d.open()
async def delete_inbound(mgr, id, cb): mgr.delete_inbound(id); cb()

class SubEditor:
    def __init__(self, data=None):
        self.data = data; self.d = data.copy() if data else {'name':'','token':str(uuid.uuid4()),'nodes':[]}
        self.sel = set(self.d['nodes'])

    def ui(self, dlg):
        with ui.card().classes('w-2/3 max-w-5xl h-[80vh] flex flex-col p-6'):
            with ui.row().classes('w-full justify-between items-center mb-2'): 
                ui.label('订阅编辑器').classes('text-xl font-bold')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense')
            
            ui.input('订阅名称', value=self.d['name']).classes('w-full text-lg').on_value_change(lambda e: self.d.update({'name':e.value}))
            
            cont = ui.column().classes('w-full flex-grow overflow-y-auto border rounded-md p-2 bg-gray-50 mt-4')
            
            async def load():
                with cont: ui.spinner('dots', size='2rem').classes('self-center mt-4')
                tasks = [fetch_inbounds_safe(s, force_refresh=False) for s in SERVERS_CACHE]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                cont.clear()
                
                with cont:
                    for i, srv in enumerate(SERVERS_CACHE):
                        res = results[i]
                        if not res or isinstance(res, Exception): res = NODES_DATA.get(srv['url'], [])
                        
                        node_ids = []
                        if res:
                            node_ids = [f"{srv['url']}|{n['id']}" for n in res]
                        
                        def toggle_all(e, ids=node_ids):
                            if e.value: self.sel.update(ids)
                            else: self.sel.difference_update(ids)
                            safe_notify(f"已{'全选' if e.value else '取消'} {srv['name']} 节点", "info")

                        with ui.expansion('', icon='dns').classes('w-full bg-white mb-1 shadow-sm rounded border').props('default-opened header-class="font-bold text-slate-700"') as exp:
                            with exp.add_slot('header'):
                                with ui.row().classes('w-full items-center justify-between no-wrap'):
                                    ui.checkbox(srv['name'], on_change=toggle_all).props('dense').classes('font-bold text-sm truncate flex-grow mr-2')
                                    ui.label(f"{len(res)}").classes('text-xs text-gray-400')

                            with ui.column().classes('w-full p-2 pl-4 gap-2'):
                                if not res: ui.label('无可用节点').classes('text-gray-400 text-sm')
                                else:
                                    with ui.column().classes('w-full gap-2'):
                                        for n in res:
                                            k = f"{srv['url']}|{n['id']}"
                                            ui.checkbox(n['remark'], value=(k in self.sel), on_change=lambda e, k=k: self.sel.add(k) if e.value else self.sel.discard(k)).props('dense').classes('text-sm w-full truncate')
            
            asyncio.create_task(load())
            
            async def save():
                self.d['nodes'] = list(self.sel)
                if self.data: 
                    for i, s in enumerate(SUBS_CACHE):
                        if s['token'] == self.data['token']: SUBS_CACHE[i] = self.d
                else: SUBS_CACHE.append(self.d)
                await save_subs(); await load_subs_view(); dlg.close()
            with ui.row().classes('w-full justify-end mt-4 gap-4'): ui.button('保存', on_click=save).props('color=primary')

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

# ================= 渲染逻辑 =================
async def refresh_content(scope='ALL', data=None, force_refresh=False):
    client = ui.context.client
    with client: show_loading(content_container)
    
    if force_refresh:
        count = len(SERVERS_CACHE)
        if scope == 'GROUP': count = len([s for s in SERVERS_CACHE if s.get('group', '默认分组') == data])
        elif scope == 'SINGLE': count = 1
        safe_notify(f'正在同步 {count} 个服务器...')

    async def _render():
        await asyncio.sleep(0.1)
        targets = []
        title = ""
        if scope == 'ALL': targets = SERVERS_CACHE; title = f"🌍 所有节点 ({len(targets)})"
        elif scope == 'GROUP': targets = [s for s in SERVERS_CACHE if s.get('group', '默认分组') == data]; title = f"📁 分组: {data} ({len(targets)})"
        elif scope == 'SINGLE': targets = [data]; title = f"🖥️ {data['name']}"

        with client:
            content_container.clear()
            SERVER_UI_MAP.clear()
            
            with content_container:
                with ui.row().classes('items-center w-full mb-4 border-b pb-2'):
                    ui.label(title).classes('text-2xl font-bold'); ui.space()
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
            cols = 'grid-template-columns: 2fr 100px 100px 100px 100px 150px; align-items: center;'
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2').style(cols):
                for h in ['备注名称', '所在组', '协议', '端口', '状态', '操作']: ui.label(h).classes('w-full text-center')
            
            if not res: ui.label('暂无节点或连接失败').classes('text-gray-400 mt-4 text-center w-full'); return
            if not force_refresh: ui.label('本地缓存模式').classes('text-xs text-gray-300 w-full text-right px-2')
            
            for n in res:
                with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(cols):
                    ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-4')
                    ui.label(server_conf.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
                    ui.label(n.get('protocol', 'unknown')).classes('uppercase text-xs font-bold w-full text-center')
                    ui.label(str(n.get('port', 0))).classes('text-blue-600 font-mono w-full text-center')
                    with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle', color='green' if n.get('enable') else 'red').props('size=xs')
                    
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
            cols = 'grid-template-columns: 150px 2fr 100px 80px 80px 80px 150px; align-items: center;'
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2 bg-gray-50').style(cols):
                for h in ['服务器', '备注名称', '所在组', '协议', '端口', '状态', '操作']: ui.label(h).classes('w-full text-center')
            
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
                        with ui.element('div').classes('grid w-full gap-4 py-3 border-b bg-red-50 px-2 items-center').style(cols):
                            ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-center'); ui.label('❌ 连接失败').classes('text-red-500 font-bold w-full text-center col-span-2'); ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate'); ui.label('-').classes('w-full text-center'); ui.label('-').classes('w-full text-center')
                            with ui.element('div').classes('flex justify-center w-full'): ui.icon('error', color='red').props('size=xs')
                            with ui.row().classes('gap-2 justify-center w-full'): ui.button(icon='settings', on_click=lambda s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm color=grey')
                        continue

                    for n in res:
                        try:
                            with ui.element('div').classes('grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(cols):
                                ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-center')
                                ui.label(n.get('remark', '未命名')).classes('font-bold truncate w-full text-left pl-4')
                                ui.label(srv.get('group', '默认分组')).classes('text-xs text-gray-500 w-full text-center truncate')
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
        online_servers = 0; total_nodes = 0; total_traffic_bytes = 0; server_traffic_map = {}; 
        protocol_count = {} 
        
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
            ui.label('系统概览').classes('text-2xl font-bold mb-4')
            with ui.row().classes('w-full gap-4 mb-6'):
                def stat_card(title, value, sub_text, icon, color_cls):
                    with ui.card().classes('flex-1 p-4 shadow-sm border border-gray-100 hover:shadow-md transition'):
                        with ui.row().classes('items-center justify-between w-full'):
                            with ui.column().classes('gap-1'):
                                ui.label(title).classes('text-gray-500 text-xs font-bold uppercase tracking-wider'); ui.label(str(value)).classes('text-2xl font-bold text-slate-800'); ui.label(sub_text).classes(f'{color_cls} text-xs font-bold')
                            with ui.element('div').classes(f'p-3 rounded-full {color_cls} bg-opacity-10'): ui.icon(icon).classes(f'{color_cls} text-xl')
                stat_card('服务器状态', f"{online_servers} / {total_servers}", '在线 / 总数', 'dns', 'text-blue-600')
                stat_card('节点总数', total_nodes, '个有效节点', 'hub', 'text-purple-600')
                stat_card('累计流量消耗', traffic_display, '上传 + 下载', 'bolt', 'text-green-600')
                stat_card('订阅配置', len(SUBS_CACHE), '个订阅链接', 'rss_feed', 'text-orange-600')
            with ui.row().classes('w-full gap-6 mb-6'):
                with ui.card().classes('w-2/3 p-6 shadow-sm border border-gray-100'):
                    ui.label('服务器流量排行 (GB)').classes('text-lg font-bold text-slate-800 mb-4')
                    sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)[:15] 
                    names = [x[0] for x in sorted_traffic]; values = [round(x[1]/(1024**3), 2) for x in sorted_traffic]
                    ui.echart({'tooltip': {'trigger': 'axis'}, 'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True}, 'xAxis': {'type': 'value'}, 'yAxis': {'type': 'category', 'data': names}, 'series': [{'type': 'bar', 'data': values, 'itemStyle': {'color': '#6366f1', 'borderRadius': [0, 4, 4, 0]}, 'barWidth': 20}]}).classes('w-full h-64')
                
                with ui.card().classes('flex-grow p-6 shadow-sm border border-gray-100'):
                    ui.label('节点协议分布').classes('text-lg font-bold text-slate-800 mb-4')
                    pie_data = [{'name': k, 'value': v} for k, v in protocol_count.items()]
                    ui.echart({'tooltip': {'trigger': 'item'}, 'legend': {'bottom': '0%'}, 'series': [{'name': '协议', 'type': 'pie', 'radius': ['40%', '70%'], 'avoidLabelOverlap': False, 'itemStyle': {'borderRadius': 10, 'borderColor': '#fff', 'borderWidth': 2}, 'label': {'show': False, 'position': 'center'}, 'emphasis': {'label': {'show': True, 'fontSize': '20', 'fontWeight': 'bold'}}, 'labelLine': {'show': False}, 'data': pie_data}]}).classes('w-full h-64')
    asyncio.create_task(_render())

@ui.refreshable
def render_sidebar_content():
    with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0'):
        ui.label('X-UI 面板').classes('text-xl font-bold mb-4 text-slate-800')
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
    
    # 底部数据管理按钮
    with ui.column().classes('w-full p-2 border-t mt-auto'):
        ui.button('数据备份 / 恢复', icon='save', on_click=open_data_mgmt_dialog).props('flat align=left').classes('w-full text-slate-600 text-sm')

@ui.page('/login')
def login_page():
    def try_login():
        if username.value == ADMIN_USER and password.value == ADMIN_PASS:
            app.storage.user['authenticated'] = True
            ui.navigate.to('/') 
        else:
            ui.notify('账号或密码错误', color='negative')

    with ui.card().classes('absolute-center w-80 p-6'):
        ui.label('请登录').classes('text-xl font-bold mb-4 w-full text-center')
        username = ui.input('账号').classes('w-full mb-2')
        password = ui.input('密码', password=True).classes('w-full mb-4').on('keydown.enter', try_login)
        ui.button('登录', on_click=try_login).classes('w-full')

@ui.page('/')
def main_page():
    if not app.storage.user.get('authenticated', False):
        return RedirectResponse('/login')

    with ui.header().classes('bg-slate-900 text-white h-14'):
        with ui.row().classes('w-full items-center justify-between'):
            ui.label('X-UI Manager Pro').classes('text-lg font-bold ml-4')
            ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round dense')

    global content_container
    with ui.row().classes('w-full h-screen gap-0'):
        with ui.column().classes('w-80 h-full border-r pr-0 overflow-hidden'):
            render_sidebar_content()
        content_container = ui.column().classes('flex-grow h-full pl-6 overflow-y-auto p-4 bg-slate-50')
    
    ui.timer(0.1, lambda: asyncio.create_task(load_dashboard_stats()), once=True)
    logger.info("✅ UI 已就绪")

if __name__ in {"__main__", "__mp_main__"}:
    logger.info("🚀 系统正在初始化...")
    ui.run(title='X-UI Pro', host='0.0.0.0', port=8080, language='zh-CN', storage_secret='sijuly_secret_key', reload=False)
