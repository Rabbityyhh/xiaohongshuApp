"""
Notion API 封装

提供数据库查询、页面创建、去重检查等功能。
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass, field

from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)


@dataclass
class NotionConfig:
    """Notion API 配置"""
    api_token: str
    database_id: str


class NotionClient:
    """Notion API client wrapper"""

    def __init__(self, config: NotionConfig):
        self.config = config
        self.client = Client(auth=config.api_token)
        self._database_schema: Optional[dict] = None
        self._data_source_id: Optional[str] = None
        self._properties_schema: Optional[dict] = None

    def _get_data_source_id(self) -> str:
        """Get the data source ID from the database"""
        if self._data_source_id is None:
            response = self.client.databases.retrieve(
                database_id=self.config.database_id
            )
            data_sources = response.get("data_sources", [])
            if data_sources:
                self._data_source_id = data_sources[0]["id"]
            else:
                raise RuntimeError("No data source found in database")
        return self._data_source_id

    def _get_properties_schema(self) -> dict:
        """Get properties schema from the data source (cached)"""
        if self._properties_schema is None:
            ds_id = self._get_data_source_id()
            response = self.client.data_sources.retrieve(
                data_source_id=ds_id
            )
            self._properties_schema = response.get("properties", {})
            logger.info("Successfully retrieved Notion database properties")
        return self._properties_schema

    def get_database_schema(self) -> dict:
        """Get the full data source schema (cached)"""
        if self._database_schema is None:
            ds_id = self._get_data_source_id()
            self._database_schema = self.client.data_sources.retrieve(
                data_source_id=ds_id
            )
            logger.info("Successfully retrieved Notion database schema")
        return self._database_schema

    def list_field_names(self) -> list[str]:
        """List all field names in the database"""
        props = self._get_properties_schema()
        return [
            prop.get("name", prop_id)
            for prop_id, prop in props.items()
        ]

    def get_field_type(self, field_name: str) -> Optional[str]:
        """Get the type of a specific field"""
        props = self._get_properties_schema()
        for prop_id, prop in props.items():
            if prop.get("name") == field_name:
                return prop.get("type")
        return None

    def get_select_options(self, field_name: str) -> list[dict]:
        """
        Get existing options for a Select or Multi-select field
        Returns: [{"name": "韩系", "color": "gray"}, ...]
        """
        props = self._get_properties_schema()
        for prop_id, prop in props.items():
            if prop.get("name") == field_name:
                field_type = prop.get("type")
                if field_type == "select":
                    return prop.get("select", {}).get("options", [])
                elif field_type == "multi_select":
                    return prop.get("multi_select", {}).get("options", [])
        return []

    def query_pages_by_link(self, link: str) -> list[dict]:
        """
        Query existing pages by link field (for dedup)

        Args:
            link: Post link to search for

        Returns:
            Matching pages (usually empty or 1)
        """
        try:
            ds_id = self._get_data_source_id()
            response = self.client.data_sources.query(
                data_source_id=ds_id,
                filter={
                    "property": "链接",
                    "url": {
                        "equals": link
                    }
                }
            )
            return response.get("results", [])
        except APIResponseError as e:
            logger.error(f"Query pages failed: {e}")
            return []

    def create_page(self, properties: dict) -> dict:
        """
        在数据库中创建新页面

        Args:
            properties: Notion 页面属性字典，格式匹配 Notion API

        Returns:
            创建的页面对象
        """
        try:
            page = self.client.pages.create(
                parent={"database_id": self.config.database_id},
                properties=properties
            )
            logger.info(f"成功创建 Notion 页面: {page.get('id')}")
            return page
        except APIResponseError as e:
            logger.error(f"创建页面失败: {e}")
            raise

    def update_page(self, page_id: str, properties: dict) -> dict:
        """更新已有页面"""
        try:
            page = self.client.pages.update(
                page_id=page_id,
                properties=properties
            )
            logger.info(f"成功更新 Notion 页面: {page_id}")
            return page
        except APIResponseError as e:
            logger.error(f"更新页面失败: {e}")
            raise

    def upsert_page(self, link: str, properties: dict) -> dict:
        """
        如果链接已存在则更新，否则创建新页面

        Args:
            link: 帖子链接（用于去重）
            properties: 页面属性

        Returns:
            创建或更新的页面对象
        """
        existing = self.query_pages_by_link(link)
        if existing:
            page_id = existing[0]["id"]
            logger.info(f"链接已存在，更新页面: {page_id}")
            return self.update_page(page_id, properties)
        else:
            logger.info("链接不存在，创建新页面")
            return self.create_page(properties)
