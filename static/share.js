"use strict";

const copyButton = document.getElementById("copy-share");
if (copyButton) {
    copyButton.addEventListener("click", async () => {
        const input = document.getElementById("share-url");
        const message = document.getElementById("copy-status");
        try {
            await navigator.clipboard.writeText(input.value);
            message.textContent = "链接已复制。接收者需登录后访问。";
        } catch {
            input.focus();
            input.select();
            message.textContent = "自动复制不可用，链接已选中，请手动复制。";
        }
    });
}
