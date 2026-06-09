"""
小红书浏览器管理

- 启动 Playwright Chromium（持久化用户目录 + 反检测）
- 使用 playwright-stealth 隐藏自动化特征
- 首次运行引导用户扫码登录
- 登录态自动持久化，后续无需重复登录
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# 用户数据目录（保存登录态）
USER_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "browser_profile"


# ====== 反检测脚本（精简兜底） ======
# playwright-stealth 覆盖了绝大多数指纹检测点。
# 这里只保留 playwright-stealth 未覆盖的额外修补。
_STEALTH_FALLBACK_JS = """
// 覆盖 permissions（playwright-stealth 可能未处理）
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
    Promise.resolve({ state: Notification.permission }) :
    originalQuery(parameters)
);
"""


class XHSBrowser:
    """小红书浏览器管理器"""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self) -> Page:
        """
        启动浏览器并返回页面对象

        - 使用持久化用户目录保存登录态
        - 首次运行需要用户扫码登录
        """
        logger.info("正在启动浏览器...")

        self.playwright = await async_playwright().start()

        # 确保用户数据目录存在
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 启动持久化浏览器上下文
        # 注意：已移除 --disable-blink-features=AutomationControlled，
        # 该 flag 本身是自动化工具的特征标记，反而会暴露身份
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 注入兜底反检测脚本
        await self.context.add_init_script(_STEALTH_FALLBACK_JS)

        # 创建或复用页面
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        # 对新页面应用 stealth 补丁（后续新打开的页面也适用）
        await self._apply_stealth(self.page)
        self.context.on('page', lambda p: asyncio.ensure_future(self._apply_stealth(p)))

        # 检查登录状态
        await self._ensure_logged_in()

        logger.info("浏览器启动完成")
        return self.page

    async def _apply_stealth(self, page: Page):
        """
        对页面应用反检测补丁

        优先使用 playwright-stealth（覆盖 WebGL / Canvas / 字体等十几个维度），
        降级时只注入精简版脚本。
        """
        try:
            from playwright_stealth import stealth_async
            await stealth_async(page)
            logger.debug("playwright-stealth 已应用")
        except ImportError:
            logger.warning(
                "playwright-stealth 未安装，反检测能力有限。"
                "请执行: pip install playwright-stealth"
            )

    async def _ensure_logged_in(self):
        """
        确保已登录小红书

        检查当前页面是否需要登录，如果需要则引导用户扫码
        """
        await self.page.goto(
            "https://www.xiaohongshu.com/explore",
            wait_until="domcontentloaded",
            timeout=30000
        )

        # 等待页面加载
        await asyncio.sleep(2)

        # 检查是否需要登录（出现登录弹窗）
        login_needed = await self.page.evaluate("""
            () => {
                // 检查是否有登录弹窗或登录按钮
                const loginBtn = document.querySelector('.login-btn');
                const qrCode = document.querySelector('.qrcode-img');
                const closeBtn = document.querySelector('.close-btn');
                return !!loginBtn || !!qrCode;
            }
        """)

        if login_needed:
            logger.info("=" * 60)
            logger.info("🔐 需要登录小红书，请在弹出的浏览器窗口中扫码登录")
            logger.info("=" * 60)

            # 等待用户完成登录（最多等待 120 秒）
            for i in range(120):
                await asyncio.sleep(1)

                # 检查是否已登录成功（URL 不再有登录相关路径）
                current_url = self.page.url
                if "login" not in current_url and "explore" in current_url:
                    # 进一步检查是否能看到正常内容
                    logged_in = await self.page.evaluate("""
                        () => {
                            const loginBtn = document.querySelector('.login-btn');
                            return !loginBtn;
                        }
                    """)
                    if logged_in:
                        logger.info("✅ 登录成功！")
                        # 等待登录态完全写入
                        await asyncio.sleep(2)
                        return

                if i % 10 == 0 and i > 0:
                    logger.info(f"等待登录中... ({i}s / 120s)")

            logger.warning("⚠️ 登录等待超时，部分功能可能受限")
        else:
            logger.info("✅ 已检测到登录状态")

    async def close(self):
        """关闭浏览器"""
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("浏览器已关闭")


async def create_browser(headless: bool = False) -> XHSBrowser:
    """工厂函数：创建并启动浏览器"""
    browser = XHSBrowser(headless=headless)
    return browser
