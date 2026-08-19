"""资料库、目录与来源文件的 PostgreSQL 事实模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


class MaterialLibrary(Base):
    """管理员维护的一组来源材料。"""

    __tablename__ = "material_libraries"

    library_id = Column(String(64), primary_key=True, comment="资料库 ID")
    name = Column(String(255), nullable=False, comment="资料库名称")
    created_by = Column(String(64), nullable=False, index=True, comment="创建人 UID")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive, comment="创建时间")
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        comment="更新时间",
    )

    def to_dict(self) -> dict[str, Any]:
        """返回资料库的可序列化事实。"""

        return {
            "library_id": self.library_id,
            "name": self.name,
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class MaterialDirectory(Base):
    """资料库中的一个可见目录。"""

    __tablename__ = "material_directories"

    directory_id = Column(String(64), primary_key=True, comment="目录 ID")
    library_id = Column(
        String(64),
        ForeignKey("material_libraries.library_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属资料库 ID",
    )
    parent_id = Column(
        String(64),
        ForeignKey("material_directories.directory_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="父目录 ID",
    )
    name = Column(String(512), nullable=False, comment="目录名称")
    relative_path = Column(String(1024), nullable=False, comment="资料库内相对路径")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive, comment="创建时间")
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        comment="更新时间",
    )

    __table_args__ = (UniqueConstraint("library_id", "relative_path", name="uq_material_directories_library_path"),)

    def to_dict(self) -> dict[str, Any]:
        """返回目录树恢复所需事实。"""

        return {
            "directory_id": self.directory_id,
            "library_id": self.library_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "relative_path": self.relative_path,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class MaterialSourceFile(Base):
    """一份原始文件及其完整正文处理事实。"""

    __tablename__ = "material_source_files"

    file_id = Column(String(64), primary_key=True, comment="来源文件 ID")
    library_id = Column(
        String(64),
        ForeignKey("material_libraries.library_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属资料库 ID",
    )
    directory_id = Column(
        String(64),
        ForeignKey("material_directories.directory_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment="所属目录 ID",
    )
    original_filename = Column(String(512), nullable=False, comment="原始文件名")
    relative_path = Column(String(1024), nullable=False, comment="资料库内来源相对路径")
    file_type = Column(String(64), nullable=False, comment="原始文件扩展名")
    file_size = Column(BigInteger, nullable=False, comment="原始文件字节数")
    source_bucket = Column(String(255), nullable=False, comment="原始文件 MinIO bucket")
    source_object = Column(String(1024), nullable=False, comment="原始文件 MinIO object")
    markdown_bucket = Column(String(255), nullable=True, comment="规范化 Markdown MinIO bucket")
    markdown_object = Column(String(1024), nullable=True, comment="规范化 Markdown MinIO object")
    status = Column(String(32), nullable=False, default="pending", index=True, comment="文件处理状态")
    progress = Column(Integer, nullable=False, default=0, comment="文件处理进度")
    line_count = Column(Integer, nullable=False, default=0, comment="规范化 Markdown 行数")
    error_message = Column(Text, nullable=True, comment="需要处理的具体原因")
    created_by = Column(String(64), nullable=False, index=True, comment="上传人 UID")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive, comment="创建时间")
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        comment="更新时间",
    )

    __table_args__ = (UniqueConstraint("library_id", "relative_path", name="uq_material_source_files_library_path"),)

    def to_dict(self) -> dict[str, Any]:
        """返回文件列表和任务投影共用的处理事实。"""

        return {
            "file_id": self.file_id,
            "library_id": self.library_id,
            "directory_id": self.directory_id,
            "original_filename": self.original_filename,
            "relative_path": self.relative_path,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "status": self.status,
            "progress": self.progress,
            "line_count": self.line_count,
            "error_message": self.error_message,
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


__all__ = ["Base", "MaterialDirectory", "MaterialLibrary", "MaterialSourceFile"]
