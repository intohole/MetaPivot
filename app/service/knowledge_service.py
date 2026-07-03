"""KnowledgeService - 知识库管理

职责：
1. 文档上传（保存 → 分块 → 向量化 → 入库 Milvus）
2. 文档列表与删除
3. 语义检索（向量召回 → 重排 → 返回结果）
4. 当前阶段：Milvus 未接入时降级为关键词检索
"""
import os
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from app.infra.db.models_core import KnowledgeDocumentORM
from app.infra.db.session import get_db_session
from app.utils.config import settings
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("knowledge_service")

# 文件上传目录
_UPLOAD_DIR = os.path.join(os.getcwd(), "data", "knowledge")
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
_ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".doc", ".html"}


class KnowledgeService:
    """知识库服务单例"""

    async def upload_document(
        self,
        file,
        metadata: str,
        user_id: str,
    ) -> dict:
        """上传文档

        流程：
        1. 校验文件类型与大小
        2. 保存到本地（生产环境可换对象存储）
        3. 创建文档记录（status=processing）
        4. 异步触发分块+向量化（_process_document）
        """
        filename = file.filename or "unknown.txt"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"不支持的文件类型: {ext}",
                400,
            )

        # 读取并校验大小
        content = await file.read()
        if len(content) > _MAX_FILE_SIZE:
            raise AppError(ErrorCode.VALIDATION_ERROR, "文件大小超过 50MB 限制", 400)

        # 保存文件
        os.makedirs(_UPLOAD_DIR, exist_ok=True)
        file_id = str(uuid.uuid4())
        saved_filename = f"{file_id}{ext}"
        file_path = os.path.join(_UPLOAD_DIR, saved_filename)
        with open(file_path, "wb") as f:
            f.write(content)

        # 解析 metadata
        import json
        try:
            meta_dict = json.loads(metadata) if metadata else {}
        except json.JSONDecodeError:
            meta_dict = {}

        # 创建文档记录
        async with get_db_session() as session:
            doc = KnowledgeDocumentORM(
                filename=filename,
                file_path=file_path,
                file_size=len(content),
                mime_type=file.content_type or "",
                chunk_count=0,
                status="processing",
                metadata_=meta_dict,
                created_by=user_id,
            )
            session.add(doc)
            await session.flush()
            doc_id = doc.id

        # 异步处理分块与向量化
        import asyncio
        asyncio.create_task(self._process_document(doc_id, file_path, ext))

        return {
            "id": doc_id,
            "filename": filename,
            "status": "processing",
            "chunk_count": 0,
        }

    async def _process_document(self, doc_id: str, file_path: str, ext: str) -> None:
        """异步处理文档：分块 + 向量化 + 入库 Milvus"""
        try:
            chunks = await self._split_document(file_path, ext)
            # TODO: P5 接入 Milvus 向量化
            # embeddings = await self._embed_chunks(chunks)
            # await self._save_to_milvus(doc_id, chunks, embeddings)
            async with get_db_session() as session:
                doc = await session.get(KnowledgeDocumentORM, doc_id)
                if doc:
                    doc.chunk_count = len(chunks)
                    doc.status = "indexed"
            log.info("Document {} processed: {} chunks", doc_id, len(chunks))
        except Exception as e:
            log.exception("Process document {} failed: {}", doc_id, e)
            async with get_db_session() as session:
                doc = await session.get(KnowledgeDocumentORM, doc_id)
                if doc:
                    doc.status = "failed"

    async def _split_document(self, file_path: str, ext: str) -> list[str]:
        """文档分块（简化实现：纯文本按段落切分）"""
        if ext == ".txt" or ext == ".md":
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            # 按双换行分块，每块最多 500 字
            paragraphs = text.split("\n\n")
            chunks = []
            current = ""
            for p in paragraphs:
                if len(current) + len(p) < 500:
                    current = (current + "\n\n" + p).strip() if current else p
                else:
                    if current:
                        chunks.append(current)
                    current = p
            if current:
                chunks.append(current)
            return chunks
        # 其他格式暂不支持自动分块
        return [f"[{ext} file - chunking not supported in MVP]"]

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str = "",
    ) -> tuple[list[dict], int]:
        async with get_db_session() as session:
            stmt = select(KnowledgeDocumentORM)
            if status:
                stmt = stmt.where(KnowledgeDocumentORM.status == status)
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(KnowledgeDocumentORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            items = (await session.execute(stmt)).scalars().all()
            return [self._to_dict(d) for d in items], total

    async def delete_document(self, document_id: str) -> dict:
        async with get_db_session() as session:
            doc = await session.get(KnowledgeDocumentORM, document_id)
            if doc is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档不存在", 404)
            # 删除本地文件
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                except OSError as e:
                    log.warning("Delete file failed: {}", e)
            # TODO: 删除 Milvus 中对应向量
            await session.delete(doc)
            return {"id": document_id, "deleted": True}

    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.7,
        filter_: Optional[dict] = None,
    ) -> dict:
        """语义检索

        当前阶段：调用 RAG 模块的兜底实现（关键词匹配）
        P5 完整版：Milvus 向量召回 + 重排
        """
        from app.infra.rag.search import search as rag_search
        result = await rag_search({"query": query, "top_k": top_k})
        return {
            "results": result.get("results", []),
            "total": result.get("total", 0),
            "engine": result.get("engine", "keyword_fallback"),
        }

    def _to_dict(self, d: KnowledgeDocumentORM) -> dict:
        return {
            "id": d.id,
            "filename": d.filename,
            "file_size": d.file_size,
            "mime_type": d.mime_type,
            "chunk_count": d.chunk_count,
            "status": d.status,
            "metadata": d.metadata_,
            "created_by": d.created_by,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }


knowledge_service = KnowledgeService()
