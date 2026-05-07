#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手机版视频下载工具 (Termux / 命令行)
支持: 抖音 · Twitter/X · YouTube · BiliBili · Instagram · 小红书 · 微博
默认保存至手机相册 /sdcard/DCIM/

用法:
  python mobile_downloader.py "https://v.douyin.com/xxxx/"
  python mobile_downloader.py -q 1080p "https://youtube.com/watch?v=xxx"
  python mobile_downloader.py -a "https://youtube.com/watch?v=xxx"   # 仅音频
  python mobile_downloader.py links.txt                                # 批量文件
"""

import sys
import os
import re
import json
import time
import math
import argparse
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

import requests
import yt_dlp
from yt_dlp.jsinterp import js_number_to_string

# ── 终端颜色 ────────────────────────────────────────────
class Color:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

def cprint(msg, color='', bold=False):
    prefix = Color.BOLD if bold else ''
    print(f'{prefix}{color}{msg}{Color.RESET}')
    sys.stdout.flush()

# ── 配置 ────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / 'mobile_config.json'

# 默认保存路径：Android 相册
DEFAULT_SAVE_PATH = '/sdcard/DCIM/VideoDownload'
if not os.path.isdir('/sdcard'):
    # 非 Android 环境回退
    DEFAULT_SAVE_PATH = str(Path.home() / 'Downloads' / 'VideoDownload')

DEFAULT_CONFIG = {
    'save_path': DEFAULT_SAVE_PATH,
    'audio_only': False,
    'cookie_file': '',
    'proxy': '',
    'quality': 'best',
}


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


def check_ffmpeg() -> bool:
    if shutil.which('ffmpeg'):
        return True
    # Termux 常见路径
    for loc in ['/data/data/com.termux/files/usr/bin/ffmpeg']:
        if os.path.isfile(loc):
            return True
    return False


def find_ffmpeg() -> str | None:
    result = shutil.which('ffmpeg')
    if result:
        return result
    termux_path = '/data/data/com.termux/files/usr/bin/ffmpeg'
    if os.path.isfile(termux_path):
        return termux_path
    return None


# ── 平台定义 ────────────────────────────────────────────
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

    # 策略1: fxtwitter API
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
                            'url': v_url,
                            'bitrate': v.get('bitrate', 0) or 0,
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

    # 策略2: Syndication API
    try:
        num = (int(tweet_id) / 1e15) * math.pi
        token = js_number_to_string(num, 36).translate(str.maketrans('', '', '.0'))
        resp = requests.get(
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
                        return {
                            'tweet_id': tweet_id, 'title': title,
                            'author': f'{author} (@{screen_name})' if author else screen_name,
                            'text': text, 'mp4_variants': mp4_variants, 'm3u8_url': m3u8_url,
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
    """获取 douyin.wtf 令牌，每个端点失败时重试一次"""
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
                _log('  ⚠ ttwid 获取失败，1秒后重试...')
                time.sleep(1)
        except Exception:
            if attempt == 0:
                _log('  ⚠ ttwid 网络异常，1秒后重试...')
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
                _log('  ⚠ msToken 获取失败，1秒后重试...')
                time.sleep(1)
        except Exception:
            if attempt == 0:
                _log('  ⚠ msToken 网络异常，1秒后重试...')
                time.sleep(1)

    return tokens if tokens else None


def _write_douyin_cookie_file(tokens):
    try:
        fd, path = tempfile.mkstemp(suffix='.txt', prefix='douyin_cookies_')
        with os.fdopen(fd, 'w') as f:
            f.write('# Netscape HTTP Cookie File\n')
            f.write('# Generated by mobile_downloader\n\n')
            for name, value in tokens.items():
                f.write(f'.douyin.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n')
                f.write(f'.iesdouyin.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n')
        return path
    except Exception:
        return None


def _resolve_douyin_short(url):
    """短链接还原：HEAD 跟踪 → GET 提取 HTML"""
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

    # 1. 提取视频 ID
    video_id = _extract_douyin_video_id(url)
    if not video_id:
        _log('  ❌ 无法从链接中提取视频 ID')
        return None

    # 2. 获取令牌
    tokens = _fetch_douyin_tokens(on_log=on_log)
    if not tokens:
        _log('  ❌ douyin.wtf 令牌接口不可用')
        return None

    ttwid = tokens.get('ttwid', '')
    msToken = tokens.get('msToken', '')
    cookies = {'ttwid': ttwid, 'msToken': msToken}

    # 3. 生成 A-Bogus
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
        _log('  ❌ 无法生成 A-Bogus 签名')
        return None

    # 4. 调用抖音 API（含重试）
    data = None
    for attempt in range(3):
        try:
            full_url = f'{douyin_api}&a_bogus={a_bogus}'
            headers = {
                'User-Agent': _DOUYIN_UA,
                'Referer': 'https://www.douyin.com/',
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
        _log('  ❌ 抖音 API 未返回视频数据')
        return None

    # 5. 解析响应
    aweme = data.get('aweme_detail', {}) or {}
    if not aweme:
        _log('  ❌ 响应中缺少视频详情')
        return None

    video = aweme.get('video', {})
    if not video:
        _log('  ❌ 视频数据为空（可能已删除或私密）')
        return None

    mp4_variants = []

    # download_addr（去水印地址）
    download_urls = video.get('download_addr', {}).get('url_list', [])
    for u in download_urls:
        if u:
            mp4_variants.append({'url': u, 'bitrate': 0, 'quality': '下载地址'})
            break

    # play_addr（playwm→play 去水印）
    if not mp4_variants:
        play_urls = video.get('play_addr', {}).get('url_list', [])
        for u in play_urls:
            if u:
                nwm = u.replace('playwm', 'play')
                mp4_variants.append({'url': nwm, 'bitrate': 0, 'quality': '无水印'})
                break

    # bit_rate 清晰度
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
        _log('  ❌ 未找到可下载的视频流')
        return None

    # 元数据
    author_info = aweme.get('author', {}) or {}
    author = (author_info.get('nickname', '')
              or author_info.get('unique_id', '')
              or '未知')
    title = (aweme.get('desc', '')
             or aweme.get('item_title', '')
             or f'Douyin_{video_id}')[:80].replace('\n', ' ')
    duration = int(aweme.get('duration', 0) or 0)

    return {
        'video_id': video_id, 'title': title, 'author': author,
        'mp4_variants': mp4_variants, 'm3u8_url': '',
        'duration_ms': duration, 'cookies': cookies,
    }


# ── 下载核心 ────────────────────────────────────────────
class VideoDownloader:
    def __init__(self, proxy=''):
        self._cancel = False
        self._proxy = proxy
        self._last_pct = -1

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

    def _progress_hook_ytdlp(self, d):
        if self._cancel:
            raise Exception('用户取消下载')
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            eta = d.get('eta', 0)
            if total > 0:
                pct = downloaded / total * 100
            else:
                pct = 0
            eta_str = f'{eta // 60}:{eta % 60:02d}' if eta else '--:--'
            # 每 5% 输出一次，避免刷屏
            if int(pct / 5) != self._last_pct:
                self._last_pct = int(pct / 5)
                bar_len = 20
                filled = int(bar_len * pct / 100)
                bar = '█' * filled + '░' * (bar_len - filled)
                print(f'\r  [{bar}] {pct:5.1f}%  {self._format_speed(speed):>10s}  剩余 {eta_str}',
                      end='', flush=True)

    def _direct_http_download(self, url, save_path, filename, referer='', cookies=None):
        """直接 HTTP 下载（用于已有直链）"""
        filepath = os.path.join(save_path, filename)
        self._last_pct = -1
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
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
                        pct = (downloaded / total * 100) if total > 0 else 0
                        eta = (total - downloaded) / speed if speed > 0 and total > 0 else 0
                        eta_str = f'{int(eta // 60)}:{int(eta % 60):02d}' if eta else '--:--'
                        if int(pct / 5) != self._last_pct:
                            self._last_pct = int(pct / 5)
                            bar_len = 20
                            filled = int(bar_len * pct / 100)
                            bar = '█' * filled + '░' * (bar_len - filled)
                            print(f'\r  [{bar}] {pct:5.1f}%  {self._format_speed(speed):>10s}  剩余 {eta_str}',
                                  end='', flush=True)
            print()
            return filepath
        except Exception:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return None

    def _try_douyin_direct(self, url, save_path):
        cprint('  🔍 直连抖音 API 解析...', Color.CYAN)
        video_info = parse_douyin_video(url, on_log=lambda msg: cprint(msg, Color.YELLOW))

        if not video_info:
            return None

        best_mp4 = video_info['mp4_variants'][0] if video_info['mp4_variants'] else None
        if not best_mp4:
            return None

        title = video_info['title']
        author = video_info['author']
        quality = best_mp4.get('quality', '')
        duration_ms = int(video_info['duration_ms']) if video_info['duration_ms'] else 0
        duration_s = duration_ms // 1000
        dur_str = f'{duration_s // 60}:{duration_s % 60:02d}' if duration_s else '--:--'
        cookies = video_info.get('cookies', {})

        print(f'  ✅ 解析成功: {title}')
        print(f'     作者: {author}  |  时长: {dur_str}' + (f'  |  类型: {quality}' if quality else ''))

        safe_title = re.sub(r'[\\/*?:"<>|]', '', title)
        vid = video_info.get('video_id', '') or 'douyin'
        filename = f'{safe_title}_{vid}.mp4'
        cprint('  ⬇ 直接 HTTP 下载...', Color.CYAN)
        filepath = self._direct_http_download(
            best_mp4['url'], save_path, filename,
            referer='https://www.douyin.com/',
            cookies=cookies,
        )
        if self._cancel:
            return None
        if filepath:
            size = os.path.getsize(filepath)
            return {'title': title, 'path': filepath, 'size': size, 'platform': 'douyin',
                    'author': author, 'duration': dur_str}
        return None

    def _try_twitter_direct(self, url, save_path):
        cprint('  🔍 直连 Twitter API 解析...', Color.CYAN)
        video_info = parse_twitter_video(url)

        if not video_info:
            return None

        best_mp4 = video_info['mp4_variants'][0] if video_info['mp4_variants'] else None
        if not best_mp4:
            return None

        title = video_info['title']
        author = video_info['author']
        duration_ms = int(video_info['duration_ms']) if video_info['duration_ms'] else 0
        duration_s = duration_ms // 1000
        dur_str = f'{duration_s // 60}:{duration_s % 60:02d}' if duration_s else '--:--'

        print(f'  ✅ 解析成功: {title}')
        print(f'     作者: {author}  |  时长: {dur_str}')
        if best_mp4.get('height'):
            print(f'     画质: {best_mp4["height"]}p')

        safe_title = re.sub(r'[\\/*?:"<>|]', '', title)
        filename = f'{safe_title}_{video_info["tweet_id"]}.mp4'
        cprint('  ⬇ 直接 HTTP 下载...', Color.CYAN)
        filepath = self._direct_http_download(
            best_mp4['url'], save_path, filename, referer=url, cookies=None,
        )
        if self._cancel:
            return None
        if filepath:
            size = os.path.getsize(filepath)
            return {'title': title, 'path': filepath, 'size': size, 'platform': 'twitter',
                    'author': author, 'duration': dur_str}
        return None

    def download(self, url, save_path, quality='best', audio_only=False,
                 cookie_file='', subtitles=False):
        """下载单个视频，返回 (success, data_or_error)"""
        platform = detect_platform(url)
        os.makedirs(save_path, exist_ok=True)
        self._last_pct = -1

        pi = PLATFORM_INFO.get(platform)
        name = f'{pi["icon"]} {pi["name"]}' if pi else '未知平台'
        print(f'\n{"─" * 50}')
        print(f'  平台: {name}')
        print(f'  链接: {url[:70]}{"..." if len(url) > 70 else ""}')
        print(f'  保存: {save_path}')

        # ── 抖音: 优先直连 API ──
        douyin_token_cookie = None
        if platform == 'douyin':
            result = self._try_douyin_direct(url, save_path)
            if result:
                return True, result
            cprint('  🔄 回退到 yt-dlp...', Color.YELLOW)
            if not cookie_file:
                cprint('  🔑 尝试获取令牌...', Color.CYAN)
                tokens = _fetch_douyin_tokens(on_log=lambda msg: cprint(msg, Color.YELLOW))
                if tokens:
                    douyin_token_cookie = _write_douyin_cookie_file(tokens)
                    if douyin_token_cookie:
                        cprint(f'  ✅ 已获取令牌 (ttwid={tokens.get("ttwid", "")[:16]}...)', Color.GREEN)
                if not douyin_token_cookie:
                    cprint('  ⚠ 令牌获取失败，尝试无 Cookie 下载...', Color.YELLOW)

        # ── Twitter: 优先直连 API ──
        if platform == 'twitter':
            result = self._try_twitter_direct(url, save_path)
            if result:
                return True, result
            cprint('  🔄 回退到 yt-dlp...', Color.YELLOW)

        # ── yt-dlp 下载 ──
        if douyin_token_cookie:
            cookie_file = douyin_token_cookie

        opts = {
            'outtmpl': os.path.join(save_path, '%(title)s.%(ext)s'),
            'progress_hooks': [self._progress_hook_ytdlp],
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'retries': 5,
            'fragment_retries': 5,
            'extract_flat': False,
        }

        if cookie_file and os.path.isfile(cookie_file):
            opts['cookiefile'] = cookie_file

        if self._proxy:
            opts['proxy'] = self._proxy

        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            opts['ffmpeg_location'] = ffmpeg_path

        # 平台配置
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
            opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best'
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
        else:
            opts['format'] = 'bestvideo+bestaudio/best'

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if self._cancel:
                    return False, '下载已取消'
                if info is None:
                    return False, '无法解析视频信息，链接可能无效'
                print()  # 换行

                # 处理结果
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
                        return False, '该链接不包含可下载的视频'

                title = info.get('title', '未知标题') or '未知标题'
                duration = info.get('duration', 0)
                dur_str = f'{duration // 60}:{duration % 60:02d}' if duration else '--:--'

                fp = ydl.prepare_filename(info)
                if not os.path.isfile(fp):
                    for e in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                        p = fp.rsplit('.', 1)[0] + f'.{e}'
                        if os.path.isfile(p):
                            fp = p
                            break

                size = os.path.getsize(fp) if os.path.isfile(fp) else 0

                print(f'  ✅ 下载成功: {title}')
                print(f'     时长: {dur_str}  |  大小: {self._format_size(size)}')
                print(f'     位置: {fp}')

                return True, {'title': title, 'path': fp, 'size': size, 'platform': platform}
        except Exception as e:
            print()
            err = str(e)
            if 'cancel' in err.lower():
                return False, '下载已取消'
            return False, err
        finally:
            if douyin_token_cookie:
                try:
                    os.unlink(douyin_token_cookie)
                except Exception:
                    pass


# ── 批量下载 ────────────────────────────────────────────
def batch_download(urls, save_path, quality='best', audio_only=False,
                   cookie_file='', proxy='', subtitles=False):
    downloader = VideoDownloader(proxy=proxy)
    results = []
    total = len(urls)
    success = 0
    fail = 0

    for i, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue
        cprint(f'\n{"=" * 50}', Color.BOLD)
        cprint(f'  [{i + 1}/{total}]', Color.BOLD)
        ok, data = downloader.download(url, save_path, quality=quality,
                                       audio_only=audio_only, cookie_file=cookie_file,
                                       subtitles=subtitles)
        results.append((url, ok, data))
        if ok:
            success += 1
        else:
            fail += 1
            cprint(f'  ❌ 失败: {data}', Color.RED)

    cprint(f'\n{"─" * 50}', Color.BOLD)
    cprint(f'  完成: {success} 成功' + (f', {fail} 失败' if fail else ''), Color.BOLD)
    return results


# ── 命令行入口 ──────────────────────────────────────────
def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description='手机版视频下载工具 — 支持抖音/Twitter/YouTube/BiliBili/Instagram/小红书/微博',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python mobile_downloader.py "https://v.douyin.com/xxxx/"
  python mobile_downloader.py -q 1080p "https://youtube.com/watch?v=xxx"
  python mobile_downloader.py -a "https://youtube.com/watch?v=xxx"
  python mobile_downloader.py links.txt            # 批量下载
  python mobile_downloader.py -l                  # 读取剪贴板
        ''',
    )

    parser.add_argument('url', nargs='?', help='视频链接（或包含链接列表的文本文件路径）')
    parser.add_argument('-q', '--quality', default=cfg.get('quality', 'best'),
                        choices=['best', '1080p', '720p', '480p'],
                        help='画质 (默认: best)')
    parser.add_argument('-a', '--audio-only', action='store_true',
                        help='仅下载音频 (输出 MP3)')
    parser.add_argument('-s', '--subtitles', action='store_true',
                        help='下载字幕')
    parser.add_argument('-o', '--output', default=cfg.get('save_path', DEFAULT_SAVE_PATH),
                        help=f'保存目录 (默认: {cfg.get("save_path", DEFAULT_SAVE_PATH)})')
    parser.add_argument('-c', '--cookie', default=cfg.get('cookie_file', ''),
                        help='Netscape 格式 Cookie 文件路径')
    parser.add_argument('-p', '--proxy', default=cfg.get('proxy', ''),
                        help='HTTP/SOCKS5 代理 (如 socks5://127.0.0.1:1080)')
    parser.add_argument('-l', '--clipboard', action='store_true',
                        help='从剪贴板读取链接')
    parser.add_argument('--save-config', action='store_true',
                        help='将当前参数保存为默认配置')

    args = parser.parse_args()

    # 检查 FFmpeg
    if not check_ffmpeg():
        cprint('⚠ 未检测到 FFmpeg — 视频合并不了', Color.YELLOW)
        cprint('  Termux 安装: pkg install ffmpeg', Color.YELLOW)
        print()

    # 保存配置
    if args.save_config:
        cfg['save_path'] = args.output
        cfg['audio_only'] = args.audio_only
        cfg['cookie_file'] = args.cookie
        cfg['proxy'] = args.proxy
        cfg['quality'] = args.quality
        save_config(cfg)
        cprint(f'✅ 配置已保存: {CONFIG_FILE}', Color.GREEN)

    # 获取 URL
    urls = []
    if args.clipboard:
        try:
            import subprocess
            text = subprocess.check_output(
                ['termux-clipboard-get'],
                timeout=3,
            ).decode('utf-8', errors='replace').strip()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(('http://', 'https://')):
                    urls.append(line)
        except Exception:
            cprint('❌ 读取剪贴板失败，请确保已安装 termux-api', Color.RED)
            sys.exit(1)
    elif args.url:
        arg = args.url.strip()
        if arg.startswith(('http://', 'https://')):
            urls = [arg]
        elif os.path.isfile(arg):
            # 从文件读取链接列表
            with open(arg, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(('http://', 'https://')):
                        urls.append(line)
            if not urls:
                cprint(f'❌ 文件中未找到有效链接: {arg}', Color.RED)
                sys.exit(1)
        else:
            cprint(f'❌ 无效链接或文件不存在: {arg}', Color.RED)
            sys.exit(1)

    if not urls:
        parser.print_help()
        sys.exit(0)

    # 执行下载
    cprint('╔══════════════════════════════════════════════╗', Color.CYAN)
    cprint('║  📥 视频下载工具 (手机版)                    ║', Color.CYAN)
    cprint('╚══════════════════════════════════════════════╝', Color.CYAN)
    cprint(f'  保存路径: {args.output}', Color.CYAN)
    if args.cookie:
        cprint(f'  Cookie:   {args.cookie}', Color.CYAN)
    if args.proxy:
        cprint(f'  代理:     {args.proxy}', Color.CYAN)
    cprint(f'  画质:     {args.quality}  |  仅音频: {"是" if args.audio_only else "否"}', Color.CYAN)
    print()

    if len(urls) == 1:
        dl = VideoDownloader(proxy=args.proxy)
        ok, data = dl.download(urls[0], args.output, quality=args.quality,
                               audio_only=args.audio_only, cookie_file=args.cookie,
                               subtitles=args.subtitles)
        if ok:
            cprint(f'\n✅ 下载完成！', Color.GREEN)
            # 通知系统扫描媒体文件（Android 相册可见）
            fp = data.get('path', '')
            if fp and os.path.isfile(fp) and os.path.isdir('/sdcard'):
                try:
                    subprocess.run(['am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE',
                                   '-d', f'file://{fp}'], timeout=5, capture_output=True)
                except Exception:
                    pass
        else:
            cprint(f'\n❌ 下载失败: {data}', Color.RED)

            # 特判：抖音需要 Cookie
            if 'douyin' in detect_platform(urls[0]):
                cprint('\n💡 提示: 抖音可能需要浏览器 Cookie', Color.YELLOW)
                cprint('   1. 在 Firefox 登录 douyin.com 后将 cookies.txt 复制到手机', Color.YELLOW)
                cprint('   2. 使用 -c cookies.txt 指定文件路径', Color.YELLOW)
    else:
        batch_download(urls, args.output, quality=args.quality,
                       audio_only=args.audio_only, cookie_file=args.cookie,
                       proxy=args.proxy, subtitles=args.subtitles)


if __name__ == '__main__':
    main()
