"""
缩略图生成工具
使用 VLC 截取视频第一帧作为缩略图，图片直接使用 Pillow 缩放
"""
import os
import hashlib
import subprocess
import tempfile
from PIL import Image
from utils.config import Config


THUMB_SIZE = (160, 90)  # 缩略图尺寸


def get_thumb_path(file_path: str) -> str:
    """根据文件路径生成缩略图缓存路径"""
    config = Config()
    cache_dir = config['thumbnail_cache_dir']
    os.makedirs(cache_dir, exist_ok=True)
    file_hash = hashlib.md5(file_path.encode()).hexdigest()
    return os.path.join(cache_dir, f"{file_hash}.jpg")


def generate_video_thumbnail(file_path: str) -> str:
    """使用 ffmpeg 或 VLC 截取视频缩略图"""
    thumb_path = get_thumb_path(file_path)
    if os.path.exists(thumb_path):
        return thumb_path

    # 尝试使用 ffmpeg
    try:
        cmd = [
            'ffmpeg', '-y', '-i', file_path,
            '-ss', '00:00:01',
            '-vframes', '1',
            '-vf', f'scale={THUMB_SIZE[0]}:{THUMB_SIZE[1]}:force_original_aspect_ratio=decrease,pad={THUMB_SIZE[0]}:{THUMB_SIZE[1]}:(ow-iw)/2:(oh-ih)/2',
            thumb_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        if result.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
    except Exception:
        pass

    return ""


def generate_image_thumbnail(file_path: str) -> str:
    """生成图片缩略图"""
    thumb_path = get_thumb_path(file_path)
    if os.path.exists(thumb_path):
        return thumb_path

    try:
        with Image.open(file_path) as img:
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            # 创建黑色背景
            bg = Image.new('RGB', THUMB_SIZE, (0, 0, 0))
            offset = ((THUMB_SIZE[0] - img.width) // 2, (THUMB_SIZE[1] - img.height) // 2)
            if img.mode == 'RGBA':
                bg.paste(img, offset, img)
            else:
                bg.paste(img.convert('RGB'), offset)
            bg.save(thumb_path, 'JPEG', quality=85)
        return thumb_path
    except Exception:
        return ""


def generate_audio_thumbnail() -> str:
    """音频文件使用默认图标"""
    return ""


def get_video_info(file_path: str) -> dict:
    """使用 ffprobe 获取视频信息"""
    info = {'duration': 0.0, 'width': 0, 'height': 0}
    try:
        import json as json_mod
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-show_format', file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json_mod.loads(result.stdout)
            # 获取时长
            fmt = data.get('format', {})
            info['duration'] = float(fmt.get('duration', 0))
            # 获取视频流信息
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    info['width'] = stream.get('width', 0)
                    info['height'] = stream.get('height', 0)
                    break
    except Exception:
        pass
    return info
