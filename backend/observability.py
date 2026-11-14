# 作用：把一行文本追加写入 logs 目录下的指定文件。
# 接收：文件名（例如 "error.log"）、要写入的字符串。
# 返回：无。
def write_log_line(filename, text):
    with open("logs/" + filename, "a", encoding="utf-8") as log_file:
        log_file.write(text)
