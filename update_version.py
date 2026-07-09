"""更新 version.txt 为 v3.4.0（UTF-16 LE 格式）"""
with open("version.txt", "w", encoding="utf-16-le") as f:
    f.write("v3.4.0\r\n")
print("version.txt updated to v3.4.0")
