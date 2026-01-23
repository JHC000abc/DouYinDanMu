// 辅助函数：设置状态信息和对应的 CSS 类名
function setStatus(type, msg) {
    const statusEl = document.getElementById('status');
    // 重置类名，只保留基础类和传入的状态类
    statusEl.className = 'status-msg ' + (type || '');
    statusEl.innerText = msg;
    // 清除内联颜色样式，让 CSS 类来控制颜色
    statusEl.style.color = '';
}

async function parseStream() {
    const input = document.getElementById('urlInput').value.trim();
    const resultArea = document.getElementById('resultArea');
    const streamUrlEl = document.getElementById('streamUrl');
    const btn = document.getElementById('parseBtn');

    if (!input) {
        setStatus('error', "请输入链接或房间号");
        return;
    }

    // 1. 提取 ID 逻辑
    let roomId = input;
    const urlMatch = input.match(/live\.douyin\.com\/(\d+)/);
    if (urlMatch) {
        roomId = urlMatch[1];
    } else {
        const numMatch = input.match(/^(\d+)$/);
        if (!numMatch) {
            const lastNum = input.match(/(\d{10,})/);
            if (lastNum) roomId = lastNum[1];
        }
    }

    // UI 状态更新
    btn.disabled = true;
    // 使用新的纯 CSS 加载图标类名
    btn.innerHTML = '<div class="loading-spinner"></div> 解析中...';
    setStatus('searching', `正在解析房间 ID: ${roomId}`);
    resultArea.style.display = "none";
    streamUrlEl.innerText = "";

    try {
        // 2. 调用后端接口
        const response = await fetch(`/monitor/${roomId}`);
        const data = await response.json();

        if (data.code === 200 && data.url) {
            setStatus('success', "解析成功");
            streamUrlEl.innerText = data.url;
            resultArea.style.display = "block";
        } else {
            throw new Error(data.msg || "未获取到流地址");
        }
    } catch (error) {
        console.error(error);
        setStatus('error', `解析失败: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.innerText = "解析";
    }
}

function copyToClipboard() {
    const text = document.getElementById('streamUrl').innerText;
    navigator.clipboard.writeText(text).then(() => {
        const btn = document.querySelector('.copy-btn');
        // 添加 .copied 类名，触发 CSS 状态切换
        btn.classList.add('copied');
        btn.innerText = "已复制";
        setTimeout(() => {
            // 2秒后恢复原状
            btn.classList.remove('copied');
            btn.innerText = "一键复制";
        }, 2000);
    }).catch(err => {
        alert('复制失败，请手动复制');
    });
}

document.addEventListener('DOMContentLoaded', function() {
    const inputEl = document.getElementById('urlInput');
    if (inputEl) {
        inputEl.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                parseStream();
            }
        });
    }
});