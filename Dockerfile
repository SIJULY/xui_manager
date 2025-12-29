# 通用配置：监听 80 端口 (用户配置域名时只需修改 :80 为 example.com)
:80 {
    # 1. 拦截订阅转换请求 -> 转发给 subconverter 容器
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy subconverter:25500
    }

    # 2. 其他请求 -> 转发给 xui_manager 容器
    handle {
        reverse_proxy xui_manager:8080
    }
}
