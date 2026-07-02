import requests
import json

BASE_URL = "http://localhost:8000"

def test_finance_calculate():
    """测试财务计算API"""
    url = f"{BASE_URL}/finance/calculate"
    payload = {
        "initial_investment": 10000,
        "annual_revenue": 5000,
        "annual_costs": 2000,
        "project_years": 5,
        "discount_rate": 0.05
    }
    
    print("Testing finance/calculate endpoint...")
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    print("-" * 50)


def test_finance_budget():
    """测试预算设置API"""
    url = f"{BASE_URL}/finance/budget"
    payload = {
        "project_id": "PROJ-TEST-001",
        "items": [
            {
                "category": "开发",
                "amount": 5000,
                "description": "开发人员费用"
            },
            {
                "category": "硬件",
                "amount": 3000,
                "description": "服务器费用"
            }
        ],
        "total_budget": 8000,
        "currency": "CNY"
    }
    
    print("Testing finance/budget endpoint...")
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    print("-" * 50)


def test_get_project():
    """测试获取项目信息API"""
    url = f"{BASE_URL}/projects/TEST001"
    
    print("Testing projects/{id} endpoint...")
    response = requests.get(url)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    print("-" * 50)


def test_get_openapi():
    """测试获取OpenAPI规范"""
    url = f"{BASE_URL}/openapi.json"
    
    print("Testing openapi.json endpoint...")
    response = requests.get(url)
    print(f"Status Code: {response.status_code}")
    data = response.json()
    print(f"Title: {data['info']['title']}")
    print(f"Description: {data['info']['description']}")
    print(f"Number of paths: {len(data['paths'])}")
    print("-" * 50)


if __name__ == "__main__":
    print("Testing FastAPI endpoints...\n")
    
    test_get_openapi()
    test_finance_calculate()
    test_finance_budget()
    test_get_project()
    
    print("All tests completed!")