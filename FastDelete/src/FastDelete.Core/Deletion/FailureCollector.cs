using FastDelete.Core.Enumeration;
using FastDelete.Core.Interop;

namespace FastDelete.Core.Deletion;

/// <summary>A single deletion failure with enough detail for the failure report and retry.</summary>
public sealed record DeleteFailure
{
    public required string Path { get; init; }          // display form (no \\?\ prefix)
    public required int ErrorCode { get; init; }        // Win32 error or HRESULT
    public required string Message { get; init; }
    public WorkItemKind Kind { get; init; }

    public bool Retryable => ErrorCode is Win32.ERROR_DIR_NOT_EMPTY
        or Win32.ERROR_ACCESS_DENIED
        or Win32.ERROR_SHARING_VIOLATION
        or Win32.ERROR_LOCK_VIOLATION;

    public override string ToString() => $"{Path} - 0x{ErrorCode:X8}: {Message}";
}

/// <summary>Thread-safe failure collector; one deletion failure never aborts the run.</summary>
public sealed class FailureCollector
{
    private readonly System.Collections.Concurrent.ConcurrentBag<DeleteFailure> _failures = new();

    public void Add(string prefixedPath, WorkItemKind kind, int errorCode)
    {
        _failures.Add(new DeleteFailure
        {
            Path = LongPath.Display(prefixedPath),
            Kind = kind,
            ErrorCode = errorCode,
            Message = Win32.GetErrorMessage(errorCode),
        });
    }

    public void Add(DeleteFailure failure) => _failures.Add(failure);

    public IReadOnlyList<DeleteFailure> ToList() => _failures.ToArray();
    public int Count => _failures.Count;
}
