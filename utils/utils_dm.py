# !/usr/bin/python3
# -*- coding:utf-8 -*-
"""
@author: JHC000abc@gmail.com
@file: utils_dm.py
@time: 2026/1/23 14:20 
@desc: 

"""

import threading
import websocket
import gzip
import json
import time
import urllib.request
import requests
import re
import os
import shutil
from urllib.parse import urlencode
from datetime import datetime
from models.models_dm import LiveDanmakuModel


os.environ["EXECJS_RUNTIME"] = "Node"

try:
    import execjs
    import execjs.runtime_names
except ImportError:
    print("❌ 错误: 未安装 PyExecJS 库。请运行: pip install PyExecJS")
    pass

# ⚠️ 确保 dy_pb2.py 在同一目录下
try:
    from plugins import dy_pb2
except ImportError:
    print("❌ 错误: 未找到 dy_pb2.py，请确保该文件在同一目录下。")
    # exit(1) # 避免直接退出，允许仅运行流服务


ROOMS_CONFIG_FILE = "config/rooms.json"


SessionLocal = None


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
JS_FILE_PATH = "src/js/crypto.js"  # 默认值


class DouyinStreamFetcher:
    """
    基于 Requests + PyExecJS 的高级流解析器
    代码逻辑已与 main2.py 完全对齐 (使用 DouyinRecorder 的逻辑)
    """

    def __init__(self, room_id, custom_headers=None):
        self.room_id = room_id
        # 初始 URL
        self.room_url = f"https://live.douyin.com/{room_id}"

        self.session = requests.Session()
        # ⚠️ 强制使用 main2.py 中验证通过的 Windows UA
        self.session.headers.update({
            "User-Agent": STREAM_USER_AGENT,
            "Referer": "https://live.douyin.com/",
        })

        # 注入用户配置的 Headers (如 Cookie)，但严格过滤 User-Agent
        if custom_headers and isinstance(custom_headers, dict):
            safe_headers = {
                k: v for k, v in custom_headers.items()
                if k.lower() not in ['host', 'user-agent']
            }
            self.session.headers.update(safe_headers)

        self.js_ctx = self._load_js_environment()

    def _load_js_environment(self):
        """
        加载并配置 JS 运行环境 (含浏览器环境模拟)
        完全复刻 main2.py 的逻辑
        """
        if not os.path.exists(JS_FILE_PATH):
            print(f"❌ 严重错误: 找不到 {JS_FILE_PATH} 文件，无法进行签名！")
            return None

        # 检查 Node.js
        node_path = shutil.which("node") or shutil.which("nodejs")
        if not node_path:
            print("❌ 严重错误: 系统未检测到 Node.js 环境！")
            return None

        # 显式设置 node 路径
        os.environ["EXECJS_COMMAND"] = node_path

        try:
            with open(JS_FILE_PATH, 'r', encoding='utf-8') as f:
                raw_js = f.read()

            # 1. 移除 null 定义 (Patch) - 关键步骤，防止 Mock 被覆盖
            cleaned_js = raw_js.replace("var window = null;", "// var window = null; (Patch)")
            cleaned_js = cleaned_js.replace("global.window = null;", "// global.window = null;")

            # 2. 注入浏览器环境 (Mock) - 使用 STREAM_USER_AGENT 常量
            browser_mock = """
            global.window = {
                params: {},
                addEventListener: function() {},
                navigator: {
                    userAgent: "%s",
                    appCodeName: "Mozilla",
                    appName: "Netscape",
                    platform: "Win32"
                },
                document: {
                    referrer: "https://live.douyin.com/",
                    cookie: "",
                    getElementById: function() { return null; },
                    addEventListener: function() {}
                },
                location: {
                    href: "https://live.douyin.com/",
                    protocol: "https:",
                    hostname: "live.douyin.com"
                },
                screen: { width: 1920, height: 1080 }
            };
            global.document = global.window.document;
            global.navigator = global.window.navigator;
            global.location = global.window.location;
            """ % STREAM_USER_AGENT

            # 3. 注入适配器 - 使用 verified working 的版本
            adapter_code = """
            ;
            function generate_signature(params_string) {
                try {
                    if (typeof window !== 'undefined' && window.byted_acrawler && window.byted_acrawler.sign) {
                        return window.byted_acrawler.sign({url: params_string});
                    }
                    if (typeof global !== 'undefined' && global.byted_acrawler && global.byted_acrawler.sign) {
                        return global.byted_acrawler.sign({url: params_string});
                    }
                    if (typeof sign === 'function') {
                        return sign(params_string, "%s");
                    }
                } catch (e) {
                    return "err: " + e.toString();
                }
                return "";
            }
            """

            full_code = browser_mock + "\n" + cleaned_js + "\n" + adapter_code
            return execjs.compile(full_code)

        except Exception as e:
            print(f"❌ JS 编译失败: {e}")
            return None

    def get_ttwid(self):
        """获取 ttwid，这对签名至关重要"""
        try:
            # 优先检查 session 是否已有 ttwid (可能是从 rooms.json 注入的)
            if "ttwid" in self.session.cookies:
                return self.session.cookies.get("ttwid")

            # 否则发起请求获取 fresh one
            self.session.get("https://live.douyin.com/", timeout=10)
            ttwid = self.session.cookies.get("ttwid")
            if ttwid:
                print(f"✅ 获取到新 ttwid: {ttwid[:10]}...")
            return ttwid
        except:
            return None

    def get_room_id(self):
        """获取真实 Room ID (处理重定向)"""
        try:
            # 兼容：如果已经是纯数字，且看起来像 room_id，直接使用
            print(f"🔄 正在解析 Room ID: {self.room_id}")

            # 使用 session 请求，带上正确的 UA
            response = self.session.get(self.room_url, allow_redirects=True, timeout=10)
            final_url = response.url

            web_rid_match = re.search(r'live\.douyin\.com/(\d+)', final_url)
            if not web_rid_match:
                web_rid_match = re.search(r'reflow/(\d+)', final_url)

            if not web_rid_match:
                path_match = re.search(r'/(\d+)', final_url.split('?')[0])
                web_rid = path_match.group(1) if path_match else self.room_id
            else:
                web_rid = web_rid_match.group(1)

            # 尝试从页面内容提取 roomId
            patterns = [
                r'\\"roomId\\":\\"(\d+)\\"',
                r'"roomId"\s*:\s*"(\d+)"',
                r'room_id=(\d+)',
                r'data-room-id="(\d+)"'
            ]

            real_room_id = web_rid  # 默认回退
            for pattern in patterns:
                match = re.search(pattern, response.text)
                if match:
                    real_room_id = match.group(1)
                    break

            return real_room_id, web_rid

        except Exception as e:
            print(f"⚠️ 解析 ID 失败: {e}，将尝试使用原 ID")
            return self.room_id, self.room_id

    def _extract_url_from_data(self, stream_data):
        """辅助函数：健壮地提取流地址，兼容 Dict/List/Str"""
        if not stream_data: return None

        # 1. 优先尝试 FLV
        flv_data = stream_data.get('flv_pull_url')
        if flv_data:
            if isinstance(flv_data, dict):
                return flv_data.get('FULL_HD1') or flv_data.get('HD1') or flv_data.get('SD1') or \
                    list(flv_data.values())[0]
            elif isinstance(flv_data, list) and len(flv_data) > 0:
                return flv_data[0]
            elif isinstance(flv_data, str):
                return flv_data

        # 2. 其次尝试 HLS
        hls_data = stream_data.get('hls_pull_url_map') or stream_data.get('hls_pull_url')
        if hls_data:
            if isinstance(hls_data, dict):
                return hls_data.get('FULL_HD1') or hls_data.get('HD1') or hls_data.get('SD1') or \
                    list(hls_data.values())[0]
            elif isinstance(hls_data, list) and len(hls_data) > 0:
                return hls_data[0]
            elif isinstance(hls_data, str):
                return hls_data
        return None

    def get_flv_url(self):
        """执行完整的获取流程"""
        # 1. 预备环境
        self.get_ttwid()
        real_room_id, web_rid = self.get_room_id()

        if not real_room_id:
            print("❌ 无法获取有效的 Room ID")
            return None

        # 2. 构造参数
        params = {
            "aid": "6383",
            "app_name": "douyin_web",
            "device_platform": "web",
            "browser_language": "zh-CN",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_online": "true",
            "room_id": real_room_id,
            "web_rid": web_rid,
        }

        query_string = urlencode(params)

        # 3. 计算签名
        x_bogus = ""
        if self.js_ctx:
            print("⏳ 正在计算 X-Bogus 签名...")
            try:
                x_bogus = self.js_ctx.call("generate_signature", query_string)
                print(f"✅ 生成签名: {x_bogus}")
            except Exception as e:
                print(f"❌ 签名生成异常: {e}")
        else:
            print("⚠️ JS 环境未就绪，尝试无签名请求（极可能失败）")

        if x_bogus and "err" not in x_bogus:
            params['X-Bogus'] = x_bogus

        # 4. 请求 API
        api_url = "https://live.douyin.com/webcast/room/web/enter/"
        try:
            # 这里的 self.session 已经使用了正确的 UA
            resp = self.session.get(api_url, params=params, timeout=5)

            # 调试：检查是否返回了 HTML 报错页面
            if not resp.text.strip().startswith("{"):
                print(f"❌ API 返回了非 JSON 数据 (可能是 403/验证码): {resp.text[:100]}...")
                return None

            data = resp.json()

            # 深入两层获取数据 (兼容性处理)
            room_payload = data.get('data', {}).get('data')
            if isinstance(room_payload, list) and len(room_payload) > 0:
                room_payload = room_payload[0]
            elif isinstance(data.get('data'), dict):
                room_payload = data.get('data')

            if not room_payload:
                print(f"❌ API 返回空数据: {data}")
                return None

            stream_data = room_payload.get('stream_url')
            if not stream_data:
                status = room_payload.get('status')
                print(f"❌ 直播间可能未开播 (Status: {status})")
                return None

            # 5. 提取最终地址
            target_url = self._extract_url_from_data(stream_data)
            if target_url:
                # 强制替换 https 以避免 SSL 握手问题
                return target_url.replace("https://", "http://")
            return None

        except Exception as e:
            print(f"❌ [解析流] 请求异常: {e}")
            return None


# ================= 业务逻辑：弹幕监控 (保持原样) =================

class RoomManager:
    def __init__(self):
        self.rooms = {}
        self.lock = threading.Lock()
        self.config_file = ROOMS_CONFIG_FILE
        self.load_rooms()

    def load_rooms(self):
        if not os.path.exists(self.config_file): return
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
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

            # 确保目录存在
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
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
                    # 如果已存在，更新配置（允许更新 Cookie）
                    self.rooms[room_id]["config"] = config
                    self.rooms[room_id]["name"] = anchor_name
                    self.save_rooms()
                    return True, f"直播间 {room_id} 配置已更新"

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

    def get_room_config(self, room_id):
        """辅助函数：获取房间配置（用于流解析提取 Cookie）"""
        with self.lock:
            if room_id in self.rooms:
                return self.rooms[room_id].get("config")
        return None

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
