"""
小红书分析工具 — Web 服务

FastAPI 后端：接收关键词，启动管线，通过 SSE 实时推送进度
"""

import asyncio
import io
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Windows 编码修复（必须在最前面） ──
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    # 确保标准流使用 utf-8，避免 cp932 编码错误
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name)
        if hasattr(_stream, "buffer"):
            try:
                setattr(sys, _stream_name,
                        io.TextIOWrapper(_stream.buffer, encoding="utf-8", errors="replace"))
            except (AttributeError, OSError):
                pass

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

logger = logging.getLogger("server")

# ── Run state ──────────────────────────────────────────────────────────────
# 简单字典存储所有运行状态，不做持久化
_runs: dict[str, dict] = {}
_run_lock = asyncio.Lock()


def _now():
    return datetime.now().strftime("%H:%M:%S")


# ── SSE 日志处理器 ─────────────────────────────────────────────────────────

class SSEQueueHandler(logging.Handler):
    """把 logging 记录转成 SSE 事件，推入 asyncio.Queue"""

    def __init__(self, queue: asyncio.Queue):
        super().__init__()
        self.queue = queue

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            # 推入队列（在 event loop 中调度）
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.queue.put({
                    "type": "log",
                    "level": record.levelname,
                    "message": msg,
                    "time": _now(),
                }))
        except Exception:
            pass  # SSE 推送失败不应影响主流程


# ── 标准输出捕获 ───────────────────────────────────────────────────────────

class _StdoutCapture(io.TextIOBase):
    """同时写入原始 stdout 和 SSE 队列"""

    def __init__(self, queue: asyncio.Queue, original):
        self.queue = queue
        self.original = original

    def write(self, s: str):
        self.original.write(s)
        stripped = s.rstrip("\n").strip()
        if stripped:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.queue.put({
                    "type": "log",
                    "level": "INFO",
                    "message": stripped,
                    "time": _now(),
                }))
        return len(s)

    def flush(self):
        self.original.flush()

    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self.original, "errors", "replace")

    @property
    def buffer(self):
        return getattr(self.original, "buffer", None)

    def fileno(self):
        return getattr(self.original, "fileno", lambda: -1)()


# ── 状态管理 ───────────────────────────────────────────────────────────────

def _create_run(keyword: str, top_n: int, headless: bool, skip_notion: bool) -> str:
    """创建一次运行记录，返回 run_id"""
    run_id = str(uuid.uuid4())[:8]
    _runs[run_id] = {
        "id": run_id,
        "keyword": keyword,
        "top_n": top_n,
        "headless": headless,
        "skip_notion": skip_notion,
        "status": "pending",  # pending → running → done / error / cancelled
        "queue": asyncio.Queue(),
        "started_at": None,
        "finished_at": None,
        "stats": {},
        "task": None,
        "cancel_flag": asyncio.Event(),
    }
    return run_id


def _get_run(run_id: str) -> dict:
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(404, f"运行 {run_id} 不存在")
    return run


# ── 管道执行 ───────────────────────────────────────────────────────────────

async def _execute_pipeline(
    run_id: str,
    keyword: str,
    top_n: int,
    headless: bool,
    skip_notion: bool,
):
    """后台执行完整的爬虫→分析→写入管线，并通过 SSE 推送进度"""
    run = _runs[run_id]
    queue = run["queue"]
    cancel = run["cancel_flag"]

    run["status"] = "running"
    run["started_at"] = _now()

    # ── progress callback ──
    async def progress_callback(event_type: str, data: dict):
        if cancel.is_set():
            raise asyncio.CancelledError("用户取消")
        await queue.put({"type": event_type, "time": _now(), **data})

    # ── 安装日志捕获 ──
    root_logger = logging.getLogger()
    sse_handler = SSEQueueHandler(queue)
    sse_handler.setLevel(logging.INFO)
    sse_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(sse_handler)

    # ── 捕获 stdout（Rich 的输出） ──
    original_stdout = sys.stdout
    sys.stdout = _StdoutCapture(queue, original_stdout)

    try:
        from src.main import main as run_main
        await run_main(
            keyword=keyword,
            top_n=top_n,
            headless=headless,
            skip_notion=skip_notion,
            progress_callback=progress_callback,
        )
        run["status"] = "done"
        await queue.put({"type": "done", "time": _now(), "status": "success"})

    except asyncio.CancelledError:
        run["status"] = "cancelled"
        await queue.put({"type": "error", "time": _now(), "message": "任务已被用户取消"})

    except Exception as e:
        logger.error(f"管线执行失败: {e}", exc_info=True)
        run["status"] = "error"
        await queue.put({"type": "error", "time": _now(), "message": str(e)})

    finally:
        sys.stdout = original_stdout
        root_logger.removeHandler(sse_handler)
        run["finished_at"] = _now()
        await queue.put({"type": "stream_end", "time": _now()})


# ── FastAPI App ─────────────────────────────────────────────────────────────

app = FastAPI(title="小红书分析工具")

# 读入前端页面（启动时缓存）
_WEB_DIR = Path(__file__).parent / "web"
_INDEX_HTML = (_WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index():
    return _INDEX_HTML


@app.post("/api/run")
async def api_run(
    keyword: str = Form(...),
    top_n: int = Form(10),
    headless: bool = Form(False),
    skip_notion: bool = Form(False),
):
    """启动一次新的分析运行"""
    if _run_lock.locked():
        raise HTTPException(409, "当前有任务正在运行中，请等待完成或取消后再试")

    run_id = _create_run(keyword, top_n, headless, skip_notion)
    run = _runs[run_id]

    # 后台启动管线（lock 在 _execute_pipeline 内持有）
    async def _locked():
        async with _run_lock:
            run["task"] = asyncio.current_task()
            await _execute_pipeline(run_id, keyword, top_n, headless, skip_notion)

    asyncio.create_task(_locked())

    return JSONResponse({"run_id": run_id, "status": "started"})


@app.get("/api/run/{run_id}/stream")
async def api_run_stream(run_id: str, request: Request):
    """SSE 端点：实时推送管线进度"""
    run = _get_run(run_id)
    queue = run["queue"]

    async def event_stream():
        while True:
            # 检查客户端是否断开
            if await request.is_disconnected():
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=10)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("stream_end",):
                    break
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                yield f"data: {json.dumps({'type': 'heartbeat', 'time': _now()})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/run/{run_id}/stop")
async def api_run_stop(run_id: str):
    """停止正在运行的任务"""
    run = _get_run(run_id)
    if run["status"] != "running":
        raise HTTPException(400, f"任务状态为 '{run['status']}'，无法停止")
    run["cancel_flag"].set()
    return JSONResponse({"status": "cancelling"})


@app.get("/api/run/{run_id}/status")
async def api_run_status(run_id: str):
    """查询运行状态"""
    run = _get_run(run_id)
    return JSONResponse({
        "id": run["id"],
        "keyword": run["keyword"],
        "status": run["status"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "stats": run["stats"],
    })


# ── 入口 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.server:app", host="127.0.0.1", port=8000, reload=False)
