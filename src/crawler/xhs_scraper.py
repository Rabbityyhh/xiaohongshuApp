"""
小红书话题搜索 & 帖子列表获取

流程：
1. 访问搜索页 URL
2. 等待搜索结果加载
3. 滚动加载更多帖子
4. 提取帖子卡片信息
5. 按点赞/评论数排序取 Top N
"""

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class PostCard:
    """搜索结果中的帖子卡片信息（初步数据）"""
    title: str = ""
    link: str = ""
    cover_url: str = ""
    likes_text: str = ""    # 原始文本（如 "1.2万"）
    likes_count: int = 0    # 解析后的数字
    comments_count: int = 0
    bookmarks_count: int = 0
    blogger_name: str = ""
    blogger_link: str = ""


def _parse_count(text: str) -> int:
    """
    解析互动数据文本为数字

    示例:
        "1.2万" → 12000
        "2038" → 2038
        "1.5万" → 15000
        "" → 0
    """
    if not text or not text.strip():
        return 0

    text = text.strip().replace(",", "")

    # 处理 "万" 单位
    if "万" in text:
        num_str = text.replace("万", "")
        try:
            return int(float(num_str) * 10000)
        except ValueError:
            return 0

    # 直接数字
    try:
        return int(text)
    except ValueError:
        return 0


class XHSScraper:
    """小红书搜索爬虫"""

    BASE_URL = "https://www.xiaohongshu.com"

    def __init__(self, page: Page):
        self.page = page

    def _build_search_url(self, keyword: str, sort: str = "popularity") -> str:
        """构建搜索 URL"""
        # URL encode 中文关键词
        from urllib.parse import quote
        encoded_keyword = quote(keyword)
        return f"{self.BASE_URL}/search_result?keyword={encoded_keyword}&sort={sort}&source=web_search_result_notes"

    async def search_topic(
        self,
        keyword: str,
        max_fetch: int = 50,
        top_n: int = 10,
        sort: str = "popularity",
    ) -> list[PostCard]:
        """
        搜索话题并获取热门帖子

        Args:
            keyword: 搜索关键词
            max_fetch: 滚动加载的上限条数
            top_n: 最终返回的 Top N 条
            sort: 排序方式 (popularity=最热, general=综合, time=最新)

        Returns:
            按点赞数和评论数排名的帖子列表（去重后最多 top_n*2 条）
        """
        search_url = self._build_search_url(keyword, sort)
        logger.info(f"🔍 访问搜索页: {search_url}")

        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # 等待搜索结果加载
        await self._wait_for_results()

        # 滚动加载更多帖子
        cards = await self._scroll_and_collect(max_fetch)

        logger.info(f"共获取 {len(cards)} 条帖子卡片")

        # 按点赞数取 Top N
        by_likes = sorted(cards, key=lambda c: c.likes_count, reverse=True)[:top_n]

        # 按评论数取 Top N
        by_comments = sorted(cards, key=lambda c: c.comments_count, reverse=True)[:top_n]

        # 合并去重
        seen_links = set()
        result = []
        for card in by_likes + by_comments:
            if card.link and card.link not in seen_links:
                seen_links.add(card.link)
                result.append(card)

        logger.info(
            f"点赞 Top {top_n}: {[(c.title[:20], c.likes_count) for c in by_likes]}"
        )
        logger.info(
            f"评论 Top {top_n}: {[(c.title[:20], c.comments_count) for c in by_comments]}"
        )
        logger.info(f"去重后共 {len(result)} 条帖子")

        return result

    async def _wait_for_results(self, timeout: int = 10):
        """等待搜索结果加载"""
        for i in range(timeout):
            await asyncio.sleep(1)

            # 检查是否有搜索结果卡片
            has_results = await self.page.evaluate("""
                () => {
                    const cards = document.querySelectorAll(
                        '[class*="note-item"], section.note-item, .feeds-page .note-item'
                    );
                    return cards.length > 0;
                }
            """)
            if has_results:
                logger.info("搜索结果已加载")
                return

        # 超时后保存调试信息
        logger.warning("等待搜索结果超时！保存调试截图和页面 HTML...")
        debug_dir = Path(__file__).parent.parent.parent / "data" / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(debug_dir / "search_page_timeout.png"), full_page=False)
        html_content = await self.page.content()
        (debug_dir / "search_page_timeout.html").write_text(html_content, encoding="utf-8")
        # 同时输出页面标题和 URL 辅助排查
        page_title = await self.page.title()
        current_url = self.page.url
        logger.warning(f"当前页面标题: {page_title}")
        logger.warning(f"当前页面 URL: {current_url}")
        logger.warning(f"调试文件已保存到: {debug_dir}")

    async def _scroll_and_collect(self, max_fetch: int) -> list[PostCard]:
        """
        滚动页面并收集帖子卡片

        使用随机化的滚动距离、等待间隔和偶尔回滚来模拟人类行为，
        降低被小红书反爬虫系统检测的风险。
        """
        cards = []
        seen_links = set()
        scroll_attempts = 0
        max_scrolls = 30  # 最多滚动次数

        while len(cards) < max_fetch and scroll_attempts < max_scrolls:
            # 提取当前可见的卡片
            new_cards = await self._extract_cards()

            for card in new_cards:
                if card.link and card.link not in seen_links:
                    seen_links.add(card.link)
                    cards.append(card)

            logger.info(
                f"已滚动 {scroll_attempts + 1} 次，收集 {len(cards)} 条"
            )

            # 人类化滚动（随机距离 + 随机等待 + 偶尔回滚）
            await self._human_like_scroll()

            scroll_attempts += 1

            # 如果没有新卡片出现，可能到底了
            if len(new_cards) == 0 and scroll_attempts > 3:
                logger.info("没有更多新内容，停止滚动")
                break

        return cards

    async def _human_like_scroll(self):
        """
        模拟人类滚动行为，降低自动化特征

        策略：
        - 每次滚动距离随机（300~1200px）
        - 每次等待间隔随机（1.5~4.0s）
        - 约 20% 概率向上回滚 100~400px（模拟回看）
        - 约 15% 概率鼠标悬停在某张卡片上（模拟浏览）
        """
        # 1. 主滚动：随机距离
        scroll_distance = random.randint(300, 1200)
        await self.page.evaluate(f"window.scrollBy(0, {scroll_distance})")

        # 2. 主等待：随机间隔（比原来 2s 更分散）
        await asyncio.sleep(round(random.uniform(1.5, 4.0), 2))

        # 3. 偶尔回滚一小段（模拟用户回看刚才的内容）
        if random.random() < 0.2:
            back_distance = random.randint(100, 400)
            await self.page.evaluate(f"window.scrollBy(0, -{back_distance})")
            await asyncio.sleep(round(random.uniform(0.5, 1.5), 2))

        # 4. 偶尔悬停在某张卡片上（模拟用户浏览感兴趣的内容）
        if random.random() < 0.15:
            try:
                await self.page.evaluate("""
                    () => {
                        const cards = document.querySelectorAll(
                            'section.note-item, [class*="note-item"]'
                        );
                        if (cards.length > 0) {
                            const idx = Math.floor(Math.random() * cards.length);
                            const card = cards[idx];
                            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            // 触发 mouseenter 事件
                            card.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                        }
                    }
                """)
                await asyncio.sleep(round(random.uniform(0.8, 2.0), 2))
            except Exception:
                pass  # 悬停失败不影响主流程

    async def _extract_cards(self) -> list[PostCard]:
        """从当前页面提取帖子卡片列表"""
        cards = await self.page.evaluate("""
            () => {
                const cards = [];
                // 小红书搜索结果的卡片选择器
                const items = document.querySelectorAll(
                    'section.note-item, [class*="note-item"], .feeds-page .note-item, .search-result-item'
                );

                items.forEach(item => {
                    try {
                        // 提取链接
                        const linkEl = item.querySelector('a[href*="/explore/"], a[href*="/discovery/item/"]');
                        const link = linkEl ? linkEl.href : '';

                        // 提取标题
                        const titleEl = item.querySelector('.title, [class*="title"], .note-title, a.title');
                        const title = titleEl ? titleEl.textContent.trim() : '';

                        // 提取封面图
                        const imgEl = item.querySelector('img');
                        const coverUrl = imgEl ? (imgEl.src || imgEl.dataset.src || '') : '';

                        // 提取点赞数
                        const likeEl = item.querySelector(
                            '.like-count, [class*="like-wrapper"] .count, .like span, [class*="like"] [class*="count"]'
                        );
                        const likesText = likeEl ? likeEl.textContent.trim() : '0';

                        // 提取博主名
                        const authorEl = item.querySelector(
                            '.author .name, .nickname, [class*="author"] [class*="name"], .user-name'
                        );
                        const bloggerName = authorEl ? authorEl.textContent.trim() : '';

                        // 提取博主链接
                        const authorLinkEl = item.querySelector('a[href*="/user/profile/"]');
                        const bloggerLink = authorLinkEl ? authorLinkEl.href : '';

                        cards.push({
                            title: title,
                            link: link,
                            coverUrl: coverUrl,
                            likesText: likesText,
                            bloggerName: bloggerName,
                            bloggerLink: bloggerLink
                        });
                    } catch (e) {
                        // 跳过解析失败的卡片
                    }
                });

                return cards;
            }
        """)

        # 转换为 PostCard 对象并解析数据
        result = []
        for c in cards:
            card = PostCard(
                title=c.get("title", ""),
                link=c.get("link", ""),
                cover_url=c.get("coverUrl", ""),
                likes_text=c.get("likesText", "0"),
                likes_count=_parse_count(c.get("likesText", "0")),
                blogger_name=c.get("bloggerName", ""),
                blogger_link=c.get("bloggerLink", ""),
            )
            result.append(card)

        return result


async def scrape_topic(
    page: Page,
    keyword: str,
    max_fetch: int = 50,
    top_n: int = 10,
) -> list[PostCard]:
    """便捷函数：搜索话题并返回帖子列表"""
    scraper = XHSScraper(page)
    return await scraper.search_topic(
        keyword=keyword,
        max_fetch=max_fetch,
        top_n=top_n,
    )
