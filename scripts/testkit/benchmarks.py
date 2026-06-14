#!/usr/bin/env python3
"""测评集(benchmark suite)— 每个 testkit 用例的任务级验收脚本。

沉淀原则(与 reset.sh 的 CASES 同源,不复制任务文本):
  * 任务文本在运行时从 reset.sh 的 CASES 列表解析(单一事实来源);
  * 每个用例 = 通用检查(所有用例必过的工程底线)+ 专项检查(该用例的
    交付物语义),全部落在这个文件里,可审阅、可扩展、可回归;
  * 每个检查是 (name, ok, detail) 三元组;得分 = 通过加权和 / 总权重。

用法(被 run_benchmark.py 调用,也可独立验收任意工作区):
    python3 scripts/testkit/benchmarks.py <case_key> <workspace_dir>
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RESET_SH = Path(__file__).resolve().parent / "reset.sh"
SEED_CASES = Path(__file__).resolve().parent / "seed_cases.py"

TEXT_EXT = {".md", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".csv", ".yml", ".yaml"}


def case_tasks() -> dict[str, str]:
    """Parse the CASES list → {key: task_text} (single source of truth).

    Cases now live in seed_cases.py as 7-tuples
    ``(key, title, who, cat, spec, task, attach)`` (older reset.sh used 4-tuples
    ``(key, title, who, task)``). We locate the CASES assignment via the module
    AST and ``literal_eval`` just that node — robust over 500 Chinese prompts and
    tolerant of both tuple arities. Falls back to reset.sh if seed_cases is absent.
    """
    import ast

    for src in (SEED_CASES, RESET_SH):
        if not src.exists():
            continue
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CASES" for t in node.targets
            ):
                try:
                    cases = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return {}
                out: dict[str, str] = {}
                for c in cases:
                    if len(c) >= 6:  # 7-tuple: task at index 5
                        out[c[0]] = c[5]
                    elif len(c) == 4:  # legacy 4-tuple: task at index 3
                        out[c[0]] = c[3]
                return out
    return {}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    weight: float = 1.0

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail[:200]}


def _files(ws: Path) -> list[Path]:
    out = []
    for p in ws.rglob("*"):
        rel = p.relative_to(ws)
        if rel.parts and rel.parts[0] in (".git", ".polynoia", "node_modules", ".venv"):
            continue
        if p.is_file():
            out.append(p)
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _find(ws: Path, *globs: str) -> Path | None:
    for g in globs:
        for p in ws.rglob(g):
            rel = p.relative_to(ws)
            if rel.parts and rel.parts[0] in (".git", ".polynoia", "node_modules"):
                continue
            return p
    return None


# ── 通用底线(所有用例)────────────────────────────────────────────


def generic_checks(ws: Path) -> list[Check]:
    import subprocess

    files = _files(ws)
    checks = [Check("交付了文件(非空工作区)", len(files) > 0, f"{len(files)} 个文件")]
    # ≥1 笔非 init 提交进了 main
    try:
        log = subprocess.run(
            ["git", "-C", str(ws), "log", "--oneline", "main"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip().splitlines()
        non_init = [l for l in log if "workspace init" not in l]
        checks.append(Check("产出合入 main(≥1 非 init 提交)", len(non_init) >= 1, f"{len(non_init)} 笔"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("产出合入 main(≥1 非 init 提交)", False, str(e)[:80]))
    # 没有合并冲突残留
    dirty = [
        str(p.relative_to(ws))
        for p in files
        if p.suffix in TEXT_EXT and "<<<<<<<" in _read(p)
    ]
    checks.append(Check("无冲突标记残留", not dirty, ", ".join(dirty[:3])))
    # 半成品守门:不要只有一个空壳文件
    total_bytes = sum(p.stat().st_size for p in files)
    checks.append(Check("交付物有实质内容(>1KB)", total_bytes > 1024, f"{total_bytes}B"))
    return checks


# ── 专项验收 ─────────────────────────────────────────────────────


def v_game_2048(ws: Path) -> list[Check]:
    html = _find(ws, "*.html")
    js_all = " ".join(_read(p) for p in _files(ws) if p.suffix in (".js", ".jsx", ".ts", ".html"))
    return [
        Check("有 HTML 入口", html is not None, str(html.relative_to(ws)) if html else ""),
        Check("实现了棋盘/格子逻辑", bool(re.search(r"grid|board|tile|cell|4\s*[x×*]\s*4", js_all, re.I))),
        Check("实现了 2048 合并/分数", bool(re.search(r"merge|combine|score|2048", js_all, re.I))),
        Check("有键盘或触摸操作", bool(re.search(r"keydown|keyup|ArrowUp|touchstart|swipe", js_all, re.I))),
        Check("游戏体量合理(>2KB 代码)", len(js_all) > 2048, f"{len(js_all)}B"),
    ]


def v_react_plane_war(ws: Path) -> list[Check]:
    pkg = _find(ws, "app/package.json", "package.json")
    appjsx = _find(ws, "app/src/App.jsx", "src/App.jsx", "app/src/App.tsx")
    readme = _find(ws, "README.md", "app/README.md")
    code = " ".join(_read(p) for p in _files(ws) if p.suffix in (".jsx", ".tsx", ".js"))
    return [
        Check("React 工程结构(package.json)", pkg is not None),
        Check("有 src/App 组件(非单 HTML)", appjsx is not None),
        Check("实现了核心玩法(敌机/子弹/碰撞)", bool(re.search(r"enemy|bullet|collision|hit", code, re.I))),
        Check("有分数/生命值", bool(re.search(r"score|lives|hp|health", code, re.I))),
        Check("README 含启动方式", readme is not None and "npm" in _read(readme)),
    ]


def v_family_budget_xlsx(ws: Path) -> list[Check]:
    x = _find(ws, "*.xlsx")
    valid = False
    sheets = ""
    if x:
        try:
            with zipfile.ZipFile(x) as z:
                valid = "xl/workbook.xml" in z.namelist()
                sheets = f"{len([n for n in z.namelist() if n.startswith('xl/worksheets/')])} sheet"
        except zipfile.BadZipFile:
            valid = False
    return [
        Check("交付了 .xlsx", x is not None, str(x.relative_to(ws)) if x else ""),
        Check("xlsx 是合法 OOXML(可打开)", valid, sheets),
    ]


def v_sales_analysis_report(ws: Path) -> list[Check]:
    md = _find(ws, "sales-analysis*.md", "*分析*.md", "*.md")
    body = _read(md) if md else ""
    csv = _find(ws, "*.csv")
    return [
        Check("有分析报告 markdown", md is not None and len(body) > 500, f"{len(body)} 字符"),
        Check("覆盖关键口径(GMV/客单价/复购)", bool(re.search(r"GMV|客单价|复购|退款", body))),
        Check("有数据文件(csv)", csv is not None),
        Check("报告有结构(≥3 个小节)", body.count("#") >= 3),
    ]


def v_single_agent_portfolio(ws: Path) -> list[Check]:
    html = _find(ws, "index.html", "*.html")
    body = _read(html) if html else ""
    css = _find(ws, "*.css")
    return [
        Check("有网站入口 index.html", html is not None),
        Check("有作品集语义(gallery/作品)", bool(re.search(r"gallery|portfolio|作品|photo", body, re.I))),
        Check("有样式(css 或内联)", css is not None or "<style" in body),
        Check("有多个作品位(≥3 图)", body.count("<img") >= 3 or body.count("background-image") >= 3),
    ]


def v_django_like_api_spec(ws: Path) -> list[Check]:
    py = " ".join(_read(p) for p in _files(ws) if p.suffix == ".py")
    spec = _find(ws, "*api*.md", "*spec*.md", "README.md")
    return [
        Check("有 API 实现(路由装饰器)", bool(re.search(r"@(app|router)\.(get|post|put|patch|delete)", py))),
        Check("覆盖权限语义(role/permission)", bool(re.search(r"role|permission|权限|auth", py + _read(spec) if spec else py, re.I))),
        Check("有接口文档/spec", spec is not None),
    ]


# ── REAL acceptance for hard multi-file deliverables ────────────────
# These go beyond regex: they actually COMPILE the backend (py_compile —
# real syntax validity across every .py), validate package.json is parseable
# with a build script + framework dep, and (opt-in via POLYNOIA_BM_BUILD=1)
# run `npm ci && npm run build` with a timeout. This is what kills the
# "an empty stub with a 1KB README passes" hole.
import json as _json
import os as _os
import py_compile as _pyc
import subprocess as _sp
import tempfile as _tmp


def _py_all_compile(ws: Path) -> tuple[bool, str]:
    pys = [p for p in _files(ws) if p.suffix == ".py"]
    if not pys:
        return False, "无 .py 文件"
    bad = []
    for p in pys:
        try:
            _pyc.compile(str(p), doraise=True)
        except _pyc.PyCompileError as e:
            bad.append(f"{p.name}: {str(e.exc_value)[:60]}")
    return (not bad), (f"{len(pys)} py 全过" if not bad else f"语法错: {bad[:2]}")


def _pkg_json(ws: Path) -> dict | None:
    f = _find(ws, "app/package.json", "frontend/package.json", "package.json", "web/package.json")
    if not f:
        return None
    try:
        return _json.loads(_read(f))
    except (ValueError, _json.JSONDecodeError):
        return None


def _npm_build(ws: Path) -> Check:
    """Opt-in real build (POLYNOIA_BM_BUILD=1). Off by default — install+build
    is minutes & network-dependent; the structural+compile checks already give
    real acceptance signal."""
    if _os.environ.get("POLYNOIA_BM_BUILD") != "1":
        return Check("npm 构建(opt-in,未启用)", True, "set POLYNOIA_BM_BUILD=1 to run", weight=0.0)
    pkg = _find(ws, "app/package.json", "frontend/package.json", "package.json")
    if not pkg:
        return Check("npm 构建", False, "无 package.json")
    d = pkg.parent
    try:
        _sp.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=d, capture_output=True, timeout=600)
        r = _sp.run(["npm", "run", "build"], cwd=d, capture_output=True, text=True, timeout=600)
        return Check("npm run build 通过", r.returncode == 0, r.stderr[-160:] if r.returncode else "built")
    except Exception as e:  # noqa: BLE001
        return Check("npm run build 通过", False, str(e)[:120])


def v_fullstack_issue_tracker(ws: Path) -> list[Check]:
    pkg = _pkg_json(ws)
    py = " ".join(_read(p) for p in _files(ws) if p.suffix == ".py")
    compiled, cmsg = _py_all_compile(ws)
    return [
        Check("前端 package.json 合法", pkg is not None),
        Check("前端是 React 工程", bool(pkg and "react" in _json.dumps(pkg.get("dependencies", {})).lower())),
        Check("有 build 脚本", bool(pkg and "build" in (pkg.get("scripts") or {}))),
        Check("后端 FastAPI app", bool(re.search(r"FastAPI\(|APIRouter\(", py))),
        Check("后端有 CRUD 路由(≥3)", len(re.findall(r"@(app|router)\.(get|post|put|patch|delete)", py)) >= 3),
        Check("后端 Python 全部可编译", compiled, cmsg),
        _npm_build(ws),
    ]


def v_vue_inventory_admin(ws: Path) -> list[Check]:
    pkg = _pkg_json(ws)
    js = " ".join(_read(p) for p in _files(ws) if p.suffix in (".js", ".ts", ".vue", ".mjs"))
    return [
        Check("前端 package.json 合法", pkg is not None),
        Check("前端是 Vue 工程", bool(pkg and "vue" in _json.dumps(pkg.get("dependencies", {})).lower())),
        Check("有 .vue 组件", _find(ws, "*.vue") is not None),
        Check("后端 Express 路由", bool(re.search(r"express\(\)|app\.(get|post|put|delete)\(|router\.(get|post)", js))),
        Check("有库存语义(CRUD/库存字段)", bool(re.search(r"inventory|stock|库存|quantity|sku", js, re.I))),
        _npm_build(ws),
    ]


def v_csv_upload_dashboard(ws: Path) -> list[Check]:
    files = _files(ws)
    code = " ".join(_read(p) for p in files if p.suffix in (".py", ".js", ".jsx", ".ts", ".tsx", ".html"))
    compiled, cmsg = _py_all_compile(ws) if any(p.suffix == ".py" for p in files) else (True, "无 py")
    return [
        Check("有 CSV 解析逻辑", bool(re.search(r"csv|parse|read_csv|papaparse|FileReader", code, re.I))),
        Check("有上传入口", bool(re.search(r'type=["\']file["\']|upload|multipart|FormData', code, re.I))),
        Check("有图表/看板渲染", bool(re.search(r"chart|canvas|svg|recharts|echarts|d3|table", code, re.I))),
        Check("Python(若有)可编译", compiled, cmsg),
    ]


def v_ops_status_dashboard(ws: Path) -> list[Check]:
    pkg = _pkg_json(ws)
    code = " ".join(_read(p) for p in _files(ws) if p.suffix in (".py", ".js", ".jsx", ".ts", ".tsx"))
    compiled, cmsg = _py_all_compile(ws) if any(p.suffix == ".py" for p in _files(ws)) else (True, "无 py")
    return [
        Check("前端工程(package.json)", pkg is not None),
        Check("有状态/健康语义", bool(re.search(r"status|health|uptime|incident|监控|可用", code, re.I))),
        Check("有后端接口", bool(re.search(r"@(app|router)\.(get|post)|app\.(get|post)\(|fetch\(|axios", code))),
        Check("Python(若有)可编译", compiled, cmsg),
    ]


VERIFIERS = {
    "game_2048": v_game_2048,
    "react_plane_war": v_react_plane_war,
    "family_budget_xlsx": v_family_budget_xlsx,
    "sales_analysis_report": v_sales_analysis_report,
    "single_agent_portfolio": v_single_agent_portfolio,
    "django_like_api_spec": v_django_like_api_spec,
    "fullstack_issue_tracker": v_fullstack_issue_tracker,
    "vue_inventory_admin": v_vue_inventory_admin,
    "csv_upload_dashboard": v_csv_upload_dashboard,
    "ops_status_dashboard": v_ops_status_dashboard,
}


def verify(case_key: str, ws_dir: str | Path) -> dict:
    """通用 + 专项检查 → {score: 0..1, checks: [...]}。"""
    ws = Path(ws_dir)
    checks = generic_checks(ws)
    extra = VERIFIERS.get(case_key)
    if extra is not None:
        checks += extra(ws)
    total = sum(c.weight for c in checks)
    got = sum(c.weight for c in checks if c.ok)
    return {
        "score": round(got / total, 3) if total else 0.0,
        "checks": [c.as_dict() for c in checks],
        "specific": extra is not None,
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(verify(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
