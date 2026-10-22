"use strict";

function optionLabel(index) {
    let label = "";
    while (index > 0) {
        index -= 1;
        label = String.fromCharCode(65 + index % 26) + label;
        index = Math.floor(index / 26);
    }
    return label;
}

function mountQuestionEditor(document) {
    const editor = document.querySelector("[data-option-editor]");
    if (editor) {
        const add = editor.querySelector(".option-add");
        add.addEventListener("click", event => {
            event.preventDefault();
            const index = editor.querySelectorAll(".option-row").length + 1;
            const letter = optionLabel(index);
            const row = document.createElement("div");
            row.className = "option-row";
            const label = document.createElement("label");
            label.className = "option-letter";
            label.htmlFor = "option-" + index;
            label.textContent = letter;
            const input = document.createElement("input");
            input.id = label.htmlFor;
            input.name = "option_text";
            input.required = true;
            input.placeholder = "填写选项内容";
            input.setAttribute("aria-label", "选项 " + letter + " 的内容");
            row.append(label, input);
            editor.insertBefore(row, add);
            input.focus();
        });
    }

    const type = document.querySelector("[data-fill-type]");
    if (type) {
        const refresh = () => {
            for (const section of document.querySelectorAll("[data-fill-validation]")) {
                section.hidden = section.disabled = section.dataset.fillValidation !== type.value;
            }
            document.querySelector('input[name="kind"]').value = type.value;
        };
        type.addEventListener("change", refresh);
        refresh();
    }
}

if (typeof module !== "undefined") module.exports = {optionLabel, mountQuestionEditor};
if (typeof document !== "undefined") mountQuestionEditor(document);
