FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装基础依赖
RUN apt-get update && apt-get install -y iputils-ping curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 核心修改 ---
# ✅ 把当前目录下的所有文件（包括 app代码、static资源、templates等）全部复制进去
COPY . .

# 这里的 CMD 路径要对应
CMD ["python", "app/main.py"]
