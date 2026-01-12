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
