"""
辩论会话管理模块
用于管理辩论过程中的上下文信息、状态和文件
"""

import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List


class DebateSession:
    """
    辩论会话类，管理单次辩论的上下文和状态
    """
    
    def __init__(self, session_id: Optional[str] = None, name: str = ""):
        self.session_id = session_id or str(uuid.uuid4())
        self.name = name or f"Debate_Session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        
        # 存储辩论相关的上下文数据
        self.context_data: Dict[str, Any] = {}
        
        # 存储各参与方的发言记录
        self.statements: List[Dict[str, Any]] = []
        
        # 存储当前辩论阶段
        self.phase = "initial"
        
        # 存储预算相关信息（时间、迭代次数等）
        self.budget = {
            "max_iterations": 10,
            "current_iteration": 0,
            "max_time": 3600,  # 默认1小时
            "start_time": datetime.now()
        }
        
        # 存储收敛判断相关信息
        self.convergence_data = {
            "has_converged": False,
            "convergence_threshold": 0.8,
            "agreement_score": 0.0
        }

    def add_context(self, key: str, value: Any):
        """添加上下文信息"""
        self.context_data[key] = value
        self.updated_at = datetime.now()

    def get_context(self, key: str, default: Any = None) -> Any:
        """获取上下文信息"""
        return self.context_data.get(key, default)

    def add_statement(self, speaker: str, statement: str, metadata: Optional[Dict[str, Any]] = None):
        """添加发言记录"""
        statement_record = {
            "id": str(uuid.uuid4()),
            "speaker": speaker,
            "statement": statement,
            "timestamp": datetime.now(),
            "metadata": metadata or {}
        }
        self.statements.append(statement_record)
        self.updated_at = datetime.now()

    def update_phase(self, phase: str):
        """更新辩论阶段"""
        self.phase = phase
        self.updated_at = datetime.now()

    def increment_iteration(self):
        """递增迭代计数"""
        self.budget["current_iteration"] += 1
        self.updated_at = datetime.now()

    def is_budget_exhausted(self) -> bool:
        """检查是否超出预算"""
        # 检查迭代次数
        if self.budget["current_iteration"] >= self.budget["max_iterations"]:
            return True
            
        # 检查时间限制
        elapsed_time = (datetime.now() - self.budget["start_time"]).total_seconds()
        if elapsed_time > self.budget["max_time"]:
            return True
            
        return False

    def update_convergence(self, agreement_score: float):
        """更新收敛状态"""
        self.convergence_data["agreement_score"] = agreement_score
        self.convergence_data["has_converged"] = agreement_score >= self.convergence_data["convergence_threshold"]
        self.updated_at = datetime.now()

    def has_converged(self) -> bool:
        """检查是否已收敛"""
        return self.convergence_data["has_converged"]

    def to_dict(self) -> Dict[str, Any]:
        """将会话转换为字典格式以便存储"""
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context_data": self.context_data,
            "statements": [
                {**stmt, "timestamp": stmt["timestamp"].isoformat()} 
                for stmt in self.statements
            ],
            "phase": self.phase,
            "budget": self.budget,
            "convergence_data": self.convergence_data
        }

    def save_to_file(self, filepath: str):
        """将会话数据保存到文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_from_file(cls, filepath: str) -> 'DebateSession':
        """从文件加载会话数据"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        session = cls(session_id=data["session_id"], name=data["name"])
        session.created_at = datetime.fromisoformat(data["created_at"])
        session.updated_at = datetime.fromisoformat(data["updated_at"])
        session.context_data = data["context_data"]
        session.statements = [
            {**stmt, "timestamp": datetime.fromisoformat(stmt["timestamp"])} 
            for stmt in data["statements"]
        ]
        session.phase = data["phase"]
        session.budget = data["budget"]
        session.convergence_data = data["convergence_data"]
        
        return session

    def get_session_summary(self) -> Dict[str, Any]:
        """获取会话摘要信息"""
        return {
            "session_id": self.session_id,
            "name": self.name,
            "phase": self.phase,
            "statement_count": len(self.statements),
            "current_iteration": self.budget["current_iteration"],
            "max_iterations": self.budget["max_iterations"],
            "elapsed_time": (datetime.now() - self.budget["start_time"]).total_seconds(),
            "has_converged": self.has_converged(),
            "agreement_score": self.convergence_data["agreement_score"]
        }


class SessionManager:
    """
    会话管理器，负责管理多个辩论会话
    """
    
    def __init__(self, sessions_dir: str = "./sessions"):
        self.sessions_dir = sessions_dir
        self.active_sessions: Dict[str, DebateSession] = {}
        
        # 确保会话目录存在
        os.makedirs(sessions_dir, exist_ok=True)

    def create_session(self, name: str = "", session_id: Optional[str] = None) -> DebateSession:
        """创建新会话"""
        session = DebateSession(session_id=session_id, name=name)
        self.active_sessions[session.session_id] = session
        
        return session

    def get_session(self, session_id: str) -> Optional[DebateSession]:
        """获取指定会话"""
        return self.active_sessions.get(session_id)

    def save_session(self, session: DebateSession, filename: Optional[str] = None):
        """保存会话到文件"""
        if filename is None:
            filename = f"{session.name}_{session.session_id}.json"
        
        filepath = os.path.join(self.sessions_dir, filename)
        session.save_to_file(filepath)

    def load_session(self, filepath: str) -> DebateSession:
        """从文件加载会话"""
        session = DebateSession.load_from_file(filepath)
        self.active_sessions[session.session_id] = session
        return session

    def list_sessions(self) -> List[str]:
        """列出所有活动会话ID"""
        return list(self.active_sessions.keys())

    def end_session(self, session_id: str, save: bool = True):
        """结束指定会话"""
        if session_id in self.active_sessions:
            session = self.active_sessions[session_id]
            if save:
                self.save_session(session)
            del self.active_sessions[session_id]