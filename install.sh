#!/bin/bash

# ==============================================================================
# X-UI Manager Pro 一键安装/管理脚本 (稳定版 - 强提醒模式)
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

# 智能等待 APT 锁
wait_for_apt_lock() {
    echo -e "${BLUE}[信息] 正在检查系统 APT 锁状态...${PLAIN}"
    local wait_time=0
    local timeout=60
    while fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
          fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
          fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
        echo -e "${YELLOW}[警告] 系统后台正在运行更新进程，已等待 ${wait_time} 秒...${PLAIN}"
        sleep 10
        ((wait_time+=10))
        if [ "$wait_time" -ge "$timeout" ]; then
            echo -e "${RED}[错误] APT 锁已被占用超过 ${timeout} 秒。${PLAIN}"
            read -p "是否尝试强制结束占用进程并删除锁文件？(y/n) [推荐先选 n]: " force_unlock
            if [ "$force_unlock" == "y" ]; then
                echo -e "${RED}[警告] 正在执行强制解锁...${PLAIN}"
                killall apt apt-get dpkg 2>/dev/null
                rm -f /var/lib/apt/lists/lock /var/cache/apt/archives/lock /var/lib/dpkg/lock*
                dpkg --configure -a
                echo -e "${GREEN}[成功] 已执行强制清理。${PLAIN}"
                break
            else
                wait_time=0
            fi
        fi
    done
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_info "未检测到 Docker，正在安装..."
        wait_for_apt_lock
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

deploy_base() {
    check_docker
    mkdir -p ${INSTALL_DIR}/app
    mkdir -p ${INSTALL_DIR}/data
    cd ${INSTALL_DIR}

    print_info "正在拉取最新代码..."
    # 下载 Dockerfile 和 requirements.txt
    curl -sS -O ${REPO_URL}/Dockerfile
    curl -sS -O ${REPO_URL}/requirements.txt

    print_info "正在下载主程序..."
    curl -sS -o app/main.py ${REPO_URL}/app/main.py

    if [ ! -f "app/main.py" ]; then
        print_error "主程序下载失败！请检查 GitHub 仓库中是否包含 app/main.py 文件。"
    fi

    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
    # 确保 admin_config.json 存在
    if [ ! -f "data/admin_config.json" ]; then echo "{}" > data/admin_config.json; fi
}

generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4
    local SECRET=$5 

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
      - ./data/servers.json:/app/data/servers.json
      - ./data/subscriptions.json:/app/data/subscriptions.json
      - ./data/nodes_cache.json:/app/data/nodes_cache.json
      - ./data/admin_config.json:/app/data/admin_config.json
    environment:
      - TZ=Asia/Shanghai
      - XUI_USERNAME=${USER}
      - XUI_PASSWORD=${PASS}
      - XUI_SECRET_KEY=${SECRET}
EOF
}

install_caddy_if_needed() {
    if ! command -v caddy &> /dev/null; then
        print_info "未检测到 Caddy，正在安装..."
        wait_for_apt_lock
        apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
        apt-get update
        apt-get install -y caddy
    fi
    if ! systemctl is-active --quiet caddy; then
        systemctl enable --now caddy
    fi
}

configure_caddy() {
    local DOMAIN=$1
    local PORT=$2
    if [ -f "$CADDY_CONFIG_PATH" ]; then
        sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$CADDY_CONFIG_PATH"
    fi
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
    systemctl reload caddy
}

# --- 菜单动作 ---

install_panel() {
    wait_for_apt_lock
    deploy_base

    echo "------------------------------------------------"
    read -p "请设置面板登录账号 [admin]: " admin_user
    admin_user=${admin_user:-admin}
    read -p "请设置面板登录密码 [admin]: " admin_pass
    admin_pass=${admin_pass:-admin}
    
    # --- 生成/设置通讯密钥 ---
    rand_key=$(cat /proc/sys/kernel/random/uuid | tr -d '-')
    echo "------------------------------------------------"
    echo -e "${YELLOW}配置自动注册通讯密钥 (用于 OCI 开机面板对接)${PLAIN}"
    echo -e "系统已生成随机密钥: ${GREEN}${rand_key}${PLAIN}"
    read -p "按回车使用此密钥，或输入自定义密钥: " input_key
    secret_key=${input_key:-$rand_key}
    echo "------------------------------------------------"

    # --- ✨✨✨ 重点警告 ✨✨✨ ---
    echo -e "${YELLOW}================================================================${PLAIN}"
    echo -e "${RED}⚠️  特别提示 / IMPORTANT WARNING  ⚠️${PLAIN}"
    echo -e "${YELLOW}如果您的VPS已经部署了其他网站（例如使用了 Nginx, NPM, Caddy 等），${PLAIN}"
    echo -e "${RED}请务必选择 [1] IP + 端口模式${PLAIN}${YELLOW}，否则会导致 80/443 端口冲突！${PLAIN}"
    echo -e "${YELLOW}================================================================${PLAIN}"
    
    echo "请选择访问方式："
    echo "  1) IP + 端口访问 (安全，适合已有 Web 服务的环境)"
    echo "  2) 域名访问 (自动申请 HTTPS，适合纯净环境)"
    read -p "请输入选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        read -p "请输入开放端口 [8081]: " port
        port=${port:-8081}
        
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass" "$secret_key"
        
        print_info "正在启动容器..."
        docker compose up -d --build
        
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！"
        echo -e "登录地址: http://${ip_addr}:${port}"
    else
        read -p "请输入您的域名: " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        read -p "请输入内部运行端口 [8081]: " port
        port=${port:-8081}

        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key"
        
        print_info "正在启动容器..."
        docker compose up -d --build

        install_caddy_if_needed
        configure_caddy "$domain" "$port"

        print_success "安装成功！"
        echo -e "登录地址: https://${domain}"
    fi
    
    echo -e "账号: ${BLUE}${admin_user}${PLAIN}"
    echo -e "密码: ${BLUE}${admin_pass}${PLAIN}"
    echo -e "------------------------------------------------"
    echo -e "${RED}⚠️ 重要：请复制下方密钥到 OCI 开机面板脚本中 ⚠️${PLAIN}"
    echo -e "通讯密钥: ${GREEN}${secret_key}${PLAIN}"
    echo -e "------------------------------------------------"
}

update_panel() {
    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录，请先执行安装。"; fi
    
    # 检查是否存在配置文件，防止误操作
    if [ ! -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        print_error "配置文件 docker-compose.yml 丢失，无法更新。"
    fi

    echo -e "${BLUE}=================================================${PLAIN}"
    print_info "检测到现有配置，正在执行安全更新..."
    print_info "此操作将保留您原有的访问方式（IP或域名），不修改配置。"
    echo -e "${BLUE}=================================================${PLAIN}"

    cd ${INSTALL_DIR}
    docker compose down
    
    # 更新代码
    print_info "正在拉取最新代码..."
    curl -sS -o app/main.py ${REPO_URL}/app/main.py
    
    print_info "正在重建容器..."
    docker compose up -d --build
    print_success "更新完成！服务已重启。"
}

uninstall_panel() {
    read -p "确定要卸载吗？(y/n): " confirm
    if [ "$confirm" != "y" ]; then exit 0; fi
    if [ -d "${INSTALL_DIR}" ]; then
        cd ${INSTALL_DIR}
        docker compose down
        cd /root
        rm -rf ${INSTALL_DIR}
    fi
    if [ -f "$CADDY_CONFIG_PATH" ]; then
        if grep -q "$CADDY_MARK_START" "$CADDY_CONFIG_PATH"; then
            sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$CADDY_CONFIG_PATH"
            systemctl reload caddy
        fi
    fi
    print_success "卸载完成。"
}

# --- 主菜单 ---
check_root
clear
echo -e "${GREEN} X-UI Manager Pro 一键管理脚本 ${PLAIN}"
echo -e "  1. 安装面板"
echo -e "  2. 更新面板"
echo -e "  3. 卸载面板"
echo -e "  0. 退出"
read -p "请输入选项: " choice

case $choice in
    1) install_panel ;;
    2) update_panel ;;
    3) uninstall_panel ;;
    0) exit 0 ;;
    *) print_error "无效选项" ;;
esac
