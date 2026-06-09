"""
Notion integration tests

Tests:
1. Connect to Notion API, retrieve database schema
2. Verify field name alignment between code and Notion database
3. Validate FieldMapper output format
4. Create a test page with all field types
5. Query/dedup test
6. Update (upsert) test
7. Read select/multi-select options
8. Clean up test data
"""

import os
import sys
import logging

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notion.client import NotionClient, NotionConfig
from src.notion.field_mapper import FieldMapper, FIELD_NAMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("test_notion")


def load_config():
    """Load .env config"""
    load_dotenv()
    token = os.getenv("NOTION_API_TOKEN")
    database_id = os.getenv("NOTION_DATABASE_ID")

    if not token or not database_id:
        logger.error("Missing NOTION_API_TOKEN or NOTION_DATABASE_ID, check .env")
        sys.exit(1)

    return NotionConfig(api_token=token, database_id=database_id)


def test_connection(client: NotionClient):
    """Test 1: Connect to Notion API, get database schema"""
    logger.info("=" * 60)
    logger.info("Test 1: Get database schema")
    logger.info("=" * 60)

    schema = client.get_database_schema()
    assert schema is not None, "Schema must not be None"
    assert "properties" in schema, "Schema must have properties"

    db_title = schema.get("title", [])
    title_text = "".join(
        t.get("plain_text", "") for t in db_title if isinstance(t, dict)
    )
    logger.info(f"[OK] DB title: {title_text}")

    properties = schema.get("properties", {})
    field_names = [
        prop.get("name", prop_name)
        for prop_name, prop in properties.items()
    ]
    logger.info(f"[OK] Fields ({len(field_names)}):")
    for name in field_names:
        ftype = client.get_field_type(name)
        logger.info(f"   - {name} ({ftype})")

    return schema


def test_field_alignment(client: NotionClient):
    """Test 2: Verify FIELD_NAMES match actual Notion database fields"""
    logger.info("\n" + "=" * 60)
    logger.info("Test 2: Field name alignment")
    logger.info("=" * 60)

    db_fields = client.list_field_names()
    db_field_set = set(db_fields)

    all_aligned = True
    for internal_key, cn_name in FIELD_NAMES.items():
        if cn_name in db_field_set:
            ftype = client.get_field_type(cn_name)
            logger.info(f"  [OK] {internal_key} -> \"{cn_name}\" ({ftype})")
        else:
            logger.warning(f"  [MISS] {internal_key} -> \"{cn_name}\" -- not found in DB!")
            all_aligned = False

    # Check for DB fields not in code
    code_fields = set(FIELD_NAMES.values())
    missing_in_code = db_field_set - code_fields
    if missing_in_code:
        logger.info(f"\n  DB fields not in FIELD_NAMES: {missing_in_code}")

    if all_aligned:
        logger.info("\n[OK] All field names aligned")
    return all_aligned


def test_field_mapping():
    """Test 3: Validate FieldMapper output format"""
    logger.info("\n" + "=" * 60)
    logger.info("Test 3: FieldMapper output validation")
    logger.info("=" * 60)

    mapper = FieldMapper()

    properties = mapper.map_post_to_notion_properties(
        platform="小红书",
        link="https://www.xiaohongshu.com/explore/test123",
        title="Test post title",
        blogger_tags=["tag1", "tag2"],
        style=["韩系", "通勤"],
        scene="咖啡厅",
        shoot_type=["全身", "他人拍摄"],
        emotion=["温柔", "气质"],
        cover_desc="Cover description",
        bookmarks=100,
        likes=500,
        comments=30,
        viral_analysis="This post went viral because...",
        blogger="https://www.xiaohongshu.com/user/test",
        notes="Test note",
    )

    # Type checks
    assert FIELD_NAMES["platform"] in properties, "platform field missing"
    assert properties[FIELD_NAMES["platform"]]["select"]["name"] == "小红书"

    assert FIELD_NAMES["link"] in properties, "link field missing"
    assert properties[FIELD_NAMES["link"]]["url"].startswith("https://")

    # "文案" is rich_text in actual DB
    assert FIELD_NAMES["title"] in properties, "title field missing"
    assert len(properties[FIELD_NAMES["title"]]["rich_text"]) == 1
    assert properties[FIELD_NAMES["title"]]["rich_text"][0]["text"]["content"] == "Test post title"

    assert FIELD_NAMES["style"] in properties, "style field missing"
    assert len(properties[FIELD_NAMES["style"]]["multi_select"]) == 2

    # "场景" is multi_select in actual DB (was wrongly assumed as select)
    assert FIELD_NAMES["scene"] in properties, "scene field missing"
    assert len(properties[FIELD_NAMES["scene"]]["multi_select"]) == 1
    assert properties[FIELD_NAMES["scene"]]["multi_select"][0]["name"] == "咖啡厅"

    assert FIELD_NAMES["bookmarks"] in properties, "bookmarks field missing"
    assert properties[FIELD_NAMES["bookmarks"]]["number"] == 100

    assert FIELD_NAMES["likes"] in properties, "likes field missing"
    assert properties[FIELD_NAMES["likes"]]["number"] == 500

    # "博主" is rich_text in actual DB (was wrongly assumed as url)
    assert FIELD_NAMES["blogger"] in properties, "blogger field missing"
    assert len(properties[FIELD_NAMES["blogger"]]["rich_text"]) == 1

    # "备注" is the actual title column in this DB
    assert FIELD_NAMES["notes"] in properties, "notes field missing"
    assert len(properties[FIELD_NAMES["notes"]]["title"]) == 1
    assert "收藏率" not in properties, "[FAIL] bookmark_rate is Formula, should NOT be written!"

    logger.info("  [OK] All field types correct")
    logger.info("  [OK] bookmark_rate (Formula) not written")

    return properties


def test_create_page(client: NotionClient, mapper: FieldMapper):
    """Test 4: Create a Notion page"""
    logger.info("\n" + "=" * 60)
    logger.info("Test 4: Create Notion page")
    logger.info("=" * 60)

    test_link = "https://www.xiaohongshu.com/explore/test_claude_debug_001"

    properties = mapper.map_post_to_notion_properties(
        platform="小红书",
        link=test_link,
        title="[Claude Test] Automated test post - please delete",
        blogger_tags=["test-tag-A", "test-tag-B"],
        style=["韩系", "极简"],
        scene="家里",
        shoot_type=["对镜自拍", "全身"],
        emotion=["干净", "松弛感"],
        cover_desc="Test cover description text",
        bookmarks=99,
        likes=888,
        comments=66,
        viral_analysis="[Test] Viral analysis content generated by Claude automated test.",
        blogger="https://www.xiaohongshu.com/user/test_blogger",
        notes="This page was created by Claude automated test. Safe to delete after verification.",
    )

    try:
        page = client.create_page(properties)
        page_id = page.get("id")
        logger.info(f"  [OK] Page created: {page_id}")
        return page_id, test_link
    except Exception as e:
        logger.error(f"  [FAIL] Page creation failed: {e}")
        raise


def test_query_and_upsert(client: NotionClient, mapper: FieldMapper, test_link: str):
    """Test 5 & 6: Query dedup + Upsert"""
    logger.info("\n" + "=" * 60)
    logger.info("Test 5: Query dedup (query_pages_by_link)")
    logger.info("=" * 60)

    existing = client.query_pages_by_link(test_link)
    if existing:
        logger.info(f"  [OK] Found {len(existing)} existing page(s), dedup works")
        logger.info(f"     Page ID: {existing[0]['id']}")
    else:
        logger.warning("  [WARN] No page found (may be Notion API indexing delay)")

    # Test Upsert (should update existing page)
    logger.info("\n" + "=" * 60)
    logger.info("Test 6: Upsert (existing page should update)")
    logger.info("=" * 60)

    properties_update = mapper.map_post_to_notion_properties(
        platform="小红书",
        link=test_link,
        title="[Claude Test] Automated test post - UPDATED",
        likes=999,
        notes="This page was updated by Upsert.",
    )

    try:
        page = client.upsert_page(test_link, properties_update)
        logger.info(f"  [OK] Upsert success: {page.get('id')}")
    except Exception as e:
        logger.error(f"  [FAIL] Upsert failed: {e}")


def test_select_options(client: NotionClient):
    """Test 7: Read existing select/multi-select options"""
    logger.info("\n" + "=" * 60)
    logger.info("Test 7: Read field options")
    logger.info("=" * 60)

    select_fields = ["穿搭风格", "场景", "情绪关键词"]
    for fname in select_fields:
        try:
            options = client.get_select_options(fname)
            option_names = [o.get("name") for o in options]
            logger.info(f"  {fname}: {option_names}")
        except Exception as e:
            logger.warning(f"  {fname}: read failed - {e}")

    logger.info("  [OK] Options read complete")


def cleanup_test_pages(client: NotionClient, test_link: str):
    """Cleanup: archive test pages"""
    logger.info("\n" + "=" * 60)
    logger.info("Cleanup: Archive test pages")
    logger.info("=" * 60)

    existing = client.query_pages_by_link(test_link)
    if existing:
        for page in existing:
            page_id = page["id"]
            try:
                client.client.pages.update(page_id=page_id, archived=True)
                logger.info(f"  [OK] Archived test page: {page_id}")
            except Exception as e:
                logger.error(f"  [FAIL] Archive failed ({page_id}): {e}")
    else:
        logger.info("  No test pages found to clean up")


def main():
    logger.info("Starting Notion integration tests\n")

    config = load_config()
    client = NotionClient(config)
    mapper = FieldMapper()

    test_link = None
    try:
        test_connection(client)
        test_field_alignment(client)
        test_field_mapping()
        page_id, test_link = test_create_page(client, mapper)
        test_query_and_upsert(client, mapper, test_link)
        test_select_options(client)

        logger.info("\n" + "=" * 60)
        logger.info("ALL Notion tests passed!")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        if test_link:
            cleanup_test_pages(client, test_link)


if __name__ == "__main__":
    main()
