"""Move files between this PC and the PYNQ board over the Jupyter HTTP API.

No SSH, no shared folder, no extra service -- the Jupyter server that PYNQ
already runs (port 9090) has a file API, and that is enough.

Why this is not just `curl`:
    Jupyter rejects every non-GET request that does not carry an _xsrf token.
    The token is handed out on the login page and must be replayed BOTH as a
    cookie and as an X-XSRFToken header. Missing it gives a bare 403 with no
    hint about what is wrong.

Binary files:
    `put` / `get` send text. A .bit is 4 MB of binary -- sending it as text
    would corrupt it, so use `putb` / `getb`, which base64 the payload and set
    "format": "base64" in the contents API.

Usage:
    python pynq_jupyter_files.py ls
    python pynq_jupyter_files.py put  <local-file> <board-path>     # 文本
    python pynq_jupyter_files.py get  <board-path> <local-file>     # 文本
    python pynq_jupyter_files.py putb <local-file> <board-path>     # 二进制
    python pynq_jupyter_files.py getb <board-path> <local-file>     # 二进制

Verified 2026-09-27 against PYNQ-Z2 v2.7.0, Jupyter 5.x, board at 192.168.2.99.

Note: <board-path> is relative to the Jupyter root, which is
/home/xilinx/jupyter_notebooks -- NOT /home/xilinx. To write outside the root,
upload first and `mv` it from the serial console.
"""
import argparse
import base64
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

BASE = "http://192.168.2.99:9090"
PASSWORD = "xilinx"


class Board:
    def __init__(self, base=BASE, password=PASSWORD):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.xsrf = self._login(password)

    def _login(self, password):
        html = self.opener.open(self.base + "/login").read().decode("utf-8", "replace")
        m = re.search(r'name="_xsrf"\s+value="([^"]+)"', html)
        if not m:
            raise RuntimeError("no _xsrf field on the login page -- is this a Jupyter server?")
        xsrf = m.group(1)
        body = urllib.parse.urlencode({"_xsrf": xsrf, "password": password}).encode()
        self.opener.open(self.base + "/login", body)
        return xsrf

    def _url(self, path):
        return self.base + "/api/contents/" + urllib.parse.quote(path)

    def ls(self, path=""):
        d = json.loads(self.opener.open(self._url(path)).read())
        if d.get("type") != "directory":
            return [d["path"]]
        return sorted(e["name"] + ("/" if e["type"] == "directory" else "")
                      for e in d["content"])

    def get(self, path):
        d = json.loads(self.opener.open(self._url(path)).read())
        c = d["content"]
        return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False, indent=1)

    def put(self, path, text):
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps({"type": "file", "format": "text", "content": text}).encode(),
            method="PUT")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-XSRFToken", self.xsrf)
        return json.loads(self.opener.open(req).read())["path"]

    def get_bytes(self, path):
        d = json.loads(self.opener.open(self._url(path)).read())
        if d.get("format") != "base64":
            raise RuntimeError("板子上这个文件不是二进制格式：%s" % d.get("format"))
        return base64.b64decode(d["content"])

    def put_bytes(self, path, data):
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps({"type": "file", "format": "base64",
                             "content": base64.b64encode(data).decode()}).encode(),
            method="PUT")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-XSRFToken", self.xsrf)
        return json.loads(self.opener.open(req).read())["path"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["ls", "put", "get", "putb", "getb"])
    ap.add_argument("a", nargs="?", default="")
    ap.add_argument("b", nargs="?")
    args = ap.parse_args()

    board = Board()

    if args.action == "ls":
        for name in board.ls(args.a):
            print(name)
    elif args.action == "put":
        text = open(args.a, encoding="utf-8").read()
        print("put ->", board.put(args.b, text))
    elif args.action == "putb":
        data = open(args.a, "rb").read()
        print("putb -> %s  (%d 字节)" % (board.put_bytes(args.b, data), len(data)))
    elif args.action == "getb":
        data = board.get_bytes(args.a)
        open(args.b, "wb").write(data)
        print("getb -> %s  (%d 字节)" % (args.b, len(data)))
    else:
        text = board.get(args.a)
        if args.b:
            open(args.b, "w", encoding="utf-8", newline="\n").write(text)
            print("get ->", args.b, "(%d bytes)" % len(text))
        else:
            sys.stdout.write(text)


if __name__ == "__main__":
    main()
