# !/usr/bin/python3
# -*- coding:utf-8 -*-
"""
@author: JHC000abc@gmail.com
@file: utils_dm.py
@time: 2026/1/24 15:30
@desc: 包含流地址解析、直播间信息查询及 WebSocket 弹幕监控逻辑 (已优化复用 utils_zb)
"""
import threading
import websocket
import gzip
import time
import urllib.request
import requests
import re
import json
import os
from datetime import datetime
from models.models_dm import LiveDanmakuModel
from utils.utils_db import *
from utils.utils_zb import DouyinRecorder  # 引入 utils_zb 进行复用

# ⚠️ 确保 dy_pb2.py 在同一目录下
try:
    from plugins import dy_pb2
except ImportError:
    print("❌ 错误: 未找到 dy_pb2.py，请确保该文件在同一目录下。")

ROOMS_CONFIG_FILE = "config/rooms.json"


class DouyinStreamFetcher:
    """
    基于 utils_zb.DouyinRecorder 的封装类
    用于适配 utils_dm 原有的业务逻辑，同时复用底层解析能力
    """

    def __init__(self, page_url, custom_headers=None):
        self.room_url = page_url
        # 核心：直接实例化 Recorder，复用其 JS 环境和 Session 管理
        self.recorder = DouyinRecorder(self.room_url)

        # 注入用户配置的 Headers (如 Cookie)，同步给 recorder 的 session
        if custom_headers and isinstance(custom_headers, dict):
            safe_headers = {
                k: v for k, v in custom_headers.items()
                if k.lower() not in ['host', 'user-agent']
            }
            self.recorder.session.headers.update(safe_headers)

    def get_flv_url(self):
        """
        获取直播流地址
        直接调用 DouyinRecorder 的逻辑 (含签名生成)
        """
        return self.recorder.get_stream_url()

    def get_room_info(self):
        """
        获取直播间详细状态状态 (在线人数、标题等)
        此逻辑保留在 dm 中，因为 zb 主要关注流地址
        """
        # 1. 复用 recorder 获取 ttwid
        ttwid = self.recorder.get_ttwid()

        # 2. 复用 recorder 获取真实 ID (含重定向处理)
        real_room_id, web_rid = self.recorder.get_room_id()
        print(real_room_id, web_rid)
        if not real_room_id:
            return {"error": "无法解析 Room ID", "status": -1}

        # 3. 构造请求参数 (沿用 recorder 的 session)
        url = "https://live.douyin.com/webcast/room/web/enter/"

        params = {
            "aid": "6383",
            "device_platform": "web",
            "browser_language": "zh-CN",
            "browser_platform": "Linux x86_64",
            "browser_name": "Chrome",
            "browser_version": "142.0.0.0",
            "web_rid": web_rid,
            "room_id_str": real_room_id,
        }
        cookies = {"ttwid": ttwid}
        headers = {"headers": self.recorder.session.headers.get("User-Agent")}
        try:
            # 使用 recorder 的 session 发送请求，确保 Cookie/Header 一致
            response = requests.get(url, params=params, headers=headers, cookies=cookies, timeout=5)
            try:
                json_data = response.json()
            except json.JSONDecodeError:
                return {"error": "API 返回非 JSON 数据", "raw": response.text[:100], "status": -1}

            # 解析返回数据
            data_wrapper = json_data.get("data", {})
            data_list = data_wrapper.get("data", [])

            result = {
                "room_id": real_room_id,
                "web_rid": web_rid
            }

            if data_list and len(data_list) > 0:
                room_info = data_list[0]
                user_info = data_wrapper.get("user", {})

                result.update({
                    "status": room_info.get("status"),  # 2=直播中, 4=关播/结束
                    "status_str": room_info.get("status_str"),
                    "title": room_info.get("title"),
                    "user_count": room_info.get("user_count_str"),
                    "owner_nickname": user_info.get("nickname"),
                    "owner_avatar": user_info.get("avatar_thumb", {}).get("url_list", [""])[0],
                    "live_room_mode": room_info.get("live_room_mode"),
                })
            else:
                result["status"] = 4
                result["msg"] = "未找到直播间数据"

            return result

        except Exception as e:
            return {"error": f"请求异常: {str(e)}", "status": -1}


# ================= 业务逻辑：弹幕监控 =================

class RoomManager:
    def __init__(self):
        self.rooms = {}
        self.lock = threading.Lock()
        self.config_file = ROOMS_CONFIG_FILE
        self.load_rooms()

    def load_rooms(self):
        if not os.path.exists(self.config_file): return
        try:
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
        with self.lock:
            if room_id in self.rooms:
                return self.rooms[room_id].get("config")
        return None

    def _log(self, room_id, user, msg):
        if room_id in self.rooms:
            log_entry = f"[{datetime.now().strftime('%H:%M:%S')}] {user}: {msg}"
            self.rooms[room_id]["logs"].append(log_entry)
            if len(self.rooms[room_id]["logs"]) > 50:
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

                # === 检查直播间状态 (复用 Fetcher -> Recorder) ===
                try:
                    # 这里的 Fetcher 现在内部使用 DouyinRecorder

                    fetcher = DouyinStreamFetcher(self.rooms[room_id]["page_url"], config.get('headers'))
                    room_info = fetcher.get_room_info()

                    current_status = room_info.get("status")
                    if current_status == 4:
                        self._log(room_id, "系统", "直播已结束，停止监控")
                        self.stop_room(room_id)
                        break
                    elif current_status == 2:
                        pass  # 正常直播中
                    elif current_status != -1:
                        self._log(room_id, "系统", f"直播间状态代码: {current_status}，尝试连接...")
                except Exception as e:
                    self._log(room_id, "系统", f"状态检测失败: {e}，继续尝试...")
                # ===================================

                ws = websocket.WebSocketApp(
                    ws_url, header=header_list, cookie=cookie_str,
                    on_open=on_open, on_message=on_message,
                    on_error=on_error, on_close=on_close
                )

                with self.lock:
                    if room_id in self.rooms:
                        self.rooms[room_id]["ws"] = ws

                ws.run_forever(ping_interval=10, ping_timeout=5)

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
        """
        解析单条消息，全量解析并保存
        """
        try:
            data = None
            log_priority = "normal"  # normal, high, low

            # 1. 聊天消息
            if method == 'WebcastChatMessage':
                msg = dy_pb2.ChatMessage()
                msg.ParseFromString(payload)
                data = {
                    "msg_type": "chat",
                    "content": msg.content,
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id)
                }

            # 2. 成员进入消息
            elif method == 'WebcastMemberMessage':
                msg = dy_pb2.MemberMessage()
                msg.ParseFromString(payload)
                data = {
                    "msg_type": "member",
                    "content": "进入直播间",
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id)
                }

            # 3. 礼物消息
            elif method == 'WebcastGiftMessage':
                msg = dy_pb2.GiftMessage()
                msg.ParseFromString(payload)
                count = msg.comboCount or msg.groupCount or msg.repeatCount or 1
                data = {
                    "msg_type": "gift",
                    "content": f"送礼物 {msg.giftId} x{count}",
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id),
                    "gift_id": str(msg.giftId),
                    "gift_count": count
                }
                log_priority = "high"

            # 4. 点赞消息
            elif method == 'WebcastLikeMessage':
                msg = dy_pb2.LikeMessage()
                msg.ParseFromString(payload)
                data = {
                    "msg_type": "like",
                    "content": f"点赞 x{msg.count}",
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id),
                    "gift_count": msg.count
                }

            # 5. 社交消息 (关注/分享)
            elif method == 'WebcastSocialMessage':
                msg = dy_pb2.SocialMessage()
                msg.ParseFromString(payload)
                action_text = "关注了主播" if msg.action == 1 else "分享了直播间"
                data = {
                    "msg_type": "social",
                    "content": action_text,
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id)
                }

            # 6. 直播间统计消息 (在线人数/榜单)
            elif method == 'WebcastRoomUserSeqMessage':
                msg = dy_pb2.RoomUserSeqMessage()
                msg.ParseFromString(payload)
                online = msg.totalUserStr or str(msg.totalUser)
                total_pv = msg.totalPvForAnchor or str(msg.total)
                content = f"当前在线: {online}, 累计观看: {total_pv}"
                data = {
                    "msg_type": "stats",
                    "content": content,
                    "user_nick": "系统",
                    "user_uid": "0"
                }
                log_priority = "low"

            # 7. 粉丝票/音浪更新消息
            elif method == 'WebcastUpdateFanTicketMessage':
                msg = dy_pb2.UpdateFanTicketMessage()
                msg.ParseFromString(payload)
                data = {
                    "msg_type": "heat",
                    "content": f"当前音浪: {msg.roomFanTicketCountText}",
                    "user_nick": "系统",
                    "user_uid": "0"
                }
                log_priority = "low"

            # 8. 直播间控制消息 (下播/暂停)
            elif method == 'WebcastControlMessage':
                msg = dy_pb2.ControlMessage()
                msg.ParseFromString(payload)
                status_map = {1: "直播结束", 3: "直播暂停"}
                status_text = status_map.get(msg.action, f"状态变更:{msg.action}")
                data = {
                    "msg_type": "control",
                    "content": status_text,
                    "user_nick": "系统",
                    "user_uid": "0"
                }
                log_priority = "high"

            # 9. 粉丝团消息
            elif method == 'WebcastFansClubMessage':
                msg = dy_pb2.FansClubMessage()
                msg.ParseFromString(payload)
                data = {
                    "msg_type": "fans_club",
                    "content": msg.content,
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id)
                }

            # 10. 表情消息
            elif method == 'WebcastEmojiChatMessage':
                msg = dy_pb2.EmojiChatMessage()
                msg.ParseFromString(payload)
                data = {
                    "msg_type": "emoji",
                    "content": msg.defaultContent,
                    "user_nick": msg.user.nickName,
                    "user_uid": str(msg.user.id)
                }

            # 11. 榜单消息 (新增)
            elif method == 'WebcastRoomRankMessage':
                msg = dy_pb2.RoomRankMessage()
                msg.ParseFromString(payload)
                rank_str = ""
                if msg.ranksList:
                    top_user = msg.ranksList[0].user.nickName
                    rank_str = f"榜一更新: {top_user}"
                data = {
                    "msg_type": "rank",
                    "content": rank_str or "榜单更新",
                    "user_nick": "系统",
                    "user_uid": "0"
                }
                log_priority = "low"

            # 12. 房间横幅/Banner (新增)
            elif method == 'WebcastInRoomBannerMessage':
                msg = dy_pb2.InRoomBannerMessage()
                msg.ParseFromString(payload)
                # 解析 extra JSON 数据
                try:
                    extra_dict = json.loads(msg.extra) if msg.extra else {}
                    banner_title = extra_dict.get("title", "横幅消息")
                except:
                    banner_title = "横幅消息"

                data = {
                    "msg_type": "banner",
                    "content": banner_title,
                    "user_nick": "系统",
                    "user_uid": "0"
                }
                log_priority = "low"

            # 保存入库并输出日志
            if data:
                data.update({"room_id": room_id, "display_id": "", "gender": "", "avatar_url": ""})
                self._save_db(data)

                # 图标映射
                icon_map = {
                    'gift': '🎁', 'member': '🚪', 'like': '❤️', 'chat': '💬',
                    'social': '➕', 'stats': '📊', 'heat': '🔥', 'control': '🛑',
                    'fans_club': '🌟', 'emoji': '😎', 'rank': '🏆', 'banner': '🎏'
                }

                self._log(room_id, icon_map.get(data['msg_type'], '*'), f"{data['user_nick']}: {data['content']}")

        except Exception as e:
            # print(f"Parse Error for {method}: {e}")
            pass

    def _save_db(self, data):
        if not SessionLocal:
            return
        session = SessionLocal()
        try:
            log = LiveDanmakuModel(**data)
            session.add(log)
            session.commit()
        except Exception as e:
            print(f"DB Error: {e}")
        finally:
            session.close()
