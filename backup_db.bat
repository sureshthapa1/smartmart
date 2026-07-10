@echo off
cd /d "%~dp0"
set TS=%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TS=%TS: =0%
copy "instance\smart_mart_dev.db" "instance\backups\smart_mart_dev_%TS%.db"
echo Backup saved to instance\backups\smart_mart_dev_%TS%.db
