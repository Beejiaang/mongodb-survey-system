"""Phase two HTML forms; reuse the existing question editor and auth stack."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from database import get_request_database
from question_library import (LibraryError, bank_revision, change_bank, create_bank, create_item,
    create_version, fork_version, get_bank, get_item, get_version, import_version, list_items,
    list_versions, save_survey_question, share_item, usage, version_difference,
    request_share, decide_share, share_records)
from survey_views import KINDS, STATUS_LABELS, _question_from_form, _render_question
from surveys import get_owned_survey, list_owned_surveys

library = Blueprint("library", __name__, url_prefix="/library")


def _db():
    return get_request_database()


def _values(question):
    values = {"title": question["title"], "required": "on" if question["required"] else None,
              "options_text": "\n".join(o["text"] for o in question.get("options", []))}
    values.update({k: "" if v is None else "on" if v is True else None if v is False else str(v)
                   for k, v in question["validation"].items()})
    return values


def _editor(item=None, base=None):
    default = base["content"]["input_type"] if base and base["content"]["type"] == "fill_blank" else base["content"]["type"] if base else "single_choice"
    kind = request.form.get("kind", default) if request.method == "POST" else request.args.get("kind", default)
    if request.method == "POST" and kind in ("text", "number"):
        kind = request.form.get("input_type", kind)
    if kind not in KINDS:
        abort(400)
    values = request.form if request.method == "POST" else _values(base["content"]) if base and kind == default else {}
    error, status = None, 200
    if request.method == "POST":
        action = request.form.get("action")
        if action == "number_hint":
            if kind not in ("text", "number"):
                abort(400)
            values = request.form.to_dict()
            title = values.get("title", "").strip()
            hint = "请使用阿拉伯数字回答"
            values["title"] = title if hint in title else f"{title}（{hint}）" if title else hint
            values["kind"] = kind = "number"
        elif action == "add_option":
            if kind not in ("single_choice", "multiple_choice"):
                abort(400)
        elif action:
            raise LibraryError("题目操作无效。")
        else:
            try:
                question = _question_from_form(kind)
                if item:
                    vid = create_version(_db(), item["_id"], g.user["_id"], base["_id"], question,
                                         note=request.form.get("change_note", ""))
                    iid = item["_id"]
                else:
                    iid = create_item(_db(), g.user["_id"], question)
                    vid = get_item(_db(), iid, g.user["_id"])["current_version_id"]
            except ValueError as invalid:
                error, status = str(invalid), 400
            else:
                flash("题目版本已保存，已有问卷内容保持固定。", "success")
                return redirect(url_for("library.item", item_id=iid, version=vid), code=303)
    return _render_question(None, kind, values, error, status, library_mode=True,
                             library_item=item, base_version=base)


@library.get("")
@login_required
def index():
    return render_template("library/index.html", title="题目与题库", items=list_items(_db(), g.user["_id"]))


@library.route("/new", methods=("GET", "POST"))
@login_required
def new():
    return _editor()


@library.get("/items/<item_id>")
@login_required
def item(item_id):
    owner = g.user["_id"]
    entry = get_item(_db(), item_id, owner)
    versions = list_versions(_db(), entry["_id"], owner)
    if not versions:
        raise LibraryError("题目不存在或未获授权。", 404)
    selected_id = request.args.get("version", str(versions[0]["_id"]))
    _, selected = get_version(_db(), selected_id, owner)
    if selected["question_id"] != entry["_id"]:
        raise LibraryError("版本不属于当前题目。")
    mapping = {v["_id"]: v for v in versions}
    compare = mapping.get(selected["parent_version_id"])
    if request.args.get("compare"):
        _, compare = get_version(_db(), request.args["compare"], owner)
        if compare["question_id"] != entry["_id"]:
            raise LibraryError("比较版本不属于当前题目。")
    differences = version_difference(compare["content"], selected["content"]) if compare else []
    drafts = [s for s in list_owned_surveys(_db(), owner) if s["status"] == "draft"]
    if entry["owner_id"] != owner:
        allowed = _db().question_shares.distinct("survey_id", {
            "version_id": selected["_id"], "requester_id": owner, "status": "approved"})
        drafts = [s for s in drafts if s["_id"] in allowed]
    return render_template("library/item.html", title=entry["name"], item=entry, versions=versions,
                           selected=selected, compare=compare, differences=differences,
                           mapping=mapping, drafts=drafts, kinds=KINDS)


@library.route("/items/<item_id>/versions/new", methods=("GET", "POST"))
@login_required
def new_version(item_id):
    entry = get_item(_db(), item_id, g.user["_id"], owner_only=True)
    _, base = get_version(_db(), request.args.get("base", str(entry["current_version_id"])), g.user["_id"])
    if base["question_id"] != entry["_id"]:
        raise LibraryError("来源版本不属于当前题目。")
    return _editor(entry, base)


@library.post("/versions/<version_id>/restore")
@login_required
def restore(version_id):
    entry, version = get_version(_db(), version_id, g.user["_id"])
    vid = create_version(_db(), entry["_id"], g.user["_id"], version["_id"], restore=True)
    flash(f"已用v{version['number']}内容生成新版本，并记录恢复来源。", "success")
    return redirect(url_for("library.item", item_id=entry["_id"], version=vid), code=303)


@library.post("/versions/<version_id>/fork")
@login_required
def fork(version_id):
    iid = fork_version(_db(), version_id, g.user["_id"])
    flash("已派生为自己的独立题目，可以继续创建新版本。", "success")
    return redirect(url_for("library.item", item_id=iid), code=303)


@library.post("/items/<item_id>/share")
@login_required
def share(item_id):
    action = request.form.get("action")
    if action not in ("share", "revoke"):
        raise LibraryError("请选择共享或撤销。")
    share_item(_db(), item_id, g.user["_id"], request.form.get("username", ""),
               survey_id=request.form.get("survey_id"), version_id=request.form.get("version_id"),
               revoke=action == "revoke")
    flash("共享设置已保存。", "success")
    return redirect(url_for("library.item", item_id=item_id), code=303)


@library.route("/sharing", methods=("GET", "POST"))
@login_required
def sharing():
    if request.method == "POST":
        raw_number = request.form.get("version_number", "1")
        if not raw_number.isascii() or not raw_number.isdigit() or len(raw_number) > 9:
            raise LibraryError("版本号须为正整数。")
        request_share(_db(), request.form.get("item_id"), g.user["_id"], request.form.get("survey_id"),
                      version_number=int(raw_number), message=request.form.get("message", ""))
        flash("申请已提交；出题者批准后，仅可在指定问卷使用该版本。", "success")
        return redirect(url_for("library.sharing"), code=303)
    return render_template("library/sharing.html", title="题目共享申请与审批",
                           records=share_records(_db(), g.user["_id"]),
                           drafts=[s for s in list_owned_surveys(_db(), g.user["_id"]) if s["status"] == "draft"])


@library.post("/sharing/<share_id>/decision")
@login_required
def sharing_decision(share_id):
    decide_share(_db(), share_id, g.user["_id"], request.form.get("action"))
    flash("审批或撤销已保存。", "success")
    return redirect(url_for("library.sharing"), code=303)


@library.post("/versions/<version_id>/use")
@login_required
def use(version_id):
    sid = request.form.get("survey_id", "")
    import_version(_db(), sid, g.user["_id"], version_id)
    flash("已加入指定版本，跳转请在当前问卷配置。", "success")
    return redirect(url_for("surveys.detail", survey_id=sid), code=303)


@library.post("/from-survey/<survey_id>/<question_id>")
@login_required
def from_survey(survey_id, question_id):
    iid = save_survey_question(_db(), survey_id, g.user["_id"], question_id)
    flash("已保存为常用题目，原问卷未作改动。", "success")
    return redirect(url_for("library.item", item_id=iid), code=303)


@library.get("/items/<item_id>/usage")
@login_required
def item_usage(item_id):
    entry = get_item(_db(), item_id, g.user["_id"])
    result = usage(_db(), item_id, g.user["_id"], request.args.get("version"))
    return render_template("library/usage.html", title="题目使用情况", item=entry, usage=result,
                           status_labels=STATUS_LABELS)


@library.route("/banks", methods=("GET", "POST"))
@login_required
def banks():
    if request.method == "POST":
        bid = create_bank(_db(), g.user["_id"], request.form.get("name", ""), request.form.get("description", ""))
        return redirect(url_for("library.bank", bank_id=bid), code=303)
    items = list(_db().question_banks.find({"owner_id": g.user["_id"]}).sort("created_at", -1))
    return render_template("library/banks.html", title="我的题库", banks=items)


@library.route("/banks/<bank_id>", methods=("GET", "POST"))
@login_required
def bank(bank_id):
    entry = get_bank(_db(), bank_id, g.user["_id"])
    if request.method == "POST":
        change_bank(_db(), bank_id, g.user["_id"], request.form.get("action"),
                    request.form.get("version_id"), request.form.get("revision"),
                    name=request.form.get("name"), description=request.form.get("description", ""))
        if request.form.get("action") == "delete":
            flash("题库已删除，其中的题目和问卷内容仍保留。", "success")
            return redirect(url_for("library.banks"), code=303)
        flash("题库已更新，已有问卷继续使用原版本。", "success")
        return redirect(url_for("library.bank", bank_id=bank_id), code=303)
    entries = []
    for ref in entry["entries"]:
        try:
            item, version = get_version(_db(), ref["version_id"], g.user["_id"])
            entries.append({"item": item, "version": version, "ref": ref})
        except LibraryError:
            entries.append({"item": None, "version": None, "ref": ref})
    return render_template("library/bank.html", title=entry["name"], bank=entry,
                           entries=entries, revision=bank_revision(entry), items=list_items(_db(), g.user["_id"]),
                           versions={str(i["_id"]): list_versions(_db(), i["_id"], g.user["_id"]) for i in list_items(_db(), g.user["_id"])})


@library.route("/select/<survey_id>", methods=("GET", "POST"))
@login_required
def select(survey_id):
    survey = get_owned_survey(_db(), survey_id, g.user["_id"])
    if survey is None:
        raise LibraryError("问卷不存在。", 404)
    if survey["status"] != "draft":
        raise LibraryError("只有草稿可以选题。", 409)
    if request.method == "POST":
        import_version(_db(), survey_id, g.user["_id"], request.form.get("version_id"))
        flash("指定版本已加入问卷。", "success")
        return redirect(url_for("surveys.detail", survey_id=survey_id), code=303)
    items = list_items(_db(), g.user["_id"], survey_id=survey_id)
    bank_id = request.args.get("bank")
    pinned = get_bank(_db(), bank_id, g.user["_id"])["entries"] if bank_id else None
    allowed = {r["version_id"] for r in pinned} if pinned is not None else None
    rows = []
    for item in items:
        versions = list_versions(_db(), item["_id"], g.user["_id"], survey_id=survey_id)
        versions = [v for v in versions if allowed is None or v["_id"] in allowed]
        if versions:
            rows.append({"item": item, "versions": versions})
    return render_template("library/select.html", title="从题库选题", survey=survey, rows=rows,
                           banks=list(_db().question_banks.find({"owner_id": g.user["_id"]})), bank_id=bank_id)


@library.get("/versions/<version_id>/results")
@login_required
def version_results(version_id):
    from library_results import summarize_version
    try:
        page = int(request.args.get("page", "1"))
        if page < 1 or page > 100000:
            raise ValueError()
    except ValueError:
        raise LibraryError("页码无效。")
    summary = summarize_version(_db(), version_id, g.user["_id"], page=page)
    return render_template("library/results.html", title="跨问卷单版本统计", summary=summary,
                           version_id=version_id, page=page, kinds=KINDS)
