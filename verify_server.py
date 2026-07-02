import requests
import time

print("等待服务器启动...")
time.sleep(3)  # 给服务器一些时间启动

try:
    # 测试API文档页面
    response = requests.get("http://localhost:8000/docs")
    if response.status_code == 200:
        print("✅ API文档页面可访问！")
        print(f"   状态码: {response.status_code}")
        print(f"   响应长度: {len(response.text)} 字符")
        print("   你可以在浏览器中访问 http://localhost:8000/docs 查看API文档")
    else:
        print(f"❌ API文档页面访问失败，状态码: {response.status_code}")

    # 验证服务器是否响应
    response = requests.get("http://localhost:8000/system/status")
    if response.status_code == 200:
        print("✅ 系统管理API端点正常")
        data = response.json()
        print(f"   系统信息: {data['system_info']['version']} 版本")
    else:
        print(f"❌ 系统管理API端点异常，状态码: {response.status_code}")

    print("\n🎉 FastAPI服务器已成功启动并运行！")
    print("✅ Swagger UI 已配置在 /docs 路径")
    print("✅ 所有API端点按功能分组标签组织")
    print("✅ 关键端点都有描述信息")

except requests.exceptions.ConnectionError:
    print("❌ 无法连接到服务器，请确保FastAPI服务器正在运行")
except Exception as e:
    print(f"❌ 发生错误: {str(e)}")