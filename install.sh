#!/bin/bash

# ==============================================================================
# X-Fusion Panel 一键安装/管理脚本 (Python + Go 混合架构版)
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

    mkdir -p ${INSTALL_DIR}/app
    mkdir -p ${INSTALL_DIR}/data
    mkdir -p ${INSTALL_DIR}/static
    # ✨ 新增：创建 Go Worker 目录
    mkdir -p ${INSTALL_DIR}/go-worker
    mkdir -p ${INSTALL_DIR}/redis_data
    
    cd ${INSTALL_DIR}

    print_info "正在拉取最新代码 (X-Fusion Hybrid)..."
    curl -sS -O ${REPO_URL}/Dockerfile
    curl -sS -O ${REPO_URL}/requirements.txt
    curl -sS -o app/main.py ${REPO_URL}/app/main.py

    # ✨ 新增：拉取 Go 代码 (请确保 GitHub 上有这个文件)
    print_info "正在拉取 Go Worker 代码..."
    curl -sS -o go-worker/main.go ${REPO_URL}/go-worker/main.go

    # ✨ 新增：自动写入 Go Worker 的 Dockerfile (无需从 GitHub 拉取，直接生成)
    cat > go-worker/Dockerfile << EOF
FROM golang:alpine AS builder
WORKDIR /app
# 初始化模块并安装依赖
RUN go mod init x-fusion-worker || true
RUN go get github.com/redis/go-redis/v9
# 复制源码
COPY . .
# 编译
RUN go build -o worker main.go

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/worker .
CMD ["./worker"]
EOF

    # 静态资源
    print_info "正在更新资源文件..."
    curl -sS -o static/x-install.sh "${REPO_URL}/x-install.sh"
    curl -sS -o static/xterm.css "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css"
    curl -sS -o static/xterm.js "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"
    curl -sS -o static/xterm-addon-fit.js "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"
    curl -sS -o static/world.json "https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/world.json"

    if [ ! -f "app/main.py" ]; then
        print_error "Python 主程序下载失败！"
    fi
    if [ ! -f "go-worker/main.go" ]; then
        print_error "Go Worker 主程序下载失败！请检查 GitHub 仓库是否已包含 go-worker/main.go"
    fi

    # 初始化空文件
    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
    if [ ! -f "data/admin_config.json" ]; then echo "{}" > data/admin_config.json; fi
    if [ ! -f "Caddyfile" ]; then touch Caddyfile; fi
}

# --- ✨ 核心修改：生成包含 Go 和 Redis 的 Compose ---
generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4
    local SECRET=$5 
    local ENABLE_CADDY=$6 

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
      - ./data:/app/data
      - ./app/main.py:/app/main.py
      - ./static:/app/static
    depends_on:
      - redis
    environment:
      - TZ=Asia/Shanghai
      - XUI_USERNAME=${USER}
      - XUI_PASSWORD=${PASS}
      - XUI_SECRET_KEY=${SECRET}
      - REDIS_HOST=redis

  # ✨✨✨ 新增：Go 采集器 ✨✨✨
  go-worker:
    build: ./go-worker
    container_name: x-fusion-worker
    restart: always
    depends_on:
      - redis
    environment:
      - TZ=Asia/Shanghai
      - REDIS_HOST=redis

  # ✨✨✨ 新增：Redis 数据库 ✨✨✨
  redis:
    image: redis:alpine
    container_name: x-fusion-redis
    restart: always
    volumes:
      - ./redis_data:/data
    environment:
      - TZ=Asia/Shanghai

  subconverter:
    image: tindy2013/subconverter:latest
    container_name: subconverter
    restart: always
    ports:
      - "127.0.0.1:25500:25500"
    environment:
      - TZ=Asia/Shanghai
EOF

    # 追加 Caddy 配置
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
        docker compose up -d --build
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！登录地址: http://${ip_addr}:${port}"

    elif [ "$net_choice" == "3" ]; then
        read -p "请输入内部运行端口 [8081]: " port
        port=${port:-8081}
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在启动容器..."
        docker compose up -d --build
        
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
        docker compose up -d --build
        
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
    print_info "正在执行智能更新 (升级到混合架构)..."

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
    # 清理旧版可能存在的残留容器
    if docker ps -a | grep -q "xui_manager"; then docker rm -f xui_manager 2>/dev/null; fi

    # 4. 更新代码 (下载 Python + Go)
    deploy_base

    # 5. 重新生成配置 (增加 Redis 和 Go Worker)
    generate_compose "$BIND_IP" "$OLD_PORT" "$OLD_USER" "$OLD_PASS" "$OLD_KEY" "$ENABLE_CADDY"

    if [ "$ENABLE_CADDY" == "true" ] && [ -f "Caddyfile" ]; then
          EXISTING_DOMAIN=$(grep " {" Caddyfile | head -n 1 | awk '{print $1}')
          if [ -n "$EXISTING_DOMAIN" ]; then
              configure_caddy_docker "${EXISTING_DOMAIN}"
          fi
    fi

    # 6. 启动
    print_info "启动新容器 (Python + Go Worker + Redis)..."
    docker compose up -d --build
    print_success "更新完成！架构已升级。"
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
echo -e "${GREEN} X-Fusion Panel 一键管理脚本 (混合架构版) ${PLAIN}"
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
