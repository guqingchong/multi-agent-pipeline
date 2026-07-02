# 部署相关文件说明

此目录包含用于不同环境部署 multi-agent-pipeline 系统的脚本和配置文件。

## 文件清单

- `setup.sh` - Linux/Mac 环境一键安装脚本
- `start.sh` - Linux/Mac 环境启动脚本
- `verify-runtime.sh` - Linux/Mac 环境验证脚本
- `docker-compose.yml` - Docker Compose 部署配置
- `Dockerfile` - 容器化构建文件
- `requirements.txt` - Python 依赖列表

## 使用方法

### 本地安装（Linux/Mac）

1. 给脚本添加执行权限：
   ```bash
   chmod +x setup.sh start.sh verify-runtime.sh
   ```

2. 运行安装脚本：
   ```bash
   ./setup.sh
   ```

3. 验证安装：
   ```bash
   ./verify-runtime.sh
   ```

4. 启动应用：
   ```bash
   ./start.sh
   ```

### Docker 部署

1. 构建并启动服务：
   ```bash
   docker-compose up --build
   ```

2. 或以后台模式运行：
   ```bash
   docker-compose up --build -d
   ```