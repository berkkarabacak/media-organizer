namespace FastDelete.Core.Deletion;

/// <summary>
/// Immutable progress snapshot. Reported by the engine; the consumer (GUI) decides
/// how often to render it. Counts are eventually-consistent under parallelism.
/// </summary>
public readonly struct DeletionProgress
{
    public long FilesDeleted { get; init; }
    public long DirectoriesDeleted { get; init; }
    public long LinksDeleted { get; init; }
    public long Failed { get; init; }
    public long ItemsProcessed { get; init; }   // files+dirs+links attempted
    public long TotalDiscovered { get; init; }  // -1 when unknown (walker still running)
    public string CurrentItem { get; init; }    // last processed item (display form)
    public TimeSpan Elapsed { get; init; }
    public bool IsCancelled { get; init; }

    public double ItemsPerSecond => Elapsed.TotalSeconds > 0.1 ? ItemsProcessed / Elapsed.TotalSeconds : 0;
}
