import requests
import time

# 等待服务器启动
time.sleep(3)

try:
    # 测试API文档页面是否可访问
    response = requests.get("http://localhost:8000/docs")
    
    if response.status_code == 200:
        print("✅ API文档页面可访问！")
        print(f"状态码: {response.status_code}")
        print("API文档已成功配置在 /docs 路径")
    else:
        print(f"❌ API文档页面访问失败，状态码: {response.status_code}")
        
    # 测试其他API端点
    endpoints = [
        "/finance/calculate",
        "/knowledge/search?query=test",
        "/documents/template",
        "/projects/test-project",
        "/system/status"
    ]
    
    for endpoint in endpoints:
        response = requests.get(f"http://localhost:8000{endpoint}")
        if response.status_code == 200:
            print(f"✅ {endpoint} 端点正常")
        else:
            print(f"❌ {endpoint} 端点异常，状态码: {response.status_code}")
            
except requests.exceptions.ConnectionError:
    print("❌ 无法连接到服务器，请确保FastAPI服务器正在运行")
except Exception as e:
    print(f"❌ 发生错误: {str(e)}")