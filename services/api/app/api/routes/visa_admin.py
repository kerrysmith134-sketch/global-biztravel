"""签证目录维护接口（ADR-0034 网页形式兜底）：API + ADMIN HTML 维护页。

签证官方源没有免费 API；维护页给 ADMIN 一个"网页即可修改目录"的兜底路径，
覆盖条目落 ``visa_catalog_overrides`` 表并与离线基线合并（人工确认强制为真）。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.deps import ROLE_ADMIN, ContextDep, SessionDep, require_role
from app.services import visa_catalog_override_service

router = APIRouter(tags=["admin"])


class VisaOverrideEntry(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    business_entry: str = Field(min_length=1, max_length=200)
    review_hint: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "allow"}


class VisaOverrideUpsert(BaseModel):
    entry: VisaOverrideEntry


@router.get(
    "/admin/visa-catalog",
    summary="签证目录（离线基线 + 组织覆盖合并）",
)
def get_visa_catalog(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    return visa_catalog_override_service.effective_catalog(session, context.organization_id)


@router.put(
    "/admin/visa-catalog/{country_code}",
    status_code=200,
    summary="覆盖一个国家的签证要求条目（人工确认语义强制保留）",
)
def put_visa_override(
    country_code: str,
    payload: VisaOverrideUpsert,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_ADMIN)
    override = visa_catalog_override_service.upsert_override(
        session,
        context.organization_id,
        country_code=country_code,
        entry=payload.entry.model_dump(),
        actor_user_id=context.user_id,
    )
    return {
        "country_code": override.country_code,
        "entry": override.entry,
        "updated_at": override.updated_at.isoformat(),
    }


@router.delete(
    "/admin/visa-catalog/{country_code}",
    status_code=200,
    summary="删除一个国家的组织级覆盖（回落到离线基线）",
)
def delete_visa_override(country_code: str, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    visa_catalog_override_service.delete_override(
        session, context.organization_id, country_code=country_code
    )
    return {"country_code": country_code.upper(), "override_removed": True}


_MAINTENANCE_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>签证目录维护</title>
<style>
  body { font-family: -apple-system, "PingFang SC", sans-serif; margin: 2rem; color: #222; }
  h1 { font-size: 1.3rem; } h2 { font-size: 1.1rem; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  th, td { border: 1px solid #ccc; padding: .4rem .6rem; text-align: left; font-size: .9rem; }
  th { background: #f5f5f5; } textarea { width: 100%; min-height: 8rem; font-family: monospace; }
  button { padding: .4rem 1rem; margin-right: .5rem; } .msg { margin: .5rem 0; color: #060; }
  .err { color: #900; } code { background: #f5f5f5; padding: .1rem .3rem; font-size: .8rem; }
  .hint { color: #666; font-size: .85rem; }
</style>
</head>
<body>
<h1>签证要求目录维护（组织级覆盖）</h1>
<p class="hint">离线基线只读；在这里修改会整条覆盖对应国家，并强制保留人工确认标记。
签证要求不是官方结论——发布前请按官方渠道复核。删除覆盖即回落基线。</p>
<table id="catalog"><thead><tr><th>国家</th><th>条目</th><th></th></tr></thead><tbody></tbody></table>
<h2>覆盖 / 新增一个国家</h2>
<p><label>国家代码（ISO alpha-2）：<input id="cc" size="4" placeholder="DE"></label></p>
<p><textarea id="entry" placeholder='{"label": "德国", "business_entry": "申根短期商务访问", "review_hint": "…"}'></textarea></p>
<button onclick="save()">保存覆盖</button>
<button onclick="removeOverride()">删除覆盖</button>
<p id="msg" class="msg"></p>
<script>
/* dev_auth 下身份取默认种子组织；生产部署应替换为真实会话注入 */
const ORG = localStorage.getItem("gbt_org_id") || "11111111-1111-1111-1111-111111111111";
const USER = localStorage.getItem("gbt_user_id") || "22222222-2222-2222-2222-222222222222";
const H = { "X-Org-Id": ORG, "X-User-Id": USER, "Content-Type": "application/json" };
let current = {};

async function load() {
  const res = await fetch("visa-catalog", { headers: H });
  const data = await res.json();
  current = data.countries || {};
  const body = document.querySelector("#catalog tbody");
  body.innerHTML = "";
  for (const [code, entry] of Object.entries(current)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><b>${code}</b></td><td><code>${JSON.stringify(entry)}</code></td>
      <td><button onclick="edit('${code}')">编辑</button></td>`;
    body.appendChild(tr);
  }
}

function edit(code) {
  document.getElementById("cc").value = code;
  document.getElementById("entry").value = JSON.stringify(current[code], null, 2);
}

async function save() {
  const cc = document.getElementById("cc").value.trim().toUpperCase();
  let entry;
  try { entry = JSON.parse(document.getElementById("entry").value); }
  catch (e) { return show("JSON 解析失败：" + e.message, true); }
  const res = await fetch("visa-catalog/" + cc, { method: "PUT", headers: H, body: JSON.stringify({ entry }) });
  show(res.ok ? "已保存覆盖：" + cc : await res.text(), !res.ok);
  if (res.ok) load();
}

async function removeOverride() {
  const cc = document.getElementById("cc").value.trim().toUpperCase();
  const res = await fetch("visa-catalog/" + cc, { method: "DELETE", headers: H });
  show(res.ok ? "已删除覆盖：" + cc : await res.text(), !res.ok);
  if (res.ok) load();
}

function show(text, isErr) {
  const el = document.getElementById("msg");
  el.textContent = text; el.className = isErr ? "msg err" : "msg";
}

load();
</script>
</body>
</html>"""


@router.get(
    "/admin/visa-catalog.html",
    response_class=HTMLResponse,
    summary="签证目录维护页（网页形式，ADMIN）",
)
def visa_catalog_page(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    return HTMLResponse(_MAINTENANCE_PAGE)


__all__ = ["router"]
