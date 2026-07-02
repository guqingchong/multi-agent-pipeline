# multi-agent-pipeline 部署指南

这是一个多智能体协作编码系统的部署文件集合，支持多种部署方式：

## 部署选项

- **Windows**: 使用 `setup.ps1` 和 `start.ps1` 脚本
- **Linux/macOS**: 使用 `setup.sh` 和 `start.sh` 脚本  
- **Docker**: 使用 `docker-compose.yml` 配置

## 文件说明

- `setup.sh` - Linux/macOS 环境一键安装脚本
- `start.sh` - Linux/macOS 环境启动脚本
- `verify-runtime.sh` - Linux/macOS 环境验证脚本
- `docker-compose.yml` - Docker Compose 部署配置
- `Dockerfile` - 容器化构建文件
- `requirements.txt` - Python 依赖列表
- `DEPLOY.md` - 详细的部署指南文档

## 快速开始

### Linux/macOS
```bash
# 给脚本添加执行权限
chmod +x setup.sh start.sh verify-runtime.sh

# 安装依赖
./setup.sh

# 验证环境
./verify-runtime.sh

# 启动应用
./start.sh
```

### Docker
```bash
# 构建并启动
docker-compose up --build
```

更多详细信息，请参阅 [DEPLOY.md](DEPLOY.md) 文档。