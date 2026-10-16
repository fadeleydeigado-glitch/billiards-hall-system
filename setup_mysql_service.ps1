# MySQL root 密码：不在脚本中硬编码；请在本地通过环境变量 MYSQL_PASSWORD（或 .env 文件）配置。
# 切勿把真实密码提交到 GitHub。
$ErrorActionPreference = 'Stop'
try {
$bin = 'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe'
$data = 'C:\MySQLData'
$ini = Join-Path $data 'my.ini'
if (-not (Test-Path -LiteralPath $data)) { throw 'C:\MySQLData does not exist' }
if (-not (Test-Path -LiteralPath $ini)) {
    @('[mysqld]','basedir=C:/Program Files/MySQL/MySQL Server 8.4','datadir=C:/MySQLData','port=3306','mysqlx=0','character-set-server=utf8mb4','collation-server=utf8mb4_0900_ai_ci','[client]','port=3306') | Set-Content -LiteralPath $ini -Encoding ASCII
}
& $bin --remove MySQL84 2>$null
& $bin --install MySQL84 --defaults-file="$ini"
Start-Service MySQL84
Start-Sleep -Seconds 3
Get-Service MySQL84 | Select-Object Status, Name, DisplayName
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
Read-Host '按回车关闭此窗口'
