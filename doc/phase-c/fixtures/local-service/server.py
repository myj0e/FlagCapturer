"""Known synthetic TCP protocol; no challenge credentials or host access."""

import socketserver


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(2)
        request = self.rfile.readline(128)
        if request == b"solve\n":
            self.wfile.write(b"CTFBOT_SYNTHETIC{local-service}\n")
        elif request:
            self.wfile.write(b"send solve followed by newline\n")


if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("0.0.0.0", 31337), Handler) as server:
        server.serve_forever()
