using System.Text;
using FastDelete.Core.Enumeration;
using FastDelete.Core.Interop;

namespace FastDelete.Core.Deletion;

/// <summary>
/// Recycle Bin deletion via SHFileOperationW (FO_DELETE | FOF_ALLOWUNDO | FOF_NO_UI).
/// Batched in chunks so a single pathological path cannot abort a giant operation.
/// SHFileOperation does not report per-file errors, so a failed chunk is recorded
/// as one failure per path in that chunk.
/// </summary>
public static class RecycleBinDeleter
{
    private const int ChunkSize = 2000;

    public static Task<DeletionResult> DeleteAsync(
        IReadOnlyList<string> selectedPaths,
        CancellationToken cancellationToken)
    {
        var failures = new FailureCollector();
        var sw = System.Diagnostics.Stopwatch.StartNew();
        long files = 0, dirs = 0, links = 0;

        foreach (var chunk in Chunk(selectedPaths, ChunkSize))
        {
            cancellationToken.ThrowIfCancellationRequested();

            var sb = new StringBuilder(chunk.Count * 64);
            foreach (var path in chunk)
            {
                sb.Append(path).Append('\0');
                // classify for counters
                try
                {
                    var attrs = System.IO.File.GetAttributes(path);
                    if (attrs.HasFlag(System.IO.FileAttributes.Directory))
                    {
                        if (attrs.HasFlag(System.IO.FileAttributes.ReparsePoint)) links++; else dirs++;
                    }
                    else files++;
                }
                catch { /* counter best-effort only */ }
            }
            sb.Append('\0'); // double-null termination

            var op = new Win32.SHFILEOPSTRUCT
            {
                hwnd = IntPtr.Zero,
                wFunc = Win32.FO_DELETE,
                pFrom = sb.ToString(),
                pTo = IntPtr.Zero,
                fFlags = (ushort)(Win32.FOF_ALLOWUNDO | Win32.FOF_NO_UI | Win32.FOF_WANTNUKEWARNING),
                fAnyOperationsAborted = 0,
                hNameMappings = IntPtr.Zero,
                lpszProgressTitle = null,
            };

            int result = Win32.SHFileOperationW(ref op);
            if (result != 0 || op.fAnyOperationsAborted != 0)
            {
                // No per-file detail available; record the chunk as failures.
                foreach (var path in chunk)
                    failures.Add(path, WorkItemKind.File, result != 0 ? result : Win32.ERROR_ACCESS_DENIED);
                files = Math.Max(0, files - chunk.Count(c =>
                {
                    try { return !System.IO.File.GetAttributes(c).HasFlag(System.IO.FileAttributes.Directory); }
                    catch { return false; }
                }));
            }
        }

        sw.Stop();
        return Task.FromResult(new DeletionResult
        {
            FilesDeleted = files,
            DirectoriesDeleted = dirs,
            LinksDeleted = links,
            Failures = failures.ToList(),
            Elapsed = sw.Elapsed,
            WasCancelled = false,
        });
    }

    private static IEnumerable<IReadOnlyList<string>> Chunk(IReadOnlyList<string> paths, int size)
    {
        for (int i = 0; i < paths.Count; i += size)
            yield return paths.Skip(i).Take(size).ToArray();
    }
}
