#!/bin/bash

# ==============================================================================
# X-UI Manager Pro 一键安装/管理脚本 (最终完美版)
# GitHub: https://github.com/SIJULY/xui_manager
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="xui_manager"
INSTALL_DIR="/root/${PROJECT_NAME}"
REPO_URL="https://raw.githubusercontent.com/SIJULY/xui_manager/main"
CADDY_CONFIG_PATH="/etc/caddy/Caddyfile"
CADDY_MARK_START="# X-UI Manager Config Start"
CADDY_MARK_END="# X-UI Manager Config End"

# 颜色定义
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
PLAIN="\033[0m"

# --- 辅助函数 ---
print_info() { echo -e "${BLUE}[信息]${PLAIN} $1"; }
print_success() { echo -e "${GREEN}[成功]${PLAIN} $1"; }
print_warning() { echo -e "${YELLOW}[警告]${PLAIN} $1"; }
print_error() { echo -e "${RED}[错误]${PLAIN} $1"; exit 1; }

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        print_error "此脚本必须以 root 用户身份运行。"
    fi
}

# 关键修复：等待系统释放 APT 锁
wait_for_apt_lock() {
    echo -e "${BLUE}[信息] 正在检查系统 APT 锁状态...${PLAIN}"
    # 只要检测到 apt 或 dpkg 的锁被占用，就循环等待
    while fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
          fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
          fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
        echo -e "${YELLOW}[警告] 系统后台正在运行更新进程 (apt/dpkg)，脚本暂停 10 秒等待锁释放...${PLAIN}"
        sleep 10
    done
    echo -e "${GREEN}[成功] APT 锁已释放，继续执行任务。${PLAIN}"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_info "未检测到 Docker，正在安装..."
        wait_for_apt_lock # 安装前再次确认锁状态
        curl -fsSL https://get.docker.com | bash
        systemctl enable docker
        systemctl start docker
    fi
    if ! docker compose version &> /dev/null; then
        print_info "未检测到 Docker Compose 插件，正在安装..."
        wait_for_apt_lock
        apt-get update && apt-get install -y docker-compose-plugin
    fi
}

# --- 核心功能函数 ---

# 1. 基础环境部署 (下载代码、生成Dockerfile)
deploy_base() {
    check_docker
    mkdir -p ${INSTALL_DIR}/app
    mkdir -p ${INSTALL_DIR}/data
    cd ${INSTALL_DIR}

    print_info "正在拉取最新代码..."
    # 下载 Dockerfile 和 requirements.txt
    curl -sS -O ${REPO_URL}/Dockerfile
    curl -sS -O ${REPO_URL}/requirements.txt
    # 下载主程序
    curl -sS -o app/main.py ${REPO_URL}/app/main.py

    # 初始化数据文件
    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
}

# 2. 生成 docker-compose.yml (根据模式动态生成)
generate_compose() {
    local BIND_IP=$1 # 127.0.0.1 (域名模式) 或 0.0.0.0 (IP模式)
    local PORT=$2

    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
version: '3.8'
services:
  xui-manager:
    build: .
    container_name: xui_manager
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      - ./data/servers.json:/app/servers.json
      - ./data/subscriptions.json:/app/subscriptions.json
    environment:
      - TZ=Asia/Shanghai
EOF
}

# 3. 安装 Caddy (如果不存在)
install_caddy_if_needed() {
    if ! command -v caddy &> /dev/null; then
        print_info "未检测到 Caddy，正在安装官方版本..."
        wait_for_apt_lock
        apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
        apt-get update
        apt-get install -y caddy
        print_success "Caddy 安装完成。"
    else
        print_info "检测到已安装 Caddy，将复用现有环境。"
    fi
    
    # 确保 Caddy 启动
    if ! systemctl is-active --quiet caddy; then
        systemctl enable --now caddy || print_warning "Caddy 启动尝试失败，可能端口被占用，稍后重试 reload。"
    fi
}

# 4. 配置 Caddy 反代
configure_caddy() {
    local DOMAIN=$1
    local PORT=$2

    # 先清理旧配置（如果有）
    if [ -f "$CADDY_CONFIG_PATH" ]; then
        sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$CADDY_CONFIG_PATH"
    fi

    # 追加新配置
    print_info "正在追加 Caddy 配置..."
    
    # 确保文件以换行符结尾
    if [ -s "$CADDY_CONFIG_PATH" ] && [ "$(tail -c 1 "$CADDY_CONFIG_PATH")" != "" ]; then
        echo "" >> "$CADDY_CONFIG_PATH"
    fi

    cat >> "$CADDY_CONFIG_PATH" << EOF
${CADDY_MARK_START}
${DOMAIN} {
    reverse_proxy 127.0.0.1:${PORT}
}
${CADDY_MARK_END}
EOF

    # 智能重载/启动
    if systemctl is-active --quiet caddy; then
        systemctl reload caddy
        print_success "Caddy 配置已更新并重载。"
    else
        systemctl enable --now caddy
        print_success "Caddy 已启动并加载配置。"
    fi
}

# --- 菜单动作 ---

install_panel() {
    # 第一步先检查锁，防止后面报错
    wait_for_apt_lock
    
    deploy_base

    echo "------------------------------------------------"
    echo "请选择访问方式："
    echo "  1) IP + 端口访问 (简单，无HTTPS)"
    echo "  2) 域名访问 (自动申请HTTPS，推荐)"
    echo "------------------------------------------------"
    read -p "请输入选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        # IP 模式
        read -p "请输入开放端口 [8081]: " port
        port=${port:-8081}
        
        generate_compose "0.0.0.0" "$port"
        
        print_info "正在启动容器..."
        docker compose up -d --build
        
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！"
        echo -e "访问地址: http://${ip_addr}:${port}"

    else
        # 域名模式
        read -p "请输入您的域名 (例如 panel.test.com): " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        
        read -p "请输入内部运行端口 (避免冲突) [8081]: " port
        port=${port:-8081}

        # 1. 生成仅监听本地的 Docker 配置
        generate_compose "127.0.0.1" "$port"
        
        # 2. 启动容器
        print_info "正在启动容器..."
        docker compose up -d --build

        # 3. 处理 Caddy
        install_caddy_if_needed
        configure_caddy "$domain" "$port"

        print_success "安装成功！"
        echo -e "访问地址: https://${domain}"
    fi
}

update_panel() {
    if [ ! -d "${INSTALL_DIR}" ]; then
        print_error "未检测到安装目录，请先安装。"
    fi
    print_info "正在更新代码..."
    cd ${INSTALL_DIR}
    docker compose down
    
    # 重新下载最新代码
    curl -sS -O ${REPO_URL}/Dockerfile
    curl -sS -O ${REPO_URL}/requirements.txt
    curl -sS -o app/main.py ${REPO_URL}/app/main.py
    
    print_info "正在重建容器..."
    docker compose up -d --build
    print_success "更新完成！"
}

uninstall_panel() {
    print_warning "确定要卸载吗？所有数据将丢失！(y/n)"
    read -p "确认: " confirm
    if [ "$confirm" != "y" ]; then exit 0; fi

    if [ -d "${INSTALL_DIR}" ]; then
        cd ${INSTALL_DIR}
        print_info "停止并删除容器..."
        docker compose down
        cd /root
        rm -rf ${INSTALL_DIR}
    fi

    # 清理 Caddy 配置
    if [ -f "$CADDY_CONFIG_PATH" ]; then
        if grep -q "$CADDY_MARK_START" "$CADDY_CONFIG_PATH"; then
            print_info "移除 Caddy 相关配置..."
            sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$CADDY_CONFIG_PATH"
            systemctl reload caddy
        fi
    fi

    print_success "卸载完成。"
}

# --- 主菜单 ---
check_root

clear
echo -e "${GREEN}=================================================${PLAIN}"
echo -e "${GREEN}      X-UI Manager Pro 一键管理脚本 (Docker)      ${PLAIN}"
echo -e "${GREEN}=================================================${PLAIN}"
echo -e "  1. 安装面板 (全新安装)"
echo -e "  2. 更新面板 (保留数据)"
echo -e "  3. 卸载面板"
echo -e "  0. 退出"
echo -e "${GREEN}=================================================${PLAIN}"
read -p "请输入选项: " choice

case $choice in
    1) install_panel ;;
    2) update_panel ;;
    3) uninstall_panel ;;
    0) exit 0 ;;
    *) print_error "无效选项" ;;
esac
