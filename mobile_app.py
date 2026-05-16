#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手机版视频下载 APP (Kivy GUI) v3.1
支持: 抖音 · Twitter/X · YouTube · BiliBili · Instagram · 小红书 · 微博
默认保存至 /sdcard/DCIM/VideoDownload（Android）或 ~/Downloads/VideoDownload（桌面）

打包: buildozer android debug
"""

import os
import re
import json
import time
import math
import tempfile
import threading
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
try:
    import kivy
    kivy.require('2.1.0')
except ImportError:
    pass

from kivy.app import App
from kivy.core.text import LabelBase
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.checkbox import CheckBox
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.utils import platform
from kivy.properties import StringProperty
from kivy.lang import Builder

# ── 注册中文字体 ─────────────────────────────────────
FONT_FILE = str(Path(__file__).parent / 'chinese_font.ttc')
if os.path.exists(FONT_FILE):
    LabelBase.register('Roboto', FONT_FILE)
    LabelBase.register('DroidSans', FONT_FILE)

# ── 平台判断 ──────────────────────────────────────────
IS_ANDROID = platform == 'android'

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
    'download_history': [],
}


# ── 全局 HTTP Session（trust_env=False 防止读取系统/环境代理）──
def _http_session():
    """创建不读取系统代理和环境变量的 requests Session"""
    s = requests.Session()
    s.trust_env = False
    return s


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
    if IS_ANDROID:
        if os.path.isfile('/data/data/com.termux/files/usr/bin/ffmpeg'):
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
    """解析 Twitter/X 视频信息（无需登录）
    策略: 1. api.fxtwitter.com  2. cdn.syndication.twimg.com（备用）
    """
    tweet_id = _extract_tweet_id(url)
    if not tweet_id:
        return None

    # ── 策略1: fxtwitter API ──
    try:
        resp = _http_session().get(
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

    # ── 策略2: Syndication API（备用）──
    try:
        num = (int(tweet_id) / 1e15) * math.pi
        token = js_number_to_string(num, 36).translate(str.maketrans('', '', '.0'))
        resp = _http_session().get(
            'https://cdn.syndication.twimg.com/tweet-result',
            headers={'User-Agent': 'Googlebot'},
            params={'id': tweet_id, 'lang': 'en', 'token': token},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, dict):
                video = data.get('video') or data.get('videoInfo')
                if video:
                    variants = video.get('variants', [])
                    mp4_variants = []
                    m3u8_url = None
                    for v in variants:
                        ct = v.get('type', '') or v.get('content_type', '')
                        src = v.get('src', '')
                        if not src:
                            continue
                        if 'video/mp4' in ct or src.endswith('.mp4'):
                            mp4_variants.append({'url': src, 'bitrate': v.get('bitrate', 0)})
                        elif 'x-mpegURL' in ct or src.endswith('.m3u8'):
                            m3u8_url = src
                    if mp4_variants or m3u8_url:
                        mp4_variants.sort(key=lambda x: x['bitrate'], reverse=True)
                        author = data.get('user', {}).get('name', '') or data.get('author', {}).get('name', '')
                        screen_name = data.get('user', {}).get('screen_name', '') or data.get('author', {}).get('screen_name', '')
                        text = data.get('text', '') or ''
                        title = text[:60].replace('\n', ' ') if text else f'Tweet_{tweet_id}'
                        photos = data.get('photos', []) or data.get('mediaDetails', [])
                        thumbnail = photos[0].get('url', '') if photos else ''
                        return {
                            'tweet_id': tweet_id, 'title': title,
                            'author': f'{author} (@{screen_name})' if author else screen_name,
                            'text': text, 'thumbnail': thumbnail,
                            'mp4_variants': mp4_variants, 'm3u8_url': m3u8_url,
                            'duration_ms': video.get('durationMs', 0),
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
            on_log(msg, 'warn')
    tokens = {}
    for attempt in range(2):
        try:
            resp = _http_session().get(
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
                _log('ttwid 获取失败，1秒后重试...')
                time.sleep(1)
        except Exception:
            if attempt == 0:
                _log('ttwid 网络异常，1秒后重试...')
                time.sleep(1)
    for attempt in range(2):
        try:
            resp = _http_session().get(
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
                _log('msToken 获取失败，1秒后重试...')
                time.sleep(1)
        except Exception:
            if attempt == 0:
                _log('msToken 网络异常，1秒后重试...')
                time.sleep(1)
    return tokens if tokens else None


def _write_douyin_cookie_file(tokens):
    try:
        fd, path = tempfile.mkstemp(suffix='.txt', prefix='douyin_cookies_')
        with os.fdopen(fd, 'w') as f:
            f.write('# Netscape HTTP Cookie File\n')
            f.write('# Generated by video_downloader\n\n')
            for name, value in tokens.items():
                f.write(f'.douyin.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n')
                f.write(f'.iesdouyin.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n')
        return path
    except Exception:
        return None


def _resolve_douyin_short(url):
    try:
        resp = _http_session().head(url, headers={'User-Agent': _DOUYIN_UA},
                            allow_redirects=True, timeout=10)
        if resp.url != url and 'douyin.com' in resp.url:
            return resp.url
    except Exception:
        pass
    try:
        resp = _http_session().get(url, headers={'User-Agent': _DOUYIN_UA},
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
    """通过直接调用抖音内部 API 解析视频信息，使用 douyin.wtf 生成令牌和签名"""
    def _log(msg, level='info'):
        if on_log:
            on_log(msg, level)

    video_id = _extract_douyin_video_id(url)
    if not video_id:
        _log('无法从链接中提取视频 ID', 'warn')
        return None

    tokens = _fetch_douyin_tokens(on_log=on_log)
    if not tokens:
        _log('douyin.wtf 令牌接口不可用（ttwid / msToken）', 'warn')
        return None

    ttwid = tokens.get('ttwid', '')
    msToken = tokens.get('msToken', '')
    cookies = {'ttwid': ttwid, 'msToken': msToken}

    douyin_api = (
        f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
        f'?aweme_id={video_id}&aid=6383&device_platform=Android'
        f'&browser_language=zh-CN&browser_name=Chrome&browser_online=true'
        f'&browser_platform=Android&browser_version=120.0.0.0'
        f'&engine_name=Blink&engine_version=120.0.0.0'
    )

    a_bogus = None
    try:
        resp = _http_session().get(
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
        _log('无法生成 A-Bogus 签名（anti-bot 令牌）', 'warn')
        return None

    data = None
    for attempt in range(3):
        try:
            full_url = f'{douyin_api}&a_bogus={a_bogus}'
            headers = {
                'User-Agent': _DOUYIN_UA, 'Referer': 'https://www.douyin.com/',
                'Accept': 'application/json',
            }
            resp = _http_session().get(full_url, headers=headers, cookies=cookies, timeout=20)
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
        _log('抖音 API 未返回视频数据（令牌可能过期，或视频需要登录）', 'warn')
        return None

    aweme = data.get('aweme_detail', {}) or {}
    if not aweme:
        _log('响应中缺少视频详情', 'warn')
        return None
    video = aweme.get('video', {})
    if not video:
        _log('视频数据为空（可能已删除或设为私密）', 'warn')
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
        return None

    author_info = aweme.get('author', {}) or {}
    author = (author_info.get('nickname', '') or author_info.get('unique_id', '') or '未知')
    title = (aweme.get('desc', '') or aweme.get('item_title', '')
             or f'Douyin_{video_id}')[:80].replace('\n', ' ')
    duration = int(aweme.get('duration', 0) or 0)
    cover = ''
    cover_info = video.get('cover', {}) or {}
    cover_urls = cover_info.get('url_list', [])
    if cover_urls:
        cover = cover_urls[0]

    return {
        'video_id': video_id, 'title': title, 'author': author,
        'thumbnail': cover, 'mp4_variants': mp4_variants, 'm3u8_url': '',
        'duration_ms': duration, 'cookies': cookies,
    }


# ── 下载引擎（对齐 main.py VideoDownloader）────────────────
class VideoDownloader:
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self._cancel = False
        self._speed_samples = []

    def cancel(self):
        self._cancel = True

    def _log(self, msg, level='info'):
        if self.log_callback:
            self.log_callback(msg, level)

    def _format_size(self, bytes_):
        if not bytes_:
            return '--'
        if bytes_ > 1073741824:
            return f'{bytes_ / 1073741824:.2f} GB'
        if bytes_ > 1048576:
            return f'{bytes_ / 1048576:.1f} MB'
        if bytes_ > 1024:
            return f'{bytes_ / 1024:.0f} KB'
        return f'{bytes_} B'

    def _format_speed(self, speed):
        if not speed:
            return '--'
        if speed > 1048576:
            return f'{speed / 1048576:.1f} MB/s'
        if speed > 1024:
            return f'{speed / 1024:.0f} KB/s'
        return f'{speed:.0f} B/s'

    def _progress_hook(self, d):
        if self._cancel:
            raise Exception("用户取消下载")
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            eta = d.get('eta', 0)
            if speed:
                self._speed_samples.append(speed)
                if len(self._speed_samples) > 10:
                    self._speed_samples.pop(0)
                avg_speed = sum(self._speed_samples) / len(self._speed_samples)
            else:
                avg_speed = 0
            percent = (downloaded / total * 100) if total > 0 else 0
            eta_str = f'{eta // 60}:{eta % 60:02d}' if eta > 0 else '--:--'
            if self.progress_callback:
                self.progress_callback({
                    'percent': min(percent, 100),
                    'speed': self._format_speed(avg_speed),
                    'eta': eta_str,
                    'downloaded': self._format_size(downloaded),
                    'total': self._format_size(total),
                })
        elif d['status'] == 'finished':
            if self.progress_callback:
                self.progress_callback({
                    'percent': 100, 'speed': '', 'eta': '0:00',
                    'downloaded': '', 'total': '',
                })
            if self.log_callback:
                self._log('下载完成，正在合并处理...')

    def _process_result(self, info, ydl, platform):
        if info.get('_type') == 'playlist' or info.get('entries'):
            entries = info.get('entries', [])
            first_valid = None
            for entry in (entries or []):
                if entry:
                    first_valid = entry
                    break
            if first_valid:
                info = first_valid
            else:
                has_file = False
                for entry in (entries or []):
                    if entry:
                        fp = ydl.prepare_filename(entry)
                        if os.path.isfile(fp):
                            has_file = True
                            info = entry
                            break
                        for ext_try in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                            alt = fp.rsplit('.', 1)[0] + f'.{ext_try}'
                            if os.path.isfile(alt):
                                has_file = True
                                info = entry
                                break
                        if has_file:
                            break
                if not has_file:
                    raise Exception('该链接不包含可下载的视频内容')

        title = info.get('title', '未知标题') or '未知标题'
        ext = info.get('ext', 'mp4') or 'mp4'
        duration = info.get('duration', 0)
        dur_str = f'{duration // 60}:{duration % 60:02d}' if duration else '--:--'

        fp = ydl.prepare_filename(info)
        if not os.path.isfile(fp):
            for e in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                p = fp.rsplit('.', 1)[0] + f'.{e}'
                if os.path.isfile(p):
                    fp = p
                    break
        if not os.path.isfile(fp):
            fp = ''

        size = os.path.getsize(fp) if fp and os.path.isfile(fp) else 0
        self._log('─' * 42)
        self._log(f'✅ 下载成功！')
        self._log(f'   标题: {title}')
        self._log(f'   时长: {dur_str}')
        self._log(f'   大小: {self._format_size(size)}')
        if fp:
            self._log(f'   位置: {fp}')
        self._log('─' * 42)
        return {'title': title, 'path': fp, 'size': size, 'platform': platform}

    def _build_opts(self, platform, save_path, quality='best', audio_only=False,
                    cookie_file='', cookie_browser='', proxy='', subtitles=False):
        opts = {
            'outtmpl': os.path.join(save_path, '%(title)s.%(ext)s'),
            'progress_hooks': [self._progress_hook],
            'quiet': True,
            'no_warnings': True,
            'retries': 5,
            'fragment_retries': 5,
            'extract_flat': False,
        }

        if cookie_file and os.path.isfile(cookie_file):
            opts['cookiefile'] = cookie_file

        opts['proxy'] = proxy if proxy else ''

        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            opts['ffmpeg_location'] = ffmpeg_path

        if subtitles:
            opts['writesubtitles'] = True
            opts['subtitleslangs'] = ['zh-Hans', 'zh', 'en']
            opts['subtitlesformat'] = 'vtt'

        if platform == 'douyin':
            opts.update({
                'format': 'bestvideo+bestaudio/best',
                'extractor_args': {'douyin': {'use_api': 'mobile'}},
                'http_headers': {
                    'User-Agent': _DOUYIN_UA,
                    'Referer': 'https://www.douyin.com/',
                },
            })
        elif platform == 'twitter':
            opts.update({
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best',
            })
        elif platform == 'youtube':
            if audio_only:
                opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                })
            else:
                fmt_map = {
                    'best': 'bestvideo+bestaudio/best',
                    '1080p': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                    '720p': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
                    '480p': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
                }
                opts['format'] = fmt_map.get(quality, fmt_map['best'])
        elif platform == 'bilibili':
            fmt_map = {
                'best': 'bestvideo+bestaudio/best',
                '1080p': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                '720p': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
            }
            opts['format'] = fmt_map.get(quality, fmt_map['best'])
        elif platform == 'instagram':
            opts['format'] = 'best'
        elif platform == 'xiaohongshu':
            opts['format'] = 'best'
        elif platform == 'weibo':
            opts['format'] = 'bestvideo+bestaudio/best'
        else:
            opts['format'] = 'bestvideo+bestaudio/best'

        return opts

    def _direct_http_download(self, url, save_path, filename,
                               referer='', cookies=None, proxy=''):
        filepath = os.path.join(save_path, filename)
        self._speed_samples = []
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer or 'https://www.google.com/',
            }
            session = _http_session()
            if proxy:
                session.proxies = {'http': proxy, 'https': proxy}
            resp = session.get(url, headers=headers, cookies=cookies,
                               stream=True, timeout=30)
            if resp.status_code != 200:
                return None
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 1024 * 1024 * 2
            start_time = time.time()

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
                        elapsed = time.time() - start_time
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        self._speed_samples.append(speed)
                        if len(self._speed_samples) > 10:
                            self._speed_samples.pop(0)
                        avg_speed = sum(self._speed_samples) / len(self._speed_samples)
                        pct = (downloaded / total * 100) if total > 0 else 0
                        eta = (total - downloaded) / avg_speed if avg_speed > 0 and total > 0 else 0
                        eta_str = f'{int(eta // 60)}:{int(eta % 60):02d}' if eta > 0 else '--:--'
                        if self.progress_callback:
                            self.progress_callback({
                                'percent': min(pct, 100),
                                'speed': self._format_speed(avg_speed),
                                'eta': eta_str,
                                'downloaded': self._format_size(downloaded),
                                'total': self._format_size(total),
                            })

            if self.progress_callback:
                self.progress_callback({
                    'percent': 100, 'speed': '', 'eta': '0:00',
                    'downloaded': '', 'total': '',
                })
            return filepath
        except Exception:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return None

    def _try_twitter_direct(self, url, save_path, proxy=''):
        self._log('🔍 尝试直接解析视频地址（无需登录）...')
        video_info = parse_twitter_video(url)
        if not video_info:
            self._log('⚠️ 直接解析未获取到视频，回退到 yt-dlp...', 'warn')
            return (False, 'fallback')

        best_mp4 = video_info['mp4_variants'][0] if video_info['mp4_variants'] else None
        title = video_info['title']
        author = video_info['author']
        duration_ms = int(video_info['duration_ms']) if video_info['duration_ms'] else 0
        duration_s = duration_ms // 1000
        dur_str = f'{duration_s // 60}:{duration_s % 60:02d}' if duration_s else '--:--'

        self._log(f'✅ 解析成功')
        self._log(f'   作者: {author}')
        self._log(f'   标题: {title}')
        if best_mp4:
            quality = best_mp4.get('quality', '')
            height = best_mp4.get('height', 0)
            if height:
                self._log(f'   画质: {height}p' + (f' ({quality})' if quality else ''))
            elif best_mp4.get('bitrate'):
                self._log(f'   码率: {best_mp4["bitrate"] / 1000:.0f} kbps')
        self._log(f'   时长: {dur_str}')

        if best_mp4:
            safe_title = re.sub(r'[\\/*?:"<>|]', '', title)
            filename = f'{safe_title}_{video_info["tweet_id"]}.mp4'
            self._log('⬇ 直接 HTTP 下载...')
            filepath = self._direct_http_download(best_mp4['url'], save_path, filename, referer=url, proxy=proxy)
            if self._cancel:
                return (False, '下载已取消')
            if filepath:
                size = os.path.getsize(filepath) if os.path.isfile(filepath) else 0
                self._log('─' * 42)
                self._log(f'✅ 下载成功！')
                self._log(f'   标题: {title}')
                self._log(f'   作者: {author}')
                self._log(f'   时长: {dur_str}')
                self._log(f'   大小: {self._format_size(size)}')
                self._log(f'   位置: {filepath}')
                self._log('─' * 42)
                return (True, {'title': title, 'path': filepath, 'size': size, 'platform': 'twitter'})
            else:
                self._log('⚠️ HTTP 下载失败，回退到 yt-dlp...', 'warn')
                return (False, 'fallback')
        else:
            self._log('⚠️ 仅有 m3u8 流，回退到 yt-dlp 处理...', 'warn')
            return (False, 'fallback')

    def _try_douyin_direct(self, url, save_path, proxy=''):
        self._log('🔍 解析抖音视频（直连抖音 API + douyin.wtf 令牌）...')
        video_info = parse_douyin_video(url, on_log=self._log)
        if not video_info:
            self._log('⚠️ 解析未获取到视频，回退到 yt-dlp...', 'warn')
            return (False, 'fallback')

        best_mp4 = video_info['mp4_variants'][0] if video_info['mp4_variants'] else None
        if not best_mp4:
            self._log('⚠️ 未找到 MP4 视频，回退到 yt-dlp...', 'warn')
            return (False, 'fallback')

        title = video_info['title']
        author = video_info['author']
        quality = best_mp4.get('quality', '')
        duration_ms = int(video_info['duration_ms']) if video_info['duration_ms'] else 0
        duration_s = duration_ms // 1000
        dur_str = f'{duration_s // 60}:{duration_s % 60:02d}' if duration_s else '--:--'
        cookies = video_info.get('cookies', {})

        self._log(f'✅ 解析成功')
        self._log(f'   作者: {author}')
        self._log(f'   标题: {title}')
        if quality:
            self._log(f'   类型: {quality}')
        self._log(f'   时长: {dur_str}')

        safe_title = re.sub(r'[\\/*?:"<>|]', '', title)
        vid = video_info.get('video_id', '') or 'douyin'
        filename = f'{safe_title}_{vid}.mp4'
        self._log('⬇ 直接 HTTP 下载...')
        filepath = self._direct_http_download(
            best_mp4['url'], save_path, filename,
            referer='https://www.douyin.com/',
            cookies=cookies,
            proxy=proxy,
        )
        if self._cancel:
            return (False, '下载已取消')
        if filepath:
            size = os.path.getsize(filepath) if os.path.isfile(filepath) else 0
            self._log('─' * 42)
            self._log(f'✅ 下载成功！')
            self._log(f'   标题: {title}')
            self._log(f'   作者: {author}')
            self._log(f'   时长: {dur_str}')
            self._log(f'   大小: {self._format_size(size)}')
            self._log(f'   位置: {filepath}')
            self._log('─' * 42)
            return (True, {'title': title, 'path': filepath, 'size': size, 'platform': 'douyin'})
        else:
            self._log('⚠️ HTTP 下载失败，回退到 yt-dlp...', 'warn')
            return (False, 'fallback')

    def download(self, url, save_path, quality='best',
                 audio_only=False, cookie_file='', cookie_browser='',
                 proxy='', subtitles=False):
        """下载单个视频，返回 (success, data)"""
        platform = detect_platform(url)
        os.makedirs(save_path, exist_ok=True)
        self._speed_samples = []

        if self.log_callback:
            pi = PLATFORM_INFO.get(platform)
            name = f'{pi["icon"]} {pi["name"]}' if pi else '未知平台'
            self._log(f'平台: {name}')
            self._log(f'链接: {url}')
            self._log(f'保存: {save_path}')
            if cookie_file:
                self._log(f'Cookie: {os.path.basename(cookie_file)}')

        # ── 抖音直连 ──
        douyin_token_cookie = None
        if platform == 'douyin':
            result = self._try_douyin_direct(url, save_path, proxy)
            if result is not None:
                ok, data = result
                if ok:
                    return True, data
                elif 'fallback' in str(data):
                    pass
                else:
                    return False, data
            self._log('🔄 回退到 yt-dlp 下载...')
            if not cookie_file:
                self._log('🔑 尝试获取新鲜令牌...')
                tokens = _fetch_douyin_tokens()
                if tokens:
                    douyin_token_cookie = _write_douyin_cookie_file(tokens)
                    if douyin_token_cookie:
                        self._log(f'✅ 已获取令牌 (ttwid={tokens.get("ttwid", "")[:16]}...)')
                    else:
                        self._log('⚠️ 令牌写入失败', 'warn')
                else:
                    self._log('⚠️ 令牌获取失败', 'warn')

        # ── Twitter 直连 ──
        if platform == 'twitter':
            result = self._try_twitter_direct(url, save_path, proxy)
            if result is not None:
                ok, data = result
                if ok:
                    return True, data
                elif 'fallback' in str(data):
                    pass
                else:
                    return False, data

        if douyin_token_cookie:
            cookie_file = douyin_token_cookie

        opts = self._build_opts(platform, save_path, quality, audio_only,
                                cookie_file, '', proxy, subtitles)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if self._cancel:
                    return False, '下载已取消'
                if info is None:
                    return False, '无法解析视频信息，链接可能无效或需要登录'
                return True, self._process_result(info, ydl, platform)

        except Exception as e:
            err = str(e)
            if 'cancel' in err.lower():
                return False, '下载已取消'

            if any(k in err.lower() for k in ['ffmpeg', 'ffprobe', 'no such file or directory: ff']):
                self._log('❌ 错误: 未找到 FFmpeg，无法合并视频流', 'error')
                return False, '未找到 FFmpeg，无法合并视频流。请在 Termux 中安装 FFmpeg'

            if platform == 'twitter' and any(k in err.lower() for k in [
                'login', 'author', 'guest', '403', '401', 'cookie', 'private'
            ]):
                self._log('⚠️ 尝试使用备用格式重试...', 'warn')
                try:
                    alt_opts = opts.copy()
                    alt_opts['format'] = 'best'
                    alt_opts.pop('extractor_args', None)
                    with yt_dlp.YoutubeDL(alt_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if self._cancel:
                            return False, '下载已取消'
                        if info:
                            return True, self._process_result(info, ydl, platform)
                except Exception:
                    pass
                self._log('💡 提示: Twitter/X 需要登录才能访问此内容', 'warn')
                self._log('   请导出 cookies.txt 后放入手机并在设置中指定', 'warn')

            if platform == 'douyin' and any(k in err.lower() for k in [
                'fresh cookies', 'cookie', 'need login', 'private'
            ]):
                self._log('❌ 抖音下载失败 — 需要登录 Cookie', 'error')
                self._log('   💡 请在电脑端导出 cookies.txt 后传入手机', 'warn')
                return False, '抖音需要登录 Cookie，请导出 cookies.txt 后在设置中指定'

            if 'format' in err.lower() and quality != 'best':
                self._log('⚠️ 画质选项失败，尝试使用最佳可用画质重试...')
                opts2 = self._build_opts(platform, save_path, 'best', audio_only,
                                         cookie_file, '', proxy, False)
                try:
                    with yt_dlp.YoutubeDL(opts2) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if self._cancel:
                            return False, '下载已取消'
                        if info is None:
                            return False, '无法解析视频信息'
                        return True, self._process_result(info, ydl, platform)
                except Exception as e2:
                    err = str(e2)
                    if 'cancel' in err.lower():
                        return False, '下载已取消'

            self._log(f'❌ 错误: {err}')
            return False, err
        finally:
            if douyin_token_cookie:
                try:
                    os.unlink(douyin_token_cookie)
                except Exception:
                    pass


# ── 批量下载管理器 ───────────────────────────────────────
class BatchDownloader:
    def __init__(self, downloader):
        self.downloader = downloader
        self.results = []
        self._cancel = False

    def cancel(self):
        self._cancel = True
        self.downloader.cancel()

    def run(self, urls, save_path, quality='best', audio_only=False,
            cookie_file='', proxy='', subtitles=False):
        self.results = []
        total = len(urls)
        for i, url in enumerate(urls):
            if self._cancel:
                break
            url = url.strip()
            if not url:
                continue
            if self.downloader.log_callback:
                self.downloader._log(f'\n{"=" * 42}')
                self.downloader._log(f'[{i + 1}/{total}] 开始下载')
                self.downloader._log(f'{"=" * 42}')
            ok, data = self.downloader.download(url, save_path, quality,
                                                audio_only, cookie_file,
                                                '', proxy, subtitles)
            self.results.append((url, ok, data))
        return self.results


# ── Kivy UI ────────────────────────────────────────────
KV_STRING = '''
#:import platform kivy.utils.platform

<RootWidget>:
    orientation: 'vertical'
    padding: dp(14)
    spacing: dp(10)

    # ── 标题 ──
    Label:
        text: '📥 视频下载工具'
        font_size: dp(22)
        size_hint_y: None
        height: dp(48)
        color: 0.2, 0.6, 0.9, 1
        bold: True

    # ── URL 输入 ──
    BoxLayout:
        size_hint_y: None
        height: dp(90)
        spacing: dp(8)
        TextInput:
            id: url_input
            hint_text: '粘贴视频链接（每行一个，支持批量）...'
            multiline: True
            font_size: dp(16)
            write_tab: False
            on_text_validate: root.start_download()
        Button:
            text: '粘贴'
            size_hint_x: None
            width: dp(72)
            font_size: dp(16)
            on_release: root.paste_clipboard()

    # ── 设置行 ──
    BoxLayout:
        size_hint_y: None
        height: dp(48)
        spacing: dp(10)
        Spinner:
            id: quality_spinner
            text: 'best'
            values: ['best', '1080p', '720p', '480p']
            size_hint_x: 0.35
            font_size: dp(15)
            height: dp(48)
        BoxLayout:
            size_hint_x: None
            width: dp(48)
            CheckBox:
                id: audio_only
                size_hint: 1, 1
        Label:
            text: '仅音频'
            size_hint_x: None
            width: dp(60)
            font_size: dp(14)
            color: 0.7, 0.7, 0.7, 1
            bold: True
        Label:
            id: platform_label
            text: ''
            font_size: dp(14)
            color: 0.5, 0.5, 0.5, 1
            bold: True

    # ── 保存路径 ──
    BoxLayout:
        size_hint_y: None
        height: dp(34)
        spacing: dp(6)
        Label:
            text: '保存到:'
            size_hint_x: None
            width: dp(60)
            font_size: dp(13)
            color: 0.5, 0.5, 0.5, 1
        Label:
            id: save_path_label
            text: root.short_save_path
            font_size: dp(13)
            color: 0.3, 0.6, 0.9, 1
            halign: 'left'
            text_size: self.width, self.height
            shorten: True

    # ── 下载按钮 ──
    Button:
        id: download_btn
        text: '⬇  开 始 下 载'
        size_hint_y: None
        height: dp(56)
        font_size: dp(19)
        background_color: 0.2, 0.6, 0.9, 1
        color: 1, 1, 1, 1
        on_release: root.start_download()

    # ── 加载指示器 ──
    BoxLayout:
        id: loading_box
        size_hint_y: None
        height: dp(30)
        opacity: 0
        disabled: True
        Label:
            id: loading_label
            text: '正在解析链接...'
            font_size: dp(14)
            color: 0.5, 0.5, 0.5, 1

    # ── 进度条 ──
    ProgressBar:
        id: progress_bar
        max: 100
        value: 0
        size_hint_y: None
        height: dp(10)

    Label:
        id: progress_label
        text: '就绪'
        size_hint_y: None
        height: dp(26)
        font_size: dp(14)
        color: 0.5, 0.5, 0.5, 1

    # ── 日志 ──
    ScrollView:
        do_scroll_x: False
        bar_width: dp(6)
        Label:
            id: log_label
            text: '支持: 抖音 · Twitter · YouTube · BiliBili\\nInstagram · 小红书 · 微博\\n\\n粘贴链接后自动识别平台'
            font_size: dp(14)
            color: 0.75, 0.75, 0.75, 1
            markup: True
            size_hint_y: None
            height: self.texture_size[1]
            text_size: self.width, None
            valign: 'top'
            halign: 'left'
'''

Builder.load_string(KV_STRING)


class RootWidget(BoxLayout):
    save_path = StringProperty(DEFAULT_SAVE_PATH)
    short_save_path = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cfg = load_config()
        self.save_path = self.cfg.get('save_path', DEFAULT_SAVE_PATH)
        self._update_short_path()
        self.downloading = False
        self.downloader = None
        self.batch_downloader = None
        self.thread = None

        try:
            os.makedirs(self.save_path, exist_ok=True)
        except Exception:
            pass

        self.ids.url_input.bind(text=self.on_url_change)

    def _update_short_path(self):
        """生成简短可读的保存路径（仅显示最后两级目录）"""
        p = self.save_path.replace('\\', '/')
        parts = p.rstrip('/').split('/')
        if len(parts) >= 3:
            self.short_save_path = '.../' + '/'.join(parts[-2:])
        else:
            self.short_save_path = p

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
        # 取第一行检测平台
        first_line = value.strip().split('\n')[0]
        platform = detect_platform(first_line)
        pi = PLATFORM_INFO.get(platform)
        if pi:
            self.ids.platform_label.text = f'{pi["icon"]} {pi["name"]}'
        else:
            self.ids.platform_label.text = '未知平台'

    def log(self, msg):
        current = self.ids.log_label.text
        timestamp = time.strftime('%H:%M:%S')
        self.ids.log_label.text = current + f'\n[{timestamp}] {msg}'
        Clock.schedule_once(lambda dt: setattr(
            self.ids.log_label, 'height', self.ids.log_label.texture_size[1]), 0.05)

    def _get_urls(self):
        text = self.ids.url_input.text.strip()
        # 用正则从文本中提取所有 URL（支持分享文本中夹杂的链接）
        urls = re.findall(r'https?://[^\s]+', text)
        return urls

    def update_progress(self, info):
        def _update(dt):
            pct = info.get('percent', 0)
            self.ids.progress_bar.value = pct
            parts = [f'{pct:.1f}%']
            for k in ['speed', 'eta', 'downloaded', 'total']:
                v = info.get(k, '')
                if v:
                    parts.append(v)
            self.ids.progress_label.text = '  |  '.join(parts)
        Clock.schedule_once(_update, 0)

    def _show_loading(self, show):
        def _apply(dt):
            self.ids.loading_box.opacity = 1 if show else 0
        Clock.schedule_once(_apply, 0)

    def start_download(self):
        if self.downloading:
            # 取消确认
            self._show_cancel_confirm()
            return

        urls = self._get_urls()
        if not urls:
            self.log('请粘贴有效的视频链接（以 http:// 或 https:// 开头）')
            return

        self.downloading = True
        self.ids.download_btn.text = '⏹  取 消 下 载'
        self.ids.download_btn.background_color = (0.9, 0.3, 0.3, 1)
        self.ids.progress_bar.value = 0
        self.ids.progress_label.text = '准备中...'
        self._show_loading(True)

        quality = self.ids.quality_spinner.text
        audio = self.ids.audio_only.active
        proxy = self.cfg.get('proxy', '')

        self.downloader = VideoDownloader(
            progress_callback=self.update_progress,
            log_callback=lambda msg, level='info': self.log(msg),
        )

        count = len(urls)
        self.log('━' * 30)
        self.log(f'📥 开始下载 {count} 个视频' if count > 1 else '📥 开始下载')

        if count > 1:
            self.batch_downloader = BatchDownloader(self.downloader)
            self.thread = threading.Thread(
                target=self._batch_thread,
                args=(urls, quality, audio, proxy),
                daemon=True,
            )
        else:
            self.thread = threading.Thread(
                target=self._single_thread,
                args=(urls[0], quality, audio, proxy),
                daemon=True,
            )
        self.thread.start()

    def _single_thread(self, url, quality, audio, proxy):
        try:
            ok, data = self.downloader.download(
                url, self.save_path,
                quality=quality, audio_only=audio,
                cookie_file=self.cfg.get('cookie_file', ''),
                proxy=proxy,
            )
            if ok:
                Clock.schedule_once(lambda dt: self._on_success(data), 0)
            else:
                Clock.schedule_once(lambda dt: self._on_fail(data), 0)
        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_fail(str(e)), 0)

    def _batch_thread(self, urls, quality, audio, proxy):
        bd = self.batch_downloader
        results = bd.run(urls, self.save_path, quality=quality,
                         audio_only=audio, cookie_file=self.cfg.get('cookie_file', ''),
                         proxy=proxy)
        Clock.schedule_once(lambda dt: self._on_batch_done(results), 0)

    def _on_success(self, data):
        self.downloading = False
        self.ids.download_btn.text = '⬇  开 始 下 载'
        self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
        self.ids.progress_bar.value = 100
        self._show_loading(False)

        title = data.get('title', '')
        mb = data.get('size', 0) / 1048576
        fp = data.get('path', '')
        self.ids.progress_label.text = f'✅ {title} ({mb:.1f} MB)'
        self.log(f'✅ 下载完成: {title}')
        self.log(f'   大小: {mb:.1f} MB')
        self.log(f'   位置: {fp}')

        # 记录历史
        self.cfg.setdefault('download_history', []).append({
            'title': title,
            'platform': data.get('platform', ''),
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'path': fp,
        })
        if len(self.cfg['download_history']) > 200:
            self.cfg['download_history'] = self.cfg['download_history'][-200:]
        save_config(self.cfg)

        # Android: 触发媒体扫描
        if IS_ANDROID and fp and os.path.isfile(fp):
            try:
                import subprocess
                subprocess.run(['am', 'broadcast', '-a',
                              'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
                              '-d', f'file://{fp}'], timeout=5, capture_output=True)
            except Exception:
                pass

        self._show_success_popup(title, mb)

    def _on_fail(self, error):
        self.downloading = False
        self.ids.download_btn.text = '⬇  开 始 下 载'
        self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
        self.ids.progress_bar.value = 0
        self.ids.progress_label.text = f'❌ {error}'
        self._show_loading(False)
        self.log(f'❌ 下载失败: {error}')
        self._show_fail_popup(error)

    def _on_batch_done(self, results):
        self.downloading = False
        self.ids.download_btn.text = '⬇  开 始 下 载'
        self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
        self._show_loading(False)

        success_count = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        self.ids.progress_bar.value = 100
        self.ids.progress_label.text = f'✅ 完成: {success_count}/{total}'
        self.log(f'✅ 批量下载完成: {success_count}/{total}')

        # 记录下载历史
        for url, ok, data in results:
            if ok and isinstance(data, dict) and data.get('path'):
                self.cfg.setdefault('download_history', []).append({
                    'title': data.get('title', ''),
                    'platform': data.get('platform', ''),
                    'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'path': data.get('path', ''),
                })
        if len(self.cfg['download_history']) > 200:
            self.cfg['download_history'] = self.cfg['download_history'][-200:]
        save_config(self.cfg)

        # 显示失败详情
        for url, ok, data in results:
            if not ok:
                self.log(f'  ❌ {url[:50]}... → {data}')

    # ── 弹窗 ──
    def _show_success_popup(self, title, mb):
        try:
            content = BoxLayout(orientation='vertical', padding=20, spacing=12)
            content.add_widget(Label(
                text=f'[b]下载完成！[/b]\n\n{title[:50]}\n大小: {mb:.1f} MB',
                markup=True, font_size='16sp', halign='center'))
            btn = Button(text='确定', size_hint=(1, None), height='48dp',
                        font_size='16sp')
            popup = Popup(title='✅ 下载成功', content=content,
                         size_hint=(0.85, 0.4), auto_dismiss=True)
            btn.bind(on_release=popup.dismiss)
            content.add_widget(btn)
            popup.open()
        except Exception:
            pass

    def _show_fail_popup(self, error):
        try:
            content = BoxLayout(orientation='vertical', padding=20, spacing=12)
            err_msg = str(error)[:120]
            content.add_widget(Label(
                text=f'[b]下载失败[/b]\n\n{err_msg}',
                markup=True, font_size='15sp', halign='center'))
            btn = Button(text='确定', size_hint=(1, None), height='48dp',
                        font_size='16sp')
            popup = Popup(title='❌ 下载失败', content=content,
                         size_hint=(0.85, 0.4), auto_dismiss=True)
            btn.bind(on_release=popup.dismiss)
            content.add_widget(btn)
            popup.open()
        except Exception:
            pass

    def _show_cancel_confirm(self):
        try:
            content = BoxLayout(orientation='vertical', padding=16, spacing=10)
            content.add_widget(Label(
                text='确定要取消当前下载吗？',
                font_size='16sp', halign='center'))
            btn_box = BoxLayout(size_hint=(1, None), height='48dp', spacing=12)
            btn_yes = Button(text='取消下载', font_size='15sp',
                            background_color=(0.9, 0.3, 0.3, 1))
            btn_no = Button(text='继续下载', font_size='15sp',
                           background_color=(0.3, 0.7, 0.3, 1))
            popup = Popup(title='确认取消', content=content,
                         size_hint=(0.8, 0.35), auto_dismiss=False)
            btn_yes.bind(on_release=lambda dt: self._do_cancel(popup))
            btn_no.bind(on_release=popup.dismiss)
            btn_box.add_widget(btn_yes)
            btn_box.add_widget(btn_no)
            content.add_widget(btn_box)
            popup.open()
        except Exception:
            pass

    def _do_cancel(self, popup):
        popup.dismiss()
        self.downloading = False
        if self.downloader:
            self.downloader.cancel()
        self.ids.download_btn.text = '⬇  开 始 下 载'
        self.ids.download_btn.background_color = (0.2, 0.6, 0.9, 1)
        self.ids.progress_label.text = '⏹ 已取消'
        self._show_loading(False)
        self.log('⏹ 用户取消下载')


class VideoDownloaderApp(App):
    def build(self):
        self.title = '视频下载工具'
        self.icon = 'icon.png'
        return RootWidget()

    def on_start(self):
        if IS_ANDROID and HAS_ANDROID:
            try:
                request_permissions([Permission.WRITE_EXTERNAL_STORAGE,
                                    Permission.READ_EXTERNAL_STORAGE])
            except Exception:
                pass


if __name__ == '__main__':
    VideoDownloaderApp().run()
