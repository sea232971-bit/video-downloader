#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频下载工具 v3.1 — 支持抖音/Twitter(X)/YouTube/Bilibili/Instagram/小红书等平台
支持无水印下载、批量下载、画质选择、音频提取
"""

import sys
import os
import re
import json
import time
import math
import queue
import threading
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

import requests

# ── 全局 HTTP Session（trust_env=False 防止读取系统/环境代理）──
def _http_session():
    """创建不读取系统代理和环境变量的 requests Session"""
    s = requests.Session()
    s.trust_env = False
    return s

# 解决 Windows 终端编码问题
if sys.platform == 'win32':
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import yt_dlp
from yt_dlp.jsinterp import js_number_to_string


# ============================================================
# 配置
# ============================================================
CONFIG_FILE = Path(__file__).parent / 'config.json'

DEFAULT_CONFIG = {
    'save_path': str(Path.home() / 'Downloads' / 'VideoDownload'),
    'window_width': 820,
    'window_height': 780,
    'audio_only': False,
    'auto_open_folder': True,
    'cookie_file': '',
    'proxy': '',
    'threads': 1,
    'download_history': [],
}


def load_config():
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text('utf-8'))
            # merge with defaults so new keys get filled in
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), 'utf-8')
    except Exception:
        pass


def check_ffmpeg() -> bool:
    """检查 FFmpeg 是否可用，搜索 PATH 及常见安装位置"""
    if shutil.which('ffmpeg'):
        return True
    # 常见安装位置
    for loc in [
        Path.home() / 'ffmpeg',
        Path('C:/ffmpeg'),
        Path('ffmpeg'),
    ]:
        for sub in loc.glob('ffmpeg-*/bin/ffmpeg.exe') if loc.exists() else []:
            return True
        # 也检查直接在根目录的情况
        if (loc / 'bin' / 'ffmpeg.exe').exists():
            return True
    return False


def find_ffmpeg() -> str | None:
    """查找 FFmpeg 可执行文件路径"""
    result = shutil.which('ffmpeg')
    if result:
        return result
    for loc in [
        Path.home() / 'ffmpeg',
        Path('C:/ffmpeg'),
        Path('ffmpeg'),
    ]:
        if not loc.exists():
            continue
        # ffmpeg-版本号/bin/ffmpeg.exe
        for sub in sorted(loc.glob('ffmpeg-*/bin/ffmpeg.exe'), reverse=True):
            return str(sub)
        # 直接在 bin/ 下
        direct = loc / 'bin' / 'ffmpeg.exe'
        if direct.exists():
            return str(direct)
    return None


# ============================================================
# 平台定义
# ============================================================
PLATFORM_INFO = {
    'douyin': {
        'name': '抖音',
        'icon': '🎵',
        'color': '#1E90FF',
        'patterns': [r'(v\.)?douyin\.com', r'iesdouyin\.com'],
        'desc': '无水印下载',
    },
    'twitter': {
        'name': 'Twitter / X',
        'icon': '🐦',
        'color': '#1DA1F2',
        'patterns': [r'twitter\.com', r'x\.com', r'fxtwitter\.com', r'vxtwitter\.com', r'twitfix\.com'],
        'desc': '最高画质',
    },
    'youtube': {
        'name': 'YouTube',
        'icon': '▶️',
        'color': '#FF0000',
        'patterns': [r'youtube\.com', r'youtu\.be'],
        'desc': '可选画质/字幕',
    },
    'bilibili': {
        'name': 'BiliBili',
        'icon': '📺',
        'color': '#FB7299',
        'patterns': [r'bilibili\.com', r'b23\.tv'],
        'desc': '可选画质',
    },
    'instagram': {
        'name': 'Instagram',
        'icon': '📷',
        'color': '#E4405F',
        'patterns': [r'instagram\.com'],
        'desc': '视频/图片下载',
    },
    'xiaohongshu': {
        'name': '小红书',
        'icon': '📕',
        'color': '#FF2442',
        'patterns': [r'xiaohongshu\.com', r'xhslink\.com'],
        'desc': '笔记下载',
    },
    'weibo': {
        'name': '微博',
        'icon': '💬',
        'color': '#E6162D',
        'patterns': [r'weibo\.com', r'weibo\.cn'],
        'desc': '视频下载',
    },
}


def detect_platform(url: str) -> str:
    url = url.strip()
    for name, info in PLATFORM_INFO.items():
        for p in info['patterns']:
            if re.search(p, url, re.I):
                return name
    return 'unknown'


# ============================================================
# Twitter/X 视频解析 — 多种策略（无需登录）
# ============================================================

def _extract_tweet_id(url: str) -> str | None:
    """从 Twitter/X 链接中提取推文 ID"""
    m = re.search(r'(?:twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com|twitfix\.com)'
                  r'(?:/i)?(?:/[^/]+)?/status(?:es)?/(\d+)', url, re.I)
    return m.group(1) if m else None


def parse_twitter_video(url: str) -> dict | None:
    """解析 Twitter/X 视频信息（无需登录）

    策略:
    1. api.fxtwitter.com — 无需登录，最稳定
    2. cdn.syndication.twimg.com — 官方 syndication API（备用）

    返回: {'mp4_variants': [...], 'title': str, 'author': str, ...} 或 None
    """
    tweet_id = _extract_tweet_id(url)
    if not tweet_id:
        return None

    # ── 策略1: fxtwitter API（无需登录，最可靠）──
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
                # fxtwitter 把视频放在 media.videos 或 media.all 中
                videos = media.get('videos') or media.get('all') or []

                # 也检查 photos — 如果有视频，它可能在 videos 里
                if not videos:
                    photos = media.get('photos') or media.get('all') or []
                    # 有些版本把视频放 photos 里
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
                            'url': v_url,
                            'bitrate': v.get('bitrate', 0) or 0,
                            'quality': v.get('quality', ''),
                            'width': v.get('width', 0) or 0,
                            'height': v.get('height', 0) or 0,
                        })

                if mp4_variants or m3u8_url:
                    mp4_variants.sort(key=lambda x: (
                        x['height'] or x['bitrate'] or 0
                    ), reverse=True)
                    text = tweet.get('text', '') or ''
                    title = text[:60].replace('\n', ' ') if text else f'Tweet_{tweet_id}'
                    duration = 0
                    if videos:
                        duration = (videos[0].get('duration', 0) or 0)
                    return {
                        'tweet_id': tweet_id,
                        'title': title,
                        'author': f'{author} (@{screen_name})' if author else screen_name,
                        'text': text,
                        'thumbnail': media.get('thumbnail', '') if isinstance(media, dict) else '',
                        'mp4_variants': mp4_variants,
                        'm3u8_url': m3u8_url,
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
                            'tweet_id': tweet_id,
                            'title': title,
                            'author': f'{author} (@{screen_name})' if author else screen_name,
                            'text': text,
                            'thumbnail': thumbnail,
                            'mp4_variants': mp4_variants,
                            'm3u8_url': m3u8_url,
                            'duration_ms': video.get('durationMs', 0),
                        }
    except Exception:
        pass

    return None


# ============================================================
# 抖音视频解析（yt-dlp 抖音提取器已失效，使用自建解析）
# ============================================================

# 抖音短链接 → 视频 ID 的正则模式
_DOUYIN_URL_PATTERNS = [
    re.compile(r'(?:www\.)?douyin\.com/(?:video|note)/(\d+)', re.I),
    re.compile(r'(?:www\.)?douyin\.com/user/[^?]+.*?modal_id=(\d+)', re.I),
    re.compile(r'(?:www\.)?iesdouyin\.com/share/video/(\d+)', re.I),
    re.compile(r'v\.douyin\.com/(\w+)', re.I),
]

# 抖音需要手机 UA 才能正确获取内容
_DOUYIN_UA = ('Mozilla/5.0 (Linux; Android 10; Pixel 3) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/120.0.0.0 Mobile Safari/537.36')


def _fetch_douyin_tokens(on_log=None) -> dict | None:
    """通过 douyin.wtf API 获取新鲜的抖音令牌（ttwid, msToken）
    每个端点失败时重试一次，返回 {'ttwid': str, 'msToken': str} 或 None
    """
    def _log(msg):
        if on_log:
            on_log(msg, 'warn')

    tokens = {}
    # 获取 ttwid
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

    # 获取 msToken
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


def _write_douyin_cookie_file(tokens: dict) -> str | None:
    """将抖音令牌写入临时 Netscape 格式 cookie 文件，返回文件路径"""
    import tempfile
    try:
        fd, path = tempfile.mkstemp(suffix='.txt', prefix='douyin_cookies_')
        with os.fdopen(fd, 'w') as f:
            f.write('# Netscape HTTP Cookie File\n')
            f.write('# Generated by video_downloader\n\n')
            for name, value in tokens.items():
                # Netscape format: domain flag path secure expiration name value
                f.write(f'.douyin.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n')
                f.write(f'.iesdouyin.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n')
        return path
    except Exception:
        return None


def _resolve_douyin_short(url: str) -> str | None:
    """将 douyin 短链接还原为完整链接（HEAD 跟踪 HTTP 重定向 → GET 提取 HTML 跳转）"""
    try:
        resp = _http_session().head(url, headers={'User-Agent': _DOUYIN_UA},
                            allow_redirects=True, timeout=10)
        if resp.url != url and 'douyin.com' in resp.url:
            return resp.url
    except Exception:
        pass

    # HEAD 未重定向时，用 GET 从 HTML 中提取目标链接
    try:
        resp = _http_session().get(url, headers={'User-Agent': _DOUYIN_UA},
                           allow_redirects=True, timeout=10)
        if resp.url != url and 'douyin.com' in resp.url:
            return resp.url
        # 部分短链返回 <a href="...">Found</a> 格式
        m = re.search(r'href="(https?://[^"]+?douyin\.com[^"]*)"', resp.text[:3000], re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _extract_douyin_video_id(url: str) -> str | None:
    """从抖音链接提取视频 ID"""
    for pat in _DOUYIN_URL_PATTERNS:
        m = pat.search(url)
        if m:
            gid = m.group(1)
            if pat.pattern.startswith(r'v\.'):
                # 短链接，需要解析
                full = _resolve_douyin_short(url)
                if full:
                    return _extract_douyin_video_id(full)
                return gid  # 返回短码作为 fallback
            return gid
    return None


def parse_douyin_video(url: str, on_log=None) -> dict | None:
    """通过直接调用抖音内部 API 解析视频信息

    使用 douyin.wtf 生成令牌和签名，直接请求抖音 API，
    CDN URL 会基于本次请求的会话签名，因此可以直接下载。

    返回: {'mp4_variants': [...], 'title': str, 'author': str, ...,
            'cookies': dict, 'video_id': str} 或 None
    """
    def _log(msg, level='info'):
        if on_log:
            on_log(msg, level)

    # 1. 提取视频 ID
    video_id = _extract_douyin_video_id(url)
    if not video_id:
        _log('无法从链接中提取视频 ID', 'warn')
        return None

    # 2. 获取新鲜令牌
    tokens = _fetch_douyin_tokens(on_log=on_log)
    if not tokens:
        _log('⚠️ douyin.wtf 令牌接口不可用（ttwid / msToken）', 'warn')
        return None

    ttwid = tokens.get('ttwid', '')
    msToken = tokens.get('msToken', '')
    cookies = {'ttwid': ttwid, 'msToken': msToken}

    # 3. 构建抖音 API URL 并生成 A-Bogus
    douyin_api = (
        f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
        f'?aweme_id={video_id}&aid=6383&device_platform=web'
        f'&browser_language=zh-CN&browser_name=Chrome&browser_online=true'
        f'&browser_platform=Win32&browser_version=120.0.0.0'
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
        _log('⚠️ 无法生成 A-Bogus 签名（anti-bot 令牌）', 'warn')
        return None

    # 4. 调用抖音详情 API（加入重试，偶发空响应）
    data = None
    for attempt in range(3):
        try:
            full_url = f'{douyin_api}&a_bogus={a_bogus}'
            headers = {
                'User-Agent': _DOUYIN_UA,
                'Referer': 'https://www.douyin.com/',
                'Accept': 'application/json',
            }
            resp = _http_session().get(full_url, headers=headers, cookies=cookies, timeout=20)
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                break
            elif resp.status_code == 200 and not resp.text.strip():
                # 偶发空响应，刷新令牌重试
                tokens = _fetch_douyin_tokens()
                if tokens:
                    cookies = {'ttwid': tokens.get('ttwid', ''), 'msToken': tokens.get('msToken', '')}
                continue
        except Exception:
            continue

    if not data:
        _log('⚠️ 抖音 API 未返回视频数据（令牌可能过期，或视频需要登录）', 'warn')
        return None

    # 5. 解析响应
    aweme = data.get('aweme_detail', {}) or {}
    if not aweme:
        _log('⚠️ 抖音 API 响应中缺少视频详情', 'warn')
        return None

    video = aweme.get('video', {})
    if not video:
        _log('⚠️ 视频数据为空（可能已被删除或设为私密）', 'warn')
        return None

    mp4_variants = []

    # download_addr（直接下载地址，去水印）
    download_urls = video.get('download_addr', {}).get('url_list', [])
    for u in download_urls:
        if u:
            mp4_variants.append({'url': u, 'bitrate': 0, 'quality': '下载地址'})
            break

    # play_addr（需要 playwm→play 去水印）
    if not mp4_variants:
        play_urls = video.get('play_addr', {}).get('url_list', [])
        for u in play_urls:
            if u:
                nwm = u.replace('playwm', 'play')
                mp4_variants.append({'url': nwm, 'bitrate': 0, 'quality': '无水印'})
                break

    # bit_rate 各清晰度
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

    # 6. 提取元数据
    author_info = aweme.get('author', {}) or {}
    author = (author_info.get('nickname', '')
              or author_info.get('unique_id', '')
              or '未知')
    title = (aweme.get('desc', '')
             or aweme.get('item_title', '')
             or f'Douyin_{video_id}')[:80].replace('\n', ' ')
    duration = int(aweme.get('duration', 0) or 0)
    cover = ''
    cover_info = video.get('cover', {}) or {}
    cover_urls = cover_info.get('url_list', [])
    if cover_urls:
        cover = cover_urls[0]

    return {
        'video_id': video_id,
        'title': title,
        'author': author,
        'thumbnail': cover,
        'mp4_variants': mp4_variants,
        'm3u8_url': '',
        'duration_ms': duration,
        'cookies': cookies,  # 返回 cookies 供下载使用
    }


# ============================================================
# 下载核心
# ============================================================
class VideoDownloader:
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self._cancel = False
        self._speed_samples = []

    def cancel(self):
        self._cancel = True

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

            # 速度平滑
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

    def _log(self, msg, level='info'):
        if self.log_callback:
            self.log_callback(msg, level)

    def _process_result(self, info, ydl, platform):
        """从 extract_info 结果中提取下载信息"""
        # 处理播放列表/合辑（取第一个有效视频）
        if info.get('_type') == 'playlist' or info.get('entries'):
            entries = info.get('entries', [])
            # 找到第一个有效条目（同时检查文件是否已下载）
            first_valid = None
            for entry in (entries or []):
                if entry:
                    first_valid = entry
                    break
            if first_valid:
                info = first_valid
            else:
                # 检查文件系统上已下载的文件
                has_file = False
                for entry in (entries or []):
                    if entry:
                        fp = ydl.prepare_filename(entry)
                        if os.path.isfile(fp):
                            has_file = True
                            info = entry
                            break
                        # 检查不同扩展名
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

        # 找出实际下载的文件
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

        # Cookie — 优先使用浏览器提取，其次使用文件
        if cookie_browser and cookie_browser != 'none':
            if cookie_browser == 'auto':
                # 自动按优先级尝试：firefox (无 DPAPI 问题) > edge > chrome
                for browser in ('firefox', 'edge', 'chrome'):
                    try:
                        from yt_dlp.cookies import SUPPORTED_BROWSERS
                        if browser in SUPPORTED_BROWSERS:
                            opts['cookiesfrombrowser'] = (browser,)
                            break
                    except Exception:
                        opts['cookiesfrombrowser'] = (browser,)
                        break
            else:
                opts['cookiesfrombrowser'] = (cookie_browser,)
        elif cookie_file and os.path.isfile(cookie_file):
            opts['cookiefile'] = cookie_file

        # 始终显式设置代理 — 无代理时空字符串阻止 yt-dlp 读取系统/环境代理
        opts['proxy'] = proxy if proxy else ''

        # FFmpeg 路径
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            opts['ffmpeg_location'] = ffmpeg_path

        # 字幕
        if subtitles:
            opts['writesubtitles'] = True
            opts['subtitleslangs'] = ['zh-Hans', 'zh', 'en']
            opts['subtitlesformat'] = 'vtt'

        # ----- 平台特定配置 -----
        if platform == 'douyin':
            opts.update({
                'format': 'bestvideo+bestaudio/best',
                'extractor_args': {'douyin': {'use_api': 'mobile'}},
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Pixel 3) '
                                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                                  'Chrome/120.0.0.0 Mobile Safari/537.36',
                    'Referer': 'https://www.douyin.com/',
                },
            })

        elif platform == 'twitter':
            # Twitter/X: GraphQL API（需要 Cookie 登录），优先 MP4 容器
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
            opts.update({
                'format': 'best',
            })

        elif platform == 'xiaohongshu':
            opts.update({
                'format': 'best',
            })

        elif platform == 'weibo':
            opts.update({
                'format': 'bestvideo+bestaudio/best',
            })

        else:
            opts.update({
                'format': 'bestvideo+bestaudio/best',
            })

        return opts

    def _direct_http_download(self, url: str, save_path: str, filename: str,
                               referer: str = '', cookies: dict = None,
                               proxy: str = '') -> str | None:
        """直接 HTTP 下载文件（用于已有直链的场景），返回文件路径或 None"""
        filepath = os.path.join(save_path, filename)
        self._speed_samples = []

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer or 'https://www.google.com/',
            }
            # 代理: 始终显式设置，避免 requests 自动读取系统/环境变量代理
            session = requests.Session()
            session.trust_env = False  # 禁止读取系统代理和环境变量(HTTP_PROXY等)
            if proxy:
                session.proxies = {'http': proxy, 'https': proxy}
            resp = session.get(url, headers=headers, cookies=cookies,
                               stream=True, timeout=30)
            if resp.status_code != 200:
                return None
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 1024 * 1024 * 2  # 2MB
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

    def _try_twitter_direct(self, url: str, save_path: str, proxy: str = '') -> tuple | None:
        """尝试直接解析 Twitter 视频（无需登录，优先 fxtwitter API）
        返回 (ok, data) 或 None（表示需要回退到 yt-dlp）
        """
        self._log('🔍 尝试直接解析视频地址（无需登录）...')
        video_info = parse_twitter_video(url)

        if not video_info:
            self._log('⚠️ 直接解析未获取到视频，回退到 yt-dlp...', 'warn')
            return (False, 'fallback')

        # 优先使用 MP4
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
            # 直接 HTTP 下载 MP4
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
            # 只有 m3u8，回退到 yt-dlp 处理
            self._log('⚠️ 仅有 m3u8 流，回退到 yt-dlp 处理...', 'warn')
            return (False, 'fallback')

    def _try_douyin_direct(self, url: str, save_path: str, proxy: str = '') -> tuple | None:
        """直接调用抖音 API 解析视频（通过 douyin.wtf 令牌生成，无需登录）
        返回 (ok, data) 或 None（表示需要回退到 yt-dlp）
        """
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

        # 直接 HTTP 下载（传入 cookies，CDN URL 需要与 API 调用相同的会话）
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

    def download(self, url: str, save_path: str, quality='best',
                 audio_only=False, cookie_file='', cookie_browser='',
                 proxy='', subtitles=False) -> tuple:
        """下载单个视频，返回 (success, message_or_title)"""
        platform = detect_platform(url)
        os.makedirs(save_path, exist_ok=True)

        self._speed_samples = []

        if self.log_callback:
            pi = PLATFORM_INFO.get(platform)
            name = f'{pi["icon"]} {pi["name"]}' if pi else '未知平台'
            self._log(f'平台: {name}')
            self._log(f'链接: {url}')
            self._log(f'保存: {save_path}')
            if cookie_browser:
                self._log(f'Cookie: 浏览器({cookie_browser})')
            elif cookie_file:
                self._log(f'Cookie: {os.path.basename(cookie_file)}')

        # ── 抖音: 优先通过 douyin.wtf 令牌直接调用抖音 API 并下载 ──
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
            # 如果直接解析失败，回退到 yt-dlp，先尝试获取令牌
            self._log('🔄 回退到 yt-dlp 下载...')
            has_cookie = (cookie_browser and cookie_browser != 'none') or cookie_file
            if not has_cookie:
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
            else:
                self._log(f'🍪 yt-dlp 使用 Cookie: {cookie_browser or os.path.basename(cookie_file)}')

        # ── Twitter/X: 优先使用 fxtwitter API（无需登录）──
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

        # 抖音令牌 cookie 文件（优先于浏览器 cookie）
        if douyin_token_cookie:
            cookie_file = douyin_token_cookie
            cookie_browser = ''

        opts = self._build_opts(platform, save_path, quality, audio_only,
                                cookie_file, cookie_browser, proxy, subtitles)

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

            # FFmpeg 缺失的错误提示
            if any(k in err.lower() for k in ['ffmpeg', 'ffprobe', 'no such file or directory: ff']):
                if self.log_callback:
                    self._log('❌ 错误: 未找到 FFmpeg，无法合并视频流', 'error')
                    self._log('   安装方法: 打开终端(PowerShell)执行 → winget install Gyan.FFmpeg', 'warn')
                    self._log('   然后重启本工具', 'warn')
                return False, '未找到 FFmpeg，无法合并视频流。请安装 FFmpeg: winget install Gyan.FFmpeg'

            # DPAPI 解密失败（Chrome 常见问题 — 推荐用 Firefox）
            if 'DPAPI' in err or 'dpapi' in err.lower():
                if self.log_callback:
                    self._log('❌ Chrome Cookie 解密失败 (DPAPI)', 'error')
                    self._log('   💡 解决方案: Cookie 下拉选择 Firefox（无 DPAPI 问题）', 'warn')
                    self._log('   或使用 "Get cookies.txt" 扩展导出 cookie.txt 文件', 'warn')
                return False, 'Chrome DPAPI 解密失败，请改用 Firefox 或导出 cookie.txt'

            # 浏览器 Cookie 数据库锁定（用户未关闭浏览器）
            if 'could not copy' in err.lower() and 'cookie' in err.lower():
                if self.log_callback:
                    self._log('❌ 无法读取浏览器 Cookie — 请先关闭浏览器再试', 'error')
                    self._log('   💡 如果仍然失败，Cookie 下拉选择 Firefox（兼容性更好）', 'warn')
                return False, '浏览器 Cookie 数据库被锁定，请先关闭浏览器再试'

            # Twitter 登录相关错误的智能提示
            if platform == 'twitter' and any(k in err.lower() for k in [
                'login', 'author', 'guest', '403', '401', 'cookie', 'private'
            ]):
                # 尝试用更宽松的格式重试
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

                if self.log_callback:
                    self._log('💡 提示: Twitter/X 需要登录才能访问此内容', 'warn')
                    self._log('   请在 Cookie 栏选择浏览器导出的 cookie.txt 文件后重试', 'warn')
                    self._log('   导出方法: 安装 "Get cookies.txt" 浏览器扩展', 'warn')

            # 抖音 Cookie 相关错误
            if platform == 'douyin' and any(k in err.lower() for k in [
                'fresh cookies', 'cookie', 'need login', 'private'
            ]):
                if self.log_callback:
                    self._log('❌ 抖音下载失败 — 需要浏览器 Cookie', 'error')
                    self._log('   💡 打开 Firefox 访问一次 douyin.com，然后本工具选择 Firefox Cookie', 'warn')
                    self._log('   或使用 "Get cookies.txt" 扩展导出 cookie.txt 文件', 'warn')
                return False, '抖音需要浏览器 Cookie，请在 Cookie 下拉选择 Firefox 后重试'

            # 如果格式问题导致失败，尝试用简单格式重试
            if 'format' in err.lower() and quality != 'best':
                self._log('⚠️ 画质选项失败，尝试使用最佳可用画质重试...')
                opts2 = self._build_opts(platform, save_path, 'best', audio_only,
                                         cookie_file, cookie_browser, proxy, False)
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

            if self.log_callback:
                self._log(f'❌ 错误: {err}')
            return False, err
        finally:
            # 清理临时令牌 cookie 文件
            if douyin_token_cookie:
                try:
                    os.unlink(douyin_token_cookie)
                except Exception:
                    pass


# ============================================================
# 批量下载管理器
# ============================================================
class BatchDownloader:
    def __init__(self, downloader: VideoDownloader):
        self.downloader = downloader
        self.results = []
        self._cancel = False

    def cancel(self):
        self._cancel = True
        self.downloader.cancel()

    def run(self, urls, save_path, quality='best', audio_only=False,
            cookie_file='', cookie_browser='', proxy='', subtitles=False):
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
                                                cookie_browser, proxy, subtitles)
            self.results.append((url, ok, data))
        return self.results


# ============================================================
# GUI 应用
# ============================================================
class Application:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()

        self.root.title('视频下载工具 v3.0')
        w = self.cfg.get('window_width', 820)
        h = self.cfg.get('window_height', 780)
        self.root.geometry(f'{w}x{h}')
        self.root.minsize(720, 620)

        self._setup_styles()

        # 状态
        self.downloading = False
        self.downloader = None
        self.batch_downloader = None
        self.current_results = []
        self.queue = queue.Queue()

        # 变量
        self.save_path = tk.StringVar(value=self.cfg.get('save_path', ''))
        self.audio_only = tk.BooleanVar(value=self.cfg.get('audio_only', False))
        self.auto_open = tk.BooleanVar(value=self.cfg.get('auto_open_folder', True))
        self.subtitle_var = tk.BooleanVar(value=False)
        self.cookie_path = tk.StringVar(value=self.cfg.get('cookie_file', ''))
        self.cookie_browser = tk.StringVar(value=self.cfg.get('cookie_browser', 'none'))
        self.proxy_var = tk.StringVar(value=self.cfg.get('proxy', ''))
        self.quality_var = tk.StringVar(value='best')

        self._create_widgets()
        self._poll_queue()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._center_window()
        self._log('就绪 — 支持平台: 抖音 · Twitter/X · YouTube · BiliBili · Instagram · 小红书 · 微博', 'info')
        self._log('提示: 粘贴链接后自动识别平台，支持批量下载（每行一个链接）', 'info')
        if not check_ffmpeg():
            self._log('⚠️ 未检测到 FFmpeg — 视频合并(HD画质)/音频提取/AI配音字幕等功能将无法使用', 'warn')
            self._log('   安装方法: 打开终端(PowerShell)执行 → winget install Gyan.FFmpeg', 'warn')
            self._log('   或手动下载: https://www.gyan.dev/ffmpeg/builds/ ', 'warn')
        if self.cfg.get('cookie_browser') and self.cfg['cookie_browser'] != 'none':
            self._log(f'🍪 浏览器 Cookie 已启用: {self.cfg["cookie_browser"]}', 'info')
        elif self.cfg.get('cookie_file'):
            self._log(f'📄 Cookie 文件已加载: {os.path.basename(self.cfg["cookie_file"])}', 'info')
        self._log('💡 提示: Twitter/X/Instagram 推荐 Cookie 下拉选 Firefox（兼容性最好）', 'info')

    # ----- 样式 -----
    def _setup_styles(self):
        style = ttk.Style()
        for t in ('vista', 'clam', 'default'):
            try:
                style.theme_use(t)
                break
            except:
                continue
        self.C_BG = '#1e1e1e'
        self.C_FG = '#d4d4d4'
        self.C_OK = '#4EC9B0'
        self.C_ERR = '#f44747'
        self.C_WARN = '#CE9178'
        self.C_HL = '#569CD6'

    # ----- 界面 -----
    def _create_widgets(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        # ======== Header ========
        hdr = ttk.Frame(main)
        hdr.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(hdr, text='📥 视频下载工具',
                  font=('微软雅黑', 16, 'bold')).pack(side=tk.LEFT)
        self.platform_indicator = ttk.Label(hdr, text='',
                                            font=('微软雅黑', 9), foreground='#999')
        self.platform_indicator.pack(side=tk.LEFT, padx=12)

        # ======== 选项栏 ========
        opt_frame = ttk.Frame(main)
        opt_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(opt_frame, text='画质:').pack(side=tk.LEFT)
        self.quality_combo = ttk.Combobox(opt_frame, textvariable=self.quality_var,
                                          values=['best', '1080p', '720p', '480p'],
                                          width=8, state='readonly')
        self.quality_combo.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Checkbutton(opt_frame, text='仅音频(MP3)',
                        variable=self.audio_only).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(opt_frame, text='下载字幕',
                        variable=self.subtitle_var).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(opt_frame, text='自动打开文件夹',
                        variable=self.auto_open).pack(side=tk.LEFT)

        # ======== URL 输入 ========
        url_box = ttk.LabelFrame(main, text=' 视频链接（每行一个，支持批量）', padding=10)
        url_box.pack(fill=tk.X, pady=(0, 8))

        self.url_text = scrolledtext.ScrolledText(
            url_box, height=4, wrap=tk.WORD,
            font=('微软雅黑', 10),
            relief=tk.FLAT, borderwidth=2,
        )
        self.url_text.pack(fill=tk.X)
        self.url_text.bind('<Control-Return>', lambda e: self._start_download())
        self.url_text.bind('<Command-Return>', lambda e: self._start_download())

        btn_row = ttk.Frame(url_box)
        btn_row.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(btn_row, text='📋 粘贴', command=self._paste_url, width=8).pack(side=tk.LEFT)
        ttk.Button(btn_row, text='清空', command=lambda: self.url_text.delete(1.0, tk.END), width=6).pack(side=tk.LEFT, padx=4)

        self.url_count_label = ttk.Label(btn_row, text='', font=('微软雅黑', 9), foreground='#999')
        self.url_count_label.pack(side=tk.RIGHT)

        # URL 变化检测
        self.url_text.bind('<KeyRelease>', self._on_url_change)

        # ======== 保存路径 ========
        path_box = ttk.LabelFrame(main, text=' 保存位置 ', padding=10)
        path_box.pack(fill=tk.X, pady=(0, 8))

        row = ttk.Frame(path_box)
        row.pack(fill=tk.X)
        self.path_entry = ttk.Entry(row, textvariable=self.save_path, font=('微软雅黑', 9))
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(row, text='📂 浏览', command=self._browse, width=8).pack(side=tk.RIGHT)
        ttk.Button(row, text='📂 打开', command=self._open_folder, width=8).pack(side=tk.RIGHT, padx=(0, 5))

        # ======== Cookie / 代理 ========
        adv_frame = ttk.Frame(main)
        adv_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(adv_frame, text='Cookie:', font=('微软雅黑', 9)).pack(side=tk.LEFT)
        self.cookie_browser_combo = ttk.Combobox(adv_frame, textvariable=self.cookie_browser,
                                                  values=['none', 'firefox', 'edge', 'chrome', 'brave', 'opera'],
                                                  width=8, state='readonly')
        self.cookie_browser_combo.pack(side=tk.LEFT, padx=(0, 4))
        self.cookie_browser_combo.set(self.cookie_browser.get())  # 读取配置文件中的值，不硬编码覆盖
        self.cookie_entry = ttk.Entry(adv_frame, textvariable=self.cookie_path, font=('微软雅黑', 9))
        self.cookie_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(adv_frame, text='📄 选择', command=self._browse_cookie, width=7).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(adv_frame, text='代理:', font=('微软雅黑', 9)).pack(side=tk.LEFT)
        self.proxy_entry = ttk.Entry(adv_frame, textvariable=self.proxy_var, font=('微软雅黑', 9), width=20)
        self.proxy_entry.pack(side=tk.LEFT, padx=4)

        # ======== 下载按钮 ========
        self.btn_download = ttk.Button(main, text='⬇ 开始下载',
                                       command=self._start_download)
        self.btn_download.pack(fill=tk.X, pady=4, ipady=4)

        # ======== 下载进度 ========
        prog_box = ttk.LabelFrame(main, text=' 下载进度 ', padding=10)
        prog_box.pack(fill=tk.X, pady=(0, 8))

        self.progress_bar = ttk.Progressbar(prog_box, mode='determinate')
        self.progress_bar.pack(fill=tk.X)

        self.progress_label = ttk.Label(prog_box, text='就绪',
                                        font=('微软雅黑', 9), foreground='#999')
        self.progress_label.pack(anchor=tk.W, pady=(3, 0))

        # ======== 日志 ========
        log_box = ttk.LabelFrame(main, text=' 运行日志 ', padding=10)
        log_box.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_box, height=10, wrap=tk.WORD,
                                font=('Consolas', 10),
                                bg=self.C_BG, fg=self.C_FG,
                                insertbackground='white',
                                relief=tk.FLAT, borderwidth=2)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(log_box, orient=tk.VERTICAL, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scroll.set)

        # 日志按钮行
        log_btn_row = ttk.Frame(main)
        log_btn_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(log_btn_row, text='🧹 清空日志',
                   command=self._clear_log, width=12).pack(side=tk.LEFT)
        ttk.Button(log_btn_row, text='📂 打开下载目录',
                   command=self._open_folder, width=14).pack(side=tk.RIGHT)

        # 状态栏
        self.status_bar = ttk.Label(main, text='就绪', font=('微软雅黑', 9),
                                    foreground='#888', anchor=tk.W)
        self.status_bar.pack(fill=tk.X, pady=(4, 0))

    # ----- 辅助 -----
    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f'{w}x{h}+{x}+{y}')

    def _get_urls(self):
        text = self.url_text.get(1.0, tk.END).strip()
        urls = []
        for line in text.splitlines():
            line = line.strip()
            if line and line.startswith(('http://', 'https://')):
                urls.append(line)
        return urls

    def _update_url_count(self):
        urls = self._get_urls()
        if urls:
            self.url_count_label.config(text=f'识别 {len(urls)} 个链接')
        else:
            self.url_count_label.config(text='')

    def _on_url_change(self, *args):
        self._update_url_count()
        # 检测第一个有效的 URL 平台
        urls = self._get_urls()
        if urls:
            platform = detect_platform(urls[0])
            pi = PLATFORM_INFO.get(platform)
            if pi:
                self.platform_indicator.config(
                    text=f'{pi["icon"]} {pi["name"]} — {pi["desc"]}',
                    foreground=pi['color'])
                return
        self.platform_indicator.config(text='', foreground='#999')

    def _paste_url(self):
        try:
            text = self.root.clipboard_get().strip()
            self.url_text.delete(1.0, tk.END)
            # 检测多行或空格分隔的 URL
            lines = re.split(r'[\n\r\s]+', text)
            if len(lines) > 1 or ('\n' in text):
                # 批量粘贴
                for line in lines:
                    line = line.strip()
                    if line:
                        self.url_text.insert(tk.END, line + '\n')
            else:
                self.url_text.insert(1.0, text)
            self._on_url_change()
        except Exception:
            pass

    def _browse(self):
        current = self.save_path.get().strip()
        initial = current if (current and os.path.isdir(current)) else str(Path.home() / 'Downloads')
        path = filedialog.askdirectory(title='选择保存目录', initialdir=initial)
        if path:
            self.save_path.set(path)
            self.cfg['save_path'] = path
            save_config(self.cfg)

    def _open_folder(self):
        path = self.save_path.get().strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showinfo('提示', '目录不存在，请先创建或下载一个视频')

    def _browse_cookie(self):
        path = filedialog.askopenfilename(
            title='选择 Cookie 文件 (Netscape格式)',
            filetypes=[('Cookie 文件', '*.txt'), ('所有文件', '*.*')])
        if path:
            self.cookie_path.set(path)

    def _clear_log(self):
        self.log_text.delete(1.0, tk.END)

    # ----- 日志与进度 -----
    def _log(self, msg, level='info'):
        colors = {
            'info': '#d4d4d4',
            'success': self.C_OK,
            'error': self.C_ERR,
            'warn': self.C_WARN,
            'highlight': self.C_HL,
        }
        color = colors.get(level, '#d4d4d4')
        tag = f'tag_{id(msg)}_{time.time_ns()}'
        self.log_text.tag_configure(tag, foreground=color)
        ts = time.strftime('%H:%M:%S')
        self.log_text.insert(tk.END, f'[{ts}] {msg}\n', tag)
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _update_progress(self, info):
        pct = info.get('percent', 0)
        self.progress_bar['value'] = pct
        parts = [f'进度: {pct:.1f}%']
        for k, label in [('speed', '速度'), ('eta', '剩余'), ('downloaded', '已下载'), ('total', '总计')]:
            v = info.get(k, '')
            if v:
                parts.append(f'{label}: {v}')
        self.progress_label.config(text='  |  '.join(parts))
        self.root.update_idletasks()

    # ----- 下载控制 -----
    def _start_download(self):
        if self.downloading:
            # 正在下载时点击为取消
            self._cancel_download()
            return

        urls = self._get_urls()
        if not urls:
            messagebox.showwarning('提示', '请粘贴视频链接（以 http:// 或 https:// 开头）')
            self.url_text.focus()
            return

        save_path = self.save_path.get().strip()
        if not save_path:
            messagebox.showwarning('提示', '请选择保存目录')
            return
        os.makedirs(save_path, exist_ok=True)

        # 切换到下载状态
        self.downloading = True
        self.btn_download.config(text='⏹ 取消下载', state=tk.NORMAL)
        self.progress_bar['value'] = 0
        self.progress_label.config(text='准备中...')
        self.status_bar.config(text='下载中...')

        quality = self.quality_var.get()
        audio = self.audio_only.get()
        cookie = self.cookie_path.get().strip()
        cookie_browser = self.cookie_browser.get().strip()
        proxy = self.proxy_var.get().strip()
        subs = self.subtitle_var.get()

        count = len(urls)
        self._log('━' * 42, 'highlight')
        self._log(f'📥 开始下载 {count} 个视频' if count > 1 else '📥 开始下载', 'highlight')
        self._log(f'画质: {quality}  |  音频: {"是" if audio else "否"}  |  字幕: {"是" if subs else "否"}', 'info')
        self._log(f'保存: {save_path}', 'info')

        # 后台下载
        self.main_downloader = VideoDownloader(
            progress_callback=lambda info: self._enqueue(('progress', info)),
            log_callback=lambda msg, level='info': self._enqueue(('log', (msg, level))),
        )

        if count > 1:
            self.batch_downloader = BatchDownloader(self.main_downloader)
            t = threading.Thread(
                target=self._batch_thread,
                args=(urls, save_path, quality, audio, cookie, cookie_browser, proxy, subs),
                daemon=True,
            )
        else:
            t = threading.Thread(
                target=self._single_thread,
                args=(urls[0], save_path, quality, audio, cookie, cookie_browser, proxy, subs),
                daemon=True,
            )
        t.start()

    def _single_thread(self, url, save_path, quality, audio, cookie, cookie_browser, proxy, subs):
        ok, data = self.main_downloader.download(
            url, save_path, quality, audio, cookie, cookie_browser, proxy, subs)
        self._enqueue(('done', [(url, ok, data)]))

    def _batch_thread(self, urls, save_path, quality, audio, cookie, cookie_browser, proxy, subs):
        bd = self.batch_downloader
        results = bd.run(urls, save_path, quality, audio, cookie, cookie_browser, proxy, subs)
        self._enqueue(('done', results))

    def _cancel_download(self):
        if self.main_downloader:
            self.main_downloader.cancel()
        self._log('⏹ 用户取消下载', 'warn')

    def _enqueue(self, item):
        self.queue.put(item)

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self.queue.get_nowait()

                if msg_type == 'progress':
                    self._update_progress(data)
                elif msg_type == 'log':
                    msg_text, level = data
                    self._log(msg_text, level)
                elif msg_type == 'done':
                    self._on_download_done(data)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _on_download_done(self, results):
        success_count = sum(1 for _, ok, _ in results if ok)
        fail_count = sum(1 for _, ok, _ in results if not ok)
        total = len(results)

        for url, ok, data in results:
            if ok and isinstance(data, dict) and data.get('path'):
                self.current_results.append(data)
                # 记录到历史
                self.cfg.setdefault('download_history', []).append({
                    'title': data.get('title', ''),
                    'platform': data.get('platform', ''),
                    'time': datetime.now().isoformat(),
                    'path': data.get('path', ''),
                })
                # 清理旧历史（保留最近 200 条）
                if len(self.cfg['download_history']) > 200:
                    self.cfg['download_history'] = self.cfg['download_history'][-200:]
                save_config(self.cfg)

        self._log('')
        if total == 1 and success_count == 1:
            self._log('✅ 下载成功！', 'success')
            self.progress_label.config(text='✅ 下载完成')
            self.status_bar.config(text='✅ 下载完成')

            # 打开文件夹
            data = results[0][2]
            if isinstance(data, dict) and self.auto_open.get():
                fp = data.get('path', '')
                if fp and os.path.isfile(fp):
                    self.root.after(300, lambda: os.startfile(os.path.dirname(fp)))

        elif total > 1:
            self._log(f'✅ 批量下载完成: {success_count}/{total} 个成功'
                      + (f', {fail_count} 个失败' if fail_count else ''), 'success')
            self.progress_label.config(
                text=f'✅ 完成: {success_count}/{total}')
            self.status_bar.config(
                text=f'✅ 完成: {success_count}/{total}'
                      + (f', {fail_count} 个失败' if fail_count else ''))

            if self.auto_open.get():
                self.root.after(300, lambda: os.startfile(self.save_path.get()))
        else:
            self._log('❌ 下载失败', 'error')
            self.progress_label.config(text='❌ 下载失败')
            self.status_bar.config(text='❌ 下载失败')

        # 显示失败详情
        for url, ok, data in results:
            if not ok:
                self._log(f'  ❌ {url[:60]}... → {data}', 'error')

        self.downloading = False
        self.main_downloader = None
        self.batch_downloader = None
        self.btn_download.config(text='⬇ 开始下载', state=tk.NORMAL)

    def _on_close(self):
        # 保存配置
        self.cfg['save_path'] = self.save_path.get()
        self.cfg['audio_only'] = self.audio_only.get()
        self.cfg['auto_open_folder'] = self.auto_open.get()
        self.cfg['cookie_file'] = self.cookie_path.get()
        self.cfg['cookie_browser'] = self.cookie_browser.get()
        self.cfg['proxy'] = self.proxy_var.get()
        try:
            self.cfg['window_width'] = self.root.winfo_width()
            self.cfg['window_height'] = self.root.winfo_height()
        except Exception:
            pass
        save_config(self.cfg)

        if self.downloading:
            if messagebox.askyesno('确认退出', '下载正在进行中，确定要退出吗？'):
                if self.main_downloader:
                    self.main_downloader.cancel()
                self.root.destroy()
        else:
            self.root.destroy()


# ============================================================
# 启动
# ============================================================
def main():
    root = tk.Tk()
    app = Application(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        if app.main_downloader:
            app.main_downloader.cancel()
        root.destroy()


if __name__ == '__main__':
    main()
