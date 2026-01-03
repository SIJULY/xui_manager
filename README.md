# 🌐 X-Fusion Panel

![Docker](https://img.shields.io/badge/Docker-Enabled-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-yellow)
![License](https://img.shields.io/badge/License-MIT-green)

> **声明**：  此项目仅供个人学习、交流使用，请遵守当地法律法规，勿用于非法用途；请勿用于生产环境。

> **注意**： 在使用此项目和〔教程〕过程中，若因违反以上声明使用规则而产生的一切后果由使用者自负。


**如果觉得项目有用，请点个 ⭐ Star 支持一下！**

---

****X-Fusion Panel**** 是一个全能型 VPS 集中运维与监控平台。它首创 “混合双轨制” 监控架构，完美融合了 X-UI 面板管理 与 轻量级服务器探针。无论你是需要管理成百上千个节点的使用者，还是拥有多台 VPS 的极客，X-Fusion 都能通过统一的 Web 界面，提供包含 实时性能监控、WebSSH 终端、批量节点管理、智能订阅转换 在内的一站式解决方案。

---

### ✨ 核心功能

### 1. 🛡️ 混合双轨制监控 (Hybrid Monitor)

打破传统面板限制，采用 主动拉取 + 被动推送 双重机制：

被动推送 (Probe Mode)：通过一键安装 Python 轻量探针，服务器主动每 3 秒汇报 CPU、内存、硬盘、负载及实时流量。数据走 HTTP 推送，无需开放额外端口，不受防火墙限制。****

主动拉取 (Panel Mode)：对于未安装探针的机器，自动回退到传统的 X-UI API 轮询模式，确保即使不装探针也能获取基础流量数据。

****高容错设计：探针内置沙盒隔离机制，即使 X-UI 面板崩溃或 API 变动，探针依然能稳定汇报系统状态，永不掉线。****

### 2. 💻 沉浸式 WebSSH 终端

****内置 Xterm.js：提供类似本地终端的丝滑体验，支持全彩显示、命令补全、自适应窗口大小。****

灵活认证：支持 全局密钥、独立密钥、独立密码 三种认证方式，满足不同服务器的安全需求。

批量执行：支持向选定的多台服务器批量发送 Shell 命令，并在统一的日志窗口查看执行结果（支持 sudo 交互）。

### 3. 🤖 智能运维与自动化

****智能分组与命名：****

自动识别服务器 IP 归属地，添加国旗 Emoji（如 🇺🇸、🇭🇰、🇯🇵）。

支持 拖拽排序，可自定义区域分组的显示顺序。

****双向同步添加：****

单台添加：智能识别“纯 SSH 模式”与“面板模式”。若未填面板信息但配置了 SSH，自动激活探针安装。

批量添加：支持 双独立开关（[ ] 添加 X-UI 面板 | [ ] 启用 Root 探针），灵活控制批量导入的服务器行为。

### 4. 🔗 强大的订阅管理

聚合与分组：一键生成包含所有节点的聚合订阅，或按“国家/地区”、“自定义 Tag”生成专属订阅。

内置转换：集成 SubConverter，直接输出 Clash、Surge、Loon 等格式配置。

高级策略：支持 正则重命名（Rename）、正则筛选（Include/Exclude）、自动排序、强制开启 UDP 等高级处理策略。

### 5. 📊 可视化仪表盘

全球实景地图：基于 Leaflet 的动态地图，直观展示全球节点分布。

实时数据：协议分布饼图、流量排行柱状图、实时上传/下载速率。

****安全防护：支持 MFA (TOTP) 二次验证，记录登录 IP，保障面板安全。****

---
## 📸 界面预览## 

***📊 全景仪表盘与地图***
<img width="100%" alt="Dashboard" src="https://github.com/user-attachments/assets/7c8b2163-50aa-4b6a-b550-8dde09e57818" />

***🧱 智能监控墙 (支持拖拽排序)***
<img width="100%" alt="Monitor Wall" src="https://github.com/user-attachments/assets/7746c2b5-c0da-4fbf-8499-e6431e7e5d94" />

***🛠️ 批量添加 (支持双独立开关)***
<img width="100%" alt="Batch Add" src="https://github.com/user-attachments/assets/4dbdcb" />

***💻 WebSSH 终端与单机管理***
<img width="100%" alt="WebSSH" src="https://github.com/user-attachments/assets/fae65091-c411-46c3-a4a3-8af97eac50b9" />

***🔗 订阅策略编辑器***
<img width="100%" alt="Subscription" src="https://github.com/user-attachments/assets/db7439ee-7cbc-4860-b723-598d8517f1e2" />





## 🚀 快速安装## 

推荐使用 Docker 一键启动，脚本会自动处理环境依赖和配置。

复制以下命令在服务器执行即可（需提前安装 Docker）：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main/install.sh)
```
提示：如果是旧版本 (xui_manager) 用户，直接运行此命令并选择 [2] 更新面板，脚本会自动将旧数据迁移至新目录。

**💡 添加服务器逻辑说明**

为了适应不同场景，面板在添加服务器时采用了智能判断逻辑：

***1. 单台添加***

X-UI 优先原则：如果填写了面板 URL 和账号密码，且未勾选“启用探针”，则仅作为面板管理，不进行 SSH 连接。

纯 SSH 补位：如果未填写面板信息，但填写了 SSH 连接信息，系统会自动强制启用探针模式（用于纯服务器监控）。

****混合模式：同时填写并勾选，则既管理面板又安装探针。****

***2. 批量添加***

****采用 双独立开关 设计：****

仅勾选 [添加 X-UI 面板]：后台仅尝试连接 X-UI API，绝对不会 发起 SSH 连接。

仅勾选 [启用 Root 探针]：后台仅尝试 SSH 连接并安装 Agent，不尝试连接面板 API。

同时勾选：执行全套初始化流程。

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

