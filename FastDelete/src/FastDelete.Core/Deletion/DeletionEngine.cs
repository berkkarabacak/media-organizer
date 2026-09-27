using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading.Channels;
using FastDelete.Core.Enumeration;
using FastDelete.Core.Interop;

namespace FastDelete.Core.Deletion;

public enum DeletionMode
{
    /// <summary>Permanent, high-performance deletion. The main feature.</summary>
    Permanent,
    /// <summary>Send to the Windows Recycle Bin via SHFileOperation.</summary>
    RecycleBin
}

public sealed class DeletionOptions
{
    public static readonly DeletionOptions Default = new();

    /// <summary>0 = adaptive: clamp(ProcessorCount / 2, 2, 4) - see docs/benchmarks.md.</summary>
    public int MaxDegreeOfParallelism { get; set; } = 0;
    public bool UseDispositionEx { get; set; } = true;
    public DeletionMode Mode { get; set; } = DeletionMode.Permanent;
    public int ChannelCapacity { get; set; } = 16384;
}

public sealed class DeletionResult
{
    public required long FilesDeleted { get; init; }
    public required long DirectoriesDeleted { get; init; }
    public required long LinksDeleted { get; init; }
    public required IReadOnlyList<DeleteFailure> Failures { get; init; }
    public required TimeSpan Elapsed { get; init; }
    public required bool WasCancelled { get; init; }

    public long TotalItems => FilesDeleted + DirectoriesDeleted + LinksDeleted;
    public double ItemsPerSecond => Elapsed.TotalSeconds > 0.1 ? TotalItems / Elapsed.TotalSeconds : 0;
}

/// <summary>
/// High-throughput recursive deletion engine.
///
/// Architecture: per-root single-pass walkers stream work items into a bounded channel;
/// a pool of workers deletes FILES and REPARSE LINKS in parallel; ALL directories are
/// deferred to one sequential post-order pass (parallel RemoveDirectory on ancestor
/// chains convoys on NTFS metadata locks - measured 5x slowdown on 1k-deep trees).
/// Per-item failures are collected, never thrown.
/// </summary>
public sealed class DeletionEngine
{
    private const int MaxDirectoryRetryRounds = 64;
    private const int ProgressReportIntervalMs = 100;

    private readonly DeletionOptions _options;

    public DeletionEngine(DeletionOptions? options = null)
    {
        _options = options ?? DeletionOptions.Default;
    }

    public Task<DeletionResult> DeleteAsync(
        IReadOnlyList<string> selectedPaths,
        IProgress<DeletionProgress>? progress = null,
        CancellationToken cancellationToken = default,
        PauseToken? pauseToken = null)
    {
        ArgumentNullException.ThrowIfNull(selectedPaths);
        if (selectedPaths.Count == 0)
            return Task.FromResult(new DeletionResult
            {
                FilesDeleted = 0, DirectoriesDeleted = 0, LinksDeleted = 0,
                Failures = Array.Empty<DeleteFailure>(), Elapsed = TimeSpan.Zero, WasCancelled = false,
            });

        if (_options.Mode == DeletionMode.RecycleBin)
            return RecycleBinDeleter.DeleteAsync(selectedPaths, cancellationToken);

        return DeletePermanentAsync(selectedPaths, progress, cancellationToken, pauseToken);
    }

    private async Task<DeletionResult> DeletePermanentAsync(
        IReadOnlyList<string> selectedPaths,
        IProgress<DeletionProgress>? progress,
        CancellationToken cancellationToken,
        PauseToken? pauseToken)
    {
        var sw = Stopwatch.StartNew();
        var failures = new FailureCollector();
        var counters = new DeletionCounters();
        var deferredDirs = new ConcurrentQueueShim();

        // Normalize roots once; every work item originates from one of these roots.
        var roots = new List<RootEntry>(selectedPaths.Count);
        var seenCanonical = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var raw in selectedPaths.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            cancellationToken.ThrowIfCancellationRequested();
            string prefixed = LongPath.Prefix(raw);
            uint attrs = FileDeleter.GetAttributes(prefixed);
            if (attrs == uint.MaxValue)
            {
                failures.Add(prefixed, WorkItemKind.File, Marshal.GetLastWin32Error());
                continue;
            }
            string canonical = LongPath.NormalizeForCompare(prefixed);
            if (!seenCanonical.Add(canonical))
                continue; // duplicate root (alias/casing)
            roots.Add(new RootEntry(prefixed, attrs, canonical));
        }

        if (roots.Count == 0)
        {
            sw.Stop();
            return BuildResult(counters, failures, sw.Elapsed, wasCancelled: false);
        }

        var channel = Channel.CreateBounded<DeleteWorkItem>(new BoundedChannelOptions(_options.ChannelCapacity)
        {
            SingleReader = false,
            SingleWriter = false,
        });

        var walkerCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);

        // Walkers: one single-pass enumeration per root; producers run concurrently.
        var walkerTasks = roots.Select(root => Task.Run(async () =>
        {
            try
            {
                await DirectoryWalker.WalkAsync(root.Prefixed, root.Attributes, channel.Writer, walkerCts.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (walkerCts.IsCancellationRequested) { }
            catch (Exception)
            {
                // Enumeration hiccup on one root must not poison the others.
            }
        }, CancellationToken.None)).ToArray();

        // Complete the channel only when every walker is done.
        var walkersDone = Task.Run(async () =>
        {
            try { await Task.WhenAll(walkerTasks).ConfigureAwait(false); } catch { /* cancelled */ }
            channel.Writer.TryComplete();
        }, CancellationToken.None);

        int workers = _options.MaxDegreeOfParallelism <= 0
            ? Math.Clamp(Environment.ProcessorCount / 2, 2, 4)
            : Math.Max(1, _options.MaxDegreeOfParallelism);

        string lastItem = string.Empty;
        var workerTasks = Enumerable.Range(0, workers).Select(_ => Task.Run(async () =>
        {
            var reader = channel.Reader;
            while (await reader.WaitToReadAsync(cancellationToken).ConfigureAwait(false))
            {
                while (reader.TryRead(out var item))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    pauseToken?.WaitIfPaused(cancellationToken);

                    Interlocked.Increment(ref counters.ItemsProcessed);
                    lastItem = LongPath.Display(item.Path);

                    // Directories are never deleted by the parallel workers: parallel
                    // RemoveDirectory calls on ancestor chains convoy on NTFS metadata
                    // locks (measured 5x slowdown on 1k-deep trees) and mostly fail with
                    // DIR_NOT_EMPTY anyway. The walker emits post-order (children first),
                    // and the sequential pass below preserves that order, so each parent
                    // is attempted only after its children are gone.
                    if (item.Kind == WorkItemKind.Directory)
                    {
                        deferredDirs.Enqueue(item);
                        Report(progress, counters, sw.Elapsed, lastItem, cancellationToken, final: false);
                        continue;
                    }

                    var (status, error) = DeleteOne(item);
                    switch (status)
                    {
                        case DeleteStatus.Deleted:
                        case DeleteStatus.NotFound:
                            counters.IncrementFor(item.Kind);
                            break;
                        case DeleteStatus.DirectoryNotEmpty:
                            deferredDirs.Enqueue(item);
                            break;
                        default:
                            Interlocked.Increment(ref counters.Failed);
                            failures.Add(item.Path, item.Kind, error);
                            break;
                    }

                    Report(progress, counters, sw.Elapsed, lastItem, cancellationToken, final: false);
                }
            }
        }, CancellationToken.None)).ToArray();

        bool wasCancelled = false;
        try
        {
            await Task.WhenAll(workerTasks).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            wasCancelled = true;
        }
        finally
        {
            walkerCts.Cancel();
            await walkersDone.ConfigureAwait(false);
        }

        // Sequential post-order deletion of ALL directories (see worker-loop comment).
        // First pass almost always succeeds because children precede parents; the round
        // loop only covers external TOCTOU (another process dropping files into a dir).
        if (!wasCancelled)
        {
            for (int round = 0; round < MaxDirectoryRetryRounds && deferredDirs.TryPeek(out _); round++)
            {
                int remaining = deferredDirs.Count;
                for (int i = 0; i < remaining; i++)
                {
                    if (!deferredDirs.TryDequeue(out var dir))
                        break;
                    cancellationToken.ThrowIfCancellationRequested();
                    pauseToken?.WaitIfPaused(cancellationToken);

                    var (status, error) = DeleteOne(dir);
                    if (status == DeleteStatus.DirectoryNotEmpty)
                    {
                        deferredDirs.Enqueue(dir);
                    }
                    else if (status == DeleteStatus.Failed)
                    {
                        Interlocked.Increment(ref counters.Failed);
                        failures.Add(dir.Path, dir.Kind, error);
                    }
                    else
                    {
                        counters.IncrementFor(dir.Kind);
                        Report(progress, counters, sw.Elapsed, LongPath.Display(dir.Path), cancellationToken, final: false);
                    }
                }
                if (deferredDirs.IsEmpty)
                    break;
            }
        }

        // Whatever could not be emptied after all rounds is a failure.
        while (deferredDirs.TryDequeue(out var leftover))
        {
            failures.Add(leftover.Path, leftover.Kind, Win32.ERROR_DIR_NOT_EMPTY);
            Interlocked.Increment(ref counters.Failed);
        }

        sw.Stop();
        Report(progress, counters, sw.Elapsed, lastItem, cancellationToken, final: true);
        return BuildResult(counters, failures, sw.Elapsed, wasCancelled);
    }

    /// <summary>Deletes one work item; never throws.</summary>
    private (DeleteStatus status, int error) DeleteOne(in DeleteWorkItem item)
    {
        try
        {
            return item.Kind switch
            {
                WorkItemKind.ReparseLink => FileDeleter.DeleteReparseLink(item.Path, item.Attributes),
                WorkItemKind.Directory => FileDeleter.DeleteDirectoryClassic(item.Path),
                _ => DeleteFile(item),
            };
        }
        catch (Exception)
        {
            return (DeleteStatus.Failed, Win32.ERROR_ACCESS_DENIED);
        }
    }

    private (DeleteStatus status, int error) DeleteFile(in DeleteWorkItem item)
    {
        if (_options.UseDispositionEx)
        {
            if (FileDeleter.TryDeleteFileDispositionEx(item.Path, item.Attributes, out int exError))
                return (DeleteStatus.Deleted, 0);

            // Not supported / not a file / downlevel FS -> classic path.
            if (exError is not (Win32.ERROR_INVALID_FUNCTION or Win32.ERROR_NOT_SUPPORTED
                or Win32.ERROR_INVALID_PARAMETER or Win32.ERROR_ACCESS_DENIED))
            {
                return (DeleteStatus.Failed, exError);
            }
        }
        return FileDeleter.DeleteFileClassic(item.Path);
    }

    private static void Report(
        IProgress<DeletionProgress>? progress,
        DeletionCounters counters,
        TimeSpan elapsed,
        string currentItem,
        CancellationToken cancellationToken,
        bool final)
    {
        if (progress is null) return;
        if (!final)
        {
            long now = (long)Win32.GetTickCount64();
            if (now - counters.LastReportMs < ProgressReportIntervalMs) return;
            counters.LastReportMs = now;
        }
        progress.Report(new DeletionProgress
        {
            FilesDeleted = counters.FilesDeleted,
            DirectoriesDeleted = counters.DirectoriesDeleted,
            LinksDeleted = counters.LinksDeleted,
            Failed = counters.Failed,
            ItemsProcessed = counters.ItemsProcessed,
            TotalDiscovered = -1,
            CurrentItem = currentItem,
            Elapsed = elapsed,
            IsCancelled = cancellationToken.IsCancellationRequested,
        });
    }

    private static DeletionResult BuildResult(
        DeletionCounters counters, FailureCollector failures, TimeSpan elapsed, bool wasCancelled)
        => new()
        {
            FilesDeleted = counters.FilesDeleted,
            DirectoriesDeleted = counters.DirectoriesDeleted,
            LinksDeleted = counters.LinksDeleted,
            Failures = failures.ToList(),
            Elapsed = elapsed,
            WasCancelled = wasCancelled,
        };

    private sealed class DeletionCounters
    {
        public long FilesDeleted;
        public long DirectoriesDeleted;
        public long LinksDeleted;
        public long Failed;
        public long ItemsProcessed;
        public long LastReportMs;

        public void IncrementFor(WorkItemKind kind)
        {
            switch (kind)
            {
                case WorkItemKind.Directory: Interlocked.Increment(ref DirectoriesDeleted); break;
                case WorkItemKind.ReparseLink: Interlocked.Increment(ref LinksDeleted); break;
                default: Interlocked.Increment(ref FilesDeleted); break;
            }
        }
    }

    /// <summary>ConcurrentQueue wrapper so the engine body reads cleanly.</summary>
    private sealed class ConcurrentQueueShim
    {
        private readonly System.Collections.Concurrent.ConcurrentQueue<DeleteWorkItem> _q = new();
        public void Enqueue(DeleteWorkItem item) => _q.Enqueue(item);
        public bool TryDequeue(out DeleteWorkItem item) => _q.TryDequeue(out item);
        public bool TryPeek(out DeleteWorkItem item) => _q.TryPeek(out item);
        public bool IsEmpty => _q.IsEmpty;
        public int Count => _q.Count;
    }

    private readonly struct RootEntry
    {
        public RootEntry(string prefixed, uint attributes, string canonical)
        {
            Prefixed = prefixed;
            Attributes = attributes;
            Canonical = canonical;
        }
        public string Prefixed { get; }
        public uint Attributes { get; }
        public string Canonical { get; }
    }
}
