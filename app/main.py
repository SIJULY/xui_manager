# ================= ✨✨✨ 高性能渲染函数 (3D 卡片版) ✨✨✨ =================
async def render_aggregated_view(server_list, show_ping=False, force_refresh=False, token=None):
    # 如果强制刷新，后台触发一下数据更新，但不阻塞当前 UI 渲染
    if force_refresh:
        asyncio.create_task(asyncio.gather(*[fetch_inbounds_safe(s, force_refresh=True) for s in server_list], return_exceptions=True))

    # 修改 1: 增加容器内边距，给 3D 卡片留出阴影空间
    list_container = ui.column().classes('w-full gap-3 p-1')
    
    # 定义布局样式
    is_all_servers = (len(server_list) == len(SERVERS_CACHE) and not show_ping)
    use_special_mode = is_all_servers or show_ping
    current_css = COLS_SPECIAL_WITH_PING if use_special_mode else COLS_NO_PING

    list_container.clear()
    with list_container:
        # 1. 绘制静态表头 (为了对齐卡片内容，增加 px-4)
        with ui.element('div').classes('grid w-full gap-4 font-bold text-gray-400 border-b pb-2 px-6 mb-1 uppercase tracking-wider text-xs').style(current_css):
            ui.label('服务器').classes('text-left pl-1')
            ui.label('备注名称').classes('text-left pl-1')
            if use_special_mode: ui.label('在线状态').classes('text-center')
            else: ui.label('所在组').classes('text-center')
            ui.label('已用流量').classes('text-center')
            ui.label('协议').classes('text-center')
            ui.label('端口').classes('text-center')
            if not use_special_mode: ui.label('状态').classes('text-center')
            ui.label('操作').classes('text-center')
        
        # 2. 遍历服务器，绘制每一行 (3D 卡片化)
        for srv in server_list:
            
            # ✨✨✨ 3D 卡片核心样式 ✨✨✨
            card_cls = (
                'grid w-full gap-4 py-3 px-4 items-center group relative '
                'bg-white rounded-xl border border-gray-200 border-b-[3px] '  # 3D 基础结构
                'shadow-sm transition-all duration-150 ease-out '  # 动画过渡
                'hover:shadow-md hover:border-blue-300 hover:-translate-y-[2px] '  # 悬停上浮
                'active:border-b active:translate-y-[2px] active:shadow-none '  # 点击下沉
            )
            
            row_card = ui.element('div').classes(card_cls).style(current_css)
            
            with row_card:
                # --- 静态内容 ---
                ui.label(srv.get('name', '未命名')).classes('text-xs text-gray-400 truncate w-full text-left pl-2 font-mono')
                
                # --- 动态内容 (Label 占位) ---
                
                # 1. 备注名
                lbl_remark = ui.label('Loading...').classes('font-bold truncate w-full text-left pl-2 text-slate-700')
                
                # 2. 分组或在线状态
                if use_special_mode:
                    with ui.row().classes('w-full justify-center items-center gap-1'):
                        icon_status = ui.icon('bolt').classes('text-gray-300 text-sm')
                        lbl_ip = ui.label(get_real_ip_display(srv['url'])).classes('text-xs font-mono text-gray-500 font-bold bg-gray-100 px-1.5 rounded')
                        bind_ip_label(srv['url'], lbl_ip) # 绑定 DNS 更新
                else:
                    lbl_group = ui.label(srv.get('group', '默认分组')).classes('text-xs font-bold text-gray-500 w-full text-center truncate bg-gray-50 px-2 py-0.5 rounded-full')

                # 3. 流量
                lbl_traffic = ui.label('--').classes('text-xs text-gray-600 w-full text-center font-mono font-bold')
                
                # 4. 协议 & 端口
                lbl_proto = ui.label('--').classes('uppercase text-[10px] font-black w-fit mx-auto px-1.5 py-0.5 rounded bg-slate-100 text-slate-500')
                lbl_port = ui.label('--').classes('text-blue-600 font-mono w-full text-center font-bold text-xs')

                # 5. 状态圆点 (非特殊模式下)
                icon_dot = None
                if not use_special_mode:
                    with ui.element('div').classes('flex justify-center w-full'): 
                        icon_dot = ui.element('div').classes('w-2 h-2 rounded-full bg-gray-300 shadow-sm')
                
                # 6. 操作按钮 (扁平化圆形按钮)
                with ui.row().classes('gap-1 justify-center w-full no-wrap'):
                    
                    def make_handlers(current_s):
                        # A. 复制链接
                        async def on_copy_link():
                            nodes = NODES_DATA.get(current_s['url'], [])
                            if nodes:
                                await safe_copy_to_clipboard(generate_node_link(nodes[0], current_s['url']))
                            else:
                                safe_notify('暂无节点数据', 'warning')
                        
                        # B. 复制明文
                        async def on_copy_text():
                            nodes = NODES_DATA.get(current_s['url'], [])
                            if nodes:
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

                    h_copy, h_text = make_handlers(srv)

                    # 1. 复制链接
                    ui.button(icon='content_copy', on_click=h_copy).props('flat dense size=sm round').tooltip('复制链接 (Base64)').classes('text-gray-500 hover:text-blue-600 hover:bg-blue-50')
                    
                    # 2. 复制明文
                    ui.button(icon='description', on_click=h_text).props('flat dense size=sm round').tooltip('复制明文配置 (Surge/Loon)').classes('text-gray-500 hover:text-purple-600 hover:bg-purple-50')
                    
                    # 3. 详情/删除 (使用 Settings 图标)
                    ui.button(icon='settings', on_click=lambda s=srv: refresh_content('SINGLE', s)).props('flat dense size=sm round').tooltip('服务器详情/删除').classes('text-gray-500 hover:text-slate-800 hover:bg-slate-100')

            # ================= 内部闭包更新函数 =================
            def update_row(_srv=srv, _lbl_rem=lbl_remark, _lbl_tra=lbl_traffic, 
                           _lbl_pro=lbl_proto, _lbl_prt=lbl_port, _icon_dot=icon_dot, 
                           _icon_stat=icon_status if use_special_mode else None):
                
                nodes = NODES_DATA.get(_srv['url'], [])
                
                if not nodes:
                    is_probe = _srv.get('probe_installed', False)
                    msg = '同步中...' if not is_probe else '离线/无节点'
                    _lbl_rem.set_text(msg)
                    _lbl_rem.classes(replace='text-gray-400' if not is_probe else 'text-red-400', remove='text-slate-700')
                    _lbl_tra.set_text('--')
                    _lbl_pro.set_text('UNK')
                    _lbl_prt.set_text('--')
                    if _icon_stat: _icon_stat.classes(replace='text-red-300')
                    if _icon_dot: _icon_dot.classes(replace='bg-gray-300')
                    return

                n = nodes[0]
                total_traffic = sum(x.get('up',0) + x.get('down',0) for x in nodes)
                
                _lbl_rem.set_text(n.get('remark', '未命名'))
                _lbl_rem.classes(replace='text-slate-700', remove='text-gray-400 text-red-400')
                
                _lbl_tra.set_text(format_bytes(total_traffic))
                _lbl_pro.set_text(n.get('protocol', 'unk').upper())
                _lbl_prt.set_text(str(n.get('port', 0)))

                is_online = _srv.get('_status') == 'online'
                is_enable = n.get('enable', True)
                
                if use_special_mode and _icon_stat:
                    color = 'text-green-500' if is_online else 'text-red-500'
                    if not _srv.get('probe_installed'): color = 'text-orange-400'
                    _icon_stat.classes(replace=color, remove='text-gray-300')
                
                if not use_special_mode and _icon_dot:
                    # 动态颜色和光晕
                    color_cls = "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]" if is_enable else "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]"
                    _icon_dot.classes(replace=color_cls, remove="bg-gray-300 shadow-sm")

            ui.timer(2.0, update_row)
            update_row()
