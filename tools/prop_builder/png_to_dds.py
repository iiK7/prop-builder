# PNG -> DDS for GTA textures: power-of-two, max 1024, full mip chain.
# Uses Pillow's BC1/BC3 encoder when available, else uncompressed A8R8G8B8.
import sys, struct
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGBA")
p2 = lambda v: 1 << max(2, min(10, (v - 1).bit_length()))
im = im.resize((p2(im.width), p2(im.height)), Image.LANCZOS)
has_alpha = im.getextrema()[3][0] < 255
try:
    import io
    fmt = "DXT5" if has_alpha else "DXT1"
    levels, m = [], im
    while True:
        buf = io.BytesIO(); m.save(buf, "DDS", pixel_format=fmt); levels.append(buf.getvalue())
        if m.width <= 4 and m.height <= 4: break
        m = m.resize((max(4, m.width // 2), max(4, m.height // 2)), Image.LANCZOS)
    head = bytearray(levels[0][:128])
    struct.pack_into("<I", head, 8, struct.unpack_from("<I", head, 8)[0] | 0x20000)   # DDSD_MIPMAPCOUNT
    struct.pack_into("<I", head, 28, len(levels))                                       # mip count
    struct.pack_into("<I", head, 108, struct.unpack_from("<I", head, 108)[0] | 0x400008)  # COMPLEX|MIPMAP
    open(dst, "wb").write(bytes(head) + b"".join(l[128:] for l in levels))
    print("DDS", fmt, im.size, len(levels), "mips")
except Exception as e:
    mips, w, h, m = [], im.width, im.height, im
    while True:
        r, g, b, a = m.split(); mips.append(Image.merge("RGBA", (b, g, r, a)).tobytes())
        if w == 1 and h == 1: break
        w, h = max(1, w // 2), max(1, h // 2); m = m.resize((w, h), Image.LANCZOS)
    hdr = struct.pack("<4sIIIIIII44xIIIIIIIIIIIII", b"DDS ", 124, 0x1|0x2|0x4|0x1000|0x20000|0x8,
                      im.height, im.width, im.width * 4, 0, len(mips),
                      32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000,
                      0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)
    open(dst, "wb").write(hdr + b"".join(mips))
    print("DDS A8R8G8B8 (fallback:", e, ")", im.size, len(mips), "mips")
