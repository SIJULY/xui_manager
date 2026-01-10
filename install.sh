#!/bin/bash

# ==============================================================================
# X-Fusion Panel 一键安装/管理脚本
# GitHub: https://github.com/SIJULY/x-fusion-panel
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="x-fusion-panel"
INSTALL_DIR="/root/${PROJECT_NAME}"
OLD_INSTALL_DIR="/root/xui_manager" 

# 仓库地址
REPO_URL="https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main"

CADDY_CONFIG_PATH="/etc/caddy/Caddyfile"
CADDY_MARK_START="# X-Fusion Panel Config Start"
CADDY_MARK_END="# X-Fusion Panel Config End"

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

migrate_old_data() {
    if [ -d "$OLD_INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
        echo -e "${YELLOW}=================================================${PLAIN}"
        print_info "检测到旧版安装目录 ($OLD_INSTALL_DIR)"
        print_info "正在自动迁移数据到新目录 ($INSTALL_DIR)..."
        
        # 1. 停止旧容器
        cd "$OLD_INSTALL_DIR"
        if docker compose ps | grep -q "xui_manager"; then
            print_info "停止旧版容器..."
            docker compose down
        fi
        
        # 2. 移动目录
        cd /root
        mv "$OLD_INSTALL_DIR" "$INSTALL_DIR"
        print_success "目录重命名完成。"
        
        # 3. [关键修复] 将旧配置重命名为备份，供 update 读取，而不是删除
        if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
            mv "$INSTALL_DIR/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml.bak"
        fi
        
        echo -e "${YELLOW}=================================================${PLAIN}"
    fi
}

deploy_base() {
    check_docker
    
    # 执行迁移检查
    migrate_old_data

    mkdir -p ${INSTALL_DIR}/app
    mkdir -p ${INSTALL_DIR}/data
    mkdir -p ${INSTALL_DIR}/static
    
    cd ${INSTALL_DIR}

    print_info "正在拉取最新代码 (X-Fusion)..."
    curl -sS -O ${REPO_URL}/Dockerfile
    curl -sS -O ${REPO_URL}/requirements.txt

    print_info "正在下载主程序..."
    curl -sS -o app/main.py ${REPO_URL}/app/main.py

    # 下载探针安装脚本到 static 目录
    print_info "正在下载最新探针脚本..."
    curl -sS -o static/x-install.sh "${REPO_URL}/x-install.sh"

    print_info "正在下载静态资源 (xterm.js)..."
    curl -sS -o static/xterm.css "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css"
    curl -sS -o static/xterm.js "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"
    curl -sS -o static/xterm-addon-fit.js "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"

    # 下载世界地图数据到 static 目录
    print_info "正在下载地图数据..."
    curl -sS -o static/world.json "https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/world.json"

    if [ ! -f "app/main.py" ]; then
        print_error "主程序下载失败！请检查 GitHub 仓库地址是否正确。"
    fi

    # 初始化空文件 (如果不存在)
    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
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
  x-fusion-panel:
    build: .
    container_name: x-fusion-panel
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      - ./data/servers.json:/app/data/servers.json
      - ./data/subscriptions.json:/app/data/subscriptions.json
      - ./data/nodes_cache.json:/app/data/nodes_cache.json
      - ./data/admin_config.json:/app/data/admin_config.json
      - ./static:/app/static
    environment:
      - TZ=Asia/Shanghai
      - XUI_USERNAME=${USER}
      - XUI_PASSWORD=${PASS}
      - XUI_SECRET_KEY=${SECRET}

  subconverter:
    image: tindy2013/subconverter:latest
    container_name: subconverter
    restart: always
    ports:
      - "127.0.0.1:25500:25500"
EOF
}

install_caddy_if_needed() {
    if ! command -v caddy &> /dev/null; then
        print_info "未检测到 Caddy，正在安装..."
        wait_for_apt_lock
        # 检测系统类型并安装
        if [ -f /etc/debian_version ]; then
            apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
            apt-get update
            apt-get install -y caddy
        elif [ -f /etc/redhat-release ]; then
            yum install -y yum-utils
            yum-config-manager --add-repo https://dl.cloudsmith.io/public/caddy/stable/rpm.repo
            yum install -y caddy
        else
             print_warning "不支持的系统，请手动安装 Caddy。"
        fi
    fi
    
    if command -v caddy &> /dev/null; then
        if ! systemctl is-active --quiet caddy; then
            systemctl enable --now caddy
        fi
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
    # 1. 拦截订阅转换请求 (转发给 SubConverter)
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy 127.0.0.1:25500
    }

    # 2. 面板主体 (转发给 Python 面板)
    handle {
        reverse_proxy 127.0.0.1:${PORT}
    }
}
${CADDY_MARK_END}
EOF
    systemctl reload caddy
}

# --- 菜单动作 ---

install_panel() {
    wait_for_apt_lock
    deploy_base

    # 如果有旧配置，尝试读取旧账号密码（为了方便迁移）
    local def_user="admin"
    local def_pass="admin"
    local def_key=$(cat /proc/sys/kernel/random/uuid | tr -d '-')

    # 简单的配置嗅探 (如果存在旧的 compose 文件)
    if [ -f "${INSTALL_DIR}/docker-compose.yml.bak" ]; then
        grep "XUI_USERNAME" "${INSTALL_DIR}/docker-compose.yml.bak" &>/dev/null && def_user=$(grep "XUI_USERNAME=" "${INSTALL_DIR}/docker-compose.yml.bak" | cut -d= -f2)
        grep "XUI_PASSWORD" "${INSTALL_DIR}/docker-compose.yml.bak" &>/dev/null && def_pass=$(grep "XUI_PASSWORD=" "${INSTALL_DIR}/docker-compose.yml.bak" | cut -d= -f2)
        grep "XUI_SECRET_KEY" "${INSTALL_DIR}/docker-compose.yml.bak" &>/dev/null && def_key=$(grep "XUI_SECRET_KEY=" "${INSTALL_DIR}/docker-compose.yml.bak" | cut -d= -f2)
    fi

    echo "------------------------------------------------"
    read -p "请设置面板登录账号 [${def_user}]: " admin_user
    admin_user=${admin_user:-$def_user}
    read -p "请设置面板登录密码 [${def_pass}]: " admin_pass
    admin_pass=${admin_pass:-$def_pass}
    
    echo "------------------------------------------------"
    echo -e "${YELLOW}配置自动注册通讯密钥${PLAIN}"
    echo -e "推荐密钥: ${GREEN}${def_key}${PLAIN}"
    read -p "按回车使用此密钥，或输入自定义密钥: " input_key
    secret_key=${input_key:-$def_key}
    echo "------------------------------------------------"

    echo -e "${YELLOW}================================================================${PLAIN}"
    echo -e "${RED}⚠️  特别提示  ⚠️${PLAIN}"
    echo -e "${YELLOW}已有Web服务(Nginx/Caddy)请选 [1] IP模式，否则会导致端口冲突。${PLAIN}"
    echo -e "${YELLOW}================================================================${PLAIN}"
    
    echo "请选择访问方式："
    echo "  1) IP + 端口访问"
    echo "  2) 域名访问 (自动 HTTPS)"
    read -p "请输入选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        read -p "请输入开放端口 [8081]: " port
        port=${port:-8081}
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass" "$secret_key"
        print_info "正在启动容器 (X-Fusion Panel)..."
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
        print_info "正在启动容器 (X-Fusion Panel)..."
        docker compose up -d --build
        install_caddy_if_needed
        configure_caddy "$domain" "$port"
        print_success "安装成功！"
        echo -e "登录地址: https://${domain}"
    fi
}

update_panel() {
    # 智能判断：如果是旧目录用户运行了更新，先执行迁移
    if [ -d "$OLD_INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
        print_warning "检测到旧版目录，正在迁移到新架构..."
        migrate_old_data
    fi

    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录，请先执行安装。"; fi
    
    # [修复] 检查配置是否存在（支持主文件或备份文件）
    if [ ! -f "${INSTALL_DIR}/docker-compose.yml" ] && [ ! -f "${INSTALL_DIR}/docker-compose.yml.bak" ]; then 
        print_error "配置文件丢失，无法更新。"
    fi

    echo -e "${BLUE}=================================================${PLAIN}"
    print_info "正在执行 X-Fusion Panel 智能更新..."
    
    cd ${INSTALL_DIR}
    
    # [修复] 如果有现役配置，先备份为 .bak；如果没现役配置但有 .bak（刚迁移完），则跳过备份步骤
    if [ -f "docker-compose.yml" ]; then
        cp docker-compose.yml docker-compose.yml.bak
        print_info "已备份旧配置"
    fi

    # [修复] 统一从 .bak 读取旧配置（确保无论是迁移还是更新都能读到）
    CONFIG_FILE="docker-compose.yml.bak"

    # 提取旧配置
    OLD_USER=$(grep "XUI_USERNAME=" $CONFIG_FILE | cut -d= -f2)
    OLD_PASS=$(grep "XUI_PASSWORD=" $CONFIG_FILE | cut -d= -f2)
    OLD_KEY=$(grep "XUI_SECRET_KEY=" $CONFIG_FILE | cut -d= -f2)
    PORT_LINE=$(grep ":8080" $CONFIG_FILE | head -n 1)
    
    if [[ $PORT_LINE == *"127.0.0.1"* ]]; then
        BIND_IP="127.0.0.1"
        OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*127.0.0.1:([0-9]+):8080.*/\1/' | tr -d ' "-')
        IS_DOMAIN_MODE=true
    else
        BIND_IP="0.0.0.0"
        if [[ $PORT_LINE == *"0.0.0.0"* ]]; then
             OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*0.0.0.0:([0-9]+):8080.*/\1/' | tr -d ' "-')
        else
             OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*- "([0-9]+):8080.*/\1/' | tr -d ' "-')
        fi
        IS_DOMAIN_MODE=false
    fi

    # 停止旧容器
    docker compose down

    # 彻底删除可能的旧名容器（防止冲突）
    if docker ps -a | grep -q "xui_manager"; then
        docker rm -f xui_manager 2>/dev/null
    fi

    print_info "正在拉取最新代码..."
    curl -sS -o app/main.py ${REPO_URL}/app/main.py
    
    print_info "更新静态资源..."
    mkdir -p static
    
    # [修复] 补齐探针安装脚本
    curl -sS -o static/x-install.sh "${REPO_URL}/x-install.sh"
    
    curl -sS -o static/xterm.css "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css"
    curl -sS -o static/xterm.js "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"
    curl -sS -o static/xterm-addon-fit.js "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"

    # [修复] 补齐世界地图数据
    print_info "更新地图数据..."
    curl -sS -o static/world.json "https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/world.json"

    generate_compose "$BIND_IP" "$OLD_PORT" "$OLD_USER" "$OLD_PASS" "$OLD_KEY"

    if [ "$IS_DOMAIN_MODE" = true ] && [ -f "$CADDY_CONFIG_PATH" ]; then
        EXISTING_DOMAIN=$(grep -B 2 "reverse_proxy 127.0.0.1:${OLD_PORT}" "$CADDY_CONFIG_PATH" | grep " {" | head -n 1 | awk '{print $1}')
        if [ -n "$EXISTING_DOMAIN" ]; then
            install_caddy_if_needed
            configure_caddy "${EXISTING_DOMAIN}" "${OLD_PORT}"
        fi
    fi

    print_info "启动新容器..."
    docker compose up -d --build
    print_success "更新完成！"
}

uninstall_panel() {
    read -p "确定要卸载吗？(y/n): " confirm
    if [ "$confirm" != "y" ]; then exit 0; fi
    
    # 删除新目录
    if [ -d "${INSTALL_DIR}" ]; then
        cd ${INSTALL_DIR}
        docker compose down
        cd /root
        rm -rf ${INSTALL_DIR}
    fi
    
    # 也检查旧目录，防止残留
    if [ -d "${OLD_INSTALL_DIR}" ]; then
        cd "${OLD_INSTALL_DIR}"
        docker compose down 2>/dev/null
        cd /root
        rm -rf "${OLD_INSTALL_DIR}"
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
echo -e "${GREEN} X-Fusion Panel 一键管理脚本 ${PLAIN}"
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
