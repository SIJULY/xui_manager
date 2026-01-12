# 编译阶段
FROM golang:alpine AS builder

WORKDIR /app
COPY . .

# 编译成名为 worker 的二进制文件
RUN go build -o worker main.go

# 运行阶段 (使用极小的 alpine 镜像)
FROM alpine:latest

WORKDIR /app
# 从编译阶段把二进制文件复制过来
COPY --from=builder /app/worker .

# 启动
CMD ["./worker"]
