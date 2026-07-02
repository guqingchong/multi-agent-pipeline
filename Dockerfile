# Dockerfile — multi-agent-pipeline 容器化构建文件
#
# 自动生成于: delivery.py (W5-Q06)

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY . .

# 设置PYTHONPATH
ENV PYTHONPATH=/app/src

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 默认命令
CMD ["python", "src/pipeline.py", "--help"]