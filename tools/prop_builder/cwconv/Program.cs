// cwconv: CodeWalker XML -> binary GTA V asset (.ydr / .ytyp / .ypt), using CodeWalker.Core;
// also a particle file (.ypt) -> CodeWalker XML with its textures (--ypt2xml), to edit it
using System.Xml;
using CodeWalker.GameFiles;

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
if (args.Length < 2) { Console.Error.WriteLine("usage: cwconv <in.ydr.xml|in.ytyp.xml|in.ypt.xml> <out file> | --ypt2xml <in.ypt> <out dir>"); return 1; }
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
