using System.Diagnostics;

namespace FastDelete.TestData;

/// <summary>
/// Generates synthetic directory trees for benchmarks and manual testing.
/// All profiles are deterministic (fixed seeds) so runs are comparable.
/// </summary>
public static class TreeGenerator
{
    public static readonly string[] AllProfiles = ["tiny10k", "tiny100k", "tiny500k", "deep1k", "mixed", "junctions"];

    public sealed record Stats(string Profile, int Files, int Directories, long Bytes, TimeSpan Elapsed);

    public static Stats Generate(string root, string profile, int degreeOfParallelism = 8)
    {
        Directory.CreateDirectory(root);
        var sw = Stopwatch.StartNew();
        var counters = new Counters();
        switch (profile)
        {
            case "tiny10k": Tiny(root, 100, 100, counters, degreeOfParallelism); break;
            case "tiny100k": Tiny(root, 200, 500, counters, degreeOfParallelism); break;
            case "tiny500k": Tiny(root, 500, 1000, counters, degreeOfParallelism); break;
            case "deep1k": Deep(root, 1000, counters); break;
            case "mixed": Mixed(root, counters, degreeOfParallelism); break;
            case "junctions": Junctions(root, counters, degreeOfParallelism); break;
            default: throw new ArgumentException($"Unknown profile '{profile}'. Known: {string.Join(", ", AllProfiles)}");
        }
        sw.Stop();
        return new Stats(profile, counters.Files, counters.Directories, counters.Bytes, sw.Elapsed);
    }

    /// <summary>N dirs × F files of 32 bytes. The classic "huge selection" scenario.</summary>
    private static void Tiny(string root, int dirs, int filesPerDir, Counters c, int dop)
    {
        byte[] payload = new byte[32];
        new Random(42).NextBytes(payload);

        var dirPaths = new string[dirs];
        for (int d = 0; d < dirs; d++)
        {
            dirPaths[d] = Path.Combine(root, "dir" + d.ToString("D4"));
            Directory.CreateDirectory(dirPaths[d]);
            c.Directories++;
        }

        Parallel.ForEach(dirPaths, new ParallelOptions { MaxDegreeOfParallelism = dop }, dir =>
        {
            for (int f = 0; f < filesPerDir; f++)
            {
                File.WriteAllBytes(Path.Combine(dir, "f" + f.ToString("D5") + ".bin"), payload);
                Interlocked.Increment(ref c.Files);
                Interlocked.Add(ref c.Bytes, payload.Length);
            }
        });
    }

    /// <summary>1,000 levels of nesting with a file at the bottom and at every 100th level.</summary>
    private static void Deep(string root, int depth, Counters c)
    {
        string current = root;
        for (int i = 0; i < depth; i++)
        {
            current = Path.Combine(current, "level" + i.ToString("D4"));
            Directory.CreateDirectory(current);
            c.Directories++;
            if (i % 100 == 0)
            {
                File.WriteAllText(Path.Combine(current, "marker.txt"), $"level {i}");
                c.Files++;
            }
        }
        File.WriteAllText(Path.Combine(current, "bottom.txt"), "bottom of the pit");
        c.Files++;
        c.Bytes += 17;
    }

    /// <summary>50 dirs with mixed sizes 1 KiB - 8 MiB plus read-only files.</summary>
    private static void Mixed(string root, Counters c, int dop)
    {
        var rng = new Random(7);
        var specs = new List<(string Dir, int Index)>();
        for (int d = 0; d < 50; d++)
        {
            string dir = Path.Combine(root, "mix" + d.ToString("D3"));
            Directory.CreateDirectory(dir);
            c.Directories++;
            int files = 20 + rng.Next(30);
            for (int f = 0; f < files; f++) specs.Add((dir, f));
        }

        Parallel.ForEach(specs, new ParallelOptions { MaxDegreeOfParallelism = dop }, spec =>
        {
            int size = 1024 << rng.Next(0, 14); // 1 KiB .. 8 MiB
            string path = Path.Combine(spec.Dir, "m" + spec.Index.ToString("D4") + ".dat");
            WriteRandomFile(path, size, rng.Next());
            if (spec.Index % 9 == 0) File.SetAttributes(path, FileAttributes.ReadOnly);
            Interlocked.Increment(ref c.Files);
            Interlocked.Add(ref c.Bytes, size);
        });
    }

    /// <summary>Tree with planted junctions pointing OUTSIDE the tree - target must survive.</summary>
    private static void Junctions(string root, Counters c, int dop)
    {
        Tiny(root, 20, 50, c, dop);

        // Outside target: sibling of root, never passed to the engine.
        string outside = Path.GetFullPath(Path.Combine(root, "..", "junction_outside_" + Path.GetFileName(root)));
        Directory.CreateDirectory(outside);
        File.WriteAllText(Path.Combine(outside, "precious.txt"), "must survive");

        string link = Path.Combine(root, "junction_out");
        var psi = new System.Diagnostics.ProcessStartInfo("cmd.exe", $"/c mklink /J \"{link}\" \"{outside}\"")
        { CreateNoWindow = true, UseShellExecute = false };
        var p = System.Diagnostics.Process.Start(psi)!;
        p.WaitForExit();
        if (p.ExitCode != 0) throw new InvalidOperationException("mklink /J failed");
    }

    private static void WriteRandomFile(string path, int size, int seed)
    {
        var rng = new Random(seed);
        var buffer = new byte[Math.Min(size, 65536)];
        using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None, 65536, FileOptions.SequentialScan);
        int remaining = size;
        while (remaining > 0)
        {
            int chunk = Math.Min(remaining, buffer.Length);
            rng.NextBytes(buffer);
            fs.Write(buffer, 0, chunk);
            remaining -= chunk;
        }
    }

    private sealed class Counters
    {
        public int Files;
        public int Directories;
        public long Bytes;
    }
}
