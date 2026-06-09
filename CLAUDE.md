# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a personal automation tool that scrapes Xiaohongshu (小红书) fashion posts for a given topic, uses DeepSeek API to analyze their content (style, scene, emotion, viral factors), and writes structured results into a Notion database. The target Notion database is focused on outfit/fashion content analysis and has 16 predefined fields.

## How to run

```bash
# Standard run (first run must be NON-headless — QR login required)
python -m src.main --keyword "韩系穿搭"

# Dry run: scrape + AI analysis only, skip Notion
python -m src.main --keyword "韩系穿搭" --skip-notion

# Headless mode (only works after initial login is cached)
python -m src.main --keyword "韩系穿搭" --headless

# More posts
python -m src.main --keyword "通勤穿搭" --top 15
```

## Architecture

The pipeline has three sequential stages orchestrated by `src/main.py`:

1. **Crawl** (`src/crawler/`) — Playwright-based browser automation
2. **Analyze** (`src/analyzer/`) — DeepSeek API content classification
3. **Write** (`src/notion/`) — Notion API database insertion

### Data flow and key types

```
Search keyword
  → XHSScraper.search_topic() returns list[PostCard]  (basic: title, link, likes_text)
  → XHSParser.parse_post()    returns PostDetail      (full: description, tags, stats, blogger)
  → LLMAnalyzer.analyze_post() fills in PostDetail    (AI fields: style, scene, emotion, viral_analysis)
  → FieldMapper.map_post_to_notion_properties()       (Python dict → Notion API properties format)
  → NotionClient.upsert_page()                        (dedup by link, create or update)
```

### Browser login persistence

`xhs_browser.py` uses Playwright's `launch_persistent_context` with a user data directory at `data/browser_profile/`. On first run, the browser opens non-headless and waits up to 120s for the user to scan the QR code. The login state (cookies, localStorage) persists to disk and subsequent runs reuse it. Never delete `data/browser_profile/` unless you need to re-login.

### Notion field mapping

`field_mapper.py` uses hardcoded Chinese field names (e.g., `"穿搭风格"`, `"爆点分析（主观）"`) that match the user's Notion database exactly. The field names are also listed in `config.yaml` under `notion_fields`. When the Notion database schema changes, both `field_mapper.py` and `config.yaml` need updating. The `收藏率` field is a Notion Formula — it must NOT be written to (Notion computes it from 收藏数/点赞数).

### AI analysis prompt design

`llm_analyzer.py` uses few-shot prompting with 4 real examples extracted from the user's existing Notion data. The system prompt constrains all tag selections to predefined candidate lists. If you need to add new tags (styles, scenes, etc.), update both `config.yaml` → `tag_system` and the corresponding constant lists in `llm_analyzer.py`. The two must stay in sync.

The analyzer returns structured JSON with these keys: `style`, `scene`, `shoot_type`, `emotion`, `blogger_tags_suggested`, `viral_analysis`.

## Configuration

- `.env` — secrets: `NOTION_API_TOKEN`, `NOTION_DATABASE_ID`, `DEEPSEEK_API_KEY`
- `.env.example` — template of the above
- `config.yaml` — app settings: tag options, field name mappings, crawl params (read by users, not loaded at runtime currently)

The YAML config is documentation/reference; the code has duplicated constants. If you change field names or tags, update both.

## Key constraints

- **First run must be non-headless** (the default) — QR code login requires a visible browser window
- Xiaohongshu's `xsec_token` in URLs is session-bound and auto-refreshed by Playwright's browser context
- Request intervals are hardcoded: 2s between detail page visits, 1s between AI calls, 0.5s between Notion writes — adjust only if hitting rate limits
- Duplicate detection is URL-based via `NotionClient.query_pages_by_link()`
- `src/main.py` uses absolute imports (`from src.xxx`) — always run from the project root (`E:\app`) with `python -m src.main`
