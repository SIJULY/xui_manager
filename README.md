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
