# 🌐 X-Fusion Panel

![Docker](https://img.shields.io/badge/Docker-Enabled-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-yellow)
![License](https://img.shields.io/badge/License-MIT-green)

> **声明**：  此项目仅供个人学习、交流使用，请遵守当地法律法规，勿用于非法用途；请勿用于生产环境。

> **注意**： 在使用此项目和〔教程〕过程中，若因违反以上声明使用规则而产生的一切后果由使用者自负。

⭐ Support

如果觉得项目有用，请点个 ⭐ Star 支持一下！

---

**X-Fusion Panel**  是一个全能型 VPS 集中管理平台。它将 X-UI 面板管理、高性能 SSH 网页终端、全球节点地图监控以及订阅转换功能完美融合。通过统一的 Web 界面，您可以轻松掌控分布在世界各地的服务器。

---

## ✨ 核心功能

* **📊 全景仪表盘**：可视化展示在线服务器、节点状态、实时流量消耗及协议分布图表。
* **🌍 全球实景地图**：基于 Leaflet 的节点地图，自动定位服务器地理位置，直观监控全球资产。
* **💻 交互式 SSH 终端**：内置高性能 WebSSH 客户端（Xterm.js），支持全屏、自适应布局、文件挂载，提供类似本地终端的操作体验。
* **🔗 智能订阅管理**：
    * **聚合订阅**：自动生成包含所有节点的聚合链接。
    * **分组订阅**：支持按“国家/地区”或“自定义标签”生成专属订阅。
    * **格式转换**：内置 SubConverter，直接输出 Clash、Surge 等格式。
* **☁️ 集中管理**：支持无限添加 X-UI 面板，自动同步节点状态，支持批量操作。
* **💾 数据安全**：采取密码+MFA+IP记录三重保护措施保证面板数据安全，支持 JSON 全量备份与恢复，支持批量导入 URL，支持平滑迁移。
* **🛡️ 稳定架构**：采用 Docker 容器化部署，内置 Caddy 反代，开箱即用。

---
<img width="2146" height="1816" alt="image" src="https://github.com/user-attachments/assets/7c8b2163-50aa-4b6a-b550-8dde09e57818" />
<img width="2119" height="1393" alt="image" src="https://github.com/user-attachments/assets/7746c2b5-c0da-4fbf-8499-e6431e7e5d94" />
<img width="2133" height="1876" alt="0c69c5d7-a82a-4900-9d45-2190d075f557" src="https://github.com/user-attachments/assets/b65f53fb-37f3-46c6-8897-f1359fb5d4eb" />
<img width="2136" height="1881" alt="image" src="https://github.com/user-attachments/assets/fae65091-c411-46c3-a4a3-8af97eac50b9" />
<img width="2143" height="1868" alt="image" src="https://github.com/user-attachments/assets/db7439ee-7cbc-4860-b723-598d8517f1e2" />





## 🚀 快速安装

推荐使用 Docker 一键启动，脚本会自动处理环境依赖和配置。

复制以下命令在服务器执行即可（需提前安装 Docker）：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/install.sh)
```
提示：如果是旧版本 (xui_manager) 用户，直接运行此命令并选择 [2] 更新面板，脚本会自动将旧数据迁移至新目录。

## 🛠️ 反向代理配置指南
⚠️ 注意： 如果您在安装时选择了 「域名访问」 模式，脚本会自动为您配置好 Caddy，无需进行以下操作。 仅当您选择「IP + 端口」模式，且希望手动配置 Nginx 或 Caddy 将域名指向面板时，才需要参考以下内容。

###  选择「IP + 端口」模式的用户，后续按照如下方式进行反代配置
🌍方案 A：Nginx 用户 (推荐)
  如果您服务器上已经安装了 Nginx，建议在 /etc/nginx/conf.d/ 目录下新建一个配置文件（例如 xui_manager.conf），并填入以下内容：

```bash
server {
    listen 80;
    # 请修改为您实际的域名
    server_name example.com;

    # 1. 订阅转换 (关键配置)
    # ⚠️ 注意：proxy_pass 结尾必须带 /sub，用于路径重写
    location /convert {
        # 假设 subconverter 运行在本地 25500
        proxy_pass [http://127.0.0.1:25500/sub](http://127.0.0.1:25500/sub);
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # 2. 主面板
    location / {
        proxy_pass [http://127.0.0.1:8081](http://127.0.0.1:8081); # 你的面板端口
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # WebSocket 支持 (NiceGUI 必需)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```
提示：配置完成后，请使用 nginx -t 检查配置并重启 Nginx。如果需要 HTTPS，请自行配置 SSL 证书。

🌍方案 B：已有 Caddy 的用户 (手动配置)
如果您已经安装了 Caddy（非脚本安装），请根据您的需求修改您的 /etc/caddy/Caddyfile（或其他目录）。

场景 1：使用全新的子域名（推荐） 
请在 Caddyfile 的末尾追加以下内容：
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
场景 2：想把面板挂在现有域名的子路径下（高级） 
如果您想通过 blog.com/panel 这种方式访问，请在您现有的站点配置块中插入：
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


* 进入安装目录：
```bash
cd /root/x-fusion-panel
```

* 查看日志：

```bash
 docker logs -f --tail 100 x-fusion-panel
```

* 重启服务：
```bash
cd /root/x-fusion-panel && docker compose up -d --build
```


## 📂 数据目录说明

程序启动后会自动在 /root/x-fusion-panel 下生成以下关键文件/目录：

data/servers.json: 面板服务器列表数据库

data/subscriptions.json: 订阅配置数据库

data/admin_config.json: 管理员配置（MFA、自定义分组等）

static/: 本地静态资源（xterm.js 等，用于加速 SSH 访问）

⚠️ 注意：请定期备份 data 目录以防数据丢失。

