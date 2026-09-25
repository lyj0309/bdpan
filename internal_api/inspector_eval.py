#!/usr/bin/env python3
"""Evaluate JavaScript in the running BaiduNetdisk Electron main process."""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import urllib.request


INSPECTOR_URL = "http://127.0.0.1:9229/json/list"


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("inspector websocket closed unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock, payload, opcode=1):
    payload = payload.encode("utf-8") if isinstance(payload, str) else payload
    mask = os.urandom(4)
    size = len(payload)
    header = bytearray([0x80 | opcode])
    if size < 126:
        header.append(0x80 | size)
    elif size < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", size))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", size))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def recv_message(sock):
    parts = []
    message_opcode = None
    while True:
        first, second = recv_exact(sock, 2)
        finished = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", recv_exact(sock, 2))[0]
        elif size == 127:
            size = struct.unpack("!Q", recv_exact(sock, 8))[0]
        mask = recv_exact(sock, 4) if masked else None
        payload = recv_exact(sock, size)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 8:
            raise RuntimeError("inspector websocket closed")
        if opcode == 9:
            send_frame(sock, payload, opcode=10)
            continue
        if opcode in (1, 2):
            message_opcode = opcode
            parts = [payload]
        elif opcode == 0:
            parts.append(payload)
        else:
            continue
        if finished and message_opcode is not None:
            return b"".join(parts).decode("utf-8")


def connect_inspector():
    with urllib.request.urlopen(INSPECTOR_URL, timeout=3) as response:
        targets = json.load(response)
    if not targets:
        raise RuntimeError("no Node inspector target found")
    websocket_url = targets[0]["webSocketDebuggerUrl"]
    prefix = "ws://127.0.0.1:9229"
    if not websocket_url.startswith(prefix):
        raise RuntimeError("unexpected inspector URL")
    path = websocket_url[len(prefix):]
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock = socket.create_connection(("127.0.0.1", 9229), timeout=10)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1:9229\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    if not response.startswith(b"HTTP/1.1 101"):
        sock.close()
        raise RuntimeError("inspector websocket upgrade failed")
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    )
    if b"sec-websocket-accept: " + expected.lower() not in response.lower():
        sock.close()
        raise RuntimeError("invalid inspector websocket response")
    return sock


def evaluate(expression):
    sock = connect_inspector()
    try:
        message = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "generatePreview": False,
            },
        }
        send_frame(sock, json.dumps(message, ensure_ascii=False))
        while True:
            response = json.loads(recv_message(sock))
            if response.get("id") != 1:
                continue
            if "error" in response:
                raise RuntimeError(response["error"].get("message", "inspector error"))
            result = response["result"]
            if "exceptionDetails" in result:
                details = result["exceptionDetails"]
                exception = details.get("exception", {})
                raise RuntimeError(exception.get("description") or details.get("text", "JavaScript error"))
            remote = result.get("result", {})
            if "value" in remote:
                return remote["value"]
            if remote.get("subtype") == "null":
                return None
            return {"type": remote.get("type"), "description": remote.get("description")}
    finally:
        sock.close()


def main():
    request = json.load(sys.stdin)
    try:
        value = evaluate(request["expression"])
        json.dump({"ok": True, "value": value}, sys.stdout, ensure_ascii=False)
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.exit(1)


if __name__ == "__main__":
    main()
