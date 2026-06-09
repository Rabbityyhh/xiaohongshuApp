"""
主入口：编排小红书爬虫 → AI 分析 → Notion 写入的完整流程

用法:
    python -m src.main --keyword "韩系穿搭"
    python -m src.main --keyword "通勤穿搭" --top 10 --headless

前置条件:
    1. 配置 .env 文件（NOTION_API_TOKEN, NOTION_DATABASE_ID, DEEPSEEK_API_KEY）
    2. 首次运行需要在弹出的浏览器中扫码登录小红书
"""

import argparse
import asyncio
import io
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 修复 Windows 中文终端 Unicode 编码问题
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

console = Console()

# 导入各模块
from src.crawler.xhs_browser import XHSBrowser
from src.crawler.xhs_scraper import XHSScraper
from src.crawler.xhs_parser import XHSParser
from src.crawler.xhs_parser import PostDetail
from src.notion.client import NotionClient, NotionConfig
from src.notion.field_mapper import FieldMapper
from src.analyzer.llm_analyzer import LLMAnalyzer  # DeepSeek API


def _check_config():
    """检查必要的配置项"""
    issues = []

    if not os.getenv("NOTION_API_TOKEN"):
        issues.append("NOTION_API_TOKEN 未设置（在 .env 文件中）")
    if not os.getenv("NOTION_DATABASE_ID"):
        issues.append("NOTION_DATABASE_ID 未设置（在 .env 文件中）")
    if not os.getenv("DEEPSEEK_API_KEY"):
        issues.append("DEEPSEEK_API_KEY 未设置（在 .env 文件中）")

    if issues:
        console.print("[bold red]❌ 配置缺失:[/bold red]")
        for issue in issues:
            console.print(f"  • {issue}")
        console.print("\n[yellow]请参考 .env.example 文件创建 .env 并填入正确的值[/yellow]")
        return False
    return True


def _print_post_table(posts: list[PostDetail]):
    """用 Rich 表格打印帖子信息"""
    table = Table(title="📊 帖子列表", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("标题", style="cyan", max_width=30)
    table.add_column("博主", style="green", max_width=15)
    table.add_column("点赞", justify="right")
    table.add_column("收藏", justify="right")
    table.add_column("评论", justify="right")
    table.add_column("风格", max_width=15)
    table.add_column("场景", max_width=10)

    for i, post in enumerate(posts, 1):
        style_str = ", ".join(post.style[:3]) if post.style else "-"
        table.add_row(
            str(i),
            post.title[:30] if post.title else "(无标题)",
            post.blogger_name[:15] if post.blogger_name else "-",
            str(post.likes_count),
            str(post.bookmarks_count),
            str(post.comments_count),
            style_str,
            post.scene or "-",
        )

    console.print(table)


async def _analyze_posts(
    analyzer: LLMAnalyzer,
    posts: list[PostDetail],
) -> list[PostDetail]:
    """
    批量分析帖子内容

    对每条帖子调用 DeepSeek API，填充：
    - style (穿搭风格)
    - scene (场景)
    - shoot_type (拍摄类型)
    - emotion (情绪关键词)
    - viral_analysis (爆点分析)
    """
    console.print("\n[bold]🤖 开始 AI 分析...[/bold]")
    console.print(f"共 {len(posts)} 条帖子需要分析\n")

    for i, post in enumerate(posts):
        console.print(f"[{i+1}/{len(posts)}] 分析: {post.title[:40]}...")

        try:
            result = analyzer.analyze_post(
                title=post.title,
                blogger_tags=post.blogger_tags if post.blogger_tags else None,
                cover_desc=post.cover_desc,
                description=post.description,
            )

            post.style = result.get("style", [])
            post.scene = result.get("scene", "")
            post.shoot_type = result.get("shoot_type", [])
            post.emotion = result.get("emotion", [])
            post.viral_analysis = result.get("viral_analysis", "")

            # 如果爬虫没抓到博主标签，使用 AI 建议的
            if not post.blogger_tags:
                post.blogger_tags = result.get("blogger_tags_suggested", [])

            console.print(f"  [green]✓[/green] 风格={post.style}, 场景={post.scene}, "
                          f"情绪={post.emotion}")

        except Exception as e:
            logger.error(f"分析帖子失败 [{post.link}]: {e}")
            console.print(f"  [red]✗ 分析失败: {e}[/red]")
            # 继续处理下一条

        # API 调用间隔（避免限流）
        if i < len(posts) - 1:
            await asyncio.sleep(1)

    console.print("\n[green]✅ AI 分析完成[/green]")
    return posts


async def _write_to_notion(
    notion_client: NotionClient,
    mapper: FieldMapper,
    posts: list[PostDetail],
) -> dict:
    """
    将分析结果写入 Notion 数据库

    去重逻辑：按链接检查是否已存在，存在则更新，不存在则创建

    Returns:
        {"created": N, "updated": N, "skipped": N}
    """
    console.print("\n[bold]📝 写入 Notion 数据库...[/bold]")

    stats = {"created": 0, "updated": 0, "skipped": 0}

    for i, post in enumerate(posts):
        if not post.link:
            console.print(f"[{i+1}/{len(posts)}] [yellow]⚠ 跳过（无链接）[/yellow]")
            stats["skipped"] += 1
            continue

        console.print(f"[{i+1}/{len(posts)}] 写入: {post.title[:40]}...")

        try:
            # 构建 Notion 属性
            properties = mapper.map_post_to_notion_properties(
                platform=post.platform,
                link=post.link,
                title=post.title,
                blogger_tags=post.blogger_tags,
                style=post.style,
                scene=post.scene,
                shoot_type=post.shoot_type,
                emotion=post.emotion,
                cover_desc=post.cover_desc,
                bookmarks=post.bookmarks_count,
                likes=post.likes_count,
                comments=post.comments_count,
                viral_analysis=post.viral_analysis,
                blogger=post.blogger_link,
                notes=f"自动采集于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )

            # 检查是否已存在
            existing = notion_client.query_pages_by_link(post.link)
            if existing:
                notion_client.update_page(existing[0]["id"], properties)
                stats["updated"] += 1
                console.print(f"  [blue]↻ 已更新[/blue]")
            else:
                notion_client.create_page(properties)
                stats["created"] += 1
                console.print(f"  [green]✓ 已创建[/green]")

        except Exception as e:
            logger.error(f"写入 Notion 失败 [{post.link}]: {e}")
            console.print(f"  [red]✗ 写入失败: {e}[/red]")
            stats["skipped"] += 1

        # Notion API 限流保护
        if i < len(posts) - 1:
            await asyncio.sleep(0.5)

    return stats


async def main(
    keyword: str = "韩系穿搭",
    top_n: int = 10,
    headless: bool = False,
    skip_notion: bool = False,
    progress_callback: Optional[callable] = None,
):
    """
    主流程：

    1. 检查配置
    2. 启动浏览器（首次需扫码登录）
    3. 搜索话题，获取热门帖子列表
    4. 逐个访问帖子详情页，提取完整数据
    5. AI 分析每条帖子的内容
    6. 写入 Notion 数据库
    7. 输出报告

    Args:
        progress_callback: 可选，async callback(event_type, data) 用于 Web UI 进度推送
    """
    # ── 进度上报 helper ──
    async def _report(event_type: str, **data):
        if progress_callback:
            try:
                await progress_callback(event_type, data)
            except Exception:
                pass  # 回调失败不影响主流程

    console.print(Panel.fit(
        "[bold cyan]小红书话题数据 → Notion 数据库 自动分析工具[/bold cyan]\n"
        f"话题: {keyword} | Top: {top_n} | 模式: {'后台' if headless else '可视'}",
        border_style="cyan"
    ))

    # Step 0: 检查配置
    if not _check_config():
        return

    await _report("step", step="config", status="done")

    browser = None

    try:
        # ===== Step 1: 启动浏览器 =====
        console.print("\n[bold]🌐 Step 1: 启动浏览器...[/bold]")
        await _report("step", step="browser", status="start")
        browser = XHSBrowser(headless=headless)
        page = await browser.start()
        await _report("step", step="browser", status="done")

        # ===== Step 2: 搜索话题，获取帖子列表 =====
        console.print(f"\n[bold]🔍 Step 2: 搜索话题 [{keyword}]...[/bold]")
        await _report("step", step="search", status="start")
        scraper = XHSScraper(page)
        post_cards = await scraper.search_topic(
            keyword=keyword,
            max_fetch=50,
            top_n=top_n,
        )

        if not post_cards:
            console.print("[red]❌ 没有搜索到任何帖子，请检查关键词或网络[/red]")
            return

        console.print(f"\n[green]获取到 {len(post_cards)} 条帖子卡片[/green]")
        await _report("step", step="search", status="done")

        # 检查是否为 headless 模式且无法获取互动数据，给出提示
        if headless:
            console.print(
                "[yellow]⚠ 后台模式运行，如果数据不完整请尝试不带 --headless 运行[/yellow]"
            )

        # ===== Step 3: 解析帖子详情 =====
        console.print(f"\n[bold]📄 Step 3: 解析帖子详情 ({len(post_cards)} 条)...[/bold]")
        await _report("step", step="parse", status="start")
        parser = XHSParser(page)
        posts = []
        for i, card in enumerate(post_cards):
            console.print(f"[{i+1}/{len(post_cards)}] 解析: {card.title[:40]}...")
            try:
                detail = await parser.parse_post(card.link)
                # 合并卡片数据（搜索页可能有一些详情页没有的数据）
                if not detail.blogger_name and card.blogger_name:
                    detail.blogger_name = card.blogger_name
                if not detail.blogger_link and card.blogger_link:
                    detail.blogger_link = card.blogger_link
                if detail.likes_count == 0 and card.likes_count:
                    detail.likes_count = card.likes_count
                posts.append(detail)
            except Exception as e:
                logger.error(f"解析失败: {card.link}, {e}")
                # 即使解析失败，也保留基本信息
                posts.append(PostDetail(
                    title=card.title,
                    link=card.link,
                    likes_count=card.likes_count,
                    blogger_name=card.blogger_name,
                    blogger_link=card.blogger_link,
                ))
            # 请求间隔
            if i < len(post_cards) - 1:
                await asyncio.sleep(2)

        # 按点赞数排序
        posts.sort(key=lambda p: p.likes_count, reverse=True)

        # 打印帖子列表
        console.print(f"\n[green]成功解析 {len(posts)} 条帖子[/green]")
        _print_post_table(posts)
        await _report("step", step="parse", status="done")

        # ===== Step 4: AI 分析 =====
        console.print(f"\n[bold]🤖 Step 4: AI 内容分析...[/bold]")
        await _report("step", step="analyze", status="start")
        try:
            analyzer = LLMAnalyzer()
            posts = await _analyze_posts(analyzer, posts)
        except ValueError as e:
            console.print(f"[red]AI 分析初始化失败: {e}[/red]")
            console.print("[yellow]跳过 AI 分析，继续后续步骤[/yellow]")

        await _report("step", step="analyze", status="done")

        # 打印分析结果摘要
        console.print("\n[bold]📋 AI 分析结果摘要:[/bold]")
        for post in posts:
            console.print(
                f"  [cyan]{post.title[:30]:<30}[/cyan] "
                f"风格: {', '.join(post.style) if post.style else '-'} | "
                f"场景: {post.scene or '-'} | "
                f"爆点: {post.viral_analysis[:50] if post.viral_analysis else '-'}..."
            )

        # ===== Step 5: 写入 Notion =====
        if skip_notion:
            console.print("\n[yellow]⏭ --skip-notion 已设置，跳过 Notion 写入[/yellow]")
        else:
            console.print(f"\n[bold]📝 Step 5: 写入 Notion...[/bold]")
            await _report("step", step="notion", status="start")
            try:
                notion_config = NotionConfig(
                    api_token=os.getenv("NOTION_API_TOKEN", ""),
                    database_id=os.getenv("NOTION_DATABASE_ID", ""),
                )
                notion_client = NotionClient(notion_config)
                mapper = FieldMapper()

                stats = await _write_to_notion(notion_client, mapper, posts)

                console.print(f"\n[bold]📊 写入结果:[/bold]")
                console.print(f"  [green]✓ 新建: {stats['created']} 条[/green]")
                console.print(f"  [blue]↻ 更新: {stats['updated']} 条[/blue]")
                console.print(f"  [yellow]⚠ 跳过: {stats['skipped']} 条[/yellow]")
                await _report("step", step="notion", status="done")
                await _report("done", total_posts=len(posts), created=stats["created"],
                              updated=stats["updated"], skipped=stats["skipped"])

            except Exception as e:
                logger.error(f"Notion 写入失败: {e}")
                console.print(f"[red]❌ Notion 写入失败: {e}[/red]")

        # ===== 完成 =====
        console.print(Panel.fit(
            f"[bold green]✅ 任务完成！[/bold green]\n"
            f"话题: {keyword}\n"
            f"获取: {len(posts)} 条帖子\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            border_style="green"
        ))

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ 用户中断[/yellow]")
    except Exception as e:
        logger.error(f"运行出错: {e}", exc_info=True)
        console.print(f"\n[red]❌ 运行出错: {e}[/red]")
    finally:
        # 清理
        if browser:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="小红书话题数据 → Notion 数据库 自动分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.main --keyword "韩系穿搭"
  python -m src.main --keyword "通勤穿搭" --top 15
  python -m src.main --keyword "韩系穿搭" --headless --skip-notion
        """
    )
    parser.add_argument(
        "--keyword", "-k",
        type=str,
        default="韩系穿搭",
        help="要搜索的小红书话题关键词"
    )
    parser.add_argument(
        "--top", "-t",
        type=int,
        default=10,
        help="获取 Top N 条帖子 (默认 10)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="后台模式运行浏览器（不显示窗口）"
    )
    parser.add_argument(
        "--skip-notion",
        action="store_true",
        help="跳过 Notion 写入（仅测试爬虫+AI分析）"
    )
    args = parser.parse_args()

    asyncio.run(main(
        keyword=args.keyword,
        top_n=args.top,
        headless=args.headless,
        skip_notion=args.skip_notion,
    ))
