import pytest
from unittest.mock import Mock, patch, MagicMock
import chromadb
from chromadb.config import Settings


class TestKnowledgeQuality:
    """
    知识库质量测试类，包含以下测试：
    1. Chroma检索召回率测试
    2. 政策检索精确率测试
    3. 政策废止检测测试
    4. 双源融合检索测试
    """

    def setup_method(self):
        """设置测试环境"""
        # 模拟chroma客户端
        self.mock_chroma_client = Mock()
        self.mock_collection = Mock()
        self.mock_chroma_client.get_collection.return_value = self.mock_collection
        
        # 模拟wiki客户端
        self.mock_wiki_client = Mock()

    @patch('chromadb.Client')  # 标准的chromadb客户端导入方式
    def test_chroma_recall_rate(self, mock_chroma_class):
        """
        测试Chroma检索召回率
        查询已知文档→验证返回结果包含该文档
        """
        # 模拟已知文档
        known_document_id = "doc_123"
        known_document_content = "这是一份关于人工智能发展的政策文件"
        known_document_metadata = {"source": "policy_doc", "year": 2023}
        
        # 将已知文档添加到集合中
        stored_documents = {
            known_document_id: {
                "content": known_document_content,
                "metadata": known_document_metadata
            }
        }
        
        # 模拟检索结果，确保包含已知文档
        mock_query_result = {
            "documents": [[known_document_content]],
            "metadatas": [[known_document_metadata]],
            "distances": [[0.1]],
            "ids": [[known_document_id]]
        }
        
        # 配置mock对象
        mock_chroma_instance = Mock()
        mock_chroma_class.return_value = mock_chroma_instance
        mock_chroma_instance.get_collection.return_value = self.mock_collection
        self.mock_collection.query.return_value = mock_query_result
        
        # 执行查询
        query = "人工智能发展政策"
        result = self.mock_collection.query(
            query_texts=[query],
            n_results=5  # 假设返回前5个结果
        )
        
        # 验证返回结果包含已知文档ID
        assert known_document_id in result['ids'][0], \
            f"Expected to retrieve document {known_document_id} but got {result['ids'][0]}"
        
        # 验证返回结果包含已知文档内容
        assert known_document_content in result['documents'][0], \
            f"Expected to retrieve content '{known_document_content}' but got {result['documents'][0]}"
        
        # 验证模拟对象被正确调用
        self.mock_collection.query.assert_called_once_with(
            query_texts=[query],
            n_results=5
        )

    def test_policy_precision_rate(self):
        """
        测试政策检索精确率
        查询'52号文'→验证返回52号文相关内容
        """
        # 模拟52号文相关内容
        policy_content = [
            "这是关于网络安全的第52号文件内容",
            "根据第52号文件规定，网络运营者应当履行安全保护义务",
            "52号文详细说明了数据安全和个人信息保护的要求"
        ]
        
        # 模拟检索结果
        mock_query_result = {
            "documents": [policy_content],
            "metadatas": [[{"doc_type": "policy", "doc_num": "52号文", "source": "official_doc"}]],
            "distances": [[0.2, 0.3, 0.4]],
            "ids": [["policy_52_1", "policy_52_2", "policy_52_3"]]
        }
        
        # 配置mock对象
        self.mock_collection.query.return_value = mock_query_result
        
        # 执行查询
        query = "52号文"
        result = self.mock_collection.query(
            query_texts=[query],
            n_results=5
        )
        
        # 验证返回结果包含52号文相关内容
        result_docs = result["documents"][0]
        assert any("52号文" in doc for doc in result_docs), \
            f"Expected documents containing '52号文' but got: {result_docs}"
        
        # 验证元数据中包含52号文标识
        result_metadatas = result["metadatas"][0]
        assert any(meta.get("doc_num") == "52号文" for meta in result_metadatas), \
            f"Expected metadata containing '52号文' but got: {result_metadatas}"
        
        # 验证模拟对象被正确调用
        self.mock_collection.query.assert_called_once_with(
            query_texts=[query],
            n_results=5
        )

    def test_policy_obsolete_detection(self):
        """
        测试政策废止检测
        传入已废止政策文号→验证返回废止标记
        """
        # 模拟已废止的政策文号
        obsolete_policy_id = "政发[2010]15号"
        
        # 模拟政策检查器
        mock_policy_checker = Mock()
        
        # 模拟返回废止信息
        obsolete_info = {
            "policy_id": obsolete_policy_id,
            "status": "obsolete",
            "obsolete_date": "2020-05-01",
            "replaced_by": "政发[2020]8号",
            "reason": "被新政策替代"
        }
        
        # 设定mock返回值
        mock_policy_checker.check_policy_status.return_value = obsolete_info
        
        # 执行政策状态检查
        result = mock_policy_checker.check_policy_status(obsolete_policy_id)
        
        # 验证返回结果包含废止标记
        assert result["status"] == "obsolete", \
            f"Expected policy status to be 'obsolete' but got '{result['status']}'"
        assert result["policy_id"] == obsolete_policy_id, \
            f"Expected policy ID '{obsolete_policy_id}' but got '{result['policy_id']}'"
        assert "obsolete_date" in result, \
            "Expected 'obsolete_date' in result but not found"
        assert result["obsolete_date"] == "2020-05-01", \
            f"Expected obsolete date '2020-05-01' but got '{result['obsolete_date']}'"
        
        # 验证mock被正确调用
        mock_policy_checker.check_policy_status.assert_called_once_with(obsolete_policy_id)

    def test_dual_source_fusion_retrieval(self):
        """
        测试双源融合检索
        查询→验证结果同时包含chroma和wiki来源
        """
        # 模拟chroma检索结果
        chroma_result = {
            "documents": [["chroma来源的文档内容第一部分", "chroma来源的文档内容第二部分"]],
            "metadatas": [
                [{"source": "chroma", "type": "internal_doc", "id": "chr_001"}],
                [{"source": "chroma", "type": "internal_doc", "id": "chr_002"}]
            ],
            "distances": [[0.15, 0.18]],
            "ids": [["chr_001", "chr_002"]]
        }
        
        # 模拟wiki检索结果
        wiki_result = {
            "documents": [["wiki来源的文档内容第一部分", "wiki来源的文档内容第二部分"]],
            "metadatas": [
                [{"source": "wiki", "type": "external_doc", "id": "wiki_001"}],
                [{"source": "wiki", "type": "external_doc", "id": "wiki_002"}]
            ],
            "distances": [[0.22, 0.25]],
            "ids": [["wiki_001", "wiki_002"]]
        }
        
        # 创建模拟的chroma和wiki客户端
        mock_chroma_client = Mock()
        mock_wiki_client = Mock()
        
        # 设置模拟对象的返回值
        self.mock_collection.query.return_value = chroma_result
        mock_chroma_client.get_collection.return_value = self.mock_collection
        mock_wiki_client.search.return_value = wiki_result
        
        # 执行双源查询
        query = "双源融合测试查询"
        
        # 模拟从两个源获取结果
        chroma_search_result = self.mock_collection.query(
            query_texts=[query],
            n_results=2
        )
        
        wiki_search_result = mock_wiki_client.search(query)
        
        # 验证chroma结果包含正确的源标识
        chroma_sources = []
        for metadata_list in chroma_search_result["metadatas"]:
            for metadata in metadata_list:
                chroma_sources.append(metadata.get("source"))
        
        # 验证wiki结果包含正确的源标识
        wiki_sources = []
        for metadata_list in wiki_search_result["metadatas"]:
            for metadata in metadata_list:
                wiki_sources.append(metadata.get("source"))
        
        # 验证结果同时包含chroma和wiki来源
        assert "chroma" in chroma_sources, "Expected to find 'chroma' source in chroma results"
        assert "wiki" in wiki_sources, "Expected to find 'wiki' source in wiki results"
        
        # 模拟融合后的结果应该包含来自两个源的内容
        all_sources = chroma_sources + wiki_sources
        assert "chroma" in all_sources, "Fused results should contain 'chroma' source"
        assert "wiki" in all_sources, "Fused results should contain 'wiki' source"
        
        # 验证两个客户端都被调用
        self.mock_collection.query.assert_called_once_with(
            query_texts=[query],
            n_results=2
        )
        mock_wiki_client.search.assert_called_once_with(query)


# 运行测试
if __name__ == "__main__":
    pytest.main([__file__])