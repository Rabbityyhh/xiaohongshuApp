"""
数据 → Notion 字段映射器

将爬虫数据和 AI 分析结果映射为 Notion API 可接受的属性格式。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 从 config.yaml 加载的字段名映射
FIELD_NAMES = {
    "platform": "平台",
    "link": "链接",
    "title": "文案",
    "blogger_tags": "博主自定义标签",
    "style": "穿搭风格",
    "scene": "场景",
    "shoot_type": "拍摄类型",
    "emotion": "情绪关键词",
    "cover_desc": "封面",
    "bookmarks": "收藏数",
    "likes": "点赞数",
    "comments": "评论数",
    "viral_analysis": "爆点分析（主观）",
    "blogger": "博主",
    "notes": "备注",
}


class FieldMapper:
    """
    将内部数据结构映射为 Notion API properties 格式
    """

    @staticmethod
    def _make_title(text: str) -> dict:
        """创建 Title 类型字段"""
        return {
            "title": [
                {
                    "type": "text",
                    "text": {"content": text or ""}
                }
            ]
        }

    @staticmethod
    def _make_rich_text(text: str) -> dict:
        """创建 Rich Text 类型字段"""
        return {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": text or ""}
                }
            ]
        }

    @staticmethod
    def _make_url(url: str) -> dict:
        """创建 URL 类型字段"""
        return {"url": url or ""}

    @staticmethod
    def _make_number(value) -> dict:
        """创建 Number 类型字段"""
        try:
            num = int(value) if value else 0
        except (ValueError, TypeError):
            num = 0
        return {"number": num}

    @staticmethod
    def _make_select(name: str) -> dict:
        """创建 Select 类型字段"""
        if not name:
            return {"select": None}
        return {"select": {"name": name}}

    @staticmethod
    def _make_multi_select(names: list[str]) -> dict:
        """创建 Multi-select 类型字段"""
        if not names:
            return {"multi_select": []}
        return {
            "multi_select": [
                {"name": name} for name in names if name
            ]
        }

    def map_post_to_notion_properties(
        self,
        platform: str = "小红书",
        link: str = "",
        title: str = "",
        blogger_tags: Optional[list[str]] = None,
        style: Optional[list[str]] = None,
        scene: str = "",
        shoot_type: Optional[list[str]] = None,
        emotion: Optional[list[str]] = None,
        cover_desc: str = "",
        bookmarks: int = 0,
        likes: int = 0,
        comments: int = 0,
        viral_analysis: str = "",
        blogger: str = "",
        notes: str = "",
    ) -> dict:
        """
        将所有数据映射为 Notion API 的 properties 格式

        Returns:
            Notion API create_page 所需的 properties 字典
        """
        properties = {}

        # 平台 (Select)
        if platform:
            properties[FIELD_NAMES["platform"]] = self._make_select(platform)

        # 链接 (URL)
        if link:
            properties[FIELD_NAMES["link"]] = self._make_url(link)

        # 文案 (Rich Text — post body text)
        if title:
            properties[FIELD_NAMES["title"]] = self._make_rich_text(title)

        # 博主自定义标签 (Multi-select)
        if blogger_tags:
            properties[FIELD_NAMES["blogger_tags"]] = self._make_multi_select(blogger_tags)

        # 穿搭风格 (Multi-select)
        if style:
            properties[FIELD_NAMES["style"]] = self._make_multi_select(style)

        # 场景 (Multi-select — actual DB type is multi_select, not select)
        if scene:
            properties[FIELD_NAMES["scene"]] = self._make_multi_select([scene])

        # 拍摄类型 (Multi-select)
        if shoot_type:
            properties[FIELD_NAMES["shoot_type"]] = self._make_multi_select(shoot_type)

        # 情绪关键词 (Multi-select)
        if emotion:
            properties[FIELD_NAMES["emotion"]] = self._make_multi_select(emotion)

        # 封面 (Rich Text)
        if cover_desc:
            properties[FIELD_NAMES["cover_desc"]] = self._make_rich_text(cover_desc)

        # 收藏数 (Number)
        properties[FIELD_NAMES["bookmarks"]] = self._make_number(bookmarks)

        # 点赞数 (Number)
        properties[FIELD_NAMES["likes"]] = self._make_number(likes)

        # 评论数 (Number)
        properties[FIELD_NAMES["comments"]] = self._make_number(comments)

        # 爆点分析（主观）(Rich Text)
        if viral_analysis:
            properties[FIELD_NAMES["viral_analysis"]] = self._make_rich_text(viral_analysis)

        # 博主 (Rich Text — not URL as originally assumed)
        if blogger:
            properties[FIELD_NAMES["blogger"]] = self._make_rich_text(blogger)

        # 备注 (Title — the database's actual title column)
        if notes:
            properties[FIELD_NAMES["notes"]] = self._make_title(notes)

        return properties
