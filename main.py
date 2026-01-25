import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import csv
import codecs
import os
import re
import json  # ✅ 新增：用于解析配置字符串
from datetime import datetime
from models.models_db import DBConfig
from utils.utils_dm import *
from utils.utils_zb import *
from utils.utils_db import *
from sqlalchemy import create_engine, text

os.environ["EXECJS_RUNTIME"] = "Node"

JS_FILE_PATH = "src/js/crypto.js"  # 默认值

manager = RoomManager()
app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/")
async def index():
    if os.path.exists("src/html/index.html"):
        return FileResponse("src/html/index.html", media_type="text/html")
    return HTMLResponse("<h1>Error: index.html not found</h1>")


@app.get("/monitor")
async def monitor_page():
    if os.path.exists("src/html/monitor.html"):
        return FileResponse("src/html/monitor.html", media_type="text/html")
    return HTMLResponse("<h1>Error: src/html/monitor.html not found</h1>")


@app.get("/script.js")
async def get_script():
    if os.path.exists("src/js/script.js"):
        return FileResponse("src/js/script.js", media_type="application/javascript")
    return HTMLResponse("console.error('script.js missing')", status_code=404)


@app.get("/style.css")
async def get_css():
    if os.path.exists("src/css/style.css"):
        return FileResponse("src/css/style.css", media_type="text/css")
    return HTMLResponse("/* style.css missing */", media_type="text/css")


@app.get("/css/monitor.css")
async def get_monitor_css():
    path = "src/css/monitor.css"
    if os.path.exists(path):
        return FileResponse(path, media_type="text/css")
    return HTMLResponse("/* monitor.css missing */", media_type="text/css")


@app.get("/js/monitor.js")
async def get_monitor_js():
    path = "src/js/monitor.js"
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    return HTMLResponse("console.error('monitor.js missing')", status_code=404)


# -----------------------------

@app.get("/api/rooms")
def list_rooms(): return manager.get_list()


@app.post("/api/add")
async def add_room(request: Request):
    try:
        data = await request.json()
        config_str = data.get("config")

        # ✅ 核心修复：在调用 manager 之前，手动检查是否重复
        if config_str:
            try:
                # 1. 将配置字符串解析为 JSON 对象
                config_json = json.loads(config_str)
                # 2. 尝试提取房间 ID (字段可能是 room_id, id 或 web_rid)
                page_url = config_json.get("page_url")
                path_match = re.search(r'/(\d+)', page_url.split('?')[0])
                if path_match:
                    web_rid = path_match.group(1)
                    current_rooms = manager.get_map()
                    if current_rooms.get(web_rid) is not None:
                        print(f"🚫 拦截重复添加: {page_url}")
                        return {"success": False, "msg": f"直播间 [{current_rooms.get(web_rid)}] 已在列表中！"}

            except Exception as e:
                # 解析 JSON 失败时不阻断流程，继续交给 manager 处理
                print(f"⚠️ 预检查解析失败: {e}")
                return {"success": False, "msg": f"直播间 [{page_url}] 已在列表中！"}

        # 如果通过了预检查，才真正添加
        success, msg = manager.add_room(config_str)
        return {"success": success, "msg": msg}

    except Exception as e:
        return {"success": False, "msg": f"系统错误: 输入有误，请重新从插件复制"}


@app.post("/api/start/{room_id}")
def start_room(room_id: str):
    manager.start_room(room_id)
    return {"status": "ok"}


@app.post("/api/stop/{room_id}")
def stop_room(room_id: str):
    manager.stop_room(room_id)
    return {"status": "ok"}


@app.post("/api/remove/{room_id}")
def remove_room(room_id: str):
    manager.remove_room(room_id)
    return {"status": "ok"}


@app.post("/api/get_live_url")
def get_live_url_api(room_id: str):
    """
    使用 Requests + JS 签名获取高清流
    """
    custom_headers = None
    config = manager.get_room_config(room_id)
    if config and "headers" in config:
        custom_headers = config["headers"]

    fetcher = DouyinStreamFetcher(room_id, custom_headers)
    url = fetcher.get_flv_url()

    if url:
        return {"success": True, "url": url}
    else:
        return {"success": False, "msg": "解析失败 (可能未开播或 Cookie 无效)"}


# === 新增：获取房间详细信息 API ===
@app.get("/api/room_info/{room_id}")
def get_room_info_api(room_id: str):
    """
    获取直播间详细状态 (在线人数、标题、是否开播)
    """
    custom_headers = None
    config = manager.get_room_config(room_id)
    if config and "headers" in config:
        custom_headers = config["headers"]

    fetcher = DouyinStreamFetcher(room_id, custom_headers)
    info = fetcher.get_room_info()
    return info


# =================================


@app.get("/api/db/config")
def get_db_config_api():
    config = load_db_config()
    return config


@app.post("/api/db/test")
def test_db_connection(config: DBConfig):
    url = get_db_url(config.dict())
    try:
        test_engine = create_engine(url)
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"success": True, "msg": "✅ 连接成功"}
    except Exception as e:
        return {"success": False, "msg": f"❌ 连接失败: {str(e)}"}


@app.post("/api/db/save")
def save_db_config_api(config: DBConfig):
    save_db_config(config)
    init_db_engine()
    if engine:
        return {"success": True, "msg": "配置已保存并重载"}
    else:
        return {"success": False, "msg": "配置已保存，但连接失败，请检查"}


@app.get("/api/download/{room_id}")
def download_room_data(room_id: str):
    if not SessionLocal:
        return HTMLResponse(content="<h1>数据库未连接，无法导出</h1>", status_code=500)

    def iter_csv():
        session = SessionLocal()
        try:
            yield codecs.BOM_UTF8
            output = io.StringIO()
            writer = csv.writer(output)
            headers = ["记录ID", "直播间ID", "用户昵称", "用户UID", "内容/行为", "类型", "礼物ID", "礼物数量",
                       "采集时间"]
            writer.writerow(headers)
            yield output.getvalue().encode('utf-8')
            output.seek(0)
            output.truncate(0)

            query = session.query(LiveDanmakuModel).filter(
                LiveDanmakuModel.room_id == room_id
            ).execution_options(stream_results=True).yield_per(2000)

            for r in query:
                row = [
                    str(r.id), str(r.room_id), r.user_nick or "", str(r.user_uid or ""),
                                               r.content or "", r.msg_type or "", str(r.gift_id or ""),
                    str(r.gift_count or 0),
                    r.capture_time.strftime("%Y-%m-%d %H:%M:%S") if r.capture_time else ""
                ]
                writer.writerow(row)
                yield output.getvalue().encode('utf-8')
                output.seek(0)
                output.truncate(0)
        except Exception as e:
            print(f"Export Error: {e}")
        finally:
            session.close()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"live_{room_id}_{timestamp}.csv"

    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/monitor/{uid}")
def monitor(uid: str):
    """
    前端页面调用的接口
    """
    print(f"🔍 收到解析请求, 参数: {uid}")

    clean_uid = uid
    if "douyin.com" in uid:
        match = re.search(r'(\d{10,})', uid)
        if match:
            clean_uid = match.group(1)
            print(f"   提取到 ID: {clean_uid}")

    url = f"https://live.douyin.com/{clean_uid}"
    print("url",url)

    recorder = DouyinRecorder(url)
    stream_url = recorder.get_stream_url()
    print("stream_url",stream_url)
    if stream_url:
        print(f"✅ 解析成功: {stream_url[:50]}...")
        return {"code": 200, "url": stream_url, "msg": "success"}
    else:
        print("❌ 解析失败")
        return {"code": 500, "url": None, "msg": "解析失败，请检查房间号或稍后重试"}


if __name__ == "__main__":
    print("🚀 启动 Web 控制台: http://127.0.0.1:8000")
    print("📺 监控墙页面: http://127.0.0.1:8000/monitor")
    uvicorn.run(app, host="0.0.0.0", port=8000)