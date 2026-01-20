#!/bin/bash

# ==============================================================================
# X-Fusion Panel 一键安装/管理脚本 (Docker Hub 发行版 + 智能清理)
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="x-fusion-panel"
INSTALL_DIR="/root/${PROJECT_NAME}"
OLD_INSTALL_DIR="/root/xui_manager" 

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
    local wait_time=0
    local timeout=60
    while fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
          fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
          fuser /var/lib/apt/lists/lock >/dev/null 2>&1 ; do
        echo -e "${YELLOW}[系统] 等待系统更新锁释放 (${wait_time}s)...${PLAIN}"
        sleep 10
        ((wait_time+=10))
        if [ "$wait_time" -ge "$timeout" ]; then
            killall apt apt-get dpkg 2>/dev/null
            rm -f /var/lib/apt/lists/lock /var/cache/apt/archives/lock /var/lib/dpkg/lock*
            dpkg --configure -a
            break
        fi
    done
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        print_info "正在安装 Docker..."
        wait_for_apt_lock
        curl -fsSL https://get.docker.com | bash
        systemctl enable docker
        systemctl start docker
    fi
    if ! docker compose version &> /dev/null; then
        print_info "正在安装 Docker Compose..."
        wait_for_apt_lock
        apt-get update && apt-get install -y docker-compose-plugin
    fi
}

# --- 核心功能 ---

migrate_old_data() {
    # 迁移旧版目录结构（如果有）
    if [ -d "$OLD_INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
        print_warning "正在迁移旧版数据..."
        cd "$OLD_INSTALL_DIR"
        docker compose down 2>/dev/null
        cd /root
        mv "$OLD_INSTALL_DIR" "$INSTALL_DIR"
        # 重命名旧的 compose 文件以防冲突
        if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
            mv "$INSTALL_DIR/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml.bak"
        fi
    fi
}

init_directories() {
    # 只创建必要的配置目录，不下载代码
    mkdir -p ${INSTALL_DIR}/data
    cd ${INSTALL_DIR}

    # 初始化空数据文件，防止 Docker 自动创建为文件夹导致报错
    if [ ! -f "data/servers.json" ]; then echo "[]" > data/servers.json; fi
    if [ ! -f "data/subscriptions.json" ]; then echo "[]" > data/subscriptions.json; fi
    if [ ! -f "data/admin_config.json" ]; then echo "{}" > data/admin_config.json; fi
    if [ ! -f "Caddyfile" ]; then touch Caddyfile; fi
}

generate_compose() {
    local BIND_IP=$1
    local PORT=$2
    local USER=$3
    local PASS=$4
    local SECRET=$5 
    local ENABLE_CADDY=$6 

    # 生成 docker-compose.yml
    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
version: '3.8'
services:
  x-fusion-panel:
    # 🔥 核心：直接使用 Docker Hub 镜像 (无需本地构建)
    image: sijuly0713/x-fusion-panel:latest
    container_name: x-fusion-panel
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
    volumes:
      # 🔥 核心：只挂载数据，不挂载代码
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

    # 如果启用 Caddy，追加配置
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
    
    # 清理旧标记
    sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$DOCKER_CADDY_FILE"
    
    # 写入新配置
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
    check_docker
    migrate_old_data
    init_directories

    # 默认值
    local def_user="admin"
    local def_pass="admin"
    local def_key=$(cat /proc/sys/kernel/random/uuid | tr -d '-')

    echo "------------------------------------------------"
    read -p "设置账号 [${def_user}]: " admin_user
    admin_user=${admin_user:-$def_user}
    read -p "设置密码 [${def_pass}]: " admin_pass
    admin_pass=${admin_pass:-$def_pass}
    read -p "设置密钥 (回车跳过): " input_key
    secret_key=${input_key:-$def_key}
    echo "------------------------------------------------"

    echo "请选择访问方式："
    echo "  1) IP + 端口 (无 HTTPS)"
    echo "  2) 域名访问 (自动 HTTPS，全新机器)"
    echo "  3) 域名访问 (共存模式，已有 Nginx)"
    read -p "选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    if [ "$net_choice" == "1" ]; then
        read -p "开放端口 [8081]: " port
        port=${port:-8081}
        generate_compose "0.0.0.0" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在拉取镜像并启动..."
        docker compose up -d
        ip_addr=$(curl -s ifconfig.me)
        print_success "安装成功！http://${ip_addr}:${port}"

    elif [ "$net_choice" == "3" ]; then
        read -p "内部端口 [8081]: " port
        port=${port:-8081}
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "false"
        
        print_info "正在拉取镜像并启动..."
        docker compose up -d
        print_success "容器已启动 (共存模式)。请手动配置宿主机 Nginx 反代 127.0.0.1:${port}"

    else
        read -p "输入域名: " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        port=8081
        
        configure_caddy_docker "$domain"
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "true"
        
        print_info "正在拉取镜像并启动..."
        docker compose up -d
        print_success "安装成功！https://${domain}"
    fi
}

update_panel() {
    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录。"; fi
    cd ${INSTALL_DIR}
    
    # 备份当前配置
    if [ -f "docker-compose.yml" ]; then
        cp docker-compose.yml docker-compose.yml.bak
    fi
    
    if [ ! -f "docker-compose.yml.bak" ]; then print_error "配置文件丢失，无法提取旧配置。"; fi

    print_info "正在提取旧配置..."
    CONFIG_FILE="docker-compose.yml.bak"

    # 1. 提取旧参数
    OLD_USER=$(grep "XUI_USERNAME=" $CONFIG_FILE | cut -d= -f2)
    OLD_PASS=$(grep "XUI_PASSWORD=" $CONFIG_FILE | cut -d= -f2)
    OLD_KEY=$(grep "XUI_SECRET_KEY=" $CONFIG_FILE | cut -d= -f2)
    PORT_LINE=$(grep ":8080" $CONFIG_FILE | head -n 1)
    
    # 2. 判断网络模式
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

    # 3. 停止并清理旧容器
    print_info "停止旧容器..."
    docker compose down
    if docker ps -a | grep -q "xui_manager"; then docker rm -f xui_manager 2>/dev/null; fi

    # =======================================================
    # ✨✨✨ 自动清理：删除旧版遗留的源码文件 ✨✨✨
    # =======================================================
    print_info "正在清理旧版冗余源码文件..."
    rm -rf app/
    rm -rf static/
    rm -f Dockerfile requirements.txt x_fusion_agent.py
    # 绝对保留 data/ 和 Caddyfile
    # =======================================================

    # 4. 重新初始化目录 (确保 data 存在)
    init_directories

    # 5. 重新生成配置
    generate_compose "$BIND_IP" "$OLD_PORT" "$OLD_USER" "$OLD_PASS" "$OLD_KEY" "$ENABLE_CADDY"

    # 如果是 Caddy 模式，恢复 Caddyfile 配置
    if [ "$ENABLE_CADDY" == "true" ] && [ -f "Caddyfile" ]; then
          EXISTING_DOMAIN=$(grep " {" Caddyfile | head -n 1 | awk '{print $1}')
          if [ -n "$EXISTING_DOMAIN" ]; then
              configure_caddy_docker "${EXISTING_DOMAIN}"
          fi
    fi

    # 6. 拉取最新镜像并启动
    print_info "正在拉取最新 Docker 镜像..."
    docker compose pull
    print_info "正在重启容器..."
    docker compose up -d
    
    # 清理无用的旧镜像
    docker image prune -f
    print_success "更新完成！旧版冗余文件已清理。"
}

uninstall_panel() {
    read -p "确定卸载并删除所有数据吗？(y/n): " confirm
    if [ "$confirm" == "y" ]; then
        if [ -d "${INSTALL_DIR}" ]; then
            cd ${INSTALL_DIR}
            docker compose down
            cd /root
            rm -rf ${INSTALL_DIR}
        fi
        print_success "卸载完成。"
    fi
}

# --- 主入口 ---
check_root
clear
echo -e "${GREEN}=========================================${PLAIN}"
echo -e "${GREEN}   X-Fusion Panel 一键管理 (Docker Hub版)   ${PLAIN}"
echo -e "${GREEN}=========================================${PLAIN}"
echo -e "  1. 安装面板"
echo -e "  2. 更新面板 (自动清理旧文件)"
echo -e "  3. 卸载面板"
echo -e "  0. 退出"
echo -e ""
read -p "请输入选项: " choice

case $choice in
    1) install_panel ;;
    2) update_panel ;;
    3) uninstall_panel ;;
    0) exit 0 ;;
    *) print_error "无效选项" ;;
esac
