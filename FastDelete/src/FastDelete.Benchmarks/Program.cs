using System.Diagnostics;
using FastDelete.Core.Deletion;
using FastDelete.TestData;

// FastDelete.Benchmarks - engine throughput vs worker count on generated trees.
//   --profiles tiny10k,tiny100k,deep1k,mixed   (default all four; tiny500k is opt-in)
//   --workers 1,2,4,8                           (default)
//   --root <dir>                                (default %TEMP%\FastDeleteBench)
//   --runs <n>                                  repeats per cell, best time wins (default 1)
//   --classic                                   use classic DeleteFile path instead of DispositionEx

string baseRoot = Arg(args, "--root") ?? Path.Combine(Path.GetTempPath(), "FastDeleteBench");
var profiles = (Arg(args, "--profiles") ?? "tiny10k,tiny100k,deep1k,mixed").Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
var workers = (Arg(args, "--workers") ?? "1,2,4,8").Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries).Select(int.Parse).ToArray();
int runs = int.TryParse(Arg(args, "--runs"), out var r) ? r : 1;
bool classic = args.Contains("--classic");

if (Directory.Exists(baseRoot)) Directory.Delete(baseRoot, recursive: true);
Directory.CreateDirectory(baseRoot);

Console.WriteLine($"Machine: {Environment.ProcessorCount} logical CPUs | classic={classic} | base={baseRoot}");
Console.WriteLine();

var table = new Dictionary<string, Dictionary<int, (double seconds, long items, double ips)>>();

foreach (var profile in profiles)
{
    table[profile] = new();
    long items = 0;

    foreach (int w in workers)
    {
        double best = double.MaxValue; long itemsDone = 0; double ips = 0;
        for (int run = 0; run < runs; run++)
        {
            string tree = Path.Combine(baseRoot, $"run_{profile}_{w}");
            Directory.CreateDirectory(tree);
            // generate into the run dir fresh each time for comparability
            var st = TreeGenerator.Generate(tree, profile);
            if (items == 0) items = st.Files + st.Directories + 1; // engine also counts the tree root
            GC.Collect(); GC.WaitForPendingFinalizers();

            var engine = new DeletionEngine(new DeletionOptions { MaxDegreeOfParallelism = w, UseDispositionEx = !classic });
            var sw = Stopwatch.StartNew();
            var result = await engine.DeleteAsync(new[] { tree });
            sw.Stop();

            if (result.Failures.Count > 0)
                Console.WriteLine($"  !! {profile} w={w}: {result.Failures.Count} failures, first: {result.Failures[0].Path} 0x{result.Failures[0].ErrorCode:X}");
            long done = result.FilesDeleted + result.DirectoriesDeleted + result.LinksDeleted;
            if (done != items)
                Console.WriteLine($"  !! {profile} w={w}: deleted {done} of {items} items");

            if (sw.Elapsed.TotalSeconds < best) { best = sw.Elapsed.TotalSeconds; itemsDone = done; ips = done / sw.Elapsed.TotalSeconds; }
            TryCleanup(tree);
        }
        table[profile][w] = (best, itemsDone, ips);
    }
}

// ---- report ----
Console.WriteLine($"{Environment.NewLine}Seconds (best of {runs})");
Console.Write("profile".PadRight(12));
foreach (int w in workers) Console.Write(("w=" + w).PadLeft(12));
Console.WriteLine();
foreach (var (profile, cells) in table)
{
    Console.Write(profile.PadRight(12));
    foreach (int w in workers)
        Console.Write(cells[w].seconds.ToString("F2").PadLeft(12));
    Console.WriteLine();
}
Console.WriteLine($"{Environment.NewLine}Items/second");
Console.Write("profile".PadRight(12));
foreach (int w in workers) Console.Write(("w=" + w).PadLeft(12));
Console.WriteLine();
foreach (var (profile, cells) in table)
{
    Console.Write(profile.PadRight(12));
    foreach (int w in workers)
        Console.Write(((long)cells[w].ips).ToString("N0").PadLeft(12));
    Console.WriteLine();
}

static string? Arg(string[] args, string name)
{
    int i = Array.IndexOf(args, name);
    return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
}

static void TryCleanup(string dir)
{
    try
    {
        if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true);
    }
    catch { /* leftover locked files etc. - report next run */ }
}
