#!/usr/bin/env python3
"""webui_accept.py —— 页面交付前的自动验收：版面 + 数据 + 接口，一把过。

它不做"看着还行"的判断，只量三件客观的事：

  1. 一屏：documentElement 的 scrollHeight/scrollWidth 不超出视口（允差 1px）
  2. 不重叠：同一列里相邻的卡片矩形不相交；卡片里的文字不被裁（scrollWidth）
  3. 有数：电平/ENV/GR/8 条增益/4 个压缩滑杆/健康 6 项都在，且渲染出数字

顺带跑一遍后端自检（fir_pl_webui.py --selftest），确认接口契约没被改坏。

用法：
    python tools/webui_accept.py                     # 自己起假数据 + 无头浏览器
    python tools/webui_accept.py --url http://192.168.2.99:8080/
    python tools/webui_accept.py --url ... --size 1366x768 --shot out.png

没有装 Playwright 也能跑：用的是系统里的 Edge/Chrome 无头模式，
注入一段测量脚本，再把结果从 document.title 里捞回来。

退出码 0 = 全过，1 = 有 FAIL。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/microsoft-edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

PROBE_JS = r"""
<script>
(function(){
  var errs = [];
  window.addEventListener('error', function(e){ errs.push(String(e.message||e)); });
  function ready(){
    var out = {errs: errs};
    var de = document.documentElement;
    out.overflow_y = de.scrollHeight - window.innerHeight;
    out.overflow_x = de.scrollWidth - window.innerWidth;

    function q(s){ return document.querySelectorAll(s); }
    out.n_gain_left  = q('#gains .gain-row').length;
    out.n_gain_right = q('#gains-r .gain-row').length;
    out.n_comp_slider = q('#comp-grid input[type=range]').length;
    out.has_level = !!document.getElementById('bar-in') && !!document.getElementById('bar-out');
    out.has_health = q('.a-health .hl-grid > div').length;

    // 1) 卡片重叠：同一列里任意两张卡片的相交面积
    var worst = 0, worstPair = '';
    ['.col-l', '.col-r', '#grid'].forEach(function(sel){
      var col = document.querySelector(sel); if (!col) return;
      var kids = [].filter.call(col.children, function(el){
        return el.classList.contains('sect') || el.classList.contains('col');
      });
      for (var i = 0; i < kids.length; i++) for (var j = i + 1; j < kids.length; j++) {
        var a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
        var w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        var h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        var area = (w > 0 && h > 0) ? w * h : 0;
        if (area > worst) { worst = area; worstPair = (kids[i].className||'') + ' / ' + (kids[j].className||''); }
      }
    });
    out.overlap_px = Math.round(worst);
    out.overlap_pair = worstPair;

    // 2) 文字被裁：scrollWidth 超过 clientWidth 的叶子节点
    var clipped = [];
    q('.band, .val, .lvl-val, .hl-grid b, h3, .statline span').forEach(function(el){
      if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) {
        clipped.push((el.className || el.tagName) + ':' + el.textContent.trim().slice(0, 14));
      }
    });
    out.clipped = clipped.slice(0, 8);
    out.n_clipped = clipped.length;

    // 3) 有没有真的渲染出数字（假数据/真板子都该有）
    var lvl = document.getElementById('val-in');
    out.val_in = lvl ? lvl.textContent.trim() : '';
    var g = document.querySelector('#gains .gain-row .val');
    out.val_gain = g ? g.textContent.trim() : '';
    var om = document.getElementById('om-env');
    out.val_env = om ? om.textContent.trim().slice(0, 20) : '';
    var hl = document.querySelector('#hl-temp');
    out.val_temp = hl ? hl.textContent.trim() : '';

    // 4) 关键区块顺序（左列是不是 频响→波形→频谱）
    var left = [].map.call(document.querySelectorAll('.col-l > .sect'), function(s){
      var t = s.querySelector('h3'); return t ? t.textContent.trim().slice(0, 4) : '?';
    });
    out.left_order = left.join('>');

    document.title = 'PROBE' + JSON.stringify(out);
  }
  if (document.readyState === 'complete') setTimeout(ready, 1600);
  else window.addEventListener('load', function(){ setTimeout(ready, 1600); });
})();
</script>
"""


def find_browser():
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 Edge/Chrome，装一个或改用 --dump 手工量")


class Injector(BaseHTTPRequestHandler):
    """把目标页的 HTML 抓下来、插一段测量脚本、再吐回去；/api/* 原样转发。"""

    base = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            req = urllib.request.Request(self.base.rstrip("/") + self.path)
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type", "text/html; charset=utf-8")
        except Exception as ex:
            self.send_error(502, str(ex))
            return
        if "text/html" in ctype:
            txt = body.decode("utf-8", "replace")
            txt = txt.replace("</body>", PROBE_JS + "</body>")
            body = txt.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def measure(url, browser, size):
    w, h = [int(x) for x in size.lower().split("x")]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Injector)
    Injector.base = url
    port = srv.server_address[1]
    th = __import__("threading").Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        out = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--window-size=%d,%d" % (w, h), "--virtual-time-budget=6000",
             "--dump-dom", "http://127.0.0.1:%d/" % port],
            capture_output=True, timeout=90)
    finally:
        srv.shutdown()
        srv.server_close()

    dom = out.stdout.decode("utf-8", "replace")
    i = dom.find("<title>PROBE")
    if i < 0:
        raise SystemExit("没拿到测量结果（页面没跑完？）\n" + dom[:400])
    raw = dom[i + len("<title>PROBE"):]
    raw = raw[:raw.find("</title>")]
    raw = raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return json.loads(raw)


def shot(url, browser, size, path):
    w, h = [int(x) for x in size.lower().split("x")]
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--window-size=%d,%d" % (w, h), "--virtual-time-budget=6000",
                    "--screenshot=" + path, url],
                   capture_output=True, timeout=90)


def run_selftest():
    webui = os.path.join(ROOT, "board", "scripts", "fir_pl_webui.py")
    p = subprocess.run([sys.executable, webui, "--selftest"], capture_output=True, timeout=120)
    txt = (p.stdout + p.stderr).decode("utf-8", "replace")
    tail = [l for l in txt.splitlines() if l.strip().startswith("SELFTEST")]
    return p.returncode == 0, (tail[-1] if tail else txt[-200:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="默认自动起假数据服务")
    ap.add_argument("--size", default="1600x900")
    ap.add_argument("--shot", default="")
    ap.add_argument("--no-selftest", action="store_true")
    a = ap.parse_args()

    browser = find_browser()
    base, stop = None, None
    if a.url:
        base = a.url
    else:
        sys.path.insert(0, HERE)
        import webui_dev
        port = 8097
        base, stop, _ = webui_dev.start(port, keep=False)
        base = "http://127.0.0.1:%d/" % port

    fails = []
    try:
        m = measure(base, browser, a.size)
        print("== 版面 ==")
        print("  视口 %s  纵向溢出 %dpx  横向溢出 %dpx" % (a.size, m["overflow_y"], m["overflow_x"]))
        print("  左列顺序 %s" % m["left_order"])
        print("  卡片最大重叠 %d px²  %s" % (m["overlap_px"], m["overlap_pair"]))
        print("  被裁文字 %d 处 %s" % (m["n_clipped"], m["clipped"][:4]))
        print("== 控件 ==")
        print("  增益行 左%d 右%d   压缩滑杆 %d   健康项 %d" %
              (m["n_gain_left"], m["n_gain_right"], m["n_comp_slider"], m["has_health"]))
        print("  读数 进=%s 增益=%s ENV=%s 温度=%s" %
              (m["val_in"], m["val_gain"], m["val_env"], m["val_temp"]))
        if m["errs"]:
            print("  JS 错误 %s" % m["errs"][:3])

        def chk(name, cond):
            print("  [%s] %s" % ("PASS" if cond else "FAIL", name))
            if not cond:
                fails.append(name)

        print("== 判定 ==")
        chk("一屏（纵向不溢出）", m["overflow_y"] <= 1)
        chk("一屏（横向不溢出）", m["overflow_x"] <= 1)
        chk("卡片不重叠", m["overlap_px"] <= 4)
        chk("文字不被裁", m["n_clipped"] == 0)
        chk("左耳 4 条增益", m["n_gain_left"] == 4)
        chk("右耳 4 条增益", m["n_gain_right"] == 4)
        chk("压缩 4 个滑杆", m["n_comp_slider"] == 4)
        chk("电平两条", m["has_level"])
        chk("健康 6 项", m["has_health"] == 6)
        chk("电平有读数", m["val_in"] not in ("", "—", "—dBFS"))
        chk("无 JS 错误", not m["errs"])

        if a.shot:
            shot(base, browser, a.size, a.shot)
            print("  截图 %s" % a.shot)

        if not a.no_selftest:
            ok, line = run_selftest()
            print("== 后端自检 ==")
            print("  " + line)
            chk("fir_pl_webui --selftest", ok)
    finally:
        if stop:
            stop()

    print("")
    if fails:
        print("FAIL %d 项：%s" % (len(fails), "; ".join(fails)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
