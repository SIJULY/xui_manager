# 🌐 X-UI Manager Pro

![Docker](https://img.shields.io/badge/Docker-Enabled-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-yellow)
![License](https://img.shields.io/badge/License-MIT-green)

**X-UI Manager Pro** 是一个现代化的 X-UI 多面板集中管理系统。通过统一的 Web 界面，你可以轻松管理分布在不同服务器上的 X-UI 面板，实现节点同步、流量监控、分组订阅管理以及批量操作。

---

## ✨ 核心功能

* **📊 可视化仪表盘**：实时展示在线服务器、节点总数、总流量消耗及协议分布。
* **🔗 聚合与分组订阅**：自动生成包含所有节点的聚合订阅，或按分组生成专属订阅链接。
* **☁️ 集中管理**：支持无限添加 X-UI 面板地址，自动同步节点状态与配置。
* **💾 数据备份**：支持 JSON 全量备份与恢复，支持批量导入 URL。
* **🛡️ 稳定运行**：基于 Docker 容器化部署，自动保活。

---

## 🚀 快速安装

推荐使用 Docker 一键启动，无需配置 Python 环境。



复制以下命令在服务器执行即可（需提前安装 Docker）：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/SIJULY/xui_manager/main/install.sh)
```


## 🛠️ 特殊说明

###  选择「IP + 端口」模式的用户，后续按照如下方式进行反代配置
* 方案 A：Nginx部署修改
  Nginx 做反向代理，需要在配置文件（server 块）里加上这个 location：

```bash
server {
    listen 80;
    server_name example.com;

    # 1. 订阅转换 (关键配置)
    # 注意：proxy_pass 结尾必须带 /sub，这样才能把 /convert 替换掉
    location /convert {
        # 假设 subconverter 运行在本地 25500
        proxy_pass http://127.0.0.1:25500/sub;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # 2. 主面板
    location / {
        proxy_pass http://127.0.0.1:8081; # 你的面板端口
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # WebSocket 支持 (NiceGUI 需要)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```
* B：已有 Caddy 的用户 (手动配置)
如果您已经安装了 Caddy（非脚本安装），请根据您的需求修改您的 /etc/caddy/Caddyfile（或其他目录）。
场景
1：使用全新的子域名（推荐） 请在 Caddyfile 的末尾追加以下内容：
```bash
# 请将 xui.yourdomain.com 替换为您实际用于面板的域名
xui.yourdomain.com {
    # 1. 拦截订阅转换请求 (转发给 SubConverter)
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy 127.0.0.1:25500
    }

    # 2. 面板主体 (转发给 Python 面板)
    handle {
        reverse_proxy 127.0.0.1:8081
    }
}
```
场景 2：想把面板挂在现有域名的子路径下（高级） 如果您想通过 blog.com/panel 这种方式访问，请在您现有的站点配置块中插入：
```bash
your-existing-site.com {
    # ... 您原有的配置 ...

    # 1. 订阅转换
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy 127.0.0.1:25500
    }

    # 2. X-UI 面板 (假设挂载在 /panel 路径)
    # 注意：这需要面板本身支持 Base Path 设置，否则可能会有静态资源路径问题
    # 如果面板不支持 Base Path，建议使用场景 1
    handle_path /panel* {
        reverse_proxy 127.0.0.1:8081
    }
}
```


## 🛠️ 管理命令

* 查看日志：

```bash
 docker logs -f --tail 100 xui_manager
```
* 重启服务：
```bash
cd /root/xui_manager && docker compose up -d --build
```


## 📂 数据目录说明

程序启动后会自动在当前目录生成 data 文件夹，包含以下关键文件：

data/servers.json: 面板服务器列表

data/subscriptions.json: 订阅配置

data/nodes_cache.json: 节点缓存数据

⚠️ 注意：请定期备份 data 目录以防数据丢失。

<img width="1908" height="1546" alt="image" src="https://github.com/user-attachments/assets/8ef99f70-bbd4-4565-ae87-836b2c999204" />




## 如果觉得项目有用，请点个 ⭐ Star 支持一下！
