"""Create a realistic demo photo library for real-UI testing of Media Organizer."""
import os, random, struct, zlib
from pathlib import Path
from PIL import Image

DEST = Path(r"C:\Users\OdinLocal\Documents\Kimi\Workspaces\MediaOrganizer\demo-library")

def jpeg_with_exif(path, dt, gps=None, seed=0):
    im = Image.new("RGB", (640, 480), ((seed * 37) % 255, (seed * 91) % 255, (seed * 53) % 255))
    exif = Image.Exif()
    exif[0x9003] = dt  # DateTimeOriginal "YYYY:MM:DD HH:MM:SS"
    exif[0x010F] = "DemoCam"
    exif[0x0110] = "DemoCam X100"
    if gps:
        from PIL.TiffImagePlugin import IFDRational
        lat, lon = gps
        def dms(v):
            d = int(abs(v)); m = int((abs(v) - d) * 60); s = round(((abs(v) - d) * 60 - m) * 6000)
            return (IFDRational(d, 1), IFDRational(m, 1), IFDRational(s, 100))
        gps_ifd = {1: "N" if lat >= 0 else "S", 2: dms(lat),
                   3: "E" if lon >= 0 else "W", 4: dms(lon)}
        exif[0x8825] = gps_ifd
    im.save(path, "JPEG", exif=exif, quality=85)

def plain_jpeg(path, seed=0):
    im = Image.new("RGB", (640, 480), ((seed * 77) % 255, (seed * 13) % 255, (seed * 41) % 255))
    im.save(path, "JPEG", quality=85)

def mp4_with_date(path, year, month, day):
    """Minimal MP4 with mvhd creation_time."""
    epoch = 2082844800
    import datetime as dtm
    t = int(dtm.datetime(year, month, day, 12, 0, 0).timestamp()) + epoch
    def box(typ, payload):
        return struct.pack(">I", 8 + len(payload)) + typ + payload
    mvhd_payload = b"\x00\x00\x00\x00" + struct.pack(">II", t, t) + struct.pack(">I", 1000) + struct.pack(">I", 5000) + b"\x00" * 80
    moov = box(b"moov", box(b"mvhd", mvhd_payload))
    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isom")
    path.write_bytes(ftyp + moov)

random.seed(42)
DEST.mkdir(parents=True, exist_ok=True)
n = 0

# 1. Photos with real EXIF dates across years/months
samples = [
    ("2019:07:14 18:20:33", "IMG_4031.jpg"), ("2019:07:15 09:05:11", "IMG_4032.jpg"),
    ("2020:12:25 10:44:00", "IMG_7780.jpg"), ("2021:03:02 12:15:44", "PXL_20210302_121544.jpg"),
    ("2021:08:19 21:03:00", "IMG_5510.jpg"), ("2022:06:14 08:30:00", "IMG_9012.jpg"),
    ("2022:10:03 16:02:00", "holiday_final.jpg"), ("2023:01:05 11:11:11", "IMG_1100.jpg"),
    ("2023:07:30 22:11:01", "IMG_2211.jpg"), ("2024:01:01 00:00:15", "IMG_0001.jpg"),
]
for dt, name in samples:
    jpeg_with_exif(DEST / name, dt, seed=n); n += 1

# 2. Photos with GPS (Istanbul) + EXIF
for i, name in enumerate(["istanbul_bosphorus.jpg", "istanbul_galata.jpg"]):
    jpeg_with_exif(DEST / name, "2023:05:1%d 14:00:00" % (i + 2), gps=(41.0082 + i * 0.01, 28.9784), seed=n); n += 1

# 3. WhatsApp-style filenames (date in name only, no EXIF)
for name in ["IMG-20220614-WA0031.jpg", "IMG-20220720-WA0004.jpg", "IMG-20231225-WA0012.jpg"]:
    plain_jpeg(DEST / name, seed=n); n += 1

# 4. Videos with container dates
mp4_with_date(DEST / "VID_20201225_094410.mp4", 2020, 12, 25)
mp4_with_date(DEST / "VID_20230730_221101.mp4", 2023, 7, 30)
mp4_with_date(DEST / "birthday_party.mp4", 2022, 6, 14)

# 5. Duplicates: same bytes, different names, different folders
(DEST / "backup_copy").mkdir(exist_ok=True)
dup_bytes = (DEST / "IMG_4031.jpg").read_bytes()
(DEST / "backup_copy" / "photo_backup_1.jpg").write_bytes(dup_bytes)
(DEST / "backup_copy" / "DSCN_copy_of_4031.jpg").write_bytes(dup_bytes)

# 6. Undated / uncertain files (no EXIF, generic names)
for name in ["photo (3).jpg", "scan0001.jpg", "image.jpg"]:
    plain_jpeg(DEST / name, seed=n); n += 1

# 7. A nested subfolder with more
(DEST / "old phone dump" / "camera").mkdir(parents=True, exist_ok=True)
jpeg_with_exif(DEST / "old phone dump" / "camera" / "IMG_20180822_193012.jpg", "2018:08:22 19:30:12", seed=n)
plain_jpeg(DEST / "old phone dump" / "camera" / "IMG-20210910-WA0007.jpg")

total = sum(1 for _ in DEST.rglob("*") if _.is_file())
print(f"Demo library ready: {total} files at {DEST}")
