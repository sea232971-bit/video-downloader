[app]
title = 视频下载工具
package.name = videodownloader
package.domain = com.videodl
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt

version = 3.1
version.code = 3

requirements = python3,kivy,yt-dlp,requests

android.arch = arm64-v8a
android.api = 33
android.minapi = 26
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

orientation = portrait
fullscreen = 0

log_level = 2

presplash.color = #1E90FF
presplash.filename = %(source.dir)s/presplash.png
icon.filename = %(source.dir)s/icon.png

android.allow_clear_text = true

[buildozer]
log_level = 2
