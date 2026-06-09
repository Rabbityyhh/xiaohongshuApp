# 小红书话题数据 → Notion 数据库 自动分析工具

自动获取小红书指定话题下点赞和评论排名前 10 的帖子，使用 AI 分析内容（穿搭风格、场景、情绪等），并写入 Notion 数据库。

## 功能

1. **小红书数据抓取** — 基于 Playwright 浏览器自动化，搜索话题获取热门帖子
2. **AI 内容分析** — 使用 DeepSeek API 分析穿搭风格、场景、拍摄类型、情绪关键词、爆点原因
3. **Notion 自动写入** — 按照已有数据库字段自动填充，支持去重（已存在则更新）

## 前置准备

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置 Notion API

1. 打开 https://www.notion.so/my-integrations 创建 Integration
2. 复制 `Internal Integration Token`
3. 打开你的 Notion 数据库页面，右上角 `...` → 连接 → 添加刚创建的 Integration
4. 从数据库 URL 中复制 Database ID（32位字符串）

### 3. 配置 DeepSeek API

1. 打开 https://platform.deepseek.com 获取 API Key

### 4. 创建 .env 文件

```bash
cp .env
```

编辑 `.env`，填入你的 API Token 和 Database ID。

### 5. 小红书账号

需要一个小红书账号用于扫码登录（首次运行时会弹出浏览器窗口）。登录状态会被保存，后续无需重复登录。

## 使用

```bash
# 基础用法：搜索 "韩系穿搭" 获取 Top 10
python -m src.main --keyword "韩系穿搭"

# 获取 Top 15
python -m src.main --keyword "通勤穿搭" --top 15

# 后台模式（不显示浏览器窗口）
python -m src.main --keyword "韩系穿搭" --headless

# 仅测试爬虫 + AI 分析，不写 Notion
python -m src.main --keyword "韩系穿搭" --skip-notion
```

## 项目结构

```
.
├── .env.example           # 环境变量模板
├── requirements.txt       # Python 依赖
├── config.yaml            # 应用配置（话题、标签体系等）
├── data/
│   └── browser_profile/   # 浏览器持久化登录态（自动生成）
├── src/
│   ├── main.py            # 主入口
│   ├── crawler/
│   │   ├── xhs_browser.py     # 浏览器管理 & 登录
│   │   ├── xhs_scraper.py     # 话题搜索 & 帖子列表
│   │   └── xhs_parser.py      # 帖子详情解析
│   ├── notion/
│   │   ├── client.py          # Notion API 封装
│   │   └── field_mapper.py    # 数据 → Notion 字段映射
│   └── analyzer/
│       └── llm_analyzer.py    # DeepSeek API 内容分析
└── README.md
```

## 数据流

```
输入话题 "韩系穿搭"
  → 浏览器搜索（Playwright）
  → 获取帖子列表（点赞Top10 + 评论Top10）
  → 解析详情页（标题/文案/互动数据/博主/标签）
  → AI 分析（穿搭风格/场景/拍摄类型/情绪/爆点原因）
  → 写入 Notion（去重：按链接检查）
```

## 注意事项

- 小红书有反爬机制，请求频率不宜过高，建议保持默认的 2 秒间隔
- 首次运行必须使用**非 headless 模式**（默认），以便扫码登录
- 登录态保存在 `data/browser_profile/`，不要删除该目录
- Notion API 有速率限制，程序已内置 0.5 秒写入间隔
- AI 分析每次调用约消耗 ~1000-2000 tokens
