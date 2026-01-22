#!/bin/bash

# ==============================================================================
# X-Fusion Panel 一键安装/管理脚本 (双模式：标准版 + 开发者版)
# ==============================================================================

# --- 全局变量 ---
PROJECT_NAME="x-fusion-panel"
INSTALL_DIR="/root/${PROJECT_NAME}"
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

# 📥 [开发者模式专属] 下载源码到本地
download_source_code() {
    print_info "正在下载源代码（开发者模式）..."
    
    mkdir -p ${INSTALL_DIR}/app
    mkdir -p ${INSTALL_DIR}/static

    # 下载核心文件
    curl -sS -o ${INSTALL_DIR}/Dockerfile ${REPO_URL}/Dockerfile
    curl -sS -o ${INSTALL_DIR}/requirements.txt ${REPO_URL}/requirements.txt
    curl -sS -o ${INSTALL_DIR}/app/main.py ${REPO_URL}/app/main.py
    
    # 下载静态资源 (示例，根据你仓库实际情况调整)
    curl -sS -o ${INSTALL_DIR}/static/xterm.css "https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css"
    curl -sS -o ${INSTALL_DIR}/static/xterm.js "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"
    curl -sS -o ${INSTALL_DIR}/static/xterm-addon-fit.js "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"
    
    # 简单的完整性检查
    if [ ! -f "${INSTALL_DIR}/app/main.py" ]; then
        print_error "源码下载失败，请检查网络或仓库地址。"
    fi
    print_success "源码下载完成！你可以在 ${INSTALL_DIR}/app 中直接修改代码。"
}

init_directories() {
    mkdir -p ${INSTALL_DIR}/data
    cd ${INSTALL_DIR}
    # 初始化空数据文件
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
    local MODE=$7  # 接收模式参数: "standard" 或 "dev"

    cat > ${INSTALL_DIR}/docker-compose.yml << EOF
version: '3.8'
services:
  x-fusion-panel:
EOF

    # 🔄 核心分歧点：根据模式写入不同的配置
    if [ "$MODE" == "dev" ]; then
        # === 开发者模式配置 ===
        cat >> ${INSTALL_DIR}/docker-compose.yml << EOF
    # 🛠️ [开发者模式] 使用本地构建 + 源码挂载
    build: .
    image: x-fusion-panel:dev
    volumes:
      - ./data:/app/data
      # 🔥 核心：将宿主机当前目录挂载到容器 /app
      # 这样你在宿主机修改 app/main.py，容器内立即生效
      - ./:/app
EOF
    else
        # === 标准模式配置 ===
        cat >> ${INSTALL_DIR}/docker-compose.yml << EOF
    # 🚀 [标准模式] 使用 Docker Hub 官方镜像
    image: sijuly0713/x-fusion-panel:latest
    volumes:
      - ./data:/app/data
EOF
    fi

    # === 公共配置部分 ===
    cat >> ${INSTALL_DIR}/docker-compose.yml << EOF
    container_name: x-fusion-panel
    restart: always
    ports:
      - "${BIND_IP}:${PORT}:8080"
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

    # 如果启用 Caddy
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
    
    sed -i "/${CADDY_MARK_START}/,/${CADDY_MARK_END}/d" "$DOCKER_CADDY_FILE"
    
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
    init_directories

    # 1. 选择模式
    echo "------------------------------------------------"
    echo "请选择安装模式："
    echo -e "  1) ${GREEN}标准模式 (Standard)${PLAIN} - 推荐，使用官方镜像，稳定纯净"
    echo -e "  2) ${YELLOW}开发者模式 (Developer)${PLAIN} - 下载源码到本地，修改代码重启即生效"
    echo "------------------------------------------------"
    read -p "选择模式 [1]: " mode_choice
    mode_choice=${mode_choice:-1}
    
    local MODE_TAG="standard"
    if [ "$mode_choice" == "2" ]; then
        MODE_TAG="dev"
        # 如果是开发者模式，必须先下载源码
        download_source_code
    fi

    # 2. 配置账号
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

    # 3. 配置网络
    echo "请选择访问方式："
    echo "  1) IP + 端口 (无 HTTPS)"
    echo "  2) 域名访问 (自动 HTTPS)"
    echo "  3) 域名访问 (共存模式)"
    read -p "选项 [2]: " net_choice
    net_choice=${net_choice:-2}

    local port=8081
    local bind="127.0.0.1"
    local caddy="false"

    if [ "$net_choice" == "1" ]; then
        read -p "开放端口 [8081]: " port
        port=${port:-8081}
        bind="0.0.0.0"
        generate_compose "$bind" "$port" "$admin_user" "$admin_pass" "$secret_key" "false" "$MODE_TAG"

    elif [ "$net_choice" == "3" ]; then
        read -p "内部端口 [8081]: " port
        port=${port:-8081}
        generate_compose "127.0.0.1" "$port" "$admin_user" "$admin_pass" "$secret_key" "false" "$MODE_TAG"
        print_info "共存模式配置生成完毕。"

    else
        read -p "输入域名: " domain
        if [ -z "$domain" ]; then print_error "域名不能为空"; fi
        configure_caddy_docker "$domain"
        generate_compose "127.0.0.1" "8081" "$admin_user" "$admin_pass" "$secret_key" "true" "$MODE_TAG"
    fi

    # 4. 启动
    print_info "正在启动容器..."
    if [ "$MODE_TAG" == "dev" ]; then
        print_info "开发者模式：正在构建镜像..."
        cd ${INSTALL_DIR} && docker compose up -d --build
    else
        print_info "标准模式：正在拉取镜像..."
        cd ${INSTALL_DIR} && docker compose up -d
    fi
    
    local ip_addr=$(curl -s ifconfig.me)
    if [ "$net_choice" == "1" ]; then
        print_success "安装成功！http://${ip_addr}:${port}"
    elif [ "$net_choice" == "2" ]; then
        print_success "安装成功！https://${domain}"
    else
        print_success "安装成功！请配置反代指向 127.0.0.1:${port}"
    fi
    
    if [ "$MODE_TAG" == "dev" ]; then
        echo -e "${YELLOW}提示：代码位于 ${INSTALL_DIR}/app，修改后执行 docker compose restart 即可生效。${PLAIN}"
    fi
}

update_panel() {
    if [ ! -d "${INSTALL_DIR}" ]; then print_error "未检测到安装目录。"; fi
    cd ${INSTALL_DIR}
    
    # 备份与提取配置
    if [ -f "docker-compose.yml" ]; then cp docker-compose.yml docker-compose.yml.bak; fi
    if [ ! -f "docker-compose.yml.bak" ]; then print_error "配置丢失。"; fi

    print_info "正在提取旧配置..."
    CONFIG_FILE="docker-compose.yml.bak"
    OLD_USER=$(grep "XUI_USERNAME=" $CONFIG_FILE | cut -d= -f2)
    OLD_PASS=$(grep "XUI_PASSWORD=" $CONFIG_FILE | cut -d= -f2)
    OLD_KEY=$(grep "XUI_SECRET_KEY=" $CONFIG_FILE | cut -d= -f2)
    PORT_LINE=$(grep ":8080" $CONFIG_FILE | head -n 1)
    
    # 检测是否为开发者模式
    IS_DEV="false"
    if grep -q "build: ." $CONFIG_FILE; then
        IS_DEV="true"
        print_warning "检测到当前为【开发者模式】"
    else
        print_info "检测到当前为【标准模式】"
    fi

    # 提取端口和 Caddy 状态 (逻辑同上，省略部分重复细节以保持脚本整洁)
    # ... (此处复用你原来的提取端口逻辑，为节省篇幅未展开，实际使用需保留) ...
    # 简易提取逻辑：
    if [[ $PORT_LINE == *"127.0.0.1"* ]]; then
        BIND_IP="127.0.0.1"
        OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*127.0.0.1:([0-9]+):8080.*/\1/' | tr -d ' "-')
        ENABLE_CADDY=$(grep -q "container_name: caddy" $CONFIG_FILE && echo "true" || echo "false")
    else
        BIND_IP="0.0.0.0"
        OLD_PORT=$(echo "$PORT_LINE" | sed -E 's/.*:([0-9]+):8080.*/\1/' | tr -d ' "-')
        if [[ $OLD_PORT == *"0.0.0.0"* ]]; then OLD_PORT=$(echo "$OLD_PORT" | cut -d: -f2); fi
        ENABLE_CADDY="false"
    fi

    print_info "停止旧容器..."
    docker compose down

    # 根据模式执行更新
    if [ "$IS_DEV" == "true" ]; then
        # 开发者模式：询问是否覆盖代码
        read -p "是否从仓库拉取最新代码覆盖本地修改？(y/n) [n]: " pull_code
        if [ "$pull_code" == "y" ]; then
            download_source_code
            print_success "代码已更新。"
        else
            print_info "跳过代码更新，保留本地修改。"
        fi
        # 重新生成配置
        generate_compose "$BIND_IP" "$OLD_PORT" "$OLD_USER" "$OLD_PASS" "$OLD_KEY" "$ENABLE_CADDY" "dev"
        print_info "正在重新构建..."
        docker compose up -d --build
    else
        # 标准模式：清理旧代码文件
        rm -rf app/ static/ Dockerfile requirements.txt
        generate_compose "$BIND_IP" "$OLD_PORT" "$OLD_USER" "$OLD_PASS" "$OLD_KEY" "$ENABLE_CADDY" "standard"
        print_info "正在拉取最新镜像..."
        docker compose pull
        docker compose up -d
        docker image prune -f
    fi
    
    # 恢复 Caddy 配置
    if [ "$ENABLE_CADDY" == "true" ] && [ -f "Caddyfile" ]; then
          EXISTING_DOMAIN=$(grep " {" Caddyfile | head -n 1 | awk '{print $1}')
          if [ -n "$EXISTING_DOMAIN" ]; then configure_caddy_docker "${EXISTING_DOMAIN}"; fi
    fi

    print_success "更新完成！"
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
echo -e "${GREEN}    X-Fusion Panel 一键管理脚本          ${PLAIN}"
echo -e "${GREEN}=========================================${PLAIN}"
echo -e "  1. 安装面板 (支持 标准版/开发版)"
echo -e "  2. 更新面板"
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
