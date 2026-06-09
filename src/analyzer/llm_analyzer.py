"""
AI 内容分析模块 — 使用 DeepSeek API 分析小红书帖子内容

分析维度（对应 Notion 数据库字段）：
- 穿搭风格 (Multi-select)
- 场景 (Select)
- 拍摄类型 (Multi-select)
- 情绪关键词 (Multi-select)
- 爆点分析（主观）(Text)

DeepSeek API 兼容 OpenAI SDK，调用方式与 OpenAI Chat Completions 一致。
"""

import json
import logging
import os
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ====== 标签体系 ======
# 与 config.yaml 保持同步，作为 AI 的候选标签池

STYLE_OPTIONS = [
    "轻熟", "韩系", "日系", "通勤", "极简", "松弛感", "高级感",
    "学院风", "运动休闲", "法式", "美式复古", "新中式", "街头",
    "优雅", "暗黑"
]

SCENE_OPTIONS = [
    "家里", "酒店", "公园", "咖啡厅", "街头", "办公室", "商场",
    "公共卫生间", "地铁/公交", "展览/美术馆", "户外郊野", "健身房",
    "学校", "餐厅"
]

SHOOT_TYPE_OPTIONS = [
    "对镜自拍", "多角度", "露脸", "不露脸", "半身", "全身",
    "他人拍摄", "公园拍摄", "动态/视频", "静态/图片", "细节特写",
    "俯拍", "座位自拍"
]

EMOTION_OPTIONS = [
    "温柔", "气质", "精致", "干净", "松弛感", "有钱感",
    "普通人可复制", "通勤感", "氛围感", "显瘦", "酷", "元气",
    "甜美", "知性", "清冷", "辣妹"
]

# ====== Few-shot 示例 ======
# 从用户 Notion 数据库中提取的真实分析案例

FEW_SHOT_EXAMPLES = [
    {
        "input": {
            "title": "温柔紫～",
            "blogger_tags": ["ootdinspo", "韩系穿搭"],
            "cover_desc": "酒店对镜自拍",
            "文案简要": "一套紫色系的穿搭，温柔又高级"
        },
        "output": {
            "style": ["轻熟", "韩系"],
            "scene": "酒店",
            "shoot_type": ["多角度", "对镜自拍", "露脸"],
            "emotion": ["气质", "温柔", "精致"],
            "blogger_tags_suggested": ["ootdinspo", "韩系穿搭"],
            "viral_analysis": "干净温柔，看起来有一点高级。不像小孩子。成熟知性。拍摄清晰，动作自然。"
        }
    },
    {
        "input": {
            "title": "一周穿搭不重样｜衬衫搭配合集✔️",
            "blogger_tags": ["一周穿搭不重样", "气质穿搭", "衬衫", "阔腿裤"],
            "cover_desc": "多角度对镜自拍",
            "文案简要": "一周衬衫穿搭合集，搭配阔腿裤和运动鞋"
        },
        "output": {
            "style": ["日系", "通勤"],
            "scene": "家里",
            "shoot_type": ["对镜自拍"],
            "emotion": ["普通人可复制", "通勤感"],
            "blogger_tags_suggested": ["一周穿搭不重样", "气质穿搭", "衬衫", "阔腿裤"],
            "viral_analysis": "通勤穿搭，普通人复制简单，不需要身材和颜值撑着。衬衫加休闲西裤，配运动鞋。日常感非常强。"
        }
    },
    {
        "input": {
            "title": "入夏的一周穿搭小合集～",
            "blogger_tags": [],
            "cover_desc": "站在公园里，背景是绿色的树，视觉冲击力强。",
            "文案简要": "入夏穿搭合集，在公园拍摄的宽松舒适搭配"
        },
        "output": {
            "style": ["松弛感", "通勤"],
            "scene": "公园",
            "shoot_type": ["公园拍摄"],
            "emotion": ["干净", "普通人可复制", "松弛感"],
            "blogger_tags_suggested": [],
            "viral_analysis": "服装宽松舒适，平时上班，逛街都能穿。拍摄照片吸引人，阳光下的公园拍摄，配合动作给人很阳光积极的感觉。"
        }
    },
    {
        "input": {
            "title": "打工人一周穿搭",
            "blogger_tags": ["一周穿搭不重样", "韩系穿搭", "打工人的日常穿搭"],
            "cover_desc": "举手机挡脸，他人拍摄",
            "文案简要": "适合上班的一周穿搭，有点小设计又不过分"
        },
        "output": {
            "style": ["极简", "通勤", "韩系"],
            "scene": "家里",
            "shoot_type": ["对镜自拍"],
            "emotion": ["普通人可复制", "通勤感"],
            "blogger_tags_suggested": ["一周穿搭不重样", "韩系穿搭", "打工人的日常穿搭"],
            "viral_analysis": "适合上班通勤，但是又有一点小设计。有一点精致，普通人也可以复制。"
        }
    }
]

# ====== System Prompt ======

SYSTEM_PROMPT = """你是一个专业的时尚穿搭内容分析助手。你的任务是根据小红书穿搭帖子的信息，分析并输出结构化的分类标签和爆点分析。

## 分析要求

1. **穿搭风格** (style): 从候选列表中选择 1-3 个最匹配的风格标签
2. **场景** (scene): 从候选列表中选择 1 个最匹配的场景
3. **拍摄类型** (shoot_type): 从候选列表中选择 1-3 个拍摄方式
4. **情绪关键词** (emotion): 从候选列表中选择 2-4 个最贴切的情绪标签
5. **博主自定义标签建议** (blogger_tags_suggested): 从帖子中提取或推断合适的标签
6. **爆点分析** (viral_analysis): 用 2-3 句话分析这个帖子为什么受欢迎（中文）

## 重要规则
- 只从候选列表中选择，不要编造新标签
- 如果没有完全匹配的场景，选择最接近的
- 爆点分析要具体，结合帖子的穿搭特点、拍摄方式、场景氛围来分析
- 关注：衣服是否日常可复制、拍摄是否清晰有质感、场景是否有代入感、是否有氛围感"""


def _build_user_prompt(
    title: str = "",
    blogger_tags: Optional[list[str]] = None,
    cover_desc: str = "",
    description: str = "",
) -> str:
    """构建发给 AI 的用户消息"""
    parts = []

    if title:
        parts.append(f"<帖子标题>\n{title}\n</帖子标题>")
    else:
        parts.append("<帖子标题>\n（无标题）\n</帖子标题>")

    if blogger_tags:
        tags_str = "、".join(blogger_tags)
        parts.append(f"<博主标签>\n{tags_str}\n</博主标签>")
    else:
        parts.append("<博主标签>\n（无）\n</博主标签>")

    if cover_desc:
        parts.append(f"<封面描述>\n{cover_desc}\n</封面描述>")
    else:
        parts.append("<封面描述>\n（无）\n</封面描述>")

    if description:
        parts.append(f"<帖子文案>\n{description}\n</帖子文案>")
    else:
        parts.append("<帖子文案>\n（无）\n</帖子文案>")

    parts.append("请根据以上信息，分析这个穿搭帖子并输出 JSON。")

    return "\n".join(parts)


class LLMAnalyzer:
    """使用 DeepSeek API 分析穿搭帖子内容"""

    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat"):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = model

    def analyze_post(
        self,
        title: str = "",
        blogger_tags: Optional[list[str]] = None,
        cover_desc: str = "",
        description: str = "",
    ) -> dict:
        """
        分析单个帖子的内容

        Args:
            title: 帖子标题/文案
            blogger_tags: 博主使用的标签
            cover_desc: 封面图描述
            description: 帖子正文

        Returns:
            分析结果字典:
            {
                "style": [...],
                "scene": "...",
                "shoot_type": [...],
                "emotion": [...],
                "blogger_tags_suggested": [...],
                "viral_analysis": "..."
            }
        """
        # 裁剪文案长度（节省 token）
        if description and len(description) > 500:
            description = description[:500] + "..."

        user_message = _build_user_prompt(
            title=title,
            blogger_tags=blogger_tags,
            cover_desc=cover_desc,
            description=description,
        )

        # 构建完整的系统提示（含标签选项）
        full_system = SYSTEM_PROMPT + f"""

## 穿搭风格候选列表
{json.dumps(STYLE_OPTIONS, ensure_ascii=False)}

## 场景候选列表
{json.dumps(SCENE_OPTIONS, ensure_ascii=False)}

## 拍摄类型候选列表
{json.dumps(SHOOT_TYPE_OPTIONS, ensure_ascii=False)}

## 情绪关键词候选列表
{json.dumps(EMOTION_OPTIONS, ensure_ascii=False)}"""

        # 构建消息列表（DeepSeek/OpenAI 格式：system 消息在 messages 数组中）
        messages = [{"role": "system", "content": full_system}]

        for example in FEW_SHOT_EXAMPLES:
            inp = example["input"]
            out = example["output"]

            ex_user = _build_user_prompt(
                title=inp["title"],
                blogger_tags=inp.get("blogger_tags", []),
                cover_desc=inp.get("cover_desc", ""),
                description=inp.get("文案简要", ""),
            )

            messages.append({"role": "user", "content": ex_user})
            messages.append({
                "role": "assistant",
                "content": json.dumps(out, ensure_ascii=False, indent=2)
            })

        # 最后一条是真正的用户消息
        messages.append({"role": "user", "content": user_message})

        logger.info(f"正在分析帖子: {title[:50]}...")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=2000,
                messages=messages,
                temperature=0.3,
            )

            # 解析返回的 JSON
            content = response.choices[0].message.content
            result = json.loads(content)

            # 验证必要字段
            required_fields = ["style", "scene", "shoot_type", "emotion", "viral_analysis"]
            for field in required_fields:
                if field not in result:
                    result[field] = [] if field != "scene" else ""
                    if field == "viral_analysis":
                        result[field] = ""

            logger.info(f"分析完成: 风格={result.get('style')}, 场景={result.get('scene')}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"解析 DeepSeek 返回的 JSON 失败: {e}")
            logger.error(f"原始返回: {content[:500]}")
            return {
                "style": [],
                "scene": "",
                "shoot_type": [],
                "emotion": [],
                "blogger_tags_suggested": [],
                "viral_analysis": ""
            }
        except Exception as e:
            logger.error(f"DeepSeek API 调用失败: {e}")
            raise
