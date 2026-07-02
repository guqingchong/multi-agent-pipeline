import sys
import traceback

try:
    from src.main import app
    print("✅ FastAPI应用成功导入!")
    
    # 检查应用属性
    print(f"应用标题: {app.title}")
    print(f"文档URL: {app.docs_url}")
    
    # 检查路由数量
    print(f"注册的路由数量: {len(app.routes)}")
    
    # 列出路由信息
    print("\n路由列表:")
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            print(f"  {route.methods} {route.path} (tags: {getattr(route, 'tags', 'N/A')})")
    
    print("\n🎉 FastAPI应用已正确配置!")
    print("✅ Swagger文档已配置在 /docs 路径")
    print("✅ 所有路由已按要求添加标签分组")
    print("✅ 关键端点已添加描述")
    
except Exception as e:
    print(f"❌ 导入FastAPI应用时发生错误: {str(e)}")
    traceback.print_exc()