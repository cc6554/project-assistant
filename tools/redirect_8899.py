"""8899 → 17689 重定向（后台无窗口运行）。

历史遗留：用户浏览器一直用 http://127.0.0.1:8899/ 作为应用入口，
但 8899 早已没有真实服务，页面是旧缓存。本服务把 8899 所有请求
302 重定向到 sidecar 静态服务 http://127.0.0.1:17689/（应用最新前端）。
"""
import http.server
import socketserver


class RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:17689/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, *args):  # 静默日志
        pass


class Server(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("127.0.0.1", 8899), RedirectHandler) as srv:
        srv.serve_forever()
