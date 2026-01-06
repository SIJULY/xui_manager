# 🌐 X-Fusion Panel

![Docker](https://img.shields.io/badge/Docker-Enabled-blue)
![Python](https://img.shields.io/badge/Python-3.10%2B-yellow)
![License](https://img.shields.io/badge/License-MIT-green)

> **声明**：  此项目仅供个人学习、交流使用，请遵守当地法律法规，勿用于非法用途；请勿用于生产环境。

> **注意**： 在使用此项目和〔教程〕过程中，若因违反以上声明使用规则而产生的一切后果由使用者自负。


**如果觉得项目有用，请点个 ⭐ Star 支持一下！**

---
# 📖 项目简介
****X-Fusion Panel**** 是新一代的 轻量级 VPS 集中运维与监控平台。


它不仅仅是一个探针，更是一个可视化的运维控制台。X-Fusion 首创 「混合双轨监控架构」，在提供极低资源占用的服务器性能监控（CPU/内存/硬盘/流量）的同时，深度融合了 X-UI 面板管理能力。

无论您是拥有大量 VPS 的集群管理者，还是追求极致效率的极客，X-Fusion 都能通过一个优雅的 Web 界面，为您提供 全景实时监控、WebSSH 批量运维、节点自动化管理 的一站式体验。

---

# ✨ 核🏗️ 核心架构：混合双轨制 (Hybrid Monitor)

X-Fusion 打破了传统探针与管理面板的界限，采用 主动拉取 (Pull) 与 被动推送 (Push) 相结合的机制，实现了高可用与低延迟的完美平衡。
```bash
graph TD
    User[用户 / 浏览器] -->|访问| Panel[X-Fusion 主控面板]
    
    subgraph "Track A: 探针模式 (实时监控)"
        Agent[Python 轻量探针] -->|HTTP Push / 3s| Panel
        NoteA[数据: CPU, 内存, 负载, 实时网速]
    end
    
    subgraph "Track B: 面板模式 (业务管理)"
        Panel -->|API Request| XUI[X-UI 面板 / API]
        NoteB[数据: 节点管理, 账号流量, 订阅]
    end
    
    Agent -.->|沙盒隔离| XUI
    style Panel fill:#f9f,stroke:#333,stroke-width:2px
    style Agent fill:#bbf,stroke:#333,stroke-width:1px
```

### 🛡️ 轨道 A轨道 A 混合双轨制监控 (Hybrid Monitor)

原理：在服务器上一键植入 Python 轻量探针，Agent 每 3 秒主动向面板汇报状态。

***优势：无需开放额外端口（走 HTTP/HTTPS），不受防火墙入站规则限制；即使 X-UI 面板崩溃，探针依然能独立汇报服务器存活状态。***

### ⚡ 轨道 B：主动拉取 (Panel Mode)***

原理：对于未安装探针的机器，自动降级为 API 轮询模式。

***优势：兼容性强，确保在不侵入系统的情况下，依然能获取基础的流量统计和节点信息。***

### ✨核心功能亮点

 ***1. 💻 沉浸式 WebSSH 终端***

全球实景地图：基于 Leaflet/ECharts 的动态地图，直观展示全球节点分布与网络连通性。

实时数据流：提供秒级刷新的 CPU、内存、硬盘仪表盘，以及协议分布饼图和流量排行柱状图。

三网延迟检测：内置电信、联通、移动三网 Ping 值检测，链路质量一目了然。

***2. 💻 极客级 WebSSH 终端***

原生体验：内置 Xterm.js，支持真彩显示、命令补全、快捷键，体验媲美本地终端。

灵活认证：支持 全局密钥（所有机器共用）、独立密钥、独立密码 三种认证方式。

批量运维：支持向选定的多台服务器 批量发送 Shell 命令，并在统一日志窗口查看执行结果（完美支持 sudo 交互）。

***3. 🔗 深度 X-UI 集成与订阅管理***

节点自动化：自动同步 X-UI 面板中的节点配置，支持增删改查。

聚合订阅：一键生成聚合订阅链接。

内置转换：集成 SubConverter，支持将节点直接转换为 Clash、Surge、Loon 等客户端配置。

高级策略：支持正则重命名、正则筛选（Include/Exclude）、自动排序、强制开启 UDP 等高级订阅策略。

***4. 🤖 智能运维与自动化***

IP 归属地识别：自动识别服务器 IP，智能添加国旗 Emoji（如 🇺🇸、🇭🇰、🇯🇵）。

双向同步添加：

智能识别：添加服务器时，若仅填写 SSH 信息，自动激活探针安装流程；若仅填写面板信息，则仅对接 API。

批量导入：支持 IP:端口 格式批量导入，并提供双独立开关控制初始化行为。

---
# 📸 界面预览## 

***📊 全景仪表盘与地图***


<img width="100%" alt="Dashboard" src="https://github.com/user-attachments/assets/7c8b2163-50aa-4b6a-b550-8dde09e57818" />

***🧱 探针页面***
<img width="1933" height="1916" alt="image" src="https://github.com/user-attachments/assets/87eb196f-68aa-4ea0-a263-ad5d6842dedf" />


***🛠️ 批量添加 (支持双独立开关)***
<img width="1923" height="1921" alt="image" src="https://github.com/user-attachments/assets/04965188-6aa3-4021-9d4e-106052b568bf" />


***💻 WebSSH 终端与单机管理***
<img width="1923" height="1926" alt="630ebad3-a49b-425a-860c-9c52cee10d17" src="https://github.com/user-attachments/assets/e0c35eed-9a0c-4491-8b59-d58744b1243d" />

***🔗 订阅策略编辑器***
<img width="1903" height="1906" alt="image" src="https://github.com/user-attachments/assets/091c851e-1440-40a3-b28e-b462baf732a8" />






# 🚀 快速安装## 

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

# 🛠️ 反向代理配置指南
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


# 🛠️ 管理命令


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


# 📂 数据目录说明

程序启动后会自动在 /root/x-fusion-panel 下生成以下关键文件/目录：

data/servers.json: 面板服务器列表数据库

data/subscriptions.json: 订阅配置数据库

data/admin_config.json: 管理员配置（MFA、自定义分组等）

static/: 本地静态资源（xterm.js 等，用于加速 SSH 访问）

⚠️ 注意：请定期备份 data 目录以防数据丢失。

