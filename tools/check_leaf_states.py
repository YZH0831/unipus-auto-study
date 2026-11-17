# -*- coding: utf-8 -*-
"""通过监听页面自身的 course_progress 响应，读每个叶子的 pass 状态。

不手动 fetch（缺鉴权头会 401），只解析浏览器自己发的请求。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

INST = "course-v2:818fac3ca01f081+nhce_4_vls_1+20240205"
URL0 = ("https://ucontent.unipus.cn/_explorationpc_default/pc.html"
        "?cid=1847183133920289074&appid=116#/"
        f"{INST}/courseware/74112ed2a002c50/706f6b62001277d/"
        "706f74aee00277d/706f74aee01277d")


def main():
    from upc.browser import BrowserSession
    from upc.config import load_config
    from upc.logutil import setup_logging
    from study_ai import build_leaves

    cfg = load_config(ROOT / "config.yaml", strict=False)
    cfg.browser.headless = True
    log = setup_logging(ROOT / "logs", "WARNING")
    sess = json.loads((Path(cfg.state_dir) / "api_session.json").read_text(encoding="utf-8"))
    s = BrowserSession(cfg, log)
    p = s.page
    all_leafs = {}
    progress_urls = []

    def on_resp(r):
        u = r.url or ""
        if "course_progress" not in u:
            return
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return
        rt = j.get("rt") or {}
        lf = rt.get("leafs") or {}
        if lf:
            all_leafs.update(lf)
            progress_urls.append((u.split("/")[-3] if "/" in u else u, len(lf)))

    p.on("response", on_resp)
    try:
        s.context.add_cookies([
            {"name": c["name"], "value": c["value"],
             "domain": c.get("domain") or "", "path": c.get("path") or "/"}
            for c in sess["cookies"] if c.get("domain")])
        s.goto(URL0, context="课件")
        p.wait_for_timeout(12000)

        # 结构树（同源 fetch 可用）
        raw = p.evaluate(r"""
        async (inst) => {
          const r = await fetch(`/course/api/course/${inst}/default`,
                                {credentials:'include'});
          return await r.json();
        }
        """, INST)
        cs = (raw or {}).get("course")
        struct = json.loads(cs) if isinstance(cs, str) else cs
        leaves, micros = build_leaves(struct)

        print("捕获 progress 响应:", progress_urls)
        print("合计 leaf 状态:", len(all_leafs))
        print("\n=== micro 3 (Listening to China) 8 个叶子 ===")
        mine = [x for x in leaves if x.get("micro_idx") == 3]
        for x in mine:
            st = all_leafs.get(x["id"]) or {}
            state = st.get("state") or {}
            print(f"  {x['name'][:20]:<22} {x.get('tab_type'):<10} "
                  f"pass={state.get('pass')} pass2={state.get('pass2')} "
                  f"duration={st.get('duration')} inProgress={'id' in st}")

        print("\n=== 已 pass 的叶子 ===")
        passed = [k for k, v in all_leafs.items() if (v.get("state") or {}).get("pass")]
        for k in passed[:20]:
            name = next((x["name"] for x in leaves if x["id"] == k), "?")
            print(f"  {k:<20} {name[:30]}")
        print(f"\n共 {len(passed)}/{len(all_leafs)} 个 pass")

        (ROOT / "output" / "leaf_states.json").write_text(
            json.dumps({"leafs": all_leafs, "urls": progress_urls},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
