# !/usr/bin/python3
# -*- coding:utf-8 -*-
"""
@author: JHC000abc@gmail.com
@file: utils_zb.py
@time: 2026/1/23 14:20 
@desc: 

"""
import requests
import re
import os
import shutil
from urllib.parse import urlencode

os.environ["EXECJS_RUNTIME"] = "Node"

try:
    import execjs
    import execjs.runtime_names
except ImportError:
    print("❌ 错误: 未安装 PyExecJS 库。请运行: pip install PyExecJS")
    pass

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
JS_FILE_PATH = "src/js/crypto.js"  # 默认值



class DouyinRecorder:
    def __init__(self, room_url):
        self.room_url = room_url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": "https://live.douyin.com/"
        })
        self.js_ctx = self._load_js_environment()

    def _load_js_environment(self):
        """
        加载并配置 JS 运行环境 (含浏览器环境模拟)
        """
        if not os.path.exists(JS_FILE_PATH):
            print(f"❌ 错误: 找不到 {JS_FILE_PATH} 文件。")
            return None

        # 检查 Node.js
        node_path = shutil.which("node") or shutil.which("nodejs")
        if not node_path:
            print("❌ 严重错误: 系统未检测到 Node.js 环境！")
            return None

        os.environ["EXECJS_COMMAND"] = node_path

        # 验证 Runtime
        try:
            runtime = execjs.get()
            if "Node" not in runtime.name and "V8" not in runtime.name:
                try:
                    runtime = execjs.get(execjs.runtime_names.Node)
                except:
                    pass
        except Exception as e:
            print(f"⚠️ 环境初始化警告: {e}")

        # --- 核心修改：构建带有浏览器模拟的 JS 代码 ---
        try:
            with open(JS_FILE_PATH, 'r', encoding='utf-8') as f:
                raw_js = f.read()

            # 1. 移除 null 定义
            cleaned_js = raw_js.replace("var window = null;", "// var window = null; (Patch)")
            cleaned_js = cleaned_js.replace("global.window = null;", "// global.window = null;")

            # 2. 注入浏览器环境 (Mock)
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
            """ % USER_AGENT

            # 3. 注入适配器
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
            """ % USER_AGENT

            full_code = browser_mock + "\n" + cleaned_js + "\n" + adapter_code
            return execjs.compile(full_code)

        except Exception as e:
            print(f"❌ JS 编译失败: {e}")
            return None

    def get_ttwid(self):
        try:
            self.session.get("https://live.douyin.com/", timeout=10)
            ttwid = self.session.cookies.get("ttwid")
            if ttwid:
                print(f"✅ 获取到 ttwid: {ttwid[:10]}...")
            return ttwid
        except:
            return None

    def get_room_id(self):
        try:
            print(f"正在解析页面: {self.room_url}")
            headers = {"User-Agent": USER_AGENT}
            self.get_ttwid()
            response = self.session.get(self.room_url, headers=headers, allow_redirects=True, timeout=10)
            final_url = response.url

            web_rid_match = re.search(r'live\.douyin\.com/(\d+)', final_url)
            if not web_rid_match:
                web_rid_match = re.search(r'reflow/(\d+)', final_url)

            if not web_rid_match:
                path_match = re.search(r'/(\d+)', final_url.split('?')[0])
                if path_match:
                    web_rid = path_match.group(1)
                else:
                    # 如果URL本身就是数字结尾，尝试直接用
                    if self.room_url.split('/')[-1].isdigit():
                        web_rid = self.room_url.split('/')[-1]
                    else:
                        raise ValueError("无法解析 web_rid")
            else:
                web_rid = web_rid_match.group(1)

            patterns = [
                r'\\"roomId\\":\\"(\d+)\\"',
                r'"roomId"\s*:\s*"(\d+)"',
                r'room_id=(\d+)',
                r'data-room-id="(\d+)"'
            ]

            for pattern in patterns:
                match = re.search(pattern, response.text)
                if match:
                    return match.group(1), web_rid

            print("⚠️ 未找到真实 room_id，使用 web_rid")
            return web_rid, web_rid

        except Exception as e:
            print(f"❌ 解析房间 ID 错误: {e}")
            return None, None

    def _extract_url_from_data(self, stream_data):
        """
        辅助函数：健壮地提取流地址，兼容 Dict/List/Str
        """
        if not stream_data:
            return None

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


    def get_room_status(self):
        self.get_ttwid()
        real_room_id, web_rid = self.get_room_id()
        if not real_room_id:
            return None


        api_url = "https://live.douyin.com/webcast/room/web/enter/"
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

        resp = self.session.get(api_url, params=params, timeout=5)
        print(resp.text)
        try:
            data = resp.json()
            print(data["data"]["room_status"])
        except:
            print(f"❌ API 返回非 JSON 数据: {resp.text[:50]}")
            return None

        if not data.get("data"):
            print("❌ API 返回 data 为空 (可能是 Cookie 过期)")
            return None




    def get_stream_url(self):
        if not self.js_ctx:
            print("❌ JS 环境未就绪，无法进行签名")
            return None

        self.get_ttwid()
        real_room_id, web_rid = self.get_room_id()
        if not real_room_id:
            return None

        print(f"获取到 Room ID: {real_room_id}")

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

        print("⏳ 正在计算 X-Bogus 签名...")
        try:
            x_bogus = self.js_ctx.call("generate_signature", query_string)
        except Exception as e:
            print(f"❌ 调用 JS 函数失败: {e}")
            return None

        if not x_bogus or "err" in x_bogus or len(x_bogus) < 10:
            print(f"❌ 签名生成失败: {x_bogus}")
            # return None # 即使签名失败也尝试请求，有时候能行
        else:
            params['X-Bogus'] = x_bogus
            print(f"✅ 生成签名: {x_bogus}")

        api_url = "https://live.douyin.com/webcast/room/web/enter/"
        try:
            resp = self.session.get(api_url, params=params, timeout=5)
            # print(resp.json()) # 调试时可开启
            try:
                data = resp.json()
            except:
                print(f"❌ API 返回非 JSON 数据: {resp.text[:50]}")
                return None

            if not data.get("data"):
                print("❌ API 返回 data 为空 (可能是 Cookie 过期)")
                return None

            # 深入两层获取数据
            room_payload = data['data'].get('data')
            if not room_payload:
                # 有时候 data 下面直接是字段，有时候是 data.data 列表
                if isinstance(data['data'], dict):
                    room_payload = [data['data']]  # 统一为列表处理
                else:
                    print("❌ API 结构变更: data.data 不存在")
                    return None

            # 这里的 room_payload 应该是一个列表
            if isinstance(room_payload, list) and len(room_payload) > 0:
                room_payload = room_payload[0]

            stream_data = room_payload.get('stream_url')
            if not stream_data:
                status = room_payload.get('status')
                print(f"❌ 直播间未开播 (状态码: {status})")
                return None

            # 使用提取函数
            target_url = self._extract_url_from_data(stream_data)

            if not target_url:
                print("❌ 解析失败: 未在 stream_data 中找到有效的 flv 或 hls 地址")
                return None

            # 强制 http 避免 SSL 问题
            target_url = target_url.replace("https://", "http://")
            return target_url

        except Exception as e:
            print(f"❌ 请求异常: {e}")
            return None

    def record(self):
        url = self.get_stream_url()
        if not url:
            print("❌ 解析失败，无法获取流地址")
            return None

        print(f"✅ 解析成功: {url}")
        return url  # 修改：返回 URL 以便 API 调用