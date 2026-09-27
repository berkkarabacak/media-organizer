namespace FastDelete.Core.Deletion;

/// <summary>Cooperative pause. Workers poll <see cref="IsPaused"/> between items.</summary>
public sealed class PauseToken
{
    private volatile bool _paused;

    public bool IsPaused => _paused;

    public void Pause() => _paused = true;

    public void Resume() => _paused = false;

    /// <summary>Blocks while paused; honors cancellation.</summary>
    public void WaitIfPaused(CancellationToken cancellationToken)
    {
        while (_paused)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Thread.Sleep(50);
        }
    }
}
