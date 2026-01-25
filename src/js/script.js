const API_URL = "";
// 默认刷新频率 (从本地存储读取，默认 2000ms)
let refreshInterval = localStorage.getItem('refreshRate') ? parseInt(localStorage.getItem('refreshRate')) : 2000;
let refreshTimer = null;

// 暗黑模式初始化
const toggle = document.getElementById('darkModeToggle');
if (localStorage.getItem('darkMode') === 'enabled') {
    document.body.classList.add('dark-mode');
    toggle.checked = true;
}
toggle.addEventListener('change', () => {
    if (toggle.checked) {
        document.body.classList.add('dark-mode');
        localStorage.setItem('darkMode', 'enabled');
    } else {
        document.body.classList.remove('dark-mode');
        localStorage.setItem('darkMode', 'disabled');
    }
});

// === 弹窗管理 ===
const modal = document.getElementById('settingsModal');

async function openSettingsModal() {
    modal.classList.add('open');
    // 回显刷新频率
    document.getElementById('refreshRate').value = refreshInterval;

    // 加载数据库配置
    try {
        const res = await fetch(API_URL + "/api/db/config");
        const config = await res.json();
        document.getElementById('dbHost').value = config.host;
        document.getElementById('dbPort').value = config.port;
        document.getElementById('dbUser').value = config.user;
        document.getElementById('dbPass').value = config.password;
        document.getElementById('dbName').value = config.database;
    } catch (e) {
    }
}

function closeSettingsModal() {
    modal.classList.remove('open');
}

// === 设置保存 ===
async function saveAllSettings() {
    // 1. 保存刷新频率
    const newRate = parseInt(document.getElementById('refreshRate').value);
    if (newRate && newRate >= 500) {
        refreshInterval = newRate;
        localStorage.setItem('refreshRate', refreshInterval);
        restartTimer(); // 重启定时器
    } else {
        alert("刷新频率建议不小于 500ms");
        return;
    }

    // 2. 保存数据库配置
    try {
        const res = await fetch(API_URL + "/api/db/save", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(getDBForm())
        });
        const r = await res.json();
        if (r.success) {
            alert("✅ 所有配置保存成功！");
            closeSettingsModal();
        } else {
            alert("⚠️ 数据库连接失败：" + r.msg);
        }
    } catch (e) {
        alert("保存请求失败");
    }
}

function getDBForm() {
    return {
        host: document.getElementById('dbHost').value,
        port: document.getElementById('dbPort').value,
        user: document.getElementById('dbUser').value,
        password: document.getElementById('dbPass').value,
        database: document.getElementById('dbName').value
    };
}

async function testDBConnection() {
    try {
        const res = await fetch(API_URL + "/api/db/test", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(getDBForm())
        });
        const r = await res.json();
        alert(r.msg);
    } catch (e) {
        alert("测试请求失败");
    }
}

// === 定时器管理 ===
function restartTimer() {
    if (refreshTimer) clearInterval(refreshTimer);
    console.log(`⏱️ 刷新频率更新为: ${refreshInterval}ms`);
    refreshTimer = setInterval(refreshList, refreshInterval);
}

// === 卡片渲染 ===
function createCardHTML(room) {
    // 修改点：
    // 1. 增加 card-content 容器
    // 2. CSV 按钮增加 "CSV" 文字
    return `
        <button class="btn-close-card" onclick="control('${room.room_id}', 'remove')" title="删除卡片">
            <i class="fas fa-times"></i>
        </button>
        <div class="card-content">
            <div class="card-header">
                <div class="info-box">
                    <span class="anchor-name" title="${room.title}">${room.name}</span>
                    <span class="room-id">ID: ${room.room_id}</span>
                </div>
                <div class="status-badge">
                    <span class="status-dot"></span>
                    <span class="status-text">...</span>
                </div>
            </div>
            <div class="log-area" data-last-log="${room.latest_log || ''}"></div>
        </div>
        <div class="card-actions">
            <button class="btn-action btn-start" onclick="control('${room.room_id}', 'start')"><i class="fas fa-play"></i> 启动</button>
            <button class="btn-action btn-stop" onclick="control('${room.room_id}', 'stop')"><i class="fas fa-pause"></i> 暂停</button>
            <button class="btn-download" onclick="downloadData('${room.room_id}')" title="导出CSV"><i class="fas fa-file-csv"></i> CSV</button>            
            <a href="${room.page_url}" target="_blank" class="btn-link" title="跳转直播间"><i class="fas fa-external-link-alt"></i> 直达</a>            
            <button class="btn-copy" data-url="${room.page_url}" title="复制链接"><i class="fas fa-copy"></i> 复制</button>
        </div>
    `;
}

async function refreshList() {
    try {
        const res = await fetch(API_URL + "/api/rooms");
        const data = await res.json();
        document.getElementById('total-count').innerText = `监控中: ${data.length}`;
        const container = document.getElementById('roomList');

        // 标记所有已存在的卡片
        const existingCards = new Set();
        container.querySelectorAll('.card').forEach(el => existingCards.add(el.dataset.roomId));
        const newDataIds = new Set();

        data.forEach(room => {
            newDataIds.add(room.room_id);
            let card = container.querySelector(`.card[data-room-id="${room.room_id}"]`);
            if (card) {
                updateCardUI(card, room);
            } else {
                card = document.createElement('div');
                card.className = 'card';
                card.dataset.roomId = room.room_id;
                card.innerHTML = createCardHTML(room);
                container.appendChild(card);
                updateCardUI(card, room); // 立即更新一次状态
            }
        });

        // 移除多余卡片
        container.querySelectorAll('.card').forEach(el => {
            if (!newDataIds.has(el.dataset.roomId)) el.remove();
        });
    } catch (e) {
        console.error(e);
    }
}

function updateCardUI(card, room) {
    const status = room.status;

    // 清除所有状态类
    card.classList.remove('card-stopped', 'card-ended');
    const badge = card.querySelector('.status-badge');
    const statusText = card.querySelector('.status-text');
    badge.classList.remove('running', 'stopped', 'ended');

    const btnStart = card.querySelector('.btn-start');
    const btnStop = card.querySelector('.btn-stop');

    if (status === 'running') {
        badge.classList.add('running');
        statusText.innerText = '运行中';
        btnStart.style.display = 'none';
        btnStop.style.display = 'inline-flex';
    } else if (status === 'stopped') {
        card.classList.add('card-stopped');
        badge.classList.add('stopped');
        statusText.innerText = '已暂停';
        btnStart.style.display = 'inline-flex';
        btnStop.style.display = 'none';
    } else if (status === 'ended') {
        // 🔴 强制覆盖样式
        card.classList.add('card-ended');
        badge.classList.add('ended');
        statusText.innerText = '直播结束';
        btnStart.style.display = 'inline-flex';
        btnStop.style.display = 'none';
    }

    // 更新日志
    const logArea = card.querySelector('.log-area');
    const lastLog = logArea.dataset.lastLog;
    const newLog = room.latest_log;
    if (newLog && newLog !== lastLog && !newLog.includes('等待数据')) {
        const item = document.createElement('div');
        item.className = 'log-item';
        item.innerText = newLog;
        logArea.appendChild(item);
        logArea.dataset.lastLog = newLog;
        if (logArea.children.length > 50) logArea.removeChild(logArea.firstElementChild);
        logArea.scrollTop = logArea.scrollHeight;
    } else if (!logArea.children.length && newLog) {
        logArea.innerHTML = `<div class="log-item">${newLog}</div>`;
    }
}

// === 修改核心：解析后端返回结果，处理重复添加逻辑 ===
async function addRoom() {
    const input = document.getElementById('configInput');
    const configValue = input.value.trim();
    if (!configValue) return alert("配置内容不能为空");

    try {
        // 1. 接收 fetch 返回的响应对象
        const res = await fetch(API_URL + "/api/add", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({config: configValue})
        });

        // 2. 解析 JSON 结果
        const data = await res.json();

        // 3. 根据后端返回的 success 字段判断
        // 现在由于后端拦截了重复ID，若重复 success 将为 false
        if (data.success) {
            input.value = ""; // 只有成功时才清空输入框
            refreshList();
            showCopyToast(" " + (data.msg || "添加成功"));
        } else {
            // 失败（如重复添加）：弹出 alert 警告，且不清空输入框
            alert("添加失败: " + data.msg);
        }
    } catch (e) {
        console.error(e);
        alert("请求发生异常: " + e.message);
    }
}

async function control(id, act) {
    await fetch(API_URL + `/api/${act}/${id}`, {method: "POST"});
    refreshList();
}

function downloadData(id) {
    window.open(API_URL + '/api/download/' + id);
}

// === 新增：一键启动逻辑 ===
async function startAllRooms() {
    // 1. 获取所有卡片元素
    const cards = document.querySelectorAll('.card');
    if (cards.length === 0) {
        alert("⚠️ 当前列表中没有监控任务！");
        return;
    }

    // 2. 确认操作
    if (!confirm(`确定要一键启动所有任务吗？\n(共 ${cards.length} 个直播间)`)) return;

    // 3. 遍历并启动
    let startCount = 0;
    for (const card of cards) {
        const roomId = card.dataset.roomId;

        // 检查状态：如果是 'running' 状态的就不重复发送请求了，节省资源
        const badge = card.querySelector('.status-badge');
        if (!badge.classList.contains('running')) {
            // 调用原有的 control 函数
            control(roomId, 'start');
            startCount++;

            // ⚠️ 关键：增加一个小延时，防止瞬间发送大量请求导致后端或网络阻塞
            await new Promise(resolve => setTimeout(resolve, 100));
        }
    }

    // 4. 反馈
    if (startCount > 0) {
        // 这里的提示是非阻塞的，稍微延迟一下刷新列表
        setTimeout(() => {
            refreshList();
            // 可以在页面上显示个临时的 toast 提示，这里简单用 log
            console.log(`已发送 ${startCount} 个启动指令`);
        }, 1000);
    } else {
        alert("所有任务似乎都已经在运行中了。");
    }
}

// === 新增：点击直达链接自动复制并提示 ===
/**
 * 功能：拦截列表中的链接点击
 * 1. 阻止默认直接跳转
 * 2. 复制链接到剪贴板
 * 3. 弹出提示
 * 4. 延迟后在新标签页打开链接 (确保复制完成)
 */
// === 新增：监听独立复制按钮点击 ===
document.getElementById('roomList').addEventListener('click', function (e) {
    // 1. 检查是否点击了复制按钮
    // 修改点：因为关闭按钮也在 roomList 里，确保不冲突，这里仅针对 btn-copy
    const btn = e.target.closest('.btn-copy');

    if (btn) {
        const url = btn.dataset.url;

        // 执行复制逻辑
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(url).then(() => {
                showCopyToast('链接已复制');
            }).catch(() => {
                fallbackCopyTextToClipboard(url);
            });
        } else {
            fallbackCopyTextToClipboard(url);
        }
    }
});

// 辅助函数：兼容性复制（保持原样，确保一定要有这个函数）
function fallbackCopyTextToClipboard(text) {
    var textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.left = "-9999px";
    textArea.style.top = "0";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
        var successful = document.execCommand('copy');
        if (successful) showCopyToast('📋 链接已复制');
        else showCopyToast('❌ 复制失败');
    } catch (err) {
        showCopyToast('❌ 无法访问剪贴板');
    }
    document.body.removeChild(textArea);
}

// 显示提示框函数
function showCopyToast(msg) {
    const toast = document.getElementById('toast-notification');
    if (!toast) return;

    const span = toast.querySelector('span');
    if (span) span.textContent = msg;

    toast.classList.add('show');

    // 清除之前的定时器（如果有）防止闪烁
    if (window.toastTimeout) clearTimeout(window.toastTimeout);

    window.toastTimeout = setTimeout(() => {
        toast.classList.remove('show');
    }, 2000);
}

// 启动
restartTimer();
refreshList();