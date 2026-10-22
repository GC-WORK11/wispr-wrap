#!/usr/bin/env python3
"""Render a QR code in the terminal with ANSI half-block chars."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qrcodegen


def render(text):
    q = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    s = q.get_size()

    def mod(x, y):
        return 0 <= x < s and 0 <= y < s and q.get_module(x, y)

    print()
    for y in range(-1, s + 1, 2):
        line = []
        for x in range(-1, s + 1):
            top, bot = mod(x, y), mod(x, y + 1)
            if top and bot:
                line.append("\033[30;40m█")
            elif top and not bot:
                line.append("\033[30;47m▀")
            elif bot and not top:
                line.append("\033[30;47m▄")
            else:
                line.append("\033[37;47m█")
        print("".join(line) + "\033[0m")
    print()


if __name__ == "__main__":
    render(sys.argv[1])
