"use strict";

const copyButton = document.getElementById("copy-share");
if (copyButton) {
    copyButton.addEventListener("click", async () => {
        const input = document.getElementById("share-url");
        const message = document.getElementById("copy-status");
        const success = "链接已复制。接收者需登录后访问。";
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(input.value);
                message.textContent = success;
                return;
            }
        } catch { /* HTTP局域网地址或权限拒绝时，继续尝试兼容复制。 */ }
        input.focus();
        input.select();
        input.setSelectionRange(0, input.value.length);
        try {
            if (document.execCommand && document.execCommand("copy")) {
                message.textContent = success;
                return;
            }
        } catch { /* 浏览器禁用兼容复制时保留选择范围。 */ }
        message.textContent = "自动复制不可用，链接已选中，请按 Ctrl+C 或在手机上长按链接复制。";
    });
}
