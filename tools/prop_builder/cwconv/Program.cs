// cwconv: CodeWalker XML -> binary GTA V asset (.ydr / .ytyp / .ypt), using CodeWalker.Core;
// also a particle file (.ypt) -> CodeWalker XML with its textures (--ypt2xml), to edit it.
// Clothing (clothes_variant.py):
//   ytd-export <in.ytd> <dir>                    textures.xml + one .dds per texture (CodeWalker's own export),
//                                                a .png of mip 0 when CodeWalker can decode it, textures.json
//   ytd-build <dir|textures.xml|a.dds ...> <out.ytd> [--rename old=new]... [--strict]
//                                                texture dictionary from the export folder (names, usage, flags from
//                                                the xml; pixels, format and mips from the .dds files) or plain .dds
//                                                files (name = file name); read back and listed. --strict: fail when
//                                                a .dds has another format / mip count than the xml says
//   ymt-to-xml <in.ymt> <out.xml>                ped variation file (CPedVariationInfo) -> CodeWalker meta XML;
//                                                prints FORMAT RSC|PSO|RBF
//   xml-to-ymt <in.xml> <out.ymt> [--pso]        and back (RSC meta, or PSO with --pso)
using System.Text;
using System.Text.Json;
using System.Xml;
using CodeWalker;
using CodeWalker.GameFiles;
using CodeWalker.Utils;

static byte[] Png(byte[] rgba, int w, int h)
{
    // minimal PNG writer (RGBA8, no filter) so the export needs no image library
    static uint Crc(byte[] d, int o, int n)
    {
        uint c = 0xFFFFFFFF;
        for (int i = o; i < o + n; i++) { c ^= d[i]; for (int k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1; }
        return ~c;
    }
    var ms = new MemoryStream();
    void Chunk(string type, byte[] body)
    {
        var buf = new byte[body.Length + 4];
        Encoding.ASCII.GetBytes(type).CopyTo(buf, 0); body.CopyTo(buf, 4);
        var len = BitConverter.GetBytes(body.Length); Array.Reverse(len); ms.Write(len);
        ms.Write(buf);
        var crc = BitConverter.GetBytes(Crc(buf, 0, buf.Length)); Array.Reverse(crc); ms.Write(crc);
    }
    ms.Write(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 });
    var ihdr = new byte[13];
    var bw = BitConverter.GetBytes(w); Array.Reverse(bw); bw.CopyTo(ihdr, 0);
    var bh = BitConverter.GetBytes(h); Array.Reverse(bh); bh.CopyTo(ihdr, 4);
    ihdr[8] = 8; ihdr[9] = 6;
    Chunk("IHDR", ihdr);
    var raw = new MemoryStream();
    using (var z = new System.IO.Compression.ZLibStream(raw, System.IO.Compression.CompressionLevel.Optimal, true))
        for (int y = 0; y < h; y++) { z.WriteByte(0); z.Write(rgba, y * w * 4, w * 4); }
    Chunk("IDAT", raw.ToArray());
    Chunk("IEND", Array.Empty<byte>());
    return ms.ToArray();
}

static YtdFile LoadYtd(string path)
{
    var bytes = File.ReadAllBytes(path);
    if (bytes.Length < 16 || BitConverter.ToUInt32(bytes, 0) != 0x37435352) throw new Exception("not a texture dictionary (no RSC7 header: encrypted or another file)");
    var ytd = new YtdFile();
    var e = RpfFile.CreateResourceFileEntry(ref bytes, 0); e.Name = Path.GetFileName(path);
    bytes = ResourceBuilder.Decompress(bytes);
    ytd.Load(bytes, e);
    if (ytd.TextureDict?.Textures?.data_items == null) throw new Exception("no textures could be read");
    return ytd;
}

static object TexInfo(Texture t) => new
{
    name = t.Name, width = (int)t.Width, height = (int)t.Height, mips = (int)t.Levels, format = t.Format.ToString(),
    usage = t.Usage.ToString(), dds = (t.Name ?? "null") + ".dds",
};

static int Fail(string msg, int code = 2) { Console.Error.WriteLine(msg); return code; }

// hashes in the meta print as names when CodeWalker knows the string (else hash_XXXXXXXX, which reads back the same)
static void LearnNames(Meta meta, string fileName)
{
    var strs = MetaTypes.GetStrings(meta);
    if (strs != null) foreach (var s in strs) JenkIndex.Ensure(s);
    var stem = Path.GetFileNameWithoutExtension(fileName).ToLowerInvariant();
    for (int i = 0; i < stem.Length; i++)
        if (i == 0 || stem[i - 1] == '_') JenkIndex.Ensure(stem.Substring(i));
}

if (args.Length >= 1 && args[0] == "ytd-export")
{
    if (args.Length != 3) return Fail("usage: cwconv ytd-export <in.ytd> <out dir>", 1);
    YtdFile ytd;
    try { ytd = LoadYtd(Path.GetFullPath(args[1])); } catch (Exception ex) { return Fail($"could not read {Path.GetFileName(args[1])}: {ex.Message}"); }
    var dir = Path.GetFullPath(args[2]); Directory.CreateDirectory(dir);
    File.WriteAllText(Path.Combine(dir, "textures.xml"), YtdXml.GetXml(ytd, dir));
    var list = new List<object>();
    foreach (var t in ytd.TextureDict.Textures.data_items)
    {
        string png = null;
        try
        {
            var px = DDSIO.GetPixels(t, 0);
            if (px != null) { png = (t.Name ?? "null") + ".png"; File.WriteAllBytes(Path.Combine(dir, png), Png(px, t.Width, t.Height)); }
        }
        catch { }
        list.Add(new { tex = TexInfo(t), png });
        Console.WriteLine($"  texture {t.Name} {t.Width}x{t.Height} {t.Format} mips={t.Levels} usage={t.Usage}");
    }
    File.WriteAllText(Path.Combine(dir, "textures.json"), JsonSerializer.Serialize(new { file = Path.GetFileName(args[1]), textures = list }, new JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine($"OK {Path.GetFileName(args[1])} textures={list.Count}");
    return 0;
}
if (args.Length >= 1 && args[0] == "ytd-build")
{
    var inputs = new List<string>(); var rename = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase); bool strict = false;
    for (int i = 1; i < args.Length; i++)
    {
        if (args[i] == "--strict") strict = true;
        else if (args[i] == "--rename" && i + 1 < args.Length) { var kv = args[++i].Split('=', 2); if (kv.Length == 2) rename[kv[0]] = kv[1]; }
        else inputs.Add(args[i]);
    }
    if (inputs.Count < 2) return Fail("usage: cwconv ytd-build <dir|textures.xml|a.dds ...> <out.ytd> [--rename old=new] [--strict]", 1);
    var output = Path.GetFullPath(inputs[^1]); inputs.RemoveAt(inputs.Count - 1);
    var textures = new List<Texture>();
    try
    {
        var first = Path.GetFullPath(inputs[0]);
        string xmlPath = Directory.Exists(first) ? Path.Combine(first, "textures.xml") : first.EndsWith(".xml", StringComparison.OrdinalIgnoreCase) ? first : null;
        if (xmlPath != null)
        {
            if (!File.Exists(xmlPath)) return Fail($"no textures.xml in {first}");
            var doc = new XmlDocument(); doc.Load(xmlPath);
            var folder = Path.GetDirectoryName(xmlPath);
            foreach (XmlNode item in doc.DocumentElement.SelectNodes("Item"))
            {
                var want = (Xml.GetChildInnerText(item, "Format"), Xml.GetChildUIntAttribute(item, "MipLevels", "value"));
                var t = new Texture(); t.ReadXml(item, folder);
                if (want.Item1 != t.Format.ToString() || want.Item2 != t.Levels)
                {
                    var msg = $"{t.Name}: the .dds is {t.Format} with {t.Levels} mips, the xml says {want.Item1} with {want.Item2}";
                    if (strict) return Fail(msg, 3);
                    Console.WriteLine("WARN " + msg);
                }
                textures.Add(t);
            }
        }
        else
        {
            foreach (var p in inputs)
            {
                var t = DDSIO.GetTexture(File.ReadAllBytes(Path.GetFullPath(p)));
                if (t == null) return Fail($"could not read {p}");
                t.Name = Path.GetFileNameWithoutExtension(p);
                t.Usage = TextureUsage.DIFFUSE;
                textures.Add(t);
            }
        }
    }
    catch (Exception ex) { return Fail("could not build the textures: " + ex.Message); }
    foreach (var t in textures)
    {
        if (t.Name != null && rename.TryGetValue(t.Name, out var nn)) t.Name = nn;
        t.NameHash = JenkHash.GenHash(t.Name?.ToLowerInvariant());
    }
    var dupe = textures.GroupBy(t => t.NameHash).FirstOrDefault(g => g.Count() > 1);
    if (dupe != null) return Fail($"two textures are called {dupe.First().Name}");
    var ytdOut = new YtdFile { TextureDict = new TextureDictionary() };
    ytdOut.TextureDict.BuildFromTextureList(textures);
    var data = ytdOut.Save();
    if (data == null || data.Length == 0) return Fail("conversion produced no data");
    File.WriteAllBytes(output, data);
    // read it back the way the game files are read
    var back = LoadYtd(output);
    foreach (var t in back.TextureDict.Textures.data_items)
        Console.WriteLine($"  texture {t.Name} {t.Width}x{t.Height} {t.Format} mips={t.Levels} usage={t.Usage}");
    Console.WriteLine($"OK {Path.GetFileName(output)} {data.Length} bytes textures={back.TextureDict.Textures.data_items.Length}");
    return 0;
}
if (args.Length >= 1 && args[0] == "ymt-to-xml")
{
    if (args.Length != 3) return Fail("usage: cwconv ymt-to-xml <in.ymt> <out.xml>", 1);
    var path = Path.GetFullPath(args[1]);
    var bytes = File.ReadAllBytes(path);
    if (bytes.Length < 16) return Fail("the .ymt is empty or cut short");
    string xml, fmt;
    try
    {
        if (BitConverter.ToUInt32(bytes, 0) == 0x37435352)
        {
            var e = RpfFile.CreateResourceFileEntry(ref bytes, 2); e.Name = Path.GetFileName(path);
            bytes = ResourceBuilder.Decompress(bytes);
            var ymt = new YmtFile(); ymt.Load(bytes, e);
            if (ymt.Meta == null) return Fail("could not read the .ymt meta");
            var root = ymt.Meta.GetRootBlock();
            LearnNames(ymt.Meta, path);
            xml = MetaXml.GetXml(ymt.Meta); fmt = "RSC";
            Console.WriteLine($"ROOT {MetaXml.HashString(root?.StructureNameHash ?? 0)}");
        }
        else
        {
            var ms = new MemoryStream(bytes);
            if (PsoFile.IsPSO(ms)) { var pso = new PsoFile(); pso.Load(ms); xml = PsoXml.GetXml(pso); fmt = "PSO"; }
            else if (RbfFile.IsRBF(new MemoryStream(bytes))) { var rbf = new RbfFile(); rbf.Load(new MemoryStream(bytes)); xml = RbfXml.GetXml(rbf); fmt = "RBF"; }
            else return Fail("not a ped variation file (no RSC7, PSO or RBF header: encrypted or another file)");
        }
    }
    catch (Exception ex) { return Fail($"could not read {Path.GetFileName(path)}: {ex.Message}"); }
    if (string.IsNullOrWhiteSpace(xml)) return Fail("the .ymt gave no XML");
    File.WriteAllText(Path.GetFullPath(args[2]), xml);
    Console.WriteLine($"FORMAT {fmt}");
    Console.WriteLine($"OK {Path.GetFileName(args[2])}");
    return 0;
}
if (args.Length >= 1 && args[0] == "xml-to-ymt")
{
    if (args.Length < 3) return Fail("usage: cwconv xml-to-ymt <in.xml> <out.ymt> [--pso]", 1);
    var input = Path.GetFullPath(args[1]);
    var doc = new XmlDocument(); doc.Load(input);
    byte[] data;
    try { data = XmlMeta.GetData(doc, args.Contains("--pso") ? MetaFormat.PSO : MetaFormat.RSC, Path.GetDirectoryName(input)); }
    catch (Exception ex) { return Fail("could not build the .ymt: " + ex.Message); }
    if (data == null || data.Length == 0) return Fail("conversion produced no data");
    File.WriteAllBytes(Path.GetFullPath(args[2]), data);
    Console.WriteLine($"OK {Path.GetFileName(args[2])} {data.Length} bytes");
    return 0;
}

if (args.Length == 2 && args[0] == "--check")
{
    var f = Path.GetFullPath(args[1]); var bytes = File.ReadAllBytes(f);
    if (f.EndsWith(".ydr"))
    {
        var y = new YdrFile(); var e = RpfFile.CreateResourceFileEntry(ref bytes, 0); e.Name = Path.GetFileName(f); bytes = ResourceBuilder.Decompress(bytes); y.Load(bytes, e);
        var d = y.Drawable;
        Console.WriteLine($"YDR name={d?.Name} models={d?.DrawableModels?.High?.Length} geoms={d?.DrawableModels?.High?[0]?.Geometries?.Length} verts={d?.DrawableModels?.High?[0]?.Geometries?[0]?.VerticesCount}");
        var tx = d?.ShaderGroup?.TextureDictionary?.Textures?.data_items;
        if (tx != null) foreach (var t in tx) Console.WriteLine($"  texture {t.Name} {t.Width}x{t.Height} {t.Format} mips={t.Levels}");
        Console.WriteLine($"  shaders={d?.ShaderGroup?.Shaders?.data_items?.Length} bound={d?.Bound?.GetType().Name} bbMax={d?.BoundingBoxMax}");
    }
    else
    {
        var y = new YtypFile(); var e = RpfFile.CreateResourceFileEntry(ref bytes, 0); e.Name = Path.GetFileName(f); bytes = ResourceBuilder.Decompress(bytes); y.Load(bytes, e);
        foreach (var a in y.AllArchetypes) Console.WriteLine($"YTYP archetype={a.Name} asset={a.AssetName} txd={a.TextureDict} lod={a.LodDist} bbMax={a.BBMax}");
    }
    return 0;
}
if (args.Length == 3 && args[0] == "--ypt2xml")
{
    var bytes = File.ReadAllBytes(Path.GetFullPath(args[1]));
    var outDir = Path.GetFullPath(args[2]);
    Directory.CreateDirectory(outDir);
    var ypt = new YptFile(); ypt.Load(bytes);
    if (ypt.PtfxList == null) { Console.Error.WriteLine($"could not read the particle file: {ypt.ErrorMessage}"); return 2; }
    var name = Path.GetFileNameWithoutExtension(args[1]);
    File.WriteAllText(Path.Combine(outDir, name + ".ypt.xml"), YptXml.GetXml(ypt, outDir));
    Console.WriteLine($"OK {name}.ypt.xml effects={ypt.AllEffects?.Length}");
    if (ypt.AllEffects != null) foreach (var fx in ypt.AllEffects) Console.WriteLine($"  effect {fx?.Name}");
    return 0;
}
if (args.Length == 2 && args[0] == "--check-ypt")
{
    var ypt = new YptFile(); ypt.Load(File.ReadAllBytes(Path.GetFullPath(args[1])));
    if (ypt.PtfxList == null) { Console.Error.WriteLine($"could not read the particle file: {ypt.ErrorMessage}"); return 2; }
    Console.WriteLine($"YPT effects={ypt.AllEffects?.Length} textures={ypt.PtfxList.TextureDictionary?.Textures?.data_items?.Length}");
    if (ypt.AllEffects != null) foreach (var fx in ypt.AllEffects) Console.WriteLine($"  effect {fx?.Name}");
    return 0;
}
if (args.Length < 2) { Console.Error.WriteLine("usage: cwconv <in.ydr.xml|in.ytyp.xml|in.ypt.xml> <out file> | --ypt2xml <in.ypt> <out dir> | ytd-export | ytd-build | ymt-to-xml | xml-to-ymt"); return 1; }
{
    var input = Path.GetFullPath(args[0]);
    var output = Path.GetFullPath(args[1]);
    var folder = Path.GetDirectoryName(input);
    var doc = new XmlDocument();
    doc.Load(input);

    byte[] data;
    if (input.EndsWith(".ydr.xml", StringComparison.OrdinalIgnoreCase))
        data = XmlYdr.GetYdr(doc, folder).Save();
    else if (input.EndsWith(".ytyp.xml", StringComparison.OrdinalIgnoreCase))
        data = XmlMeta.GetData(doc, MetaFormat.RSC, folder);
    else if (input.EndsWith(".ypt.xml", StringComparison.OrdinalIgnoreCase))
        data = XmlYpt.GetYpt(doc, folder).Save();
    else { Console.Error.WriteLine("unsupported input"); return 1; }

    if (data == null || data.Length == 0) { Console.Error.WriteLine("conversion produced no data"); return 2; }
    File.WriteAllBytes(output, data);
    Console.WriteLine($"OK {Path.GetFileName(output)} {data.Length} bytes");
    return 0;
}
