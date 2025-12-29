FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装基础依赖 (Ping工具等)
RUN apt-get update && apt-get install -y iputils-ping curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/main.py app/main.py

# 这里的 CMD 路径要和 COPY 对应
CMD ["python", "app/main.py"]
