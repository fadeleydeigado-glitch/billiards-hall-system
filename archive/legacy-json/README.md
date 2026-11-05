# 旧 JSON 版本归档

此目录保存从 JSON 文件存储迁移到 MySQL 时使用的原始数据、迁移脚本和迁移一致性测试。
它不是当前应用的运行时依赖，当前后端和前端都不会读取这里的文件。

## 内容

- `data/`：迁移前的 5 个 JSON 数据文件。
- `migrate_json_to_mysql.py`：一次性迁移脚本，会删除并重建目标表。
- `test_mysql_read.py`：迁移后用于逐项比对 MySQL 读取结果和旧 JSON 的只读测试。

## 恢复旧后端代码

JSON 运行时代的完整后端代码保留在 Git 标签 `pre-cleanup-2026-11-05` 中，
没有在当前工作区复制第二套可执行代码。查看旧文件示例：

```powershell
git show pre-cleanup-2026-11-05:io_layer.py
git show pre-cleanup-2026-11-05:business_layer.py
```

## 重新运行迁移脚本

迁移脚本会删除并重建 `billiards_hall` 中的表。只应在可丢弃的测试库中使用：

```powershell
cd archive/legacy-json
python migrate_json_to_mysql.py
```
