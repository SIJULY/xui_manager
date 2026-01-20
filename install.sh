#!/bin/bash

# ==============================================================================
# X-Fusion Panel 一键安装/管理脚本 (Docker Hub 发行版)
# GitHub: https://github.com/SIJULY/x-fusion-panel
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="x-fusion-panel"
INSTALL_DIR="/root/${PROJECT_NAME}"
OLD_INSTALL_DIR="/root/xui_manager" 

# 仓库地址
REPO_URL="https://raw.githubusercontent.com/SIJULY/x-fusion-panel/main"

# Caddy 配置标记
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
        
        cd "$OLD_INSTALL_DIR"
        if docker compose ps | grep -q "xui_manager"; then
            print_info "停止旧版容器..."
            docker compose down
        fi
        
        cd /root
        mv "$OLD_INSTALL_DIR" "$INSTALL_DIR"
        print_success "目录重命名完成。"
        
        if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
            mv "$INSTALL_DIR/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml.bak"
        fi
        echo -e "${YELLOW}=================================================${PLAIN}"
    fi
}

deploy_base() {
    check_docker
    migrate_old_data

    # ✨ 修改：只需要创建数据目录，不需要下载源码了
    mkdir -p ${INSTALL_DIR}/data
    
    cd ${INSTALL_DIR}

    # 初始化空配置文件 (防止挂载报错)
    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
    if [ ! -f "data/admin_config.json" ]; then echo "{}" > data/admin_config.json; fi
    if [ ! -f "Caddyfile" ]; then touch Caddyfile; fi
}

# --- ✨ 核心修改：使用云端镜像 ---
generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4
    local SECRET=$5 
    local ENABLE_CADDY=$6 

    # 1. 生成基础服务配置 (Panel + Subconverter)
    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
version: '3.8'
services:
  x-fusion-panel:
    # ✨ 修改：直接使用 Docker Hub 镜像
    image: sijuly0713/x-fusion-panel:latest
    container_name: x-fusion-panel
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      # ✨ 修改：只保留数据目录映射
      - ./data:/app/data
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
    environment:
      - TZ=Asia/Shanghai
EOF

    # 2. 如果启用 Caddy，追加 Caddy 服务块
    if [ "$ENABLE_CADDY" == "true" ]; then
        cat >> ${INSTALL_DIR}/docker-compose.yml << EOF

  caddy:
    image: caddy:latest
    container_name: caddy
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./caddy_data:/data
    depends_on:
      - x-fusion-panel
      - subconverter
EOF
    fi
}

configure_caddy_docker() {
    local DOMAIN=$1
    local DOCKER_CADDY_FILE="${INSTALL_DIR}/Caddyfile"

    if [ ! -f "$DOCKER_CADDY_FILE" ]; then touch "$DOCKER_CADDY_FILE"; fi
    sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$DOCKER_CADDY_FILE"
    if [ -s "$DOCKER_CADDY_FILE" ] && [ "$(tail -c 1 "$DOCKER_CADDY_FILE")" != "" ]; then echo "" >> "$DOCKER_CADDY_FILE"; fi

    # 使用容器名通信
    cat >> "$DOCKER_CADDY_FILE" << EOF
${CADDY_MARK_START}
${DOMAIN} {
    encode gzip
    handle_path /convert* {
        rewrite * /sub
        reverse_proxy subconverter:25500 
    }
    handle {
        reverse_proxy x-fusion-panel:8080
    }
}
${CADDY_MARK_END}
EOF
}

# --- 菜单动作 ---

install_panel() {
    wait_for_apt_lock
    deploy_base

    # 读取旧配置
    local def_user="admin"
    local def_pass="admin"
    local def_key=$(cat /proc/sys/kernel/random/uuid | tr -d '-')

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
    read -p "按回车使用推荐密钥 [${def_key}]: " input_key
    secret_key=${input_key:-$def_key}
    echo "------------------------------------------------"

    echo "请选择访问方式："
    echo "  1) IP + 端口访问"
    echo "  2) 域名访问 (自动申请证书，全新机器推荐)"
    echo "  3) 域名访问 (共存模式，已有 Nginx/Caddy 用户推荐)"
    read -p "请输入选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        read -p "请输入开放端口 [8081]: " port
        port=${port:-8081}
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在启动容器..."
        # ✨ 修改：不需要 --build 了，因为直接拉镜像
        docker compose up -d
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！登录地址: http://${ip_addr}:${port}"

    elif [ "$net_choice" == "3" ]; then
        read -p "请输入内部运行端口 [8081]: " port
        port=${port:-8081}
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在启动容器..."
        docker compose up -d
        
        print_success "安装成功！(共存模式)"
        echo -e "${YELLOW}请将以下配置添加到您的主 Caddyfile/Nginx 中：${PLAIN}"
        echo "------------------------------------------------"
        echo "handle_path /convert* { reverse_proxy 127.0.0.1:25500 }"
        echo "handle { reverse_proxy 127.0.0.1:${port} }"
        echo "------------------------------------------------"

    else
        read -p "请输入您的域名: " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        port=8081 
        
        configure_caddy_docker "$domain"
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "true"
        
        print_info "正在启动容器..."
        docker compose up -d
        
        print_success "安装成功！登录地址: https://${domain}"
    fi
}

update_panel() {
    if [ -d "$OLD_INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
        print_warning "检测到旧版目录，正在迁移到新架构..."
        migrate_old_data
    fi

    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录，请先执行安装。"; fi
    
    cd ${INSTALL_DIR}
    if [ -f "docker-compose.yml" ]; then
        cp docker-compose.yml docker-compose.yml.bak
    fi
    
    if [ ! -f "docker-compose.yml.bak" ]; then print_error "配置文件丢失，无法更新。"; fi

    echo -e "${BLUE}=================================================${PLAIN}"
    print_info "正在执行智能更新 (保留原有网络模式)..."

    CONFIG_FILE="docker-compose.yml.bak"

    # 1. 提取旧配置
    OLD_USER=$(grep "XUI_USERNAME=" $CONFIG_FILE | cut -d= -f2)
    OLD_PASS=$(grep "XUI_PASSWORD=" $CONFIG_FILE | cut -d= -f2)
    OLD_KEY=$(grep "XUI_SECRET_KEY=" $CONFIG_FILE | cut -d= -f2)
    PORT_LINE=$(grep ":8080" $CONFIG_FILE | head -n 1)
    
    # 2. 智能判断原有模式
    if [[ $PORT_LINE == *"127.0.0.1"* ]]; then
        BIND_IP="127.0.0.1"
        OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*127.0.0.1:([0-9]+):8080.*/\1/' | tr -d ' "-')
        if grep -q "container_name: caddy" $CONFIG_FILE; then
            ENABLE_CADDY="true"
        else
            ENABLE_CADDY="false"
        fi
    else
        BIND_IP="0.0.0.0"
        OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*:([0-9]+):8080.*/\1/' | tr -d ' "-')
        if [[ $OLD_PORT == *"0.0.0.0"* ]]; then OLD_PORT=$(echo "$OLD_PORT" | cut -d: -f2); fi
        ENABLE_CADDY="false"
    fi

    # 3. 停止并清理
    docker compose down
    if docker ps -a | grep -q "xui_manager"; then docker rm -f xui_manager 2>/dev/null; fi

    # 4. 更新代码 (其实是更新配置逻辑)
    deploy_base

    # 5. 重新生成配置
    generate_compose "$BIND_IP" "$OLD_PORT" "$OLD_USER" "$OLD_PASS" "$OLD_KEY" "$ENABLE_CADDY"

    if [ "$ENABLE_CADDY" == "true" ] && [ -f "Caddyfile" ]; then
          EXISTING_DOMAIN=$(grep " {" Caddyfile | head -n 1 | awk '{print $1}')
          if [ -n "$EXISTING_DOMAIN" ]; then
              configure_caddy_docker "${EXISTING_DOMAIN}"
          fi
    fi

    # 6. 启动 (并拉取最新镜像)
    print_info "正在拉取最新镜像..."
    docker compose pull
    print_info "启动新容器..."
    docker compose up -d
    print_success "更新完成！"
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
    print_success "卸载完成。"
}

# --- 主菜单 ---
check_root
clear
echo -e "${GREEN} X-Fusion Panel 一键管理脚本 (Docker Hub 版)${PLAIN}"
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
