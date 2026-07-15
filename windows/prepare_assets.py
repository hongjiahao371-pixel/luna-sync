import shutil
from pathlib import Path

import imageio_ffmpeg
from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
BUILD_ASSETS = ROOT / 'windows' / 'build_assets'


def main():
    BUILD_ASSETS.mkdir(parents=True, exist_ok=True)
    source_icon = ROOT / 'upk' / 'luna-sync' / 'rootfs_common' / 'icon.png'
    image = Image.open(source_icon).convert('RGBA')
    image.save(BUILD_ASSETS / 'luna-sync.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), BUILD_ASSETS / 'ffmpeg.exe')


if __name__ == '__main__':
    main()
