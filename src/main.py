from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import uvicorn

# 创建FastAPI应用实例，配置文档URL
app = FastAPI(
    title="Multi-Agent Pipeline API",
    description="多智能体协作编码系统的API文档",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)


# 数据模型定义
class FeatureRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: str
    status: str = "pending"


class ProjectInfo(BaseModel):
    project_id: str
    name: str
    description: str
    features: List[FeatureRequest]


# 财务模型相关数据结构
class FinancialInput(BaseModel):
    """
    财务计算输入参数
    """
    initial_investment: float
    annual_revenue: float
    annual_costs: float
    project_years: int
    discount_rate: float = 0.05


class FinancialOutput(BaseModel):
    """
    财务计算输出结果
    """
    npv: float
    irr: float
    payback_period: float
    roi: float
    cash_flows: List[float]
    yearly_breakdown: List[dict]


class BudgetItem(BaseModel):
    """
    预算项目
    """
    category: str
    amount: float
    description: str


class BudgetRequest(BaseModel):
    """
    预算设置请求
    """
    project_id: str
    items: List[BudgetItem]
    total_budget: float
    currency: str = "CNY"


class BudgetResponse(BaseModel):
    """
    预算设置响应
    """
    status: str
    message: str
    budget_id: str
    total_allocated: float


# 财务测算相关路由
@app.post("/finance/calculate", 
          tags=["财务测算"], 
          description="基于输入参数执行全面的财务计算和分析",
          summary="财务计算分析")
async def calculate_financials(financial_input: FinancialInput):
    """
    基于提供的财务参数执行NPV、IRR、投资回报率等财务指标计算
    
    参数:
    - initial_investment: 初始投资金额
    - annual_revenue: 年收入
    - annual_costs: 年成本
    - project_years: 项目年限
    - discount_rate: 折现率
    
    返回:
    - npv: 净现值
    - irr: 内部收益率
    - payback_period: 投资回收期
    - roi: 投资回报率
    - cash_flows: 现金流明细
    - yearly_breakdown: 年度分解详情
    """
    # 这里是模拟的财务计算逻辑，实际实现会更复杂
    import math
    
    # 计算净现金流量
    net_cash_flows = [-(financial_input.initial_investment)]  # 第0年为负的初始投资
    for year in range(1, financial_input.project_years + 1):
        net_cash_flows.append(financial_input.annual_revenue - financial_input.annual_costs)
    
    # 计算净现值(NPV)
    npv = sum(cf / ((1 + financial_input.discount_rate) ** t) for t, cf in enumerate(net_cash_flows))
    
    # 简化的内部收益率(IRR)计算（实际应使用迭代法）
    irr = 0.0  # 简化处理，实际需用数值方法求解
    
    # 计算投资回收期
    cumulative_cf = 0
    payback_period = 0
    for year, cf in enumerate(net_cash_flows):
        cumulative_cf += cf
        if cumulative_cf >= 0 and payback_period == 0:
            payback_period = year
            break
    
    # 计算投资回报率(ROI)
    total_return = sum(net_cash_flows[1:])  # 排除初始投资
    roi = total_return / financial_input.initial_investment if financial_input.initial_investment != 0 else 0
    
    # 构建年度分解数据
    yearly_breakdown = []
    for year in range(financial_input.project_years + 1):
        yearly_data = {
            "year": year,
            "cash_flow": net_cash_flows[year],
            "cumulative_cash_flow": sum(net_cash_flows[:year+1]),
            "discounted_cash_flow": net_cash_flows[year] / ((1 + financial_input.discount_rate) ** year) if year > 0 else net_cash_flows[year]
        }
        yearly_breakdown.append(yearly_data)
    
    result = FinancialOutput(
        npv=npv,
        irr=irr,
        payback_period=payback_period,
        roi=roi,
        cash_flows=net_cash_flows,
        yearly_breakdown=yearly_breakdown
    )
    
    return {
        "status": "success",
        "message": "财务计算完成",
        "data": result
    }


@app.post("/finance/budget", 
          tags=["财务测算"], 
          description="设置或更新项目预算配置",
          summary="设置项目预算",
          response_model=BudgetResponse)
async def set_budget(budget_request: BudgetRequest):
    """
    根据预算请求设置项目预算
    
    参数:
    - project_id: 项目ID
    - items: 预算项目列表
    - total_budget: 总预算金额
    - currency: 货币类型
    
    返回:
    - status: 请求状态
    - message: 响应消息
    - budget_id: 预算ID
    - total_allocated: 已分配总额
    """
    # 这里是模拟的预算设置逻辑
    import uuid
    
    # 验证预算金额是否匹配
    calculated_total = sum(item.amount for item in budget_request.items)
    if abs(calculated_total - budget_request.total_budget) > 0.01:  # 允许小额差异
        raise HTTPException(status_code=400, detail="预算项目总金额与总预算不匹配")
    
    budget_id = f"BUDGET-{str(uuid.uuid4())[:8].upper()}"
    
    response = BudgetResponse(
        status="success",
        message="预算设置成功",
        budget_id=budget_id,
        total_allocated=calculated_total
    )
    
    return response


# 知识检索相关路由
@app.get("/knowledge/search", 
         tags=["知识检索"], 
         description="在知识库中搜索与查询相关的知识点",
         summary="知识库搜索")
async def search_knowledge(query: str):
    """
    在知识库中搜索与查询相关的知识点
    
    参数:
    - query: 搜索关键词
    
    返回:
    - results: 搜索结果列表
    """
    # 模拟搜索结果
    results = [
        {"id": 1, "title": f"{query}相关知识点1", "content": f"关于{query}的相关内容1"},
        {"id": 2, "title": f"{query}相关知识点2", "content": f"关于{query}的相关内容2"}
    ]
    return {
        "status": "success",
        "query": query,
        "results": results
    }


class KnowledgeItem(BaseModel):
    """
    知识条目模型
    """
    title: str
    content: str
    tags: List[str] = []


@app.post("/knowledge/add", 
          tags=["知识检索"], 
          description="向知识库添加新的知识条目",
          summary="添加知识条目")
async def add_knowledge(knowledge_item: KnowledgeItem):
    """
    添加新的知识条目到知识库
    
    参数:
    - knowledge_item: 包含标题、内容和标签的知识条目
    
    返回:
    - status: 请求状态
    - message: 响应消息
    - item_id: 新增条目的ID
    """
    import uuid
    item_id = f"KNOW-{str(uuid.uuid4())[:8].upper()}"
    
    return {
        "status": "success",
        "message": "知识条目添加成功",
        "item_id": item_id,
        "item": knowledge_item
    }


# 文档生成相关路由
class DocumentRequest(BaseModel):
    """
    文档生成请求模型
    """
    template_id: str
    data: dict
    format: str = "pdf"
    output_filename: Optional[str] = None


class DocumentInfo(BaseModel):
    """
    文档信息模型
    """
    doc_id: str
    format: str
    generated_at: str
    file_size: Optional[int] = None
    download_url: Optional[str] = None


class DocumentGenerationResponse(BaseModel):
    """
    文档生成响应模型
    """
    status: str
    message: str
    document_info: DocumentInfo


@app.post("/documents/generate", 
          tags=["文档生成"], 
          description="根据指定模板和数据生成文档",
          summary="生成文档",
          response_model=DocumentGenerationResponse)
async def generate_document(doc_request: DocumentRequest):
    """
    根据指定模板和数据生成文档
    
    参数:
    - template_id: 使用的模板ID
    - data: 用于填充模板的数据
    - format: 输出格式 (pdf, docx, html等)
    - output_filename: 可选的输出文件名
    
    返回:
    - status: 请求状态
    - message: 响应消息
    - document_info: 生成的文档信息
    """
    import uuid
    from datetime import datetime
    
    doc_id = f"DOC-{str(uuid.uuid4())[:8].upper()}"
    
    document_info = DocumentInfo(
        doc_id=doc_id,
        format=doc_request.format,
        generated_at=datetime.now().isoformat(),
        file_size=1024000,  # 模拟文件大小
        download_url=f"/documents/download/{doc_id}"  # 模拟下载链接
    )
    
    response = DocumentGenerationResponse(
        status="success",
        message="文档生成成功",
        document_info=document_info
    )
    
    return response


class TemplateInfo(BaseModel):
    """
    模板信息模型
    """
    id: str
    name: str
    description: str
    category: str
    supported_formats: List[str]


class TemplateListResponse(BaseModel):
    """
    模板列表响应模型
    """
    status: str
    templates: List[TemplateInfo]


@app.get("/documents/template", 
         tags=["文档生成"], 
         description="获取系统中可用的文档模板列表",
         summary="获取文档模板")
async def get_document_templates():
    """
    获取系统中可用的文档模板列表
    
    返回:
    - status: 请求状态
    - templates: 模板列表
    """
    templates = [
        TemplateInfo(
            id="tpl-001", 
            name="项目计划书", 
            description="标准项目计划书模板", 
            category="project", 
            supported_formats=["pdf", "docx"]
        ),
        TemplateInfo(
            id="tpl-002", 
            name="技术方案", 
            description="技术解决方案模板", 
            category="technical", 
            supported_formats=["pdf", "docx", "html"]
        ),
        TemplateInfo(
            id="tpl-003", 
            name="测试报告", 
            description="测试结果报告模板", 
            category="testing", 
            supported_formats=["pdf", "docx", "html", "md"]
        )
    ]
    
    return {
        "status": "success",
        "templates": templates
    }


# 项目管理相关路由
class ProjectDetail(BaseModel):
    """
    项目详细信息模型
    """
    id: str
    name: str
    description: str
    status: str
    progress: int
    start_date: str
    end_date: str
    team_size: int
    budget_used: float
    budget_total: float


class ProjectCreationRequest(BaseModel):
    """
    项目创建请求模型
    """
    name: str
    description: str
    start_date: str
    end_date: str
    team_size: int = 1
    budget_total: float = 0.0


class ProjectCreationResponse(BaseModel):
    """
    项目创建响应模型
    """
    status: str
    message: str
    project_id: str
    project_url: str


@app.get("/projects/{project_id}", 
         tags=["项目管理"], 
         description="获取指定项目ID的详细信息",
         summary="获取项目详情")
async def get_project(project_id: str):
    """
    根据ID获取项目详细信息
    
    参数:
    - project_id: 项目唯一标识符
    
    返回:
    - status: 请求状态
    - project: 项目详细信息
    """
    project_detail = ProjectDetail(
        id=project_id,
        name=f"项目_{project_id}",
        description=f"这是项目{project_id}的详细描述",
        status="active",
        progress=65,
        start_date="2023-01-01",
        end_date="2023-12-31",
        team_size=5,
        budget_used=7500.0,
        budget_total=10000.0
    )
    
    return {
        "status": "success",
        "project": project_detail
    }


@app.post("/projects/create", 
          tags=["项目管理"], 
          description="创建一个新的项目",
          summary="创建项目",
          response_model=ProjectCreationResponse)
async def create_project(project_creation_request: ProjectCreationRequest):
    """
    创建一个新的项目
    
    参数:
    - name: 项目名称
    - description: 项目描述
    - start_date: 开始日期
    - end_date: 结束日期
    - team_size: 团队规模
    - budget_total: 总预算
    
    返回:
    - status: 请求状态
    - message: 响应消息
    - project_id: 新创建的项目ID
    - project_url: 项目访问URL
    """
    import uuid
    
    project_id = f"PROJ-{str(uuid.uuid4())[:8].upper()}"
    project_url = f"/projects/{project_id}"
    
    response = ProjectCreationResponse(
        status="success",
        message="项目创建成功",
        project_id=project_id,
        project_url=project_url
    )
    
    return response


class UpdateFeaturesRequest(BaseModel):
    """
    更新项目特性的请求模型
    """
    features: List[FeatureRequest]


class UpdateFeaturesResponse(BaseModel):
    """
    更新项目特性的响应模型
    """
    status: str
    message: str
    updated_features_count: int
    project_id: str


@app.put("/projects/{project_id}/features", 
         tags=["项目管理"], 
         description="更新指定项目的特性列表",
         summary="更新项目特性",
         response_model=UpdateFeaturesResponse)
async def update_project_features(project_id: str, update_request: UpdateFeaturesRequest):
    """
    更新指定项目的特性列表
    
    参数:
    - project_id: 项目ID
    - features: 特性列表
    
    返回:
    - status: 请求状态
    - message: 响应消息
    - updated_features_count: 更新的特性数量
    - project_id: 项目ID
    """
    response = UpdateFeaturesResponse(
        status="success",
        message=f"项目 {project_id} 的特性已更新",
        updated_features_count=len(update_request.features),
        project_id=project_id
    )
    
    return response


# 系统管理相关路由
class SystemInfo(BaseModel):
    """
    系统信息模型
    """
    uptime: str
    version: str
    active_agents: int
    cpu_usage: str
    memory_usage: str
    disk_usage: str
    network_io: str


class SystemStatusResponse(BaseModel):
    """
    系统状态响应模型
    """
    status: str
    system_info: SystemInfo


@app.get("/system/status", 
         tags=["系统管理"], 
         description="获取系统当前运行状态",
         summary="获取系统状态",
         response_model=SystemStatusResponse)
async def get_system_status():
    """
    获取系统当前运行状态
    
    返回:
    - status: 请求状态
    - system_info: 系统信息详情
    """
    system_info = SystemInfo(
        uptime="7 days, 12:34:56",
        version="1.0.0",
        active_agents=5,
        cpu_usage="45%",
        memory_usage="60%",
        disk_usage="75%",
        network_io="1.2GB in, 0.8GB out"
    )
    
    response = SystemStatusResponse(
        status="success",
        system_info=system_info
    )
    
    return response


class SystemConfig(BaseModel):
    """
    系统配置模型
    """
    debug_mode: bool = False
    max_workers: int = 10
    timeout_seconds: int = 30
    retry_attempts: int = 3
    log_level: str = "INFO"
    enable_metrics: bool = True


class ConfigUpdateRequest(BaseModel):
    """
    配置更新请求模型
    """
    config: SystemConfig


class ConfigUpdateResponse(BaseModel):
    """
    配置更新响应模型
    """
    status: str
    message: str
    updated_config: SystemConfig


@app.get("/system/config", 
         tags=["系统管理"], 
         description="获取系统当前配置信息",
         summary="获取系统配置")
async def get_system_config():
    """
    获取系统当前配置信息
    
    返回:
    - status: 请求状态
    - config: 当前系统配置
    """
    config = SystemConfig()
    
    return {
        "status": "success",
        "config": config
    }


@app.post("/system/config", 
          tags=["系统管理"], 
          description="更新系统配置参数",
          summary="更新系统配置",
          response_model=ConfigUpdateResponse)
async def update_system_config(config_request: ConfigUpdateRequest):
    """
    更新系统配置参数
    
    参数:
    - config: 新的系统配置
    
    返回:
    - status: 请求状态
    - message: 响应消息
    - updated_config: 更新后的配置
    """
    response = ConfigUpdateResponse(
        status="success",
        message="系统配置已更新",
        updated_config=config_request.config
    )
    
    return response


# 启动应用
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)