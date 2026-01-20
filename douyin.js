// ==UserScript==
// @name         抖音直播配置提取器 (v3.4 接口劫持版)
// @namespace    http://tampermonkey.net/
// @version      3.4.0
// @description  [专用] 劫持 fansclub 接口获取精准主播名，自动打包直播间链接
// @author       JHC & Gemini
// @match        https://live.douyin.com/*
// @grant        GM_setClipboard
// @grant        GM_addStyle
// @run-at       document-start
// ==/UserScript==

(function() {
    'use strict';

    let capturedConfig = null;
    let wsCaptured = false;
    let anchorInfo = { name: null, avatar: null }; // 存储接口抓取到的信息
    const realWin = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;

    // UI 样式
    GM_addStyle(`
        #dy-ws-extractor {
            position: fixed; top: 100px; left: 20px; z-index: 2147483647;
            background: rgba(30, 30, 30, 0.95); color: #fff; padding: 16px;
            border-radius: 12px; width: 260px; font-family: -apple-system, system-ui, sans-serif;
            border: 1px solid rgba(255, 255, 255, 0.15);
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            backdrop-filter: blur(8px); transition: 0.3s;
        }
        .dy-header { font-size: 14px; font-weight: 800; color: #fff; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 8px; }
        .dy-info-item { font-size: 13px; color: #b0b0b0; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
        .dy-info-item strong { color: #fff; font-weight: 500; min-width: 36px; }
        .dy-info-val { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; color: #a29bfe; font-weight: 700; }
        #dy-ws-status { font-size: 12px; margin: 12px 0; padding: 6px 10px; background: rgba(255,255,255,0.05); border-radius: 6px; color: #ff9f43; display: flex; align-items: center; gap: 6px; }
        #dy-copy-btn {
            width: 100%; padding: 10px; border: none; border-radius: 8px;
            background: #6c5ce7; color: white; cursor: pointer; font-weight: 600; font-size: 13px;
            transition: all 0.2s; opacity: 0.5; pointer-events: none;
        }
        #dy-copy-btn.ready { opacity: 1; pointer-events: auto; background: #00b894; box-shadow: 0 4px 12px rgba(0, 184, 148, 0.3); }
        #dy-copy-btn.ready:hover { transform: translateY(-1px); filter: brightness(1.1); }
        #dy-copy-btn:active { transform: scale(0.98); }
    `);

    // --- 核心 1: 劫持 XHR 获取 fansclub 接口数据 ---
    const OriginalXHR = realWin.XMLHttpRequest;
    realWin.XMLHttpRequest = class extends OriginalXHR {
        constructor() {
            super();
        }
        open(method, url) {
            this._url = url;
            return super.open(method, url);
        }
        send(body) {
            this.addEventListener('load', () => {
                try {
                    if (this._url && this._url.includes('webcast/fansclub/homepage')) {
                        const res = JSON.parse(this.responseText);
                        if (res && res.data && res.data.club_info) {
                            const info = res.data.club_info;
                            if (info.anchor_name) {
                                console.log('🔥 [Hook] 成功拦截 fansclub 接口，获取主播:', info.anchor_name);
                                anchorInfo.name = info.anchor_name;
                                anchorInfo.avatar = info.anchor_avatar_url;
                                updateUIInfo(); // 立即更新 UI
                            }
                        }
                    }
                } catch (e) {
                    // console.error('解析 fansclub 失败', e);
                }
            });
            return super.send(body);
        }
    };

    // --- 核心 2: 兜底获取策略 (RENDER_DATA) ---
    function getFallbackMeta() {
        let nick = null;
        let title = null;
        try {
            // 1. RENDER_DATA
            const script = document.getElementById('RENDER_DATA');
            if (script && script.textContent) {
                const data = JSON.parse(decodeURIComponent(script.textContent));
                const roomInfo = data?.app?.initialState?.roomStore?.roomInfo?.room || data?.initialState?.roomStore?.roomInfo?.room;
                if (roomInfo) {
                    nick = roomInfo.owner?.nickname;
                    title = roomInfo.title;
                }
            }
            // 2. DOM (最后防线)
            if (!nick) {
                const el = document.querySelector('[data-e2e="live-author-nickname"]') || document.querySelector('h1');
                if (el) nick = el.innerText.trim();
            }
            if (!title) {
                const el = document.querySelector('[data-e2e="live-title"]');
                if (el) title = el.innerText.trim();
            }
        } catch(e) {}

        return { name: nick, title: title };
    }

    function initUI() {
        if (document.getElementById('dy-ws-extractor')) return;

        const div = document.createElement('div');
        div.id = 'dy-ws-extractor';
        div.innerHTML = `
            <div class="dy-header">📡 直播捕获 V3.4</div>
            <div class="dy-info-item">
                <strong>主播</strong>
                <span class="dy-info-val" id="dy-show-name">等待获取...</span>
            </div>
            <div class="dy-info-item">
                <strong>标题</strong>
                <span class="dy-info-val" id="dy-show-title">...</span>
            </div>
            <div id="dy-ws-status">⏳ 等待 WebSocket...</div>
            <button id="dy-copy-btn">📋 复制完整配置</button>
        `;
        document.body.appendChild(div);

        document.getElementById('dy-copy-btn').onclick = () => {
            if (!capturedConfig) return;

            // 最终合并信息：接口数据 > RENDER_DATA > DOM
            const fallback = getFallbackMeta();
            capturedConfig.name = anchorInfo.name || fallback.name || "未知主播";
            capturedConfig.title = fallback.title || "无标题";

            // ⭐️ 新增：加入页面 URL，供前端“直达”按钮使用
            capturedConfig.page_url = window.location.href;

            // 复制
            GM_setClipboard(JSON.stringify(capturedConfig));

            const btn = document.getElementById('dy-copy-btn');
            const originalText = btn.innerText;
            btn.innerText = "✅ 已复制！";
            btn.style.background = "#fdcb6e";
            setTimeout(() => {
                btn.innerText = originalText;
                btn.style.background = "#00b894";
            }, 2000);
        };
    }

    function updateUIInfo() {
        const nameEl = document.getElementById('dy-show-name');
        if (nameEl && anchorInfo.name) {
            nameEl.innerText = anchorInfo.name;
            nameEl.style.color = "#55efc4"; // 亮绿色表示已从接口获取
        }
    }

    function updateStatus(isSuccess) {
        const btn = document.getElementById('dy-copy-btn');
        const status = document.getElementById('dy-ws-status');
        const titleEl = document.getElementById('dy-show-title');

        if (isSuccess && btn && status) {
            const meta = getFallbackMeta();
            // 如果接口还没抓到名字，先显示兜底名字
            if (!anchorInfo.name) document.getElementById('dy-show-name').innerText = meta.name || "获取中...";
            if (titleEl) titleEl.innerText = meta.title || "";

            btn.classList.add('ready');
            status.innerHTML = "✅ 信号已锁定";
            status.style.color = "#55efc4";
            status.style.background = "rgba(0, 184, 148, 0.1)";
        }
    }

    // --- WebSocket 劫持 ---
    const OriginalWebSocket = realWin.WebSocket;
    realWin.WebSocket = class extends OriginalWebSocket {
        constructor(url, protocols) {
            super(url, protocols);
            if (url && typeof url === 'string' && url.includes('webcast') && url.includes('wss://')) {
                if (!wsCaptured) {
                    console.log('🔥 [Hook] 成功捕获 WebSocket');
                    wsCaptured = true;
                    setTimeout(() => {
                        initUI();
                        capturedConfig = {
                            url: url,
                            headers: {
                                "User-Agent": navigator.userAgent,
                                "Cookie": document.cookie
                            }
                        };
                        updateStatus(true);
                    }, 1000);
                }
            }
        }
    };

    window.addEventListener('load', () => setTimeout(initUI, 1500));

})();