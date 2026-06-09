"""
小红书帖子详情页解析

从帖子详情页提取完整数据：
- 标题/文案
- 博主信息
- 互动数据（点赞、收藏、评论）
- 封面描述
- 标签
"""

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class PostDetail:
    """帖子完整数据"""
    # 基本信息
    title: str = ""
    link: str = ""
    platform: str = "小红书"

    # 内容
    description: str = ""          # 帖子文案
    blogger_tags: list[str] = field(default_factory=list)  # 博主使用的标签
    cover_desc: str = ""           # 封面图描述（AI 分析的输入之一）

    # 博主信息
    blogger_name: str = ""
    blogger_link: str = ""

    # 互动数据
    likes_count: int = 0
    comments_count: int = 0
    bookmarks_count: int = 0

    # AI 分析结果（后续填充）
    style: list[str] = field(default_factory=list)
    scene: str = ""
    shoot_type: list[str] = field(default_factory=list)
    emotion: list[str] = field(default_factory=list)
    viral_analysis: str = ""

    # 备注
    notes: str = ""


def _parse_count_js(text: str) -> int:
    """
    解析互动数据（处理 JS 中的各种格式）
    示例: "1.2万", "2038", "1.5w", ""
    """
    if not text or not text.strip():
        return 0
    text = text.strip().lower().replace(",", "").replace(" ", "")

    if "万" in text or "w" in text:
        num_str = text.replace("万", "").replace("w", "")
        try:
            return int(float(num_str) * 10000)
        except ValueError:
            return 0

    try:
        return int(float(text))
    except ValueError:
        return 0


class XHSParser:
    """小红书帖子详情页解析器"""

    def __init__(self, page: Page):
        self.page = page

    async def parse_post(self, url: str) -> PostDetail:
        """
        解析单个帖子的详情页

        Args:
            url: 帖子链接

        Returns:
            PostDetail 包含完整数据
        """
        logger.info(f"📄 解析帖子: {url[:80]}...")

        detail = PostDetail(link=url)

        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待页面内容加载
            await asyncio.sleep(2)

            # 提取各种数据
            detail.title = await self._extract_title()
            detail.description = await self._extract_description()
            detail.blogger_tags = await self._extract_tags()
            detail.likes_count = await self._extract_likes()
            detail.bookmarks_count = await self._extract_bookmarks()
            detail.comments_count = await self._extract_comments()
            detail.blogger_name = await self._extract_blogger_name()
            detail.blogger_link = await self._extract_blogger_link()
            detail.cover_desc = await self._extract_cover_description()

            logger.info(
                f"解析完成: 标题={detail.title[:30]}, "
                f"赞={detail.likes_count}, "
                f"藏={detail.bookmarks_count}, "
                f"评={detail.comments_count}"
            )

        except Exception as e:
            logger.error(f"解析帖子失败: {url}, 错误: {e}")
            # 返回已有数据，不中断整体流程

        return detail

    async def _safe_evaluate(self, js: str, default=None):
        """安全执行 JS 表达式"""
        try:
            result = await self.page.evaluate(js)
            return result
        except Exception:
            return default

    async def _extract_title(self) -> str:
        """提取帖子标题"""
        title = await self._safe_evaluate("""
            () => {
                const el = document.querySelector(
                    '#detail-title, .title, [class*="note-title"], h1, .note-scroller .title'
                );
                return el ? el.textContent.trim() : '';
            }
        """, "")
        return title

    async def _extract_description(self) -> str:
        """提取帖子文案/正文"""
        desc = await self._safe_evaluate("""
            () => {
                // 尝试多种选择器
                const selectors = [
                    '#detail-desc',
                    '.note-scroller .desc',
                    '[class*="note-text"]',
                    '.note-content .desc',
                    '.content .desc',
                    '.desc',
                    '[class*="description"]'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent.trim()) {
                        return el.textContent.trim();
                    }
                }
                return '';
            }
        """, "")
        return desc

    async def _extract_tags(self) -> list[str]:
        """提取帖子标签 (hashtags 和话题标签)"""
        tags = await self._safe_evaluate("""
            () => {
                const tags = [];
                // 话题标签
                document.querySelectorAll(
                    '[class*="tag"], [class*="hash"], a[href*="/tag/"], a[href*="/topic/"]'
                ).forEach(el => {
                    const text = el.textContent.replace(/^#/, '').trim();
                    if (text && !tags.includes(text)) {
                        tags.push(text);
                    }
                });
                return tags;
            }
        """, [])
        return tags or []

    async def _extract_likes(self) -> int:
        """提取点赞数"""
        text = await self._safe_evaluate("""
            () => {
                const el = document.querySelector(
                    '[class*="like"] [class*="count"], '
                    + '[class*="like-wrapper"] [class*="count"], '
                    + '.like-btn .count, '
                    + '.interact-item.like .count'
                );
                return el ? el.textContent.trim() : '0';
            }
        """, "0")
        return _parse_count_js(text)

    async def _extract_bookmarks(self) -> int:
        """提取收藏数"""
        text = await self._safe_evaluate("""
            () => {
                const el = document.querySelector(
                    '[class*="collect"] [class*="count"], '
                    + '[class*="collect-wrapper"] [class*="count"], '
                    + '.collect-btn .count, '
                    + '.interact-item.collect .count'
                );
                return el ? el.textContent.trim() : '0';
            }
        """, "0")
        return _parse_count_js(text)

    async def _extract_comments(self) -> int:
        """提取评论数"""
        text = await self._safe_evaluate("""
            () => {
                const el = document.querySelector(
                    '[class*="comment"] [class*="count"], '
                    + '[class*="comment-wrapper"] [class*="count"], '
                    + '.comment-btn .count, '
                    + '.interact-item.comment .count'
                );
                return el ? el.textContent.trim() : '0';
            }
        """, "0")
        return _parse_count_js(text)

    async def _extract_blogger_name(self) -> str:
        """提取博主昵称"""
        name = await self._safe_evaluate("""
            () => {
                const el = document.querySelector(
                    '.username, '
                    + '[class*="author"] .name, '
                    + '.nickname, '
                    + '[class*="user-name"], '
                    + '.author-info .name'
                );
                return el ? el.textContent.trim() : '';
            }
        """, "")
        return name

    async def _extract_blogger_link(self) -> str:
        """提取博主主页链接"""
        link = await self._safe_evaluate("""
            () => {
                const el = document.querySelector(
                    'a[href*="/user/profile/"]'
                );
                if (el) {
                    const href = el.href;
                    // 去掉 xsec_token 参数，保留基础链接
                    const url = new URL(href);
                    return url.origin + url.pathname;
                }
                return '';
            }
        """, "")
        return link

    async def _extract_cover_description(self) -> str:
        """
        提取封面图描述

        这可以通过以下方式：
        1. 获取封面图的 alt 文本
        2. 尝试获取 AI 生成的字幕
        3. 如果都没有，用标题 + 博主标签拼接一个基础描述
        """
        desc = await self._safe_evaluate("""
            () => {
                // 尝试获取封面图
                const coverImg = document.querySelector(
                    '.cover img, [class*="cover"] img, .note-image img, .main-image img'
                );
                if (coverImg && coverImg.alt) {
                    return coverImg.alt;
                }
                return '';
            }
        """, "")
        return desc


async def parse_post(page: Page, url: str) -> PostDetail:
    """便捷函数：解析帖子详情"""
    parser = XHSParser(page)
    return await parser.parse_post(url)


async def parse_posts(page: Page, urls: list[str], interval: float = 2.0) -> list[PostDetail]:
    """
    批量解析帖子详情

    Args:
        page: Playwright Page 对象
        urls: 帖子链接列表
        interval: 基础请求间隔（秒），实际会在 ±50% 范围内随机抖动

    Returns:
        PostDetail 列表
    """
    details = []
    for i, url in enumerate(urls):
        logger.info(f"[{i+1}/{len(urls)}] 解析帖子...")
        detail = await parse_post(page, url)
        details.append(detail)
        if i < len(urls) - 1:
            # 随机化间隔：基础值 ±50%，最低 1.0s
            jittered = max(1.0, interval * random.uniform(0.5, 1.5))
            await asyncio.sleep(round(jittered, 2))
    return details
