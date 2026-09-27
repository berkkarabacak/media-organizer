using FastDelete.TestData;

// FastDelete.TestData - synthetic tree generator.
//   --root <dir>       base directory (default: %TEMP%\FastDeleteTrees)
//   --profile <name>   one of: tiny10k tiny100k tiny500k deep1k mixed junctions all (default: all)
//   --clean            wipe the base directory first
//   --dop <n>          generation parallelism (default 8)

string root = Arg(args, "--root") ?? Path.Combine(Path.GetTempPath(), "FastDeleteTrees");
string profile = Arg(args, "--profile") ?? "all";
int dop = int.TryParse(Arg(args, "--dop"), out var d) ? d : 8;

if (args.Contains("--clean") && Directory.Exists(root))
{
    Directory.Delete(root, recursive: true);
    Console.WriteLine($"cleaned {root}");
}

Directory.CreateDirectory(root);

var profiles = profile == "all" ? TreeGenerator.AllProfiles : new[] { profile };
foreach (var p in profiles)
{
    string dir = Path.Combine(root, p);
    if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true);
    try
    {
        var stats = TreeGenerator.Generate(dir, p, dop);
        Console.WriteLine($"{p,-10} files={stats.Files,7} dirs={stats.Directories,6} bytes={stats.Bytes,12:N0} gen={stats.Elapsed.TotalSeconds,7:F1}s -> {dir}");
    }
    catch (Exception ex)
    {
        Console.WriteLine($"{p,-10} FAILED: {ex.Message}");
    }
}
return 0;

static string? Arg(string[] args, string name)
{
    int i = Array.IndexOf(args, name);
    return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
}
