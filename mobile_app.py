#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手机版视频下载 APP (Kivy GUI)
支持: 抖音 · Twitter/X · YouTube · BiliBili · Instagram · 小红书 · 微博
默认保存至 /sdcard/DCIM/VideoDownload

打包: buildozer android debug
"""

import os
import sys
import re
import json
import time
import math
import threading
import tempfile
from pathlib import Path

import requests
import yt_dlp
from yt_dlp.jsinterp import js_number_to_string

# ── Android 权限 ──────────────────────────────────────
try:
    from android.permissions import request_permissions, Permission
    HAS_ANDROID = True
except ImportError:
    HAS_ANDROID = False

# ── Kivy ─────────────────────────────────────────────
os.environ['KIVY_LOG_MODE'] = 'PYTHON'
# 避免生成 .kivy 日志文件
try:
    import kivy
    kivy.require('2.1.0')
except ImportError:
    pass

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.checkbox import CheckBox
from kivy.uix.progressbar import ProgressBar
from kivy.uix.gridlayout import GridLayout
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.utils import platform

# ── 平台判断 ──────────────────────────────────────────
IS_ANDROID = platform == 'android'

# 默认保存路径
if IS_ANDROID:
    DEFAULT_SAVE_PATH = '/sdcard/DCIM/VideoDownload'
else:
    DEFAULT_SAVE_PATH = str(Path.home() / 'Downloads' / 'VideoDownload')

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / 'mobile_config.json'

DEFAULT_CONFIG = {
    'save_path': DEFAULT_SAVE_PATH,
    'audio_only': False,
    'cookie_file': '',
    'proxy': '',
    'quality': 'best',
}


# ── 工具函数 ──────────────────────────────────────────
def load_config():
    try:
        if CONFIG_FILE.exists():
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(json.loads(CONFIG_FILE.read_text('utf-8')))
            return cfg
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), 'utf-8')
    except Exception:
        pass


def check_ffmpeg():
    import shutil
    if shutil.which('ffmpeg'):
        return True
    for loc in ['/data/data/com.termux/files/usr/bin/ffmpeg']:
        if os.path.isfile(loc):
            return True
    return False


def find_ffmpeg():
    import shutil
    result = shutil.which('ffmpeg')
    if result:
        return result
    p = '/data/data/com.termux/files/usr/bin/ffmpeg'
    if os.path.isfile(p):
        return p
    return None


# ── 平台定义 ──────────────────────────────────────────
PLATFORM_INFO = {
    'douyin': {
        'name': '抖音', 'icon': '🎵',
        'patterns': [r'(v\.)?douyin\.com', r'iesdouyin\.com'],
    },
    'twitter': {
        'name': 'Twitter/X', 'icon': '🐦',
        'patterns': [r'twitter\.com', r'x\.com', r'fxtwitter\.com', r'vxtwitter\.com', r'twitfix\.com'],
    },
    'youtube': {
        'name': 'YouTube', 'icon': '▶️',
        'patterns': [r'youtube\.com', r'youtu\.be'],
    },
    'bilibili': {
        'name': 'BiliBili', 'icon': '📺',
        'patterns': [r'bilibili\.com', r'b23\.tv'],
    },
    'instagram': {
        'name': 'Instagram', 'icon': '📷',
        'patterns': [r'instagram\.com'],
    },
    'xiaohongshu': {
        'name': '小红书', 'icon': '📕',
        'patterns': [r'xiaohongshu\.com', r'xhslink\.com'],
    },
    'weibo': {
        'name': '微博', 'icon': '💬',
        'patterns': [r'weibo\.com', r'weibo\.cn'],
    },
}


def detect_platform(url):
    url = url.strip()
    for name, info in PLATFORM_INFO.items():
        for p in info['patterns']:
            if re.search(p, url, re.I):
                return name
    return 'unknown'


# ── Twitter/X 解析 ──────────────────────────────────────
def _extract_tweet_id(url):
    m = re.search(r'(?:twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com|twitfix\.com)'
                  r'(?:/i)?(?:/[^/]+)?/status(?:es)?/(\d+)', url, re.I)
    return m.group(1) if m else None


def parse_twitter_video(url):
    tweet_id = _extract_tweet_id(url)
    if not tweet_id:
        return None
    try:
        resp = requests.get(
            f'https://api.fxtwitter.com/status/{tweet_id}',
            headers={'User-Agent': 'Mozilla/5.0 (compatible; VideoDownloader/2.0)'},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            tweet = data.get('tweet', {})
            if tweet:
                author_info = tweet.get('author', {})
                author = author_info.get('name', '')
                screen_name = author_info.get('screen_name', '')
                media = tweet.get('media', {}) or {}
                videos = media.get('videos') or media.get('all') or []
                if not videos:
                    photos = media.get('photos') or media.get('all') or []
                    videos = [p for p in photos if p.get('type') == 'video']
                mp4_variants = []
                m3u8_url = None
                for v in videos:
                    v_url = v.get('url', '')
                    if not v_url:
                        continue
                    if '.m3u8' in v_url or v.get('format') == 'm3u8':
                        m3u8_url = v_url
                    else:
                        mp4_variants.append({
                            'url': v_url, 'bitrate': v.get('bitrate', 0) or 0,
                            'quality': v.get('quality', ''),
                            'width': v.get('width', 0) or 0,
                            'height': v.get('height', 0) or 0,
                        })
                if mp4_variants or m3u8_url:
                    mp4_variants.sort(key=lambda x: (x['height'] or x['bitrate'] or 0), reverse=True)
                    text = tweet.get('text', '') or ''
                    title = text[:60].replace('\n', ' ') if text else f'Tweet_{tweet_id}'
                    duration = 0
                    if videos:
                        duration = (videos[0].get('duration', 0) or 0)
                    return {
                        'tweet_id': tweet_id, 'title': title,
                        'author': f'{author} (@{screen_name})' if author else screen_name,
                        'text': text, 'mp4_variants': mp4_variants, 'm3u8_url': m3u8_url,
                        'duration_ms': duration * 1000 if duration else 0,
                    }
    except Exception:
        pass
    return None


# ── 抖音解析 ────────────────────────────────────────────
_DOUYIN_UA = ('Mozilla/5.0 (Linux; Android 10; Pixel 3) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/120.0.0.0 Mobile Safari/537.36')

_DOUYIN_URL_PATTERNS = [
    re.compile(r'(?:www\.)?douyin\.com/(?:video|note)/(\d+)', re.I),
    re.compile(r'(?:www\.)?douyin\.com/user/[^?]+.*?modal_id=(\d+)', re.I),
    re.compile(r'(?:www\.)?iesdouyin\.com/share/video/(\d+)', re.I),
    re.compile(r'v\.douyin\.com/(\w+)', re.I),
]


def _fetch_douyin_tokens(on_log=None):
    def _log(msg):
        if on_log:
            on_log(msg)
    tokens = {}
    for attempt in range(2):
        try:
            resp = requests.get(
                'https://api.douyin.wtf/api/douyin/web/generate_ttwid',
                headers={'User-Agent': _DOUYIN_UA, 'Accept': 'application/json'},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 200:
                    ttwid = data.get('data', {}).get('ttwid', '')
                    if ttwid:
                        tokens['ttwid'] = ttwid
                        break
            if attempt == 0:
                _log('ttwid 获取失败，重试...')
                time.sleep(1)
        except Exception:
            if attempt == 0:
                time.sleep(1)
    for attempt in range(2):
        try:
            resp = requests.get(
                'https://api.douyin.wtf/api/douyin/web/generate_real_msToken',
                headers={'User-Agent': _DOUYIN_UA, 'Accept': 'application/json'},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 200:
                    ms = data.get('data', {}).get('msToken', '')
                    if ms:
                        tokens['msToken'] = ms
                        break
            if attempt == 0:
                time.sleep(1)
        except Exception:
            if attempt == 0:
                time.sleep(1)
    return tokens if tokens else None


def _resolve_douyin_short(url):
    try:
        resp = requests.head(url, headers={'User-Agent': _DOUYIN_UA},
                            allow_redirects=True, timeout=10)
        if resp.url != url and 'douyin.com' in resp.url:
            return resp.url
    except Exception:
        pass
    try:
        resp = requests.get(url, headers={'User-Agent': _DOUYIN_UA},
                           allow_redirects=True, timeout=10)
        if resp.url != url and 'douyin.com' in resp.url:
            return resp.url
        m = re.search(r'href="(https?://[^"]+?douyin\.com[^"]*)"', resp.text[:3000], re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _extract_douyin_video_id(url):
    for pat in _DOUYIN_URL_PATTERNS:
        m = pat.search(url)
        if m:
            gid = m.group(1)
            if pat.pattern.startswith(r'v\.'):
                full = _resolve_douyin_short(url)
                if full:
                    return _extract_douyin_video_id(full)
                return gid
            return gid
    return None


def parse_douyin_video(url, on_log=None):
    def _log(msg):
        if on_log:
            on_log(msg)
    video_id = _extract_douyin_video_id(url)
    if not video_id:
        _log('无法从链接中提取视频 ID')
        return None
    tokens = _fetch_douyin_tokens(on_log=on_log)
    if not tokens:
        _log('douyin.wtf 令牌接口不可用')
        return None
    ttwid = tokens.get('ttwid', '')
    msToken = tokens.get('msToken', '')
    cookies = {'ttwid': ttwid, 'msToken': msToken}

    douyin_api = (
        f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
        f'?aweme_id={video_id}&aid=6383&device_platform=web'
        f'&browser_language=zh-CN&browser_name=Chrome&browser_online=true'
        f'&browser_platform=Win32&browser_version=120.0.0.0'
        f'&engine_name=Blink&engine_version=120.0.0.0'
    )
    a_bogus = None
    try:
        resp = requests.get(
            'https://api.douyin.wtf/api/douyin/web/generate_a_bogus',
            params={'url': douyin_api, 'user_agent': _DOUYIN_UA},
            headers={'User-Agent': _DOUYIN_UA, 'Accept': 'application/json'},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == 200:
                a_bogus = data.get('data', {}).get('a_bogus', '')
    except Exception:
        pass
    if not a_bogus:
        _log('无法生成 A-Bogus 签名')
        return None

    data = None
    for attempt in range(3):
        try:
            full_url = f'{douyin_api}&a_bogus={a_bogus}'
            headers = {
                'User-Agent': _DOUYIN_UA, 'Referer': 'https://www.douyin.com/',
                'Accept': 'application/json',
            }
            resp = requests.get(full_url, headers=headers, cookies=cookies, timeout=20)
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                break
            elif resp.status_code == 200 and not resp.text.strip():
                tokens = _fetch_douyin_tokens()
                if tokens:
                    cookies = {'ttwid': tokens.get('ttwid', ''), 'msToken': tokens.get('msToken', '')}
                continue
        except Exception:
            continue
    if not data:
        _log('抖音 API 未返回视频数据')
        return None

    aweme = data.get('aweme_detail', {}) or {}
    if not aweme:
        _log('响应中缺少视频详情')
        return None
    video = aweme.get('video', {})
    if not video:
        _log('视频数据为空（可能已删除或私密）')
        return None

    mp4_variants = []
    download_urls = video.get('download_addr', {}).get('url_list', [])
    for u in download_urls:
        if u:
            mp4_variants.append({'url': u, 'bitrate': 0, 'quality': '下载地址'})
            break

    if not mp4_variants:
        play_urls = video.get('play_addr', {}).get('url_list', [])
        for u in play_urls:
            if u:
                nwm = u.replace('playwm', 'play')
                mp4_variants.append({'url': nwm, 'bitrate': 0, 'quality': '无水印'})
                break

    for br in video.get('bit_rate', []):
        addr = br.get('play_addr', {}) or {}
        urls = addr.get('url_list', [])
        if urls:
            nwm = urls[0].replace('playwm', 'play')
            h = br.get('height', 0) or 0
            w = br.get('width', 0) or 0
            fps = br.get('FPS', 0) or 0
            label = f'{h}p' if h else f'{w}x{h}' if w else ''
            if fps:
                label += f' {fps}fps'
            mp4_variants.append({'url': nwm, 'bitrate': 0, 'quality': label})

    if not mp4_variants:
        _log('未找到可下载的视频流')
        return None

    author_info = aweme.get('author', {}) or {}
    author = (author_info.get('nickname', '') or author_info.get('unique_id', '') or '未知')
    title = (aweme.get('desc', '') or aweme.get('item_title', '')
             or f'Douyin_{video_id}')[:80].replace('\n', ' ')
    duration = int(aweme.get('duration', 0) or 0)
    return {
        'video_id': video_id, 'title': title, 'author': author,
        'mp4_variants': mp4_variants, 'm3u8_url': '',
        'duration_ms': duration, 'cookies': cookies,
    }


# ── 下载引擎 ────────────────────────────────────────────
class DownloadEngine:
    def __init__(self, proxy=''):
        self._cancel = False
        self._proxy = proxy

    def cancel(self):
        self._cancel = True

    def _direct_http_download(self, url, save_path, filename, referer='',
                              cookies=None, progress_cb=None):
        filepath = os.path.join(save_path, filename)
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer or 'https://www.google.com/',
            }
            proxies = {'http': self._proxy, 'https': self._proxy} if self._proxy else None
            resp = requests.get(url, headers=headers, cookies=cookies,
                              proxies=proxies, stream=True, timeout=30)
            if resp.status_code != 200:
                return None
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 1024 * 1024 * 2
            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if self._cancel:
                        f.close()
                        try:
                            os.remove(filepath)
                        except Exception:
                            pass
                        return None
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(downloaded / total * 100, downloaded, total)
            if progress_cb:
                progress_cb(100, downloaded, total)
            return filepath
        except Exception:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return None

    def download(self, url, save_path, quality='best', audio_only=False,
                 cookie_file='', subtitles=False, progress_cb=None, log_cb=None):
        platform = detect_platform(url)
        os.makedirs(save_path, exist_ok=True)

        def log(msg):
            if log_cb:
                log_cb(msg)

        pi = PLATFORM_INFO.get(platform)
        name = f'{pi["icon"]} {pi["name"]}' if pi else '未知平台'
        log(f'平台: {name}')
        log(f'链接: {url[:60]}...' if len(url) > 60 else f'链接: {url}')

        douyin_token_cookie = None

        # ── 抖音直连 ──
        if platform == 'douyin':
            log('🔍 直连抖音 API 解析...')
            video_info = parse_douyin_video(url, on_log=log)
            if video_info:
                best_mp4 = video_info['mp4_variants'][0] if video_info['mp4_variants'] else None
                if best_mp4:
                    safe_title = re.sub(r'[\\/*?:"<>|]', '', video_info['title'])
                    vid = video_info.get('video_id', '') or 'douyin'
                    filename = f'{safe_title}_{vid}.mp4'
                    log(f'✅ 解析成功: {video_info["title"]}')
                    log(f'   作者: {video_info["author"]}')
                    log('⬇ 下载中...')
                    filepath = self._direct_http_download(
                        best_mp4['url'], save_path, filename,
                        referer='https://www.douyin.com/',
                        cookies=video_info.get('cookies', {}),
                        progress_cb=progress_cb,
                    )
                    if filepath:
                        size = os.path.getsize(filepath)
                        return True, {
                            'title': video_info['title'], 'path': filepath,
                            'size': size, 'platform': 'douyin',
                        }
                    log('HTTP 下载失败，回退 yt-dlp...')
                else:
                    log('未找到 MP4 流')
            else:
                log('直连解析失败，回退 yt-dlp...')

            if not cookie_file:
                log('🔑 尝试获取令牌...')
                tokens = _fetch_douyin_tokens(on_log=log)
                if tokens:
                    try:
                        fd, path = tempfile.mkstemp(suffix='.txt', prefix='dy_cookies_')
                        with os.fdopen(fd, 'w') as f:
                            f.write('# Netscape HTTP Cookie File\n\n')
                            for n, v in tokens.items():
                                f.write(f'.douyin.com\tTRUE\t/\tTRUE\t0\t{n}\t{v}\n')
                                f.write(f'.iesdouyin.com\tTRUE\t/\tTRUE\t0\t{n}\t{v}\n')
                        douyin_token_cookie = path
                        log(f'✅ 已获取令牌')
                    except Exception:
                        log('令牌写入失败')
                else:
                    log('令牌获取失败')

        # ── Twitter 直连 ──
        if platform == 'twitter':
            log('🔍 直连 Twitter API 解析...')
            video_info = parse_twitter_video(url)
            if video_info:
                best_mp4 = video_info['mp4_variants'][0] if video_info['mp4_variants'] else None
                if best_mp4:
                    safe_title = re.sub(r'[\\/*?:"<>|]', '', video_info['title'])
                    filename = f'{safe_title}_{video_info["tweet_id"]}.mp4'
                    log(f'✅ 解析成功: {video_info["title"]}')
                    log(f'   作者: {video_info["author"]}')
                    log('⬇ 下载中...')
                    filepath = self._direct_http_download(
                        best_mp4['url'], save_path, filename, referer=url,
                        progress_cb=progress_cb,
                    )
                    if filepath:
                        size = os.path.getsize(filepath)
                        return True, {
                            'title': video_info['title'], 'path': filepath,
                            'size': size, 'platform': 'twitter',
                        }
                    log('HTTP 下载失败，回退 yt-dlp...')
            else:
                log('直连解析失败，回退 yt-dlp...')

        # ── yt-dlp ──
        if douyin_token_cookie:
            cookie_file = douyin_token_cookie

        opts = {
            'outtmpl': os.path.join(save_path, '%(title)s.%(ext)s'),
            'quiet': True, 'no_warnings': True,
            'ignoreerrors': True, 'retries': 5, 'fragment_retries': 5,
            'extract_flat': False,
        }
        if cookie_file and os.path.isfile(cookie_file):
            opts['cookiefile'] = cookie_file
        if self._proxy:
            opts['proxy'] = self._proxy
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            opts['ffmpeg_location'] = ffmpeg_path

        if platform == 'douyin':
            opts.update({
                'format': 'bestvideo+bestaudio/best',
                'http_headers': {
                    'User-Agent': _DOUYIN_UA,
                    'Referer': 'https://www.douyin.com/',
                },
            })
        elif platform == 'twitter':
            opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        elif platform == 'youtube':
            if audio_only:
                opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3', 'preferredquality': '192',
                    }],
                })
            else:
                fmt_map = {'best': 'bestvideo+bestaudio/best', '1080p': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'}
                opts['format'] = fmt_map.get(quality, fmt_map['best'])
        else:
            opts['format'] = 'bestvideo+bestaudio/best'

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if self._cancel:
                    return False, '已取消'
                if info is None:
                    return False, '无法解析视频'
                if info.get('_type') == 'playlist' or info.get('entries'):
                    entries = info.get('entries', [])
                    info = next((e for e in (entries or []) if e), None)
                    if not info:
                        return False, '无视频内容'

                title = info.get('title', '未知') or '未知'
                fp = ydl.prepare_filename(info)
                if not os.path.isfile(fp):
                    for e in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                        p = fp.rsplit('.', 1)[0] + f'.{e}'
                        if os.path.isfile(p):
                            fp = p
                            break
                size = os.path.getsize(fp) if os.path.isfile(fp) else 0
                log(f'✅ 完成: {title}')
                return True, {'title': title, 'path': fp, 'size': size, 'platform': platform}
        except Exception as e:
            if 'cancel' in str(e).lower():
                return False, '已取消'
            return False, str(e)
        finally:
            if douyin_token_cookie:
                try:
                    os.unlink(douyin_token_cookie)
                except Exception:
                    pass


# ── Kivy UI ────────────────────────────────────────────
KV_STRING = '''
#:import platform kivy.utils.platform

<RootWidget>:
    orientation: 'vertical'
    padding: dp(12)
    spacing: dp(8)

    # ── 标题 ──
    Label:
        text: '📥 视频下载工具'
        font_size: dp(20)
        size_hint_y: None
        height: dp(44)
        color: 0.2, 0.6, 0.9, 1
        bold: True

    # ── URL 输入 ──
    BoxLayout:
        size_hint_y: None
        height: dp(42)
        spacing: dp(6)
        TextInput:
            id: url_input
            hint_text: '粘贴视频链接（每行一个）...'
            multiline: False
            font_size: dp(15)
        Button:
            text: '📋'
            size_hint_x: None
            width: dp(48)
            on_release: root.paste_clipboard()

    # ── 设置行 ──
    BoxLayout:
        size_hint_y: None
        height: dp(42)
        spacing: dp(8)
        Spinner:
            id: quality_spinner
            text: 'best'
            values: ['best', '1080p', '720p', '480p']
            size_hint_x: 0.35
            font_size: dp(14)
        CheckBox:
            id: audio_only
            size_hint_x: None
            width: dp(36)
        Label:
            text: '仅音频'
            size_hint_x: None
            width: dp(56)
            font_size: dp(13)
            color: 0.7, 0.7, 0.7, 1
        Label:
            id: platform_label
            text: ''
            font_size: dp(13)
            color: 0.5, 0.5, 0.5, 1

    # ── 保存路径 ──
    BoxLayout:
        size_hint_y: None
        height: dp(32)
        spacing: dp(4)
        Label:
            text: '保存:'
            size_hint_x: None
            width: dp(44)
            font_size: dp(12)
            color: 0.5, 0.5, 0.5, 1
        Label:
            id: save_path_label
            text: root.save_path
            font_size: dp(11)
            color: 0.5, 0.5, 0.5, 1
            halign: 'left'
            text_size: self.size

    # ── 下载按钮 ──
    Button:
        id: download_btn
        text: '⬇  开 始 下 载'
        size_hint_y: None
        height: dp(50)
        font_size: dp(17)
        background_color: 0.2, 0.6, 0.9, 1
        color: 1, 1, 1, 1
        on_release: root.start_download()

    # ── 进度条 ──
    ProgressBar:
        id: progress_bar
        max: 100
        value: 0
        size_hint_y: None
        height: dp(8)

    Label:
        id: progress_label
        text: '就绪'
        size_hint_y: None
        height: dp(22)
        font_size: dp(12)
        color: 0.5, 0.5, 0.5, 1

    # ── 日志 ──
    ScrollView:
        do_scroll_x: False
        bar_width: dp(6)
        Label:
            id: log_label
            text: '支持: 抖音 · Twitter · YouTube · BiliBili\\nInstagram · 小红书 · 微博\\n\\n粘贴链接后自动识别平台'
            font_size: dp(13)
            color: 0.75, 0.75, 0.75, 1
            markup: True
            size_hint_y: None
            height: self.texture_size[1]
            text_size: self.width, None
            valign: 'top'
            halign: 'left'
'''


class RootWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cfg = load_config()
        self.save_path = self.cfg.get('save_path', DEFAULT_SAVE_PATH)
        self.downloading = False
        self.engine = None
        self.thread = None

        # 确保目录存在
        try:
            os.makedirs(self.save_path, exist_ok=True)
        except Exception:
            pass

        # 绑定 URL 变化
        self.ids.url_input.bind(text=self.on_url_change)

    def paste_clipboard(self):
        try:
            from kivy.core.clipboard import Clipboard
            text = Clipboard.get('text/plain') or ''
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='replace')
            text = text.strip()
            if text:
                self.ids.url_input.text = text
                self.log('已从剪贴板粘贴链接')
        except Exception:
            self.log('读取剪贴板失败')

    def on_url_change(self, instance, value):
        if not value:
            self.ids.platform_label.text = ''
            return
        platform = detect_platform(value.strip())
        pi = PLATFORM_INFO.get(platform)
        if pi:
            self.ids.platform_label.text = f'{pi["icon"]} {pi["name"]}'
        else:
            self.ids.platform_label.text = '未知平台'

    def log(self, msg):
        current = self.ids.log_label.text
        timestamp = time.strftime('%H:%M:%S')
        self.ids.log_label.text = current + f'\n[{timestamp}] {msg}'
        # 自动滚动到底部
        Clock.schedule_once(lambda dt: setattr(
            self.ids.log_label, 'height', self.ids.log_label.texture_size[1]), 0.05)

    def update_progress(self, pct, downloaded, total):
        def _update(dt):
            self.ids.progress_bar.value = pct
            if total > 0:
                mb = downloaded / 1048576
                total_mb = total / 1048576
                self.ids.progress_label.text = f'{pct:.1f}%  |  {mb:.1f} / {total_mb:.1f} MB'
            else:
                self.ids.progress_label.text = f'{pct:.1f}%'
        Clock.schedule_once(_update, 0)

    def start_download(self):
        if self.downloading:
            # 取消
            self.downloading = False
            if self.engine:
                self.engine.cancel()
            self.ids.download_btn.text = '⬇  开 始 下 载'
            self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
            self.log('⏹ 已取消')
            return

        url = self.ids.url_input.text.strip()
        if not url or not url.startswith(('http://', 'https://')):
            self.log('请粘贴有效的视频链接')
            return

        self.downloading = True
        self.ids.download_btn.text = '⏹  取 消 下 载'
        self.ids.download_btn.background_color = (0.9, 0.3, 0.3, 1)
        self.ids.progress_bar.value = 0
        self.ids.progress_label.text = '准备...'

        quality = self.ids.quality_spinner.text
        audio = self.ids.audio_only.active

        self.engine = DownloadEngine()
        self.thread = threading.Thread(
            target=self._download_thread,
            args=(url, quality, audio),
            daemon=True,
        )
        self.thread.start()

    def _download_thread(self, url, quality, audio):
        try:
            ok, data = self.engine.download(
                url, self.save_path,
                quality=quality, audio_only=audio,
                progress_cb=self.update_progress,
                log_cb=self.log,
            )
            if ok:
                Clock.schedule_once(lambda dt: self._on_success(data), 0)
            else:
                Clock.schedule_once(lambda dt: self._on_fail(data), 0)
        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_fail(str(e)), 0)

    def _on_success(self, data):
        self.downloading = False
        self.ids.download_btn.text = '⬇  开 始 下 载'
        self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
        self.ids.progress_bar.value = 100
        mb = data.get('size', 0) / 1048576
        self.ids.progress_label.text = f'✅ {data.get("title", "")} ({mb:.1f} MB)'
        self.log(f'✅ 下载完成: {data.get("title", "")}')
        self.log(f'   大小: {mb:.1f} MB')
        self.log(f'   位置: {data.get("path", "")}')

        # Android: 触发媒体扫描
        fp = data.get('path', '')
        if IS_ANDROID and fp and os.path.isfile(fp):
            try:
                import subprocess
                subprocess.run(['am', 'broadcast', '-a',
                              'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
                              '-d', f'file://{fp}'], timeout=5, capture_output=True)
                self.log('已触发相册扫描')
            except Exception:
                pass

    def _on_fail(self, error):
        self.downloading = False
        self.ids.download_btn.text = '⬇  开 始 下 载'
        self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
        self.ids.progress_bar.value = 0
        self.ids.progress_label.text = f'❌ {error}'
        self.log(f'❌ 下载失败: {error}')
        if '抖音' in error or 'douyin' in str(error).lower():
            self.log('💡 提示: 抖音视频需要登录Cookie，请在电脑端导出cookies.txt放到手机')


class VideoDownloaderApp(App):
    def build(self):
        self.title = '视频下载工具'
        self.icon = 'icon.png'
        return RootWidget()

    def on_start(self):
        # 请求 Android 存储权限
        if IS_ANDROID and HAS_ANDROID:
            try:
                request_permissions([Permission.WRITE_EXTERNAL_STORAGE,
                                    Permission.READ_EXTERNAL_STORAGE])
            except Exception:
                pass


if __name__ == '__main__':
    VideoDownloaderApp().run()
