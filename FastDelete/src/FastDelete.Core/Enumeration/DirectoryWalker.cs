using FastDelete.Core.Interop;

namespace FastDelete.Core.Enumeration;

/// <summary>
/// Streams deletion work items from a directory tree in a SINGLE pass.
///
/// - Uses FindFirstFileEx(FindExInfoBasic) to skip 8.3 short-name lookups.
/// - Never follows reparse points: junctions/symlinks become ReparseLink items.
/// - Directories are emitted AFTER their children (post-order) so RemoveDirectory
///   is attempted only once children are scheduled.
/// - Iterative (explicit stack) - safe on 10,000+ level deep nesting.
/// - One find-handle per directory frame, resumed across loop iterations, so each
///   directory is enumerated exactly once.
/// - Backpressure-safe: WriteAsync blocks the walk when the channel is full
///   (TryWrite would SILENTLY DROP items once the bounded channel fills).
/// - Cancellation is honored between items.
/// </summary>
public static class DirectoryWalker
{
    private sealed class DirFrame
    {
        public required string Path;            // \\?\ -prefixed
        public uint Attributes;
        public IntPtr Handle;                   // find handle, Zero until opened, -1 when finished
        public bool OpenAttempted;
    }

    private static readonly IntPtr InvalidHandle = new IntPtr(-1);

    /// <summary>
    /// Walks <paramref name="rootPath"/> (already \\?\ prefixed, known to exist with
    /// <paramref name="rootAttributes"/>) and pushes work items to <paramref name="output"/>.
    /// </summary>
    public static async Task WalkAsync(
        string rootPath,
        uint rootAttributes,
        System.Threading.Channels.ChannelWriter<DeleteWorkItem> output,
        CancellationToken cancellationToken)
    {
        if (Win32.IsReparsePoint(rootAttributes))
        {
            // Never traverse a reparse root - delete the link itself.
            await output.WriteAsync(new DeleteWorkItem(rootPath, WorkItemKind.ReparseLink, rootAttributes), cancellationToken).ConfigureAwait(false);
            return;
        }

        if (!Win32.IsDirectory(rootAttributes))
        {
            await output.WriteAsync(new DeleteWorkItem(rootPath, WorkItemKind.File, rootAttributes), cancellationToken).ConfigureAwait(false);
            return;
        }

        var stack = new Stack<DirFrame>();
        stack.Push(new DirFrame { Path = rootPath, Attributes = rootAttributes });

        while (stack.Count > 0)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var frame = stack.Peek();

            if (!frame.OpenAttempted)
            {
                frame.OpenAttempted = true;
                string searchPattern = LongPath.Join(frame.Path, "*");
                IntPtr h = Win32.FindFirstFileExW(
                    searchPattern,
                    Win32.FINDEX_INFO_LEVELS.FindExInfoBasic,
                    out var firstData,
                    Win32.FINDEX_SEARCH_OPS.FindExSearchNameMatch,
                    IntPtr.Zero,
                    Win32.FIND_FIRST_EX_LARGE_FETCH);
                if (h == IntPtr.Zero || h == InvalidHandle)
                {
                    // empty/vanished/denied: emit dir (engine will report removal failure if any)
                    await output.WriteAsync(new DeleteWorkItem(frame.Path, WorkItemKind.Directory, frame.Attributes), cancellationToken).ConfigureAwait(false);
                    stack.Pop();
                    continue;
                }
                frame.Handle = h;
                // process the first result that FindFirstFileExW already returned
                await EmitEntry(frame, stack, output, cancellationToken, firstData).ConfigureAwait(false);
                continue;
            }

            if (frame.Handle == InvalidHandle)
            {
                // finished: post-order emit
                await output.WriteAsync(new DeleteWorkItem(frame.Path, WorkItemKind.Directory, frame.Attributes), cancellationToken).ConfigureAwait(false);
                stack.Pop();
                continue;
            }

            await ProcessCurrentEntry(frame, stack, output, cancellationToken).ConfigureAwait(false);
        }
    }

    private static async Task ProcessCurrentEntry(
        DirFrame frame,
        Stack<DirFrame> stack,
        System.Threading.Channels.ChannelWriter<DeleteWorkItem> output,
        CancellationToken cancellationToken)
    {
        bool hasEntry = Win32.FindNextFileW(frame.Handle, out var findData);

        if (!hasEntry)
        {
            Win32.FindClose(frame.Handle);
            frame.Handle = InvalidHandle;
            return;
        }

        await EmitEntry(frame, stack, output, cancellationToken, findData).ConfigureAwait(false);
    }

    private static async Task EmitEntry(
        DirFrame frame,
        Stack<DirFrame> stack,
        System.Threading.Channels.ChannelWriter<DeleteWorkItem> output,
        CancellationToken cancellationToken,
        Win32.WIN32_FIND_DATA findData)
    {
        cancellationToken.ThrowIfCancellationRequested();
        string name = findData.cFileName;
        if (name == "." || name == "..")
            return;

        string childPath = LongPath.Join(frame.Path, name);
        uint attrs = findData.dwFileAttributes;
        bool isDir = Win32.IsDirectory(attrs);
        bool isReparse = Win32.IsReparsePoint(attrs);

        if (isDir && !isReparse)
        {
            // depth-first: push child; it is fully enumerated before we resume this frame
            stack.Push(new DirFrame { Path = childPath, Attributes = attrs });
        }
        else if (isReparse)
        {
            // junction / symlink / mount point: delete the link, never traverse
            await output.WriteAsync(new DeleteWorkItem(childPath, WorkItemKind.ReparseLink, attrs), cancellationToken).ConfigureAwait(false);
        }
        else
        {
            await output.WriteAsync(new DeleteWorkItem(childPath, WorkItemKind.File, attrs), cancellationToken).ConfigureAwait(false);
        }
    }
}
