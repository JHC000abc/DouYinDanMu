import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import threading
import websocket
import gzip
import json
import time
import urllib.request
import re
import os
import io
import csv
import codecs
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, TIMESTAMP, Text, text
from sqlalchemy.orm import sessionmaker, declarative_base

# ⚠️ 确保 dy_pb2.py 在同一目录下
try:
    import dy_pb2
except ImportError:
    print("❌ 错误: 未找到 dy_pb2.py，请确保该文件在同一目录下。")
    exit(1)

# ================= 全局配置与状态 =================
DB_CONFIG_FILE = "db_config.json"
ROOMS_CONFIG_FILE = "rooms.json"
engine = None
SessionLocal = None
Base = declarative_base()

# 默认数据库配置
DEFAULT_DB_CONFIG = {
    "host": "127.0.0.1",
    "port": "3306",
    "user": "root",
    "password": "",
    "database": "douyin_monitor"
}


# 模型定义
class DBConfig(BaseModel):
    host: str
    port: str
    user: str
    password: str
    database: str


class LiveDanmakuModel(Base):
    __tablename__ = "live_danmaku"
    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(50), index=True)
    user_nick = Column(String(100))
    user_uid = Column(String(50))
    display_id = Column(String(50))
    content = Column(Text)
    gender = Column(String(10))
    avatar_url = Column(Text)
    msg_type = Column(String(20))
    gift_id = Column(String(50))
    gift_count = Column(Integer)
    capture_time = Column(TIMESTAMP, default=datetime.now)


# ================= 数据库管理逻辑 =================

def load_db_config():
    """加载数据库配置，不存在则创建默认"""
    if not os.path.exists(DB_CONFIG_FILE):
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


# ================= 业务逻辑 =================

class RoomManager:
    def __init__(self):
        self.rooms = {}
        self.lock = threading.Lock()
        self.config_file = ROOMS_CONFIG_FILE
        self.load_rooms()

    def load_rooms(self):
        if not os.path.exists(self.config_file): return
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                saved_rooms = json.load(f)
                print(f"📂 正在恢复 {len(saved_rooms)} 个直播间...")
                for room_id, item in saved_rooms.items():
                    config = item.get("config", item)
                    name = item.get("name", config.get("name", "未知主播"))
                    title = item.get("title", config.get("title", ""))
                    page_url = item.get("page_url", config.get("page_url", f"https://live.douyin.com/{room_id}"))

                    self.rooms[room_id] = {
                        "room_id": room_id,
                        "status": "stopped",
                        "config": config,
                        "name": name,
                        "title": title,
                        "page_url": page_url,
                        "thread": None,
                        "ws": None,
                        "logs": []
                    }
                    # 默认不自动启动，或者根据需求开启
                    # self.start_room(room_id)
        except Exception as e:
            print(f"❌ 读取配置失败: {e}")

    def save_rooms(self):
        try:
            data_to_save = {}
            with self.lock:
                for rid, r in self.rooms.items():
                    data_to_save[rid] = {
                        "config": r["config"],
                        "name": r.get("name", "未知主播"),
                        "title": r.get("title", ""),
                        "page_url": r.get("page_url", "")
                    }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            print("💾 房间配置已保存")
        except Exception as e:
            print(f"❌ 保存失败: {e}")

    def add_room(self, raw_config):
        try:
            if isinstance(raw_config, str):
                config = json.loads(raw_config)
            else:
                config = raw_config

            url = config.get('url', '')
            match = re.search(r'room_id=(\d+)', url)
            if not match: return False, "配置中无法提取 room_id，请检查 JSON"

            room_id = match.group(1)
            anchor_name = config.get("name", f"主播 {room_id}")
            room_title = config.get("title", "")
            page_url = config.get("page_url", f"https://live.douyin.com/{room_id}")

            with self.lock:
                if room_id in self.rooms:
                    return False, f"直播间 {room_id} 已在监控列表中"

                self.rooms[room_id] = {
                    "room_id": room_id,
                    "status": "stopped",
                    "config": config,
                    "name": anchor_name,
                    "title": room_title,
                    "page_url": page_url,
                    "thread": None,
                    "ws": None,
                    "logs": []
                }

            self.start_room(room_id)
            self.save_rooms()
            return True, f"添加成功: {anchor_name}"
        except json.JSONDecodeError:
            return False, "配置 JSON 格式错误"
        except Exception as e:
            return False, str(e)

    def start_room(self, room_id):
        with self.lock:
            if room_id not in self.rooms: return
            room = self.rooms[room_id]
            if room["status"] == "running": return

            t = threading.Thread(target=self._ws_thread_func, args=(room_id,))
            t.daemon = True
            room["thread"] = t
            room["status"] = "running"
            t.start()
            self._log(room_id, "系统", f"监听启动: {room['name']}")

    def stop_room(self, room_id):
        with self.lock:
            if room_id not in self.rooms: return
            room = self.rooms[room_id]
            if room["ws"]:
                try:
                    room["ws"].close()
                except:
                    pass
            room["status"] = "stopped"
            self._log(room_id, "系统", "监听已停止")

    def remove_room(self, room_id):
        self.stop_room(room_id)
        with self.lock:
            if room_id in self.rooms: del self.rooms[room_id]
        self.save_rooms()

    def get_list(self):
        res = []
        with self.lock:
            for rid, r in self.rooms.items():
                res.append({
                    "room_id": rid,
                    "name": r.get("name", "未知主播"),
                    "title": r.get("title", ""),
                    "page_url": r.get("page_url", "#"),
                    "status": r["status"],
                    "latest_log": r["logs"][-1] if r["logs"] else "等待数据..."
                })
        return res

    def _log(self, room_id, user, msg):
        if room_id in self.rooms:
            log_entry = f"[{datetime.now().strftime('%H:%M:%S')}] {user}: {msg}"
            self.rooms[room_id]["logs"].append(log_entry)
            if len(self.rooms[room_id]["logs"]) > 50:  # 保留最近50条
                self.rooms[room_id]["logs"].pop(0)
            print(f"[房{room_id}] {log_entry}")

    def _ws_thread_func(self, room_id):
        room = self.rooms.get(room_id)
        if not room: return

        try:
            config = room["config"]
            ws_url = config['url'].replace(" ", "%20").replace("|", "%7C")
            headers = config['headers']
            user_agent = headers.get("User-Agent", "Mozilla/5.0")

            cookie_str = headers.get("Cookie", "").strip().rstrip(";")
            if "ttwid=" not in cookie_str:
                self._log(room_id, "系统", "自动补全 ttwid...")
                ttwid = self._fetch_ttwid(user_agent)
                if ttwid: cookie_str += f"; {ttwid}"

            header_list = [
                f"User-Agent: {user_agent}",
                f"Cookie: {cookie_str}",
                "Origin: https://live.douyin.com",
                "Referer: https://live.douyin.com/"
            ]

            def on_open(ws):
                self._log(room_id, "系统", "WebSocket 连接成功")

            def on_message(ws, message):
                self._handle_message(room_id, message)

            def on_error(ws, error):
                self._log(room_id, "错误", str(error))

            def on_close(ws, *args):
                self._log(room_id, "系统", "连接断开，5秒后重试...")
                time.sleep(5)

            while True:
                with self.lock:
                    if self.rooms.get(room_id, {}).get("status") != "running": break

                ws = websocket.WebSocketApp(
                    ws_url, header=header_list, cookie=cookie_str,
                    on_open=on_open, on_message=on_message,
                    on_error=on_error, on_close=on_close
                )

                with self.lock:
                    if room_id in self.rooms:
                        self.rooms[room_id]["ws"] = ws

                ws.run_forever(ping_interval=10, ping_timeout=5)

                # 检查是否是被主动停止的
                with self.lock:
                    if self.rooms.get(room_id, {}).get("status") != "running": break
                time.sleep(2)

        except Exception as e:
            self._log(room_id, "异常", str(e))
            with self.lock:
                if self.rooms.get(room_id): self.rooms[room_id]["status"] = "stopped"

    def _fetch_ttwid(self, ua):
        try:
            req = urllib.request.Request("https://live.douyin.com/", headers={"User-Agent": ua})
            with urllib.request.urlopen(req) as res:
                for c in res.headers.get_all('Set-Cookie'):
                    if 'ttwid=' in c: return c.split(';')[0]
        except:
            pass
        return None

    def _handle_message(self, room_id, message):
        try:
            # 寻找 Gzip 头
            gzip_index = -1
            for i in range(len(message) - 1):
                if message[i] == 0x1f and message[i + 1] == 0x8b:
                    gzip_index = i
                    break
            if gzip_index == -1: return

            try:
                decompressed = gzip.decompress(message[gzip_index:])
            except:
                return

            response = dy_pb2.Response()
            response.ParseFromString(decompressed)

            if response.messagesList:
                for msg in response.messagesList:
                    self._parse_single_msg(room_id, msg.payload, msg.method)

            if response.needAck:
                ack = dy_pb2.Response()
                ack.internalExt = response.internalExt
                ack.fetchType = response.fetchType
                # response.routeParams 是 map 字段，不能直接 update，需遍历
                # 但 Python Protobuf 实现有时允许，如果报错可忽略
                try:
                    ack_data = ack.SerializeToString()
                    push_frame = dy_pb2.PushFrame()  # 注意：如果 dy_pb2 里没有 PushFrame 定义，这里会报错。
                    # 通常 PushFrame 是外层结构。如果 dy.proto 没有 PushFrame，
                    # 抖音协议通常不需要复杂的 ACK，或者 ACK 结构不同。
                    # 鉴于只提供了 dy.proto，这里简化 ACK 逻辑，仅在有定义时发送
                    pass
                except:
                    pass
        except Exception as e:
            # print(f"Parse Error: {e}")
            pass

    def _parse_single_msg(self, room_id, payload, method):
        try:
            data = None
            if method == 'WebcastChatMessage':
                msg = dy_pb2.ChatMessage()
                msg.ParseFromString(payload)
                data = {"msg_type": "chat", "content": msg.content, "user_nick": msg.user.nickName,
                        "user_uid": str(msg.user.id)}
            elif method == 'WebcastMemberMessage':
                msg = dy_pb2.MemberMessage()
                msg.ParseFromString(payload)
                data = {"msg_type": "member", "content": "进入直播间", "user_nick": msg.user.nickName,
                        "user_uid": str(msg.user.id)}
            elif method == 'WebcastGiftMessage':
                msg = dy_pb2.GiftMessage()
                msg.ParseFromString(payload)
                count = msg.comboCount or msg.groupCount or msg.repeatCount or 1
                data = {"msg_type": "gift", "content": f"送礼物 {msg.giftId} x{count}", "user_nick": msg.user.nickName,
                        "user_uid": str(msg.user.id), "gift_id": str(msg.giftId), "gift_count": count}
            elif method == 'WebcastLikeMessage':
                msg = dy_pb2.LikeMessage()
                msg.ParseFromString(payload)
                data = {"msg_type": "like", "content": f"点赞 x{msg.count}", "user_nick": msg.user.nickName,
                        "user_uid": str(msg.user.id), "gift_count": msg.count}

            if data:
                data.update({"room_id": room_id, "display_id": "", "gender": "", "avatar_url": ""})
                self._save_db(data)
                icon_map = {'gift': '🎁', 'member': '🚪', 'like': '❤️', 'chat': '💬'}
                # 简化日志输出，避免控制台刷屏
                if data['msg_type'] in ['gift', 'chat']:
                    self._log(room_id, icon_map.get(data['msg_type'], '*'), f"{data['user_nick']}: {data['content']}")
        except:
            pass

    def _save_db(self, data):
        if not SessionLocal: return
        session = SessionLocal()
        try:
            log = LiveDanmakuModel(**data)
            session.add(log)
            session.commit()
        except Exception as e:
            print(f"DB Error: {e}")
        finally:
            session.close()


# ================= FastAPI App =================

init_db_engine()
manager = RoomManager()
app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# --- 关键修复：静态文件路由 ---
@app.get("/")
async def index():
    if os.path.exists("index.html"):
        return FileResponse("index.html", media_type="text/html")
    return HTMLResponse("<h1>Error: index.html not found</h1>")


@app.get("/script.js")
async def get_script():
    if os.path.exists("script.js"):
        return FileResponse("script.js", media_type="application/javascript")
    return HTMLResponse("console.error('script.js missing')", status_code=404)


@app.get("/style.css")
async def get_css():
    if os.path.exists("style.css"):
        return FileResponse("style.css", media_type="text/css")
    return HTMLResponse("/* style.css missing */", media_type="text/css")


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


if __name__ == "__main__":
    print("🚀 启动 Web 控制台: http://127.0.0.1:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
