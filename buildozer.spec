[app]
title = 视频下载工具
package.name = videodownloader
package.domain = com.videodl
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt

version = 3.1
version.code = 3

# 依赖 (python-for-android 会编译这些)
requirements = python3,kivy>=2.1.0,yt-dlp,requests

# 仅打包 64 位 (现代手机都支持)
android.arch = arm64-v8a
android.allow_backup = True

# Android 版本
android.api = 33
android.minapi = 26
android.ndk = 25b
android.sdk = 33

# 权限
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

# 竖屏
orientation = portrait
fullscreen = 0

# 日志
log_level = 1
warn_on_root = 1

# p4a 配置
p4a.branch = develop
p4a.bootstrap = sdl2
p4a.hook = kivy

# 预闪屏
presplash.color = #1E90FF
presplash.filename = %(source.dir)s/presplash.png

# Android 图标
icon.filename = %(source.dir)s/icon.png

# 允许明文 HTTP (CDN 下载)
android.allow_clear_text = true

[buildozer]
log_level = 1
warn_on_root = 1
