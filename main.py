import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import io
import csv
import codecs
from sqlalchemy.orm import declarative_base
from models.models_db import DBConfig

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, text

from models.models_dm import Base

os.environ["EXECJS_RUNTIME"] = "Node"

# ================= 全局配置与状态 =================
DB_CONFIG_FILE = "config/db_config.json"

JS_FILE_PATH = "src/js/crypto.js"  # 默认值

engine = None
SessionLocal = None

# 默认数据库配置
DEFAULT_DB_CONFIG = {
    "host": "127.0.0.1",
    "port": "3306",
    "user": "root",
    "password": "",
    "database": "douyin_monitor"
}

from utils.utils_dm import *
from utils.utils_zb import *


def load_db_config():
    """加载数据库配置，不存在则创建默认"""
    if not os.path.exists(DB_CONFIG_FILE):
        # 确保目录存在
        os.makedirs(os.path.dirname(DB_CONFIG_FILE), exist_ok=True)
        save_db_config(DEFAULT_DB_CONFIG)
        return DEFAULT_DB_CONFIG
    try:
        with open(DB_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return DEFAULT_DB_CONFIG


def save_db_config(config):
    """保存数据库配置到文件"""
    if hasattr(config, 'dict'):
        config = config.dict()
    # 确保目录存在
    os.makedirs(os.path.dirname(DB_CONFIG_FILE), exist_ok=True)
    with open(DB_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def get_db_url(config):
    """拼接数据库连接字符串"""
    return f"mysql+pymysql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}?charset=utf8mb4"


def init_db_engine():
    """初始化或重置数据库引擎"""
    global engine, SessionLocal

    config = load_db_config()
    db_url = get_db_url(config)

    try:
        if engine:
            engine.dispose()

        print(f"🔄 正在连接数据库: {config['host']}:{config['port']} ({config['database']})...")
        engine = create_engine(db_url, pool_pre_ping=True, pool_size=20, max_overflow=20)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        # 测试连接并自动建表
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            print("✅ 数据库连接成功")
            Base.metadata.create_all(bind=engine)

    except Exception as e:
        print(f"⚠️ 数据库连接失败: {e}")
        print("💡 提示: 请在网页控制台的'系统设置'中配置正确的数据库信息。")
        engine = None
        SessionLocal = None


# ================= FastAPI App =================

init_db_engine()
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
    # 修改：从根目录进入 src/js 查找
    if os.path.exists("src/js/script.js"):
        return FileResponse("src/js/script.js", media_type="application/javascript")
    return HTMLResponse("console.error('script.js missing')", status_code=404)


@app.get("/style.css")
async def get_css():
    # 修改：从根目录进入 src/css 查找
    if os.path.exists("src/css/style.css"):
        return FileResponse("src/css/style.css", media_type="text/css")
    return HTMLResponse("/* style.css missing */", media_type="text/css")


# === ⚠️ 新增修改：添加针对 monitor.css 和 monitor.js 的路由支持 ===

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
    data = await request.json()
    success, msg = manager.add_room(data.get("config"))
    return {"success": success, "msg": msg}


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


# === 新增：获取直播流接口 (升级版) ===
@app.post("/api/get_live_url")
def get_live_url_api(room_id: str):
    """
    使用 Requests + JS 签名获取高清流
    """
    custom_headers = None

    # 尝试从弹幕配置中获取 Cookie，以提高解析成功率
    config = manager.get_room_config(room_id)
    if config and "headers" in config:
        custom_headers = config["headers"]

    # 实例化新的 Fetcher
    fetcher = DouyinStreamFetcher(room_id, custom_headers)
    url = fetcher.get_flv_url()

    if url:
        return {"success": True, "url": url}
    else:
        return {"success": False, "msg": "解析失败 (可能未开播或 Cookie 无效)"}


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


# ================= 新增接口：用于 monitor.html 页面调用 =================
@app.get("/monitor/{uid}")
def monitor(uid: str):
    """
    前端页面调用的接口：
    1. 接收 UID 或 URL（但这里假设前端已经提取了 ID，或者我们在后端处理链接）
    2. 如果 uid 是完整链接，尝试提取 ID
    3. 调用 Recorder 解析流地址
    4. 返回 JSON 数据
    """
    print(f"🔍 收到解析请求, 参数: {uid}")

    # 简单清洗数据，防止把 https://... 传进来导致拼接错误
    clean_uid = uid
    if "douyin.com" in uid:
        match = re.search(r'(\d{10,})', uid)
        if match:
            clean_uid = match.group(1)
            print(f"   提取到 ID: {clean_uid}")

    url = f"https://live.douyin.com/{clean_uid}"

    # 使用 Recorder
    recorder = DouyinRecorder(url)
    # 直接调用 get_stream_url() 而不是 record()，因为我们需要返回值
    stream_url = recorder.get_stream_url()

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
