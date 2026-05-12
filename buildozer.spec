[app]
title = 视频下载工具
package.name = videodownloader
package.domain = com.videodl
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt
source.exclude_dirs = .git,__pycache__,.claude,.github,build,dist,.buildozer,bin

version = 3.1
version.code = 4

requirements = python3==3.10.12,kivy==2.3.0,yt-dlp>=2024.12.0,requests>=2.28.0

android.arch = arm64-v8a
android.api = 33
android.minapi = 26
android.ndk = 25b
android.sdk = 33
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE
android.allow_clear_text = true
android.window_soft_input_mode = adjustResize

orientation = portrait
fullscreen = 0

log_level = 2

presplash.color = #1E90FF
presplash.filename = %(source.dir)s/presplash.png
icon.filename = %(source.dir)s/icon.png

[buildozer]
log_level = 2
