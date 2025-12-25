import json
import os
import uuid
import base64
import asyncio
import logging
import requests
import urllib3
from urllib.parse import urlparse
from nicegui import ui, run, app
from fastapi import Response

# --- 日志配置 ---
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_FILE = 'servers.json'
SUBS_FILE = 'subscriptions.json'

# --- 全局内存缓存 ---
SERVERS_CACHE = []
SUBS_CACHE = []
LOCATION_CACHE = {}

# 全局 UI 容器引用
sidebar_container = None
content_container = None


def init_data():
    global SERVERS_CACHE, SUBS_CACHE
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                SERVERS_CACHE = json.load(f)
        except:
            SERVERS_CACHE = []
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                SUBS_CACHE = json.load(f)
        except:
            SUBS_CACHE = []


def save_data_sync(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


async def save_servers(): await run.io_bound(save_data_sync, CONFIG_FILE, SERVERS_CACHE)


async def save_subs(): await run.io_bound(save_data_sync, SUBS_FILE, SUBS_CACHE)


init_data()
managers = {}


# =========================================================
# 1. 网络请求核心
# =========================================================
class XUIManager:
    def __init__(self, url, username, password, api_prefix=None):
        self.original_url = url.rstrip('/')
        self.url = self.original_url
        self.username = username
        self.password = password
        self.api_prefix = api_prefix
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36'})
        self.session.verify = False
        self.login_path = None

    def _request(self, method, path, **kwargs):
        target_url = f"{self.url}{path}"
        try:
            if method == 'POST':
                return self.session.post(target_url, timeout=5, allow_redirects=False, **kwargs)
            else:
                return self.session.get(target_url, timeout=5, allow_redirects=False, **kwargs)
        except:
            return None

    def login(self):
        if self.login_path: return self._try_login_at(self.login_path)
        paths = ['/login', '/xui/login', '/panel/login']
        if self.api_prefix: paths.insert(0, f"{self.api_prefix.rstrip('/')}/login")

        protocols = [self.original_url]
        if self.original_url.startswith('http://'):
            protocols.append(self.original_url.replace('http://', 'https://'))
        elif self.original_url.startswith('https://'):
            protocols.append(self.original_url.replace('https://', 'http://'))

        for proto_url in protocols:
            self.url = proto_url
            for path in paths:
                if self._try_login_at(path):
                    self.login_path = path
                    return True
        return False

    def _try_login_at(self, path):
        try:
            data = {'username': self.username, 'password': self.password}
            r = self._request('POST', path, data=data)
            if r and r.status_code == 200:
                try:
                    if r.json().get('success') == True: return True
                except:
                    pass
            return False
        except:
            return False

    def get_inbounds(self):
        if not self.login(): return None
        candidates = []
        if self.login_path: candidates.append(self.login_path.replace('login', 'inbound/list'))
        defaults = ['/xui/inbound/list', '/panel/inbound/list', '/inbound/list']
        for d in defaults:
            if d not in candidates: candidates.append(d)
        for path in candidates:
            r = self._request('POST', path)
            if r and r.status_code == 200:
                try:
                    res = r.json()
                    if res.get('success'): return res.get('obj')
                except:
                    pass
        return None

    def _action(self, suffix, data):
        if not self.login(): return False, "登录失败"
        base = self.login_path.replace('/login', '/inbound')
        r = self._request('POST', f"{base}{suffix}", json=data)
        if r:
            try:
                return r.json().get('success'), r.json().get('msg')
            except:
                return False, "解析响应失败"
        return False, "请求失败"

    def add_inbound(self, data):
        return self._action('/add', data)

    def update_inbound(self, iid, data):
        return self._action(f'/update/{iid}', data)

    def delete_inbound(self, iid):
        return self._action(f'/del/{iid}', {})


def get_manager(server_conf):
    key = server_conf['url']
    if key not in managers or managers[key].username != server_conf['user']:
        managers[key] = XUIManager(server_conf['url'], server_conf['user'], server_conf['pass'],
                                   server_conf.get('prefix'))
    return managers[key]


async def fetch_inbounds_safe(server_conf):
    try:
        mgr = get_manager(server_conf)
        inbounds = await run.io_bound(mgr.get_inbounds)
        if inbounds is None:
            mgr = managers[server_conf['url']] = XUIManager(server_conf['url'], server_conf['user'],
                                                            server_conf['pass'], server_conf.get('prefix'))
            inbounds = await run.io_bound(mgr.get_inbounds)
        return inbounds
    except Exception as e:
        return Exception(str(e))


def safe_base64(s): return base64.b64encode(s.encode('utf-8')).decode('utf-8')


def generate_node_link(node, server_host):
    try:
        p = node['protocol'];
        remark = node['remark'];
        port = node['port']
        add = node.get('listen') if node.get('listen') else server_host
        s = json.loads(node['settings']) if isinstance(node['settings'], str) else node['settings']
        st = json.loads(node['streamSettings']) if isinstance(node['streamSettings'], str) else node['streamSettings']
        net = st.get('network', 'tcp');
        tls = st.get('security', 'none');
        path = "";
        host = ""
        if net == 'ws':
            path = st.get('wsSettings', {}).get('path', '/'); host = st.get('wsSettings', {}).get('headers', {}).get(
                'Host', '')
        elif net == 'grpc':
            path = st.get('grpcSettings', {}).get('serviceName', '')

        if p == 'vmess':
            v = {"v": "2", "ps": remark, "add": add, "port": port, "id": s['clients'][0]['id'], "aid": "0",
                 "scy": "auto", "net": net, "type": "none", "host": host, "path": path, "tls": tls}
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
    except:
        return ""
    return ""


@app.get('/sub/{token}')
def sub_handler(token: str):
    sub = next((s for s in SUBS_CACHE if s['token'] == token), None)
    if not sub: return Response("Invalid Token", 404)
    links = []
    for srv in SERVERS_CACHE:
        try:
            mgr = get_manager(srv);
            inbounds = mgr.get_inbounds()
            if not inbounds: continue
            raw_url = mgr.url if mgr.url else srv['url']
            if '://' not in raw_url: raw_url = f'http://{raw_url}'
            host = urlparse(raw_url).hostname or raw_url.split('://')[-1].split(':')[0]
            for n in inbounds:
                if f"{srv['url']}|{n['id']}" in sub.get('nodes', []):
                    l = generate_node_link(n, host);
                    if l: links.append(l)
        except:
            continue
    return Response(safe_base64("\n".join(links)), media_type="text/plain; charset=utf-8")


# =========================================================
# 2. 辅助功能：地理位置查询
# =========================================================

async def get_country_from_ip(host):
    if host in LOCATION_CACHE: return LOCATION_CACHE[host]
    if host.startswith('192.168') or host.startswith('10.') or host in ['localhost', '127.0.0.1']:
        return "本地"

    def _fetch():
        try:
            r = requests.get(f'http://ip-api.com/json/{host}?lang=zh-CN', timeout=2)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 'success':
                    return data.get('country', '未知')
        except:
            pass
        return "未知区域"

    country = await run.io_bound(_fetch)
    LOCATION_CACHE[host] = country
    return country


# =========================================================
# 3. 弹窗组件
# =========================================================

async def refresh_content_wrapper(scope, data):
    await refresh_content(scope, data)


async def open_add_server_dialog(cb):
    with ui.dialog() as d, ui.card().classes('w-96'):
        ui.label('添加服务器').classes('text-lg font-bold')
        n = ui.input('名称').classes('w-full');
        g = ui.input('分组 (选填)').classes('w-full');
        u = ui.input('URL').classes('w-full')
        us = ui.input('账号').classes('w-full');
        p = ui.input('密码', password=True).classes('w-full')
        pre = ui.input('API前缀 (选填)', placeholder='如: /xui').classes('w-full')

        async def save():
            SERVERS_CACHE.append({'name': n.value, 'group': g.value, 'url': u.value, 'user': us.value, 'pass': p.value,
                                  'prefix': pre.value})
            await save_servers();
            refresh_sidebar();
            d.close()

        ui.button('保存', on_click=save).classes('w-full mt-4 color-green')
    d.open()


async def open_edit_server_dialog(idx):
    data = SERVERS_CACHE[idx]
    with ui.dialog() as d, ui.card().classes('w-96'):
        ui.label('编辑配置').classes('text-lg font-bold')
        n = ui.input('名称', value=data['name']).classes('w-full');
        g = ui.input('分组', value=data.get('group', '')).classes('w-full')
        u = ui.input('URL', value=data['url']).classes('w-full');
        us = ui.input('账号', value=data['user']).classes('w-full')
        p = ui.input('密码', value=data['pass'], password=True).classes('w-full')
        pre = ui.input('API前缀', value=data.get('prefix', '')).classes('w-full')

        async def save():
            SERVERS_CACHE[idx] = {'name': n.value, 'group': g.value, 'url': u.value, 'user': us.value, 'pass': p.value,
                                  'prefix': pre.value}
            await save_servers();
            refresh_sidebar();
            refresh_content('SINGLE', SERVERS_CACHE[idx])
            d.close()

        async def delete():
            del SERVERS_CACHE[idx];
            await save_servers();
            refresh_sidebar();
            refresh_content('ALL');
            d.close()

        with ui.row().classes('w-full justify-end mt-4'):
            ui.button('删除', on_click=delete, color='red').props('flat');
            ui.button('保存', on_click=save)
    d.open()


async def open_group_mgmt_dialog(group_name):
    with ui.dialog() as d, ui.card().classes('w-[500px] h-[70vh] flex flex-col p-0 gap-0'):
        with ui.row().classes('w-full p-4 border-b bg-gray-50 justify-between items-center'):
            ui.label(f'管理分组: {group_name}').classes('text-lg font-bold')
            new_name = ui.input('重命名组', value=group_name).props('dense').classes('w-32')
        ui.label('请勾选属于此分组的服务器:').classes('p-2 text-xs text-gray-500 bg-white')
        sel_urls = {s['url'] for s in SERVERS_CACHE if s.get('group') == group_name}
        with ui.column().classes('w-full flex-grow overflow-y-auto p-0 gap-0'):
            for i, s in enumerate(SERVERS_CACHE):
                def toggle(e, u=s['url']):
                    if e.value:
                        sel_urls.add(u)
                    else:
                        sel_urls.discard(u)

                bg_color = 'bg-white' if i % 2 == 0 else 'bg-gray-50'
                with ui.row().classes(
                        f'w-full items-center px-4 py-2 {bg_color} hover:bg-blue-50 border-b border-gray-100'):
                    ui.checkbox(s['name'], value=(s['url'] in sel_urls), on_change=toggle).classes('w-full')
        with ui.row().classes('w-full p-4 border-t bg-gray-50 justify-end'):
            async def save():
                for s in SERVERS_CACHE:
                    if s['url'] in sel_urls:
                        s['group'] = new_name.value
                    elif s.get('group') == group_name:
                        s['group'] = '默认分组'
                await save_servers()
                refresh_sidebar();
                refresh_content('GROUP', new_name.value);
                d.close()

            ui.button('取消', on_click=d.close).props('flat')
            ui.button('保存更改', on_click=save).classes('bg-primary text-white')
    d.open()


async def open_create_group_dialog():
    with ui.dialog() as d, ui.card().classes('w-80'):
        ui.label('新建分组').classes('text-lg font-bold mb-2')
        name_input = ui.input('分组名称', placeholder='例如: 亚洲节点').classes('w-full')

        async def confirm():
            if name_input.value:
                d.close();
                await open_group_mgmt_dialog(name_input.value)
            else:
                ui.notify('请输入分组名称', type='warning')

        ui.button('下一步: 选择服务器', on_click=confirm).classes('w-full mt-4 color-primary')
    d.open()


class InboundEditor:
    def __init__(self, mgr, data=None, on_success=None):
        self.mgr = mgr;
        self.cb = on_success;
        self.is_edit = data is not None
        if not data:
            self.d = {"enable": True, "remark": "", "port": 50000, "protocol": "vmess",
                      "settings": {"clients": [{"id": str(uuid.uuid4()), "alterId": 0}],
                                   "disableInsecureEncryption": False},
                      "streamSettings": {"network": "tcp", "security": "none"}}
        else:
            self.d = data.copy()
        if isinstance(self.d.get('settings'), str): self.d['settings'] = json.loads(self.d['settings'])
        if isinstance(self.d.get('streamSettings'), str): self.d['streamSettings'] = json.loads(
            self.d['streamSettings'])

    def ui(self, d):
        with ui.card().classes('w-full max-w-2xl p-6'):
            ui.label('编辑节点').classes('text-xl font-bold mb-4')
            with ui.row().classes('w-full gap-4'):
                self.rem = ui.input('备注', value=self.d.get('remark')).classes('flex-grow')
                self.ena = ui.switch('启用', value=self.d.get('enable', True)).classes('mt-2')
            with ui.row().classes('w-full gap-4'):
                self.pro = ui.select(['vmess', 'vless', 'trojan', 'shadowsocks'], value=self.d['protocol'],
                                     on_change=self.refresh_auth).classes('w-1/3')
                self.prt = ui.number('端口', value=self.d['port'], format='%.0f').classes('w-1/3')
            ui.separator().classes('my-4');
            self.auth_box = ui.column().classes('w-full');
            self.refresh_auth();
            ui.separator().classes('my-4')
            with ui.row().classes('w-full gap-4'):
                st = self.d.get('streamSettings', {})
                self.net = ui.select(['tcp', 'ws', 'grpc'], value=st.get('network', 'tcp'), label='传输').classes(
                    'w-1/3')
                self.sec = ui.select(['none', 'tls'], value=st.get('security', 'none'), label='安全').classes('w-1/3')
            with ui.row().classes('w-full justify-end mt-6'):
                ui.button('保存', on_click=lambda: self.save(d)).props('color=primary')

    def refresh_auth(self, e=None):
        self.auth_box.clear();
        p = self.pro.value;
        s = self.d.get('settings', {})
        if p in ['vmess', 'vless']:
            cid = s.get('clients', [{}])[0].get('id', str(uuid.uuid4()))
            ui.input('UUID', value=cid).classes('w-full').on_value_change(
                lambda e: s['clients'][0].update({'id': e.value}))
        elif p == 'trojan':
            pwd = s.get('clients', [{}])[0].get('password', '')
            ui.input('密码', value=pwd).classes('w-full').on_value_change(
                lambda e: s['clients'][0].update({'password': e.value}))

    def save(self, d):
        self.d['remark'] = self.rem.value;
        self.d['enable'] = self.ena.value;
        self.d['port'] = int(self.prt.value);
        self.d['protocol'] = self.pro.value
        if 'streamSettings' not in self.d: self.d['streamSettings'] = {}
        self.d['streamSettings']['network'] = self.net.value;
        self.d['streamSettings']['security'] = self.sec.value
        if self.is_edit:
            self.mgr.update_inbound(self.d['id'], self.d)
        else:
            self.mgr.add_inbound(self.d)
        d.close();
        self.cb()


async def open_inbound_dialog(mgr, data, cb):
    with ui.dialog() as d: InboundEditor(mgr, data, cb).ui(d); d.open()


async def delete_inbound(mgr, id, cb): mgr.delete_inbound(id); cb()


# =========================================================
# 4. 页面逻辑
# =========================================================

def refresh_sidebar():
    sidebar_container.clear()
    sidebar_container.classes('h-full flex flex-col p-0')

    groups = {}
    for s in SERVERS_CACHE:
        g = s.get('group', '默认分组') or '默认分组'
        groups.setdefault(g, []).append(s)

    with sidebar_container:
        with ui.column().classes('w-full p-4 border-b bg-gray-50 flex-shrink-0'):
            ui.label('X-UI 面板').classes('text-xl font-bold mb-4 text-slate-800')
            ui.button('仪表盘', icon='dashboard', on_click=lambda: asyncio.create_task(load_dashboard_stats())).props(
                'flat align=left').classes('w-full text-slate-700')
            ui.button('订阅管理', icon='rss_feed', on_click=load_subs_view).props('flat align=left').classes(
                'w-full text-slate-700')

        with ui.column().classes('w-full flex-grow overflow-y-auto p-2 gap-1'):
            ui.label('资源列表').classes('font-bold text-xs text-gray-400 mt-2 mb-1 px-2 uppercase')

            # 按钮区
            with ui.row().classes('w-full gap-2 px-1 mb-2'):
                ui.button('新建分组', icon='create_new_folder', on_click=open_create_group_dialog).props(
                    'dense unelevated').classes('flex-grow bg-blue-600 text-white text-xs')
                ui.button('添加服务器', icon='add', color='green',
                          on_click=lambda: open_add_server_dialog(lambda: refresh_sidebar())).props(
                    'dense unelevated').classes('flex-grow text-xs')

            ui.button('🌍 所有节点', icon='public', on_click=lambda: refresh_content('ALL')).props(
                'flat align=left').classes('w-full font-bold mb-2')

            for gname, gservers in groups.items():
                with ui.expansion('', icon='folder').classes('w-full border rounded mb-1 bg-white shadow-sm').props(
                        'expand-icon-class=hidden') as exp:
                    with exp.add_slot('header'):
                        with ui.row().classes('w-full items-center justify-between no-wrap'):
                            ui.label(gname).classes('flex-grow cursor-pointer font-bold truncate').on('click', lambda
                                g=gname: refresh_content('GROUP', g))
                            ui.button(icon='edit', on_click=lambda g=gname: open_group_mgmt_dialog(g)).props(
                                'flat dense round size=xs color=grey').on('click.stop')
                            ui.label(str(len(gservers))).classes('text-xs bg-gray-200 px-2 rounded-full')

                    with ui.column().classes('w-full gap-0'):
                        for srv in gservers:
                            with ui.row().classes(
                                    'w-full justify-between p-2 pl-4 cursor-pointer hover:bg-blue-50 items-center border-t border-gray-100 no-wrap'):
                                ui.label(srv['name']).classes('text-sm flex-grow truncate').on('click', lambda
                                    s=srv: refresh_content('SINGLE', s))
                                idx = SERVERS_CACHE.index(srv)
                                ui.button(icon='edit', on_click=lambda i=idx: open_edit_server_dialog(i)).props(
                                    'flat dense round size=xs color=grey')

        with ui.column().classes('w-full p-2'):
            pass


def refresh_content(scope='ALL', data=None):
    content_container.clear()
    targets = []
    title = ""
    if scope == 'ALL':
        targets = SERVERS_CACHE;
        title = f"🌍 所有节点 ({len(targets)})"
    elif scope == 'GROUP':
        targets = [s for s in SERVERS_CACHE if s.get('group', '默认分组') == data];
        title = f"📁 分组: {data}"
    elif scope == 'SINGLE':
        targets = [data];
        title = f"🖥️ {data['name']}"

    with content_container:
        with ui.row().classes('items-center w-full mb-4 border-b pb-2'):
            ui.label(title).classes('text-2xl font-bold');
            ui.space()
            ui.button('刷新列表', icon='refresh', on_click=lambda: refresh_content(scope, data)).props('outline')

        if scope == 'SINGLE':
            render_single_server_view(data)
        else:
            render_aggregated_view(targets)


def render_single_server_view(server_conf):
    mgr = get_manager(server_conf)
    with ui.row().classes('w-full mb-4 justify-end'):
        ui.button('新建节点', icon='add', color='green',
                  on_click=lambda: open_inbound_dialog(mgr, None, lambda: refresh_content('SINGLE', server_conf)))

    list_container = ui.column().classes('w-full')

    async def load_nodes():
        res = await fetch_inbounds_safe(server_conf);
        list_container.clear()
        if isinstance(res, Exception):
            with list_container: ui.label(f"连接失败: {res}").classes('text-red-500')
            return

        raw_url = mgr.url if mgr.url else server_conf['url']
        if '://' not in raw_url: raw_url = f'http://{raw_url}'
        try:
            base = f"{urlparse(raw_url).scheme}://{urlparse(raw_url).hostname}:{urlparse(raw_url).port or 80}"
        except:
            base = None

        with list_container:
            # 优化后的列布局 (无ID，有分组)
            cols = 'grid-template-columns: 1fr 100px 100px 100px 80px 100px 100px; align-items: center;'
            with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2').style(cols):
                for h in ['名称', '所在组', '协议', '端口', '状态', '延迟', '操作']: ui.label(h).classes(
                    'w-full text-center')
            if not res: ui.label('暂无节点').classes('text-gray-400 mt-4')
            for n in res:
                with ui.element('div').classes(
                        'grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(cols):
                    ui.label(n['remark']).classes('font-bold truncate w-full text-center')
                    # 显示所在组
                    ui.label(server_conf.get('group', '默认分组')).classes(
                        'text-xs text-gray-500 w-full text-center truncate')
                    ui.label(n['protocol']).classes('uppercase text-xs font-bold w-full text-center')
                    ui.label(str(n['port'])).classes('text-blue-600 font-mono w-full text-center')
                    with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle', color='green' if n[
                        'enable'] else 'red').props('size=xs')
                    lid = f"lat-{n['id']}";
                    ui.label('...').classes('text-xs text-gray-400 w-full text-center').props(f'id={lid}')
                    if base: ui.run_javascript(f"check_latency('{base}/login', '{lid}')")
                    with ui.row().classes('gap-2 justify-center w-full'):
                        ui.button(icon='edit', on_click=lambda i=n: open_inbound_dialog(mgr, i, lambda: refresh_content(
                            'SINGLE', server_conf))).props('flat dense size=sm')
                        ui.button(icon='delete', on_click=lambda i=n: delete_inbound(mgr, i['id'],
                                                                                     lambda: refresh_content('SINGLE',
                                                                                                             server_conf))).props(
                            'flat dense size=sm color=red')

    asyncio.create_task(load_nodes())


def render_aggregated_view(server_list):
    list_container = ui.column().classes('w-full gap-4')

    async def load_all():
        list_container.clear();
        tasks = [fetch_inbounds_safe(s) for s in server_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        with list_container:
            # 优化后的列布局 (无ID，有分组)
            cols = 'grid-template-columns: 150px 1fr 100px 100px 100px 80px 100px 100px; align-items: center;'
            with ui.element('div').classes(
                    'grid w-full gap-4 font-bold text-gray-500 border-b pb-2 px-2 bg-gray-50').style(cols):
                for h in ['服务器', '名称', '所在组', '协议', '端口', '状态', '延迟', '操作']: ui.label(h).classes(
                    'w-full text-center')
            has_data = False
            for i, res in enumerate(results):
                srv = server_list[i]
                if isinstance(res, Exception) or not res: continue
                has_data = True;
                mgr = get_manager(srv)
                raw_url = mgr.url if mgr.url else srv['url']
                if '://' not in raw_url: raw_url = f'http://{raw_url}'
                try:
                    base = f"{urlparse(raw_url).scheme}://{urlparse(raw_url).hostname}:{urlparse(raw_url).port or 80}"
                except:
                    base = None
                for n in res:
                    with ui.element('div').classes(
                            'grid w-full gap-4 py-3 border-b hover:bg-blue-50 transition px-2').style(cols):
                        ui.label(srv['name']).classes('text-xs text-gray-500 truncate w-full text-center')
                        ui.label(n['remark']).classes('font-bold truncate w-full text-center')
                        # 显示所在组
                        ui.label(srv.get('group', '默认分组')).classes(
                            'text-xs text-gray-500 w-full text-center truncate')
                        ui.label(n['protocol']).classes('uppercase text-xs font-bold w-full text-center')
                        ui.label(str(n['port'])).classes('text-blue-600 font-mono w-full text-center')
                        with ui.element('div').classes('flex justify-center w-full'): ui.icon('circle',
                                                                                              color='green' if n[
                                                                                                  'enable'] else 'red').props(
                            'size=xs')
                        lid = f"lat-{srv['url']}-{n['id']}";
                        ui.label('...').classes('text-xs text-gray-400 w-full text-center').props(f'id={lid}')
                        if base: ui.run_javascript(f"check_latency('{base}/login', '{lid}')")
                        with ui.row().classes('gap-2 justify-center w-full'):
                            ui.button(icon='edit', on_click=lambda m=mgr, i=n, s=srv: open_inbound_dialog(m, i,
                                                                                                          lambda: refresh_content(
                                                                                                              'SINGLE',
                                                                                                              s))).props(
                                'flat dense size=sm')
                            ui.button(icon='delete', on_click=lambda m=mgr, i=n, s=srv: delete_inbound(m, i['id'],
                                                                                                       lambda: refresh_content(
                                                                                                           'SINGLE',
                                                                                                           s))).props(
                                'flat dense size=sm color=red')
            if not has_data: ui.label('无数据').classes('text-gray-400 mt-4')

    asyncio.create_task(load_all())


class SubEditor:
    def __init__(self, data=None):
        self.d = data.copy() if data else {'name': '', 'token': str(uuid.uuid4()), 'nodes': []}
        self.sel = set(self.d['nodes'])

    def ui(self, dlg):
        with ui.card().classes('w-full max-w-4xl h-[70vh] flex flex-col'):
            ui.input('名称', value=self.d['name']).classes('w-full').on_value_change(
                lambda e: self.d.update({'name': e.value}))
            cont = ui.column().classes('flex-grow overflow-y-auto mt-4 border p-2')

            async def load():
                for srv in SERVERS_CACHE:
                    with cont:
                        with ui.expansion(srv['name']).classes('w-full').props('default-opened'):
                            res = await fetch_inbounds_safe(srv)
                            if not res or isinstance(res, Exception):
                                ui.label('连接失败').classes('text-red-500');
                                continue
                            for n in res:
                                k = f"{srv['url']}|{n['id']}"

                                def chk(e, key=k):
                                    if e.value:
                                        self.sel.add(key)
                                    else:
                                        self.sel.discard(key)

                                ui.checkbox(f"{n['remark']}", value=(k in self.sel), on_change=chk)

            asyncio.create_task(load())

            async def save():
                self.d['nodes'] = list(self.sel)
                if data:
                    for i, s in enumerate(SUBS_CACHE):
                        if s['token'] == data['token']: SUBS_CACHE[i] = self.d
                else:
                    SUBS_CACHE.append(self.d)
                await save_subs();
                await load_subs_view();
                dlg.close()

            ui.button('保存', on_click=save).classes('w-full mt-4')


def open_sub_editor(d):
    with ui.dialog() as dlg: SubEditor(d).ui(dlg); dlg.open()


async def load_subs_view():
    content_container.clear()
    with content_container:
        ui.label('订阅管理').classes('text-2xl font-bold mb-4')
        with ui.row().classes('w-full mb-4 justify-end'):
            ui.button('新建订阅', icon='add', color='green', on_click=lambda: open_sub_editor(None))

        for idx, sub in enumerate(SUBS_CACHE):
            with ui.card().classes('w-full p-4 mb-2'):
                with ui.row().classes('justify-between w-full items-center'):
                    ui.label(sub['name']).classes('font-bold text-lg')
                    with ui.row():
                        ui.button(icon='edit', on_click=lambda s=sub: open_sub_editor(s)).props('flat dense')

                        async def dl(i=idx):
                            del SUBS_CACHE[i];
                            await save_subs();
                            await load_subs_view()

                        ui.button(icon='delete', color='red', on_click=dl).props('flat dense')
                path = f"/sub/{sub['token']}"
                inp = ui.input(value=path).props('readonly').classes('w-full text-xs font-mono text-gray-500')
                ui.run_javascript(f'getElement({inp.id}).value = window.location.origin + "{path}"')


# =========================================================
# 5. 仪表盘逻辑 (V27.0 修复并发渲染 BUG)
# =========================================================

async def load_dashboard_stats():
    # 1. 后台数据准备阶段 (不要操作 content_container)
    total_servers = len(SERVERS_CACHE)
    online_servers = 0
    total_nodes = 0
    total_traffic_bytes = 0
    server_traffic_map = {}
    country_count = {}

    tasks = [fetch_inbounds_safe(s) for s in SERVERS_CACHE]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, res in enumerate(results):
        srv_name = SERVERS_CACHE[i]['name']
        srv_url = SERVERS_CACHE[i]['url']
        try:
            host = urlparse(srv_url).hostname or srv_url.split('://')[-1].split(':')[0]
        except:
            host = ""

        if isinstance(res, list):
            online_servers += 1
            total_nodes += len(res)
            srv_traffic = 0
            for n in res:
                t = n.get('up', 0) + n.get('down', 0)
                total_traffic_bytes += t
                srv_traffic += t
            server_traffic_map[srv_name] = srv_traffic
            if host:
                country = await get_country_from_ip(host)
                country_count[country] = country_count.get(country, 0) + len(res)
        else:
            server_traffic_map[srv_name] = 0

    traffic_display = f"{total_traffic_bytes / (1024 ** 3):.2f} GB"

    # 2. 最终渲染阶段 (同步清空 + 绘制，防止重叠)
    content_container.clear()
    with content_container:
        # 统计卡片
        with ui.row().classes('w-full gap-4 mb-6'):
            def stat_card(title, value, sub_text, icon, color_cls):
                with ui.card().classes('flex-1 p-4 shadow-sm border border-gray-100 hover:shadow-md transition'):
                    with ui.row().classes('items-center justify-between w-full'):
                        with ui.column().classes('gap-1'):
                            ui.label(title).classes('text-gray-500 text-xs font-bold uppercase tracking-wider')
                            ui.label(str(value)).classes('text-2xl font-bold text-slate-800')
                            ui.label(sub_text).classes(f'{color_cls} text-xs font-bold')
                        with ui.element('div').classes(f'p-3 rounded-full {color_cls} bg-opacity-10'):
                            ui.icon(icon).classes(f'{color_cls} text-xl')

            stat_card('服务器状态', f"{online_servers} / {total_servers}", '在线 / 总数', 'dns', 'text-blue-600')
            stat_card('节点总数', total_nodes, '个有效节点', 'hub', 'text-purple-600')
            stat_card('累计流量消耗', traffic_display, '上传 + 下载', 'bolt', 'text-green-600')
            stat_card('订阅配置', len(SUBS_CACHE), '个订阅链接', 'rss_feed', 'text-orange-600')

        # 图表区域
        with ui.row().classes('w-full gap-6 mb-6'):
            # 左侧：流量排行
            with ui.card().classes('w-2/3 p-6 shadow-sm border border-gray-100'):
                ui.label('服务器流量排行 (GB)').classes('text-lg font-bold text-slate-800 mb-4')
                sorted_traffic = sorted(server_traffic_map.items(), key=lambda x: x[1], reverse=True)
                names = [x[0] for x in sorted_traffic]
                values = [round(x[1] / (1024 ** 3), 2) for x in sorted_traffic]
                ui.echart({
                    'tooltip': {'trigger': 'axis'},
                    'grid': {'left': '3%', 'right': '4%', 'bottom': '3%', 'containLabel': True},
                    'xAxis': {'type': 'value'},
                    'yAxis': {'type': 'category', 'data': names},
                    'series': [
                        {'type': 'bar', 'data': values, 'itemStyle': {'color': '#6366f1', 'borderRadius': [0, 4, 4, 0]},
                         'barWidth': 20}]
                }).classes('w-full h-64')

            # 右侧：节点地区分布 Top 5
            with ui.card().classes('flex-grow p-6 shadow-sm border border-gray-100'):
                ui.label('节点地区分布 (Top 5)').classes('text-lg font-bold text-slate-800 mb-4')
                sorted_country = sorted(country_count.items(), key=lambda x: x[1], reverse=True)[:5]
                pie_data = [{'name': k, 'value': v} for k, v in sorted_country]
                ui.echart({
                    'tooltip': {'trigger': 'item'},
                    'legend': {'bottom': '0%'},
                    'series': [{
                        'name': '地区',
                        'type': 'pie',
                        'radius': [20, 100],
                        'center': ['50%', '50%'],
                        'roseType': 'area',
                        'itemStyle': {'borderRadius': 8},
                        'data': pie_data
                    }]
                }).classes('w-full h-64')


# =========================================================
# 6. 主入口
# =========================================================
@ui.page('/')
def main_page():
    ui.add_head_html('''
    <script>
    async function check_latency(url, element_id) {
        const el = document.getElementById(element_id); if(!el) return;
        const start = Date.now();
        try { await fetch(url, {mode:'no-cors', cache:'no-cache'}); 
              const lat = Date.now() - start; el.innerText = lat + ' ms';
              el.className = lat<200?"text-xs font-bold text-green-500 w-full text-center":"text-xs font-bold text-orange-500 w-full text-center";
        } catch { el.innerText='超时'; el.className="text-xs font-bold text-red-500 w-full text-center"; }
    }
    </script>
    ''')

    with ui.header().classes('bg-slate-900 text-white h-14'):
        ui.label('X-UI Manager Pro').classes('text-lg font-bold ml-4')

    global sidebar_container, content_container
    with ui.row().classes('w-full h-screen gap-0'):
        sidebar_container = ui.column().classes('w-80 h-full border-r pr-0 overflow-hidden')
        content_container = ui.column().classes('flex-grow h-full pl-6 overflow-y-auto p-4 bg-slate-50')

    refresh_sidebar()
    asyncio.create_task(load_dashboard_stats())


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title='X-UI Pro', host='0.0.0.0', port=8080, language='zh-CN', storage_secret='sijuly_secret_key')